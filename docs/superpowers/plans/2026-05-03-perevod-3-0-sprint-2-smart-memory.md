# Perevod 3.0: Sprint 2 (Smart Memory: RAG + Rolling Memory) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a sophisticated context management system using Rolling Memory (last 3 chapters summaries) and RAG to ensure long-term plot consistency.

**Architecture:** Add a pre-translation `context_retrieval` node to gather relevant lore and recent events, and a post-translation `summarization` node to update the memory for subsequent chapters.

**Tech Stack:** Python 3.12, LangGraph, Pydantic, ChromaDB.

---

### Task 1: Update Schema and State Models

**Files:**
- Modify: `src/Perevod/schemas.py`
- Modify: `src/Perevod/agents/state.py`
- Test: `tests/test_09_sprint2_schemas.py`

- [ ] **Step 1: Update `schemas.py`**
Add `ChapterSummary` model to `src/Perevod/schemas.py`.

```python
class ChapterSummary(BaseModel):
    """Схема для краткого содержания главы."""
    title: str
    summary: str = Field(description="Concise plot summary (3-5 sentences).")
    key_events: List[str] = Field(default_factory=list, description="List of major plot developments.")
    active_characters: List[str] = Field(default_factory=list, description="Characters present in this chapter.")
```

- [ ] **Step 2: Update `state.py`**
Add `rag_context` and `chapter_summaries` to `AgentState`.

```python
class AgentState(TypedDict):
    # ... existing ...
    rag_context: str                 # Combined lore and memory for the prompt
    chapter_summaries: List[Dict]    # Recent chapter summaries
```

- [ ] **Step 3: Commit changes**

---

### Task 2: Implement Summarization Node

**Files:**
- Modify: `src/Perevod/agents/nodes.py`
- Test: `tests/test_10_summarization_node.py`

- [ ] **Step 1: Implement `summarization_node`**
Add `summarization_node` to `nodes.py`. It should take the `final_translation` and create a `ChapterSummary` using the `summarization` model.

- [ ] **Step 2: Store summary in ChromaDB**
Call `kb_manager.add_or_update_entries` with metadata `{"type": "chapter_memory", "chapter_index": ...}`.

- [ ] **Step 3: Commit changes**

---

### Task 3: Implement Context Retrieval Node

**Files:**
- Modify: `src/Perevod/agents/nodes.py`
- Test: `tests/test_11_retrieval_node.py`

- [ ] **Step 1: Implement `context_retrieval_node`**
Add `context_retrieval_node` to `nodes.py`.
Logic:
1. Query ChromaDB for relevant Lore/Bible entries based on current English text.
2. Query ChromaDB for the last 3 `chapter_memory` entries (sorted by chapter index).
3. Combine into a structured string:
```text
=== WORLD BIBLE ===
{lore_entries}

=== RECENT EVENTS (PREVIOUS CHAPTERS) ===
{summaries}
```
4. Set `state['rag_context']` and `state['chapter_summaries']`.

- [ ] **Step 2: Commit changes**

---

### Task 4: Update Translation Node & Orchestration

**Files:**
- Modify: `src/Perevod/agents/nodes.py`
- Modify: `src/Perevod/graph_runner.py`
- Test: `tests/test_12_graph_integration_v3_2.py`

- [ ] **Step 1: Update `translation_node` prompt**
Modify the prompt in `nodes.py` to use `{rag_context}` instead of the old simple context retrieval.

- [ ] **Step 2: Update graph structure in `graph_runner.py`**
New flow:
`CONTEXT_RETRIEVAL -> TRANSLATION -> JUDGE -> (Refine Loop) -> SUMMARIZATION -> END`

- [ ] **Step 3: Commit changes**

---

### Task 5: Final Verification

- [ ] **Step 1: Run integration test**
Verify that Chapter 2 translation uses the summary of Chapter 1 in its context.
