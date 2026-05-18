# --- НАЧАЛО ФАЙЛА: src/Perevod/gui/main_window.py ---

import customtkinter as ctk
from tkinter import filedialog, messagebox
import os
import threading
import logging
import subprocess
import sys
from Perevod.api_usage import is_placeholder_api_key
from Perevod.graph_runner import run_translation_workflow
from Perevod.project_manager import ProjectManager
from Perevod.config import settings, PROJECT_ROOT
from Perevod.database.database_manager import DatabaseManager
from Perevod.knowledge_base.knowledge_base_manager import KnowledgeBaseManager
from Perevod.model_registry import AVAILABLE_TEXT_MODELS
from .dictionary_editor import DictionaryEditorWindow
from .bible_editor import WorldBibleEditorWindow
from .quarantine_editor import QuarantineEditorWindow

# --- DESIGN CONSTANTS ---
FONT_FAMILY = "Segoe UI"
FONT_MONO = "Consolas"

# Sleek Space Dark Color Palette
COLOR_BG_DARK = "#0F0F11"       # Deep outer background
COLOR_CARD_BG = "#16161A"       # Premium card background
COLOR_CARD_BORDER = "#25252B"   # Subtle premium border
COLOR_INPUT_BG = "#1A1A20"      # Darker inputs
COLOR_TEXT_PRIMARY = "#FFFFFF"  # Primary text
COLOR_TEXT_SECONDARY = "#8A8D98"# Secondary label text

# Accent colors (harmonized HSL glow)
COLOR_PURPLE = "#8E24AA"        # Purple Accent
COLOR_PURPLE_HOVER = "#A21CAF"
COLOR_INDIGO = "#3949AB"        # Royal Indigo
COLOR_INDIGO_HOVER = "#4F5D75"
COLOR_GREEN = "#00C853"         # Neon Green
COLOR_GREEN_HOVER = "#00E676"
COLOR_ORANGE = "#FF8F00"        # Warm Orange
COLOR_ORANGE_HOVER = "#FFA000"
COLOR_GREY = "#37474F"          # Steel Grey
COLOR_GREY_HOVER = "#455A64"
COLOR_RED = "#E53935"           # Velvet Red
COLOR_RED_HOVER = "#EF5350"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")
gui_logger = logging.getLogger("NovelTranslator.GUI")
PLACEHOLDER_API_KEY_MESSAGE = (
    "API ключ выглядит как test/fake placeholder. Укажите реальный GOOGLE_API_KEY."
)


class TextHandler(logging.Handler):
    def __init__(self, ctk_textbox):
        super().__init__()
        self.ctk_textbox = ctk_textbox
        self.setFormatter(logging.Formatter("%(asctime)s - %(message)s", "%H:%M:%S"))
        self.setLevel(logging.INFO)

    def emit(self, record):
        msg = self.format(record)
        try:
            if self.ctk_textbox.winfo_exists():
                self.ctk_textbox.after(0, self._append_message, msg)
        except Exception:
            pass

    def _append_message(self, msg):
        try:
            if not self.ctk_textbox.winfo_exists():
                return
            self.ctk_textbox.configure(state="normal")
            self.ctk_textbox.insert(ctk.END, msg + "\n")
            self.ctk_textbox.see(ctk.END)
            self.ctk_textbox.configure(state="disabled")
        except Exception:
            pass


