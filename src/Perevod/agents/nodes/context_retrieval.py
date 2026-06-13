# src/Perevod/agents/nodes/context_retrieval.py
import logging

from Perevod.agents.state import AgentState
from Perevod.agents.nodes.translation import _tokenize_for_overlap, _lexical_rerank, _chapter_index_from_title
from Perevod.utils.api_errors import gemini_api_error_metadata

logger = logging.getLogger("NovelTranslator.AgentNodes.ContextRetrieval")


def context_retrieval_node(state: AgentState) -> dict:
    """Retrieves relevant semantic and historical context for the chapters."""
    from Perevod.agents.nodes import tool_read_chapter
    logger.info("Узел [Контекст]: Сбор исторического и лорного контекста...")
    context = state.get("app_context", {})
    chapters_to_process = state.get("chapters_to_process", [])
    kb_manager = context.get("kb_manager")
    db_manager = context.get("db_manager")

    chapter_contexts = state.get("chapter_contexts", {}).copy()
    chapter_summaries = []
    context_errors = []
    context_warnings = []

    if not chapters_to_process:
        return {
            "rag_context": "=== WORLD BIBLE & LORE ===\n- No relevant lore found\n\n=== RECENT PLOT DEVELOPMENTS (PAST CHAPTERS) ===\n- No previous chapter memory found",
            "chapter_summaries": [],
            "context_errors": [],
            "context_warnings": [],
            "chapter_contexts": {}
        }

    first_rag_context = None

    for chapter_data in chapters_to_process:
        title = chapter_data.get("title", "Untitled")
        current_chapter_idx = _chapter_index_from_title(title)
        if current_chapter_idx is None:
            current_chapter_idx = 999999

        # Resume logic: check if context_retrieved is already done
        chapter_runs = state.get("chapter_runs", {})
        run_data = chapter_runs.get(title, {})
        stages = run_data.get("stages", {})
        
        if stages.get("context_retrieved") == "done":
            cached_context = state.get("chapter_contexts", {}).get(title) or run_data.get("context")
            if cached_context:
                chapter_contexts[title] = cached_context
                if first_rag_context is None:
                    first_rag_context = cached_context
                logger.info(f"Контекст для главы '{title}' загружен из кэша/чекпоинта.")
                continue

        # Читаем главу
        chapter_text = ""
        try:
            chapter_text = tool_read_chapter(chapter_data["input_path"])
            # Если tool_read_chapter возвращает Mock без spec=str, преобразуем в строку
            if not isinstance(chapter_text, str):
                chapter_text = ""
        except Exception as e:
            logger.error(f"Не удалось прочитать главу '{title}': {e}")
            context_errors.append({
                "title": title,
                "scope": "read_chapter",
                "error": str(e),
            })
            if db_manager:
                db_manager.mark_chapter_stage(title, "context_retrieved", "failed", error=str(e))
            continue

        # 1. Сбор лора (World Bible & Lore)
        retrieved_lore_docs = []
        has_lore = False
        if kb_manager and kb_manager.collection:
            try:
                cnt = kb_manager.collection.count()
                if not isinstance(cnt, int):
                    cnt = int(cnt)
                if cnt > 0:
                    has_lore = True
            except (TypeError, ValueError):
                pass

        if has_lore:
            query_tokens = _tokenize_for_overlap(chapter_text)
            kb_query = " ".join(list(query_tokens)[:15]) if query_tokens else ""
            
            try:
                # Пытаемся выполнить семантический поиск
                kb_results = kb_manager.collection.query(query_texts=[kb_query], n_results=5)
                docs = kb_results["documents"][0] if kb_results.get("documents") else []
                metas = kb_results["metadatas"][0] if kb_results.get("metadatas") else [{}] * len(docs)
                ids = kb_results["ids"][0] if kb_results.get("ids") else [""] * len(docs)
                
                candidate_docs = []
                for doc, meta, doc_id in zip(docs, metas, ids):
                    meta = meta or {}
                    if meta.get("type") == "chapter_memory":
                        continue
                    candidate_docs.append({"text": doc, "meta": meta, "id": doc_id})
                    
                reranked = _lexical_rerank(chapter_text, candidate_docs)
                for item in reranked[:3]:
                    retrieved_lore_docs.append(item["text"])
                    
            except Exception as semantic_error:
                logger.warning(
                    f"Семантический поиск завершился ошибкой: {semantic_error}. "
                    "Переход к лексическому поиску по всей коллекции..."
                )
                context_warnings.append({
                    "title": title,
                    "scope": "semantic_lore",
                    "error": str(semantic_error),
                    **gemini_api_error_metadata(semantic_error),
                })
                try:
                    # Лексический fallback: загружаем все документы
                    get_results = kb_manager.collection.get()
                    docs = get_results.get("documents", [])
                    metas = get_results.get("metadatas", []) or [{}] * len(docs)
                    ids = get_results.get("ids", []) or [""] * len(docs)
                    
                    candidate_docs = []
                    for doc, meta, doc_id in zip(docs, metas, ids):
                        meta = meta or {}
                        if meta.get("type") == "chapter_memory":
                            continue
                        candidate_docs.append({"text": doc, "meta": meta, "id": doc_id})
                        
                    reranked = _lexical_rerank(chapter_text, candidate_docs)
                    for item in reranked[:3]:
                        retrieved_lore_docs.append(item["text"])
                except Exception as fallback_error:
                    logger.error(f"Ошибка лексического fallback-поиска: {fallback_error}")
                    context_errors.append({
                        "title": title,
                        "scope": "lexical_lore",
                        "error": str(fallback_error),
                        **gemini_api_error_metadata(fallback_error),
                    })
                    if db_manager:
                        db_manager.mark_chapter_stage(
                            title,
                            "context_retrieved",
                            "failed",
                            error=str(fallback_error),
                        )
                    continue

        # 2. Сбор памяти о главах (Chapter Memory)
        valid_memories = []
        memory_error = None
        if kb_manager and kb_manager.collection:
            try:
                memory_results = kb_manager.collection.get(where={"type": "chapter_memory"})
                docs = memory_results.get("documents", [])
                metas = memory_results.get("metadatas", []) or [{}] * len(docs)
                
                for doc, meta in zip(docs, metas):
                    if meta is None:
                        chapter_index = 0
                        ch_title = "Unknown"
                    else:
                        chapter_index = meta.get("chapter_index", 0)
                        ch_title = meta.get("title", "Unknown")
                        
                    if chapter_index < current_chapter_idx:
                        valid_memories.append({
                            "content": doc,
                            "chapter_index": chapter_index,
                            "title": ch_title
                        })
                valid_memories.sort(key=lambda x: x["chapter_index"])
                chapter_summaries = valid_memories
            except Exception as e:
                logger.error(f"Ошибка получения памяти о главах: {e}")
                memory_error = str(e)
                context_errors.append({
                    "title": title,
                    "scope": "chapter_memory",
                    "error": memory_error,
                    **gemini_api_error_metadata(e),
                })

        # 3. Форматирование RAG-контекста
        rag_parts = []
        rag_parts.append("=== WORLD BIBLE & LORE ===")
        if not retrieved_lore_docs:
            rag_parts.append("- No relevant lore found")
        else:
            for doc in retrieved_lore_docs:
                rag_parts.append(f"- {doc}")

        rag_parts.append("\n=== RECENT PLOT DEVELOPMENTS (PAST CHAPTERS) ===")
        if memory_error:
            rag_parts.append("- Error retrieving chapter memory.")
        elif not valid_memories:
            rag_parts.append("- No previous chapter memory found")
        else:
            for memory in valid_memories:
                rag_parts.append(f"- {memory['title']}: {memory['content']}")

        ch_rag_context = "\n".join(rag_parts)
        chapter_contexts[title] = ch_rag_context

        if first_rag_context is None:
            first_rag_context = ch_rag_context

        # Сохранение в БД и завершение стадии
        if db_manager:
            db_manager.update_chapter_context(title, ch_rag_context)
            db_manager.mark_chapter_stage(title, "context_retrieved", "done")

    if first_rag_context is None:
        first_rag_context = ""

    return {
        "rag_context": first_rag_context,
        "chapter_summaries": chapter_summaries,
        "context_errors": context_errors,
        "context_warnings": context_warnings,
        "chapter_contexts": chapter_contexts
    }
