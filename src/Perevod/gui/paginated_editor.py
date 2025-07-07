import customtkinter as ctk
from .base_editor import BaseEditorWindow

class PaginatedEditorWindow(BaseEditorWindow):
    def __init__(self, master, title, db_manager):
        super().__init__(master, title, db_manager)
        self.current_page = 0
        self.items_per_page = 20
        self.total_items = 0
        self.total_pages = 0
        self.filtered_items = []

        self.create_pagination_widgets()
        self.load_data()

    def create_pagination_widgets(self):
        self.pagination_frame = ctk.CTkFrame(self)
        self.pagination_frame.pack(pady=10, fill="x")

        self.prev_button = ctk.CTkButton(self.pagination_frame, text="< Prev", command=self.prev_page)
        self.prev_button.pack(side="left", padx=5)

        self.page_label = ctk.CTkLabel(self.pagination_frame, text="Page 1/1")
        self.page_label.pack(side="left", padx=5)

        self.next_button = ctk.CTkButton(self.pagination_frame, text="Next >", command=self.next_page)
        self.next_button.pack(side="left", padx=5)

    def load_data(self):
        # This method should be overridden by subclasses to load and filter data
        raise NotImplementedError("Subclasses must implement load_data method")

    def update_pagination_info(self):
        self.total_pages = (self.total_items + self.items_per_page - 1) // self.items_per_page
        self.page_label.configure(text=f"Page {self.current_page + 1}/{self.total_pages}")
        self.prev_button.configure(state=ctk.NORMAL if self.current_page > 0 else ctk.DISABLED)
        self.next_button.configure(state=ctk.NORMAL if self.current_page < self.total_pages - 1 else ctk.DISABLED)

    def prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self.display_page()

    def next_page(self):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            self.display_page()

    def display_page(self):
        # This method should be overridden by subclasses to display data for the current page
        raise NotImplementedError("Subclasses must implement display_page method")