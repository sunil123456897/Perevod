# cli.py

import os
import sys
import time
import logging
from translator import NovelTranslator, InitializationError

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
        logger.critical(f"Ошибка: Директория исходных файлов не найдена: {input_dir}"); sys.exit(1)
    if not os.path.exists(output_dir):
        try:
            os.makedirs(output_dir)
            logger.info(f"Создана директория для переводов: {output_dir}")
        except Exception as e:
            logger.critical(f"Ошибка создания выходной директории: {e}"); sys.exit(1)

    # <-- ИЗМЕНЕНО: Получаем пути к файлам данных из translator.py -->
    # Создаем временный экземпляр, чтобы получить пути к файлам БЗ
    try:
        temp_translator = NovelTranslator(settings=project_settings)
        project_settings['dictionary_path'] = temp_translator.DICTIONARY_PATH
        project_settings['world_bible_path'] = temp_translator.WORLD_BIBLE_PATH
        project_settings['world_bible_proposals_path'] = temp_translator.WORLD_BIBLE_PROPOSALS_PATH
    except InitializationError as e:
        logger.critical(f"Ошибка при определении путей проекта: {e}")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"Непредвиденная ошибка: {e}")
        sys.exit(1)


    try:
        translator = NovelTranslator(settings=project_settings)
        start_time_cli = time.monotonic()
        
        last_status_len = 0
        def progress_callback_cli(progress, status):
            nonlocal last_status_len
            progress = max(0.0, min(100.0, progress))
            bar_len = 40
            filled_len = int(bar_len * bar_len * progress / 100)
            bar = '█' * filled_len + '-' * (bar_len - filled_len)
            status_line = f'\rПрогресс: |{bar}| {progress:.1f}% - {status[:70]:<70}'
            print(status_line, end="", flush=True)
            last_status_len = len(status_line)
            if progress >= 100:
                print()

        # <-- ИЗМЕНЕНО: Логика построения индекса для CLI -->
        if not translator.semantic_index:
            logger.warning("Семантический индекс не найден или пуст.")
            if input("Хотите построить семантический индекс сейчас для лучшего качества перевода? (y/n): ").lower() == 'y':
                translator.build_semantic_index(progress_callback_cli)
        
        process_method = translator.process_novel_parallel if project_settings.get('parallel_translation') else translator.process_novel
        success, _ = process_method(input_dir, output_dir, progress_callback_cli)

        duration_cli = time.monotonic() - start_time_cli
        logger.info(f"--- Перевод {'завершен успешно' if success else 'завершен с ошибками'} за {duration_cli:.2f} секунд ---")

        stats = translator.get_statistics()
        logger.info("\n--- Статистика ---")
        for key, value in stats.items():
            logger.info(f"  {key.replace('_', ' ').capitalize()}: {value}")
        logger.info("-" * 20)

    except (InitializationError, Exception) as e:
        logger.critical(f"Критическая ошибка во время перевода: {e}", exc_info=True)
        sys.exit(1)

# <-- УДАЛЕНО: Функция run_cli_dictionary_editor больше не нужна -->