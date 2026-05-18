import customtkinter as ctk
from .base_editor import BaseEditorWindow
import math


class TabbedPaginatedEditor(BaseEditorWindow):
    def __init__(self, master, title, db_manager):
        super().__init__(master, title, db_manager)

        self.current_page = 0
        self.items_per_page = 15
        self.total_items = 0
        self.total_pages = 0
        self.current_items = []

        # --- Main Layout ---
        self.main_frame.grid_rowconfigure(0, weight=1)
        self.main_frame.grid_columnconfigure(0, weight=1)

        # --- Tab View ---
        self.tab_view = ctk.CTkTabview(self.main_frame)
        self.tab_view.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        self.main_tab = self.tab_view.add("Основное")
        self.proposals_tab = self.tab_view.add("Предложения")

        self.main_tab.grid_columnconfigure(0, weight=1)
        self.main_tab.grid_rowconfigure(0, weight=1)
        self.proposals_tab.grid_columnconfigure(0, weight=1)
        self.proposals_tab.grid_rowconfigure(0, weight=1)

        # --- Scrollable Frames for Content ---
        self.main_scrollable_frame = ctk.CTkScrollableFrame(
            self.main_tab, fg_color="transparent"
        )
        self.main_scrollable_frame.grid(row=0, column=0, sticky="nsew")

        self.proposals_scrollable_frame = ctk.CTkScrollableFrame(
            self.proposals_tab, fg_color="transparent"
        )
        self.proposals_scrollable_frame.grid(row=0, column=0, sticky="nsew")

        # --- Bottom Control Frame ---
        control_frame = ctk.CTkFrame(self.main_frame)
        control_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=5)
        control_frame.grid_columnconfigure(1, weight=1)

        # --- Pagination Controls ---
        pagination_frame = ctk.CTkFrame(control_frame, fg_color="transparent")
        pagination_frame.grid(row=0, column=1, sticky="ew")
        pagination_frame.grid_columnconfigure(1, weight=1)

        self.prev_button = ctk.CTkButton(
            pagination_frame, text="< Назад", command=self.prev_page, width=100
        )
        self.prev_button.grid(row=0, column=0, padx=5, pady=5)

        self.page_label = ctk.CTkLabel(pagination_frame, text="Страница 1 / 1")
        self.page_label.grid(row=0, column=1, padx=5, pady=5)

        self.next_button = ctk.CTkButton(
            pagination_frame, text="Вперед >", command=self.next_page, width=100
        )
        self.next_button.grid(row=0, column=2, padx=5, pady=5)

        # --- Action Buttons ---
        action_buttons_frame = ctk.CTkFrame(control_frame, fg_color="transparent")
        action_buttons_frame.grid(row=0, column=2, sticky="e")

        self.accept_all_button = ctk.CTkButton(
            action_buttons_frame,
            text="Принять все",
            command=self.accept_all_proposals,
            fg_color="#2E7D32",
            hover_color="#1B5E20",
        )
        self.accept_all_button.pack(side=ctk.LEFT, padx=5, pady=5)

        self.reject_all_button = ctk.CTkButton(
            action_buttons_frame,
            text="Отклонить все",
            command=self.reject_all_proposals,
            fg_color="#D32F2F",
            hover_color="#B71C1C",
        )
        self.reject_all_button.pack(side=ctk.LEFT, padx=5, pady=5)

    def _render_main_item(self, item, frame, index):
        raise NotImplementedError("Subclasses must implement _render_main_item")

    def _render_proposal_item(self, item, frame, index):
        raise NotImplementedError("Subclasses must implement _render_proposal_item")

    def _load_data(self):
        raise NotImplementedError("Subclasses must implement _load_data")

    def _display_page(self):
        # Clear previous content
        for widget in self.main_scrollable_frame.winfo_children():
            widget.destroy()
        for widget in self.proposals_scrollable_frame.winfo_children():
            widget.destroy()

        # This logic assumes data loading provides a list of items for the current page
        # and distinguishes between main items and proposals.
        # The actual data loading and filtering must be handled in the subclass's _load_data method.

        # Example of how rendering would be called (actual data comes from subclass)
        # for i, item in enumerate(self.current_main_items):
        #     self._render_main_item(item, self.main_scrollable_frame, i)
        # for i, item in enumerate(self.current_proposal_items):
        #     self._render_proposal_item(item, self.proposals_scrollable_frame, i)

        self._update_pagination_label()

    def _update_pagination_label(self):
        self.total_pages = math.ceil(self.total_items / self.items_per_page)
        self.page_label.configure(
            text=f"Страница {self.current_page + 1} / {max(1, self.total_pages)}"
        )
        self.prev_button.configure(
            state="normal" if self.current_page > 0 else "disabled"
        )
        self.next_button.configure(
            state="normal" if self.current_page < self.total_pages - 1 else "disabled"
        )

    def prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self._load_data()

    def next_page(self):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            self._load_data()

    def accept_all_proposals(self):
        # Logic to be implemented in subclass
        pass

    def reject_all_proposals(self):
        # Logic to be implemented in subclass
        pass
