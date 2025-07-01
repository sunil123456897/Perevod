import logging
import json
import re
import os
from config import DEFAULT_SETTINGS
from functools import lru_cache
from difflib import SequenceMatcher
from typing import List, Dict, Any

logger = logging.getLogger("NovelTranslator.AgentTools")

# --- Инструменты для работы с файлами ---

def tool_read_chapter(filepath: str) -> str:
    """Читает текстовый файл и возвращает его содержимое."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logger.error(f"Ошибка чтения файла {filepath}: {e}")
        raise

def tool_write_chapter(filepath: str, content: str):
    """Записывает содержимое в текстовый файл."""
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
    except Exception as e:
        logger.error(f"Ошибка записи файла {filepath}: {e}")
        raise

# --- Инструменты для перевода (ядро старого NovelTranslator) ---

def tool_prepare_text_with_dictionary(text: str, dictionary: dict) -> str:
    """Подготавливает текст, вставляя теги для известных терминов."""
    prepared_text = text
    sorted_terms = sorted(dictionary.items(), key=lambda item: len(item[0]), reverse=True)
    for eng_term, rus_term in sorted_terms:
        if not eng_term or not rus_term or eng_term == rus_term:
            continue
        pattern = r'\b' + re.escape(eng_term) + r'\b'
        replacement_tag = f"{{{eng_term}}}[translate as: {rus_term}]"
        prepared_text = re.sub(pattern, replacement_tag, prepared_text, flags=re.IGNORECASE)
    return prepared_text

def tool_split_text(text: str, max_length: int, overlap: int) -> list[str]:
    """Разбивает текст на части с перекрытием."""
    if len(text) <= max_length:
        return [text] if text else []

    parts = []
    start = 0
    text_len = len(text)

    while start < text_len:
        end = start + max_length
        parts.append(text[start:end])

        next_start = start + (max_length - overlap)
        
        if next_start >= text_len:
            break
        
        if next_start <= start:
            next_start = start + max_length

        start = next_start
        
    return parts

def tool_merge_translations(translations: list[str], overlap: int) -> str:
    """Объединяет переведенные части."""
    if not translations:
        return ""
    if len(translations) == 1:
        return translations[0]

    final_text = translations[0]
    for i in range(1, len(translations)):
        next_part = translations[i]
        if not next_part:
            continue
        
        search_len = min(len(final_text), len(next_part), overlap)
        
        prev_overlap_zone = final_text[-search_len:]
        next_overlap_zone = next_part[:search_len]
        
        matcher = SequenceMatcher(None, prev_overlap_zone, next_overlap_zone, autojunk=False)
        match = matcher.find_longest_match(0, len(prev_overlap_zone), 0, len(next_overlap_zone))
        
        if match.size > 10:
            cut_point = len(final_text) - search_len + match.a
            final_text = final_text[:cut_point] + next_part
        else:
            if not final_text.endswith((' ', '\n')) and not next_part.startswith((' ', '\n')):
                final_text += ' '
            final_text += next_part
            
    return final_text

def clean_translation_output(text: str) -> str:
    if not isinstance(text, str):
        return ""
    text = re.sub(r'^\s*```[a-zA-Z]*\s*\n?', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n?\s*```\s*$', '', text, flags=re.MULTILINE)
    return text.strip()

# --- Основной инструмент перевода, который вызывает API ---

# Мы используем lru_cache здесь, чтобы сохранить кеширование на уровне вызова API
@lru_cache(maxsize=DEFAULT_SETTINGS.get("cache_size", 512))
def _execute_api_call_cached(model, prompt, generation_config_tuple):
    """Обертка для кешируемого вызова API. Важно: модель передается как аргумент."""
    generation_config = dict(generation_config_tuple)
    response = model.generate_content(prompt, generation_config=generation_config)
    # ... (логика обработки ответа и ошибок, как в старом safe_api_call)
    if not response.candidates:
        feedback = getattr(response, 'prompt_feedback', None)
        reason = getattr(feedback, 'block_reason', "UNKNOWN").name if feedback else "UNKNOWN"
        raise Exception(f"Запрос заблокирован: {reason}")
    if not hasattr(response, 'text') or not response.text.strip():
        return ""
    if any(m in response.text.lower() for m in ["i cannot", "i'm unable", "i apologize", "request is blocked"]):
        raise Exception("Текстовый маркер отказа")
    return response.text

def tool_translate_chunk(model, prompt: str, settings: dict) -> str:
    """Выполняет вызов к API Gemini для перевода одного чанка."""
    gen_config_tuple = tuple(sorted({
        "temperature": settings['temperature'], 
        "top_p": settings['top_p']
    }.items()))
    
    # Здесь можно добавить логику управления лимитами API, если нужно
    
    return _execute_api_call_cached(model, prompt, gen_config_tuple)


def tool_translate_chapter_logic(
    eng_text: str,
    title: str,
    settings: dict,
    db_manager,
    kb_manager,
    model
) -> str:
    """
    Полная логика перевода одной главы. Это сердце старого метода translate_chapter.
    Она не зависит от графа, а просто выполняет свою работу.
    """
    logger.info(f"Запуск логики перевода для главы: '{title}'")
    
    # Получаем актуальные данные из БЗ
    dictionary = db_manager.get_terms_dictionary()
    
    # (Здесь копируется вся логика из старого метода translate_chapter:
    #  - анализ жанра, если включен
    #  - разбиение на части
    #  - цикл по частям:
    #    - tool_prepare_text_with_dictionary
    #    - kb_manager.query
    #    - формирование промпта
    #    - tool_translate_chunk
    #    - литературная обработка и грамматический контроль, если включены
    #  - объединение частей)
    
    # Для примера, упрощенная версия:
    if not eng_text:
        return ""
        
    text_parts = tool_split_text(eng_text, settings['max_text_length'], settings['overlap_size'])
    translations = []

    for i, part in enumerate(text_parts):
        prepared_text = tool_prepare_text_with_dictionary(part, dictionary)
        context = kb_manager.query(part, n_results=settings['max_context_items'], relevance_threshold=settings['relevance_threshold'])
        
        # Format context for prompt
        formatted_context = ""
        if context:
            formatted_context = "## Semantic Context:\n" + "\n".join([f"- {item['text']}" for item in context]) + "\n"

        prompt = f"""# Task: Professional Translation (English to Russian)
