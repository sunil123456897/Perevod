

import unittest
import unittest
from unittest.mock import MagicMock, patch
import os
import sys
import json



from agents.state import AgentState
from agents.nodes import evaluation_node, apply_fixes_node

class TestQAPipeline(unittest.TestCase):

    def setUp(self):
        # Мокаем внешние зависимости
        self.mock_db_manager = MagicMock()
        self.mock_model = MagicMock()
        
        # Мокаем genai.configure и genai.GenerativeModel
        self.patcher_genai_configure = patch('google.generativeai.configure')
        self.mock_genai_configure = self.patcher_genai_configure.start()
        self.patcher_generative_model = patch('google.generativeai.GenerativeModel', return_value=self.mock_model)
        self.mock_generative_model = self.patcher_generative_model.start()

        # Мокаем logger
        self.patcher_logger_info = patch('logging.Logger.info')
        self.mock_logger_info = self.patcher_logger_info.start()

        self.default_settings = {
            "api_key": "test_api_key",
            "model_name": "gemini-pro",
        }
        
        self.sample_report = {
            "inconsistencies": [
                {
                    "english_term": "Council",
                    "russian_variants": ["Совет", "Совета"],
                    "context": [
                        "The Council decided.",
                        "Member of the Совета."
                    ]
                },
                {
                    "english_term": "Magic",
                    "russian_variants": ["магия", "волшебство"],
                    "context": [
                        "It's real магия.",
                        "The power of волшебство."
                    ]
                }
            ]
        }

    def tearDown(self):
        self.patcher_genai_configure.stop()
        self.patcher_generative_model.stop()
        self.patcher_logger_info.stop()

    def test_evaluation_node_generates_fixes(self):
        """
        Тестирует, что evaluation_node корректно генерирует план исправлений.
        """
        mock_response_text = json.dumps({
            "chosen_variant": "Совет"
        })
        mock_response_text_magic = json.dumps({
            "chosen_variant": "волшебство"
        })
        self.mock_model.generate_content.side_effect = [
            MagicMock(text=f"```json\n{mock_response_text}\n```"),
            MagicMock(text=f"```json\n{mock_response_text_magic}\n```"),
        ]

        initial_state = AgentState(
            project_name="test_project",
            project_settings=self.default_settings,
            quality_assurance_report=self.sample_report,
            db_manager=self.mock_db_manager,
            model=self.mock_model
        )
        
        result_state = evaluation_node(initial_state)

        self.assertIn("unification_verdicts", result_state)
        self.assertIsNotNone(result_state["unification_verdicts"])
        self.assertEqual(len(result_state["unification_verdicts"]), 2)
        self.assertEqual(result_state["unification_verdicts"][0]["correct_variant"], "Совет")
        self.assertEqual(result_state["unification_verdicts"][1]["correct_variant"], "волшебство")
        self.assertEqual(self.mock_model.generate_content.call_count, 2)

    @patch('agents.tools.tool_read_chapter')
    @patch('agents.tools.tool_write_chapter')
    def test_apply_fixes_node_replaces_text(self, mock_write_chapter, mock_read_chapter):
        """
        Тестирует, что apply_fixes_node корректно заменяет текст в главах.
        """
        verdicts = [
            {"correct_variant": "Совет", "russian_variants": ["Совета"]},
            {"correct_variant": "волшебство", "russian_variants": ["магия"]}
        ]
        
        processed_chapters = [
            {"title": "Chapter 1", "output_path": "output/chapter1.txt"},
            {"title": "Chapter 2", "output_path": "output/chapter2.txt"}
        ]

        # Мокаем чтение файлов
        mock_read_chapter.side_effect = [
            "Член Совета решил. Это была магия.",
            "Решение Совета. Сила магии."
        ]

        initial_state = AgentState(
            project_name="test_project",
            project_settings=self.default_settings,
            processed_chapters=processed_chapters,
            unification_verdicts=verdicts,
            db_manager=self.mock_db_manager
        )

        result_state = apply_fixes_node(initial_state)

        self.assertEqual(mock_write_chapter.call_count, 2)
        # Проверяем, что в файл записывается исправленный текст
        self.assertEqual(mock_write_chapter.call_args_list[0][0][1], "Член Совет решил. Это была волшебство.")
        self.assertEqual(mock_write_chapter.call_args_list[1][0][1], "Решение Совет. Сила волшебство.")

    def test_apply_fixes_node_no_plan(self):
        """
        Тестирует, что узел не делает ничего, если нет плана исправлений.
        """
        processed_chapters = [{"title": "Chapter 1", "rus_text": "Исходный текст."}]
        initial_state = AgentState(
            project_name="test_project",
            project_settings=self.default_settings,
            processed_chapters=processed_chapters,
            fixes_plan=None # Нет плана
        )

        result_state = apply_fixes_node(initial_state)
        
        # Текст не должен измениться
        self.assertEqual(result_state["processed_chapters"][0]["rus_text"], "Исходный текст.")

if __name__ == '__main__':
    unittest.main()

