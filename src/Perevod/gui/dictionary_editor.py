import customtkinter as ctk
from tkinter import messagebox
import logging
import threading
from .paginated_editor import TabbedPaginatedEditor

gui_logger = logging.getLogger("NovelTranslator.GUI")


class DictionaryEditorWindow(TabbedPaginatedEditor):
    def __init__(self, master, db_manager):
        super().__init__(master, "Редактор Словаря", db_manager)

        self.term_widgets = {}
        self.search_var.trace_add("write", lambda *args: self._load_data())

        # Add search and save controls to the main tab
        self._setup_main_tab_controls()

        self._load_data()

    def _setup_main_tab_controls(self):
        controls_frame = ctk.CTkFrame(self.main_tab, fg_color="transparent")
        controls_frame.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        controls_frame.grid_columnconfigure(0, weight=1)

        search_entry = ctk.CTkEntry(
            controls_frame, textvariable=self.search_var, placeholder_text="Поиск..."
        )
        search_entry.grid(row=0, column=0, sticky="ew", padx=(0, 5))

        add_button = ctk.CTkButton(
            controls_frame, text="Добавить", command=self._add_new_term_entry
        )
        add_button.grid(row=0, column=1, padx=5)

        save_button = ctk.CTkButton(
            controls_frame,
            text="Сохранить",
            command=self._save_all_terms,
            fg_color="#2E7D32",
            hover_color="#1B5E20",
        )
        save_button.grid(row=0, column=2, padx=5)

        # Re-grid the scrollable frame to be below the controls
        self.main_scrollable_frame.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
        self.main_tab.grid_rowconfigure(1, weight=1)

    def _load_data(self):
        """Loads both terms and proposals using pagination."""
        gui_logger.info("--- DictionaryEditor: Загрузка данных... ---")
        query = self.search_var.get()
        
        try:
            terms_data, total_terms = self.db_manager.get_paginated_terms(query, self.current_page, self.items_per_page)
            self.total_items = total_terms
            
            proposals_data, total_proposals = self.db_manager.get_paginated_dictionary_proposals(query, self.current_page, self.items_per_page)
            
            gui_logger.info(f"Загружено {len(terms_data)} терминов (всего {total_terms}).")
            gui_logger.info(f"Загружено {len(proposals_data)} предложений (всего {total_proposals}).")

            self._display_page(terms_data, proposals_data)
        except Exception as e:
            gui_logger.error(f"Критическая ошибка при загрузке данных словаря: {e}", exc_info=True)
            messagebox.showerror("Ошибка загрузки", f"Не удалось загрузить данные словаря: {e}")

    def _display_page(self, terms, proposals):
        # Clear previous content
        for widget in self.main_scrollable_frame.winfo_children():
            widget.destroy()
        for widget in self.proposals_scrollable_frame.winfo_children():
            widget.destroy()

        # Render headers
        ctk.CTkLabel(
            self.main_scrollable_frame,
            text="Английский термин",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, padx=5, pady=5, sticky="w")
        ctk.CTkLabel(
            self.main_scrollable_frame,
            text="Русский перевод",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=1, padx=5, pady=5, sticky="w")
        ctk.CTkLabel(
            self.proposals_scrollable_frame,
            text="Предложенный термин (Англ -> Рус)",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, padx=5, pady=5, sticky="w")

        # Render items
        self.term_widgets = {}
        for i, item in enumerate(terms):
            self._render_main_item(item, self.main_scrollable_frame, i + 1)

        for i, item in enumerate(proposals):
            self._render_proposal_item(item, self.proposals_scrollable_frame, i + 1)

        self._update_pagination_label()

    def _render_main_item(self, item, frame, index):
        """Renders a single dictionary term entry."""
        # Configure the frame's columns to expand
        frame.grid_columnconfigure((0, 1), weight=1)

        original_eng = item["english_term"]

        eng_var = ctk.StringVar(value=item["english_term"])
        rus_var = ctk.StringVar(value=item["russian_term"])

        self.term_widgets[original_eng] = {
            "eng_var": eng_var,
            "rus_var": rus_var,
            "is_new": item.get("is_new", False),
            "is_modified": False,
            "category": item["category"],
        }

        eng_entry = ctk.CTkEntry(frame, textvariable=eng_var)
        eng_entry.grid(row=index, column=0, padx=5, pady=2, sticky="ew")

        rus_entry = ctk.CTkEntry(frame, textvariable=rus_var)
        rus_entry.grid(row=index, column=1, padx=5, pady=2, sticky="ew")

        eng_var.trace_add(
            "write", lambda *args, oe=original_eng: self._mark_as_modified(oe)
        )
        rus_var.trace_add(
            "write", lambda *args, oe=original_eng: self._mark_as_modified(oe)
        )

        delete_btn = ctk.CTkButton(
            frame,
            text="X",
            width=25,
            command=lambda oe=original_eng: self._delete_term(oe),
        )
        delete_btn.grid(row=index, column=2, padx=5, pady=2)

    def _render_proposal_item(self, item, frame, index):
        """Renders a single proposal entry."""
        eng = item["english_term"]

        proposal_frame = ctk.CTkFrame(frame, fg_color="transparent")
        proposal_frame.grid(row=index, column=0, columnspan=3, sticky="ew", pady=2)
        proposal_frame.grid_columnconfigure(0, weight=1)

        label_text = (
            f"{eng}  →  {item['russian_term']} (conf: {item.get('confidence', 0):.2f})"
        )
        label = ctk.CTkLabel(proposal_frame, text=label_text, anchor="w")
        label.grid(row=0, column=0, sticky="ew", padx=5)

        btn_accept = ctk.CTkButton(
            proposal_frame,
            text="✓",
            width=25,
            command=lambda e=eng, d=item: self._accept_proposal(
                e, d["russian_term"], d["category"]
            ),
        )
        btn_accept.grid(row=0, column=1, padx=(0, 5))

        btn_reject = ctk.CTkButton(
            proposal_frame,
            text="✗",
            width=25,
            command=lambda e=eng: self._reject_proposal(e),
        )
        btn_reject.grid(row=0, column=2, padx=(0, 5))

    def _mark_as_modified(self, original_eng):
        if original_eng in self.term_widgets:
            self.term_widgets[original_eng]["is_modified"] = True

    def _add_new_term_entry(self):
        # This is a simplified version. A more robust implementation would handle multiple new terms.
        new_term_id = (
            f"__new_{len([t for t in self.term_widgets.values() if t.get('is_new')])}"
        )
        new_item = {
            "english_term": new_term_id,
            "russian_term": "",
            "category": "other",
            "is_new": True,
        }
        self._render_main_item(
            new_item, self.main_scrollable_frame, len(self.term_widgets) + 1
        )
        # Make the new entry visible
        self.main_scrollable_frame._parent_canvas.yview_moveto(1.0)

    def _save_all_terms(self):
        try:
            for original_eng, data in self.term_widgets.items():
                if not data.get("is_modified"):
                    continue

                new_eng = data["eng_var"].get().strip()
                new_rus = data["rus_var"].get().strip()

                if data.get("is_new"):
                    if new_eng and new_rus:
                        self.db_manager.add_or_update_term(
                            new_eng, new_rus, data["category"]
                        )
                elif new_eng and new_rus:
                    if original_eng != new_eng:
                        self.db_manager.delete_term(original_eng)
                    self.db_manager.add_or_update_term(
                        new_eng, new_rus, data["category"]
                    )

            gui_logger.info("Словарь сохранен.")
            self._load_data()
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить словарь: {e}")
            gui_logger.error(f"Ошибка сохранения словаря: {e}", exc_info=True)

    def _delete_term(self, original_eng):
        if not messagebox.askyesno(
            "Подтверждение",
            f"Удалить термин '{original_eng}'? Это действие также удалит его из семантического индекса.",
        ):
            return

        kb_manager = self.master.kb_manager
        if not kb_manager:
            messagebox.showerror("Ошибка", "Менеджер базы знаний не инициализирован.")
            return

        vector_id = f"dict_{original_eng}"
        
        # [ИСПРАВЛЕНИЕ]: Разделяем операции для повышения отказоустойчивости
        try:
            # Шаг 1: Удаляем из векторной базы
            kb_manager.delete_entries(ids=[vector_id])
            gui_logger.info(f"Вектор для термина '{original_eng}' успешно удален из ChromaDB.")
        except Exception as e:
            error_msg = f"Критическая ошибка при удалении вектора для '{original_eng}': {e}"
            gui_logger.critical(error_msg, exc_info=True)
            messagebox.showerror("Ошибка ChromaDB", f"{error_msg}\n\nОперация отменена. Запись в основной БД не затронута.")
            return # Прерываем операцию

        try:
            # Шаг 2: Удаляем из основной базы (только если шаг 1 успешен)
            self.db_manager.delete_term(original_eng)
            gui_logger.info(f"Термин '{original_eng}' успешно удален из основной БД.")
        except Exception as e:
            error_msg = f"Критическая ошибка при удалении термина '{original_eng}' из основной БД: {e}"
            # УСИЛЕННОЕ ЛОГИРОВАНИЕ: Явно указываем на рассинхронизацию.
            critical_alert = (
                f"ДАННЫЕ РАССИНХРОНИЗИРОВАНЫ! Вектор для '{original_eng}' был удален из ChromaDB, "
                f"но запись НЕ УДАЛОСЬ удалить из SQLite. Требуется ручное вмешательство. Ошибка: {e}"
            )
            gui_logger.critical(critical_alert, exc_info=True)
            messagebox.showerror(
                "Критическая Ошибка SQLite",
                critical_alert,
            )
            return # Прерываем операцию

        # Обновляем интерфейс только после полного успеха
        self._load_data()
        self.master.update_index_status()

    def _accept_proposal(self, eng, rus, cat):
        self.db_manager.add_or_update_term(eng, rus, cat)
        self.db_manager.delete_dictionary_proposal(eng)
        self._load_data()

    def _reject_proposal(self, eng):
        self.db_manager.delete_dictionary_proposal(eng)
        self._load_data()

    def accept_all_proposals(self):
        if messagebox.askyesno("Подтверждение", "Принять все предложения?"):
            threading.Thread(
                target=self._accept_all_proposals_thread, daemon=True
            ).start()

    def _accept_all_proposals_thread(self):
        try:
            all_proposals, _ = self.db_manager.get_paginated_dictionary_proposals(query="", page=0, per_page=0, limit=10000)
            for proposal in all_proposals:
                self.db_manager.add_or_update_term(
                    proposal["english_term"],
                    proposal["russian_term"],
                    proposal["category"],
                )
            self.db_manager.clear_dictionary_proposals()
            self.after(0, self._load_data)
            gui_logger.info(
                f"Принято и добавлено в словарь {len(all_proposals)} предложений."
            )
        except Exception as e:
            gui_logger.error(
                f"Ошибка при принятии всех предложений: {e}", exc_info=True
            )
            messagebox.showerror("Ошибка", f"Не удалось принять все предложения: {e}")

    def reject_all_proposals(self):
        if messagebox.askyesno("Подтверждение", "Отклонить все предложения?"):
            threading.Thread(
                target=self._reject_all_proposals_thread, daemon=True
            ).start()

    def _reject_all_proposals_thread(self):
        try:
            count = self.db_manager.count_dictionary_proposals()
            self.db_manager.clear_dictionary_proposals()
            self.after(0, self._load_data)
            gui_logger.info(f"Отклонено {count} предложений.")
        except Exception as e:
            gui_logger.error(
                f"Ошибка при отклонении всех предложений: {e}", exc_info=True
            )
            messagebox.showerror("Ошибка", f"Не удалось отклонить все предложения: {e}")
