# gui.py

import customtkinter as ctk
from tkinter import filedialog, messagebox, Menu
import os
import shutil
import threading
import logging
import time
import json
import re
import difflib
import sys
import google.generativeai as genai

from translator import NovelTranslator, InitializationError
from project_manager import ProjectManager
from config import DEFAULT_SETTINGS, AVAILABLE_MODELS

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

gui_logger = logging.getLogger("NovelTranslator.GUI")

class TextHandler(logging.Handler):
    def __init__(self, ctk_textbox):
        super().__init__()
        self.ctk_textbox = ctk_textbox
        self.setFormatter(logging.Formatter('%(asctime)s - %(message)s', '%H:%M:%S'))
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
            self.ctk_textbox.insert(ctk.END, msg + '\n')
            self.ctk_textbox.see(ctk.END)
        except Exception:
            pass

class TranslatorGUI(ctk.CTk):
    def __init__(self, cli_args=None):
        super().__init__()
        self.cli_args = cli_args
        self.title("Переводчик Новелл v7.1 (Steel Frame)") # Обновим версию для наглядности
        self.geometry("1200x900")
        self.minsize(1100, 850)

        self.translator = None
        self.project_manager = ProjectManager()
        self.translation_running = False
        self.last_processed_texts = []
        self.settings_vars = {}
        for key, default_value in DEFAULT_SETTINGS.items():
            VarClass = ctk.BooleanVar if isinstance(default_value, bool) else ctk.IntVar if isinstance(default_value, int) else ctk.DoubleVar if isinstance(default_value, float) else ctk.StringVar
            self.settings_vars[key] = VarClass(value=default_value)
        
        self.temp_label_var = ctk.StringVar(value=f"{self.settings_vars['temperature'].get():.2f}")
        self.top_p_label_var = ctk.StringVar(value=f"{self.settings_vars['top_p'].get():.2f}")

        self.dictionary_editor_window = None
        self.interactive_editor_window = None
        self.consistency_checker_window = None
        self.master_editor_window = None
        self.knowledge_auditor_window = None
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
        tab_main = self.tab_view.add("Основное")
        self.create_main_tab(tab_main)
        
        tab_settings = self.tab_view.add("Настройки Проекта")
        self.create_settings_tab(tab_settings)

        tab_bible = self.tab_view.add("Библия Вселенной")
        self.create_world_bible_tab(tab_bible)
        self.tab_view.set("Основное")

        bottom_frame = ctk.CTkFrame(self)
        bottom_frame.grid(row=2, column=0, padx=10, pady=(5,0), sticky="ew")
        bottom_frame.grid_columnconfigure(0, weight=1)
        bottom_frame.grid_rowconfigure(1, weight=1)
        progress_frame = ctk.CTkFrame(bottom_frame)
        progress_frame.grid(row=0, column=0, sticky="ew")
        progress_frame.grid_columnconfigure(0, weight=1)
        self.status_var = ctk.StringVar(value="Ожидание инициализации...")
        self.status_label = ctk.CTkLabel(progress_frame, textvariable=self.status_var, wraplength=800, anchor="w")
        self.status_label.grid(row=0, column=0, padx=10, pady=(5,0), sticky="ew")
        self.progress_bar = ctk.CTkProgressBar(progress_frame)
        self.progress_bar.set(0)
        self.progress_bar.grid(row=1, column=0, padx=10, pady=(0, 5), sticky="ew")
        self.log_textbox = ctk.CTkTextbox(bottom_frame, wrap=ctk.WORD, height=150, font=("Consolas", 12))
        self.log_textbox.grid(row=1, column=0, sticky="nsew")

        actions_frame = ctk.CTkFrame(self, fg_color="transparent")
        actions_frame.grid(row=3, column=0, padx=10, pady=10, sticky="ew")
        actions_frame.grid_columnconfigure(list(range(5)), weight=1)
        
        main_actions_frame = ctk.CTkFrame(actions_frame)
        main_actions_frame.grid(row=0, column=0, columnspan=2, padx=2, sticky="nsew")
        main_actions_frame.grid_columnconfigure((0,1), weight=1)
        self.init_button = ctk.CTkButton(main_actions_frame, text="Инициализировать", height=40, command=self.initialize_translator_gui)
        self.init_button.grid(row=0, column=0, padx=2, pady=2, sticky="ew")
        self.start_button = ctk.CTkButton(main_actions_frame, text="▶ Перевести", height=40, command=self.start_translation, state=ctk.DISABLED, fg_color="#2E7D32", hover_color="#1B5E20")
        self.start_button.grid(row=0, column=1, padx=2, pady=2, sticky="ew")

        kb_actions_frame = ctk.CTkFrame(actions_frame)
        kb_actions_frame.grid(row=0, column=2, columnspan=2, padx=2, sticky="nsew")
        kb_actions_frame.grid_columnconfigure((0,1,2), weight=1)
        self.dict_button = ctk.CTkButton(kb_actions_frame, text="Словарь", height=40, command=self.open_dictionary_editor, state=ctk.DISABLED)
        self.dict_button.grid(row=0, column=0, padx=2, pady=2, sticky="ew")
        self.knowledge_button = ctk.CTkButton(kb_actions_frame, text="Аудит БЗ", height=40, command=self.open_knowledge_auditor, state=ctk.DISABLED, fg_color="#0277BD", hover_color="#01579B")
        self.knowledge_button.grid(row=0, column=1, padx=2, pady=2, sticky="ew")
        
        self.build_index_button = ctk.CTkButton(kb_actions_frame, text="🧠 Построить Индекс", height=40, command=self.build_semantic_index_gui, state=ctk.DISABLED, fg_color="#6A1B9A", hover_color="#4A148C")
        self.build_index_button.grid(row=0, column=2, padx=2, pady=2, sticky="ew")
        self.index_status_label = ctk.CTkLabel(kb_actions_frame, text="Индекс: Н/Д", font=ctk.CTkFont(size=11))
        self.index_status_label.grid(row=1, column=0, columnspan=3, padx=2, pady=(0,2), sticky="ew")

        editor_actions_frame = ctk.CTkFrame(actions_frame)
        editor_actions_frame.grid(row=0, column=4, columnspan=3, padx=2, sticky="nsew")
        editor_actions_frame.grid_columnconfigure((0,1,2), weight=1)
        self.editor_button = ctk.CTkButton(editor_actions_frame, text="Редактор", height=40, command=self.open_interactive_editor, state=ctk.DISABLED)
        self.editor_button.grid(row=0, column=0, padx=2, pady=2, sticky="ew")
        self.consistency_button = ctk.CTkButton(editor_actions_frame, text="Аудит Перевода", height=40, command=self.open_consistency_checker, state=ctk.DISABLED)
        self.consistency_button.grid(row=0, column=1, padx=2, pady=2, sticky="ew")
        self.master_editor_button = ctk.CTkButton(editor_actions_frame, text="Мастер-Редактор", height=40, command=self.open_master_editor, state=ctk.DISABLED)
        self.master_editor_button.grid(row=0, column=2, padx=2, pady=2, sticky="ew")

    def create_main_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        paths = { "API ключ Gemini:": 'api_key', "Директория с главами (Проект):": 'input_dir', "Директория для перевода:": 'output_dir' }
        for i, (label, key) in enumerate(paths.items()):
            ctk.CTkLabel(tab, text=label).grid(row=i, column=0, padx=10, pady=10, sticky="w")
            entry = ctk.CTkEntry(tab, textvariable=self.settings_vars[key], width=350)
            entry.grid(row=i, column=1, padx=5, pady=10, sticky="ew")
            if key == "api_key":
                entry.configure(show="*")
                self.test_api_button = ctk.CTkButton(tab, text="Тест", width=60, command=self.test_api)
                self.test_api_button.grid(row=i, column=2, padx=(5, 10))
            else:
                ctk.CTkButton(tab, text="Обзор...", width=80, command=lambda v=self.settings_vars[key]: self.browse_directory(v)).grid(row=i, column=2, padx=(5, 10))

    def test_api(self):
        api_key = self.settings_vars['api_key'].get()
        if not api_key:
            self.show_message("Ошибка", "Пожалуйста, введите API ключ для проверки.")
            return

        self.test_api_button.configure(state="disabled", text="...")
        threading.Thread(target=self._test_api_thread, args=(api_key,), daemon=True).start()

    def _test_api_thread(self, api_key):
        try:
            genai.configure(api_key=api_key)
            model_name_to_test = self.settings_vars['model_name'].get() # Получаем модель из GUI
            model = genai.GenerativeModel(model_name_to_test)
            model.count_tokens("test")
            self.after(0, self.show_message, "Успех", "API ключ действителен и модель доступна.")
        except Exception as e:
            gui_logger.error(f"Ошибка проверки API ключа: {e}")
            self.after(0, self.show_message, "Ошибка API", f"Не удалось проверить ключ: {e}")
        finally:
            self.after(0, self.test_api_button.configure, {"state": "normal", "text": "Тест"})

    def _update_slider_label(self, value, label_var):
        label_var.set(f"{value:.2f}")

    def create_settings_tab(self, tab):
        tab.grid_columnconfigure((0, 1), weight=1)
        model_frame = ctk.CTkFrame(tab, border_width=1)
        model_frame.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        model_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(model_frame, text="Параметры Модели", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, columnspan=3, pady=(5,10), padx=10)
        ctk.CTkLabel(model_frame, text="Модель:").grid(row=1, column=0, padx=10, pady=5, sticky="w")
        ctk.CTkComboBox(model_frame, variable=self.settings_vars['model_name'], values=AVAILABLE_MODELS).grid(row=1, column=1, columnspan=2, padx=10, pady=5, sticky="ew")
        ctk.CTkLabel(model_frame, text="Температура:").grid(row=2, column=0, padx=10, pady=5, sticky="w")
        ctk.CTkSlider(model_frame, from_=0.0, to=1.5, variable=self.settings_vars['temperature'], command=lambda v: self._update_slider_label(v, self.temp_label_var)).grid(row=2, column=1, padx=10, pady=5, sticky="ew")
        ctk.CTkLabel(model_frame, textvariable=self.temp_label_var, width=40).grid(row=2, column=2, padx=10, pady=5)
        ctk.CTkLabel(model_frame, text="Top-P:").grid(row=3, column=0, padx=10, pady=5, sticky="w")
        ctk.CTkSlider(model_frame, from_=0.0, to=1.0, variable=self.settings_vars['top_p'], command=lambda v: self._update_slider_label(v, self.top_p_label_var)).grid(row=3, column=1, padx=10, pady=5, sticky="ew")
        ctk.CTkLabel(model_frame, textvariable=self.top_p_label_var, width=40).grid(row=3, column=2, padx=10, pady=5)
        
        steps_frame = ctk.CTkFrame(tab, border_width=1)
        steps_frame.grid(row=1, column=0, padx=10, pady=10, sticky="nsew")
        ctk.CTkLabel(steps_frame, text="Этапы Обработки", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10, pady=(5,10))
        ctk.CTkCheckBox(steps_frame, text="Анализ жанра и стиля", variable=self.settings_vars['enable_genre_analysis']).pack(anchor="w", padx=20, pady=5)
        ctk.CTkCheckBox(steps_frame, text="Литературная обработка", variable=self.settings_vars['enable_literary_step']).pack(anchor="w", padx=20, pady=5)
        ctk.CTkCheckBox(steps_frame, text="Грамматический контроль терминов", variable=self.settings_vars['enable_grammar_guardian']).pack(anchor="w", padx=20, pady=5)
        ctk.CTkCheckBox(steps_frame, text="Умное пополнение словаря (NER)", variable=self.settings_vars['enable_smart_dictionary_update']).pack(anchor="w", padx=20, pady=5)
        ctk.CTkCheckBox(steps_frame, text="Авто-анализ для Библии Вселенной", variable=self.settings_vars['enable_bible_analysis']).pack(anchor="w", padx=20, pady=5)

        perf_frame = ctk.CTkFrame(tab, border_width=1)
        perf_frame.grid(row=0, column=1, rowspan=2, padx=10, pady=10, sticky="nsew")
        perf_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(perf_frame, text="Процесс и Производительность", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, columnspan=2, pady=(5,10), padx=10)
        
        ctk.CTkCheckBox(perf_frame, text="Перезаписывать существующие переводы", variable=self.settings_vars['overwrite_existing']).grid(row=1, column=0, columnspan=2, padx=20, pady=5, sticky="w")
        ctk.CTkCheckBox(perf_frame, text="Параллельный перевод глав", variable=self.settings_vars['parallel_translation']).grid(row=2, column=0, columnspan=2, padx=20, pady=5, sticky="w")

        ctk.CTkLabel(perf_frame, text="Количество потоков:").grid(row=3, column=0, padx=20, pady=5, sticky="w")
        worker_counts = [str(i) for i in range(1, (os.cpu_count() or 1) * 2 + 1)]
        ctk.CTkOptionMenu(perf_frame, variable=self.settings_vars['max_workers'], values=worker_counts).grid(row=3, column=1, padx=10, pady=5, sticky="w")

    def _get_project_data_folder(self, project_name):
        if not project_name or project_name == "Default":
            return None
        script_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(script_dir, "_project_files", project_name)

    def _migrate_legacy_files(self, project_name, input_dir):
        if not project_name or not input_dir or project_name == "Default":
            return
        
        legacy_dir = os.path.join(input_dir, "_project_files")
        if not os.path.isdir(legacy_dir):
            return

        new_project_dir = self._get_project_data_folder(project_name)
        if not new_project_dir:
            return

        gui_logger.info(f"Обнаружена старая папка данных в '{legacy_dir}'. Начало миграции в '{new_project_dir}'...")
        os.makedirs(new_project_dir, exist_ok=True)
        
        try:
            for item in os.listdir(legacy_dir):
                s = os.path.join(legacy_dir, item)
                d = os.path.join(new_project_dir, item)
                if os.path.isdir(s):
                    shutil.copytree(s, d, dirs_exist_ok=True)
                else:
                    shutil.copy2(s, d)
            
            shutil.rmtree(legacy_dir)
            gui_logger.info(f"Миграция для проекта '{project_name}' успешно завершена.")
            self.show_message("Миграция", f"Файлы проекта '{project_name}' были успешно перенесены в новую централизованную структуру.")
        except Exception as e:
            gui_logger.error(f"Ошибка при миграции файлов для проекта '{project_name}': {e}", exc_info=True)
            self.show_message("Ошибка миграции", f"Не удалось переместить файлы проекта: {e}")

    def _get_project_specific_settings(self):
        settings = self.collect_settings_from_gui()
        return settings

    def open_project_data_folder(self):
        project_name = self.settings_vars['project_name'].get()
        if not project_name or project_name == "Default":
            self.show_message("Информация", "Выберите проект, чтобы открыть его папку данных.")
            return

        project_dir = self._get_project_data_folder(project_name)
        if project_dir:
            os.makedirs(project_dir, exist_ok=True)
            try:
                os.startfile(project_dir)
            except AttributeError:
                import subprocess
                if sys.platform == "darwin":
                    subprocess.Popen(["open", project_dir])
                else:
                    subprocess.Popen(["xdg-open", project_dir])
        else:
            self.show_message("Ошибка", "Не удалось определить путь к папке проекта.")

    def initialize_translator_gui(self):
        current_settings = self.collect_settings_from_gui()
        if not current_settings.get('api_key'):
            self.show_message("Ошибка", "API ключ не может быть пустым."); return
        if not current_settings.get('project_name') or current_settings.get('project_name') == "Default":
            self.show_message("Ошибка", "Пожалуйста, выберите или создайте проект перед инициализацией.")
            return

        self._migrate_legacy_files(current_settings.get('project_name'), current_settings.get('input_dir'))
        
        self.update_status("Инициализация переводчика...", 0.3)
        self._set_controls_state(ctk.DISABLED)
        
        final_settings = self._get_project_specific_settings()
        threading.Thread(target=self._initialize_thread, args=(final_settings,), daemon=True).start()

    def create_world_bible_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        self.bible_tab_view = ctk.CTkTabview(tab, height=1)
        self.bible_tab_view.grid(row=0, column=0, padx=5, pady=5, sticky="nsew")
        self._create_bible_entries_sub_tab(self.bible_tab_view.add("Записи"))
        self._create_bible_proposals_sub_tab(self.bible_tab_view.add("На утверждение"))
        
    def _create_bible_entries_sub_tab(self, sub_tab):
        sub_tab.grid_columnconfigure(0, weight=1)
        sub_tab.grid_rowconfigure(0, weight=1)
        self.bible_scroll_frame = ctk.CTkScrollableFrame(sub_tab, label_text="Записи о мире")
        self.bible_scroll_frame.grid(row=0, column=0, padx=5, pady=5, sticky="nsew")
        self.bible_scroll_frame.grid_columnconfigure(0, weight=1)
        
        add_entry_frame = ctk.CTkFrame(sub_tab)
        add_entry_frame.grid(row=1, column=0, padx=5, pady=5, sticky="ew")
        add_entry_frame.grid_columnconfigure((1, 3), weight=1)
        
        ctk.CTkLabel(add_entry_frame, text="Название (Eng):").grid(row=0, column=0, padx=10, pady=(10, 5), sticky="w")
        self.bible_name_var = ctk.StringVar()
        ctk.CTkEntry(add_entry_frame, textvariable=self.bible_name_var).grid(row=0, column=1, padx=10, pady=(10, 5), sticky="ew")
        
        ctk.CTkLabel(add_entry_frame, text="Название (Rus):").grid(row=0, column=2, padx=10, pady=(10, 5), sticky="w")
        self.bible_rus_name_var = ctk.StringVar()
        ctk.CTkEntry(add_entry_frame, textvariable=self.bible_rus_name_var).grid(row=0, column=3, padx=10, pady=(10, 5), sticky="ew")

        ctk.CTkLabel(add_entry_frame, text="Категория:").grid(row=1, column=0, padx=10, pady=5, sticky="w")
        self.bible_category_var = ctk.StringVar(value="character")
        ctk.CTkOptionMenu(add_entry_frame, variable=self.bible_category_var, values=["character", "location", "item", "concept", "organization", "other"]).grid(row=1, column=1, padx=10, pady=5, sticky="w")
        
        ctk.CTkLabel(add_entry_frame, text="Описание (Rus):").grid(row=2, column=0, padx=10, pady=5, sticky="w")
        self.bible_desc_textbox = ctk.CTkTextbox(add_entry_frame, height=60, wrap=ctk.WORD)
        self.bible_desc_textbox.grid(row=3, column=0, columnspan=4, padx=10, pady=(0, 10), sticky="ew")
        
        self.bible_add_button = ctk.CTkButton(add_entry_frame, text="Добавить/\nОбновить", command=self.add_or_update_bible_entry)
        self.bible_add_button.grid(row=0, column=4, rowspan=4, padx=10, pady=10, sticky="ns")

    def _create_bible_proposals_sub_tab(self, sub_tab):
        sub_tab.grid_columnconfigure(0, weight=1)
        sub_tab.grid_rowconfigure(0, weight=1)
        self.bible_proposals_frame = ctk.CTkScrollableFrame(sub_tab, label_text="Предложения от ИИ")
        self.bible_proposals_frame.grid(row=0, column=0, padx=5, pady=5, sticky="nsew")
        self.bible_proposals_frame.grid_columnconfigure(0, weight=1)
        
        btn_frame = ctk.CTkFrame(sub_tab)
        btn_frame.grid(row=1, column=0, padx=5, pady=5, sticky="ew")
        btn_frame.grid_columnconfigure((0,1), weight=1)
        ctk.CTkButton(btn_frame, text="✓ Принять все", fg_color="#2E7D32", hover_color="#1B5E20", command=self.approve_all_bible_proposals).grid(row=0, column=0, padx=5, pady=5, sticky="ew")
        ctk.CTkButton(btn_frame, text="✗ Отклонить все", fg_color="#D32F2F", hover_color="#B71C1C", command=self.reject_all_bible_proposals).grid(row=0, column=1, padx=5, pady=5, sticky="ew")

    def populate_world_bible_view(self):
        for widget in self.bible_scroll_frame.winfo_children():
            widget.destroy()
        if not self.translator:
            return
        
        bible_copy = self.translator.db_manager.get_world_bible()
        
        if not bible_copy:
            return

        for name, data in sorted(bible_copy.items(), key=lambda item: (item[1].get('category', 'z'), item[0])):
            entry_frame = ctk.CTkFrame(self.bible_scroll_frame, border_width=1)
            entry_frame.grid(padx=5, pady=5, sticky="ew")
            entry_frame.grid_columnconfigure(0, weight=1)
            header_frame = ctk.CTkFrame(entry_frame, fg_color="transparent")
            header_frame.grid(row=0, column=0, sticky="ew")
            header_frame.grid_columnconfigure(0, weight=1)
            
            rus_name = data.get('russian_name', '')
            label_text = f"{name} / {rus_name}" if rus_name else name
            ctk.CTkLabel(header_frame, text=label_text, font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=10, pady=(5,0), sticky="w")

            ctk.CTkLabel(header_frame, text=f"[{data.get('category', 'N/A')}]", text_color="gray").grid(row=0, column=1, padx=10, pady=(5,0), sticky="e")
            ctk.CTkLabel(entry_frame, text=data.get('description', ''), wraplength=700, justify="left").grid(row=1, column=0, padx=10, pady=(0,5), sticky="w")
            btn_container = ctk.CTkFrame(header_frame, fg_color="transparent")
            btn_container.grid(row=0, column=2, padx=10, pady=(5,0))
            ctk.CTkButton(btn_container, text="✎", width=20, height=20, command=lambda d=data, n=name: self.edit_bible_entry_from_list(n, d)).pack(side=ctk.LEFT, padx=(0,5))
            ctk.CTkButton(btn_container, text="X", width=20, height=20, fg_color="#D32F2F", hover_color="#B71C1C", command=lambda n=name: self.delete_bible_entry(n)).pack(side=ctk.LEFT)

    def populate_bible_proposals_view(self):
        for widget in self.bible_proposals_frame.winfo_children():
            widget.destroy()
        self.update_bible_proposal_tab_title()
        if not self.translator:
            return
        
        proposals_copy = self.translator.db_manager.get_world_bible_proposals()
        
        if not proposals_copy:
            return

        for name, data in sorted(proposals_copy.items()):
            entry_frame = ctk.CTkFrame(self.bible_proposals_frame, border_width=1)
            entry_frame.grid(padx=5, pady=5, sticky="ew")
            entry_frame.grid_columnconfigure(1, weight=1)
            btn_frame = ctk.CTkFrame(entry_frame, fg_color="transparent")
            btn_frame.grid(row=0, column=0, rowspan=2, padx=5, pady=5, sticky="ns")
            ctk.CTkButton(btn_frame, text="✓", width=28, fg_color="#2E7D32", hover_color="#1B5E20", command=lambda n=name: self.approve_bible_proposal(n)).pack(pady=2)
            ctk.CTkButton(btn_frame, text="✗", width=28, fg_color="#D32F2F", hover_color="#B71C1C", command=lambda n=name: self.reject_bible_proposal(n)).pack(pady=2)
            
            rus_name = data.get('russian_name', '')
            label_text = f"{name} / {rus_name}  [{data.get('category', 'N/A')}]"
            ctk.CTkLabel(entry_frame, text=label_text, font=ctk.CTkFont(weight="bold")).grid(row=0, column=1, padx=5, sticky="w")
            
            ctk.CTkLabel(entry_frame, text=data.get('description', ''), wraplength=650, justify="left").grid(row=1, column=1, padx=5, pady=(0,5), sticky="w")
    
    def approve_all_bible_proposals(self):
        if not self.translator:
            return
        proposals = self.translator.db_manager.get_world_bible_proposals()
        if not proposals:
            return
        if messagebox.askyesno("Одобрить все", f"Вы уверены, что хотите одобрить все {len(proposals)} предложений?"):
            for name, data in proposals.items():
                self.translator.db_manager.add_or_update_bible_entry(name, data)
            self.translator.db_manager.clear_world_bible_proposals()
            self.populate_world_bible_view()
            self.populate_bible_proposals_view()
            self.update_index_status()

    def reject_all_bible_proposals(self):
        if not self.translator:
            return
        count = self.translator.db_manager.count_world_bible_proposals()
        if count == 0:
            return
        if messagebox.askyesno("Отклонить все", f"Вы уверены, что хотите отклонить все {count} предложений?"):
            self.translator.db_manager.clear_world_bible_proposals()
            self.populate_bible_proposals_view()
            self.update_index_status()

    def add_or_update_bible_entry(self):
        if not self.translator:
            return
        name = self.bible_name_var.get().strip()
        rus_name = self.bible_rus_name_var.get().strip()
        description = self.bible_desc_textbox.get("1.0", "end-1c").strip()
        if not name or not description:
            self.show_message("Ошибка", "Английское название и описание не могут быть пустыми.")
            return
        
        data = {
            "russian_name": rus_name,
            "category": self.bible_category_var.get(),
            "description": description
        }
        self.translator.db_manager.add_or_update_bible_entry(name, data)
        
        self.populate_world_bible_view()
        self.bible_name_var.set("")
        self.bible_rus_name_var.set("")
        self.bible_desc_textbox.delete("1.0", "end")
        gui_logger.info(f"Запись '{name}' добавлена/обновлена в Библии Вселенной.")
        self.update_index_status()

    def edit_bible_entry_from_list(self, name, data):
        self.bible_name_var.set(name)
        self.bible_rus_name_var.set(data.get("russian_name", ""))
        self.bible_category_var.set(data.get("category", "character"))
        self.bible_desc_textbox.delete("1.0", "end")
        self.bible_desc_textbox.insert("1.0", data.get("description", ""))
        self.tab_view.set("Библия Вселенной")
        self.bible_tab_view.set("Записи")

    def _initialize_thread(self, settings):
        try:
            self.translator = NovelTranslator(settings=settings)
            self.after(0, self.update_status, "Переводчик готов к работе", 1.0)
            self.after(0, self._set_controls_state, ctk.NORMAL)
            self.after(0, self.update_dictionary_button)
            self.after(0, self.populate_world_bible_view)
            self.after(0, self.populate_bible_proposals_view)
            self.after(0, self.update_index_status)
        except Exception as e:
            self.translator = None
            self.after(0, self.show_message, "Ошибка инициализации", f"Ошибка: {e}")
            self.after(0, self.update_status, "Ошибка инициализации!", 0.0)
            self.after(0, self.init_button.configure, {"state": ctk.NORMAL})

    def _set_controls_state(self, state):
        bible_state = state if self.translator else ctk.DISABLED
        if hasattr(self, 'bible_add_button'):
            self.bible_add_button.configure(state=bible_state)
        buttons = [self.init_button, self.start_button, self.dict_button, self.editor_button, self.consistency_button, self.master_editor_button, self.knowledge_button, self.build_index_button]
        for btn in buttons:
            if btn and btn.winfo_exists():
                if btn in [self.editor_button, self.consistency_button, self.master_editor_button]:
                    btn.configure(state=state if self.last_processed_texts else ctk.DISABLED)
                elif btn in [self.knowledge_button, self.build_index_button]:
                     btn.configure(state=state if self.translator else ctk.DISABLED)
                else:
                    btn.configure(state=state)

    def start_translation(self):
        if self.translation_running:
            return
        if not self.translator or not self.translator.model:
            self.show_message("Ошибка", "Переводчик не инициализирован.")
            return
        if self.translator.kb_manager.collection.count() == 0:
             if not messagebox.askyesno("Внимание", "Семантический индекс не построен. Перевод будет менее точным и медленным. \n\nВы хотите сначала построить индекс?"):
                 pass
             else:
                 self.build_semantic_index_gui()
                 return
        settings = self._get_project_specific_settings()
        if not all(settings.get(k) for k in ['input_dir', 'output_dir']):
            self.show_message("Ошибка", "Укажите директории.")
            return
        self.translation_running = True
        self.last_processed_texts = []
        self._set_controls_state(ctk.DISABLED)
        threading.Thread(target=self._translation_thread, args=(settings,), daemon=True).start()
    
    def open_knowledge_auditor(self):
        if not self.translator:
            self.show_message("Ошибка", "Сначала инициализируйте переводчик.")
            return
        if self.knowledge_auditor_window and self.knowledge_auditor_window.winfo_exists():
            self.knowledge_auditor_window.lift()
        else:
            self.knowledge_auditor_window = KnowledgeAuditorWindow(self, self.translator)

    def approve_bible_proposal(self, name):
        if not self.translator:
            return
        proposal = self.translator.db_manager.get_world_bible_proposal(name)
        if proposal:
            self.translator.db_manager.add_or_update_bible_entry(name, proposal)
            self.translator.db_manager.delete_world_bible_proposal(name)
            self.populate_world_bible_view()
            self.populate_bible_proposals_view()
            self.update_index_status()

    def reject_bible_proposal(self, name):
        if not self.translator:
            return
        self.translator.db_manager.delete_world_bible_proposal(name)
        self.populate_bible_proposals_view()
        self.update_index_status()
    
    def delete_bible_entry(self, name):
        if not self.translator:
            return
        if messagebox.askyesno("Удаление", f"Вы уверены, что хотите удалить запись '{name}'?"):
            self.translator.db_manager.delete_bible_entry(name)
            self.populate_world_bible_view()
            gui_logger.info(f"Запись '{name}' удалена.")
            self.update_index_status()

    def update_bible_proposal_tab_title(self):
        if not self.translator:
            return
        
        count = self.translator.db_manager.count_world_bible_proposals()

        title = f"На утверждение ({count})" if count > 0 else "На утверждение"
        try:
            self.bible_tab_view.tab("На утверждение").configure(text=title)
        except Exception:
            pass

    def build_semantic_index_gui(self):
        if not self.translator:
            self.show_message("Ошибка", "Сначала инициализируйте переводчик.")
            return
        
        self._set_controls_state(ctk.DISABLED)
        self.update_status("Запуск построения семантического индекса...", 0)
        threading.Thread(target=self._build_index_thread, daemon=True).start()

    def _build_index_thread(self):
        try:
            success = self.translator.build_semantic_index(
                lambda p, s: self.after(0, self.update_status, s, p / 100.0)
            )
            if success:
                self.after(0, self.show_message, "Успех", "Семантический индекс успешно построен/обновлен!")
        except Exception as e:
            gui_logger.error(f"Ошибка при построении индекса: {e}", exc_info=True)
            self.after(0, self.show_message, "Ошибка", f"Не удалось построить индекс: {e}")
        finally:
            self.after(0, self._finalize_index_build)
            
    def _finalize_index_build(self):
        self._set_controls_state(ctk.NORMAL)
        self.update_status("Готов к работе.", 1.0)
        self.update_index_status()

    def on_dictionary_close(self):
        self.update_dictionary_button()
        self.update_index_status()

    def update_index_status(self):
        if not self.translator:
            self.index_status_label.configure(text="Индекс: Н/Д", text_color="gray")
            return
        
        index_size = self.translator.kb_manager.collection.count()
        source_size = self.translator.db_manager.count_terms() + self.translator.db_manager.count_world_bible_entries()

        if index_size == 0 and source_size > 0:
            self.index_status_label.configure(text="Индекс: не построен", text_color="#E57373")
        elif index_size < source_size:
            self.index_status_label.configure(text=f"Индекс: устарел ({index_size}/{source_size})", text_color="#FFB74D")
        elif source_size == 0:
             self.index_status_label.configure(text="Индекс: нет данных для построения", text_color="gray")
        else:
            self.index_status_label.configure(text=f"Индекс: актуален ({index_size})", text_color="#81C784")

    def update_project_list(self):
        project_names = self.project_manager.get_project_names()
        self.project_combo.configure(values=project_names or ["Default"])
        current_project = self.settings_vars['project_name'].get()
        if current_project not in project_names:
            self.settings_vars['project_name'].set(project_names[0] if project_names else "Default")

    def load_initial_settings(self):
        self.update_project_list()
        project_name_from_cli = self.cli_args.project if self.cli_args else None
        
        all_projects = self.project_manager.get_project_names()
        
        if project_name_from_cli and project_name_from_cli in all_projects:
            self.settings_vars['project_name'].set(project_name_from_cli)
        elif all_projects:
            self.settings_vars['project_name'].set(all_projects[0])
        else:
            self.settings_vars['project_name'].set("Default")
        
        self.load_selected_project()

    def apply_settings_to_gui(self, settings):
        gui_logger.debug(f"Применение настроек для проекта '{settings.get('project_name', 'N/A')}'")
        for key, var in self.settings_vars.items():
            if key in settings and (value := settings.get(key)) is not None:
                try:
                    var.set(value)
                except Exception as e:
                    gui_logger.warning(f"Не удалось установить значение для '{key}': {value} ({e})")
        self.temp_label_var.set(f"{self.settings_vars['temperature'].get():.2f}")
        self.top_p_label_var.set(f"{self.settings_vars['top_p'].get():.2f}")
    
    def collect_settings_from_gui(self):
        return {key: var.get() for key, var in self.settings_vars.items()}

    def load_selected_project(self, selected_name=None):
        project_name = selected_name or self.settings_vars['project_name'].get()
        if project_name:
            gui_logger.info(f"Загрузка проекта: {project_name}")
            project_settings = self.project_manager.get_project_settings(project_name)
            
            current_input_dir = self.settings_vars['input_dir'].get()
            current_output_dir = self.settings_vars['output_dir'].get()
            
            self.apply_settings_to_gui(project_settings)

            if not self.settings_vars['input_dir'].get() and current_input_dir:
                self.settings_vars['input_dir'].set(current_input_dir)
            if not self.settings_vars['output_dir'].get() and current_output_dir:
                self.settings_vars['output_dir'].set(current_output_dir)

            if project_name != "Default":
                self.initialize_translator_gui()
            else:
                self.translator = None
                self._set_controls_state(ctk.DISABLED)
                self.init_button.configure(state=ctk.NORMAL)
                self.update_status("Выберите или создайте проект для начала работы.")
                self.update_index_status()
                self.populate_world_bible_view()
                self.populate_bible_proposals_view()

    def save_project(self):
        dialog = ctk.CTkInputDialog(text="Введите имя проекта:", title="Сохранить проект")
        project_name = dialog.get_input()
        if project_name and project_name.strip() and project_name.strip() != "Default":
            project_name = project_name.strip()
            current_settings = self.collect_settings_from_gui()
            current_settings['project_name'] = project_name
            if self.project_manager.add_or_update_project(project_name, current_settings):
                self.update_project_list()
                self.settings_vars['project_name'].set(project_name)
                self.show_message("Успех", f"Проект '{project_name}' сохранен.")
                self.load_selected_project(project_name)

    def delete_project(self):
        project_name = self.settings_vars['project_name'].get()
        if project_name and project_name != "Default":
            project_dir = self._get_project_data_folder(project_name)
            msg = (f"Вы уверены, что хотите удалить проект '{project_name}'?\n\n"
                   f"Это действие также удалит всю папку с его данными:\n{project_dir}")
            if messagebox.askyesno("Удаление проекта", msg):
                if self.project_manager.delete_project(project_name):
                    try:
                        if project_dir and os.path.isdir(project_dir):
                            shutil.rmtree(project_dir)
                            gui_logger.info(f"Папка данных проекта '{project_dir}' удалена.")
                    except Exception as e:
                        gui_logger.error(f"Ошибка при удалении папки данных проекта: {e}")
                        self.show_message("Ошибка", f"Не удалось удалить папку данных: {e}")
                    
                    self.update_project_list()
                    self.load_initial_settings()
                    self.show_message("Успех", f"Проект '{project_name}' удален.")

    def browse_directory(self, var):
        initial_dir = var.get() or os.getcwd()
        directory = filedialog.askdirectory(initialdir=initial_dir)
        if directory:
            var.set(directory)

    def _translation_thread(self, settings):
        start_time = time.monotonic()
        try:
            self.translator.settings = settings
            self.translator._apply_settings_to_attributes()
            method = self.translator.process_novel_parallel if settings.get('parallel_translation') else self.translator.process_novel
            success, processed_texts = method(settings['input_dir'], settings['output_dir'], lambda p, s: self.update_status(s, p / 100.0))
            self.last_processed_texts = processed_texts
            duration = time.monotonic() - start_time
            msg = f"Перевод {'успешно завершен' if success else 'завершен с ошибками'} за {duration:.2f} сек."
            self.after(0, self.show_message, "Завершено", msg)
        except Exception as e:
            gui_logger.error(f"Критическая ошибка перевода: {e}", exc_info=True)
            self.after(0, self.show_message, "Критическая ошибка", f"Ошибка: {e}")
        finally:
            self.translation_running = False
            self.after(0, self._finalize_translation_gui)

    def _finalize_translation_gui(self):
        self.init_button.configure(state=ctk.NORMAL)
        state = ctk.NORMAL if self.translator and self.translator.model else ctk.DISABLED
        buttons = [self.start_button, self.dict_button, self.knowledge_button, self.build_index_button]
        for btn in buttons:
            if btn and btn.winfo_exists():
                btn.configure(state=state)
        buttons_after_translation = [self.editor_button, self.consistency_button, self.master_editor_button]
        for btn in buttons_after_translation:
            if btn and btn.winfo_exists():
                btn.configure(state=state if self.last_processed_texts else ctk.DISABLED)
        self.update_status("Готов к работе.", 1.0)
        self.update_dictionary_button()
        self.populate_bible_proposals_view()
        self.update_index_status()

    def open_dictionary_editor(self):
        if not self.translator:
            self.show_message("Ошибка", "Сначала инициализируйте переводчик.")
            return
        if self.dictionary_editor_window and self.dictionary_editor_window.winfo_exists():
            self.dictionary_editor_window.lift()
        else:
            self.dictionary_editor_window = DictionaryEditor(self, self.translator)

    def open_interactive_editor(self):
        if not self.translator:
            self.show_message("Ошибка", "Сначала инициализируйте переводчик.")
            return
        if not self.last_processed_texts:
            self.show_message("Информация", "Нет данных для редактирования. Сначала выполните перевод.")
            return
        if self.interactive_editor_window and self.interactive_editor_window.winfo_exists():
            self.interactive_editor_window.lift()
        else:
            self.interactive_editor_window = InteractiveEditorWindow(self, self.translator, self.last_processed_texts)
    
    def open_consistency_checker(self):
        if not self.translator:
            self.show_message("Ошибка", "Сначала инициализируйте переводчик.")
            return
        if not self.last_processed_texts:
            self.show_message("Информация", "Нет данных для проверки. Сначала выполните перевод.")
            return
        if self.consistency_checker_window and self.consistency_checker_window.winfo_exists():
            self.consistency_checker_window.lift()
        else:
            self.consistency_checker_window = ConsistencyCheckerWindow(self, self.translator, self.last_processed_texts)

    def open_master_editor(self):
        if not self.translator:
            self.show_message("Ошибка", "Сначала инициализируйте переводчик.")
            return
        if not self.last_processed_texts:
            self.show_message("Информация", "Нет данных для редактирования. Сначала выполните перевод.")
            return
        if self.master_editor_window and self.master_editor_window.winfo_exists():
            self.master_editor_window.lift()
        else:
            self.master_editor_window = MasterEditorWindow(self, self.translator, self.last_processed_texts)

    def show_message(self, title, message):
        if "Ошибка" in title:
            messagebox.showerror(title, message)
        else:
            messagebox.showinfo(title, message)

    def update_status(self, message, progress_value=None):
        if self.winfo_exists():
            self.status_var.set(str(message))
            if progress_value is not None:
                self.progress_bar.set(float(progress_value))
    
    def update_dictionary_button(self):
        if not self.translator:
            return
        
        num_proposals = self.translator.db_manager.count_dictionary_proposals()

        text = f"Словарь ({num_proposals})" if num_proposals > 0 else "Словарь"
        color = ("#FFA000", "#FF8F00") if num_proposals > 0 else (ctk.ThemeManager.theme["CTkButton"]["fg_color"], ctk.ThemeManager.theme["CTkButton"]["hover_color"])
        self.dict_button.configure(text=text, fg_color=color[0], hover_color=color[1])

