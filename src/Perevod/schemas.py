# src/Perevod/schemas.py
from pydantic import BaseModel, Field
from typing import List


class TermAnalysis(BaseModel):
    """Схема для одного термина, извлеченного на этапе анализа."""

    english_term: str = Field(
        description="The original English term, exactly as found."
    )
    russian_translation: str = Field(
        description="The most likely Russian translation for this term."
    )
    category: str = Field(
        description="The category of the term (e.g., Person, Location, Faction, Item, Technique, Concept)."
    )
    description: str = Field(
        description="A brief, one-sentence English description of the term based on its context."
    )


class AnalysisResult(BaseModel):
    """Схема для полного ответа от LLM на этапе анализа главы."""

    found_terms: List[TermAnalysis]


class InconsistencyVerdict(BaseModel):
    """Схема для ответа от LLM на этапе разрешения конфликтов."""

    english_term: str
    correct_variant: str = Field(
        description="The single best Russian translation chosen from the variants."
    )
    reasoning: str = Field(description="A brief justification for the choice.")


class VerdictsList(BaseModel):
    """Схема для полного ответа от LLM, содержащего список вердиктов."""

    verdicts: List[InconsistencyVerdict]


class SynonymUpdate(BaseModel):
    english_term: str = Field(description="The English term from the dictionary.")
    found_translation: str = Field(description="The actual Russian term/synonym used in the text.")


class JudgeResult(BaseModel):
    """Схема для оценки качества перевода Судьей."""

    pass_check: bool = Field(description="True if no blocking issues are found.")
    severity: str = Field(description="low|medium|high|critical")
    blocking_issues: List[str] = Field(
        default_factory=list, description="List of technical or consistency errors."
    )
    suggestions: List[str] = Field(
        default_factory=list, description="Stylistic improvements."
    )
    score: float = Field(ge=0, le=10, description="Quality score from 0 to 10.")
    synonym_updates: List[SynonymUpdate] = Field(
        default_factory=list, description="List of detected valid alternative translations/synonyms used in the translation."
    )


class ChapterSummary(BaseModel):
    """Схема для краткого содержания главы."""

    title: str
    summary: str = Field(description="Concise plot summary (3-5 sentences).")
    key_events: List[str] = Field(
        default_factory=list, description="List of major plot developments."
    )
    active_characters: List[str] = Field(
        default_factory=list, description="Characters present in this chapter."
    )
