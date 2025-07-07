# agents/tools.py

import logging
import json
import re
import os
import time
from functools import lru_cache
from typing import List, Dict, Any
import google.api_core.exceptions

logger = logging.getLogger("NovelTranslator.Tools")

# Placeholder functions for tools that interact with the file system or external APIs
# These should ideally be implemented in a separate utility module or mocked in tests

def tool_read_chapter(path: str) -> str:
    """Reads the content of a chapter file."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logging.error(f"Ошибка чтения файла {path}: {e}", exc_info=True)
        raise

def tool_write_chapter(path: str, content: str):
    """Writes content to a chapter file."""
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
    except Exception as e:
        logging.error(f"Ошибка записи в файл {path}: {e}", exc_info=True)
        raise

def tool_sanitize_text(text: str) -> tuple[str, list]:
    """Sanitizes text and returns cleaned text and a list of removed patterns."""
    # This is a placeholder. Actual sanitization logic would go here.
    return text, []

def clean_translation_output(text: str) -> str:
    """Cleans and formats the translation output from the LLM."""
    # This is a placeholder. Actual cleaning logic would go here.
    return text.strip().replace("```json", "").replace("```", "").strip()

def tool_translate_chunk(model, prompt: str, settings: Dict[str, Any]) -> str:
    """Translates a chunk of text using the LLM with retry logic for quota errors."""
    max_retries = 5
    initial_delay = 1  # seconds
    retries = 0

    generation_config = {
        "temperature": settings.get('temperature', 0.7),
        "top_p": settings.get('top_p', 0.9),
    }

    while retries < max_retries:
        try:
            response = model.generate_content(prompt, generation_config=generation_config)
            return response.text
        except google.api_core.exceptions.ResourceExhausted as e:
            retries += 1
            delay = initial_delay * (2 ** (retries - 1))  # Exponential backoff
            logger.warning(f"Превышена квота API. Повторная попытка {retries}/{max_retries} через {delay:.2f} секунд...")
            time.sleep(delay)
            if retries == max_retries:
                logger.error(f"Достигнуто максимальное количество повторных попыток. Не удалось выполнить запрос к LLM: {e}", exc_info=True)
                raise
        except Exception as e:
            logging.error(f"Ошибка при запросе к LLM: {e}", exc_info=True)
            raise

def tool_translate_chapter_logic(eng_text: str, title: str, settings: Dict[str, Any], db_manager, kb_manager, model) -> str:
    """Main logic for translating a chapter, incorporating KB and dictionary."""
    # This is a simplified placeholder. Real logic would be more complex.
    prompt = f"Translate the following English text from chapter '{title}' into Russian. Use the provided dictionary and knowledge base for consistency.\n\nEnglish Text: {eng_text}"
    return tool_translate_chunk(model, prompt, settings)

def tool_analyze_inconsistencies(processed_texts: List[Dict[str, str]], dictionary: Dict[str, Any], model, settings: Dict[str, Any]) -> Dict[str, Any]:
    """Анализирует несоответствия в обработанных текстах."""
    logger.info("Запуск анализа несоответствий...")
    all_term_translations = {}

    for chapter_data in processed_texts:
        try:
            eng_text = tool_read_chapter(chapter_data['input_path'])
            rus_text = tool_read_chapter(chapter_data['output_path'])

            logger.debug(f"Анализ несоответствий: Глава {chapter_data.get('title', 'N/A')}")
            logger.debug(f"Английский текст (начало): {eng_text[:200]}...")
            logger.debug(f"Русский текст (начало): {rus_text[:200]}...")

            prompt = f"""Given the following English text and its Russian translation, identify significant English terms and their corresponding Russian translations. Focus on proper nouns, unique concepts, and recurring thematic elements. Return a JSON array of objects, each with 'english_term' and 'russian_translation'.

English Text:
---
{eng_text}
---

Russian Translation:
---
{rus_text}
---

