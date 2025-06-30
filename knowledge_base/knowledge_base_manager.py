# knowledge_base/knowledge_base_manager.py

import os
import logging
import chromadb
import google.generativeai as genai
import numpy as np

logger = logging.getLogger("NovelTranslator.KBManager")

class KnowledgeBaseManager:
    """Управляет всеми операциями с векторной базой данных ChromaDB."""
    def __init__(self, project_name, api_key, embedding_model_name):
        self.project_name = project_name
        self.api_key = api_key
        self.embedding_model_name = embedding_model_name
        
        # Путь к хранилищу ChromaDB для конкретного проекта
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.chroma_path = os.path.join(script_dir, '..', '_project_files', project_name, 'chroma_db')
        os.makedirs(self.chroma_path, exist_ok=True)

        self.client = chromadb.PersistentClient(path=self.chroma_path)
        
        # Настройка функции для эмбеддингов
        if self.api_key:
            genai.configure(api_key=self.api_key)
            self.embedding_function = chromadb.utils.embedding_functions.GoogleGenerativeAiEmbeddingFunction(
                api_key=self.api_key, model_name=self.embedding_model_name
            )
        else:
            self.embedding_function = None
            logger.warning("API ключ не предоставлен, эмбеддинги не будут работать.")
        
        self.collection = self.client.get_or_create_collection(
            name=f"{project_name}_kb",
            embedding_function=self.embedding_function
        )
        logger.info(f"KnowledgeBaseManager инициализирован. Коллекция: '{self.collection.name}'. Записей: {self.collection.count()}")

    def _get_embedding(self, text, task_type="RETRIEVAL_DOCUMENT"):
        """Ручное получение эмбеддинга, если embedding_function недоступна."""
        if not self.api_key: return None
        try:
            result = genai.embed_content(model=self.embedding_model_name, content=text, task_type=task_type)
            return result['embedding']
        except Exception as e:
            logger.error(f"Ошибка при получении эмбеддинга для текста: '{text[:50]}...': {e}")
            return None

    def add_or_update_entries(self, documents, metadatas, ids):
        """Пакетное добавление или обновление записей в ChromaDB."""
        if not documents:
            return
        # ChromaDB автоматически обрабатывает и добавление, и обновление по ID
        self.collection.add(
            documents=documents,
            metadatas=metadatas,
            ids=ids
        )
        logger.info(f"Добавлено/обновлено {len(ids)} записей в ChromaDB.")
        
    def query(self, query_text, n_results=5, relevance_threshold=0.75):
        """Выполняет семантический поиск по базе знаний."""
        if not query_text or self.collection.count() == 0:
            return ""

        results = self.collection.query(
            query_texts=[query_text],
            n_results=n_results
        )

        # ChromaDB возвращает косинусное расстояние, а не сходство.
        # distance = 1 - similarity. Нам нужно similarity >= threshold, т.е. distance <= 1 - threshold.
        distance_threshold = 1 - relevance_threshold
        
        relevant_docs = []
        if results and results['documents']:
            for i, doc in enumerate(results['documents'][0]):
                distance = results['distances'][0][i]
                if distance <= distance_threshold:
                    relevant_docs.append(doc)

        if not relevant_docs:
            return ""

        context_str = "\n## Relevant Context (from Knowledge Base)\n"
        context_str += "- Use this highly relevant, automatically selected information for consistency.\n\n"
        for text in relevant_docs:
            context_str += f"- {text}\n"
        
        return context_str

    def rebuild_index_from_db(self, db_manager, progress_callback=None):
        """Полностью перестраивает индекс ChromaDB на основе данных из SQLite."""
        logger.info("Начало полной перестройки семантического индекса из SQLite...")
        
        terms = db_manager.get_terms_dictionary()
        bible = db_manager.get_world_bible()
        
        items_to_index = []
        for name, data in bible.items():
            text_to_embed = f"{name}: {data.get('description', '')}"
            items_to_index.append({'id': f"bible_{name}", 'text': text_to_embed, 'metadata': {'source': 'bible', 'name': name}})
        
        for term, translation in terms.items():
            text_to_embed = f"Термин: {term} (Перевод: {translation})"
            items_to_index.append({'id': f"dict_{term}", 'text': text_to_embed, 'metadata': {'source': 'dictionary', 'name': term}})

        total_items = len(items_to_index)
        if total_items == 0:
            logger.info("Нет данных для индексации.")
            if self.collection.count() > 0:
                self.collection.delete() # Очищаем, если что-то было
            if progress_callback: progress_callback(100, "Нет данных для индексации")
            return

        # Пакетная обработка для эффективности
        batch_size = 50 
        for i in range(0, total_items, batch_size):
            batch = items_to_index[i:i + batch_size]
            if progress_callback:
                progress_callback((i / total_items) * 100, f"Индексация {i+1}-{min(i+batch_size, total_items)}/{total_items}")

            self.add_or_update_entries(
                documents=[item['text'] for item in batch],
                metadatas=[item['metadata'] for item in batch],
                ids=[item['id'] for item in batch]
            )
        
        logger.info(f"Перестройка индекса завершена. Всего в коллекции: {self.collection.count()} записей.")
        if progress_callback: progress_callback(100, f"Индекс построен: {self.collection.count()} записей")