# --- НАЧАЛО ФАЙЛА: scripts/audit_and_clean_database.py ---

import json
import logging
from collections import defaultdict
import pymorphy3
import argparse
import sys # --- ДОБАВЛЕНО: для sys.exit()
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from Perevod.database.database_manager import DatabaseManager
from Perevod.project_manager import ProjectManager
from Perevod.config import settings
from Perevod.llm_provider import LLMProvider
from Perevod.utils.logging_setup import setup_logging
# --- ДОБАВЛЕНО: Импорт безопасного парсера
from Perevod.utils.llm import safe_json_loads

# --- Настройка ---
setup_logging()
logger = logging.getLogger("DBAuditor")
morph = pymorphy3.MorphAnalyzer()


# ==============================================================================
# Классы для хранения проблем
# ==============================================================================
class AuditIssue:
    def __init__(self, issue_type, description, data):
        self.issue_type = issue_type
        self.description = description
        self.data = data
        self.resolution = None

    def __str__(self):
        return f"[{self.issue_type}] {self.description}"


class SynonymMergeIssue(AuditIssue):
    def __init__(self, primary_term, aliases):
        super().__init__(
            "Synonym Merge",
            f"Primary: '{primary_term}', Aliases: {aliases}",
            {"primary": primary_term, "aliases": aliases},
        )


class InconsistencyIssue(AuditIssue):
    def __init__(self, entity_name, bible_translation, dict_translation):
        super().__init__(
            "Bible/Dict Inconsistency",
            f"Entity: '{entity_name}', Bible: '{bible_translation}', Dict: '{dict_translation}'",
            {"entity": entity_name, "options": [bible_translation, dict_translation]},
        )


