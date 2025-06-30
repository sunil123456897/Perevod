# translator.py

import google.generativeai as genai
import json
import os
import re
import time
import logging
import threading
import spacy
from collections import Counter

from functools import lru_cache
from difflib import SequenceMatcher
from config import DEFAULT_SETTINGS
# [ИЗМЕНЕНО] Импортируем новые менеджеры
from database.database_manager import DatabaseManager
from knowledge_base.knowledge_base_manager import KnowledgeBaseManager

logger = logging.getLogger("NovelTranslator.Core")

class ApiRefusalError(Exception): pass
class InitializationError(Exception): pass

class NovelTranslator:
    def __init__(self, settings=None):
        logger.debug("Инициализация NovelTranslator...")
        self.settings = DEFAULT_SETTINGS.copy()
        if settings:
            self.settings.update(settings)
        
        self.project_name = self.settings.get('project_name')
        if not self.project_name or self.project_name == "Default":
             raise InitializationError("Для работы переводчика должен быть выбран корректный проект.")

        self._apply_settings_to_attributes()

        if not self.API_KEY:
            logger.warning("API ключ не предоставлен. Методы, требующие API, не будут работать.")
        
        # [ИЗМЕНЕНО] Инициализация менеджеров данных
        self.db_manager = DatabaseManager(project_name=self.project_name)
        self.kb_manager = KnowledgeBaseManager(
            project_name=self.project_name, 
            api_key=self.API_KEY,
            embedding_model_name=self.EMBEDDING_MODEL_NAME
        )
        
        self.api_call_timestamps = []
        self.model = None
        self.embedding_model = None # Это теперь используется внутри kb_manager
        self.nlp_ner = None
        self.stats = { "chapters_translated": 0, "total_words_processed": 0, "terms_added": 0, "api_calls_executed": 0, "api_errors": 0, "api_refusals": 0, "api_cache_hits": 0 }
        
        # [ИЗМЕНЕНО] Блокировка больше не нужна для данных, т.к. SQLite транзакционен.
        # Оставим ее для статистики и API лимитов.
        self.api_lock = threading.Lock()
        self.stats_lock = threading.Lock()
        
        # [ИЗМЕНЕНО] Загрузка данных через DatabaseManager
        self.translation_dictionary = self.db_manager.get_terms_dictionary()
        self.world_bible = self.db_manager.get_world_bible()
        
        if self.API_KEY:
            self.initialize_model()
            self._load_spacy_model()
        
        logger.info(f"Переводчик инициализирован для проекта '{self.project_name}'. Загружено {len(self.translation_dictionary)} терминов, {len(self.world_bible)} записей в Библии. Индекс: {self.kb_manager.collection.count()} векторов.")
    
    

    def _apply_settings_to_attributes(self):
        for key, value in self.settings.items():
            setattr(self, key.upper(), value)

    def initialize_model(self):
        if not self.API_KEY:
            logger.error("Невозможно инициализировать модель: отсутствует API ключ.")
            return False
        logger.debug(f"Инициализация моделей Gemini: {self.MODEL_NAME}, {self.EMBEDDING_MODEL_NAME}")
        try:
            genai.configure(api_key=self.API_KEY)
            self.model = genai.GenerativeModel(self.MODEL_NAME)
            self.embedding_model = genai.GenerativeModel(self.EMBEDDING_MODEL_NAME)
            self.model.count_tokens("test")
            logger.info(f"Модели Gemini ({self.MODEL_NAME}, {self.EMBEDDING_MODEL_NAME}) успешно инициализированы.")
            return True
        except Exception as e:
            logger.error(f"Критическая ошибка при инициализации моделей Gemini: {e}", exc_info=True)
            self.model = None
            self.embedding_model = None
            raise InitializationError(f"Не удалось инициализировать модели Gemini. Проверьте API ключ и имена моделей.")

    def _load_spacy_model(self):
        def load():
            model_name = self.SPACY_MODEL_NAME
            logger.info(f"Попытка загрузки NLP-модели: {model_name}...")
            try:
                if not spacy.util.is_package(model_name):
                    logger.warning(f"Модель spaCy '{model_name}' не найдена. Попытка скачать...")
                    spacy.cli.download(model_name)
                self.nlp_ner = spacy.load(model_name, exclude=["parser", "lemmatizer"])
                logger.info(f"NLP-модель '{model_name}' успешно загружена.")
            except Exception as e:
                logger.error(f"Ошибка загрузки NLP-модели '{model_name}': {e}. NER не будет использоваться.")
                self.nlp_ner = None
        threading.Thread(target=load, daemon=True, name="SpaCyLoader").start()

    

    

    
    
    

    

    

    

    

    def manage_api_rate_limit(self):
        if self.API_RATE_LIMIT <= 0:
            return
        with self.api_lock:
            now = time.monotonic()
            self.api_call_timestamps = [t for t in self.api_call_timestamps if now - t < 60]
            if len(self.api_call_timestamps) >= self.API_RATE_LIMIT:
                wait = 60.0 - (now - self.api_call_timestamps[0])
                if wait > 0:
                    logger.info(f"Достигнут лимит API. Ожидание {wait:.2f} сек...")
                    time.sleep(wait)
            self.api_call_timestamps.append(time.monotonic())

    def safe_api_call(self, prompt, retry_count=0):
        if not self.model:
            return "ОШИБКА: Модель не инициализирована"
        
        cfg = tuple(sorted({"temperature": self.TEMPERATURE, "top_p": self.TOP_P, "top_k": self.TOP_K, "max_output_tokens": self.MAX_OUTPUT_TOKENS}.items()))
        
        try:
            return self._execute_api_call_cached(prompt, cfg)
        except ApiRefusalError as e:
            with self.stats_lock:
                self.stats["api_refusals"] += 1
            logger.error(f"API отказал: {e}")
            return f"ОШИБКА: API отказал ({e})"
        except Exception as e:
            with self.stats_lock:
                self.stats["api_errors"] += 1
            logger.warning(f"Ошибка API (попытка {retry_count + 1}/{self.MAX_RETRIES}): {e}")
            if retry_count < self.MAX_RETRIES:
                time.sleep(self.RETRY_DELAY * (retry_count + 1))
                return self.safe_api_call(prompt, retry_count + 1)
            
            logger.error(f"Превышено максимальное количество попыток: {e}")
            return f"ОШИБКА: {e} (после {self.MAX_RETRIES} попыток)"
    
    @lru_cache(maxsize=DEFAULT_SETTINGS["cache_size"])
    def _execute_api_call_cached(self, prompt, generation_config_tuple):
        cache_info = self._execute_api_call_cached.cache_info()
        if cache_info.hits > self.stats.get('api_cache_hits', 0):
            logger.debug("Ответ получен из кеша API.")
            with self.stats_lock:
                self.stats['api_cache_hits'] = cache_info.hits
        
        generation_config = dict(generation_config_tuple)
        self.manage_api_rate_limit()
        with self.stats_lock:
            self.stats["api_calls_executed"] += 1
        response = self.model.generate_content(prompt, generation_config=generation_config)

        if not response.candidates:
            feedback = getattr(response, 'prompt_feedback', None)
            reason = getattr(feedback, 'block_reason', "UNKNOWN").name if feedback else "UNKNOWN"
            raise ApiRefusalError(f"Запрос заблокирован: {reason}")
        if not hasattr(response, 'text') or not response.text.strip():
            return ""
        if any(m in response.text.lower() for m in ["i cannot", "i'm unable", "i apologize", "request is blocked"]):
            raise ApiRefusalError("Текстовый маркер отказа")
        return response.text
    
    

    def build_semantic_index(self, progress_callback=None):
        """[ИЗМЕНЕНО] Делегирует построение индекса менеджеру."""
        logger.info("Запуск построения семантического индекса через KBManager...")
        try:
            self.kb_manager.rebuild_index_from_db(self.db_manager, progress_callback)
            return True
        except Exception as e:
            logger.error(f"Ошибка при вызове rebuild_index_from_db: {e}", exc_info=True)
            return False

    def find_relevant_context(self, text_chunk):
        """[ИЗМЕНЕНО] Делегирует семантический поиск менеджеру."""
        return self.kb_manager.query(
            query_text=text_chunk, 
            n_results=self.MAX_CONTEXT_ITEMS, 
            relevance_threshold=self.RELEVANCE_THRESHOLD
        )

    

    

    def prepare_text_with_dictionary(self, text):
        prepared_text = text
        current_dictionary = self.translation_dictionary.copy()
        sorted_terms = sorted(current_dictionary.items(), key=lambda item: len(item[0]), reverse=True)
        for eng_term, rus_term in sorted_terms:
            if not eng_term or not rus_term or eng_term == rus_term:
                continue
            pattern = r'\b' + re.escape(eng_term) + r'\b'
            replacement_tag = f"{{{eng_term}}}[translate as: {rus_term}]"
            try:
                prepared_text, count = re.subn(pattern, replacement_tag, prepared_text, flags=re.IGNORECASE)
            except re.error as e:
                logger.warning(f"Ошибка re для термина '{eng_term}': {e}")
        return prepared_text
    
    def split_text(self, text):
        if len(text) <= self.MAX_TEXT_LENGTH:
            return [text] if text else []

        parts = []
        start = 0
        text_len = len(text)

        while start < text_len:
            end = start + self.MAX_TEXT_LENGTH
            parts.append(text[start:end])

            next_start = start + (self.MAX_TEXT_LENGTH - self.OVERLAP_SIZE)
            
            if next_start >= text_len:
                break
            
            if next_start <= start:
                next_start = start + self.MAX_TEXT_LENGTH

            start = next_start
            
        return parts

    def merge_translations(self, translations):
        if not translations:
            return ""
        if len(translations) == 1:
            return translations[0]

        final_text = translations[0]
        for i in range(1, len(translations)):
            next_part = translations[i]
            if not next_part:
                continue
            
            search_len = min(len(final_text), len(next_part), self.OVERLAP_SIZE)
            
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

    def clean_translation_output(self, text):
        if not isinstance(text, str):
            return ""
        text = re.sub(r'^\s*```[a-zA-Z]*\s*\n?', '', text, flags=re.MULTILINE)
        text = re.sub(r'\n?\s*```\s*$', '', text, flags=re.MULTILINE)
        return text.strip()

    def translate_chapter(self, english_text, chapter_title="Без названия", progress_callback=None):
        logger.info(f"Начало перевода главы: '{chapter_title}'")
        start_time = time.monotonic()
        def report_progress(value, message):
            if progress_callback:
                progress_callback(value, message)

        report_progress(0, f"Подготовка главы '{chapter_title}'")
        
        genre_analysis = {}
        if self.ENABLE_GENRE_ANALYSIS:
            report_progress(5, "Анализ жанра и стиля...")
            genre_prompt = f'Analyze the genre and style of the following novel chapter excerpt. Provide the output strictly in JSON format with keys: "genre", "subgenre", "tone", "style", and "key_themes".\n\nExcerpt from "{chapter_title}":\n---\n{english_text[:2500]}\n---\nJSON Output:'
            genre_response = self.safe_api_call(genre_prompt)
            if not genre_response or genre_response.startswith("ОШИБКА:"):
                logger.warning(f"Не удалось получить анализ жанра: {genre_response}")
            else:
                json_match = re.search(r'```json\s*({.*?})\s*```', genre_response, re.DOTALL) or re.search(r'({.+})', genre_response, re.DOTALL)
                if json_match:
                    try:
                        genre_analysis = json.loads(json_match.group(1))
                    except json.JSONDecodeError as e:
                        logger.warning(f"Не удалось распарсить JSON анализа жанра: {e}")
        
        report_progress(10, "Разбиение текста на части...")
        text_parts = self.split_text(english_text)
        if not text_parts and english_text:
            text_parts = [english_text]
        if not text_parts:
            return ""

        translations = []
        total_parts = len(text_parts)
        
        for i, part in enumerate(text_parts):
            base_progress = 15 + (i / total_parts) * 75
            report_progress(base_progress, f"Перевод части {i+1}/{total_parts}")
            
            prepared_text = self.prepare_text_with_dictionary(part)
            semantic_context = self.find_relevant_context(part)

            translation_prompt = f"""# Task: Professional Translation (English to Russian)

## Context:
- Novel Chapter: "{chapter_title}"
- Genre Info: {json.dumps(genre_analysis)}
{semantic_context}
## Instructions:
1. Translate the following English text into Russian accurately, preserving the meaning, style, and tone defined in the context.
2. Use dictionary hints like `{{term}}[translate as: X]` and then remove the hint, leaving only the translation X.
3. Maintain original formatting (paragraphs, dialogues).
4. Return ONLY the translated Russian text.

## Text to Translate:
{prepared_text}"""
            
            current_translation = self.safe_api_call(translation_prompt)
            if current_translation.startswith("ОШИБКА:"):
                translations.append(f"\n[ОШИБКА ПЕРЕВОДА ЧАСТИ: {current_translation}]\n")
                continue
            current_translation = self.clean_translation_output(current_translation)

            if self.ENABLE_LITERARY_STEP:
                report_progress(base_progress + (30 / total_parts), f"Литературная обработка части {i+1}/{total_parts}")
                literary_prompt = f"""# Task: Literary Refinement of Russian Translation\n\n## Instructions:\n1. Refine the provided Russian translation to make it sound more natural and expressive, while staying accurate to the original English meaning.\n2. Improve phrasing, word choice, and flow.\n3. Use the context from the semantic analysis and chapter info to guide the style.\n\n## Original English (for Context):\n{part[:1000]}...\n\n## Current Russian Translation for Refinement:\n{current_translation}"""
                improved = self.safe_api_call(literary_prompt)
                if not improved.startswith("ОШИБКА:") and improved.strip():
                    current_translation = self.clean_translation_output(improved)

            if self.ENABLE_GRAMMAR_GUARDIAN and not current_translation.startswith("ОШИБКА:"):
                report_progress(base_progress + (60 / total_parts), f"Грам. проверка части {i+1}/{total_parts}")
                used_terms = {eng: rus for eng, rus in self.translation_dictionary.items() if re.search(r'\b' + re.escape(eng) + r'\b', part, re.IGNORECASE)}
                if used_terms:
                    guardian_prompt = f"""# ROLE: Russian Grammar Guardian\n# TASK: Review the Russian translation. Ensure all special terms from the dictionary are used in the correct grammatical case and form based on the sentence's context.\n#\n# DICTIONARY (English -> Russian):\n{json.dumps(used_terms, ensure_ascii=False, indent=2)}\n#\n# ORIGINAL ENGLISH (for context):\n---\n{part}\n---\n#\n# RUSSIAN TRANSLATION TO CHECK:\n---\n{current_translation}\n---\n#\n# INSTRUCTIONS:\n1. Read the Russian sentence.\n2. For each term in the dictionary, check if its Russian translation is used correctly (correct case, number, gender).\n3. If a term is used incorrectly, fix the sentence.\n4. If the translation is already grammatically perfect, return it unchanged.\n5. **Return ONLY the final, corrected Russian text.** Nothing else.\n\n# CORRECTED RUSSIAN TEXT:"""
                    corrected_translation = self.safe_api_call(guardian_prompt)
                    if not corrected_translation.startswith("ОШИБКА:") and corrected_translation.strip():
                        current_translation = self.clean_translation_output(corrected_translation)
            
            translations.append(current_translation)

        report_progress(90, "Объединение переведенных частей...")
        final_translation = self.merge_translations(translations)
        
        if self.ENABLE_SMART_DICTIONARY_UPDATE:
            threading.Thread(target=self.update_dictionary_smart, args=(english_text,), daemon=True, name="DictUpdater").start()
        if self.ENABLE_BIBLE_ANALYSIS:
            threading.Thread(target=self.analyze_and_propose_bible_entries, args=(english_text,), daemon=True, name="BibleAnalyzer").start()
        
        with self.stats_lock:
            self.stats["chapters_translated"] += 1
            self.stats["total_words_processed"] += len(english_text.split())
        logger.info(f"Перевод главы '{chapter_title}' завершен за {time.monotonic() - start_time:.2f} сек.")
        report_progress(100, f"Глава '{chapter_title}' переведена")
        return final_translation

    def analyze_and_propose_bible_entries(self, text):
        logger.info("Начало анализа текста для Библии Вселенной...")
        
        known_entities = set(entry.english_name for entry in self.db_manager.get_all_world_bible_entries())

        prompt = f"""# ROLE: World-Building Analyst
# TASK: Read the provided text from a novel. Identify the most important and recurring characters, locations, items, and concepts. For each identified entity, provide a brief, descriptive summary and a likely Russian translation.

# INSTRUCTIONS:
1. Focus only on proper nouns and unique concepts that seem important to the plot or world.
2. Ignore minor, one-off characters or generic locations unless they are described in great detail.
3. For each entity, provide:
    - "name" (as it appears in the text)
    - "russian_name" (a plausible Russian translation of the name)
    - "category" (one of: 'character', 'location', 'item', 'concept', 'organization', 'other')
    - "description" (a concise one-sentence description in Russian based on the text)
4. Return the result STRICTLY as a JSON object with a single key "bible_proposals", which is a list of objects.
5. If no significant entities are found, return an empty list.

# TEXT FOR ANALYSIS:
---
{text[:self.NER_TEXT_LIMIT]}
---

# JSON OUTPUT:"""

        response = self.safe_api_call(prompt)
        if response.startswith("ОШИБКА:"):
            logger.error(f"Ошибка API при анализе для Библии: {response}")
            return

        json_match = re.search(r'```json\s*({.+?})\s*```', response, re.DOTALL | re.IGNORECASE) or re.search(r'({.*?})', response, re.DOTALL)
        if not json_match:
            logger.warning("Не удалось извлечь JSON из ответа анализа для Библии.")
            return

        try:
            data = json.loads(json_match.group(1))
            proposals = data.get("bible_proposals", [])
            if not isinstance(proposals, list):
                logger.warning("Предложения для Библии получены в неверном формате (не список).")
                return
            
            new_proposal_count = 0
            for proposal in proposals:
                name = proposal.get("name")
                if name and isinstance(name, str) and name not in known_entities:
                    self.db_manager.add_world_bible_proposal(english_name=name, russian_name=proposal.get("russian_name"), category=proposal.get("category"), description=proposal.get("description"))
                    new_proposal_count += 1
            
            if new_proposal_count > 0:
                logger.info(f"Найдено и добавлено {new_proposal_count} новых предложений для Библии Вселенной.")

        except (json.JSONDecodeError, TypeError) as e:
            logger.error(f"Ошибка при обработке JSON ответа для Библии: {e}")

    def update_dictionary_smart(self, original_text):
        if not self.nlp_ner:
            logger.warning("NER-модель не загружена, умное обновление словаря невозможно.")
            return
        logger.info("Начало умного обновления словаря...")
        doc = self.nlp_ner(original_text[:self.NER_TEXT_LIMIT])
        candidates = list(set([ent.text.strip() for ent in doc.ents if ent.label_ in ['PERSON', 'ORG', 'GPE', 'PRODUCT', 'WORK_OF_ART', 'LAW', 'EVENT', 'FAC'] and len(ent.text.strip()) > 2]))
        
        existing_terms = set(term.english_term for term in self.db_manager.get_all_terms())
        
        candidates = [c for c in candidates if c not in existing_terms]

        if not candidates:
            logger.info("Новых кандидатов для словаря не найдено.")
            return
        logger.info(f"Найдено {len(candidates)} кандидатов для словаря. Отправка на валидацию...")
        
        validation_prompt = f"""# ROLE: Intelligent Dictionary Assistant\n# TASK: Analyze a list of candidate terms extracted from a novel. Validate them, provide accurate Russian translations, categorize them, and rate your confidence.\n#\n# LIST OF CANDIDATE TERMS:\n{json.dumps(candidates, indent=2)}\n#\n# INSTRUCTIONS:\n1. For each candidate, decide if it's a significant, recurring, or unique term for a fantasy/sci-fi novel (a proper noun, spell, special item, etc.). IGNORE common words.\n2. If the term is significant, provide a context-appropriate Russian translation.\n3. Assign a specific category: 'character', 'location', 'organization', 'item', 'spell', 'title', 'race', 'concept', 'other'.\n4. Rate your confidence in this term being important and the translation being correct on a scale from 0.0 to 1.0.\n5. Return the result STRICTLY as a JSON object with a single key "validated_terms", which is a list of objects. Each object must have keys "english", "russian", "category", "confidence".\n\n# JSON OUTPUT:"""
        response = self.safe_api_call(validation_prompt)
        if response.startswith("ОШИБКА:"):
            logger.error(f"Ошибка на этапе валидации словаря: {response}")
            return
        
        json_match = re.search(r'```json\s*({.+?})\s*```', response, re.DOTALL | re.IGNORECASE) or re.search(r'({.*?})', response, re.DOTALL)
        if not json_match:
            logger.warning("Не удалось извлечь JSON из ответа валидации словаря.")
            return
        
        try:
            data = json.loads(json_match.group(1))
            validated_terms = data.get("validated_terms", [])
            if not isinstance(validated_terms, list):
                return
            newly_proposed_count = 0
            for term_data in validated_terms:
                eng = term_data.get("english")
                if eng and term_data.get("confidence", 0) >= self.DICTIONARY_CONFIDENCE_THRESHOLD:
                    # Проверяем, существует ли уже такой термин или предложение в БД
                    existing_term = self.db_manager.get_term_by_english(eng)
                    existing_proposal = self.db_manager.get_dictionary_proposal_by_english(eng)
                    
                    if not existing_term and not existing_proposal:
                        self.db_manager.add_dictionary_proposal(
                            english_term=eng,
                            russian_translation=term_data.get("russian", ""),
                            category=term_data.get("category", "other"),
                            confidence=term_data.get("confidence", 0)
                        )
                        newly_proposed_count += 1
            if newly_proposed_count > 0:
                logger.info(f"Добавлено {newly_proposed_count} новых терминов на утверждение.")
        except (json.JSONDecodeError, TypeError) as e:
            logger.error(f"Ошибка при обработке JSON валидации словаря: {e}")

    def process_file(self, input_path, output_path, progress_callback=None):
        chapter_title = os.path.basename(input_path)
        if os.path.exists(output_path) and not self.OVERWRITE_EXISTING:
            logger.info(f"Глава '{chapter_title}' уже существует. Пропущено.")
            try:
                with open(input_path, 'r', encoding='utf-8') as f_in, open(output_path, 'r', encoding='utf-8') as f_out:
                    return True, f_in.read(), f_out.read()
            except Exception as e:
                logger.warning(f"Не удалось прочитать существующую пару файлов для '{chapter_title}': {e}")
                return True, None, None
        try:
            with open(input_path, 'r', encoding='utf-8') as f:
                english_text = f.read()
            if not english_text.strip():
                logger.warning(f"Файл '{input_path}' пуст.")
                open(output_path, 'w', encoding='utf-8').close()
                return True, "", ""
            translated_text = self.translate_chapter(english_text, chapter_title, progress_callback)
            if not translated_text or translated_text.startswith("ОШИБКА:"):
                logger.error(f"Перевод главы '{chapter_title}' не удался.")
                return False, english_text, None
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(translated_text)
            return True, english_text, translated_text
        except Exception as e:
            logger.error(f"Критическая ошибка при обработке файла '{input_path}': {e}", exc_info=True)
            return False, None, None

    def _get_files_to_process(self, input_dir, output_dir):
        files_to_process, files_to_load_for_editor = [], []
        try:
            filenames = sorted([f for f in os.listdir(input_dir) if f.lower().endswith('.txt')])
        except FileNotFoundError:
            logger.error(f"Директория '{input_dir}' не найдена.")
            raise
        for filename in filenames:
            input_path = os.path.join(input_dir, filename)
            output_path = os.path.join(output_dir, f"ru_{filename}")
            if os.path.exists(output_path) and not self.OVERWRITE_EXISTING:
                files_to_load_for_editor.append({"input_path": input_path, "output_path": output_path})
                continue
            files_to_process.append({"input_path": input_path, "output_path": output_path})
        return files_to_process, files_to_load_for_editor

    def process_novel(self, input_dir, output_dir, progress_callback=None):
        logger.info(f"Начало ПОСЛЕДОВАТЕЛЬНОЙ обработки из '{input_dir}'")
        files_to_translate, files_to_load = self._get_files_to_process(input_dir, output_dir)
        total = len(files_to_translate)
        
        if total == 0 and not files_to_load:
            msg = "Файлы .txt для перевода не найдены."
            logger.info(msg)
            if progress_callback: progress_callback(100, msg)
            return True, []
        
        failed_chapters = 0
        processed_texts = []

        for chapter_data in files_to_load:
            _, eng, rus = self.process_file(**chapter_data)
            if eng is not None and rus is not None:
                processed_texts.append({'eng': eng, 'rus': rus, 'title': os.path.basename(chapter_data['input_path']), 'output_path': chapter_data['output_path']})

        for i, chapter_data in enumerate(files_to_translate):
            def chapter_progress_wrapper(p, s):
                if progress_callback:
                    progress_callback((i + p/100.0) / total * 100, s)
            
            success, eng, rus = self.process_file(**chapter_data, progress_callback=chapter_progress_wrapper)
            if not success:
                failed_chapters += 1
            if eng and rus:
                 processed_texts.append({'eng': eng, 'rus': rus, 'title': os.path.basename(chapter_data['input_path']), 'output_path': chapter_data['output_path']})

        if not files_to_translate and progress_callback:
            progress_callback(100, f"Все {len(files_to_load)} глав уже были переведены. Загружены в редактор.")


        processed_texts.sort(key=lambda x: x['title'])
        return failed_chapters == 0, processed_texts

    def process_novel_parallel(self, input_dir, output_dir, progress_callback=None):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        logger.info(f"Начало ПАРАЛЛЕЛЬНОЙ обработки (Потоков: {self.MAX_WORKERS})")
        files_to_translate, files_to_load = self._get_files_to_process(input_dir, output_dir)
        total = len(files_to_translate)

        if total == 0 and not files_to_load:
            msg = "Файлы .txt для перевода не найдены."
            logger.info(msg)
            if progress_callback: progress_callback(100, msg)
            return True, []
            
        completed_count = 0
        failed_chapters = 0
        processed_texts = []

        for chapter_data in files_to_load:
            _, eng, rus = self.process_file(**chapter_data)
            if eng is not None and rus is not None:
                processed_texts.append({'eng': eng, 'rus': rus, 'title': os.path.basename(chapter_data['input_path']), 'output_path': chapter_data['output_path']})
        
        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            future_to_data = {executor.submit(self.process_file, **data): data for data in files_to_translate}
            for future in as_completed(future_to_data):
                data = future_to_data[future]
                success, eng, rus = future.result()
                if not success:
                    failed_chapters += 1
                if eng and rus:
                    processed_texts.append({'eng': eng, 'rus': rus, 'title': os.path.basename(data['input_path']), 'output_path': data['output_path']})
                with self.api_lock:
                    completed_count += 1
                    if progress_callback and total > 0:
                        progress_callback(completed_count / total * 100, f"Обработано {completed_count}/{total}")
        
        if not files_to_translate and progress_callback:
            progress_callback(100, f"Все {len(files_to_load)} глав уже были переведены. Загружены в редактор.")


        processed_texts.sort(key=lambda x: x['title'])
        return failed_chapters == 0, processed_texts

    def analyze_inconsistencies(self, processed_texts):
        logger.info("Запуск аудита консистентности перевода...")
        if not processed_texts:
            return {}

        full_text_for_analysis = ""
        for item in processed_texts:
            full_text_for_analysis += f"--- CHAPTER: {item['title']} ---\n"
            full_text_for_analysis += f"--- RUSSIAN TRANSLATION ---\n{item['rus']}\n\n"

        prompt = f"""# ROLE: Linguistic Auditor
# TASK: You are given a novel split into chapters. Your task is to find inconsistencies in the translation of proper nouns and key terms.

# INSTRUCTIONS:
1. Read through all the provided chapters.
2. Identify proper nouns (character names, locations, specific items, titles, etc.) that appear in multiple chapters.
3. For each such noun, check if its Russian translation is consistent across all its occurrences.
4. Report ONLY the terms that have been translated into two or more DIFFERENT variants.
5. Ignore minor grammatical variations (e.g., different cases of the same word) unless they change the root of the word. For example, "Солнечный Камень" and "Солнечного Камня" is consistent, but "Солнечный Камень" and "Камень Солнца" is an inconsistency.
6. To find the original English term, you must infer it from the context or from the dictionary provided.
7. Return the result as a STRICTLY-formatted JSON object. The object should have a single key "inconsistencies", which is a list.
8. Each item in the list should be an object with two keys: "english_term" (your best guess for the original English name) and "russian_variants" (a list of unique Russian translations you found).
9. If no inconsistencies are found, return an empty list.

# DICTIONARY (for reference):
{json.dumps(self.translation_dictionary, ensure_ascii=False, indent=2)}

# TEXT FOR ANALYSIS:
{full_text_for_analysis[:300000]}

# JSON OUTPUT:"""

        response = self.safe_api_call(prompt)
        if response.startswith("ОШИБКА:"):
            logger.error(f"Ошибка API при аудите консистентности: {response}")
            return {"error": response}

        json_match = re.search(r'```json\s*({.+?})\s*```', response, re.DOTALL | re.IGNORECASE) or re.search(r'({.*?})', response, re.DOTALL)
        if not json_match:
            logger.warning("Не удалось извлечь JSON из ответа аудита консистентности.")
            return {"error": "Не удалось извлечь JSON"}

        try:
            data = json.loads(json_match.group(1))
            logger.info(f"Аудит консистентности завершен. Найдено {len(data.get('inconsistencies', []))} проблем.")
            return data
        except (json.JSONDecodeError, TypeError) as e:
            logger.error(f"Ошибка при обработке JSON ответа аудита: {e}")
            return {"error": str(e)}

    def run_master_edit(self, processed_texts):
        logger.info("Запуск Мастер-Редактора...")
        if not processed_texts:
            return None, "Нет текста для редактирования."

        full_text = ""
        separator_template = "\n\n---=== CHAPTER: {title} ===---\n\n"
        for item in processed_texts:
            full_text += separator_template.format(title=item['title'])
            full_text += item['rus']
        
        prompt = f"""# ROLE: Master Literary Editor
# TASK: You are given the full text of a Russian novel translation. Your goal is to perform a final, holistic proofread to ensure stylistic consistency and improve the overall flow.

# INSTRUCTIONS:
1. Read the entire text to get a feel for the overall style, tone, and rhythm.
2. Your primary goal is to **unify the style**. Make sure the narrative voice is consistent from the first chapter to the last. Smooth out any awkward phrasing or jarring transitions.
3. **DO NOT** change the core meaning of sentences. This is a stylistic edit, not a re-translation.
4. **DO NOT** change the established translation of proper nouns and key terms provided in the glossary.
5. **CRITICALLY IMPORTANT**: Preserve the chapter separators exactly as they are (e.g., `---=== CHAPTER: chapter_name.txt ===---`). They are used to split the text back into files.
6. Return the complete, edited Russian text, including the preserved separators.

# GLOSSARY (Do Not Change These Translations):
{json.dumps(self.translation_dictionary, ensure_ascii=False, indent=2)}

# WORLD BIBLE (For Context on Characters/Locations):
{json.dumps(self.world_bible, ensure_ascii=False, indent=2)}

# FULL TEXT TO EDIT:
{full_text}

# EDITED FULL TEXT:
"""
        edited_text = self.safe_api_call(prompt)
        if edited_text.startswith("ОШИБКА:"):
            logger.error(f"Ошибка API в Мастер-Редакторе: {edited_text}")
            return None, edited_text
        
        logger.info("Мастер-редактура завершена успешно.")
        return edited_text, None

    

    

    

    

    def get_statistics(self):
        with self.stats_lock:
            current_stats = self.stats.copy()
        
        current_stats["terms_in_dictionary"] = len(self.translation_dictionary)
        current_stats["world_bible_entries"] = len(self.world_bible)
        current_stats["dictionary_proposals"] = self.db_manager.count_dictionary_proposals()
        current_stats["bible_proposals"] = self.db_manager.count_world_bible_proposals()
        cache_info = self._execute_api_call_cached.cache_info()
        current_stats["api_cache_hits"] = cache_info.hits
        current_stats["api_cache_misses"] = cache_info.misses
        current_stats["api_cache_size"] = cache_info.currsize
        current_stats["semantic_index_size"] = self.kb_manager.collection.count()
        return current_stats