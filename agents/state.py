from typing import TypedDict, Annotated, List, Dict, Any
from operator import add

# Определяем структуру данных, которая будет передаваться между всеми узлами графа.
# Это единый источник правды для всего рабочего процесса.
class AgentState(TypedDict):
    # Входные данные и конфигурация
    project_name: str
    project_settings: Dict[str, Any]

    # Очередь задач и результаты
    chapters_to_process: List[Dict[str, str]] # Список словарей с путями к файлам
    processed_chapters: Annotated[list, add] # LangGraph будет автоматически объединять списки
    
    # Данные для текущей итерации
    current_chapter_data: Dict[str, Any] # {'eng_text': ..., 'rus_text': ..., 'title': ...}
    
    # Служебные поля
    error: str | None # Для записи ошибок в процессе выполнения
    
    # Менеджеры данных, которые будут передаваться между узлами
    # Мы не будем их сериализовать, а передадим в `configurable`
    db_manager: Any 
    kb_manager: Any
    
    # --- НОВЫЕ ПОЛЯ ДЛЯ ФАЗЫ 3 ---
    # Для хранения отчета от агента контроля качества
    quality_assurance_report: Dict[str, Any] | None
    # Для передачи решения пользователя обратно в граф
    user_decision: Dict[str, Any] | None

    # --- НОВОЕ ПОЛЕ ДЛЯ ФАЗЫ 4 ---
    # Для хранения вердикта от "Коллегии Судей"
    evaluation_verdict: Dict[str, Any] | None