
import customtkinter as ctk
from tkinter import messagebox
import threading
import logging
from Perevod.gui.paginated_editor import PaginatedEditorWindow

gui_logger = logging.getLogger("NovelTranslator.GUI")

class DictionaryEditorWindow(PaginatedEditorWindow):
    def __init__(self, master, db_manager):
        super().__init__(master, "Редактор Словаря")
        self.db_manager = db_manager
        self.all_proposals = []
        self.term_widgets = {}
        self.current_page_proposals = 1

        self._create_widgets()
        threading.Thread(target=self._load_data_thread, daemon=True).start()

    def _create_widgets(self):
        self.tab_view.add("Словарь")
        self.tab_view.add("Предложения")
        self.tab_view.set("Словарь")
        self.tab_view.configure(command=self.on_tab_change)

        # --- Вкладка "Словарь" ---
        terms_frame = self.tab_view.tab("Словарь")
        terms_frame.grid_columnconfigure(0, weight=1); terms_frame.grid_rowconfigure(1, weight=1)
        
        terms_controls = ctk.CTkFrame(terms_frame, fg_color="transparent")
        terms_controls.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        terms_controls.grid_columnconfigure(1, weight=1)
        self.add_term_btn = ctk.CTkButton(terms_controls, text="Добавить новый термин", command=self.add_new_term)
        self.add_term_btn.grid(row=0, column=0, padx=(0,5))
        self.save_terms_btn = ctk.CTkButton(terms_controls, text="Сохранить все изменения", command=self.save_all_terms, fg_color="#2E7D32", hover_color="#1B5E20")
        self.save_terms_btn.grid(row=0, column=2, padx=(5,0))

        self.terms_content_frame = ctk.CTkScrollableFrame(terms_frame, label_text="Принятые термины")
        self.terms_content_frame.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
        self.terms_content_frame.grid_columnconfigure((0, 1), weight=1)

        self.terms_nav_frame = ctk.CTkFrame(terms_frame, fg_color="transparent")
        self.terms_nav_frame.grid(row=2, column=0, sticky="ew", padx=5, pady=5)
        self.terms_nav_frame.grid_columnconfigure(1, weight=1)
        self.prev_terms_btn = ctk.CTkButton(self.terms_nav_frame, text="< Назад", command=lambda: self.change_page(-1))
        self.prev_terms_btn.grid(row=0, column=0)
        self.page_label_terms = ctk.CTkLabel(self.terms_nav_frame, text="Страница 1 / 1")
        self.page_label_terms.grid(row=0, column=1)
        self.next_terms_btn = ctk.CTkButton(self.terms_nav_frame, text="Вперед >", command=lambda: self.change_page(1))
        self.next_terms_btn.grid(row=0, column=2)

        # --- Вкладка "Предложения" ---
        proposals_frame = self.tab_view.tab("Предложения")
        proposals_frame.grid_columnconfigure(0, weight=1); proposals_frame.grid_rowconfigure(1, weight=1)

        proposals_controls = ctk.CTkFrame(proposals_frame, fg_color="transparent")
        proposals_controls.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        proposals_controls.grid_columnconfigure(1, weight=1)
        self.accept_all_btn = ctk.CTkButton(proposals_controls, text="✓ Принять все", command=self.accept_all_proposals, fg_color="#2E7D32", hover_color="#1B5E20")
        self.accept_all_btn.grid(row=0, column=0, padx=(0,5))
        self.reject_all_btn = ctk.CTkButton(proposals_controls, text="✗ Отклонить все", command=self.reject_all_proposals, fg_color="#D32F2F", hover_color="#B71C1C")
        self.reject_all_btn.grid(row=0, column=2, padx=(5,0))

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
            db_terms = self.db_manager.get_all_terms()
            db_proposals = list(self.db_manager.get_dictionary_proposals().values())
            self.after(0, self._populate_ui, db_terms, db_proposals)
        except Exception as e:
            gui_logger.error(f"Ошибка загрузки данных словаря: {e}", exc_info=True)
            self.after(0, messagebox.showerror, "Ошибка", f"Не удалось загрузить данные словаря: {e}")

    def _populate_ui(self, terms, proposals):
        self.all_items = sorted(terms, key=lambda x: x['english_term'].lower())
        self.all_proposals = sorted(proposals, key=lambda x: x['english_term'].lower())
        self.term_widgets = {}
        for term in self.all_items:
            term['is_new'] = False
            term['is_modified'] = False
            self.term_widgets[term['english_term']] = {'eng_var': ctk.StringVar(value=term['english_term']), 'rus_var': ctk.StringVar(value=term['russian_term'])}

        self.current_page = 1
        self.current_page_proposals = 1
        self.filter_entries()

    def on_tab_change(self):
        self.filter_entries()

    def render_page(self, terms_to_render):
        for widget in self.terms_content_frame.winfo_children(): widget.destroy()
        ctk.CTkLabel(self.terms_content_frame, text="Английский термин", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=5, pady=5, sticky="w")
        ctk.CTkLabel(self.terms_content_frame, text="Русский перевод", font=ctk.CTkFont(weight="bold")).grid(row=0, column=1, padx=5, pady=5, sticky="w")
        
        start_index = (self.current_page - 1) * self.items_per_page
        end_index = start_index + self.items_per_page
        page_terms = terms_to_render[start_index:end_index]

        for i, term_data in enumerate(page_terms):
            original_eng = term_data['english_term']
            widget_vars = self.term_widgets[original_eng]
            
            eng_entry = ctk.CTkEntry(self.terms_content_frame, textvariable=widget_vars['eng_var'])
            eng_entry.grid(row=i + 1, column=0, padx=5, pady=2, sticky="ew")
            rus_entry = ctk.CTkEntry(self.terms_content_frame, textvariable=widget_vars['rus_var'])
            rus_entry.grid(row=i + 1, column=1, padx=5, pady=2, sticky="ew")
            
            widget_vars['eng_var'].trace_add("write", lambda *args, oe=original_eng: self.mark_as_modified(oe))
            widget_vars['rus_var'].trace_add("write", lambda *args, oe=original_eng: self.mark_as_modified(oe))

            delete_btn = ctk.CTkButton(self.terms_content_frame, text="X", width=25, command=lambda oe=original_eng: self.delete_term(oe))
            delete_btn.grid(row=i + 1, column=2, padx=5, pady=2)

        total_pages = (len(terms_to_render) + self.items_per_page - 1) // self.items_per_page
        self.page_label_terms.configure(text=f"Страница {self.current_page} / {max(1, total_pages)}")
        self.prev_terms_btn.configure(state="normal" if self.current_page > 1 else "disabled")
        self.next_terms_btn.configure(state="normal" if self.current_page < total_pages else "disabled")

    def render_proposals_page(self, proposals_to_render):
        for widget in self.proposals_content_frame.winfo_children(): widget.destroy()
        ctk.CTkLabel(self.proposals_content_frame, text="Предложенный термин (Англ -> Рус)", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=5, pady=5, sticky="w")

        start_index = (self.current_page_proposals - 1) * self.items_per_page
        end_index = start_index + self.items_per_page
        page_proposals = proposals_to_render[start_index:end_index]

        for i, data in enumerate(page_proposals):
            eng = data['english_term']
            frame = ctk.CTkFrame(self.proposals_content_frame, fg_color="transparent")
            frame.grid(row=i + 1, column=0, columnspan=3, sticky="ew", pady=2)
            frame.grid_columnconfigure(0, weight=1)
            label_text = f"{eng}  →  {data['russian_term']} (conf: {data.get('confidence', 0):.2f})"
            label = ctk.CTkLabel(frame, text=label_text, anchor="w"); label.grid(row=0, column=0, sticky="ew", padx=5)
            btn_accept = ctk.CTkButton(frame, text="✓", width=25, command=lambda e=eng, d=data: self.accept_proposal(e, d['russian_term'], d['category']))
            btn_accept.grid(row=0, column=1, padx=(0, 5))
            btn_reject = ctk.CTkButton(frame, text="✗", width=25, command=lambda e=eng: self.reject_proposal(e))
            btn_reject.grid(row=0, column=2, padx=(0, 5))

        total_pages = (len(proposals_to_render) + self.items_per_page - 1) // self.items_per_page
        self.page_label_proposals.configure(text=f"Страница {self.current_page_proposals} / {max(1, total_pages)}")
        self.prev_proposals_btn.configure(state="normal" if self.current_page_proposals > 1 else "disabled")
        self.next_proposals_btn.configure(state="normal" if self.current_page_proposals < total_pages else "disabled")

    def mark_as_modified(self, original_eng):
        term = next((t for t in self.all_items if t['english_term'] == original_eng), None)
        if term: term['is_modified'] = True

    def add_new_term(self):
        new_term_id = f"__new_{len([t for t in self.all_items if t.get('is_new')])}"
        new_term = {'english_term': new_term_id, 'russian_term': '', 'category': 'other', 'is_new': True, 'is_modified': True}
        self.all_items.insert(0, new_term)
        self.term_widgets[new_term_id] = {'eng_var': ctk.StringVar(value=''), 'rus_var': ctk.StringVar(value='')}
        self.filter_entries()

    def save_all_terms(self):
        try:
            for term_data in self.all_items:
                if not term_data.get('is_modified'): continue
                
                original_eng = term_data['english_term']
                widget_vars = self.term_widgets[original_eng]
                new_eng = widget_vars['eng_var'].get().strip()
                new_rus = widget_vars['rus_var'].get().strip()

                if term_data.get('is_new'):
                    if new_eng and new_rus:
                        self.db_manager.add_or_update_term(new_eng, new_rus, term_data['category'])
                elif new_eng and new_rus:
                    if original_eng != new_eng:
                        self.db_manager.delete_term(original_eng)
                    self.db_manager.add_or_update_term(new_eng, new_rus, term_data['category'])
                else: # Term was deleted
                    self.db_manager.delete_term(original_eng)

            gui_logger.info("Словарь сохранен."); threading.Thread(target=self._load_data_thread, daemon=True).start()
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить словарь: {e}")
            gui_logger.error(f"Ошибка сохранения словаря: {e}", exc_info=True)

    def delete_term(self, original_eng):
        self.all_items = [t for t in self.all_items if t['english_term'] != original_eng]
        self.term_widgets.pop(original_eng, None)
        self.db_manager.delete_term(original_eng) # Direct delete for simplicity
        self.filter_entries()

    def accept_proposal(self, eng, rus, cat):
        self.db_manager.add_or_update_term(eng, rus, cat)
        self.db_manager.delete_dictionary_proposal(eng)
        threading.Thread(target=self._load_data_thread, daemon=True).start()
    def reject_proposal(self, eng):
        self.db_manager.delete_dictionary_proposal(eng)
        threading.Thread(target=self._load_data_thread, daemon=True).start()
    def accept_all_proposals(self):
        if messagebox.askyesno("Подтверждение", "Принять все видимые предложения?"):
            for p in self.all_proposals:
                self.db_manager.add_or_update_term(p['english_term'], p['russian_term'], p['category'])
            self.db_manager.clear_dictionary_proposals()
            threading.Thread(target=self._load_data_thread, daemon=True).start()
    def reject_all_proposals(self):
        if messagebox.askyesno("Подтверждение", "Отклонить все предложения?"):
            self.db_manager.clear_dictionary_proposals()
            threading.Thread(target=self._load_data_thread, daemon=True).start()

    def filter_entries(self, *args):
        query = self.search_var.get().lower()
        
        # Фильтрация для Словаря
        filtered_terms = [t for t in self.all_items if query in t['english_term'].lower() or query in self.term_widgets[t['english_term']]['rus_var'].get().lower()]
        self.current_page = 1
        self.render_page(filtered_terms)

        # Фильтрация для Предложений
        filtered_proposals = [p for p in self.all_proposals if query in p['english_term'].lower() or query in p['russian_term'].lower()]
        self.current_page_proposals = 1
        self.render_proposals_page(filtered_proposals)

    def change_page(self, delta):
        query = self.search_var.get().lower()
        filtered_terms = [t for t in self.all_items if query in t['english_term'].lower() or query in self.term_widgets[t['english_term']]['rus_var'].get().lower()]
        total_pages = (len(filtered_terms) + self.items_per_page - 1) // self.items_per_page
        new_page = self.current_page + delta
        if 1 <= new_page <= total_pages:
            self.current_page = new_page
            self.render_page(filtered_terms)

    def change_page_proposals(self, delta):
        query = self.search_var.get().lower()
        filtered_proposals = [p for p in self.all_proposals if query in p['english_term'].lower() or query in p['russian_term'].lower()]
        total_pages = (len(filtered_proposals) + self.items_per_page - 1) // self.items_per_page
        new_page = self.current_page_proposals + delta
        if 1 <= new_page <= total_pages:
            self.current_page_proposals = new_page
            self.render_proposals_page(filtered_proposals)