# ==============================================================================
# Основной класс аудитора
# ==============================================================================
class DatabaseAuditor:
    def __init__(self, project_name, dry_run=False):
        self.project_name = project_name
        self.dry_run = dry_run
        logger.info(
            f"--- Запуск аудита для проекта: '{project_name}' (Режим 'Dry Run': {self.dry_run}) ---"
        )

        self.pm = ProjectManager()
        self.settings = self.pm.get_project_settings(project_name)

        api_key = self.settings.get("GOOGLE_API_KEY") or self.settings.get("api_key")
        if not api_key and not self.dry_run:
            raise ValueError("API ключ не найден в настройках проекта.")

        self.model = None
        if api_key:
            model_name = self.settings.get(
                "curation_model_name", settings.curation_model_name
            )
            self.model = LLMProvider(
                {"curation": model_name},
                api_key=api_key,
            ).get_model("curation")

        self.generation_config = {"temperature": 0.5, "top_p": 0.9}
        self.safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]

        self.db = DatabaseManager(project_name)
        self.issues = defaultdict(list)

    def run_audit(self, prune_only=False):
        if not prune_only:
            self.step_1_automerge_case_duplicates()
            self.step_2_find_lexical_synonyms()
            self.step_3_find_bible_dictionary_inconsistencies()

            if any(self.issues.values()):
                self.step_4_resolve_all_issues_with_llm()
                if not self.dry_run:
                    self.step_5_apply_resolutions()
                else:
                    logger.info(
                        "РЕЖИМ 'DRY RUN': Изменения не были применены к базе данных."
                    )
            else:
                logger.info("В ходе аудита консистентности не найдено проблем.")

        self.step_6_prune_dictionary()

        logger.info(f"--- Аудит проекта '{self.project_name}' завершен. ---")

    def step_1_automerge_case_duplicates(self):
        logger.info("[Шаг 1/6] Автоматическое слияние дубликатов по регистру...")
        if not self.dry_run:
            merged_count = self.db.automerge_dictionary_duplicates()
            logger.info(
                f"Успешно объединено {merged_count} групп(ы) дубликатов в словаре."
            )
        else:
            logger.info("РЕЖИМ 'DRY RUN': Пропуск слияния дубликатов.")

    def step_2_find_lexical_synonyms(self):
        logger.info("[Шаг 2/6] Поиск лексических синонимов в словаре...")
        all_terms = self.db.get_all_terms()
        groups_by_normal_form = defaultdict(list)
        for term in all_terms:
            rus_term = term["russian_term"]
            if not rus_term:
                continue
            words = rus_term.split()
            # --- ИСПРАВЛЕНО: Добавлена защита от ошибок pymorphy3 ---
            normal_forms = []
            for w in words:
                try:
                    parsed_word = morph.parse(w)
                    if parsed_word:
                        normal_forms.append(parsed_word[0].normal_form)
                    else:
                        normal_forms.append(w) # Если не удалось распарсить, используем оригинал
                except Exception:
                    normal_forms.append(w)
            normal_form = " ".join(normal_forms)
            groups_by_normal_form[normal_form].append(term["english_term"])

        for normal_form, eng_terms in groups_by_normal_form.items():
            if len(eng_terms) > 1:
                primary_term = sorted(eng_terms, key=len)[0]
                aliases = [t for t in eng_terms if t != primary_term]
                issue = SynonymMergeIssue(primary_term, aliases)
                self.issues["synonyms"].append(issue)
                logger.info(
                    f"Найдена группа синонимов для '{normal_form}': {eng_terms}"
                )

    def step_3_find_bible_dictionary_inconsistencies(self):
        logger.info("[Шаг 3/6] Поиск несоответствий между Библией и Словарем...")
        bible_entries = self.db.get_world_bible()
        dictionary = self.db.get_terms_dictionary()
        for name, bible_data in bible_entries.items():
            if name in dictionary:
                dict_data = dictionary[name]
                bible_rus = bible_data.get("russian_name", "").strip()
                # --- ИСПРАВЛЕНО: Используется правильный ключ 'russian_term' вместо 'russian' ---
                dict_rus = dict_data.get("russian_term", "").strip()
                if bible_rus and dict_rus and bible_rus.lower() != dict_rus.lower():
                    issue = InconsistencyIssue(name, bible_rus, dict_rus)
                    self.issues["inconsistencies"].append(issue)
                    logger.info(
                        f"Найдено несоответствие для '{name}': Библия='{bible_rus}', Словарь='{dict_rus}'"
                    )

    def step_4_resolve_all_issues_with_llm(self):
        logger.info("[Шаг 4/6] Решение проблем с помощью LLM...")
        if self.issues["synonyms"]:
            self._resolve_issues_by_type(
                "synonyms", self._build_synonym_prompt, self._parse_synonym_resolutions
            )
        if self.issues["inconsistencies"]:
            self._resolve_issues_by_type(
                "inconsistencies",
                self._build_inconsistency_prompt,
                self._parse_inconsistency_resolutions,
            )

    def _resolve_issues_by_type(self, issue_type, prompt_builder, resolution_parser):
        issues_to_process = self.issues[issue_type]
        logger.info(
            f"Обработка {len(issues_to_process)} проблем типа '{issue_type}'..."
        )
        prompt = prompt_builder(issues_to_process)
        logger.debug(f"Сформированный промпт для '{issue_type}':\n{prompt}")

        if self.dry_run:
            logger.info(f"РЕЖИМ 'DRY RUN': Пропуск вызова API для '{issue_type}'.")
            return

        try:
            response = self.model.generate_content(
                prompt,
                generation_config=self.generation_config,
                request_options={"timeout": 180},
            )
            # --- ИСПРАВЛЕНО: Используется безопасный парсер ---
            resolutions = safe_json_loads(response.text)
            if not resolutions:
                logger.error(
                    f"API вернул пустой или невалидный JSON для '{issue_type}'. Причина: {response.prompt_feedback}"
                )
                return

            resolution_parser(resolutions, issues_to_process)
        except Exception as e:
            logger.error(
                f"Ошибка при взаимодействии с LLM для '{issue_type}': {e}",
                exc_info=True,
            )

    def _build_synonym_prompt(self, issues):
        parts = [
            "# ROLE: Database Curator...",
            "# TASK: For each synonym group, choose the single best English term to be the primary key.",
            "# OUTPUT FORMAT: JSON object with keys `issue_0`, `issue_1`, etc.",
        ]
        for i, issue in enumerate(issues):
            parts.append(f"\n# --- ISSUE_{i}: Synonym Consolidation ---")
            parts.append(
                f"# Candidates: {json.dumps([issue.data['primary']] + issue.data['aliases'], ensure_ascii=False)}"
            )
            parts.append('# Required JSON output: {"chosen_primary_term": "..."}')
        parts.append("\n# --- FINAL JSON OUTPUT ---")
        return "\n".join(parts)

    def _build_inconsistency_prompt(self, issues):
        parts = [
            "# ROLE: Linguistic Analyst...",
            "# TASK: For each entity, choose the best Russian name from the options.",
            "# OUTPUT FORMAT: JSON object with keys `issue_0`, `issue_1`, etc.",
        ]
        for i, issue in enumerate(issues):
            parts.append(f"\n# --- ISSUE_{i}: Name Inconsistency ---")
            parts.append(f"# Entity: '{issue.data['entity']}'")
            parts.append(
                f"# Options: {json.dumps(issue.data['options'], ensure_ascii=False)}"
            )
            parts.append('# Required JSON output: {"correct_russian_name": "..."}')
        parts.append("\n# --- FINAL JSON OUTPUT ---")
        return "\n".join(parts)

    def _parse_synonym_resolutions(self, resolutions, issues):
        for i, issue in enumerate(issues):
            if f"issue_{i}" in resolutions:
                issue.resolution = resolutions[f"issue_{i}"]

    def _parse_inconsistency_resolutions(self, resolutions, issues):
        for i, issue in enumerate(issues):
            if f"issue_{i}" in resolutions:
                issue.resolution = resolutions[f"issue_{i}"]

    def step_5_apply_resolutions(self):
        logger.info("[Шаг 5/6] Применение разрешенных проблем к базе данных...")
        applied_count = 0
        all_issues = self.issues["synonyms"] + self.issues["inconsistencies"]
        for issue in all_issues:
            if not issue.resolution:
                continue
            try:
                if isinstance(issue, SynonymMergeIssue):
                    primary = issue.resolution.get("chosen_primary_term")
                    if primary:
                        aliases = [
                            t
                            for t in (issue.data["aliases"] + [issue.data["primary"]])
                            if t != primary
                        ]
                        self.db.merge_dictionary_terms(primary, aliases)
                        logger.info(
                            f"СЛИЯНИЕ: Термины {aliases} объединены в '{primary}'."
                        )
                        applied_count += 1
                elif isinstance(issue, InconsistencyIssue):
                    correct_name = issue.resolution.get("correct_russian_name")
                    if correct_name:
                        self.db.add_or_update_term(issue.data["entity"], correct_name)
                        bible_entry = self.db.get_world_bible().get(
                            issue.data["entity"], {}
                        )
                        bible_entry["russian_name"] = correct_name
                        self.db.add_or_update_bible_entry(
                            issue.data["entity"], bible_entry
                        )
                        logger.info(
                            f"ИСПРАВЛЕНИЕ: Для '{issue.data['entity']}' установлено имя '{correct_name}'."
                        )
                        applied_count += 1
            except Exception as e:
                logger.error(
                    f"Ошибка при применении резолюции для '{issue.description}': {e}"
                )
        logger.info(f"Применено {applied_count} из {len(all_issues)} резолюций.")

    def step_6_prune_dictionary(self):
        logger.info(
            "[Шаг 6/6] Оценка релевантности терминов в словаре для прореживания..."
        )
        if self.dry_run:
            logger.info("РЕЖИМ 'DRY RUN': Пропуск шага прореживания.")
            return

        all_terms = self.db.get_all_terms()
        if len(all_terms) < 100:
            logger.info("Словарь слишком мал для прореживания. Пропускаем.")
            return

        batch_size = 100
        terms_to_delete = []
        for i in range(0, len(all_terms), batch_size):
            batch = all_terms[i : i + batch_size]
            terms_to_evaluate = [term["english_term"] for term in batch]
            prompt_parts = [
                "# ROLE: Elite Terminologist & Pruning Expert",
                "# TASK: I have a large dictionary for a fantasy novel translation. I need to prune it, keeping only the most important and canonical terms. For each term in the list below, decide if it should be KEPT or DELETED.",
                "# CRITERIA FOR KEEPING A TERM:\n- Proper Nouns (characters, places, sects, etc.).\n- Unique fantasy/cultivation concepts (realms, techniques, items).\n- Crucial thematic words.",
                "# CRITERIA FOR DELETING A TERM:\n- Common words or generic phrases (e.g., 'a long time', 'ancient trees', 'sword').\n- Plural or possessive forms that should be handled by grammar, not dictionary (e.g., 'swords', 'King's').\n- Obvious translation errors or junk data.",
                "\n# ENGLISH TERMS TO EVALUATE:",
                json.dumps(terms_to_evaluate, ensure_ascii=False, indent=2),
                "\n# OUTPUT FORMAT: Return a JSON object with a single key 'terms_to_delete', containing a list of English terms that should be deleted.",
                '# Example: {"terms_to_delete": ["ancient trees", "King\'s"]}',
            ]
            prompt = "\n".join(prompt_parts)

            max_retries = 3
            for attempt in range(max_retries):
                try:
                    response = self.model.generate_content(
                        prompt,
                        generation_config=self.generation_config,
                        request_options={"timeout": 180},
                    )
                    # --- ИСПРАВЛЕНО: Используется безопасный парсер ---
                    response_json = safe_json_loads(response.text)
                    if "terms_to_delete" in response_json:
                        terms_to_delete.extend(response_json["terms_to_delete"])
                        logger.info(
                            f"Пакет {i // batch_size + 1}: LLM предложил удалить {len(response_json['terms_to_delete'])} терминов."
                        )
                    else:
                        logger.warning(f"Пакет {i // batch_size + 1}: LLM не предложил терминов для удаления или вернул неверный формат.")
                    break
                except Exception as e:
                    logger.error(
                        f"Ошибка при обработке пакета для прореживания (попытка {attempt + 1}/{max_retries}): {e}"
                    )
                    if attempt + 1 == max_retries:
                        logger.error(
                            f"Не удалось обработать пакет после {max_retries} попыток. Пропускаем."
                        )
                    else:
                        import time
                        time.sleep(2**attempt)

        if terms_to_delete:
            logger.info(
                f"\nВсего LLM предложил поместить в карантин {len(terms_to_delete)} терминов."
            )
            quarantined_count = 0
            for term in terms_to_delete:
                self.db.quarantine_term(term, reason="Proposed for pruning by LLM")
                quarantined_count += 1
            logger.info(f"Успешно помещено в карантин {quarantined_count} терминов.")


