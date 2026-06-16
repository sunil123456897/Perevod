import os
import logging
from contextlib import contextmanager
from Perevod.config import PROJECT_ROOT
from Perevod.database.models import (
    Project,
    Term,
    WorldBibleEntry,
    DictionaryProposal,
    WorldBibleProposal,
    TranslationCache,
    get_engine_and_session,
    QuarantinedTerm,
    ChapterRun,
)
from Perevod.utils.validation import validate_project_name

logger = logging.getLogger("NovelTranslator.DBManager")


class DatabaseManager:
    def __init__(self, project_name, db_path=None):
        self.project_name = validate_project_name(project_name)
        self.db_path = db_path or os.path.join(
            PROJECT_ROOT, "_project_files", "projects_main.db"
        )
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self.engine, self.Session = get_engine_and_session(self.db_path)
        logger.info(f"DatabaseManager инициализируется. Абсолютный путь к БД: {os.path.abspath(self.db_path)}")
        self.project_id = self._get_or_create_project_id()
        logger.info(
            f"DatabaseManager успешно инициализирован для проекта '{project_name}' (ID: {self.project_id})"
        )

    @contextmanager
    def session_scope(self):
        session = self.Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            logger.error("Ошибка в транзакции, выполнен откат.", exc_info=True)
            raise
        finally:
            session.close()

    def _get_or_create_project_id(self):
        with self.session_scope() as s:
            search_name = self.project_name

            p = s.query(Project).filter_by(name=search_name).first()

            if p:
                logger.debug(f"DBManager: Проект '{search_name}' найден в БД. ID: {p.id}")
                return p.id
            else:
                logger.warning(f"DBManager: Проект '{search_name}' не найден. Создаем новую запись.")
                np = Project(name=search_name, settings_json={})
                s.add(np)
                s.flush()
                logger.info(f"DBManager: Новый проект '{search_name}' создан с ID: {np.id}")
                return np.id

    def get_project_settings(self):
        with self.session_scope() as s:
            return (
                s.query(Project).filter_by(id=self.project_id).one().settings_json or {}
            )

    def update_project_settings(self, d):
        with self.session_scope() as s:
            s.query(Project).filter_by(id=self.project_id).one().settings_json = d

    # --- Словарь ---
    def get_terms_dictionary(self):
        with self.session_scope() as s:
            return {
                t.english_term: {"russian_term": t.russian_term, "category": t.category}
                for t in s.query(Term).filter_by(project_id=self.project_id).all()
            }

    def add_or_update_term(
        self,
        eng,
        rus,
        cat="other",
        *,
        allow_overwrite=True,
        source_chapter=None,
        confidence=0.8,
        reason=None,
    ):
        with self.session_scope() as s:
            t = (
                s.query(Term)
                .filter_by(project_id=self.project_id, english_term=eng)
                .first()
            )
            if t:
                if (
                    not allow_overwrite
                    and t.russian_term.strip() != str(rus).strip()
                ):
                    proposal = (
                        s.query(DictionaryProposal)
                        .filter_by(project_id=self.project_id, english_term=eng)
                        .first()
                    )
                    if proposal:
                        proposal.russian_term = rus
                        proposal.category = cat
                        proposal.confidence = confidence
                        proposal.status = "candidate"
                        proposal.source_chapter = source_chapter
                        proposal.reason = reason
                    else:
                        s.add(
                            DictionaryProposal(
                                project_id=self.project_id,
                                english_term=eng,
                                russian_term=rus,
                                category=cat,
                                confidence=confidence,
                                status="candidate",
                                source_chapter=source_chapter,
                                reason=reason,
                            )
                        )
                    return {
                        "status": "conflict",
                        "english_term": eng,
                        "existing_russian_term": t.russian_term,
                        "candidate_russian_term": rus,
                        "source_chapter": source_chapter,
                        "reason": reason,
                    }
                t.russian_term = rus
                t.category = cat
                return {"status": "updated", "english_term": eng}
            else:
                s.add(
                    Term(
                        project_id=self.project_id,
                        english_term=eng,
                        russian_term=rus,
                        category=cat,
                    )
                )
                return {"status": "created", "english_term": eng}

    def delete_term(self, term):
        with self.session_scope() as s:
            s.query(Term).filter_by(
                project_id=self.project_id, english_term=term
            ).delete()

    def get_all_terms(self):
        with self.session_scope() as s:
            return [
                {
                    "english_term": t.english_term,
                    "russian_term": t.russian_term,
                    "category": t.category,
                }
                for t in s.query(Term).filter_by(project_id=self.project_id).all()
            ]

    def _get_paginated_query(self, model, search_column, order_column, query, page, per_page, result_formatter, limit=None):
        logger.debug(f"--- DB_PAGINATED_QUERY: project_id = {self.project_id}, table = {model.__tablename__} ---")
        with self.session_scope() as s:
            base_query = s.query(model).filter_by(project_id=self.project_id)
            if query:
                search_query = f"%{query.lower()}%"
                base_query = base_query.filter(search_column.ilike(search_query))

            total = base_query.count()

            query_builder = base_query.order_by(order_column)
            if limit is not None:
                results = query_builder.limit(limit).all()
            else:
                results = query_builder.offset(page * per_page).limit(per_page).all()

            return [result_formatter(item) for item in results], total

    def get_paginated_terms(self, query: str, page: int, per_page: int, limit: int = None):
        return self._get_paginated_query(
            Term, Term.english_term, Term.english_term, query, page, per_page,
            lambda t: {"english_term": t.english_term, "russian_term": t.russian_term, "category": t.category},
            limit=limit
        )

    def merge_dictionary_terms(self, primary_term_eng: str, alias_terms_eng: list) -> bool:
        with self.session_scope() as s:
            primary = s.query(Term).filter_by(project_id=self.project_id, english_term=primary_term_eng).first()
            if not primary:
                return False
            for alias_eng in alias_terms_eng:
                alias = s.query(Term).filter_by(project_id=self.project_id, english_term=alias_eng).first()
                if alias and alias.id != primary.id:
                    if not primary.russian_term and alias.russian_term:
                        primary.russian_term = alias.russian_term
                    s.delete(alias)
            return True

    # --- Карантин ---
    def quarantine_term(self, english_term: str, reason: str):
        with self.session_scope() as s:
            term_to_quarantine = (
                s.query(Term)
                .filter_by(project_id=self.project_id, english_term=english_term)
                .first()
            )
            if term_to_quarantine:
                quarantined_term = (
                    s.query(QuarantinedTerm)
                    .filter_by(
                        project_id=self.project_id,
                        english_term=term_to_quarantine.english_term,
                    )
                    .first()
                )
                if quarantined_term:
                    quarantined_term.russian_term = term_to_quarantine.russian_term
                    quarantined_term.category = term_to_quarantine.category
                    quarantined_term.reason = reason
                else:
                    s.add(
                        QuarantinedTerm(
                            project_id=self.project_id,
                            english_term=term_to_quarantine.english_term,
                            russian_term=term_to_quarantine.russian_term,
                            category=term_to_quarantine.category,
                            reason=reason,
                        )
                    )
                s.delete(term_to_quarantine)
                logger.info(
                    f"Термин '{english_term}' помещен в карантин по причине: {reason}"
                )

    def restore_term(self, quarantined_term_id: int) -> dict | None:
        with self.session_scope() as s:
            term_to_restore = (
                s.query(QuarantinedTerm)
                .filter_by(id=quarantined_term_id, project_id=self.project_id)
                .first()
            )
            if term_to_restore:
                details = {
                    "english_term": term_to_restore.english_term,
                    "russian_term": term_to_restore.russian_term,
                    "category": term_to_restore.category,
                }
                existing_term = (
                    s.query(Term)
                    .filter_by(
                        project_id=self.project_id,
                        english_term=term_to_restore.english_term,
                    )
                    .first()
                )
                if existing_term:
                    existing_term.russian_term = term_to_restore.russian_term
                    existing_term.category = term_to_restore.category
                else:
                    s.add(
                        Term(
                            project_id=self.project_id,
                            english_term=term_to_restore.english_term,
                            russian_term=term_to_restore.russian_term,
                            category=term_to_restore.category,
                        )
                    )
                s.delete(term_to_restore)
                logger.info(
                    f"Термин '{term_to_restore.english_term}' восстановлен из карантина."
                )
                return details
            return None

    def delete_from_quarantine(self, quarantined_term_id: int) -> dict | None:
        with self.session_scope() as s:
            term_to_delete = (
                s.query(QuarantinedTerm)
                .filter_by(id=quarantined_term_id, project_id=self.project_id)
                .first()
            )
            if term_to_delete:
                details = {
                    "english_term": term_to_delete.english_term,
                    "russian_term": term_to_delete.russian_term,
                    "category": term_to_delete.category,
                }
                logger.warning(
                    f"Термин '{term_to_delete.english_term}' окончательно удаляется из карантина."
                )
                s.delete(term_to_delete)
                return details
            return None

    def get_paginated_quarantined_terms(self, query: str, page: int, per_page: int, limit: int = None):
        return self._get_paginated_query(
            QuarantinedTerm, QuarantinedTerm.english_term, QuarantinedTerm.english_term, query, page, per_page,
            lambda t: {"id": t.id, "english_term": t.english_term, "russian_term": t.russian_term, "category": t.category, "reason": t.reason},
            limit=limit
        )

    def get_world_bible(self):
        with self.session_scope() as s:
            return { e.english_name: {"russian_name": e.russian_name, "category": e.category, "description": e.description, "russian_description": e.russian_description} for e in s.query(WorldBibleEntry).filter_by(project_id=self.project_id).all() }

    def get_paginated_bible_entries(self, query: str, page: int, per_page: int, limit: int = None):
        return self._get_paginated_query(
            WorldBibleEntry, WorldBibleEntry.english_name, WorldBibleEntry.english_name, query, page, per_page,
            lambda e: {
                "english_name": e.english_name, "russian_name": e.russian_name,
                "category": e.category, "description": e.description,
                "russian_description": e.russian_description
            },
            limit=limit
        )

    def add_or_update_bible_entry(self, eng_name, data):
        with self.session_scope() as s:
            e = s.query(WorldBibleEntry).filter_by(project_id=self.project_id, english_name=eng_name).first()
            if e:
                e.russian_name, e.category, e.description, e.russian_description = data.get("russian_name"), data.get("category"), data.get("description"), data.get("russian_description")
            else:
                entry_data = dict(data)
                entry_data.pop("english_name", None)
                s.add(WorldBibleEntry(project_id=self.project_id, english_name=eng_name, **entry_data))

    def delete_bible_entry(self, name):
        with self.session_scope() as s:
            s.query(WorldBibleEntry).filter_by(
                project_id=self.project_id, english_name=name
            ).delete()

    def get_paginated_dictionary_proposals(self, query: str, page: int, per_page: int, limit: int = None):
        return self._get_paginated_query(
            DictionaryProposal, DictionaryProposal.english_term, DictionaryProposal.english_term, query, page, per_page,
            lambda p: {
                "english_term": p.english_term, "russian_term": p.russian_term,
                "category": p.category, "confidence": p.confidence
            },
            limit=limit
        )

    def add_dictionary_proposal(
        self,
        eng,
        rus,
        cat="other",
        conf=0.8,
        *,
        source_chapter=None,
        reason=None,
        allow_existing_term=False,
    ):
        with self.session_scope() as s:
            if (
                (
                    allow_existing_term
                    or not s.query(Term)
                    .filter_by(project_id=self.project_id, english_term=eng)
                    .first()
                )
                and not s.query(DictionaryProposal)
                .filter_by(project_id=self.project_id, english_term=eng)
                .first()
            ):
                s.add(
                    DictionaryProposal(
                        project_id=self.project_id,
                        english_term=eng,
                        russian_term=rus,
                        category=cat,
                        confidence=conf,
                        status="candidate",
                        source_chapter=source_chapter,
                        reason=reason,
                    )
                )

    def get_dictionary_proposals(self):
        with self.session_scope() as s:
            return {
                p.english_term: {
                    "russian_term": p.russian_term,
                    "category": p.category,
                    "confidence": p.confidence,
                    "status": p.status,
                    "source_chapter": p.source_chapter,
                    "reason": p.reason,
                }
                for p in s.query(DictionaryProposal)
                .filter_by(project_id=self.project_id)
                .all()
            }

    def delete_dictionary_proposal(self, term):
        with self.session_scope() as s:
            s.query(DictionaryProposal).filter_by(project_id=self.project_id, english_term=term).delete()

    def count_dictionary_proposals(self):
        with self.session_scope() as s:
            return s.query(DictionaryProposal).filter_by(project_id=self.project_id).count()

    def clear_dictionary_proposals(self):
        with self.session_scope() as s:
            s.query(DictionaryProposal).filter_by(project_id=self.project_id).delete()

    def get_paginated_world_bible_proposals(self, query: str, page: int, per_page: int, limit: int = None):
        return self._get_paginated_query(
            WorldBibleProposal, WorldBibleProposal.english_name, WorldBibleProposal.english_name, query, page, per_page,
            lambda p: {
                "english_name": p.english_name, "russian_name": p.russian_name,
                "category": p.category, "description": p.description
            },
            limit=limit
        )

    def add_world_bible_proposal(self, eng_name, rus_name, cat, desc):
        with self.session_scope() as s:
            if (
                not s.query(WorldBibleEntry)
                .filter_by(project_id=self.project_id, english_name=eng_name)
                .first()
                and not s.query(WorldBibleProposal)
                .filter_by(project_id=self.project_id, english_name=eng_name)
                .first()
            ):
                s.add(
                    WorldBibleProposal(
                        project_id=self.project_id,
                        english_name=eng_name,
                        russian_name=rus_name,
                        category=cat,
                        description=desc,
                    )
                )

    def get_world_bible_proposals(self):
        with self.session_scope() as s:
            return { p.english_name: {"russian_name": p.russian_name, "category": p.category, "description": p.description} for p in s.query(WorldBibleProposal).filter_by(project_id=self.project_id).all() }

    def count_world_bible_proposals(self):
        with self.session_scope() as s:
            return (
                s.query(WorldBibleProposal)
                .filter_by(project_id=self.project_id)
                .count()
            )

    def delete_world_bible_proposal(self, name):
        with self.session_scope() as s:
            s.query(WorldBibleProposal).filter_by(
                project_id=self.project_id, english_name=name
            ).delete()

    def clear_world_bible_proposals(self):
        with self.session_scope() as s:
            s.query(WorldBibleProposal).filter_by(project_id=self.project_id).delete()

    def automerge_dictionary_duplicates(self):
        merged_groups_count = 0
        with self.session_scope() as session:
            all_terms = session.query(Term).filter_by(project_id=self.project_id).all()
            terms_by_lower = {}
            for term in all_terms:
                terms_by_lower.setdefault(term.english_term.lower(), []).append(term)
            for terms_list in terms_by_lower.values():
                if len(terms_list) > 1:
                    primary_term = sorted(terms_list, key=lambda t: t.english_term)[0]
                    alias_terms = [t for t in terms_list if t != primary_term]
                    logger.info(
                        f"Объединение дубликатов для '{primary_term.english_term}'. Псевдонимы: {[t.english_term for t in alias_terms]}"
                    )
                    for alias in alias_terms:
                        if not primary_term.russian_term and alias.russian_term:
                            primary_term.russian_term = alias.russian_term
                        session.delete(alias)
                    merged_groups_count += 1
            return merged_groups_count

    # --- Кэш Переводов ---
    def get_from_cache(self, cache_key: str) -> str | None:
        """Получает переведенный текст из кэша по ключу."""
        with self.session_scope() as s:
            entry = (
                s.query(TranslationCache)
                .filter_by(project_id=self.project_id, cache_key=cache_key)
                .first()
            )
            return entry.translated_text if entry else None

    def add_to_cache(self, cache_key: str, translated_text: str):
        """Добавляет или обновляет запись в кэше перевода."""
        with self.session_scope() as s:
            existing_entry = (
                s.query(TranslationCache)
                .filter_by(project_id=self.project_id, cache_key=cache_key)
                .first()
            )
            if existing_entry:
                existing_entry.translated_text = translated_text
            else:
                new_entry = TranslationCache(
                    project_id=self.project_id,
                    cache_key=cache_key,
                    translated_text=translated_text,
                )
                s.add(new_entry)

    def delete_from_cache(self, cache_key: str) -> None:
        """Удаляет запись из кэша перевода по ключу."""
        with self.session_scope() as s:
            s.query(TranslationCache).filter_by(
                project_id=self.project_id,
                cache_key=cache_key,
            ).delete()

    # --- Checkpoint / Resume ---
    def upsert_chapter_run(
        self,
        title: str,
        *,
        input_path: str,
        output_path: str,
        status: str = "discovered",
        error: str | None = None,
    ) -> None:
        """Creates or refreshes project-scoped checkpoint metadata for a chapter."""
        with self.session_scope() as s:
            chapter_run = (
                s.query(ChapterRun)
                .filter_by(project_id=self.project_id, title=title)
                .first()
            )
            if chapter_run:
                chapter_run.input_path = input_path
                chapter_run.output_path = output_path
                chapter_run.status = status
                chapter_run.error = error
                stages = dict(chapter_run.stages_json or {})
            else:
                stages = {}
                chapter_run = ChapterRun(
                    project_id=self.project_id,
                    title=title,
                    input_path=input_path,
                    output_path=output_path,
                    status=status,
                    error=error,
                    stages_json=stages,
                )
                s.add(chapter_run)
            stages.setdefault("discovered", "done")
            chapter_run.stages_json = stages

    def mark_chapter_stage(
        self,
        title: str,
        stage: str,
        state: str,
        *,
        error: str | None = None,
    ) -> None:
        """Persists a chapter-stage checkpoint without relying on report files."""
        with self.session_scope() as s:
            chapter_run = (
                s.query(ChapterRun)
                .filter_by(project_id=self.project_id, title=title)
                .first()
            )
            if not chapter_run:
                chapter_run = ChapterRun(
                    project_id=self.project_id,
                    title=title,
                    input_path="",
                    output_path="",
                    status="discovered",
                    stages_json={"discovered": "done"},
                )
                s.add(chapter_run)

            stages = dict(chapter_run.stages_json or {})
            stages[stage] = state
            chapter_run.stages_json = stages
            chapter_run.error = error
            chapter_run.status = "failed" if state == "failed" else stage

    def update_chapter_context(self, title: str, context_text: str) -> None:
        """Stores the chapter-specific retrieved context for safe resume."""
        with self.session_scope() as s:
            chapter_run = (
                s.query(ChapterRun)
                .filter_by(project_id=self.project_id, title=title)
                .first()
            )
            if not chapter_run:
                chapter_run = ChapterRun(
                    project_id=self.project_id,
                    title=title,
                    input_path="",
                    output_path="",
                    status="discovered",
                    stages_json={"discovered": "done"},
                )
                s.add(chapter_run)
            chapter_run.context = context_text

    def update_chapter_judge_result(self, title: str, judge_result: dict) -> None:
        """Stores structured judge output for chapter-aware resume/reporting."""
        with self.session_scope() as s:
            chapter_run = (
                s.query(ChapterRun)
                .filter_by(project_id=self.project_id, title=title)
                .first()
            )
            if not chapter_run:
                chapter_run = ChapterRun(
                    project_id=self.project_id,
                    title=title,
                    input_path="",
                    output_path="",
                    status="discovered",
                    stages_json={"discovered": "done"},
                )
                s.add(chapter_run)
            chapter_run.judge_result_json = dict(judge_result or {})

    def update_chapter_refine_result(self, title: str, refine_result: dict) -> None:
        """Stores structured refine output for chapter-aware resume/reporting."""
        with self.session_scope() as s:
            chapter_run = (
                s.query(ChapterRun)
                .filter_by(project_id=self.project_id, title=title)
                .first()
            )
            if not chapter_run:
                chapter_run = ChapterRun(
                    project_id=self.project_id,
                    title=title,
                    input_path="",
                    output_path="",
                    status="discovered",
                    stages_json={"discovered": "done"},
                )
                s.add(chapter_run)
            chapter_run.refine_result_json = dict(refine_result or {})

    def update_chapter_summary_result(self, title: str, summary_result: dict) -> None:
        """Stores structured summary output for chapter-aware resume/reporting."""
        with self.session_scope() as s:
            chapter_run = (
                s.query(ChapterRun)
                .filter_by(project_id=self.project_id, title=title)
                .first()
            )
            if not chapter_run:
                chapter_run = ChapterRun(
                    project_id=self.project_id,
                    title=title,
                    input_path="",
                    output_path="",
                    status="discovered",
                    stages_json={"discovered": "done"},
                )
                s.add(chapter_run)
            chapter_run.summary_result_json = dict(summary_result or {})

    def get_chapter_runs(self) -> dict[str, dict]:
        """Returns checkpoint state keyed by chapter title."""
        with self.session_scope() as s:
            runs = (
                s.query(ChapterRun)
                .filter_by(project_id=self.project_id)
                .order_by(ChapterRun.title)
                .all()
            )
            return {
                run.title: {
                    "title": run.title,
                    "input_path": run.input_path,
                    "output_path": run.output_path,
                    "status": run.status,
                    "stages": dict(run.stages_json or {}),
                    "context": run.context,
                    "judge_result": dict(run.judge_result_json or {}),
                    "refine_result": dict(run.refine_result_json or {}),
                    "summary_result": dict(run.summary_result_json or {}),
                    "error": run.error,
                }
                for run in runs
            }
