import customtkinter as ctk

class BaseEditorWindow(ctk.CTkToplevel):
    def __init__(self, master, title, db_manager):
        super().__init__(master)
        self.title(title)
        self.db_manager = db_manager
        self.master = master
        self.geometry("800x600")
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.create_widgets()

    def create_widgets(self):
        # This method should be overridden by subclasses
        pass

    def on_close(self):
        self.destroy()
        self.master.focus_set()
