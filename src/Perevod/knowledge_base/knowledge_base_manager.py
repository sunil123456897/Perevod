# src/Perevod/knowledge_base/knowledge_base_manager.py

import os
import logging
import time
import hashlib
import json
import sqlite3
from contextlib import closing
from numbers import Real
import chromadb
import chromadb.config
from google import genai
from google.genai import types


from Perevod.agents.reranker import Reranker
from Perevod.api_usage import ApiUsageTracker, should_track_api_usage
from Perevod.config import PROJECT_ROOT, settings
from Perevod.utils.api_errors import is_retryable_api_error
from Perevod.utils.validation import validate_project_name

logger = logging.getLogger("NovelTranslator.KBManager")


class _UnavailableEmbeddingModels:
    def embed_content(self, *args, **kwargs):
        raise RuntimeError(
            "Embedding function was reconstructed without an API key. "
            "Create KnowledgeBaseManager with an API key before embedding text."
        )


class _UnavailableEmbeddingClient:
    def __init__(self):
        self.models = _UnavailableEmbeddingModels()


def _ensure_local_chromadb_available():
    if getattr(chromadb.config, "is_thin_client", False):
        raise RuntimeError(
            "Local ChromaDB is unavailable because chromadb-client is installed. "
            "Uninstall chromadb-client and reinstall chromadb to use PersistentClient."
        )


