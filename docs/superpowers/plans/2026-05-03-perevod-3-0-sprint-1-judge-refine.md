# Perevod 3.0: Sprint 1 (MVP: Judge + Refine) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a more robust translation quality control cycle using a specialized Judge agent and a conditional Editor agent.

**Architecture:** Transition from a single-step `QA` node to a two-step `Judge -> Refine` cycle. The `Judge` identifies specific "blocking issues" (missing text, wrong terms, etc.), and the `Refine` node (Editor) only runs if critical issues are found, up to a maximum of 2 iterations.

**Tech Stack:** Python 3.12, LangGraph, Pydantic, google-genai.

---

### Task 1: Update Schema and State Models

**Files:**
- Modify: `src/Perevod/schemas.py`
- Modify: `src/Perevod/agents/state.py`
- Test: `tests/test_05_sprint1_schemas.py`

- [ ] **Step 1: Update `schemas.py`**
Add `JudgeResult` model to `src/Perevod/schemas.py`.

```python
class JudgeResult(BaseModel):
    """Схема для оценки качества перевода Судьей."""
    pass_check: bool = Field(description="True if no blocking issues are found.")
    severity: str = Field(description="low|medium|high|critical")
    blocking_issues: List[str] = Field(default_factory=list, description="List of technical or consistency errors.")
    suggestions: List[str] = Field(default_factory=list, description="Stylistic improvements.")
    score: int = Field(ge=0, le=10, description="Quality score from 0 to 10.")
```

- [ ] **Step 2: Update `state.py`**
Add new fields to `AgentState` in `src/Perevod/agents/state.py`.

```python
class AgentState(TypedDict):
    # ... existing fields ...
    judge_results: List[Dict[str, Any]] # Results per chapter
    refinement_count: int               # Current iteration counter
    blocking_issues: List[str]          # Current active issues to fix
    # ...
```

- [ ] **Step 3: Create a unit test for schemas**
Verify the new Pydantic model works.

- [ ] **Step 4: Commit changes**
`git add src/Perevod/schemas.py src/Perevod/agents/state.py && git commit -m "chore: update schemas and state for Perevod 3.0"`

---

### Task 2: Implement Judge Node

**Files:**
- Modify: `src/Perevod/agents/nodes.py`
- Test: `tests/test_06_judge_node.py`

- [ ] **Step 1: Implement `judge_node`**
Add the `judge_node` function to `src/Perevod/agents/nodes.py`. Use the prompt defined in the spec.

- [ ] **Step 2: Write unit test for `judge_node`**
Mock the LLM provider and verify the node correctly parses the `JudgeResult` and updates the state.

- [ ] **Step 3: Commit changes**
`git add src/Perevod/agents/nodes.py && git commit -m "feat: implement judge_node"`

---

### Task 3: Implement Refine Node (Editor)

**Files:**
- Modify: `src/Perevod/agents/nodes.py`
- Test: `tests/test_07_refine_node.py`

- [ ] **Step 1: Implement `refine_node`**
Add the `refine_node` function to `src/Perevod/agents/nodes.py`. It should take the `blocking_issues` from state and ask the LLM to rewrite the translation.

- [ ] **Step 2: Write unit test for `refine_node`**
Verify it correctly incorporates judge feedback into the prompt and updates the translation in state.

- [ ] **Step 3: Commit changes**
`git add src/Perevod/agents/nodes.py && git commit -m "feat: implement refine_node"`

---

### Task 4: Update Graph Orchestration

**Files:**
- Modify: `src/Perevod/graph_runner.py`
- Test: `tests/test_08_graph_integration_v3.py`

- [ ] **Step 1: Update node constants**
Add `JUDGE = "judge"` and `REFINE = "refine"`. Remove `QA`.

- [ ] **Step 2: Rebuild the graph in `build_graph()`**
Replace the linear `TRANSLATION -> QA` edge with:
```python
workflow.add_node(JUDGE, judge_node)
workflow.add_node(REFINE, refine_node)

workflow.add_edge(TRANSLATION, JUDGE)
workflow.add_conditional_edges(
    JUDGE,
    should_refine,
    {
        "refine": REFINE,
        "end": END
    }
)
workflow.add_edge(REFINE, JUDGE) # Loop back to check again
```

- [ ] **Step 3: Implement `should_refine` logic**
Implement the condition based on `pass_check` and `refinement_count`.

- [ ] **Step 4: Commit changes**
`git add src/Perevod/graph_runner.py && git commit -m "feat: update graph with judge-refine loop"`

---

### Task 5: Final Verification

- [ ] **Step 1: Run integration test**
Run a full translation workflow on a sample chapter and verify the `Judge` and `Refine` steps are triggered (or skipped) correctly.

- [ ] **Step 2: Clean up old code**
Remove the deprecated `qa_node` and `QAEvaluation` schema if they are no longer used.