class DictionaryEditor(ctk.CTkToplevel):
    def __init__(self, parent, translator):
        super().__init__(parent)
        self.main_app, self.translator = parent, translator
        self.title("Редактор и Утверждение Словаря")
        self.geometry("1000x700")
        self.transient(parent)
        self.grab_set()
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.tab_view = ctk.CTkTabview(self)
        self.tab_view.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        self.create_main_dictionary_tab(self.tab_view.add("Основной словарь"))
        self.create_proposals_tab(self.tab_view.add("На утверждение"))
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.update_proposal_tab_title()

    def create_main_dictionary_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)
        top_frame = ctk.CTkFrame(tab)
        top_frame.grid(row=0, column=0, padx=10, pady=10, sticky="ew")
        top_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(top_frame, text="Поиск:").grid(row=0, column=0, padx=10, pady=10)
        self.search_var = ctk.StringVar()
        self.search_var.trace_add("write", lambda n, i, m: self.populate_main_list())
        ctk.CTkEntry(top_frame, textvariable=self.search_var).grid(row=0, column=1, sticky="ew")
        self.main_textbox = ctk.CTkTextbox(tab, font=("Consolas", 13), wrap="none")
        self.main_textbox.grid(row=1, column=0, padx=10, pady=5, sticky="nsew")
        add_frame = ctk.CTkFrame(tab)
        add_frame.grid(row=2, column=0, padx=10, pady=5, sticky="ew")
        add_frame.grid_columnconfigure((0, 1), weight=1)
        self.eng_var, self.rus_var = ctk.StringVar(), ctk.StringVar()
        ctk.CTkEntry(add_frame, textvariable=self.eng_var, placeholder_text="English Term").grid(row=0, column=0, padx=5, pady=5, sticky="ew")
        ctk.CTkEntry(add_frame, textvariable=self.rus_var, placeholder_text="Русский перевод").grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        ctk.CTkButton(add_frame, text="Добавить", command=self.add_term).grid(row=0, column=2, padx=5, pady=5)
        ctk.CTkButton(tab, text="Удалить выделенный термин", fg_color="#D32F2F", hover_color="#B71C1C", command=self.delete_term).grid(row=3, column=0, padx=10, pady=10, sticky="ew")
        self.populate_main_list()

    def create_proposals_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        self.proposals_textbox = ctk.CTkTextbox(tab, font=("Consolas", 13), wrap="none")
        self.proposals_textbox.grid(row=0, column=0, padx=10, pady=5, sticky="nsew")
        
        multi_button_frame = ctk.CTkFrame(tab)
        multi_button_frame.grid(row=1, column=0, padx=10, pady=5, sticky="ew")
        multi_button_frame.grid_columnconfigure((0,1), weight=1)
        ctk.CTkButton(multi_button_frame, text="✓ Принять все", fg_color="#2E7D32", hover_color="#1B5E20", command=self.approve_all_terms).grid(row=0, column=0, padx=5, sticky="ew")
        ctk.CTkButton(multi_button_frame, text="✗ Отклонить все", fg_color="#D32F2F", hover_color="#B71C1C", command=self.reject_all_terms).grid(row=0, column=1, padx=5, sticky="ew")

        single_button_frame = ctk.CTkFrame(tab)
        single_button_frame.grid(row=2, column=0, padx=10, pady=(0, 10), sticky="ew")
        single_button_frame.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkButton(single_button_frame, text="Одобрить выделенное", command=self.approve_term).grid(row=0, column=0, padx=5, pady=5, sticky="ew")
        ctk.CTkButton(single_button_frame, text="Отклонить выделенное", command=self.reject_term).grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.populate_proposals_list()

    def populate_main_list(self):
        self.main_textbox.configure(state="normal")
        self.main_textbox.delete("1.0", "end")
        search_term = self.search_var.get().lower()
        
        dict_copy = self.translator.db_manager.get_terms_dictionary()
        
        sorted_dict = sorted(dict_copy.items())
        for eng, rus in sorted_dict:
            if search_term in eng.lower() or search_term in rus.lower():
                self.main_textbox.insert("end", f"{eng:<40} -> {rus}\n")
        self.main_textbox.configure(state="disabled")

    def populate_proposals_list(self):
        self.proposals_textbox.configure(state="normal")
        self.proposals_textbox.delete("1.0", "end")
        
        proposals_copy = self.translator.db_manager.get_dictionary_proposals()
        
        sorted_proposals = sorted(proposals_copy.items(), key=lambda item: item[1]['confidence'], reverse=True)
        for eng, data in sorted_proposals:
            self.proposals_textbox.insert("end", f"{data.get('confidence', 0):.2f} | {data.get('category', 'other'):<12} | {eng:<30} -> {data.get('russian')}\n")
        self.proposals_textbox.configure(state="disabled")

    def update_proposal_tab_title(self):
        count = self.translator.db_manager.count_dictionary_proposals()
        
        theme = ctk.ThemeManager.theme
        color = ("#FFA000", "#FF8F00") if count > 0 else (theme["CTkSegmentedButton"]["selected_color"], theme["CTkSegmentedButton"]["selected_hover_color"])
        try:
            self.tab_view.configure(segmented_button_selected_color=color[0], segmented_button_selected_hover_color=color[1])
            self.tab_view.tab("На утверждение").configure(text=f"На утверждение ({count})")
        except Exception:
            pass

    def get_selected_term_from_line(self, line):
        return line.split('|')[2].split('->')[0].strip() if len(line.split('|')) >= 3 else None

    def approve_term(self):
        try:
            selected_text = self.proposals_textbox.get("sel.first", "sel.last")
            if not selected_text:
                return
            eng_term = self.get_selected_term_from_line(selected_text)
            if not eng_term:
                return
            
            proposal = self.translator.db_manager.get_dictionary_proposal(eng_term)
            if proposal:
                self.translator.db_manager.add_or_update_term(eng_term, proposal['russian'], proposal['category'])
                self.translator.db_manager.delete_dictionary_proposal(eng_term)

            self.populate_proposals_list()
            self.populate_main_list()
            self.update_proposal_tab_title()
        except ctk.TclError:
            messagebox.showinfo("Информация", "Выделите строку термина для одобрения.", parent=self)

    def reject_term(self):
        try:
            selected_text = self.proposals_textbox.get("sel.first", "sel.last")
            if not selected_text:
                return
            eng_term = self.get_selected_term_from_line(selected_text)
            if not eng_term:
                return
            self.translator.db_manager.delete_dictionary_proposal(eng_term)
            self.populate_proposals_list()
            self.update_proposal_tab_title()
        except ctk.TclError:
            messagebox.showinfo("Информация", "Выделите строку термина для отклонения.", parent=self)

    def approve_all_terms(self):
        proposals = self.translator.db_manager.get_dictionary_proposals()
        if not proposals:
            return
        for eng, data in proposals.items():
            self.translator.db_manager.add_or_update_term(eng, data['russian'], data['category'])
        self.translator.db_manager.clear_dictionary_proposals()
        self.populate_proposals_list()
        self.populate_main_list()
        self.update_proposal_tab_title()

    def reject_all_terms(self):
        count = self.translator.db_manager.count_dictionary_proposals()
        if count == 0:
            return
        if messagebox.askyesno("Отклонить все", f"Вы уверены, что хотите отклонить все {count} предложений?", parent=self):
            self.translator.db_manager.clear_dictionary_proposals()
            self.populate_proposals_list()
            self.update_proposal_tab_title()

    def add_term(self):
        eng, rus = self.eng_var.get().strip(), self.rus_var.get().strip()
        if not (eng and rus):
            messagebox.showerror("Ошибка", "Термин и перевод не могут быть пустыми", parent=self)
            return
        self.translator.db_manager.add_or_update_term(eng, rus)
        self.eng_var.set("")
        self.rus_var.set("")
        self.populate_main_list()

    def delete_term(self):
        try:
            selected_text = self.main_textbox.get("sel.first", "sel.last")
            if not selected_text:
                return
            eng_term_to_delete = selected_text.split("->")[0].strip()
            if eng_term_to_delete and messagebox.askyesno("Удаление", f"Удалить термин '{eng_term_to_delete}'?", parent=self):
                self.translator.db_manager.delete_term(eng_term_to_delete)
                self.populate_main_list()
        except ctk.TclError:
            messagebox.showinfo("Информация", "Выделите текст термина для удаления.", parent=self)

    def on_close(self):
        self.main_app.on_dictionary_close()
        self.destroy()