JSON Output:"""
            
            logger.debug(f"Промпт для LLM (анализ несоответствий): {prompt[:500]}...")
            llm_response = tool_translate_chunk(model, prompt, settings)
            logger.debug(f"Сырой ответ LLM (анализ несоответствий): {llm_response[:500]}...")
            response_json = clean_translation_output(llm_response)
            logger.debug(f"Очищенный JSON ответ (анализ несоответствий): {response_json[:500]}...")
            
            extracted_terms = json.loads(response_json)
            
            if not isinstance(extracted_terms, list):
                logger.warning(f"Некорректный формат ответа LLM для извлечения терминов (ожидался список): {llm_response}")
                continue

            for item in extracted_terms:
                if "english_term" in item and "russian_translation" in item:
                    eng_term = item["english_term"]
                    rus_translation = item["russian_translation"]
                    
                    if eng_term not in all_term_translations:
                        all_term_translations[eng_term] = []
                    all_term_translations[eng_term].append(rus_translation)

        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON от LLM при анализе несоответствий: {e}. Ответ: {llm_response}", exc_info=True)
        except Exception as e:
            logger.error(f"Ошибка при обработке главы {chapter_data.get('title', 'N/A')} для анализа несоответствий: {e}", exc_info=True)

    inconsistencies = []
    for eng_term, russian_variants in all_term_translations.items():
        unique_variants = list(set(russian_variants))
        if len(unique_variants) > 1:
            inconsistencies.append({
                "english_term": eng_term,
                "russian_variants": unique_variants,
                "occurrences": len(russian_variants)
            })
    
    logger.info(f"Анализ несоответствий завершен. Найдено {len(inconsistencies)} проблем.")
    return {"inconsistencies": inconsistencies}

from Perevod import constants
...
def tool_evaluate_inconsistency(issue: Dict[str, Any], dictionary: Dict[str, Any], model, settings: Dict[str, Any]) -> Dict[str, Any]:
    """Инструмент "Коллегии Судей" для одной проблемы."""
    eng_term = issue['english_term']
    variants = issue['russian_variants']
    dict_rus_term_data = dictionary.get(eng_term)
    
    if dict_rus_term_data and dict_rus_term_data.get("russian") in variants:
        correct_variant = dict_rus_term_data.get("russian")
        logger.info(f"Вердикт для '{eng_term}' вынесен по словарю: '{correct_variant}' является каноничным.")
        return {"action": constants.UNIFY_ACTION, "english_term": eng_term, "correct_variant": correct_variant, "reason": "dictionary_match"}
            
    prompt = f"""# ROLE: Linguistic Arbiter
# TASK: You are given an English term and several Russian translation variants found in a text. Your task is to choose the single best, most contextually appropriate, and stylistically correct translation.
# ENGLISH TERM: "{eng_term}"
# RUSSIAN VARIANTS: {json.dumps(variants, ensure_ascii=False)}
# INSTRUCTIONS:
1. Analyze the variants.
2. Choose the one that is most likely to be the correct, canonical translation.
3. Return ONLY the chosen variant, and nothing else.
# BEST TRANSLATION:"""
    
    best_variant = clean_translation_output(tool_translate_chunk(model, prompt, settings))
    if not best_variant or best_variant not in variants:
        # Если LLM вернул некорректный ответ, выбираем самый частый или первый вариант
        best_variant = max(set(variants), key=variants.count)
        logger.warning(f"LLM не смог выбрать вариант для '{eng_term}'. Эвристически выбран: '{best_variant}'")
    else:
        logger.info(f"Вердикт для '{eng_term}' вынесен LLM: '{best_variant}' выбран как лучший вариант.")
    
    return {"action": constants.UNIFY_ACTION, "english_term": eng_term, "correct_variant": best_variant, "reason": "llm_choice"}

import pymorphy2

morph = None

def _initialize_pymorphy2():
    global morph
    if morph is None:
        try:
            morph = pymorphy2.MorphAnalyzer()
        except Exception as e:
            logger.error(f"Ошибка инициализации pymorphy2: {e}. Пожалуйста, убедитесь, что словарь установлен (pip install pymorphy2[dicts]).", exc_info=True)
            morph = None

def get_all_word_forms(word: str) -> List[str]:
    """Генерирует все возможные словоформы для заданного слова с помощью pymorphy2."""
    _initialize_pymorphy2()
    if morph is None:
        logger.warning("pymorphy2 не инициализирован, возвращаю только исходное слово.")
        return [word]

    forms = set()
    parsed_word = morph.parse(word)
    if parsed_word:
        for p in parsed_word:
            for form in p.lexeme:
                forms.add(form.word)
    else:
        forms.add(word)
    return list(forms)

