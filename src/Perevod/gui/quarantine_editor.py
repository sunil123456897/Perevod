import customtkinter as ctk
from tkinter import messagebox
import logging
from .paginated_editor import TabbedPaginatedEditor

gui_logger = logging.getLogger("NovelTranslator.GUI")


class QuarantineEditorWindow(TabbedPaginatedEditor):
    def __init__(self, master, db_manager):
        super().__init__(master, "Редактор Карантина", db_manager)

        self.search_var.trace_add("write", lambda *args: self._load_data())

        # Configure tabs
        self.tab_view.delete("Предложения")
        self.accept_all_button.destroy()
        self.reject_all_button.destroy()

        self._setup_controls()
        self._load_data()

    def _setup_controls(self):
        controls_frame = ctk.CTkFrame(self.main_tab, fg_color="transparent")
        controls_frame.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        controls_frame.grid_columnconfigure(0, weight=1)

        search_entry = ctk.CTkEntry(
            controls_frame, textvariable=self.search_var, placeholder_text="Поиск..."
        )
        search_entry.grid(row=0, column=0, sticky="ew", padx=(0, 5))

        self.main_scrollable_frame.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
        self.main_tab.grid_rowconfigure(1, weight=1)

    def _load_data(self):
        query = self.search_var.get()
        terms_data, total_terms = self.db_manager.get_paginated_quarantined_terms(
            query, self.current_page, self.items_per_page
        )
        self.total_items = total_terms
        self._display_page(terms_data)

    def _display_page(self, terms):
        for widget in self.main_scrollable_frame.winfo_children():
            widget.destroy()

        ctk.CTkLabel(
            self.main_scrollable_frame,
            text="Термин (Англ -> Рус)",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, padx=5, pady=5, sticky="w")
        ctk.CTkLabel(
            self.main_scrollable_frame,
            text="Причина карантина",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=1, padx=5, pady=5, sticky="w")

        for i, item in enumerate(terms):
            self._render_item(item, i + 1)

        self._update_pagination_label()

    def _render_item(self, item, index):
        self.main_scrollable_frame.grid_columnconfigure((0, 1), weight=1)

        term_text = f"{item['english_term']} -> {item['russian_term']}"
        term_label = ctk.CTkLabel(
            self.main_scrollable_frame, text=term_text, anchor="w"
        )
        term_label.grid(row=index, column=0, padx=5, pady=2, sticky="ew")

        reason_label = ctk.CTkLabel(
            self.main_scrollable_frame, text=item["reason"], anchor="w", wraplength=300
        )
        reason_label.grid(row=index, column=1, padx=5, pady=2, sticky="ew")

        restore_btn = ctk.CTkButton(
            self.main_scrollable_frame,
            text="Восстановить",
            command=lambda item_id=item["id"]: self._restore_term(item_id),
        )
        restore_btn.grid(row=index, column=2, padx=5, pady=2)

        delete_btn = ctk.CTkButton(
            self.main_scrollable_frame,
            text="Удалить",
            fg_color="#D32F2F",
            hover_color="#B71C1C",
            command=lambda item_id=item["id"]: self._delete_term(item_id),
        )
        delete_btn.grid(row=index, column=3, padx=5, pady=2)

    def _restore_term(self, term_id):
        try:
            self.db_manager.restore_term(term_id)
            gui_logger.info(f"Термин с ID {term_id} восстановлен из карантина.")
            self._load_data()
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось восстановить термин: {e}")
            gui_logger.error(
                f"Ошибка восстановления термина из карантина: {e}", exc_info=True
            )

    def _delete_term(self, term_id):
        if not messagebox.askyesno(
            "Подтверждение", "Вы уверены, что хотите навсегда удалить этот термин?"
        ):
            return
        try:
            self.db_manager.delete_from_quarantine(term_id)
            gui_logger.info(f"Термин с ID {term_id} удален из карантина.")
            self._load_data()
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось удалить термин: {e}")
            gui_logger.error(
                f"Ошибка удаления термина из карантина: {e}", exc_info=True
            )
