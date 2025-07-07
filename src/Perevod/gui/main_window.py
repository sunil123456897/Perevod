# gui/main_window.py

import customtkinter as ctk
from tkinter import filedialog, messagebox
import os
import threading
import logging
from Perevod.graph_runner import run_translation_workflow
from Perevod.project_manager import ProjectManager
from Perevod.config import DEFAULT_SETTINGS, AVAILABLE_MODELS
from Perevod.database.database_manager import DatabaseManager
from Perevod.knowledge_base.knowledge_base_manager import KnowledgeBaseManager
from Perevod.agents.translator import simple_translate
from .dictionary_editor import DictionaryEditorWindow
from .bible_editor import WorldBibleEditorWindow


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")
gui_logger = logging.getLogger("NovelTranslator.GUI")

# ======================================================================================
# Вспомогательные классы и обработчики
# ======================================================================================

class TextHandler(logging.Handler):
    def __init__(self, ctk_textbox):
        super().__init__(); self.ctk_textbox = ctk_textbox
        self.setFormatter(logging.Formatter('%(asctime)s - %(message)s', '%H:%M:%S'))
        self.setLevel(logging.INFO)
    def emit(self, record):
        msg = self.format(record)
        try:
            if self.ctk_textbox.winfo_exists(): self.ctk_textbox.after(0, self._append_message, msg)
        except Exception: pass
    def _append_message(self, msg):
        try:
            if not self.ctk_textbox.winfo_exists(): return
            self.ctk_textbox.configure(state="normal"); self.ctk_textbox.insert(ctk.END, msg + '\n')
            self.ctk_textbox.see(ctk.END); self.ctk_textbox.configure(state="disabled")
        except Exception: pass

# ======================================================================================
# Основной класс GUI
# ======================================================================================

