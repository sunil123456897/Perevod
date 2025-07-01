# cli.py

import os
import sys
import time
import logging

from graph_runner import execute_graph_step_by_step as execute_graph

logger = logging.getLogger("NovelTranslator.CLI")

def run_cli_translation(project_settings):
    """
    Основная функция для запуска перевода из командной строки.
    Принимает полный словарь настроек проекта.
    """
    project_name = project_settings.get('project_name', 'N/A')
    logger.info(f"--- Режим командной строки: Перевод проекта '{project_name}' ---")
    
    # <-- ИЗМЕНЕНО: Проверки теперь основаны на настройках проекта -->
    if not project_settings.get('api_key'):
        logger.critical("Ошибка: API ключ не указан в проекте или через аргумент --api-key.")
        sys.exit(1)
    
    input_dir = project_settings.get('input_dir')
    output_dir = project_settings.get('output_dir')

    if not input_dir or not output_dir:
        logger.critical(f"Ошибка: В настройках проекта '{project_name}' не указаны 'input_dir' и/или 'output_dir'.")
        sys.exit(1)
    if not os.path.isdir(input_dir):
        logger.critical(f"Ошибка: Директория исходных файлов не найдена: {input_dir}")
        sys.exit(1)
    if not os.path.exists(output_dir):
        try:
            os.makedirs(output_dir)
            logger.info(f"Создана директория для переводов: {output_dir}")
        except Exception as e:
            logger.critical(f"Ошибка создания выходной директории: {e}")
            sys.exit(1)

    try:
        start_time_cli = time.monotonic()
        
        final_state = execute_graph(project_name, settings_overrides=project_settings)
        
        if final_state.get("error"):
            raise Exception(final_state["error"])
        
        duration_cli = time.monotonic() - start_time_cli
        processed_count = len(final_state.get("processed_chapters", []))
        logger.info(f"--- Перевод завершен успешно за {duration_cli:.2f} секунд ({processed_count} глав) ---")

    except Exception as e:
        logger.critical(f"Критическая ошибка во время перевода: {e}", exc_info=True)
        sys.exit(1)

# <-- УДАЛЕНО: Функция run_cli_dictionary_editor больше не нужна -->