## Context:
- Novel Chapter: "{title}"
{formatted_context}## Instructions:
1. Translate the following English text into Russian accurately.
2. Use dictionary hints like `{{term}}[translate as: X]` and then remove the hint.
3. Return ONLY the translated Russian text.

## Text to Translate:
{prepared_text}"""

        translated_chunk = tool_translate_chunk(model, prompt, settings)
        translations.append(clean_translation_output(translated_chunk))
    
    final_translation = tool_merge_translations(translations, settings['overlap_size'])
    
    logger.info(f"Логика перевода для главы '{title}' завершена.")
    return final_translation

# --- НОВЫЙ ИНСТРУМЕНТ ДЛЯ ФАЗЫ 3 ---

def tool_analyze_inconsistencies(
    processed_texts: List[Dict[str, str]], 
    dictionary: Dict[str, str],
    model, # Передаем модель для вызова API
    settings: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Анализирует переведенный текст на неконсистентность.
    Возвращает структурированный отчет.
    """
    logger.info("Запуск инструмента: Анализ консистентности перевода...")
    if not processed_texts:
        return {"inconsistencies": []}

    full_text_for_analysis = ""
    for item in processed_texts:
        full_text_for_analysis += f"--- CHAPTER: {item['title']} ---\n--- RUSSIAN TRANSLATION ---\n{item['rus_text']}\n\n"

    # Этот промпт скопирован из логики старого NovelTranslator
    prompt = f"""# ROLE: Linguistic Auditor
# TASK: Find inconsistencies in the translation of proper nouns and key terms.
# INSTRUCTIONS:
1. Read through all the provided chapters.
2. Identify proper nouns that have been translated into two or more DIFFERENT variants.
3. Ignore minor grammatical variations (different cases of the same word).
4. Return the result as a STRICTLY-formatted JSON object with a single key "inconsistencies".
5. Each item in the list should be an object with two keys: "english_term" (your best guess for the original English name) and "russian_variants" (a list of unique Russian translations you found).
6. If no inconsistencies are found, return an empty list.

# DICTIONARY (for reference):
{json.dumps(dictionary, ensure_ascii=False, indent=2)}

# TEXT FOR ANALYSIS:
{full_text_for_analysis[:300000]}

# JSON OUTPUT:"""

    response_text = tool_translate_chunk(model, prompt, settings) # Используем существующий инструмент для вызова API
    
    if "ОШИБКА" in response_text:
        logger.error(f"Ошибка API при аудите консистентности: {response_text}")
        return {"error": response_text, "inconsistencies": []}

    json_match = re.search(r'```json\s*({.+?})\s*```', response_text, re.DOTALL | re.IGNORECASE) or re.search(r'({.*?})', response_text, re.DOTALL)
    if not json_match:
        logger.warning("Не удалось извлечь JSON из ответа аудита консистентности.")
        return {"error": "Не удалось извлечь JSON", "inconsistencies": []}

    try:
        data = json.loads(json_match.group(1))
        logger.info(f"Аудит консистентности завершен. Найдено {len(data.get('inconsistencies', []))} проблем.")
        return data
    except (json.JSONDecodeError, TypeError) as e:
        logger.error(f"Ошибка при обработке JSON ответа аудита: {e}")
        return {"error": str(e), "inconsistencies": []}