def tool_apply_unification_batch(verdicts: List[Dict[str, Any]], processed_chapters: List[Dict[str, str]]):
    """
    [АРХИТЕКТУРНОЕ ИЗМЕНЕНИЕ] Применяет пакет вердиктов ко всем главам.
    Читает каждый файл только один раз, применяет все исправления в памяти и записывает.
    Использует pymorphy2 для надежной замены всех словоформ.
    """
    if not verdicts:
        return

    # Группируем вердикты по правильному варианту
    # {'новый_вариант': ['старый_1', 'старый_2']}
    grouped_verdicts = {}
    for verdict in verdicts:
        correct_variant = verdict['correct_variant']
        if correct_variant not in grouped_verdicts:
            grouped_verdicts[correct_variant] = set()
        # Добавляем все варианты из вердикта, кроме самого правильного
        for old_variant in verdict.get('russian_variants', []):
            if old_variant.lower() != correct_variant.lower():
                grouped_verdicts[correct_variant].add(old_variant)

    if not grouped_verdicts:
        logger.info("Нет вердиктов для применения.")
        return

    # Создаем единый паттерн для замены для всех глав
    # Это более эффективно, чем компилировать паттерн для каждой главы
    patterns_to_replace = []
    for new_word, old_words_set in grouped_verdicts.items():
        all_forms_to_replace = set()
        for old_word in old_words_set:
            # Получаем все словоформы для старого слова
            forms = get_all_word_forms(old_word)
            all_forms_to_replace.update(forms)
        
        if all_forms_to_replace:
            # Создаем паттерн, который ищет любую из этих словоформ
            # Сортируем по длине, чтобы сначала заменялись более длинные формы
            sorted_forms = sorted(list(all_forms_to_replace), key=len, reverse=True)
            pattern = r'\b(' + '|'.join(map(re.escape, sorted_forms)) + r')\b'
            patterns_to_replace.append({'pattern': pattern, 'new': new_word})

    if not patterns_to_replace:
        logger.info("Нет замен для применения после обработки словоформ.")
        return

    logger.info(f"Начинается пакетное исправление {len(patterns_to_replace)} групп вариантов в {len(processed_chapters)} главах.")

    for chapter in processed_chapters:
        file_was_modified = False
        try:
            current_text = tool_read_chapter(chapter['output_path'])
            modified_text = current_text
            
            # Применяем все паттерны к тексту текущей главы
            for replacement_info in patterns_to_replace:
                pattern = replacement_info['pattern']
                new = replacement_info['new']
                
                # Функция для замены, которая сохраняет регистр первой буквы
                def replace_with_case(match):
                    old_word = match.group(0)
                    if old_word.istitle():
                        return new.title()
                    if old_word.isupper():
                        # Простая проверка на то, что слово целиком в верхнем регистре
                        # Можно усложнить, если нужно поддерживать смешанный регистр
                        return new.upper()
                    return new

                modified_text, count = re.subn(pattern, replace_with_case, modified_text, flags=re.IGNORECASE)
                if count > 0:
                    file_was_modified = True
            
            if file_was_modified:
                logger.info(f"Обнаружены и исправлены несоответствия в файле: {chapter['output_path']}")
                tool_write_chapter(chapter['output_path'], modified_text)
        except Exception as e:
            logger.error(f"Ошибка при применении исправлений к файлу {chapter['output_path']}: {e}")

def tool_update_knowledge_base(verdict: Dict[str, Any], db_manager):
    """Обновляет Базу Знаний (словарь) на основе одного вердикта."""
    if verdict.get('action') == 'unify':
        eng_term = verdict['english_term']
        correct_variant = verdict['correct_variant']
        logger.info(f"Обновление словаря: '{eng_term}' -> '{correct_variant}'")
        db_manager.add_or_update_term(eng_term, correct_variant)

def tool_audit_knowledge_base(db_manager):
    """Выполняет аудит и очистку Базы Знаний."""
    logger.info("Запуск инструмента: Аудит Базы Знаний...")
    try:
        merged_count = db_manager.automerge_dictionary_duplicates()
        if merged_count > 0:
            logger.info(f"Аудит завершен. Успешно объединено {merged_count} групп(ы) дубликатов в словаре.")
        else:
            logger.info("Аудит словаря завершен. Дубликатов не найдено.")
        return {"status": "success", "merged_duplicates": merged_count}
    except Exception as e:
        logger.error(f"Ошибка при автоматическом слиянии дубликатов в словаре: {e}", exc_info=True)
        return {"error": f"Ошибка слияния дубликатов: {e}"}