class KnowledgeAuditorWindow(ctk.CTkToplevel):
    def __init__(self, parent, translator):
        super().__init__(parent)
        self.main_app, self.translator = parent, translator
        self.title("Интерактивный Аудитор Базы Знаний")
        self.geometry("1100x750")
        self.transient(parent)
        self.grab_set()
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        top_frame = ctk.CTkFrame(self)
        top_frame.grid(row=0, column=0, padx=10, pady=10, sticky="ew")
        ctk.CTkLabel(top_frame, text="Анализ Базы Знаний на предмет проблем и их исправления...", font=ctk.CTkFont(size=14)).pack(side="left", padx=10)
        
        self.auto_merge_button = ctk.CTkButton(top_frame, text="Авто-исправление дубликатов", command=self.auto_merge_duplicates)
        self.auto_merge_button.pack(side="right", padx=10)

        self.status_label = ctk.CTkLabel(self, text="Запуск анализа...")
        self.status_label.grid(row=1, column=0, sticky="ew", padx=10, pady=5)
        
        self.scroll_frame = ctk.CTkScrollableFrame(self, label_text="Найденные проблемы и действия")
        self.scroll_frame.grid(row=2, column=0, padx=10, pady=10, sticky="nsew")
        self.scroll_frame.grid_columnconfigure(0, weight=1)
        
        self.progress_bar = ctk.CTkProgressBar(self, mode="indeterminate")
        self.progress_bar.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 10))
        
        self.run_analysis()

    def run_analysis(self):
        self.status_label.configure(text="Анализ...")
        self.progress_bar.start()
        self.auto_merge_button.configure(state="disabled")
        for widget in self.scroll_frame.winfo_children():
            widget.destroy()
        threading.Thread(target=self._analysis_thread, daemon=True).start()

    def _analysis_thread(self):
        report = self.translator.audit_knowledge_base()
        self.after(0, self.display_report, report)

    def display_report(self, report):
        self.progress_bar.stop()
        self.progress_bar.grid_forget()
        self.auto_merge_button.configure(state="normal")

        if "error" in report:
            self.status_label.configure(text=f"Ошибка анализа: {report['error']}")
            return

        issues = report.get("issues", [])
        if not issues:
            self.status_label.configure(text="Проблем не найдено. Ваша База Знаний в отличном состоянии!")
            return
        
        self.status_label.configure(text=f"Найдено потенциальных проблем: {len(issues)}")
        
        for i, issue in enumerate(issues):
            issue_frame = ctk.CTkFrame(self.scroll_frame, border_width=1)
            issue_frame.grid(row=i, column=0, padx=5, pady=5, sticky="ew")
            issue_frame.grid_columnconfigure(0, weight=1)
            
            self._create_issue_widget(issue_frame, issue)

    def _create_issue_widget(self, parent_frame, issue):
        issue_type = issue.get("type", "unknown")
        description = issue.get("description", "N/A")
        items = issue.get("items_involved", [])
        
        title = f"{issue_type.replace('_', ' ').title()}: {', '.join(items)}"
        ctk.CTkLabel(parent_frame, text=title, font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=10, pady=5, sticky="w")
        ctk.CTkLabel(parent_frame, text=description, wraplength=800, justify="left").grid(row=1, column=0, padx=10, pady=5, sticky="w")

        action_frame = ctk.CTkFrame(parent_frame, fg_color="transparent")
        action_frame.grid(row=2, column=0, padx=10, pady=5, sticky="ew")

        if issue_type == "potential_dictionary_duplicate" and len(items) > 1:
            chosen_term = ctk.StringVar(value=items[0])
            for term in items:
                ctk.CTkRadioButton(action_frame, text=f"Оставить '{term}'", variable=chosen_term, value=term).pack(anchor="w", padx=5)
            ctk.CTkButton(action_frame, text="Объединить", command=lambda i=items, c=chosen_term: self._handle_merge_duplicates(i, c.get())).pack(pady=5, anchor="w", padx=5)

        elif issue_type == "low_quality_description" and items:
            ctk.CTkButton(action_frame, text="Редактировать запись", command=lambda item=items[0]: self._handle_edit_bible_entry(item)).pack(pady=5, anchor="w", padx=5)
        
        elif issue_type == "contradiction" and items:
            ctk.CTkLabel(action_frame, text="Рекомендуется ручное исправление:").pack(anchor="w", padx=5)
            for item in items:
                if item in self.translator.world_bible:
                    ctk.CTkButton(action_frame, text=f"Перейти к '{item}' в Библии", command=lambda i=item: self._handle_edit_bible_entry(i)).pack(pady=2, anchor="w", padx=5)
                if item in self.translator.translation_dictionary:
                     ctk.CTkButton(action_frame, text=f"Перейти к '{item}' в Словаре", command=lambda i=item: self._handle_edit_dict_entry(i)).pack(pady=2, anchor="w", padx=5)

    def _handle_merge_duplicates(self, all_terms, primary_term):
        if self.translator.merge_dictionary_terms(primary_term, [t for t in all_terms if t != primary_term]):
            self.main_app.show_message("Успех", f"Термины успешно объединены в '{primary_term}'.")
            self.run_analysis()
            self.main_app.update_index_status()
        else:
            self.main_app.show_message("Ошибка", "Не удалось объединить термины.")

    def auto_merge_duplicates(self):
        self.auto_merge_button.configure(state="disabled", text="Обработка...")
        threading.Thread(target=self._auto_merge_thread, daemon=True).start()

    def _auto_merge_thread(self):
        merged_count = self.translator.automerge_dictionary_duplicates()
        self.after(0, self._finalize_auto_merge, merged_count)

    def _finalize_auto_merge(self, merged_count):
        self.auto_merge_button.configure(state="normal", text="Авто-исправление дубликатов")
        if merged_count > 0:
            self.main_app.show_message("Успех", f"Автоматически объединено {merged_count} групп дубликатов.")
            self.run_analysis()
            self.main_app.update_index_status()
        else:
            self.main_app.show_message("Информация", "Дубликатов для автоматического объединения не найдено.")

    def _handle_edit_bible_entry(self, item_name):
        bible_copy = self.translator.db_manager.get_world_bible()
        if item_name in bible_copy:
            self.main_app.edit_bible_entry_from_list(item_name, bible_copy[item_name])
            self.main_app.lift()
            self.destroy()
        else:
            self.main_app.show_message("Ошибка", f"Запись '{item_name}' не найдена в Библии.")

    def _handle_edit_dict_entry(self, item_name):
        self.main_app.open_dictionary_editor()
        self.main_app.lift()
        self.destroy()

