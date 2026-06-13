# Переводчик Новелл

Это приложение представляет собой инструмент для перевода новелл с использованием Google Gemini API. Оно включает в себя графический интерфейс для удобного управления проектами, словарями и базами знаний.

## Основные возможности

- **Перевод глав:** Использует Google Gemini API для перевода целых глав, что обеспечивает высокую скорость и консистентность.
- **Управление проектами:** Позволяет создавать, сохранять и удалять проекты, каждый из которых имеет свои собственные настройки.
- **Адаптивный словарь:** Автоматически определяет и разрешает конфликты терминов, обновляет словарь проекта и использует накопленные термины в следующих переводах.
- **Контекстуальный перевод:** Применяет базу знаний (семантический поиск) для подстановки релевантного контекста, улучшая точность и согласованность перевода.
- **Автоматизированный контроль качества:** Включает в себя этапы анализа и курирования для обеспечения высокого качества перевода.
- **Графический интерфейс:** Предоставляет удобный графический интерфейс для управления всеми функциями приложения.

## Быстрый запуск

Проверить окружение без запуска GUI и без обращения к Gemini API:

```bat
start.bat --doctor --project Default --input-dir path\to\input --output-dir path\to\output
```

Запустить автономный перевод без GUI:

```bat
start.bat --cli --project Default --input-dir path\to\input --output-dir path\to\output
```

Повторно перевести уже существующие выходные файлы:

```bat
start.bat --cli --project Default --input-dir path\to\input --output-dir path\to\output --overwrite-existing
```

Если `--overwrite-existing` или retry-сценарий действительно перезаписывает существующую выходную главу, старый файл сначала копируется рядом как `chapter.txt.bak`, `chapter.txt.bak.1` и т.д. Путь к backup попадает в `translation_report.json` как `output_backup_path`.

В GUI перезапись существующих переводов выключена по умолчанию. Если включить этот переключатель, приложение попросит отдельное подтверждение перед запуском. После завершения или ошибки GUI показывает путь к `translation_report.json` и краткую сводку: сколько глав обработано, сколько `failed`/`qa_failed`, сколько предупреждений и первые проблемные главы. Кнопка `Отчет` показывает подробности последнего `translation_report.json` из текущего `output_dir`, включая structured Gemini gateway diagnostics для API-сбоев.

Перезапустить только главы, которые в последнем отчете были помечены как `failed` или `qa_failed`:

```bat
start.bat --cli --project Default --input-dir path\to\input --output-dir path\to\output --retry-failed
```

Если предыдущий сбой произошел после записи перевода (например, на Judge/Editor/Summary), `--retry-failed` переиспользует существующий выходной файл и не тратит повторный вызов модели перевода. В отчете это отмечается полем `reused_existing_translation`. CLI пишет путь к `translation_report.json` даже при неуспешном завершении workflow или исключении после записи отчета, чтобы сразу было понятно, какой report использовать для диагностики и retry.

Перезапустить все незавершенные главы из отчета, включая нефатальные проблемы памяти/summary:

```bat
start.bat --cli --project Default --input-dir path\to\input --output-dir path\to\output --retry-incomplete
```

Этот режим берет `failed`, `qa_failed`, главы с `stages.* = "failed"` и главы с `warnings`. Если перевод уже есть на диске и прошлый сбой был не на стадии `translation`, output переиспользуется без повторного вызова модели перевода.

После выполнения workflow пишет машинный отчет:

```text
path\to\output\translation_report.json
```

В `translation_report.json` для каждой главы есть:

- `stages`: состояние стадий `context`, `analysis`, `glossary`, `translation`, `output`, `judge`, `refine`, `summary`, `memory`;
- `translation_source`: `api`, `cache` или `existing_file`;
- `translation_mode`: `whole_chapter`, `chunked`, `cache` или `existing_file`;
- `translation_chunk_count`: сколько частей отправлялось в модель перевода;
- `output_backup_path`: путь к backup старой выходной главы, если файл был перезаписан;
- `judge_score`, `judge_severity`, `blocking_issues`, `quality_suggestions`: результат автоматической проверки качества;
- `refined`, `refinement_count`, `refine_issues_fixed`: что сделал Editor/refine, если Judge нашел blocking issues;
- `summary`, `summary_key_events`, `summary_active_characters`: что было сохранено в rolling memory;
- `dictionary_conflicts`: новые варианты терминов, которые не перетерли утвержденный словарь и требуют проверки;
- `context_warnings`: нефатальные деградации поиска контекста, например переход с semantic search на lexical fallback;
- `error_category`, `error_retryable`, `error_status_code`, `error_operation`, `error_model`: структурная диагностика Gemini gateway, если сбой пришел из API-слоя;
- `warnings`: нефатальные проблемы, из-за которых главу стоит добрать через `--retry-incomplete`.