def main():
    parser = argparse.ArgumentParser(
        description="Инструмент для аудита и очистки базы данных проекта."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Запустить скрипт в режиме 'сухого запуска' без вызова API и изменения БД.",
    )
    parser.add_argument(
        "--prune-only",
        action="store_true",
        help="Запустить только шаг прореживания словаря.",
    )
    parser.add_argument(
        "--project",
        help="Название проекта для аудита. Если не указано, будет предложен интерактивный выбор.",
    )
    args = parser.parse_args()

    project_manager = ProjectManager()
    project_names = project_manager.get_project_names()
    if not project_names:
        print("Не найдено ни одного проекта.")
        # --- ИСПРАВЛЕНО: Добавлен выход из программы при отсутствии проектов ---
        sys.exit(0)

    if args.project:
        if args.project not in project_names:
            print(f"Проект '{args.project}' не найден.")
            sys.exit(1)
        project_to_audit = args.project
    else:
        print("Доступные проекты для аудита:")
        for i, name in enumerate(project_names):
            print(f"  {i + 1}. {name}")

        try:
            choice_str = input("Введите номер проекта для аудита: ")
            if not choice_str.isdigit():
                raise ValueError
            choice = int(choice_str) - 1
            if not (0 <= choice < len(project_names)):
                raise ValueError
            project_to_audit = project_names[choice]
        except (ValueError, IndexError):
            print("Неверный выбор. Выход.")
            # --- ИСПРАВЛЕНО: Добавлен выход из программы при неверном выборе ---
            sys.exit(1)

        confirm = input(
            f"Вы уверены, что хотите запустить аудит для проекта '{project_to_audit}'? (y/n): "
        )
        if confirm.lower() != "y":
            print("Аудит отменен.")
            return

    try:
        auditor = DatabaseAuditor(project_to_audit, dry_run=args.dry_run)
        auditor.run_audit(prune_only=args.prune_only)
    except Exception as e:
        logger.error(f"Критическая ошибка во время аудита: {e}", exc_info=True)


if __name__ == "__main__":
    main()

# --- КОНЕЦ ФАЙЛА: scripts/audit_and_clean_database.py ---