def tool_generate_dictionary_proposals(eng_text: str, rus_text: str, db_manager, model, settings: Dict[str, Any]):
    """Генерирует предложения для словаря на основе текста главы."""
    logger.info("Генерация предложений для словаря...")
    
    existing_terms = db_manager.get_terms_dictionary()
    existing_proposals = db_manager.get_dictionary_proposals()

    prompt = f"""Analyze the following English text from a fantasy novel. Identify 5 to 10 of the most important and significant terms (e.g., unique names, magical spells, specific techniques, key concepts) that should be added to a specialized dictionary. For each identified term, provide its English form, its most likely Russian translation, and a category (e.g., 'character', 'place', 'magic', 'item', 'concept', 'technique', 'other').
    
If no such significant terms are found, respond with 'NO_TERMS'.

Format your response as a JSON array of objects. Example:
[
  {{
    "term": "Mana Flow",
    "translation": "Поток Маны",
    "category": "magic"
  }},
  {{
    "term": "Shadow Step",
    "translation": "Теневой Шаг",
    "category": "technique"
  }}
]

Text to analyze:
---
{eng_text}
---"""
    
    try:
        llm_response = tool_translate_chunk(model, prompt, settings)
        response_json = clean_translation_output(llm_response)
        
        if response_json == 'NO_TERMS':
            logger.info("LLM не нашел значимых терминов для словаря.")
            return

        proposals_data = json.loads(response_json)
        
        if not isinstance(proposals_data, list):
            logger.warning(f"Некорректный формат ответа LLM для словаря (ожидался список): {llm_response}")
            return

        for proposal in proposals_data:
            if "term" in proposal and "translation" in proposal and "category" in proposal:
                eng = proposal["term"]
                rus = proposal["translation"]
                cat = proposal["category"]

                if eng in existing_terms or eng in existing_proposals:
                    continue # Уже есть в словаре или среди предложений

                db_manager.add_dictionary_proposal(eng, rus, cat)
                logger.info(f"Добавлено предложение словаря: {eng} -> {rus} ({cat})")
            else:
                logger.warning(f"Некорректный формат объекта предложения словаря: {proposal}")

    except json.JSONDecodeError as e:
        logger.error(f"Ошибка парсинга JSON от LLM для словаря: {e}. Ответ: {llm_response}", exc_info=True)
    except Exception as e:
        logger.error(f"Ошибка при генерации предложения словаря: {e}", exc_info=True)

def tool_generate_world_bible_proposals(eng_text: str, rus_text: str, db_manager, model, settings: Dict[str, Any]):
    """Генерирует предложения для Библии Вселенной на основе текста главы."""
    logger.info("Генерация предложений для Библии Вселенной...")
    
    # Промпт для извлечения сущностей и их описаний
    prompt = f"""Analyze the following English text from a fantasy novel. Identify 5 to 10 of the most important and significant entities (characters, places, magical items, unique concepts, organizations) that should be added to a "World Bible" (a compendium of lore). For each identified entity, provide its English name, a concise English description, its most likely Russian name, and a category (e.g., 'character', 'place', 'artifact', 'concept', 'organization').

If no significant entities are found, respond with 'NO_ENTITIES'.

Format your response as a JSON array of objects. Example:
[
  {{
    "english_name": "Eldoria",
    "english_description": "An ancient elven city known for its crystal spires and magical academies.",
    "russian_name": "Элдория",
    "category": "place"
  }},
  {{
    "english_name": "Shadowblade",
    "english_description": "A legendary dagger imbued with the essence of night, capable of piercing any armor.",
    "russian_name": "Теневой Клинок",
    "category": "artifact"
  }}
]

Text to analyze:
---
{eng_text}
---"""
    try:
        llm_response = tool_translate_chunk(model, prompt, settings)
        response_json = clean_translation_output(llm_response)

        if response_json == 'NO_ENTITIES':
            logger.info("LLM не нашел значимых сущностей для Библии Вселенной.")
            return

        proposals_data = json.loads(response_json)
        
        if not isinstance(proposals_data, list):
            logger.warning(f"Некорректный формат ответа LLM для Библии Вселенной (ожидался список): {llm_response}")
            return

        existing_bible_entries = db_manager.get_world_bible()
        existing_bible_proposals = db_manager.get_world_bible_proposals()

        for proposal in proposals_data:
            if "english_name" in proposal and "english_description" in proposal and "russian_name" in proposal and "category" in proposal:
                eng_name = proposal["english_name"]
                rus_name = proposal["russian_name"]
                cat = proposal["category"]
                desc = proposal["english_description"]

                if eng_name in existing_bible_entries or eng_name in existing_bible_proposals:
                    continue # Уже есть в Библии или среди предложений

                db_manager.add_world_bible_proposal(eng_name, rus_name, cat, desc)
                logger.info(f"Добавлено предложение Библии Вселенной: {eng_name} ({cat})")
            else:
                logger.warning(f"Некорректный формат объекта предложения Библии Вселенной: {proposal}")

    except json.JSONDecodeError as e:
        logger.error(f"Ошибка парсинга JSON от LLM для Библии Вселенной: {e}. Ответ: {llm_response}", exc_info=True)
    except Exception as e:
        logger.error(f"Ошибка при генерации предложения Библии Вселенной: {e}", exc_info=True)