class TranslatorGUI(ctk.CTk):
    def __init__(self, cli_args=None):
        super().__init__()
        self.cli_args = cli_args
        self.title("Переводчик Новелл v9.1 PRO")
        self.geometry("1200x900")
        self.minsize(1100, 850)
        self.configure(fg_color=COLOR_BG_DARK)

        self.db_manager = None
        self.kb_manager = None
        self.project_manager = ProjectManager()
        self.translation_running = False
        
        # Механизм для предотвращения гонки состояний при инициализации
        self.init_lock = threading.Lock()
        self.current_init_id = 0

        self.settings_vars = {
            "project_name": ctk.StringVar(value="Default"),
            "GOOGLE_API_KEY": ctk.StringVar(value=settings.GOOGLE_API_KEY),
            "input_dir": ctk.StringVar(),
            "output_dir": ctk.StringVar(),
            "overwrite_existing": ctk.BooleanVar(value=True),
            "temperature": ctk.DoubleVar(value=settings.temperature),
            "top_p": ctk.DoubleVar(value=settings.top_p),
            "embedding_model_name": ctk.StringVar(value=settings.embedding_model_name),
            "analysis_model_name": ctk.StringVar(value=settings.analysis_model_name),
            "curation_model_name": ctk.StringVar(value=settings.curation_model_name),
            "translation_model_name": ctk.StringVar(
                value=settings.translation_model_name
            ),
            "qa_model_name": ctk.StringVar(value=settings.qa_model_name),
            "summarization_model_name": ctk.StringVar(value=settings.summarization_model_name),
        }

        self.temp_label_var = ctk.StringVar(
            value=f"{self.settings_vars['temperature'].get():.2f}"
        )
        self.top_p_label_var = ctk.StringVar(
            value=f"{self.settings_vars['top_p'].get():.2f}"
        )

        self.create_widgets()
        self._configure_logging()
        self.after(100, self.load_initial_settings)

    def _configure_logging(self):
        self.log_handler = TextHandler(self.log_textbox)
        logging.getLogger("NovelTranslator").addHandler(self.log_handler)

    def create_widgets(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # --- Top Header Panel ---
        header_frame = ctk.CTkFrame(self, fg_color=COLOR_CARD_BG, border_color=COLOR_CARD_BORDER, border_width=1, corner_radius=12)
        header_frame.grid(row=0, column=0, padx=15, pady=(15, 5), sticky="ew")
        header_frame.grid_columnconfigure(1, weight=1)
        
        title_label = ctk.CTkLabel(
            header_frame, 
            text="✨ NOVEL TRANSLATOR PRO", 
            font=ctk.CTkFont(family=FONT_FAMILY, size=22, weight="bold"),
            text_color="#BB86FC"
        )
        title_label.grid(row=0, column=0, padx=20, pady=15, sticky="w")
        
        version_badge = ctk.CTkLabel(
            header_frame,
            text="v9.1 STABLE",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10, weight="bold"),
            text_color="#FFFFFF",
            fg_color="#1E1E24",
            corner_radius=6,
            height=20,
            width=80
        )
        version_badge.grid(row=0, column=2, padx=20, pady=15, sticky="e")

        # --- Project Selector Card ---
        project_frame = ctk.CTkFrame(self, fg_color=COLOR_CARD_BG, border_color=COLOR_CARD_BORDER, border_width=1, corner_radius=12)
        project_frame.grid(row=1, column=0, padx=15, pady=5, sticky="ew")
        project_frame.grid_columnconfigure(1, weight=1)
        
        project_label = ctk.CTkLabel(
            project_frame, 
            text="🗂️ Текущий проект:", 
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLOR_TEXT_PRIMARY
        )
        project_label.grid(row=0, column=0, padx=(20, 10), pady=15, sticky="w")
        
        self.project_combo = ctk.CTkComboBox(
            project_frame,
            variable=self.settings_vars["project_name"],
            state="readonly",
            command=self.load_selected_project,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            dropdown_font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLOR_INPUT_BG,
            border_color=COLOR_CARD_BORDER,
            button_color=COLOR_INDIGO,
            button_hover_color=COLOR_INDIGO_HOVER,
            corner_radius=8
        )
        self.project_combo.grid(row=0, column=1, padx=10, pady=15, sticky="ew")

        project_buttons_frame = ctk.CTkFrame(project_frame, fg_color="transparent")
        project_buttons_frame.grid(row=0, column=2, padx=(10, 20), pady=15)
        
        save_btn = ctk.CTkButton(
            project_buttons_frame,
            text="💾 Сохранить",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            width=110,
            height=32,
            fg_color=COLOR_INDIGO,
            hover_color=COLOR_INDIGO_HOVER,
            corner_radius=8,
            command=self.save_project,
        )
        save_btn.pack(side=ctk.LEFT, padx=5)
        
        delete_btn = ctk.CTkButton(
            project_buttons_frame,
            text="🗑️ Удалить",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            width=90,
            height=32,
            command=self.delete_project,
            fg_color=COLOR_RED,
            hover_color=COLOR_RED_HOVER,
            corner_radius=8
        )
        delete_btn.pack(side=ctk.LEFT)

        # --- Main Tab View ---
        self.tab_view = ctk.CTkTabview(
            self, 
            fg_color=COLOR_BG_DARK, 
            segmented_button_selected_color=COLOR_PURPLE,
            segmented_button_selected_hover_color=COLOR_PURPLE_HOVER,
            segmented_button_unselected_color=COLOR_INPUT_BG,
            segmented_button_unselected_hover_color=COLOR_CARD_BORDER,
            text_color=COLOR_TEXT_PRIMARY,
            corner_radius=12
        )
        self.tab_view.grid(row=2, column=0, padx=15, pady=5, sticky="nsew")
        self.create_main_tab(self.tab_view.add("Основное"))
        self.create_settings_tab(self.tab_view.add("Настройки"))
        self.tab_view.set("Основное")

        # --- Bottom Panel (Status, Progress & Logs) ---
        bottom_frame = ctk.CTkFrame(self, fg_color=COLOR_CARD_BG, border_color=COLOR_CARD_BORDER, border_width=1, corner_radius=12)
        bottom_frame.grid(row=3, column=0, padx=15, pady=5, sticky="ew")
        bottom_frame.grid_columnconfigure(0, weight=1)
        
        progress_frame = ctk.CTkFrame(bottom_frame, fg_color="transparent")
        progress_frame.grid(row=0, column=0, sticky="ew", padx=15, pady=10)
        progress_frame.grid_columnconfigure(0, weight=1)
        
        self.status_var = ctk.StringVar(value="Ожидание инициализации...")
        self.status_label = ctk.CTkLabel(
            progress_frame, 
            textvariable=self.status_var, 
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            text_color="#FFC107", 
            wraplength=800, 
            anchor="w"
        )
        self.status_label.grid(row=0, column=0, sticky="ew")
        
        self.progress_bar = ctk.CTkProgressBar(
            progress_frame, 
            progress_color=COLOR_GREEN,
            fg_color=COLOR_INPUT_BG,
            height=8,
            corner_radius=4
        )
        self.progress_bar.set(0)
        self.progress_bar.grid(row=1, column=0, pady=(8, 0), sticky="ew")
        
        self.log_textbox = ctk.CTkTextbox(
            bottom_frame,
            wrap=ctk.WORD,
            height=130,
            font=(FONT_MONO, 11),
            fg_color="#0A0A0C", 
            text_color="#8AF3FF", 
            border_color=COLOR_CARD_BORDER,
            border_width=1,
            corner_radius=8,
            state="disabled",
        )
        self.log_textbox.grid(row=1, column=0, padx=15, pady=(0, 15), sticky="nsew")

        # --- Bottom Control Action Panel ---
        actions_frame = ctk.CTkFrame(self, fg_color="transparent")
        actions_frame.grid(row=4, column=0, padx=15, pady=(5, 15), sticky="ew")
        actions_frame.grid_columnconfigure(list(range(6)), weight=1)

        self.init_button = ctk.CTkButton(
            actions_frame,
            text="⚙️ Инициализировать",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            height=40,
            fg_color=COLOR_PURPLE,
            hover_color=COLOR_PURPLE_HOVER,
            corner_radius=10,
            command=lambda: self.initialize_translator_gui(self.project_combo.get()),
        )
        self.init_button.grid(row=0, column=0, padx=5, pady=5, sticky="ew")
        
        self.start_button = ctk.CTkButton(
            actions_frame,
            text="▶ Перевести",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            height=40,
            command=self.start_translation,
            state=ctk.DISABLED,
            fg_color=COLOR_GREEN,
            hover_color=COLOR_GREEN_HOVER,
            corner_radius=10,
        )
        self.start_button.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        
        self.dict_button = ctk.CTkButton(
            actions_frame,
            text="📖 Словарь",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            height=40,
            command=self.open_dictionary_editor,
            state=ctk.DISABLED,
            fg_color=COLOR_INDIGO,
            hover_color=COLOR_INDIGO_HOVER,
            corner_radius=10,
        )
        self.dict_button.grid(row=0, column=2, padx=5, pady=5, sticky="ew")
        
        self.bible_button = ctk.CTkButton(
            actions_frame,
            text="🌌 Библия Вселенной",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            height=40,
            command=self.open_bible_editor,
            state=ctk.DISABLED,
            fg_color=COLOR_INDIGO,
            hover_color=COLOR_INDIGO_HOVER,
            corner_radius=10,
        )
        self.bible_button.grid(row=0, column=3, padx=5, pady=5, sticky="ew")
        
        self.quarantine_button = ctk.CTkButton(
            actions_frame,
            text="☣️ Карантин",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            height=40,
            command=self.open_quarantine_editor,
            state=ctk.DISABLED,
            fg_color=COLOR_ORANGE,
            hover_color=COLOR_ORANGE_HOVER,
            corner_radius=10,
        )
        self.quarantine_button.grid(row=0, column=4, padx=5, pady=5, sticky="ew")

        self.diag_button = ctk.CTkButton(
            actions_frame, 
            text="🔍 Диагностика БД", 
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            height=40, 
            command=self.run_db_diagnostics, 
            state=ctk.DISABLED, 
            fg_color=COLOR_GREY, 
            hover_color=COLOR_GREY_HOVER,
            corner_radius=10
        )
        self.diag_button.grid(row=0, column=5, padx=5, pady=5, sticky="ew")

    def create_main_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        
        # Central card for configuration form
        card = ctk.CTkFrame(tab, fg_color=COLOR_CARD_BG, border_color=COLOR_CARD_BORDER, border_width=1, corner_radius=12)
        card.grid(row=0, column=0, padx=15, pady=15, sticky="nsew")
        card.grid_columnconfigure(1, weight=1)
        
        ctk.CTkLabel(
            card, 
            text="🔑 Параметры проекта & Доступа", 
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
            text_color="#BB86FC"
        ).grid(row=0, column=0, columnspan=3, padx=20, pady=(20, 15), sticky="w")

        paths = {
            "🔑 API ключ Gemini:": "GOOGLE_API_KEY",
            "📂 Директория с главами:": "input_dir",
            "💾 Директория для перевода:": "output_dir",
        }
        
        for i, (label, key) in enumerate(paths.items(), start=1):
            ctk.CTkLabel(
                card, 
                text=label,
                font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
                text_color=COLOR_TEXT_PRIMARY
            ).grid(row=i, column=0, padx=20, pady=12, sticky="w")
            
            entry = ctk.CTkEntry(
                card, 
                textvariable=self.settings_vars[key], 
                fg_color=COLOR_INPUT_BG,
                border_color=COLOR_CARD_BORDER,
                text_color=COLOR_TEXT_PRIMARY,
                placeholder_text="Не настроено...",
                corner_radius=8,
                height=35
            )
            entry.grid(row=i, column=1, padx=10, pady=12, sticky="ew")
            
            if "Директория" in label:
                btn = ctk.CTkButton(
                    card,
                    text="📁 Выбрать...",
                    font=ctk.CTkFont(family=FONT_FAMILY, size=11, weight="bold"),
                    fg_color=COLOR_INDIGO,
                    hover_color=COLOR_INDIGO_HOVER,
                    corner_radius=8,
                    height=35,
                    command=lambda k=key: self.browse_directory(k),
                )
                btn.grid(row=i, column=2, padx=(10, 20), pady=12)

    def create_settings_tab(self, tab):
        tab.grid_columnconfigure((0, 1), weight=1)
        tab.grid_rowconfigure(0, weight=1)

        # Left Column - Process settings
        left_frame = ctk.CTkFrame(tab, fg_color=COLOR_CARD_BG, border_color=COLOR_CARD_BORDER, border_width=1, corner_radius=12)
        left_frame.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        left_frame.grid_columnconfigure(1, weight=1)
        
        # Right Column - Models settings
        right_frame = ctk.CTkFrame(tab, fg_color=COLOR_CARD_BG, border_color=COLOR_CARD_BORDER, border_width=1, corner_radius=12)
        right_frame.grid(row=0, column=1, padx=10, pady=10, sticky="nsew")
        right_frame.grid_columnconfigure(1, weight=1)

        # Left Column widgets
        ctk.CTkLabel(
            left_frame, 
            text="⚙️ Настройки процесса", 
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
            text_color="#BB86FC"
        ).grid(row=0, column=0, columnspan=3, pady=(20, 15), sticky="w", padx=20)
        
        ctk.CTkSwitch(
            left_frame,
            text="Перезаписывать существующие переводы",
            variable=self.settings_vars["overwrite_existing"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            progress_color=COLOR_PURPLE,
            text_color=COLOR_TEXT_PRIMARY
        ).grid(row=1, column=0, columnspan=3, padx=20, pady=10, sticky="w")
        
        ctk.CTkLabel(
            left_frame, 
            text="Температура:",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            text_color=COLOR_TEXT_PRIMARY
        ).grid(row=2, column=0, padx=20, pady=15, sticky="w")
        
        ctk.CTkSlider(
            left_frame,
            from_=0,
            to=1,
            variable=self.settings_vars["temperature"],
            progress_color=COLOR_PURPLE,
            button_color=COLOR_PURPLE,
            button_hover_color=COLOR_PURPLE_HOVER,
            command=lambda v: self.temp_label_var.set(f"{v:.2f}"),
        ).grid(row=2, column=1, padx=10, pady=15, sticky="ew")
        
        ctk.CTkLabel(
            left_frame, 
            textvariable=self.temp_label_var,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            text_color="#BB86FC"
        ).grid(row=2, column=2, padx=20)
        
        ctk.CTkLabel(
            left_frame, 
            text="Top P:",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            text_color=COLOR_TEXT_PRIMARY
        ).grid(row=3, column=0, padx=20, pady=15, sticky="w")
        
        ctk.CTkSlider(
            left_frame,
            from_=0,
            to=1,
            variable=self.settings_vars["top_p"],
            progress_color=COLOR_PURPLE,
            button_color=COLOR_PURPLE,
            button_hover_color=COLOR_PURPLE_HOVER,
            command=lambda v: self.top_p_label_var.set(f"{v:.2f}"),
        ).grid(row=3, column=1, padx=10, pady=15, sticky="ew")
        
        ctk.CTkLabel(
            left_frame, 
            textvariable=self.top_p_label_var,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            text_color="#BB86FC"
        ).grid(row=3, column=2, padx=20)

        # Right Column widgets
        ctk.CTkLabel(
            right_frame,
            text="🧠 Настройки моделей и Базы Знаний",
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
            text_color="#BB86FC"
        ).grid(row=0, column=0, columnspan=2, pady=(20, 15), sticky="w", padx=20)

        model_choices = AVAILABLE_TEXT_MODELS
        model_settings = {
            "Анализ терминов:": "analysis_model_name",
            "Курирование/Выбор:": "curation_model_name",
            "Финальный перевод:": "translation_model_name",
            "Оценка качества (QA):": "qa_model_name",
            "Суммаризация (контекст):": "summarization_model_name",
        }

        for i, (label, key) in enumerate(model_settings.items(), start=1):
            ctk.CTkLabel(
                right_frame, 
                text=label,
                font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
                text_color=COLOR_TEXT_PRIMARY
            ).grid(row=i, column=0, padx=20, pady=6, sticky="w")
            
            ctk.CTkComboBox(
                right_frame, 
                variable=self.settings_vars[key], 
                values=model_choices,
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                dropdown_font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                fg_color=COLOR_INPUT_BG,
                border_color=COLOR_CARD_BORDER,
                button_color=COLOR_INDIGO,
                button_hover_color=COLOR_INDIGO_HOVER,
                corner_radius=8,
                height=30
            ).grid(row=i, column=1, padx=(10, 20), pady=6, sticky="ew")

        # Semantic index card button
        self.build_index_button = ctk.CTkButton(
            right_frame,
            text="🧠 Перестроить семантический индекс",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            height=35,
            command=self.build_semantic_index_gui,
            state=ctk.DISABLED,
            fg_color="#6A1B9A",
            hover_color="#4A148C",
            corner_radius=8
        )
        self.build_index_button.grid(
            row=len(model_settings) + 1,
            column=0,
            columnspan=2,
            padx=20,
            pady=(20, 10),
            sticky="ew",
        )
        
        self.index_status_label = ctk.CTkLabel(
            right_frame, 
            text="Индекс: Н/Д", 
            font=ctk.CTkFont(family=FONT_FAMILY, size=11, weight="bold"),
            text_color=COLOR_TEXT_SECONDARY
        )
        self.index_status_label.grid(
            row=len(model_settings) + 2,
            column=0,
            columnspan=2,
            padx=20,
            pady=(0, 20),
            sticky="ew",
        )

    def browse_directory(self, key):
        directory = filedialog.askdirectory()
        if directory:
            self.settings_vars[key].set(directory)

    def get_current_settings_from_ui(self):
        return {key: var.get() for key, var in self.settings_vars.items()}

    def update_ui_for_translation_state(self, is_running):
        state = ctk.DISABLED if is_running else ctk.NORMAL
        self.start_button.configure(
            text="Перевод идет..." if is_running else "▶ Перевести",
            state=ctk.DISABLED if is_running else ctk.NORMAL,
            fg_color=(COLOR_RED if is_running else COLOR_GREEN),
            hover_color=(COLOR_RED_HOVER if is_running else COLOR_GREEN_HOVER),
        )
        for button in [
            self.init_button,
            self.dict_button,
            self.bible_button,
            self.build_index_button,
            self.quarantine_button,
            self.diag_button,
        ]:
            button.configure(state=state)
        self.project_combo.configure(state="disabled" if is_running else "readonly")

    def update_progress(self, value, text):
        if self.winfo_exists():
            self.progress_bar.set(value / 100)
            self.status_var.set(text)
            
            # Dynamic status coloring for premium feel
            status_label = self.__dict__.get("status_label")
            if status_label is not None:
                try:
                    if self.translation_running:
                        status_label.configure(text_color="#E040FB")  # Bright neon magenta
                    elif "Ошибка" in text or "не удалось" in text or "placeholder" in text:
                        status_label.configure(text_color="#FF5252")  # Warning red
                    elif "успешно" in text or "завершено" in text or "инициализирован" in text:
                        status_label.configure(text_color="#00C853")  # Glowing green
                    else:
                        status_label.configure(text_color="#FFC107")  # Amber gold
                except Exception:
                    pass
                
            self.update_idletasks()

    def load_initial_settings(self):
        self.update_project_list()
        project_to_load = (
            self.cli_args.project
            if self.cli_args and self.cli_args.project
            else "Default"
        )
        if project_to_load in self.project_manager.get_project_names():
            self.settings_vars["project_name"].set(project_to_load)
        self.load_project_settings(self.settings_vars["project_name"].get())

    def update_project_list(self):
        project_names = self.project_manager.get_project_names()
        if not project_names:
            self.project_combo.configure(values=["Default"])
            self.settings_vars["project_name"].set("Default")
        else:
            self.project_combo.configure(values=["Default"] + project_names)

    def load_project_settings(self, name):
        gui_logger.info(f"Загрузка проекта: {name}")
        project_settings = self.project_manager.get_project_settings(name)
        for key, value in project_settings.items():
            if key in self.settings_vars:
                self.settings_vars[key].set(value)
        self.temp_label_var.set(f"{self.settings_vars['temperature'].get():.2f}")
        self.top_p_label_var.set(f"{self.settings_vars['top_p'].get():.2f}")

    def load_selected_project(self, selected_name):
        self.load_project_settings(selected_name)
        self.initialize_translator_gui(selected_name)

    def save_project(self):
        name = self.settings_vars["project_name"].get()
        if name == "Default":
            name = ctk.CTkInputDialog(
                text="Введите имя нового проекта:", title="Сохранить проект"
            ).get_input()
            if not name:
                return

        current_settings = self.get_current_settings_from_ui()
        current_settings["project_name"] = name
        if self.project_manager.add_or_update_project(name, current_settings):
            gui_logger.info(f"Проект '{name}' успешно сохранен.")
            self.update_project_list()
            self.settings_vars["project_name"].set(name)
        else:
            messagebox.showerror("Ошибка", f"Не удалось сохранить проект '{name}'.")

    def delete_project(self):
        name = self.settings_vars["project_name"].get()
        if name == "Default":
            messagebox.showwarning("Внимание", "Нельзя удалить проект 'Default'.")
            return

        if messagebox.askyesno(
            "Подтверждение",
            f"Вы уверены, что хотите удалить проект '{name}'? Это действие необратимо и удалит ВСЕ данные проекта, включая базу знаний.",
        ):
            try:
                if self.project_manager.delete_project(name):
                    gui_logger.info(
                        f"Проект '{name}' и все связанные данные успешно удалены."
                    )
                    self.update_project_list()
                    self.settings_vars["project_name"].set("Default")
                    self.load_project_settings("Default")
                else:
                    messagebox.showerror(
                        "Ошибка",
                        f"Не удалось удалить проект '{name}' из основной базы данных.",
                    )
            except Exception as e:
                gui_logger.critical(
                    f"КРИТИЧЕСКАЯ ОШИБКА при удалении проекта '{name}'. Ошибка: {e}",
                    exc_info=True,
                )
                messagebox.showerror(
                    "Ошибка",
                    f"Произошла ошибка при удалении данных проекта '{name}'. Подробности в логе.",
                )

    def _set_ui_buttons_state(self, state):
        """Вспомогательный метод для изменения состояния кнопок."""
        for btn in [
            self.start_button,
            self.dict_button,
            self.bible_button,
            self.build_index_button,
            self.quarantine_button,
            self.diag_button,
        ]:
            btn.configure(state=state)

    def initialize_translator_gui(self, project_name_to_init: str):
        """Запускает процесс инициализации в отдельном потоке."""
        self.update_progress(0, f"Запрос на инициализацию проекта '{project_name_to_init}'...")
        self._set_ui_buttons_state(ctk.DISABLED) # Немедленно блокируем кнопки

        with self.init_lock:
            self.current_init_id += 1
            thread_id = self.current_init_id

        threading.Thread(
            target=self._initialize_thread,
            args=(project_name_to_init, thread_id),
            daemon=True
        ).start()

    def _initialize_thread(self, project_name: str, thread_id: int):
        """Этот метод выполняется в отдельном потоке."""
        try:
            with self.init_lock:
                if thread_id != self.current_init_id:
                    gui_logger.info(f"Поток инициализации (ID: {thread_id}) для '{project_name}' отменен.")
                    return

            self.after(0, self.update_progress, 10, f"Инициализация '{project_name}'...")

            if project_name == "Default":
                self.db_manager = None
                self.kb_manager = None
                self.after(0, self.update_progress, 100, "Выберите или создайте проект.")
                return

            api_key = self.settings_vars["GOOGLE_API_KEY"].get()
            embedding_model = self.settings_vars["embedding_model_name"].get()

            if not api_key:
                raise ValueError("API ключ не указан в настройках.")
            if is_placeholder_api_key(api_key):
                raise ValueError(PLACEHOLDER_API_KEY_MESSAGE)

            db_manager = DatabaseManager(project_name)
            kb_manager = KnowledgeBaseManager(project_name, api_key, embedding_model)

            with self.init_lock:
                if thread_id == self.current_init_id:
                    self.db_manager = db_manager
                    self.kb_manager = kb_manager
                    self.after(0, self._init_success, project_name)
                else:
                    gui_logger.info(f"Поток (ID: {thread_id}) для '{project_name}' завершился, но уже устарел.")

        except Exception as e:
            gui_logger.error(f"Ошибка инициализации для '{project_name}': {e}", exc_info=True)
            with self.init_lock:
                if thread_id == self.current_init_id:
                    self.after(0, self._init_failure, e)

    def _init_success(self, project_name):
        """Обновление GUI после успешной инициализации."""
        self.update_index_status()
        self.update_progress(100, f"Проект '{project_name}' инициализирован.")
        status_label = self.__dict__.get("status_label")
        if status_label is not None:
            try:
                status_label.configure(text_color="#00C853")
            except Exception:
                pass
        self._set_ui_buttons_state(ctk.NORMAL)

    def _init_failure(self, error):
        """Обновление GUI после неудачной инициализации."""
        self.update_progress(0, f"Ошибка: {error}")
        status_label = self.__dict__.get("status_label")
        if status_label is not None:
            try:
                status_label.configure(text_color="#FF5252")
            except Exception:
                pass
        self.db_manager = None
        self.kb_manager = None
        self._set_ui_buttons_state(ctk.DISABLED)
        index_status_label = self.__dict__.get("index_status_label")
        if index_status_label is not None:
            try:
                index_status_label.configure(text="Индекс: Н/Д", text_color="#FF5252")
            except Exception:
                pass

    def update_index_status(self):
        index_status_label = self.__dict__.get("index_status_label")
        if self.kb_manager and self.kb_manager.collection:
            if index_status_label is not None:
                try:
                    index_status_label.configure(text=f"Индекс: {self.kb_manager.collection.count()} записей")
                    index_status_label.configure(text_color="#BB86FC")
                except Exception:
                    pass
        else:
            if index_status_label is not None:
                try:
                    index_status_label.configure(text="Индекс: Н/Д")
                    index_status_label.configure(text_color=COLOR_TEXT_SECONDARY)
                except Exception:
                    pass

    def build_semantic_index_gui(self):
        if messagebox.askyesno("Подтверждение", "Перестроить семантический индекс? Это может занять время и токены API."):
            self.update_progress(0, "Начало перестройки индекса...")
            threading.Thread(target=self._build_index_thread, daemon=True).start()

    def _build_index_thread(self):
        try:
            self.kb_manager.rebuild_index_from_db(self.db_manager, progress_callback=self.update_progress)
            self.after(0, self.update_index_status)
        except Exception as e:
            gui_logger.error(f"Ошибка построения индекса: {e}", exc_info=True)
            self.update_progress(0, f"Ошибка индексации: {e}")

    def start_translation(self):
        if self.translation_running:
            gui_logger.warning("Перевод уже запущен; повторный запуск проигнорирован.")
            return
        settings = self.get_current_settings_from_ui()
        if not all([settings.get('GOOGLE_API_KEY'), settings.get('input_dir'), settings.get('output_dir')]):
            messagebox.showerror("Ошибка", "Пожалуйста, заполните API ключ и обе директории на вкладке 'Основное'.")
            return
        if is_placeholder_api_key(settings.get("GOOGLE_API_KEY", "")):
            messagebox.showerror("Ошибка", PLACEHOLDER_API_KEY_MESSAGE)
            return
        self.translation_running = True
        status_label = self.__dict__.get("status_label")
        if status_label is not None:
            try:
                status_label.configure(text_color="#00E676")
            except Exception:
                pass
        try:
            threading.Thread(target=self._translation_thread, daemon=True).start()
        except Exception:
            self.translation_running = False
            raise

    def _translation_thread(self):
        self.translation_running = True
        self.after(0, self.update_ui_for_translation_state, True)
        try:
            current_settings = self.get_current_settings_from_ui()
            final_state = run_translation_workflow(
                project_name=current_settings["project_name"],
                project_settings=current_settings,
                progress_callback=self.update_progress,
            )
            if final_state.get("error"):
                raise Exception(final_state["error"])
            self.update_progress(100, f"Перевод и аудит успешно завершены! Обработано глав: {len(final_state.get('processed_chapters', []))}")
            gui_logger.info("Полный цикл перевода и аудита успешно завершен.")
        except Exception as e:
            error_message = f"Критическая ошибка в рабочем процессе: {e}"
            gui_logger.error(error_message, exc_info=True)
            self.after(0, self.update_progress, 0, f"Ошибка: {e}")
            self.after(0, messagebox.showerror, "Ошибка Перевода", error_message)
        finally:
            self.translation_running = False
            self.after(0, self.update_ui_for_translation_state, False)

    def open_dictionary_editor(self):
        if self.db_manager:
            DictionaryEditorWindow(self, self.db_manager)
        else:
            messagebox.showerror("Ошибка", "Сначала инициализируйте проект.")

    def open_bible_editor(self):
        if self.db_manager:
            WorldBibleEditorWindow(
                self,
                self.db_manager,
                self.settings_vars["GOOGLE_API_KEY"].get(),
                self.settings_vars["translation_model_name"].get(),
            )
        else:
            messagebox.showerror("Ошибка", "Сначала инициализируйте проект.")

    def open_quarantine_editor(self):
        if self.db_manager:
            QuarantineEditorWindow(self, self.db_manager)
        else:
            messagebox.showerror("Ошибка", "Сначала инициализируйте проект.")

    def run_db_diagnostics(self):
        project_name = self.settings_vars["project_name"].get()
        if project_name == "Default" or not self.db_manager:
            messagebox.showerror("Ошибка", "Сначала выберите и инициализируйте проект для диагностики.")
            return
        try:
            env = os.environ.copy()
            env["PYTHONPATH"] = os.pathsep.join(
                [os.path.join(PROJECT_ROOT, "src"), env.get("PYTHONPATH", "")]
            )
            command = [
                sys.executable,
                os.path.join(PROJECT_ROOT, "scripts", "audit_and_clean_database.py"),
                "--project",
                project_name,
                "--dry-run",
            ]
            subprocess.Popen(command, cwd=PROJECT_ROOT, env=env)
            gui_logger.info(f"Запущен скрипт аудита и очистки для проекта '{project_name}'.")
        except Exception as e:
            messagebox.showerror("Ошибка запуска скрипта", f"Не удалось запустить скрипт диагностики: {e}")
            gui_logger.error(f"Ошибка при запуске скрипта диагностики: {e}", exc_info=True)

# --- КОНЕЦ ФАЙЛА: src/Perevod/gui/main_window.py ---
