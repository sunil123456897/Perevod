import customtkinter as ctk
from tkinter import messagebox
import threading
import logging
from Perevod.gui.base_editor_window import BaseEditorWindow
from Perevod.agents.translator import simple_translate

gui_logger = logging.getLogger("NovelTranslator.GUI")

class WorldBibleEditorWindow(BaseEditorWindow):
    def __init__(self, master, db_manager, api_key, model_name):
        super().__init__(master, "Редактор Библии Вселенной", "1200x800")
        self.db_manager = db_manager
        self.api_key = api_key
        self.model_name = model_name
        self.all_entries = []
        self.all_proposals = []
        self.entry_widgets = {}
        self.current_page_entries = 1
        self.current_page_proposals = 1
        self.items_per_page = 10

        self._create_widgets()
        threading.Thread(target=self._load_data_thread, daemon=True).start()

    def _create_widgets(self):
        self.tab_view.add("Библия")
        self.tab_view.add("Предложения")
        self.tab_view.set("Библия")
        self.tab_view.configure(command=self.on_tab_change)

        # --- Вкладка "Библия" ---
        entries_frame = self.tab_view.tab("Библия")
        entries_frame.grid_columnconfigure(0, weight=1); entries_frame.grid_rowconfigure(1, weight=1)
        
        entries_controls = ctk.CTkFrame(entries_frame, fg_color="transparent")
        entries_controls.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        entries_controls.grid_columnconfigure(1, weight=1)
        self.add_entry_btn = ctk.CTkButton(entries_controls, text="Добавить новую запись", command=self.add_new_entry)
        self.add_entry_btn.grid(row=0, column=0, padx=(0,5))
        self.save_entries_btn = ctk.CTkButton(entries_controls, text="Сохранить все изменения", command=self.save_all_entries, fg_color="#2E7D32", hover_color="#1B5E20")
        self.save_entries_btn.grid(row=0, column=2, padx=(5,0))

        self.entries_content_frame = ctk.CTkScrollableFrame(entries_frame, label_text="Принятые записи")
        self.entries_content_frame.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
        self.entries_content_frame.grid_columnconfigure(0, weight=1)

        self.entries_nav_frame = ctk.CTkFrame(entries_frame, fg_color="transparent")
        self.entries_nav_frame.grid(row=2, column=0, sticky="ew", padx=5, pady=5)
        self.entries_nav_frame.grid_columnconfigure(1, weight=1)
        self.prev_entries_btn = ctk.CTkButton(self.entries_nav_frame, text="< Назад", command=lambda: self.change_page_entries(-1))
        self.prev_entries_btn.grid(row=0, column=0)
        self.page_label_entries = ctk.CTkLabel(self.entries_nav_frame, text="Страница 1 / 1")
        self.page_label_entries.grid(row=0, column=1)
        self.next_entries_btn = ctk.CTkButton(self.entries_nav_frame, text="Вперед >", command=lambda: self.change_page_entries(1))
        self.next_entries_btn.grid(row=0, column=2)

        # --- Вкладка "Предложения" ---
        proposals_frame = self.tab_view.tab("Предложения")
        proposals_frame.grid_columnconfigure(0, weight=1); proposals_frame.grid_rowconfigure(1, weight=1)

        proposals_controls = ctk.CTkFrame(proposals_frame, fg_color="transparent")
        proposals_controls.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        proposals_controls.grid_columnconfigure(1, weight=1)
        self.accept_all_prop_btn = ctk.CTkButton(proposals_controls, text="✓ Принять все", command=self.accept_all_proposals, fg_color="#2E7D32", hover_color="#1B5E20")
        self.accept_all_prop_btn.grid(row=0, column=0, padx=(0,5))
        self.reject_all_prop_btn = ctk.CTkButton(proposals_controls, text="✗ Отклонить все", command=self.reject_all_proposals, fg_color="#D32F2F", hover_color="#B71C1C")
        self.reject_all_prop_btn.grid(row=0, column=2, padx=(5,0))

        self.proposals_content_frame = ctk.CTkScrollableFrame(proposals_frame, label_text="Предложения от ИИ")
        self.proposals_content_frame.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
        self.proposals_content_frame.grid_columnconfigure(0, weight=1)

        self.proposals_nav_frame = ctk.CTkFrame(proposals_frame, fg_color="transparent")
        self.proposals_nav_frame.grid(row=2, column=0, sticky="ew", padx=5, pady=5)
        self.proposals_nav_frame.grid_columnconfigure(1, weight=1)
        self.prev_proposals_btn = ctk.CTkButton(self.proposals_nav_frame, text="< Назад", command=lambda: self.change_page_proposals(-1))
        self.prev_proposals_btn.grid(row=0, column=0)
        self.page_label_proposals = ctk.CTkLabel(self.proposals_nav_frame, text="Страница 1 / 1")
        self.page_label_proposals.grid(row=0, column=1)
        self.next_proposals_btn = ctk.CTkButton(self.proposals_nav_frame, text="Вперед >", command=lambda: self.change_page_proposals(1))
        self.next_proposals_btn.grid(row=0, column=2)

        self._create_action_buttons([{"text": "Закрыть", "command": self.destroy}])

    def _load_data_thread(self):
        try:
            db_entries = self.db_manager.get_all_world_bible_entries()
            db_proposals = list(self.db_manager.get_world_bible_proposals().values())
            self.after(0, self._populate_ui, db_entries, db_proposals)
        except Exception as e:
            gui_logger.error(f"Ошибка загрузки данных Библии: {e}", exc_info=True)
            self.after(0, messagebox.showerror, "Ошибка", f"Не удалось загрузить данные Библии: {e}")

    def _populate_ui(self, entries, proposals):
        self.all_entries = sorted(entries, key=lambda x: x['english_name'].lower())
        self.all_proposals = sorted(proposals, key=lambda x: x['english_name'].lower())
        self.entry_widgets = {}
        for entry in self.all_entries:
            entry['is_new'] = False
            entry['is_modified'] = False
            self.entry_widgets[entry['english_name']] = {
                'eng_var': ctk.StringVar(value=entry['english_name']),
                'rus_var': ctk.StringVar(value=entry.get('russian_name', '')),
                'cat_var': ctk.StringVar(value=entry.get('category', '')),
                'desc_eng_text': entry.get('description', ''),
                'desc_rus_text': entry.get('russian_description', '')
            }
        self.current_page_entries = 1
        self.current_page_proposals = 1
        self.filter_entries()

    def on_tab_change(self):
        self.filter_entries()

    def render_entries_page(self, entries_to_render):
        for widget in self.entries_content_frame.winfo_children(): widget.destroy()
        
        start_index = (self.current_page_entries - 1) * self.items_per_page
        end_index = start_index + self.items_per_page
        page_entries = entries_to_render[start_index:end_index]

        for i, entry_data in enumerate(page_entries):
            self.create_entry_widget(entry_data, i)

        total_pages = (len(entries_to_render) + self.items_per_page - 1) // self.items_per_page
        self.page_label_entries.configure(text=f"Страница {self.current_page_entries} / {max(1, total_pages)}")
        self.prev_entries_btn.configure(state="normal" if self.current_page_entries > 1 else "disabled")
        self.next_entries_btn.configure(state="normal" if self.current_page_entries < total_pages else "disabled")

    def create_entry_widget(self, entry_data, index):
        original_eng = entry_data['english_name']
        widget_vars = self.entry_widgets[original_eng]

        frame = ctk.CTkFrame(self.entries_content_frame, border_width=1); frame.grid(row=index, column=0, padx=5, pady=5, sticky="ew"); frame.grid_columnconfigure(0, weight=1)
        
        top = ctk.CTkFrame(frame, fg_color="transparent"); top.grid(row=0, column=0, sticky="ew"); top.grid_columnconfigure((1, 3), weight=1)
        ctk.CTkLabel(top, text="Англ:").grid(row=0, column=0, padx=(5,2)); eng_entry = ctk.CTkEntry(top, textvariable=widget_vars['eng_var']); eng_entry.grid(row=0, column=1, padx=(0,10), sticky="ew")
        ctk.CTkLabel(top, text="Рус:").grid(row=0, column=2, padx=(5,2)); rus_entry = ctk.CTkEntry(top, textvariable=widget_vars['rus_var']); rus_entry.grid(row=0, column=3, padx=(0,10), sticky="ew")
        ctk.CTkLabel(top, text="Кат:").grid(row=0, column=4, padx=(5,2)); cat_entry = ctk.CTkEntry(top, textvariable=widget_vars['cat_var'], width=120); cat_entry.grid(row=0, column=5, padx=(0,5))

        desc_frame = ctk.CTkFrame(frame, fg_color="transparent"); desc_frame.grid(row=1, column=0, sticky="ew", padx=5, pady=5); desc_frame.grid_columnconfigure(0, weight=1)
        
        ctk.CTkLabel(desc_frame, text="Описание (Англ):").grid(row=0, column=0, sticky="w")
        desc_eng = ctk.CTkTextbox(desc_frame, height=80, wrap="word"); desc_eng.grid(row=1, column=0, sticky="ew"); desc_eng.insert("1.0", widget_vars['desc_eng_text'] or "")
        widget_vars['desc_eng_textbox'] = desc_eng

        translate_btn_frame = ctk.CTkFrame(desc_frame, fg_color="transparent"); translate_btn_frame.grid(row=2, column=0, sticky="ew"); translate_btn_frame.grid_columnconfigure(0, weight=1)
        translate_btn = ctk.CTkButton(translate_btn_frame, text="▼ Перевести ▼", command=lambda we=widget_vars: self.translate_description(we))
        translate_btn.grid(row=0, column=0, pady=2)

        ctk.CTkLabel(desc_frame, text="Описание (Рус):").grid(row=3, column=0, sticky="w")
        desc_rus = ctk.CTkTextbox(desc_frame, height=80, wrap="word"); desc_rus.grid(row=4, column=0, sticky="ew"); desc_rus.insert("1.0", widget_vars['desc_rus_text'] or "")
        widget_vars['desc_rus_textbox'] = desc_rus

        # Trace changes
        widget_vars['eng_var'].trace_add("write", lambda *args, oe=original_eng: self.mark_as_modified(oe))
        widget_vars['rus_var'].trace_add("write", lambda *args, oe=original_eng: self.mark_as_modified(oe))
        widget_vars['cat_var'].trace_add("write", lambda *args, oe=original_eng: self.mark_as_modified(oe))
        desc_eng.bind("<KeyRelease>", lambda event, oe=original_eng: self.mark_as_modified(oe))
        desc_rus.bind("<KeyRelease>", lambda event, oe=original_eng: self.mark_as_modified(oe))

        bottom = ctk.CTkFrame(frame, fg_color="transparent"); bottom.grid(row=2, column=0, sticky="ew", padx=5, pady=5); bottom.grid_columnconfigure(0, weight=1)
        del_btn = ctk.CTkButton(bottom, text="Удалить", width=80, fg_color="#D32F2F", hover_color="#B71C1C", command=lambda n=original_eng: self.delete_entry(n)); del_btn.grid(row=0, column=0, sticky="e")

    def translate_description(self, widget_vars):
        eng_text = widget_vars['desc_eng_textbox'].get("1.0", "end-1c").strip()
        if not eng_text:
            messagebox.showinfo("Информация", "Поле с английским описанием пустое.")
            return
        
        widget_vars['desc_rus_textbox'].delete("1.0", ctk.END)
        widget_vars['desc_rus_textbox'].insert("1.0", "Перевод...")
        
        def _translate_thread():
            translated_text = simple_translate(eng_text, self.api_key, self.model_name)
            self.after(0, lambda: widget_vars['desc_rus_textbox'].delete("1.0", ctk.END))
            self.after(0, lambda: widget_vars['desc_rus_textbox'].insert("1.0", translated_text))

        threading.Thread(target=_translate_thread, daemon=True).start()

    def render_proposals_page(self, proposals_to_render):
        for widget in self.proposals_content_frame.winfo_children(): widget.destroy()
        ctk.CTkLabel(self.proposals_content_frame, text="Предложенная запись", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=5, pady=5, sticky="w")

        start_index = (self.current_page_proposals - 1) * self.items_per_page
        end_index = start_index + self.items_per_page
        page_proposals = proposals_to_render[start_index:end_index]

        for i, data in enumerate(page_proposals):
            eng = data['english_name']
            frame = ctk.CTkFrame(self.proposals_content_frame, border_width=1); frame.grid(row=i + 1, column=0, sticky="ew", pady=2, padx=5); frame.grid_columnconfigure(0, weight=1)
            label_text = f"{eng} ({data['category']})"; label = ctk.CTkLabel(frame, text=label_text, anchor="w"); label.grid(row=0, column=0, sticky="ew", padx=5, pady=2)
            desc_text = ctk.CTkLabel(frame, text=data['description'], wraplength=700, justify="left", anchor="w", fg_color="gray20", corner_radius=5); desc_text.grid(row=1, column=0, sticky="ew", padx=5, pady=2)
            btn_frame = ctk.CTkFrame(frame, fg_color="transparent"); btn_frame.grid(row=0, column=1, rowspan=2, sticky="e");
            btn_accept = ctk.CTkButton(btn_frame, text="✓ Принять", width=100, command=lambda e=eng, d=data: self.accept_proposal(e, d)); btn_accept.pack(padx=5, pady=2)
            btn_reject = ctk.CTkButton(btn_frame, text="✗ Отклонить", width=100, command=lambda e=eng: self.reject_proposal(e)); btn_reject.pack(padx=5, pady=2)

        total_pages = (len(proposals_to_render) + self.items_per_page - 1) // self.items_per_page
        self.page_label_proposals.configure(text=f"Страница {self.current_page_proposals} / {max(1, total_pages)}")
        self.prev_proposals_btn.configure(state="normal" if self.current_page_proposals > 1 else "disabled")
        self.next_proposals_btn.configure(state="normal" if self.current_page_proposals < total_pages else "disabled")

    def mark_as_modified(self, original_eng):
        entry = next((e for e in self.all_entries if e['english_name'] == original_eng), None)
        if entry: entry['is_modified'] = True

    def add_new_entry(self):
        new_entry_id = f"__new_{len([e for e in self.all_entries if e.get('is_new')])}"
        new_entry = {'english_name': new_entry_id, 'russian_name': '', 'category': '', 'description': '', 'russian_description': '', 'is_new': True, 'is_modified': True}
        self.all_entries.insert(0, new_entry)
        self.entry_widgets[new_entry_id] = {
            'eng_var': ctk.StringVar(value=''), 'rus_var': ctk.StringVar(value=''),
            'cat_var': ctk.StringVar(value=''), 'desc_eng_text': '', 'desc_rus_text': ''
        }
        self.filter_entries()

    def save_all_entries(self):
        try:
            for entry_data in self.all_entries:
                if not entry_data.get('is_modified'): continue
                
                original_eng = entry_data['english_name']
                widget_vars = self.entry_widgets[original_eng]
                new_eng = widget_vars['eng_var'].get().strip()
                new_rus = widget_vars['rus_var'].get().strip()
                new_cat = widget_vars['cat_var'].get().strip()
                new_desc_eng = widget_vars['desc_eng_textbox'].get("1.0", "end-1c").strip()
                new_desc_rus = widget_vars['desc_rus_textbox'].get("1.0", "end-1c").strip()

                if not new_eng or not new_desc_eng:
                    if not entry_data.get('is_new'): self.db_manager.delete_bible_entry(original_eng)
                    continue

                update_data = {"russian_name": new_rus, "category": new_cat, "description": new_desc_eng, "russian_description": new_desc_rus}
                
                if entry_data.get('is_new'):
                    self.db_manager.add_or_update_bible_entry(new_eng, update_data)
                else:
                    if original_eng != new_eng:
                        self.db_manager.delete_bible_entry(original_eng)
                    self.db_manager.add_or_update_bible_entry(new_eng, update_data)

            gui_logger.info("Библия сохранена."); threading.Thread(target=self._load_data_thread, daemon=True).start()
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить Библию: {e}")
            gui_logger.error(f"Ошибка сохранения Библии: {e}", exc_info=True)

    def delete_entry(self, original_eng):
        self.all_entries = [e for e in self.all_entries if e['english_name'] != original_eng]
        self.entry_widgets.pop(original_eng, None)
        self.db_manager.delete_bible_entry(original_eng)
        self.filter_entries()

    def accept_proposal(self, eng, data):
        self.db_manager.add_or_update_bible_entry(eng, data)
        self.db_manager.delete_world_bible_proposal(eng)
        threading.Thread(target=self._load_data_thread, daemon=True).start()
    def reject_proposal(self, eng):
        self.db_manager.delete_world_bible_proposal(eng)
        threading.Thread(target=self._load_data_thread, daemon=True).start()
    def accept_all_proposals(self):
        if messagebox.askyesno("Подтверждение", "Принять все видимые предложения?"):
            for p in self.all_proposals:
                self.db_manager.add_or_update_bible_entry(p['english_name'], {"russian_name": p['russian_name'], "category": p['category'], "description": p['description']})
            self.db_manager.clear_world_bible_proposals()
            threading.Thread(target=self._load_data_thread, daemon=True).start()
    def reject_all_proposals(self):
        if messagebox.askyesno("Подтверждение", "Отклонить все предложения?"):
            self.db_manager.clear_world_bible_proposals()
            threading.Thread(target=self._load_data_thread, daemon=True).start()

    def filter_entries(self, *args):
        query = self.search_var.get().lower()
        
        # Фильтрация для Библии
        filtered_entries = [e for e in self.all_entries if query in e['english_name'].lower() or query in self.entry_widgets[e['english_name']]['rus_var'].get().lower() or query in self.entry_widgets[e['english_name']]['desc_eng_text'].lower() or query in self.entry_widgets[e['english_name']]['desc_rus_text'].lower()]
        self.current_page_entries = 1
        self.render_entries_page(filtered_entries)

        # Фильтрация для Предложений
        filtered_proposals = [p for p in self.all_proposals if query in p['english_name'].lower() or (p.get('russian_name') and query in p['russian_name'].lower()) or query in p['description'].lower()]
        self.current_page_proposals = 1
        self.render_proposals_page(filtered_proposals)

    def change_page_entries(self, delta):
        query = self.search_var.get().lower()
        filtered_entries = [e for e in self.all_entries if query in e['english_name'].lower() or query in self.entry_widgets[e['english_name']]['rus_var'].get().lower() or query in self.entry_widgets[e['english_name']]['desc_eng_text'].lower() or query in self.entry_widgets[e['english_name']]['desc_rus_text'].lower()]
        total_pages = (len(filtered_entries) + self.items_per_page - 1) // self.items_per_page
        new_page = self.current_page_entries + delta
        if 1 <= new_page <= total_pages:
            self.current_page_entries = new_page
            self.render_entries_page(filtered_entries)

    def change_page_proposals(self, delta):
        query = self.search_var.get().lower()
        filtered_proposals = [p for p in self.all_proposals if query in p['english_name'].lower() or (p.get('russian_name') and query in p['russian_name'].lower()) or query in p['description'].lower()]
        total_pages = (len(filtered_proposals) + self.items_per_page - 1) // self.items_per_page
        new_page = self.current_page_proposals + delta
        if 1 <= new_page <= total_pages:
            self.current_page_proposals = new_page
            self.render_proposals_page(filtered_proposals)