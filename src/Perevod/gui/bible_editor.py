import customtkinter as ctk
from tkinter import messagebox
import logging
import threading
from .paginated_editor import TabbedPaginatedEditor
from Perevod.agents.translator import simple_translate

gui_logger = logging.getLogger("NovelTranslator.GUI")


class WorldBibleEditorWindow(TabbedPaginatedEditor):
    def __init__(self, master, db_manager, api_key, model_name):
        super().__init__(master, "Редактор Библии Вселенной", db_manager)

        self.api_key = api_key
        self.model_name = model_name
        self.entry_widgets = {}
        self.search_var.trace_add("write", lambda *args: self._load_data())

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
            controls_frame, text="Добавить", command=self._add_new_entry_widget
        )
        add_button.grid(row=0, column=1, padx=5)

        save_button = ctk.CTkButton(
            controls_frame,
            text="Сохранить",
            command=self._save_all_entries,
            fg_color="#2E7D32",
            hover_color="#1B5E20",
        )
        save_button.grid(row=0, column=2, padx=5)

        self.main_scrollable_frame.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
        self.main_tab.grid_rowconfigure(1, weight=1)

    def _load_data(self):
        gui_logger.info("--- BibleEditor: Загрузка данных... ---")
        query = self.search_var.get()
        
        try:
            entries_data, total_entries = self.db_manager.get_paginated_bible_entries(query, self.current_page, self.items_per_page)
            self.total_items = total_entries
            
            proposals_data, total_proposals = self.db_manager.get_paginated_world_bible_proposals(query, self.current_page, self.items_per_page)
            
            gui_logger.info(f"Загружено {len(entries_data)} записей (всего {total_entries}).")
            gui_logger.info(f"Загружено {len(proposals_data)} предложений (всего {total_proposals}).")

            self._display_page(entries_data, proposals_data)
        except Exception as e:
            gui_logger.error(f"Критическая ошибка при загрузке данных Библии: {e}", exc_info=True)
            messagebox.showerror("Ошибка загрузки", f"Не удалось загрузить данные Библии: {e}")

    def _display_page(self, entries, proposals):
        for widget in self.main_scrollable_frame.winfo_children():
            widget.destroy()
        for widget in self.proposals_scrollable_frame.winfo_children():
            widget.destroy()

        self.entry_widgets = {}
        for i, item in enumerate(entries):
            self._render_main_item(item, self.main_scrollable_frame, i)

        for i, item in enumerate(proposals):
            self._render_proposal_item(item, self.proposals_scrollable_frame, i)

        self._update_pagination_label()

    def _render_main_item(self, item, frame, index):
        frame.grid_columnconfigure(0, weight=1)
        original_eng = item["english_name"]

        widget_data = {
            "eng_var": ctk.StringVar(value=item["english_name"]),
            "rus_var": ctk.StringVar(value=item.get("russian_name", "")),
            "cat_var": ctk.StringVar(value=item.get("category", "")),
            "is_new": item.get("is_new", False),
            "is_modified": False,
        }

        card_frame = ctk.CTkFrame(frame, border_width=1)
        card_frame.grid(row=index, column=0, padx=5, pady=5, sticky="ew")
        card_frame.grid_columnconfigure(0, weight=1)

        top_frame = ctk.CTkFrame(card_frame, fg_color="transparent")
        top_frame.grid(row=0, column=0, sticky="ew", padx=5, pady=2)
        top_frame.grid_columnconfigure((1, 3), weight=1)

        ctk.CTkLabel(top_frame, text="Англ:").grid(row=0, column=0)
        ctk.CTkEntry(top_frame, textvariable=widget_data["eng_var"]).grid(
            row=0, column=1, sticky="ew", padx=5
        )
        ctk.CTkLabel(top_frame, text="Рус:").grid(row=0, column=2)
        ctk.CTkEntry(top_frame, textvariable=widget_data["rus_var"]).grid(
            row=0, column=3, sticky="ew", padx=5
        )
        ctk.CTkLabel(top_frame, text="Кат:").grid(row=0, column=4)
        ctk.CTkEntry(top_frame, textvariable=widget_data["cat_var"], width=120).grid(
            row=0, column=5, sticky="ew", padx=5
        )

        desc_eng_textbox = ctk.CTkTextbox(card_frame, height=80, wrap="word")
        desc_eng_textbox.grid(row=1, column=0, sticky="ew", padx=5, pady=5)
        desc_eng_textbox.insert("1.0", item.get("description") or "")
        widget_data["desc_eng_textbox"] = desc_eng_textbox

        translate_btn = ctk.CTkButton(
            card_frame,
            text="▼ Перевести описание ▼",
            command=lambda w=widget_data: self._translate_description(w),
        )
        translate_btn.grid(row=2, column=0, pady=2, padx=5)

        desc_rus_textbox = ctk.CTkTextbox(card_frame, height=80, wrap="word")
        desc_rus_textbox.grid(row=3, column=0, sticky="ew", padx=5, pady=5)
        desc_rus_textbox.insert("1.0", item.get("russian_description") or "")
        widget_data["desc_rus_textbox"] = desc_rus_textbox

        del_btn = ctk.CTkButton(
            card_frame,
            text="Удалить",
            fg_color="#D32F2F",
            hover_color="#B71C1C",
            command=lambda oe=original_eng: self._delete_entry(oe),
        )
        del_btn.grid(row=4, column=0, sticky="e", padx=5, pady=5)

        self.entry_widgets[original_eng] = widget_data

        # Add traces
        for var_key in ["eng_var", "rus_var", "cat_var"]:
            widget_data[var_key].trace_add(
                "write", lambda *args, oe=original_eng: self._mark_as_modified(oe)
            )
        desc_eng_textbox.bind(
            "<KeyRelease>", lambda e, oe=original_eng: self._mark_as_modified(oe)
        )
        desc_rus_textbox.bind(
            "<KeyRelease>", lambda e, oe=original_eng: self._mark_as_modified(oe)
        )

    def _render_proposal_item(self, item, frame, index):
        eng = item["english_name"]

        card_frame = ctk.CTkFrame(frame, border_width=1)
        card_frame.grid(row=index, column=0, sticky="ew", pady=2, padx=5)
        card_frame.grid_columnconfigure(0, weight=1)

        label_text = f"{eng} ({item['category']})"
        ctk.CTkLabel(card_frame, text=label_text, anchor="w").grid(
            row=0, column=0, sticky="ew", padx=5, pady=2
        )

        desc_text = ctk.CTkLabel(
            card_frame,
            text=item["description"],
            wraplength=700,
            justify="left",
            anchor="w",
            fg_color="gray20",
            corner_radius=5,
        )
        desc_text.grid(row=1, column=0, sticky="ew", padx=5, pady=2)

        btn_frame = ctk.CTkFrame(card_frame, fg_color="transparent")
        btn_frame.grid(row=0, column=1, rowspan=2, sticky="ns", padx=5, pady=5)

        btn_accept = ctk.CTkButton(
            btn_frame,
            text="✓",
            width=30,
            command=lambda e=eng, d=item: self._accept_proposal(e, d),
        )
        btn_accept.pack(pady=2)

        btn_reject = ctk.CTkButton(
            btn_frame,
            text="✗",
            width=30,
            command=lambda e=eng: self._reject_proposal(e),
        )
        btn_reject.pack(pady=2)

    def _mark_as_modified(self, original_eng):
        if original_eng in self.entry_widgets:
            self.entry_widgets[original_eng]["is_modified"] = True

    def _add_new_entry_widget(self):
        new_id = (
            f"__new_{len([w for w in self.entry_widgets.values() if w.get('is_new')])}"
        )
        new_item = {"english_name": new_id, "is_new": True}
        self._render_main_item(
            new_item, self.main_scrollable_frame, len(self.entry_widgets)
        )
        self.main_scrollable_frame._parent_canvas.yview_moveto(1.0)

    def _save_all_entries(self):
        try:
            for original_eng, data in self.entry_widgets.items():
                if not data.get("is_modified"):
                    continue

                new_eng = data["eng_var"].get().strip()
                if not new_eng:
                    if not data.get("is_new"):
                        self.db_manager.delete_bible_entry(original_eng)
                    continue

                update_data = {
                    "russian_name": data["rus_var"].get().strip(),
                    "category": data["cat_var"].get().strip(),
                    "description": data["desc_eng_textbox"]
                    .get("1.0", "end-1c")
                    .strip(),
                    "russian_description": data["desc_rus_textbox"]
                    .get("1.0", "end-1c")
                    .strip(),
                }

                if data.get("is_new"):
                    self.db_manager.add_or_update_bible_entry(new_eng, update_data)
                else:
                    if original_eng != new_eng:
                        self.db_manager.delete_bible_entry(original_eng)
                    self.db_manager.add_or_update_bible_entry(new_eng, update_data)

            gui_logger.info("Библия сохранена.")
            self._load_data()
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить Библию: {e}")

    def _delete_entry(self, original_eng):
        if not messagebox.askyesno(
            "Подтверждение",
            f"Удалить запись '{original_eng}'? Это также удалит все связанные с ней данные из семантического индекса.",
        ):
            return

        kb_manager = self.master.kb_manager
        if not kb_manager:
            messagebox.showerror("Ошибка", "Менеджер базы знаний не инициализирован.")
            return

        # [ИСПРАВЛЕНИЕ]: Разделяем операции для повышения отказоустойчивости
        try:
            # Шаг 1: Находим и удаляем ВСЕ связанные векторы в ChromaDB
            results = kb_manager.collection.get(
                where={"name": original_eng, "source": "bible"}
            )
            ids_to_delete = results.get("ids", [])

            if ids_to_delete:
                kb_manager.delete_entries(ids=ids_to_delete)
                gui_logger.info(f"{len(ids_to_delete)} векторов для '{original_eng}' удалены из ChromaDB.")
            else:
                gui_logger.warning(f"Для записи '{original_eng}' не найдено векторов в базе знаний.")
        except Exception as e:
            error_msg = f"Критическая ошибка при удалении векторов для '{original_eng}': {e}"
            gui_logger.critical(error_msg, exc_info=True)
            messagebox.showerror("Ошибка ChromaDB", f"{error_msg}\n\nОперация отменена. Запись в основной БД не затронута.")
            return

        try:
            # Шаг 2: Затем удаляем из SQLite
            self.db_manager.delete_bible_entry(original_eng)
            gui_logger.info(f"Запись '{original_eng}' успешно удалена из основной БД.")
        except Exception as e:
            error_msg = f"Критическая ошибка при удалении записи '{original_eng}' из основной БД: {e}"
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

    def _accept_proposal(self, eng, data):
        self.db_manager.add_or_update_bible_entry(eng, data)
        self.db_manager.delete_world_bible_proposal(eng)
        self._load_data()

    def _reject_proposal(self, eng):
        self.db_manager.delete_world_bible_proposal(eng)
        self._load_data()

    def accept_all_proposals(self):
        if messagebox.askyesno("Подтверждение", "Принять все предложения?"):
            threading.Thread(
                target=self._accept_all_proposals_thread, daemon=True
            ).start()

    def _accept_all_proposals_thread(self):
        try:
            all_proposals, _ = self.db_manager.get_paginated_world_bible_proposals(query="", page=0, per_page=0, limit=10000)
            for proposal in all_proposals:
                self.db_manager.add_or_update_bible_entry(
                    proposal["english_name"], proposal
                )
            self.db_manager.clear_world_bible_proposals()
            self.after(0, self._load_data)
            gui_logger.info(
                f"Принято и добавлено в Библию {len(all_proposals)} предложений."
            )
        except Exception as e:
            gui_logger.error(
                f"Ошибка при принятии всех предложений Библии: {e}", exc_info=True
            )
            messagebox.showerror("Ошибка", f"Не удалось принять все предложения: {e}")

    def reject_all_proposals(self):
        if messagebox.askyesno("Подтверждение", "Отклонить все предложения?"):
            threading.Thread(
                target=self._reject_all_proposals_thread, daemon=True
            ).start()

    def _reject_all_proposals_thread(self):
        try:
            count = self.db_manager.count_world_bible_proposals()
            self.db_manager.clear_world_bible_proposals()
            self.after(0, self._load_data)
            gui_logger.info(f"Отклонено {count} предложений Библии.")
        except Exception as e:
            gui_logger.error(
                f"Ошибка при отклонении всех предложений Библии: {e}", exc_info=True
            )
            messagebox.showerror("Ошибка", f"Не удалось отклонить все предложения: {e}")

    def _translate_description(self, widget_data):
        eng_text = widget_data["desc_eng_textbox"].get("1.0", "end-1c").strip()
        if not eng_text:
            return

        widget_data["desc_rus_textbox"].delete("1.0", ctk.END)
        widget_data["desc_rus_textbox"].insert("1.0", "Перевод...")

        def _run_translation():
            try:
                translated_text = simple_translate(
                    eng_text, self.api_key, self.model_name
                )
                self.after(
                    0, lambda: widget_data["desc_rus_textbox"].delete("1.0", ctk.END)
                )
                self.after(
                    0,
                    lambda: widget_data["desc_rus_textbox"].insert(
                        "1.0", translated_text
                    ),
                )
            except Exception as e:
                self.after(
                    0, lambda: widget_data["desc_rus_textbox"].delete("1.0", ctk.END)
                )
                self.after(
                    0,
                    lambda e=e: widget_data["desc_rus_textbox"].insert(
                        "1.0", f"Ошибка: {e}"
                    ),
                )

        threading.Thread(target=_run_translation, daemon=True).start()