class TranslatorGUI(ctk.CTk):
    def __init__(self, cli_args=None):
        super().__init__()
        self.cli_args = cli_args
        self.title("Переводчик Новелл v8.4 (Bilingual Bible)")
        self.geometry("1200x900")
        self.minsize(1100, 850)

        self.db_manager = None
        self.kb_manager = None
        self.project_manager = ProjectManager()
        self.translation_running = False
        
        self.settings_vars = {key: ctk.BooleanVar(value=val) if isinstance(val, bool) else ctk.StringVar(value=val) for key, val in DEFAULT_SETTINGS.items()}
        self.settings_vars['temperature'] = ctk.DoubleVar(value=DEFAULT_SETTINGS['temperature'])
        self.settings_vars['top_p'] = ctk.DoubleVar(value=DEFAULT_SETTINGS['top_p'])
        
        self.temp_label_var = ctk.StringVar(value=f"{self.settings_vars['temperature'].get():.2f}")
        self.top_p_label_var = ctk.StringVar(value=f"{self.settings_vars['top_p'].get():.2f}")

        self.create_widgets()
        self._configure_logging()
        self.after(100, self.load_initial_settings)

    def _configure_logging(self):
        self.log_handler = TextHandler(self.log_textbox)
        logging.getLogger("NovelTranslator").addHandler(self.log_handler)

    def create_widgets(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        
        project_frame = ctk.CTkFrame(self)
        project_frame.grid(row=0, column=0, padx=10, pady=(10, 5), sticky="ew")
        project_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(project_frame, text="Проект:").grid(row=0, column=0, padx=(10, 5), pady=10)
        self.project_combo = ctk.CTkComboBox(project_frame, variable=self.settings_vars['project_name'], state="readonly", command=self.load_selected_project)
        self.project_combo.grid(row=0, column=1, padx=5, pady=10, sticky="ew")
        
        project_buttons_frame = ctk.CTkFrame(project_frame, fg_color="transparent")
        project_buttons_frame.grid(row=0, column=2, padx=(5, 10), pady=10)
        ctk.CTkButton(project_buttons_frame, text="Сохранить как...", width=120, command=self.save_project).pack(side=ctk.LEFT, padx=5)
        ctk.CTkButton(project_buttons_frame, text="📂", width=30, command=self.open_project_data_folder).pack(side=ctk.LEFT, padx=5)
        ctk.CTkButton(project_buttons_frame, text="Удалить", width=80, command=self.delete_project, fg_color="#D32F2F", hover_color="#B71C1C").pack(side=ctk.LEFT)

        self.tab_view = ctk.CTkTabview(self)
        self.tab_view.grid(row=1, column=0, padx=10, pady=5, sticky="nsew")
        self.create_main_tab(self.tab_view.add("Основное"))
        self.create_settings_tab(self.tab_view.add("Настройки"))
        self.tab_view.set("Основное")

        bottom_frame = ctk.CTkFrame(self)
        bottom_frame.grid(row=2, column=0, padx=10, pady=(5,0), sticky="ew")
        bottom_frame.grid_columnconfigure(0, weight=1)
        progress_frame = ctk.CTkFrame(bottom_frame, fg_color="transparent")
        progress_frame.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        progress_frame.grid_columnconfigure(0, weight=1)
        self.status_var = ctk.StringVar(value="Ожидание инициализации...")
        self.status_label = ctk.CTkLabel(progress_frame, textvariable=self.status_var, wraplength=800, anchor="w")
        self.status_label.grid(row=0, column=0, sticky="ew")
        self.progress_bar = ctk.CTkProgressBar(progress_frame)
        self.progress_bar.set(0)
        self.progress_bar.grid(row=1, column=0, pady=(5,0), sticky="ew")
        self.log_textbox = ctk.CTkTextbox(bottom_frame, wrap=ctk.WORD, height=150, font=("Consolas", 12), state="disabled")
        self.log_textbox.grid(row=1, column=0, padx=5, pady=5, sticky="nsew")

        actions_frame = ctk.CTkFrame(self, fg_color="transparent")
        actions_frame.grid(row=3, column=0, padx=10, pady=10, sticky="ew")
        actions_frame.grid_columnconfigure(list(range(4)), weight=1)
        
        self.init_button = ctk.CTkButton(actions_frame, text="Инициализировать", height=40, command=self.initialize_translator_gui)
        self.init_button.grid(row=0, column=0, padx=5, pady=5, sticky="ew")
        self.start_button = ctk.CTkButton(actions_frame, text="▶ Перевести", height=40, command=self.start_translation, state=ctk.DISABLED, fg_color="#2E7D32", hover_color="#1B5E20")
        self.start_button.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.dict_button = ctk.CTkButton(actions_frame, text="Словарь", height=40, command=self.open_dictionary_editor, state=ctk.DISABLED)
        self.dict_button.grid(row=0, column=2, padx=5, pady=5, sticky="ew")
        self.bible_button = ctk.CTkButton(actions_frame, text="Библия Вселенной", height=40, command=self.open_bible_editor, state=ctk.DISABLED)
        self.bible_button.grid(row=0, column=3, padx=5, pady=5, sticky="ew")

    def create_main_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        paths = { "API ключ Gemini:": 'api_key', "Директория с главами (Проект):": 'input_dir', "Директория для перевода (Проект):": 'output_dir'}
        for i, (label, key) in enumerate(paths.items()):
            ctk.CTkLabel(tab, text=label).grid(row=i, column=0, padx=10, pady=10, sticky="w")
            entry = ctk.CTkEntry(tab, textvariable=self.settings_vars[key], width=400)
            entry.grid(row=i, column=1, padx=10, pady=10, sticky="ew")
            if "Директория" in label:
                ctk.CTkButton(tab, text="Выбрать...", command=lambda k=key: self.browse_directory(k)).grid(row=i, column=2, padx=10, pady=10)

    def create_settings_tab(self, tab):
        tab.grid_columnconfigure((0, 1), weight=1)
        left_frame = ctk.CTkFrame(tab); left_frame.grid(row=0, column=0, padx=10, pady=10, sticky="nsew"); left_frame.grid_columnconfigure(1, weight=1)
        right_frame = ctk.CTkFrame(tab); right_frame.grid(row=0, column=1, padx=10, pady=10, sticky="nsew"); right_frame.grid_columnconfigure(0, weight=1)
        
        ctk.CTkLabel(left_frame, text="Настройки процесса", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, columnspan=3, pady=(5,10), sticky="w", padx=10)
        ctk.CTkSwitch(left_frame, text="Перезаписывать существующие переводы", variable=self.settings_vars['overwrite_existing']).grid(row=1, column=0, columnspan=3, padx=10, pady=5, sticky="w")
        ctk.CTkLabel(left_frame, text="Модель для перевода:").grid(row=2, column=0, padx=10, pady=10, sticky="w")
        ctk.CTkComboBox(left_frame, variable=self.settings_vars['model_name'], values=AVAILABLE_MODELS).grid(row=2, column=1, columnspan=2, padx=10, pady=10, sticky="ew")
        ctk.CTkLabel(left_frame, text="Температура:").grid(row=3, column=0, padx=10, pady=10, sticky="w")
        ctk.CTkSlider(left_frame, from_=0, to=1, variable=self.settings_vars['temperature'], command=lambda v: self.temp_label_var.set(f"{v:.2f}")).grid(row=3, column=1, padx=10, pady=10, sticky="ew")
        ctk.CTkLabel(left_frame, textvariable=self.temp_label_var).grid(row=3, column=2, padx=10)
        ctk.CTkLabel(left_frame, text="Top P:").grid(row=4, column=0, padx=10, pady=10, sticky="w")
        ctk.CTkSlider(left_frame, from_=0, to=1, variable=self.settings_vars['top_p'], command=lambda v: self.top_p_label_var.set(f"{v:.2f}")).grid(row=4, column=1, padx=10, pady=10, sticky="ew")
        ctk.CTkLabel(left_frame, textvariable=self.top_p_label_var).grid(row=4, column=2, padx=10)

        ctk.CTkLabel(right_frame, text="Настройки Базы Знаний", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, pady=(5,10), sticky="w", padx=10)
        ctk.CTkSwitch(right_frame, text="Авто-пополнение словаря (NER)", variable=self.settings_vars['enable_smart_dictionary_update']).grid(row=1, column=0, padx=10, pady=5, sticky="w")
        ctk.CTkSwitch(right_frame, text="Авто-анализ Библии Вселенной", variable=self.settings_vars['enable_bible_analysis']).grid(row=2, column=0, padx=10, pady=5, sticky="w")
        self.build_index_button = ctk.CTkButton(right_frame, text="🧠 Перестроить семантический индекс", command=self.build_semantic_index_gui, state=ctk.DISABLED, fg_color="#6A1B9A", hover_color="#4A148C")
        self.build_index_button.grid(row=3, column=0, padx=10, pady=20, sticky="ew")
        self.index_status_label = ctk.CTkLabel(right_frame, text="Индекс: Н/Д", font=ctk.CTkFont(size=11))
        self.index_status_label.grid(row=4, column=0, padx=10, pady=5, sticky="ew")

    def browse_directory(self, key):
        if directory := filedialog.askdirectory(): self.settings_vars[key].set(directory)

    def get_current_settings_from_ui(self):
        return {key: var.get() for key, var in self.settings_vars.items()}

    def update_ui_for_translation_state(self, is_running):
        state = ctk.DISABLED if is_running else ctk.NORMAL
        self.start_button.configure(text="⏹ Остановить" if is_running else "▶ Перевести", fg_color=("#B71C1C" if is_running else "#2E7D32"), hover_color=("#D32F2F" if is_running else "#1B5E20"))
        for button in [self.init_button, self.dict_button, self.bible_button, self.build_index_button]: button.configure(state=state)
        self.project_combo.configure(state="disabled" if is_running else "readonly")

    def update_progress(self, value, text):
        if self.winfo_exists():
            self.progress_bar.set(value / 100); self.status_var.set(text); self.update_idletasks()

    def load_initial_settings(self):
        self.update_project_list()
        project_to_load = (self.cli_args.project if self.cli_args and self.cli_args.project else "Default")
        if project_to_load in self.project_manager.get_project_names():
            self.settings_vars['project_name'].set(project_to_load)
        self.load_project_settings(self.settings_vars['project_name'].get())

    def update_project_list(self):
        project_names = self.project_manager.get_project_names()
        if not project_names:
            self.project_combo.configure(values=["Default"])
            self.settings_vars['project_name'].set("Default")
        else:
            self.project_combo.configure(values=["Default"] + project_names)

    def load_project_settings(self, name):
        gui_logger.info(f"Загрузка проекта: {name}")
        settings = self.project_manager.get_project_settings(name)
        for key, value in settings.items():
            if key in self.settings_vars: self.settings_vars[key].set(value)
        self.temp_label_var.set(f"{self.settings_vars['temperature'].get():.2f}")
        self.top_p_label_var.set(f"{self.settings_vars['top_p'].get():.2f}")

    def load_selected_project(self, selected_name):
        self.load_project_settings(selected_name); self.initialize_translator_gui()

    def save_project(self):
        if not (project_name := ctk.CTkInputDialog(text="Введите имя проекта:", title="Сохранить проект").get_input()): return
        settings = self.get_current_settings_from_ui(); settings['project_name'] = project_name
        if self.project_manager.add_or_update_project(project_name, settings):
            gui_logger.info(f"Проект '{project_name}' успешно сохранен.")
            self.update_project_list(); self.settings_vars['project_name'].set(project_name)
        else: messagebox.showerror("Ошибка", f"Не удалось сохранить проект '{project_name}'.")

    def delete_project(self):
        name = self.settings_vars['project_name'].get()
        if name == "Default": messagebox.showwarning("Внимание", "Нельзя удалить проект 'Default'."); return
        if messagebox.askyesno("Подтверждение", f"Вы уверены, что хотите удалить проект '{name}'? Это действие необратимо."):
            if self.project_manager.delete_project(name):
                gui_logger.info(f"Проект '{name}' удален.")
                self.update_project_list(); self.settings_vars['project_name'].set("Default"); self.load_project_settings("Default")
            else: messagebox.showerror("Ошибка", f"Не удалось удалить проект '{name}'.")

    def open_project_data_folder(self):
        project_name = self.settings_vars['project_name'].get()
        if project_name == "Default": messagebox.showinfo("Информация", "У проекта 'Default' нет своей папки данных."); return
        path = os.path.join('_project_files', project_name); os.makedirs(path, exist_ok=True)
        try: os.startfile(path)
        except AttributeError: import subprocess; subprocess.run(['xdg-open', path])

    def initialize_translator_gui(self):
        self.update_progress(0, "Инициализация..."); threading.Thread(target=self._initialize_thread, daemon=True).start()

    def _initialize_thread(self):
        """
        Инициализирует менеджеры баз данных и знаний в отдельном потоке,
        чтобы не блокировать графический интерфейс.
        """
        try:
            project_name = self.settings_vars['project_name'].get()
            if project_name == "Default":
                self.update_progress(100, "Выберите или создайте проект для начала работы.")
                for btn in [self.start_button, self.dict_button, self.bible_button, self.build_index_button]: btn.configure(state=ctk.DISABLED)
                return
            self.db_manager = DatabaseManager(project_name)
            self.kb_manager = KnowledgeBaseManager(project_name, self.settings_vars['api_key'].get(), self.settings_vars['embedding_model_name'].get())
            self.after(0, self.update_index_status)
            self.update_progress(100, "Инициализация завершена. Готов к переводу.")
            for btn in [self.start_button, self.dict_button, self.bible_button, self.build_index_button]: btn.configure(state=ctk.NORMAL)
        except Exception as e:
            gui_logger.error(f"Ошибка инициализации: {e}", exc_info=True); self.update_progress(0, f"Ошибка инициализации: {e}")

    def update_index_status(self):
        if self.kb_manager and self.kb_manager.collection: self.index_status_label.configure(text=f"Индекс: {self.kb_manager.collection.count()} записей")
        else: self.index_status_label.configure(text="Индекс: Н/Д")

    def build_semantic_index_gui(self):
        if messagebox.askyesno("Подтверждение", "Перестроить семантический индекс? Это может занять время и токены API."):
            self.update_progress(0, "Начало перестройки индекса..."); threading.Thread(target=self._build_index_thread, daemon=True).start()

    def _build_index_thread(self):
        """
        Выполняет перестроение семантического индекса в отдельном потоке.
        """
        try:
            self.kb_manager.rebuild_index_from_db(self.db_manager, progress_callback=self.update_progress)
            self.after(0, self.update_index_status)
        except Exception as e:
            gui_logger.error(f"Ошибка построения индекса: {e}", exc_info=True); self.update_progress(0, f"Ошибка индексации: {e}")

    def start_translation(self):
        if self.translation_running: gui_logger.warning("Остановка перевода еще не реализована."); return
        settings = self.get_current_settings_from_ui()
        if not all([settings.get('api_key'), settings.get('input_dir'), settings.get('output_dir')]):
            messagebox.showerror("Ошибка", "Пожалуйста, заполните API ключ и обе директории на вкладке 'Основное'."); return
        threading.Thread(target=self._translation_thread, daemon=True).start()

    def _translation_thread(self):
        """
        Запускает полный цикл перевода в отдельном потоке,
        обрабатывая возможные ошибки и обновляя GUI.
        """
        self.translation_running = True; self.after(0, self.update_ui_for_translation_state, True)
        try:
            settings = self.get_current_settings_from_ui()
            import google.generativeai as genai
            genai.configure(api_key=settings.get('api_key'))
            model = genai.GenerativeModel(settings.get('model_name'))
            final_state = run_translation_workflow(settings, self.db_manager, self.kb_manager, model, self.update_progress)
            if final_state.get("error"): raise Exception(final_state["error"])
            self.update_progress(100, f"Перевод и аудит успешно завершены! Обработано глав: {len(final_state.get('processed_chapters', []))}")
            gui_logger.info("Полный цикл перевода и аудита успешно завершен.")
        except Exception as e:
            error_message = f"Критическая ошибка в графе: {e}"; gui_logger.error(error_message, exc_info=True)
            self.after(0, self.update_progress, 0, f"Ошибка: {e}"); self.after(0, messagebox.showerror, "Ошибка Перевода", error_message)
        finally:
            self.translation_running = False; self.after(0, self.update_ui_for_translation_state, False)

    def open_dictionary_editor(self):
        if self.db_manager: DictionaryEditorWindow(self, self.db_manager)
        else: messagebox.showerror("Ошибка", "Сначала инициализируйте проект.")

    def open_bible_editor(self):
        if self.db_manager: WorldBibleEditorWindow(self, self.db_manager, self.settings_vars['api_key'].get(), self.settings_vars['model_name'].get())
        else: messagebox.showerror("Ошибка", "Сначала инициализируйте проект.")

if __name__ == '__main__':
    app = TranslatorGUI()
    app.mainloop()