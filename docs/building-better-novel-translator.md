# Как спроектировать более сильный автоматический переводчик новелл

Цель документа: описать, как с нуля построить похожий проект, но архитектурно чище, надежнее и ближе к продукту, который переводит главы автоматически, сохраняет память о мире и со временем улучшает качество без постоянного участия человека.

## 1. Главная идея продукта

Система должна принимать папку с исходными главами и выдавать готовые русские главы с сохранением:

- смысла и сюжетной логики;
- терминологии;
- имен, рангов, техник, артефактов и географии;
- стиля конкретной новеллы;
- памяти о предыдущих главах;
- проверяемого отчета по каждой главе.

Пользовательский сценарий должен быть простым:

1. Создать проект.
2. Указать входную папку.
3. Указать выходную папку.
4. Нажать "Перевести".
5. Получить готовые главы и отчет.

Все сложное: анализ, словарь, память, повторы API, кэш, проверка качества и восстановление после сбоев должны быть внутри системы.

## 2. Архитектурные принципы

### Обязательные принципы

- Перевод главы выполняется целиком, если лимиты модели это позволяют.
- Любой результат пишется атомарно: сначала временный файл, потом rename.
- Нельзя логировать "успешно", если хотя бы одна критичная стадия упала.
- Каждая глава имеет отдельный статус: `pending`, `processing`, `translated`, `qa_failed`, `failed`, `skipped`.
- Workflow должен уметь продолжать с места сбоя.
- Все вызовы внешних API проходят через один общий gateway.
- Все временные ошибки API повторяются с backoff.
- Лимиты бесплатных моделей учитываются до вызова API, а не после ошибки.
- Память проекта не должна зависеть только от эмбеддингов. Нужен lexical fallback.
- Данные проекта должны быть отделены от кода.

### Чего избегать

- Разбросанных прямых вызовов SDK по разным агентам.
- Молчаливых fallback, которые скрывают ошибку.
- Неограниченных retry.
- Повторного эмбеддинга одного и того же текста.
- Параллельного перевода в один output без lock.
- Смешивания GUI-логики, бизнес-логики и workflow в одном классе.
- Хранения критичных данных только в ChromaDB без структурной копии в SQLite.

## 3. Рекомендуемая структура проекта

```text
novel_translator/
  app/
    cli.py
    gui/
    main.py
  core/
    workflow.py
    state.py
    errors.py
    settings.py
  llm/
    gateway.py
    retry.py
    rate_limits.py
    prompts.py
    models.py
  translation/
    analyzer.py
    translator.py
    judge.py
    editor.py
    summarizer.py
  memory/
    world_bible.py
    glossary.py
    rolling_memory.py
    vector_store.py
    lexical_search.py
  storage/
    db.py
    repositories.py
    migrations/
  io/
    chapter_reader.py
    atomic_writer.py
    file_lock.py
  observability/
    logging.py
    reports.py
    metrics.py
  tests/
```

Ключевая идея: UI и CLI только запускают workflow. Они не должны сами переводить, обновлять словарь или писать память.

## 4. Основной workflow

Рекомендуемый граф:

```text
discover_chapters
  -> acquire_lock
  -> load_project_context
  -> for each chapter:
       load_chapter
       restore_or_create_chapter_run
       retrieve_context
       analyze_terms
       curate_glossary
       translate_whole_chapter
       judge_translation
       refine_if_needed
       write_output
       summarize_chapter
       update_memory
       write_report
  -> release_lock
```

Условная логика:

```text
if output_exists and not overwrite:
    skip

if translate fails with retryable API/network error:
    retry with bounded backoff

if translate fails with quota/auth/model-not-found error:
    fail fast with an actionable report entry

if translate fails after retry budget:
    mark chapter failed
    stop or continue depending on mode

if judge finds critical issues:
    run editor

if editor still fails judge:
    save draft separately and mark qa_failed
```

Для автономного режима лучше по умолчанию останавливать workflow на первой критичной ошибке, чтобы не производить пачку плохих переводов.

## 5. Данные проекта

### SQLite как источник истины

SQLite должна хранить:

- проекты;
- настройки проекта;
- список глав;
- статусы запусков;
- словарь терминов;
- историю решений по терминам;
- краткие пересказы глав;
- отчеты качества;
- счетчики API;
- кэш ответов, если это разрешено политикой проекта.

### ChromaDB или другой vector store

Vector store должен хранить только поисковый индекс:

- записи world bible;
- термины;
- краткую память;
- дополнительные лор-записи.

Нельзя считать vector store единственным хранилищем знаний. Индекс можно пересоздать из SQLite.

### Минимальные таблицы