Это помогает понять, где именно остановился процесс, почему `--retry-failed` может продолжить без повторного перевода и что именно требует ручной проверки.
Структурная API-диагностика сохраняется не только для верхнеуровневого сбоя workflow, но и для caught stage errors, например на `context`, `analysis`, `judge`, `refine` или `summary`.

Если анализ терминов не смог обратиться к API, workflow не падает полностью, но в отчете появляется предупреждение `Term analysis was not completed`. Это означает, что глава переведена без обновленного словаря терминов и ее стоит перезапустить через `--retry-incomplete`, когда API снова доступен.

Если база знаний или rolling memory недоступны полностью, workflow также продолжает перевод с деградированным контекстом и пишет предупреждение `Context retrieval was degraded`. Это предупреждение тоже попадает в `--retry-incomplete`.

Если semantic search для world bible/lore не сработал, но lexical fallback смог собрать контекст, глава не помечается как `failed`: в отчете остается `stages.context = "done"`, но `context_warnings` и `warnings` показывают деградацию, чтобы главу можно было добрать через `--retry-incomplete`.

Если перевод и Judge прошли, но не удалось обновить summary или память главы, workflow не падает полностью. В отчете это видно как `warning_count > 0`, а у главы будет `stages.summary = "failed"` или `stages.memory = "failed"` и описание проблемы в `warnings`.

Если после лимита автоматических refine-итераций остаются блокирующие QA-проблемы, workflow завершается ошибкой, но все равно пишет `translation_report.json`. Такая глава получает статус `qa_failed`, и ее можно перезапустить через `--retry-failed` или `--retry-incomplete`.

Live-проверки Gemini API лучше выполнять только когда доступен VPN/API. Базовый `--doctor` проверяет Python, зависимости, ChromaDB, API key, модельный профиль и папки проекта без расхода квоты. Пустой API key и тестовые/заглушечные ключи вида `fake...` / `test...` считаются диагностическим режимом: `--doctor` покажет предупреждение и не будет создавать usage-tracker, а сам workflow перевода не стартует, пока не указан реальный `GOOGLE_API_KEY`. Если профиль содержит модель, которой нет в локальном registry, или известная embedding-модель назначена на текстовую задачу, `--doctor` покажет `WARN` в строке `Gemini model profile`: это не live-проверка доступности модели, а предупреждение, что локальные budget/rate/capability правила для нее неизвестны или несовместимы с задачей.

По умолчанию включен режим бесплатного Gemini API: перевод использует `gemini-3-flash-preview`, вспомогательные задачи используют `gemini-3.1-flash-lite-preview`, эмбеддинги используют `gemini-embedding-2`. Старые сохраненные `gemini-2.5-*` и `gemini-*-pro` при запуске перевода заменяются на текущий free-tier профиль.

Приложение ведет локальный дневной счетчик успешных вызовов Gemini в `_project_files/api_usage.sqlite3` и останавливает новые API-запросы до обращения к Gemini, если бесплатный дневной лимит модели уже исчерпан. Перед реальным запросом слот лимита бронируется атомарно, поэтому параллельные запросы не могут превысить дневной бюджет; сетевая ошибка снимает бронь, а зависшие брони очищаются автоматически. Для реальных API-ключей text-модели также выдерживают локальную паузу по RPM (`gemini-3-flash-preview`: 12 секунд, `gemini-3.1-flash-lite-preview`: 4 секунды). Сетевые таймауты и DNS/VPN-сбои не списывают локальный дневной счетчик как успешный вызов. Gateway нормализует ошибки Gemini в структурные категории (`auth`, `quota`, `model_not_found`, `schema`, `network`, `transient`) и санитизирует API keys в сообщениях. `--doctor` показывает строку `Gemini daily budget`, например `gemini-3-flash-preview: 3/20`.

Кэш перевода заполняется только после успешного Judge/QA-approval. Сырой ответ модели перевода и Editor/refine-исправление до повторного успешного Judge не сохраняются в кэш. Ключ кэша учитывает модель, хэш входного текста, словарь, контекст, style guide, версию prompt-логики и generation settings. Если старый cached перевод оказался пустым или получил blocking issues на Judge, запись удаляется из кэша, чтобы следующие retry не зацикливались на том же плохом тексте.

Если в системе включено DNS-перенаправление, которое ломает прямой доступ к Gemini, можно задать прокси только для этой программы в `.env`:

```env
HTTPS_PROXY=http://127.0.0.1:7890
HTTP_PROXY=http://127.0.0.1:7890
```

После этого `--doctor` покажет строки `proxy env` и `Gemini DNS`, чтобы было видно, через какое окружение запускается приложение.

### YogaDNS и доступ к Gemini

Проверка на этой машине показала:

