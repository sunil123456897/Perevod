
import unittest
from unittest.mock import patch, MagicMock
import os
import google.generativeai as genai
import logging
import shutil

# Mock the environment variable before other imports
os.environ['GOOGLE_API_KEY'] = 'test_api_key'

from Perevod.graph_runner import run_translation_workflow
from Perevod.agents.state import AgentState
from Perevod.config import DEFAULT_SETTINGS

class TestGraphRunner(unittest.TestCase):

    @patch('graph_runner.DatabaseManager')
    @patch('Perevod.graph_runner.KnowledgeBaseManager')
    @patch('google.generativeai.GenerativeModel')
    def test_full_graph_execution(self, mock_db_manager, mock_kb_manager, mock_genai_model):
        """
        Тестирует полный прогон графа с моками зависимостей.
        Проверяет, что граф успешно доходит до конца без ошибок.
        """
        # 1. Настройка моков
        # Mock для DatabaseManager
        mock_db_instance = MagicMock()
        mock_db_instance.get_all_terms.return_value = [{'english_term': 'test', 'russian_term': 'тест'}]
        mock_db_instance.get_all_world_bible_entries.return_value = [{'english_name': 'world', 'description': 'a world'}]
        mock_db_manager.return_value = mock_db_instance

        # Mock для KnowledgeBaseManager
        mock_kb_instance = MagicMock()
        mock_kb_instance.collection.count.return_value = 1 
        mock_kb_manager.return_value = mock_kb_instance

        # Mock для LLM (genai.GenerativeModel)
        class MockResponse:
            def __init__(self, text_content):
                self.text = text_content

        mock_llm_instance = MagicMock()
        # Моделируем ответ для этапа перевода
        mock_llm_instance.generate_content.side_effect = [
            MockResponse("Translated text."), # Ответ для pre_translate_node
            MockResponse('{"inconsistencies": []}')         # Ответ для quality_assurance_node
        ]
        mock_genai_model.return_value = mock_llm_instance

        

        # 2. Подготовка тестовых данных и настроек
        project_name = "test_project"
        
        # Создаем временные директории для теста
        input_dir = os.path.join('_project_files', project_name, 'input')
        output_dir = os.path.join('_project_files', project_name, 'output')
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        # Создаем тестовый файл
        with open(os.path.join(input_dir, "chapter1.txt"), "w", encoding="utf-8") as f:
            f.write("This is a test chapter.")

        settings_overrides = DEFAULT_SETTINGS.copy()
        settings_overrides.update({
            "input_dir": input_dir,
            "output_dir": output_dir,
            "project_name": project_name,
            "enable_smart_dictionary_update": False, # Отключаем, чтобы не усложнять тест
            "enable_bible_analysis": False,
            "overwrite_existing": True,
        })

        # 3. Запуск графа
        final_state = run_translation_workflow(settings_overrides, mock_db_instance, mock_kb_instance, mock_llm_instance)

        # 4. Проверка результатов
        self.assertIsNotNone(final_state)
        self.assertNotIn("error", final_state, f"Граф завершился с ошибкой: {final_state.get('error')}")
        self.assertIn("processed_chapters", final_state)
        self.assertEqual(len(final_state["processed_chapters"]), 1)
        self.assertEqual(final_state["processed_chapters"][0]['title'], "chapter1")
        
        # Проверяем, что выходной файл был создан
        output_file_path = os.path.join(output_dir, "chapter1.txt")
        self.assertTrue(os.path.exists(output_file_path))
        
        # Проверяем, что моки были вызваны
        self.assertGreater(mock_llm_instance.generate_content.call_count, 0)

        # Очистка после теста
        os.remove(os.path.join(input_dir, "chapter1.txt"))
        os.remove(output_file_path)
        os.rmdir(input_dir)
        os.rmdir(output_dir)
        shutil.rmtree(os.path.join('_project_files', project_name, 'chroma_db'), ignore_errors=True)
        os.rmdir(os.path.join('_project_files', project_name))


if __name__ == '__main__':
    unittest.main()
