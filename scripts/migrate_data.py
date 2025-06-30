# scripts/migrate_data.py

import os
import json
import sys
import logging

# Добавляем корневую директорию в путь, чтобы импортировать модули приложения
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from database.database_manager import DatabaseManager
from knowledge_base.knowledge_base_manager import KnowledgeBaseManager
from config import DEFAULT_SETTINGS, setup_logging

def migrate_project(project_name, project_config, base_dir):
    """Мигрирует один проект из JSON в SQLite и ChromaDB."""
    print(f"\n--- Начало миграции проекта: '{project_name}' ---")
    
    # 1. Создаем менеджеров для нового проекта
    # Для миграции нам нужен API ключ для создания эмбеддингов
    api_key = project_config.get('api_key') or DEFAULT_SETTINGS.get('api_key')
    if not api_key:
        print(f"!!! ОШИБКА: API ключ не найден для проекта '{project_name}'. Пропускаем.")
        return

    db_manager = DatabaseManager(project_name=project_name)
    kb_manager = KnowledgeBaseManager(
        project_name=project_name, 
        api_key=api_key,
        embedding_model_name=DEFAULT_SETTINGS['embedding_model_name']
    )
    
    # 2. Сохраняем настройки проекта
    db_manager.update_project_settings(project_config)
    print(f"[OK] Настройки проекта сохранены в SQLite.")

    # 3. Мигрируем словарь
    dict_path = os.path.join(base_dir, project_name, 'terms_dictionary.json')
    if os.path.exists(dict_path):
        with open(dict_path, 'r', encoding='utf-8') as f:
            dictionary = json.load(f)
        for eng, rus in dictionary.items():
            db_manager.add_or_update_term(eng, rus)
        print(f"[OK] Словарь мигрирован: {len(dictionary)} терминов.")

    # 4. Мигрируем Библию Вселенной
    bible_path = os.path.join(base_dir, project_name, 'world_bible.json')
    if os.path.exists(bible_path):
        with open(bible_path, 'r', encoding='utf-8') as f:
            bible = json.load(f)
        for name, data in bible.items():
            db_manager.add_or_update_bible_entry(name, data)
        print(f"[OK] Библия Вселенной мигрирована: {len(bible)} записей.")
    
    # 5. Перестраиваем семантический индекс в ChromaDB
    print(f"[*] Запуск перестройки семантического индекса в ChromaDB...")
    kb_manager.rebuild_index_from_db(db_manager)
    print(f"[OK] Семантический индекс построен.")
    
    print(f"--- Миграция проекта '{project_name}' успешно завершена ---")


def main():
    """Главная функция для запуска миграции."""
    setup_logging()
    
    projects_json_path = 'translation_projects.json'
    if not os.path.exists(projects_json_path):
        print(f"Файл '{projects_json_path}' не найден. Миграция не требуется.")
        return

    with open(projects_json_path, 'r', encoding='utf-8') as f:
        all_projects_config = json.load(f)
    
    base_data_dir = '_project_files'
    
    print("="*50)
    print("ЗАПУСК МИГРАЦИИ ДАННЫХ ИЗ JSON В SQLITE & CHROMADB")
    print("Это одноразовая операция.")
    print("="*50)
    
    for project_name, project_config in all_projects_config.items():
        migrate_project(project_name, project_config, base_data_dir)
        
    print("\n\nМиграция всех проектов завершена.")
    print(f"Теперь вы можете удалить старый файл '{projects_json_path}'.")
    print("Старые JSON-файлы внутри папок проектов в '_project_files' также можно удалить после проверки.")

if __name__ == "__main__":
    main()