```text
projects(id, name, created_at, updated_at)
chapters(id, project_id, source_path, title, chapter_number, status, hash)
chapter_runs(id, chapter_id, started_at, finished_at, status, error)
glossary_terms(id, project_id, source_term, target_term, category, confidence, status)
world_bible_entries(id, project_id, title, body, type, status, source_chapter)
chapter_summaries(id, chapter_id, summary, created_at)
api_usage(id, date, model, operation, attempts, successful_calls, failed_calls)
embedding_cache(cache_key, model, task_type, embedding_json)
```

## 6. LLM Gateway

Все обращения к Gemini или другой модели должны идти через один слой:

```python
class LLMGateway:
    def generate_text(task, prompt, model=None, timeout=None) -> LLMResponse:
        ...

    def embed(texts, task_type) -> list[list[float]]:
        ...
```

Gateway отвечает за:

- выбор модели по задаче;
- retries;
- timeout;
- rate limiting;
- daily budget;
- логирование без утечки ключей;
- нормализацию ошибок;
- измерение времени;
- возврат структурированного результата.

Агенты не должны знать детали SDK.

## 7. Политика retry

Повторять можно:

- `500 Internal Server Error`;
- `502 Bad Gateway`;
- `503 Unavailable`;
- `504 Gateway Timeout`;
- сетевые timeout;
- временные DNS/proxy сбои.

Не повторять автоматически:

- `400 Bad Request`;
- `401/403 Auth`;
- `404 Model not found`;
- `429 RESOURCE_EXHAUSTED`, если это дневной лимит;
- ошибки схемы ответа, если повтор не изменит ситуацию.

Важное уточнение: дневной quota exhausted нельзя лечить коротким retry. Если система уже знает дневной лимит, она должна остановить запрос до обращения к API и записать понятную причину в отчет.

Рекомендуемый backoff:

```text
10s -> 20s -> 40s -> 80s
```

Для бесплатного API лучше иметь общий лимитер:

```text
Gemini 3 Flash: 5 RPM, 20 RPD
Gemini 3.1 Flash Lite: 15 RPM, 500 RPD
Gemini Embedding 2: 100 RPM, 1000 RPD
```

Если дневной лимит исчерпан, система должна остановиться с понятным сообщением и не пытаться "дожать" API.

## 8. Модели и роли

Для free-tier режима:

- `translation`: самая качественная доступная text-out модель.
- `judge`: быстрая модель, если она достаточно хорошо ловит ошибки.
- `analysis`: дешевая быстрая модель.
- `curation`: быстрая модель.
- `summarization`: быстрая модель.
- `embedding`: актуальная embedding-модель.

Важно: имена моделей меняются. Их нельзя зашивать по всему коду. Нужен один config и doctor-команда, которая умеет показать доступные модели и методы.

## 9. Качество перевода

### Переводчик

Translator получает:

- полный текст главы;
- краткую память последних глав;
- релевантные записи world bible;
- словарь терминов;
- стиль проекта;
- жесткие правила вывода.

Он должен вернуть только готовый русский текст без markdown, объяснений и комментариев.

### Judge

Judge должен проверять:

- пропуски абзацев;
- неверные имена;
- конфликт терминов;
- потерю смысла;
- машинную сухость;
- нарушение стиля;
- добавленные несуществующие факты;
- незавершенные предложения;
- мусорные вставки модели.

Результат Judge должен быть JSON:

```json
{
  "pass": true,
  "score": 8,
  "severity": "low",
  "blocking_issues": [],
  "suggestions": []
}
```

Если JSON невалидный, это ошибка проверки, а не успех.

### Editor

Editor запускается только если Judge нашел blocking issues. Он не должен переписывать всю главу без необходимости. Его задача: исправить конкретные ошибки.

## 10. Память и самоулучшение

Самоулучшение должно быть контролируемым:

1. Система извлекает новые термины и факты.
2. Новые знания получают статус `candidate`.
3. Если факт подтверждается в следующих главах или не конфликтует со словарем, статус повышается.
4. Конфликтующие термины не перезаписываются молча.
5. Все изменения словаря сохраняют причину и источник.

Хорошая память состоит из трех слоев:

- `glossary`: точные термины и имена;
- `world_bible`: факты мира, персонажи, организации, техники;
- `rolling_memory`: краткий пересказ последних глав.

Для rolling memory не обязательно тратить embedding-квоту. Ее можно доставать по `chapter_number`.

## 11. Кэширование

Нужно кэшировать:

- эмбеддинги;
- результаты анализа главы по hash исходного текста;
- переводы только если prompt и настройки совпадают;
- списки моделей doctor-команды;
- retrieval-результаты на время одного запуска.

Ключ кэша должен включать:

- модель;
- задачу;
- hash prompt/input;
- версию prompt;
- настройки генерации.

Если prompt изменился, старый кэш нельзя использовать как будто он актуален.

## 12. Восстановление после сбоев

Каждая глава должна иметь checkpoint:

```text
chapter_loaded
context_retrieved
analysis_done
glossary_updated
translation_done
judge_done
output_written
memory_updated
```

