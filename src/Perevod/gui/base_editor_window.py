import customtkinter as ctk


class BaseEditorWindow(ctk.CTkToplevel):
    def __init__(self, master, title, geometry="1000x700"):
        super().__init__(master)
        self.title(title)
        self.geometry(geometry)
        self.minsize(800, 600)
        self.transient(master)
        self.grab_set()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self.search_var = ctk.StringVar()
        self.search_var.trace_add("write", self.filter_entries)
        self.search_entry = ctk.CTkEntry(
            self, textvariable=self.search_var, placeholder_text="Поиск..."
        )
        self.search_entry.grid(row=0, column=0, padx=10, pady=(10, 5), sticky="ew")

        self.tab_view = ctk.CTkTabview(self)
        self.tab_view.grid(row=1, column=0, padx=10, pady=5, sticky="nsew")

        self.button_frame = ctk.CTkFrame(self)
        self.button_frame.grid(row=2, column=0, padx=10, pady=(5, 10), sticky="ew")

    def filter_entries(self, *args):
        raise NotImplementedError("Этот метод должен быть реализован в дочернем классе")

    def _create_action_buttons(self, button_configs):
        self.button_frame.grid_columnconfigure(
            list(range(len(button_configs))), weight=1
        )
        for i, config in enumerate(button_configs):
            btn = ctk.CTkButton(
                self.button_frame,
                text=config["text"],
                command=config["command"],
                fg_color=config.get("color"),
                hover_color=config.get("hover"),
            )
            btn.grid(row=0, column=i, padx=5, pady=5, sticky="ew")
