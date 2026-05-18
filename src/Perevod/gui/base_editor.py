import customtkinter as ctk


class BaseEditorWindow(ctk.CTkToplevel):
    def __init__(self, master, title, db_manager):
        super().__init__(master)
        self.title(title)
        self.db_manager = db_manager
        self.master = master
        self.geometry("800x600")
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.search_var = ctk.StringVar()

        # Main container frame to prevent pack/grid conflicts
        self.main_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.main_frame.pack(expand=True, fill="both")
        self.main_frame.grid_rowconfigure(0, weight=1)
        self.main_frame.grid_columnconfigure(0, weight=1)

        self.lift()
        self.focus_set()
        self.grab_set()

    def on_close(self):
        self.destroy()
        self.master.focus_set()
