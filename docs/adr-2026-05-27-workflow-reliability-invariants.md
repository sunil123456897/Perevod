# ADR: Workflow reliability invariants

Date: 2026-05-27

## Context

The translator is a local desktop-first batch workflow. Its main failure modes are expensive or unsafe: duplicated Gemini translation calls, incorrect resume state, overwritten chapter outputs, stale cache reuse, and reports that mark a partial run as successful.

This ADR records the reliability invariants that current code must preserve.

## Decisions

### Per-chapter state is authoritative

Each chapter is tracked independently. Runtime state and `translation_report.json` must not infer a chapter status from another chapter.

The stage vocabulary is:

- `discovered`
- `context_retrieved`
- `analysis_done`
- `glossary_updated`
- `translation_done`
- `judge_done`
- `refine_done`
- `output_written`
- `summary_done`
- `memory_updated`

SQLite `chapter_runs` is the durable checkpoint source for these stages. `translation_report.json` is the user-facing diagnostic artifact, not the only resume source.

### Resume must avoid duplicate translation spend

If `translation_done` and `output_written` are complete, a later failure in judge/refine/summary/memory must not cause another translation model call on retry. Retry should reuse the existing output file when the previous report/checkpoint proves that translation output is already present.

If judge found blocking issues and refine completed before a crash, stale judge blockers are not final evidence. Retry must force a fresh judge pass before reporting success or QA failure.

### Context and QA are chapter-scoped

Context retrieval, judge results, blocking issues, refine metadata, and summary metadata are chapter-specific. Multi-chapter runs must not reuse the first chapter's context for later chapters unless the run contains only one chapter and the legacy global context is the only available context.

Refine is allowed to edit only chapters with blocking issues. A refined chapter must not change the status or report fields of unrelated chapters.

### Cache entries are QA-approved only

Translation cache keys include at least:

- model name
- input text hash
- dictionary hash
- context hash
- style guide
- generation settings
- translation prompt version

The translation cache may store only QA-approved text. Raw translation output and editor/refine output before a follow-up successful judge pass must not be cached. If a cached translation is empty or receives blocking judge issues, the cache entry must be invalidated.

### SQLite is the memory source of truth

Glossary proposals, approved glossary terms, world bible data, rolling summary artifacts, and chapter run status belong in SQLite. ChromaDB is a rebuildable search index.

Self-learning glossary updates must not silently overwrite approved terms. Conflicting synonym or term proposals are stored as candidates with status, source chapter, confidence, and reason, and are surfaced in the report.

Chapter memory entries must not use fake zero-vector embeddings. If ChromaDB receives chapter memory documents, it should use the configured embedding function or be rebuildable from SQLite-backed data.

### Gemini access goes through gateway helpers

Text generation goes through `generate_text()` and the Gemini model adapter. Embedding calls go through `GeminiEmbeddingAdapter`.

Gateway behavior owns:

- timeout configuration
- retry/backoff for retryable transient errors
- no retry for auth, model-not-found, quota, and schema/validation failures
- daily budget reservations and release/record semantics
- logging that does not expose API keys

Agent nodes must not call the Google SDK directly.

### File writes are conservative

Critical output files are written atomically. Output directories are locked for a workflow run.

Before overwriting an existing translated chapter, the previous file is backed up as a sibling `.bak` file. GUI overwrite mode is off by default and requires explicit confirmation.

Input and output directories must be validated before output creation, locks, databases, or Gemini calls. They must not be the same path, nested inside each other, or traverse symlink components.

## Consequences

These invariants intentionally favor correctness and resume safety over maximum throughput. Any future refactor that changes workflow state, cache keys, memory storage, LLM calls, or file writes must either preserve these invariants or update this ADR with a tested replacement design.

Relevant verification should include targeted tests plus:

```bat
python -m ruff check src scripts tests
python -m compileall -q src scripts
python scripts\run_safe_tests.py
```