class GenAIEmbeddingFunction:
    def __init__(
        self,
        api_key: str,
        model_name: str,
        client=None,
        cache_path: str | None = None,
        usage_tracker: ApiUsageTracker | None = None,
        max_retries: int = 4,
        initial_delay: float = 10,
        sleep_func=time.sleep,
    ):
        self.model_name = model_name
        self.client = client or genai.Client(api_key=api_key)
        self.cache_path = cache_path
        self.usage_tracker = usage_tracker
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        self.sleep_func = sleep_func

    def name(self=None) -> str:
        return "google_genai"

    def is_legacy(self) -> bool:
        return False

    def default_space(self) -> str:
        return "cosine"

    def supported_spaces(self) -> list[str]:
        return ["cosine", "l2", "ip"]

    def get_config(self) -> dict[str, str]:
        return {"model_name": self.model_name}

    @staticmethod
    def build_from_config(config: dict[str, str]) -> "GenAIEmbeddingFunction":
        return GenAIEmbeddingFunction(
            api_key="",
            model_name=config.get("model_name", "gemini-embedding-2"),
            client=_UnavailableEmbeddingClient(),
        )

    def _cache_key(self, text: str, task_type: str) -> str:
        payload = f"{self.model_name}\0{task_type}\0{text}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _ensure_cache_table(self) -> None:
        if not self.cache_path:
            return
        cache_dir = os.path.dirname(self.cache_path)
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
        with closing(sqlite3.connect(self.cache_path)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS embedding_cache (
                    cache_key TEXT PRIMARY KEY,
                    model_name TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    embedding_json TEXT NOT NULL
                )
                """
            )

    @staticmethod
    def _is_valid_embedding(embedding: list[float] | object) -> bool:
        return (
            isinstance(embedding, list)
            and bool(embedding)
            and all(
                isinstance(value, Real) and not isinstance(value, bool)
                for value in embedding
            )
        )

    def _read_cached_embeddings(
        self, input: list[str], task_type: str
    ) -> tuple[list[list[float] | None], list[int], list[str]]:
        cached: list[list[float] | None] = [None] * len(input)
        missing_indexes: list[int] = []
        missing_texts: list[str] = []
        if not self.cache_path:
            return cached, list(range(len(input))), input

        try:
            self._ensure_cache_table()
            with closing(sqlite3.connect(self.cache_path)) as conn:
                for index, text in enumerate(input):
                    cache_key = self._cache_key(text, task_type)
                    row = conn.execute(
                        "SELECT embedding_json FROM embedding_cache WHERE cache_key = ?",
                        (cache_key,),
                    ).fetchone()
                    if row:
                        try:
                            cached_embedding = json.loads(row[0])
                        except json.JSONDecodeError:
                            cached_embedding = None
                        if self._is_valid_embedding(cached_embedding):
                            cached[index] = cached_embedding
                            continue
                        logger.warning(
                            "Игнорируется некорректный embedding-кэш для модели '%s'.",
                            self.model_name,
                        )
                    missing_indexes.append(index)
                    missing_texts.append(text)
        except (OSError, sqlite3.Error) as exc:
            logger.warning(
                "Embedding-кэш недоступен и будет пропущен для модели '%s': %s",
                self.model_name,
                exc,
            )
            return cached, list(range(len(input))), input
        return cached, missing_indexes, missing_texts

    def _write_cached_embeddings(
        self, texts: list[str], embeddings: list[list[float]], task_type: str
    ) -> None:
        if not self.cache_path:
            return
        try:
            self._ensure_cache_table()
            with closing(sqlite3.connect(self.cache_path)) as conn:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO embedding_cache
                        (cache_key, model_name, task_type, embedding_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    [
                        (
                            self._cache_key(text, task_type),
                            self.model_name,
                            task_type,
                            json.dumps(embedding),
                        )
                        for text, embedding in zip(texts, embeddings)
                    ],
                )
                conn.commit()
        except (OSError, sqlite3.Error) as exc:
            logger.warning(
                "Не удалось обновить embedding-кэш для модели '%s': %s",
                self.model_name,
                exc,
            )

    def _extract_embeddings(self, response, expected_count: int) -> list[list[float]]:
        response_embeddings = list(getattr(response, "embeddings", None) or [])
        if len(response_embeddings) != expected_count:
            raise RuntimeError(
                "Gemini Embedding API returned "
                f"{len(response_embeddings)} embedding(s) for "
                f"{expected_count} input(s)."
            )

        embeddings = []
        for index, embedding in enumerate(response_embeddings):
            values = list(getattr(embedding, "values", None) or [])
            if not self._is_valid_embedding(values):
                raise RuntimeError(
                    "Gemini Embedding API returned an empty embedding "
                    "or invalid values "
                    f"at index {index}."
                )
            embeddings.append(values)
        return embeddings

    def _embed_with_cache(self, input: list[str], task_type: str) -> list[list[float]]:
        cached, missing_indexes, missing_texts = self._read_cached_embeddings(
            input, task_type
        )
        if missing_texts:
            response = self._embed_remote(missing_texts, task_type)
            new_embeddings = self._extract_embeddings(response, len(missing_texts))
            try:
                self._write_cached_embeddings(missing_texts, new_embeddings, task_type)
            except (OSError, sqlite3.Error) as exc:
                logger.warning(
                    "Embedding-кэш не обновлен для модели '%s': %s",
                    self.model_name,
                    exc,
                )
            for index, embedding in zip(missing_indexes, new_embeddings):
                cached[index] = embedding
        return [embedding or [] for embedding in cached]

    def _embed_remote(self, texts: list[str], task_type: str):
        for attempt in range(1, self.max_retries + 1):
            reservation_id = None
            try:
                if self.usage_tracker:
                    reservation_id = self.usage_tracker.reserve_call(
                        self.model_name,
                        "embedContent",
                    )
                response = self.client.models.embed_content(
                    model=self.model_name,
                    contents=texts,
                    config=types.EmbedContentConfig(
                        taskType=task_type,
                        outputDimensionality=3072,
                    ),
                )
                if self.usage_tracker:
                    self.usage_tracker.record_call(
                        self.model_name,
                        "embedContent",
                        reservation_id=reservation_id,
                    )
                    reservation_id = None
                return response
            except Exception as error:
                if self.usage_tracker and reservation_id is not None:
                    self.usage_tracker.release_call(
                        self.model_name,
                        "embedContent",
                        reservation_id=reservation_id,
                    )
                if not is_retryable_api_error(error) or attempt >= self.max_retries:
                    raise

                delay = self.initial_delay * (2 ** (attempt - 1))
                logger.warning(
                    "Временная ошибка Gemini Embedding API для модели '%s': %s. "
                    "Повторная попытка %s/%s через %.2f секунд.",
                    self.model_name,
                    error,
                    attempt,
                    self.max_retries,
                    delay,
                )
                self.sleep_func(delay)

        raise RuntimeError("Gemini Embedding API retry loop exited unexpectedly.")

    def __call__(self, input: list[str]) -> list[list[float]]:
        return self._embed_with_cache(input, "RETRIEVAL_DOCUMENT")

    def embed_query(self, input: list[str]) -> list[list[float]]:
        return self._embed_with_cache(input, "RETRIEVAL_QUERY")


class KnowledgeBaseManager:
    _DEFAULT_QUERY_TOP_K = 7
    _DEFAULT_QUERY_N_CANDIDATES = 25
    _REBUILD_BATCH_SIZE = 25
    _REBUILD_BATCH_DELAY_SECONDS = 16

    def __init__(self, project_name, api_key, embedding_model_name, enable_reranker=None, db_manager=None):
        _ensure_local_chromadb_available()
        self.project_name = validate_project_name(project_name)
        self.api_key = api_key
        self.embedding_model_name = embedding_model_name
        self.enable_reranker = (
            settings.enable_reranker if enable_reranker is None else enable_reranker
        )
        self.db_manager = db_manager
        self.needs_rebuild = False
        self._in_rebuild = False

        self.chroma_path = os.path.join(
            PROJECT_ROOT, "_project_files", self.project_name, "chroma_db"
        )
        os.makedirs(self.chroma_path, exist_ok=True)

# --- НАЧАЛО ИСПРАВЛЕННОГО БЛОКА ИНИЦИАЛИЗАЦИИ ---
        # Инициализируем клиент с настройками, которые ЯВНО отключают телеметрию,
        # чтобы избежать ошибок в некоторых версиях ChromaDB.
        from chromadb.config import Settings
        self.client = chromadb.PersistentClient(
            path=self.chroma_path,
            settings=Settings(anonymized_telemetry=False, allow_reset=True)
        )
        # --- КОНЕЦ ИСПРАВЛЕННОГО БЛОКА ИНИЦИАЛИЗАЦИИ ---

        if self.api_key:
            self.embedding_function = GenAIEmbeddingFunction(
                api_key=self.api_key,
                model_name=self.embedding_model_name,
                cache_path=os.path.join(self.chroma_path, "embedding_cache.sqlite3"),
                usage_tracker=ApiUsageTracker()
                if should_track_api_usage(self.api_key)
                else None,
            )
        else:
            self.embedding_function = None
            logger.warning("API ключ не предоставлен, эмбеддинги не будут работать.")

        self.collection = self._get_or_create_collection(
            f"{self.project_name}_kb"
        )
        self.reranker = Reranker() if self.enable_reranker else None
        logger.info(
            f"KnowledgeBaseManager инициализирован. Коллекция: '{self.collection.name}'. Записей: {self.collection.count()}"
        )

    @staticmethod
    def _is_embedding_dimension_mismatch(exc: Exception) -> bool:
        message = str(exc).lower()
        return "expecting embedding with dimension" in message and "got" in message

    @staticmethod
    def _is_embedding_function_conflict(exc: Exception) -> bool:
        return "embedding function conflict" in str(exc).lower()

    def _get_or_create_collection(self, collection_name: str):
        try:
            return self.client.get_or_create_collection(
                name=collection_name,
                embedding_function=self.embedding_function,
            )
        except ValueError as exc:
            if not self._is_embedding_function_conflict(exc):
                raise
            logger.warning(
                "Сброс коллекции '%s' из-за смены embedding function.",
                collection_name,
            )
            self.client.delete_collection(name=collection_name)
            return self.client.get_or_create_collection(
                name=collection_name,
                embedding_function=self.embedding_function,
            )

    def _reset_collection(self) -> None:
        collection_name = self.collection.name
        logger.warning(
            "Сброс коллекции '%s' из-за несовместимой размерности эмбеддингов.",
            collection_name,
        )
        self.client.delete_collection(name=collection_name)
        self.collection = self._get_or_create_collection(collection_name)

        self.needs_rebuild = True
        if self.db_manager and not self._in_rebuild:
            self._in_rebuild = True
            try:
                self.rebuild_index_from_db(self.db_manager)
                self.needs_rebuild = False
            finally:
                self._in_rebuild = False

    @staticmethod
    def _text_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _filter_changed_items(self, items: list[dict]) -> list[dict]:
        if not items:
            return []

        try:
            existing = self.collection.get(
                ids=[item["id"] for item in items],
                include=["documents", "metadatas"],
            )
        except Exception as exc:
            logger.warning(
                "Не удалось проверить существующие записи индекса; будет выполнена полная индексация батча: %s",
                exc,
            )
            return items

        if not isinstance(existing, dict):
            return items

        hashes_by_id = {
            item_id: (metadata or {}).get("text_hash")
            for item_id, metadata in zip(
                existing.get("ids", []),
                existing.get("metadatas", []),
                strict=False,
            )
        }
        documents_by_id = {
            item_id: document
            for item_id, document in zip(
                existing.get("ids", []),
                existing.get("documents", []),
                strict=False,
            )
        }
        changed_items = []
        for item in items:
            item_id = item["id"]
            existing_document = documents_by_id.get(item_id)
            if existing_document is not None:
                if existing_document != item["text"]:
                    changed_items.append(item)
                continue

            if hashes_by_id.get(item_id) != item["metadata"].get("text_hash"):
                changed_items.append(item)

        return changed_items

    def _delete_stale_rebuild_items(self, expected_ids: set[str]) -> None:
        stale_ids = []
        for source in ("dictionary", "bible"):
            existing = self.collection.get(
                where={"source": source},
                include=["metadatas"],
            )
            stale_ids.extend(
                item_id
                for item_id in existing.get("ids", [])
                if item_id not in expected_ids
            )

        if stale_ids:
            self.collection.delete(ids=stale_ids)
            logger.info(
                "Удалено %s устаревших записей из семантического индекса.",
                len(stale_ids),
            )

    def add_or_update_entries(self, documents, metadatas, ids, embeddings=None):
        """Пакетное добавление или обновление записей в ChromaDB с ограничением по размеру батча."""
        if not documents:
            return

        batch_size = 100
        total_added_updated = 0

        for i in range(0, len(documents), batch_size):
            batch_documents = documents[i : i + batch_size]
            batch_metadatas = metadatas[i : i + batch_size]
            batch_ids = ids[i : i + batch_size]
            batch_embeddings = embeddings[i : i + batch_size] if embeddings is not None else None

            if not batch_documents:
                continue

            try:
                kwargs = {
                    "documents": batch_documents,
                    "metadatas": batch_metadatas,
                    "ids": batch_ids,
                }
                if batch_embeddings is not None:
                    kwargs["embeddings"] = batch_embeddings
                self.collection.upsert(**kwargs)
            except Exception as exc:
                if not self._is_embedding_dimension_mismatch(exc):
                    raise
                self._reset_collection()
                kwargs = {
                    "documents": batch_documents,
                    "metadatas": batch_metadatas,
                    "ids": batch_ids,
                }
                if batch_embeddings is not None:
                    kwargs["embeddings"] = batch_embeddings
                self.collection.upsert(**kwargs)
            total_added_updated += len(batch_ids)
            logger.info(
                f"Добавлено/обновлено {len(batch_ids)} записей в ChromaDB (всего: {total_added_updated})."
            )

        logger.info(f"Всего добавлено/обновлено {total_added_updated} записей.")

    def delete_entries(self, ids: list[str]):
        """Удаляет записи из ChromaDB по списку их ID."""
        if not ids:
            return
        try:
            self.collection.delete(ids=ids)
            logger.info(f"Удалено {len(ids)} записей из ChromaDB.")
        except Exception as e:
            logger.error(f"Ошибка при удалении записей из ChromaDB: {e}", exc_info=True)
            raise

    def delete_collection(self):
        """Удаляет всю коллекцию ChromaDB для текущего проекта."""
        try:
            self.client.delete_collection(name=self.collection.name)
            logger.info(f"Коллекция '{self.collection.name}' успешно удалена.")
        except Exception as e:
            logger.error(
                f"Ошибка при удалении коллекции '{self.collection.name}': {e}",
                exc_info=True,
            )
            raise

    def upsert_from_verdicts(self, verdicts: list):
        """
        Инкрементально обновляет или добавляет записи в ChromaDB на основе списка вердиктов.
        """
        if not verdicts:
            return

        logger.info(
            f"Инкрементальное обновление семантического индекса для {len(verdicts)} вердиктов..."
        )

        documents = []
        metadatas = []
        ids = []

        for verdict in verdicts:
            eng_term = verdict["english_term"]
            rus_term = verdict["correct_variant"]

            text_to_embed = (
                f"Dictionary Term. Term: {eng_term}. Translation: {rus_term}."
            )
            documents.append(text_to_embed)
            metadatas.append({"source": "dictionary", "name": eng_term})
            ids.append(f"dict_{eng_term}")

        self.add_or_update_entries(documents=documents, metadatas=metadatas, ids=ids)
        logger.info("Инкрементальное обновление семантического индекса завершено.")

    def query(self, query_text, top_k=None, n_candidates=None, where=None):
        """Выполняет семантический поиск по базе знаний с двухэтапным ранжированием."""
        # Устанавливаем значения по умолчанию, если они не переданы
        top_k = top_k if top_k is not None else self._DEFAULT_QUERY_TOP_K
        n_candidates = n_candidates if n_candidates is not None else self._DEFAULT_QUERY_N_CANDIDATES
        start_time = time.time()
        if not self.api_key:
            logger.warning("Запрос к базе знаний невозможен: API ключ отсутствует.")
            return ""
        if not query_text or self.collection.count() == 0 or len(query_text) < 3:
            return ""

        try:
            # Убедимся, что n_candidates не превышает количество элементов в коллекции
            actual_n_candidates = min(n_candidates, self.collection.count())
            if actual_n_candidates == 0:
                return ""

            results = self.collection.query(
                query_texts=[query_text], n_results=actual_n_candidates, where=where
            )

            candidate_docs = [
                {
                    "text": doc,
                    "id": results["ids"][0][i],
                    "metadata": results["metadatas"][0][i],
                }
                for i, doc in enumerate(results["documents"][0])
            ]
            if not candidate_docs:
                return ""

            ranked_docs = (
                self.reranker.rerank(query_text, candidate_docs)
                if self.reranker
                else candidate_docs
            )
            selected_docs = ranked_docs[:top_k]
            if not selected_docs:
                return ""

            context_str = "\n## Relevant Context (from Knowledge Base)\n- Use this highly relevant, automatically selected information for consistency.\n\n"
            context_str += "".join([f"- {doc['text']}\n" for doc in selected_docs])

            end_time = time.time()
            logger.info(
                f"Запрос к базе знаний выполнен за {end_time - start_time:.4f} секунд."
            )

            return context_str
        except Exception as e:
            if self._is_embedding_dimension_mismatch(e):
                self._reset_collection()
                return ""
            logger.error(
                f"Ошибка при выполнении запроса к ChromaDB: {e}", exc_info=True
            )
            return ""

    def rebuild_index_from_db(self, db_manager, progress_callback=None):
        """Полностью перестраивает Семантический Индекс на основе данных из SQLite."""
        # ПРОВЕРКА БЕЗОПАСНОСТИ: Нельзя строить индекс без функции эмбеддингов.
        if not self.embedding_function:
            error_msg = "API ключ не предоставлен. Перестройка семантического индекса невозможна."
            logger.error(error_msg)
            if progress_callback:
                progress_callback(0, error_msg)
            raise ValueError(error_msg)

        logger.info("Начало полной перестройки семантического индекса из SQLite...")

        terms = db_manager.get_terms_dictionary()
        bible = db_manager.get_world_bible()

        items_to_index = []

        for name, data in bible.items():
            description_chunks = [data.get("description", "")]
            for i, chunk in enumerate(description_chunks):
                text_to_embed = f"Bible Entry. Category: {data.get('category', 'N/A')}. Name: {name} (Russian: {data.get('russian_name', 'N/A')}). Description: {chunk}"
                items_to_index.append(
                    {
                        "id": f"bible_{name}_chunk_{i}",
                        "text": text_to_embed,
                        "metadata": {
                            "source": "bible",
                            "name": name,
                            "text_hash": self._text_hash(text_to_embed),
                        },
                    }
                )

        for term, data in terms.items():
            rus_translation = data.get("russian_term") or data.get("russian", "N/A")
            text_to_embed = f"Dictionary Term. Category: {data.get('category', 'N/A')}. Term: {term}. Translation: {rus_translation}."
            items_to_index.append(
                {
                    "id": f"dict_{term}",
                    "text": text_to_embed,
                    "metadata": {
                        "source": "dictionary",
                        "name": term,
                        "text_hash": self._text_hash(text_to_embed),
                    },
                }
            )

        total_items = len(items_to_index)
        if total_items == 0:
            logger.info(
                "Нет данных для индексации. Удаление устаревших записей словаря и лора..."
            )
            self._delete_stale_rebuild_items(set())
            if progress_callback:
                progress_callback(100, "Нет данных для индексации")
            return

        self._delete_stale_rebuild_items(
            {item["id"] for item in items_to_index}
        )

        batch_size = self._REBUILD_BATCH_SIZE
        for i in range(0, total_items, batch_size):
            batch = items_to_index[i : i + batch_size]
            changed_batch = self._filter_changed_items(batch)
            if progress_callback:
                progress_callback(
                    (i / total_items) * 100,
                    f"Индексация {i + 1}-{min(i + batch_size, total_items)}/{total_items}",
                )
            if changed_batch:
                self.add_or_update_entries(
                    documents=[item["text"] for item in changed_batch],
                    metadatas=[item["metadata"] for item in changed_batch],
                    ids=[item["id"] for item in changed_batch],
                )
            if changed_batch and i + batch_size < total_items:
                time.sleep(self._REBUILD_BATCH_DELAY_SECONDS)

        logger.info(
            f"Перестройка индекса завершена. Всего в коллекции: {self.collection.count()} записей."
        )
        if progress_callback:
            progress_callback(
                100, f"Индекс построен: {self.collection.count()} записей"
            )
