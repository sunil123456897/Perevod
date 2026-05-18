# Спецификация дизайна: Perevod 3.0 "Пайплайн Великого Архивариуса"

**Дата:** 2026-05-03  
**Версия:** 1.0  
**Статус:** Draft  
**Цель:** Описать архитектуру многоагентной системы перевода с глубокой интеграцией RAG и самокоррекцией.

---

## 1. Обзор (Overview)
Perevod 3.0 переходит от линейного перевода к циклическому агентному пайплайну. Система имитирует работу профессионального издательства: сбор контекста -> анализ -> перевод -> критика -> редактура -> архивация знаний.

### Основные агенты:
1.  **Chronicle Agent (Летописец):** Управляет Библией Вселенной (World Bible).
2.  **Translator Agent (Переводчик):** Создает черновой литературный перевод.
3.  **Judge Agent (Судья):** Критикует перевод на основе словаря, стиля и логики.
4.  **Editor Agent (Редактор):** Исправляет текст на основе замечаний Судьи.

---

## 2. Адаптивная архитектура (LangGraph)

Система оптимизирована для работы с Free-tier API за счет **условных переходов**. Агенты вызываются только тогда, когда это необходимо.

### Узлы (Nodes):
1.  **`context_retrieval`**: (Кэшируемо) Загрузка Библии и Rolling Memory (3 главы).
2.  **`pre_analysis`**: Летописец ищет новые сущности.
3.  **`translate`**: Основной переводчик создает черновик.
4.  **`judge`**: Судья оценивает качество (Severity-based).
5.  **`refine`**: (Условно) Редактор исправляет блокирующие ошибки.
6.  **`post_analysis`**: Сохранение фактов со статусом `candidate`.

### Логика графа (Conditional Edge):
```python
if judge.has_blocking_issues and refinement_count < 2:
    return "refine"
else:
    return "post_analysis"
```

---

## 3. Спецификация Агентов и Промпты

### 3.1. Judge Agent (Critique)
**Задача:** Проверка по измеримым критериям.
**JSON Output:**
{
  "pass": bool,
  "severity": "low|medium|high|critical",
  "blocking_issues": ["Список критических ошибок"],
  "suggestions": ["Рекомендации по стилю"],
  "score": 0-10
}

### 3.2. Chronicle Agent (Post-Analysis)
**Задача:** Обновление базы знаний.
**Статус данных:** Все новые записи помечаются как `status: candidate`. Они становятся `status: verified` только после ручного одобрения в GUI или подтверждения в 3-х последующих главах.


### 3.3. Editor Agent (Refine)
**Задача:** Исправить текст, сохранив литературность.
**Prompt:**
```text
You are the Senior Editor. Correct the Russian translation based on the Judge's feedback.
Feedback: {issues}

Guidelines:
- Maintain the style of a high-quality fantasy novel.
- Fix all technical issues mentioned by the Judge.
- Do NOT add unnecessary AI-slop.
```

---

## 4. Структура данных (State)

Обновить `AgentState` в `src/Perevod/agents/state.py`:
```python
class AgentState(TypedDict):
    # ... existing ...
    rag_context: List[str]          # Данные из Библии и памяти
    current_draft: str              # Текущий черновик главы
    critique_history: List[str]     # История замечаний Судьи
    refinement_count: int           # Счетик итераций
    final_translation: str          # Финальный текст
```

---

## 5. Стратегия тестирования

1.  **Unit-тесты для Агентов:** Проверка парсинга JSON ответов.
2.  **Integration-тест графа:** Прогон одной главы через все 5 этапов.
3.  **RAG-тест:** Убедиться, что информация из главы 1 попадает в контекст главы 2.

---

## 6. Самопроверка (Self-Review)
- **Placeholders:** Все промпты описаны.
- **Ambiguity:** Логика цикла (макс. 2 итерации) зафиксирована.
- **Consistency:** SQLite и ChromaDB используются согласованно (SQLite для структуры, Chroma для поиска).