class ConsistencyCheckerWindow(ctk.CTkToplevel):
    def __init__(self, parent, translator, processed_texts):
        super().__init__(parent)
        self.main_app, self.translator, self.processed_texts = parent, translator, processed_texts
        self.title("Аудитор Консистентности с Прямым Исправлением")
        self.geometry("900x700")
        self.transient(parent)
        self.grab_set()
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        top_frame = ctk.CTkFrame(self)
        top_frame.grid(row=0, column=0, padx=10, pady=10, sticky="ew")
        ctk.CTkLabel(top_frame, text="Поиск и исправление неконсистентных переводов...", font=ctk.CTkFont(size=16)).pack(side="left", padx=10)
        self.auto_unify_button = ctk.CTkButton(top_frame, text="Унифицировать всё (автоматически)", command=self.auto_unify_all, fg_color="#2E7D32", hover_color="#1B5E20")
        self.auto_unify_button.pack(side="right", padx=10)

        self.status_label = ctk.CTkLabel(self, text="Запуск анализа...")
        self.status_label.grid(row=1, column=0, sticky="ew", padx=10)
        
        self.scroll_frame = ctk.CTkScrollableFrame(self, label_text="Найденные проблемы")
        self.scroll_frame.grid(row=2, column=0, padx=10, pady=10, sticky="nsew")
        self.scroll_frame.grid_columnconfigure(0, weight=1)
        
        threading.Thread(target=self.run_analysis, daemon=True).start()

    def run_analysis(self):
        self.auto_unify_button.configure(state="disabled")
        report = self.translator.analyze_inconsistencies(self.processed_texts)
        self.after(0, self.display_report, report)

    def display_report(self, report):
        for widget in self.scroll_frame.winfo_children():
            widget.destroy()
        if "error" in report:
            self.status_label.configure(text=f"Ошибка анализа: {report['error']}")
            return
        inconsistencies = report.get("inconsistencies", [])
        if not inconsistencies:
            self.status_label.configure(text="Проблем не найдено. Поздравляем!")
            self.auto_unify_button.configure(state="disabled")
            return
        
        self.status_label.configure(text=f"Найдено проблем: {len(inconsistencies)}")
        self.auto_unify_button.configure(state="normal")
        for i, issue in enumerate(inconsistencies):
            eng_term = issue.get("english_term")
            variants = issue.get("russian_variants", [])
            if not eng_term or not variants or len(variants) < 2:
                continue
            issue_frame = ctk.CTkFrame(self.scroll_frame, border_width=1)
            issue_frame.grid(row=i, column=0, padx=5, pady=5, sticky="ew")
            issue_frame.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(issue_frame, text=f"Термин (Eng): '{eng_term}'", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, columnspan=2, padx=10, pady=5, sticky="w")
            chosen_variant = ctk.StringVar(value=variants[0])
            for j, variant in enumerate(variants):
                ctk.CTkRadioButton(issue_frame, text=variant, variable=chosen_variant, value=variant).grid(row=j+1, column=0, padx=20, pady=2, sticky="w")
            
            ctk.CTkButton(issue_frame, text="Унифицировать и Сохранить", command=lambda e=eng_term, v=variants, c=chosen_variant, f=issue_frame: self.unify_and_save_term(e, v, c.get(), f)).grid(row=1, column=1, rowspan=len(variants), padx=10, sticky="e")

    def unify_and_save_term(self, eng_term, old_variants, correct_variant, frame_to_disable):
        for widget in frame_to_disable.winfo_children():
            if isinstance(widget, (ctk.CTkButton, ctk.CTkRadioButton)):
                widget.configure(state="disabled")
        
        button = next((w for w in frame_to_disable.winfo_children() if isinstance(w, ctk.CTkButton)), None)
        if button:
            button.configure(text="Обработка...")

        self.status_label.configure(text=f"Унификация '{eng_term}' до '{correct_variant}'...")
        threading.Thread(target=self._unify_thread, args=(eng_term, old_variants, correct_variant, frame_to_disable), daemon=True).start()

    def _unify_thread(self, eng_term, old_variants, correct_variant, frame_to_disable):
        replacements_count = 0
        files_modified_count = 0
        
        variants_to_replace = [v for v in old_variants if v.lower() != correct_variant.lower()]

        for chapter_data in self.processed_texts:
            original_text = chapter_data['rus']
            modified_text = original_text
            file_was_modified = False

            for old_variant in variants_to_replace:
                modified_text, count = re.subn(r'\b' + re.escape(old_variant) + r'\b', correct_variant, modified_text, flags=re.IGNORECASE)
                if count > 0:
                    replacements_count += count
                    file_was_modified = True
            
            if file_was_modified:
                chapter_data['rus'] = modified_text
                try:
                    with open(chapter_data['output_path'], 'w', encoding='utf-8') as f:
                        f.write(modified_text)
                    files_modified_count += 1
                except Exception as e:
                    gui_logger.error(f"Не удалось сохранить унифицированный файл {chapter_data['output_path']}: {e}")
                    self.after(0, self.show_message, "Ошибка Сохранения", f"Не удалось сохранить файл: {chapter_data['output_path']}\n{e}")

        self.after(0, self._finalize_unification, replacements_count, files_modified_count, frame_to_disable)

    def _finalize_unification(self, replacements_count, files_modified_count, frame_to_disable):
        if self.main_app.interactive_editor_window and self.main_app.interactive_editor_window.winfo_exists():
            self.main_app.interactive_editor_window.processed_texts = self.processed_texts
            self.main_app.interactive_editor_window.load_chapter(self.main_app.interactive_editor_window.chapter_var.get())
        
        self.show_message("Успех", f"Унификация завершена.\nВыполнено замен: {replacements_count}\nИзменено файлов: {files_modified_count}")
        
        frame_to_disable.destroy()
        
        if not self.scroll_frame.winfo_children():
            self.status_label.configure(text="Все проблемы решены!")
            self.auto_unify_button.configure(state="disabled")

    def auto_unify_all(self):
        self.status_label.configure(text="Запуск автоматической унификации...")
        self.auto_unify_button.configure(state="disabled", text="Обработка...")
        for widget in self.scroll_frame.winfo_children():
            widget.destroy()
        threading.Thread(target=self._auto_unify_all_thread, daemon=True).start()

    def _auto_unify_all_thread(self):
        summary = self.translator.run_auto_unification(self.processed_texts)
        self.after(0, self._finalize_auto_unify_all, summary)

    def _finalize_auto_unify_all(self, summary):
        if self.main_app.interactive_editor_window and self.main_app.interactive_editor_window.winfo_exists():
            self.main_app.interactive_editor_window.processed_texts = self.processed_texts
            self.main_app.interactive_editor_window.load_chapter(self.main_app.interactive_editor_window.chapter_var.get())

        message = (f"Автоматическая унификация завершена.\n\n"
                   f"Найдено и обработано проблем: {summary['issues_found']}\n"
                   f"Всего сделано замен: {summary['replacements_made']}\n"
                   f"Изменено файлов на диске: {summary['files_modified']}")
        self.show_message("Авто-унификация завершена", message)
        self.destroy()

    def show_message(self, title, message):
        messagebox.showinfo(title, message, parent=self)