Если сбой произошел после перевода, но до памяти, повторный запуск должен не переводить главу заново, а продолжить с записи памяти.

Если сбой произошел во время API 503, система должна повторить вызов. Если retry исчерпан, глава остается `failed`, а уже готовые предыдущие главы не трогаются.

## 13. CLI, GUI и Doctor

### CLI

Минимальные команды:

```bat
translator doctor --project Fermer --input-dir input --output-dir output --check-api
translator translate --project Fermer --input-dir input --output-dir output
translator translate --project Fermer --input-dir input --output-dir output --overwrite
translator retry-failed --project Fermer
translator rebuild-index --project Fermer
```

### GUI

GUI должен быть оболочкой над теми же командами:

- выбор проекта;
- выбор папок;
- кнопка запуска;
- прогресс по главам;
- список ошибок;
- просмотр словаря;
- просмотр world bible;
- просмотр отчета качества.

GUI не должен содержать бизнес-логику workflow.

### Doctor

Doctor должен проверять:

- Python;
- зависимости;
- `.env`;
- API key;
- доступность Gemini;
- список доступных моделей;
- поддержку `generateContent` и `embedContent`;
- input/output paths;
- права записи;
- состояние SQLite;
- состояние vector store;
- proxy/DNS.

## 14. Observability

Логи должны отвечать на вопросы:

- какая глава обрабатывается;
- какая модель вызвана;
- сколько попыток API было;
- сколько секунд занял вызов;
- какой статус главы;
- где лежит output;
- почему глава упала.

Нужен машинный отчет:

```json
{
  "project": "Fermer",
  "started_at": "...",
  "finished_at": "...",
  "processed_count": 1,
  "failed_count": 0,
  "chapters": [
    {
      "title": "Chapter 585 ...",
      "status": "translated",
      "output_path": "...",
      "judge_score": 8,
      "api_attempts": 4
    }
  ]
}
```

## 15. Тестирование

Минимальный набор тестов:

- парсинг глав;
- сортировка глав по номеру;
- atomic write;
- file lock;
- retry policy;
- rate limit;
- daily quota guard;
- JSON parsing от LLM;
- Judge invalid JSON fail-fast;
- workflow не пишет fake success;
- resume после partial failure;
- glossary conflict resolution;
- embedding cache;
- lexical fallback;
- rebuild vector index from SQLite.

Интеграционные live-тесты с Gemini должны быть отдельными и выключенными по умолчанию, чтобы не тратить бесплатную квоту.

## 16. Безопасность

Обязательно:

- не логировать API key;
- не писать secrets в отчеты;
- валидировать input/output paths;
- запрещать output в системные директории;
- защищаться от path traversal при именах файлов;
- не удалять пользовательские файлы без явного флага;
- делать backup перед массовым overwrite;
- ограничить размер входной главы, если модель не выдержит контекст.

## 17. Производительность и квоты

Самые дорогие места:

- перевод главы;
- Judge;
- эмбеддинги;
- повторная обработка уже готовых глав;
- лишний retrieval.

Оптимизации:

- не эмбеддить одинаковый текст дважды;
- не обновлять vector store, если словарь не изменился;
- rolling memory доставать без embedding;
- анализировать только новые главы;
- сохранять checkpoints;
- применять Judge только к реально переведенным главам;
- использовать быстрые модели для вспомогательных задач.

## 18. Что сделать лучше, чем в текущем проекте

- Сразу строить `LLMGateway`, а не вызывать SDK из агентов.
- Сразу ввести таблицу `chapter_runs`.
- Сразу отделить SQLite-истину от ChromaDB-индекса.
- Сразу сделать resume/checkpoint.
- Сразу сделать doctor с проверкой доступных моделей.
- Сразу сделать daily quota guard.
- Сразу тестировать fake-success сценарии.
- Сразу сделать prompt versioning для кэша.
- Сразу иметь `retry-failed`, чтобы не запускать весь проект заново.

## 19. Практический MVP

Если строить заново, первый рабочий MVP:

1. CLI-only перевод одной папки.
2. SQLite проекты и статусы глав.
3. Один LLM gateway.
4. Перевод целой главы.
5. Atomic output.
6. Retry 503.
7. Report JSON.
8. Resume failed.
9. Glossary.
10. Rolling memory.

GUI стоит делать после того, как CLI workflow стабилен и покрыт тестами.

## 20. Главный критерий готовности

Проект можно считать хорошим, когда выполняются условия:

- пользователь кладет новые главы в input;
- запускает одну команду или кнопку;
- уже переведенные главы не трогаются;
- новые главы переводятся целиком;
- термины сохраняются между главами;
- память обновляется;
- временные ошибки API повторяются;
- дневные лимиты не прожигаются;
- при сбое понятно, какая глава упала и почему;
- повторный запуск продолжает работу, а не начинает все заново.