```text
proxy env: HTTP_PROXY/HTTPS_PROXY not set for this process
Gemini DNS: generativelanguage.googleapis.com -> 185.250.151.49
Gemini API: timed out
```

Это означает, что YogaDNS подменяет DNS-ответ для Gemini, но приложение не получает рабочий HTTP-proxy. Для работы Gemini API нужно одно из двух:

- настроить в YogaDNS/системе правило, при котором `generativelanguage.googleapis.com` резолвится в реальные Google IP и TCP 443 доступен напрямую;
- или включить в VPN/proxy-клиенте локальный HTTP-proxy и прописать его в `.env` как `HTTPS_PROXY` и `HTTP_PROXY`.

Важно: YogaDNS сам по себе является DNS-инструментом, а не HTTP-proxy для Python SDK. В `.env` нужен порт именно proxy/VPN-клиента. Быстрая проверка типовых локальных proxy-портов (`7890`, `7891`, `8080`, `1080`, `10808`, `2080`, `20170`, `20171`, `9090`, `8888`) сейчас не нашла открытого listener на `127.0.0.1`.

После настройки прокси команда должна показывать `[OK] proxy env` и `[OK] Gemini API`:

```bat
start.bat --doctor --project Default --input-dir input --output-dir output --check-api --api-timeout 30
```

## Безопасность запуска

- Один output-каталог нельзя переводить параллельно двумя процессами: workflow создает `.translation.lock` и снимает его после завершения.
- `input_dir` и `output_dir` проверяются до создания output, lock-файла, SQLite и вызовов Gemini: папки не должны совпадать, быть вложенными друг в друга или проходить через symlink-компоненты.
- Имена проектов валидируются перед созданием SQLite/ChromaDB путей: запрещены path traversal, абсолютные пути, Windows-reserved имена (`CON`, `NUL`, `LPT1` и т.п.), trailing dot и символы вроде `:`, `?`, `*`, `|`.
- Перевод глав выполняется целиком, пока полный промпт помещается в бюджет. Если глава слишком большая, она автоматически делится на последовательные части с сохранением контекста; это видно в отчете как `translation_mode = "chunked"`.
- Для экономии TPM и контекста новые термины текущего анализа всегда попадают в prompt, а старые термины из словаря проекта добавляются только когда встречаются в тексте главы. Judge при этом проверяет перевод по полному накопленному словарю проекта.
- API-запросы перевода и эмбеддингов имеют ограниченные повторы только для временных сетевых ошибок и временных 5xx. Ошибки квоты, доступа, неверной модели, schema/response-schema и битого retry-отчета не ретраятся скрыто.
- Перед финальным статусом Judge дополняется локальными проверками: пустой вывод, подозрительно короткий перевод, слишком много английского текста, отсутствие обязательных словарных терминов и типовые AI-style фразы.
- Выходные главы и отчет записываются атомарно, чтобы снизить риск повреждения файлов при сбое.

## Локальная проверка перед изменениями

Safe-проверки не запускают live Gemini API и используют моки/тестовые ключи:

```bat
python -m ruff check src scripts tests
python -m compileall -q src scripts
python scripts\run_safe_tests.py
```

CI запускает эти проверки на Windows и Ubuntu. Coverage smoke выполняется отдельным шагом на Windows/Python 3.12:

```bat
python scripts\run_coverage.py
python -m coverage report -m
```

## Архитектурный blueprint

Если нужно строить похожий переводчик заново или планировать следующую крупную версию, см. [docs/building-better-novel-translator.md](docs/building-better-novel-translator.md). Документ описывает целевую архитектуру, LLM gateway, retry, лимиты API, память, resume после сбоев и критерии production-ready версии.

Текущие reliability-инварианты этой реализации зафиксированы в [docs/adr-2026-05-27-workflow-reliability-invariants.md](docs/adr-2026-05-27-workflow-reliability-invariants.md). Если меняется checkpoint/resume, cache, memory, LLM gateway или overwrite-поведение, этот ADR нужно сохранить актуальным.

## Новые функции

- **Кеширование эмбеддингов:** Для снижения расхода Gemini Embedding API используется локальный SQLite-кэш эмбеддингов.
- **Диагностика и recovery:** `--doctor`, `translation_report.json`, `--retry-failed` и `--retry-incomplete` помогают продолжать перевод после сетевых, API и QA-сбоев.
- **Локализация ошибок:** Были добавлены коды ошибок и их описания для более удобной диагностики проблем.
- **Наблюдаемость:** Логи, машинный отчет и статусы стадий показывают, где именно остановился workflow.
- **Тестовое покрытие:** Покрыты критичные компоненты: graph runner, agent nodes, LLM gateway, API usage, Chroma/KB, doctor, CLI и файловые операции.