class MasterEditorWindow(ctk.CTkToplevel):
    def __init__(self, parent, translator, processed_texts):
        super().__init__(parent)
        self.main_app, self.translator, self.processed_texts = parent, translator, processed_texts
        self.edited_text_full = ""
        self.title("Мастер-Редактор (Глобальная стилистическая правка)")
        self.geometry("1200x800")
        self.transient(parent)
        self.grab_set()
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        top_frame = ctk.CTkFrame(self)
        top_frame.grid(row=0, column=0, padx=10, pady=10, sticky="ew")
        self.status_label = ctk.CTkLabel(top_frame, text="Идет анализ всей книги... Это может занять несколько минут.", font=ctk.CTkFont(size=14))
        self.status_label.pack(side="left", padx=10)
        self.save_button = ctk.CTkButton(top_frame, text="Сохранить в новую папку...", state="disabled", command=self.save_edited_version)
        self.save_button.pack(side="right", padx=10)
        self.diff_textbox = ctk.CTkTextbox(self, font=("Consolas", 13), wrap="word")
        self.diff_textbox.grid(row=1, column=0, padx=10, pady=10, sticky="nsew")
        self.diff_textbox.tag_config("add", foreground="#4CAF50")
        self.diff_textbox.tag_config("del", foreground="#F44336")
        self.diff_textbox.tag_config("info", foreground="gray")
        self.diff_textbox.configure(state="disabled")
        threading.Thread(target=self.run_master_edit_thread, daemon=True).start()

    def run_master_edit_thread(self):
        edited_text, error = self.translator.run_master_edit(self.processed_texts)
        self.after(0, self.display_results, edited_text, error)

    def display_results(self, edited_text, error):
        self.diff_textbox.configure(state="normal")
        self.diff_textbox.delete("1.0", "end")
        if error:
            self.status_label.configure(text=f"Ошибка: {error}")
            self.diff_textbox.insert("end", f"Не удалось выполнить мастер-редактуру.\n\nОшибка: {error}")
            self.diff_textbox.configure(state="disabled")
            return
        self.edited_text_full = edited_text
        original_full_text = ""
        separator_template = "\n\n---=== CHAPTER: {title} ===---\n\n"
        for item in self.processed_texts:
            original_full_text += separator_template.format(title=item['title'])
            original_full_text += item['rus']
        self.status_label.configure(text="Анализ завершен. Просмотрите изменения.")
        self.save_button.configure(state="normal")
        diff = difflib.ndiff(original_full_text.splitlines(keepends=True), edited_text.splitlines(keepends=True))
        for line in diff:
            if line.startswith('+ '):
                self.diff_textbox.insert("end", line[2:], "add")
            elif line.startswith('- '):
                self.diff_textbox.insert("end", line[2:], "del")
            elif line.startswith('? '):
                continue
            else:
                self.diff_textbox.insert("end", line)
        self.diff_textbox.configure(state="disabled")

    def save_edited_version(self):
        if not self.edited_text_full:
            messagebox.showerror("Ошибка", "Нет отредактированного текста для сохранения.", parent=self)
            return
        output_dir = self.translator.settings.get("output_dir")
        new_dir = filedialog.askdirectory(initialdir=output_dir, title="Выберите папку для сохранения мастер-версии")
        if not new_dir:
            return
        try:
            chapters = re.split(r'\n\n---=== CHAPTER: (.+?) ===---\n\n', self.edited_text_full)
            if len(chapters) < 2: 
                with open(os.path.join(new_dir, "MASTER_EDIT_FULL.txt"), 'w', encoding='utf-8') as f:
                    f.write(self.edited_text_full)
                messagebox.showinfo("Успех", "Текст сохранен в один файл, так как не удалось разделить на главы.", parent=self)
                return
            for i in range(1, len(chapters), 2):
                title = chapters[i]
                content = chapters[i+1]
                output_path = os.path.join(new_dir, f"ru_{title}")
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(content.strip())
            messagebox.showinfo("Успех", f"Мастер-версия успешно сохранена в папку:\n{new_dir}", parent=self)
            self.destroy()
        except Exception as e:
            messagebox.showerror("Ошибка сохранения", f"Не удалось сохранить файлы: {e}", parent=self)