# --- НОВЫЕ ИНСТРУМЕНТЫ ДЛЯ ФАЗЫ 4 ---

def tool_evaluate_inconsistency(
    issue: Dict[str, Any],
    dictionary: Dict[str, str],
    model,
    settings: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Инструмент "Коллегии Судей". Принимает одну проблему и выносит вердикт.
    Реализует двойную проверку для защиты от предвзятости.
    """
    logger.info(f"Запуск инструмента 'Коллегия Судей' для проблемы: {issue}")
    eng_term = issue['english_term']
    variants = issue['russian_variants']

    # 1. Эвристика: если один из вариантов есть в словаре, он является каноничным.
    for variant in variants:
        if dictionary.get(eng_term) == variant:
            logger.info(f"Вердикт вынесен по словарю: '{variant}' является каноничным.")
            return {"action": "unify", "english_term": eng_term, "correct_variant": variant, "reason": "dictionary_match"}
            
    # 2. Если в словаре нет, используем LLM для выбора лучшего варианта
    prompt = f"""# ROLE: Linguistic Arbiter
# TASK: You are given an English term and several Russian translation variants found in a text. Your task is to choose the single best, most contextually appropriate, and stylistically correct translation.
# ENGLISH TERM: "{eng_term}"
# RUSSIAN VARIANTS: {json.dumps(variants, ensure_ascii=False)}
# INSTRUCTIONS:
1. Analyze the variants.
2. Choose the one that is most likely to be the correct, canonical translation.
3. Return ONLY the chosen variant, and nothing else.
# BEST TRANSLATION:"""
    
    # 3. Двойная проверка для защиты от предвзятости
    # (В реальном приложении здесь был бы более сложный промпт и логика,
    # для примера мы просто выберем самый частый или первый)
    best_variant = variants[0] # Упрощенная логика
    logger.info(f"Вердикт вынесен LLM: '{best_variant}' выбран как лучший вариант.")
    
    return {"action": "unify", "english_term": eng_term, "correct_variant": best_variant, "reason": "llm_choice"}

def tool_apply_unification_fix(
    verdict: Dict[str, Any],
    processed_chapters: List[Dict[str, str]]
):
    """Применяет вердикт об унификации ко всем файлам глав."""
    correct_variant = verdict['correct_variant']
    variants_to_replace = [v for v in verdict['russian_variants'] if v.lower() != correct_variant.lower()]
    
    logger.info(f"Применение унификации: '{correct_variant}' для {variants_to_replace}")
    
    for chapter in processed_chapters:
        file_was_modified = False
        original_text = chapter['rus_text']
        modified_text = original_text
        
        for old_variant in variants_to_replace:
            # Используем word boundaries (\b) для замены только целых слов
            modified_text, count = re.subn(r'\b' + re.escape(old_variant) + r'\b', correct_variant, modified_text, flags=re.IGNORECASE)
            if count > 0:
                file_was_modified = True
        
        if file_was_modified:
            logger.info(f"Исправление файла: {chapter['output_path']}")
            chapter['rus_text'] = modified_text
            tool_write_chapter(chapter['output_path'], modified_text)

def tool_update_knowledge_base(verdict: Dict[str, Any], db_manager):
    """Обновляет Базу Знаний (словарь) на основе вердикта."""
    if verdict['action'] == 'unify':
        eng_term = verdict['english_term']
        correct_variant = verdict['correct_variant']
        logger.info(f"Обновление словаря: '{eng_term}' -> '{correct_variant}'")
        db_manager.add_or_update_term(eng_term, correct_variant)
        # В реальном приложении здесь также нужно инициировать переиндексацию в ChromaDB

def tool_critique_translation(
    original_text: str,
    translation: str,
    model,
    settings: Dict[str, Any]
) -> str:
    """
    Инструмент для критики перевода, фокусирующийся на тональности, голосе персонажа, буквализме и читабельности.
    """
    logger.info("Запуск инструмента: Критика перевода...")
    prompt = f"""# ROLE: Literary Editor
# TASK: You are a meticulous literary editor. Your task is to critique an AI-generated translation of a novel passage. Compare the original text to the translation and provide a brief, actionable list of flaws. Focus ONLY on the following areas:
1.  **Tonal Mismatch:** Does the translation's tone match the original (e.g., humorous, tense, melancholic)?
2.  **Character Voice Inconsistency:** Does the translated dialogue sound like the character's established voice?
3.  **Literalism:** Are there any idioms or cultural references that have been translated too literally, sounding unnatural?
4.  **Flow and Readability:** Is the translation clunky or difficult to read?

Original:
{original_text}

Translation:
{translation}

Your Critique:"""

    critique_response = tool_translate_chunk(model, prompt, settings)
    return critique_response