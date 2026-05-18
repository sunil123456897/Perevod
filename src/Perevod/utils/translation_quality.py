from __future__ import annotations

import re
from dataclasses import dataclass, field


AI_SLOP_PHRASES = (
    "стоит отметить",
    "важно понимать",
    "следует отметить",
    "неудивительно, что",
    "в заключение",
)

SEVERITY_RANK = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}


@dataclass(frozen=True)
class TranslationSanityResult:
    pass_check: bool
    severity: str
    blocking_issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    score_cap: float = 10.0


def evaluate_translation_sanity(
    original_text: str,
    translated_text: str,
    canonical_dictionary: dict[str, str] | None = None,
) -> TranslationSanityResult:
    """Fast deterministic checks that catch obvious bad translations before LLM QA."""
    original = original_text.strip()
    translated = translated_text.strip()
    dictionary = canonical_dictionary or {}
    blocking_issues: list[str] = []
    suggestions: list[str] = []
    score_cap = 10.0

    if not translated:
        return TranslationSanityResult(
            pass_check=False,
            severity="critical",
            blocking_issues=["Translation output is empty."],
            score_cap=0.0,
        )

    if _looks_truncated(original, translated):
        blocking_issues.append(
            "Translation is suspiciously short compared with the original."
        )
        score_cap = min(score_cap, 4.0)

    latin_ratio = _latin_ratio(translated)
    latin_letters = _latin_letter_count(translated)
    if (latin_ratio > 0.18 and len(translated) >= 120) or (
        latin_ratio > 0.65 and latin_letters >= 20
    ):
        blocking_issues.append(
            "Translation contains too much untranslated Latin-script text."
        )
        score_cap = min(score_cap, 5.0)

    missing_terms = _missing_required_terms(original, translated, dictionary)
    if missing_terms:
        preview = ", ".join(missing_terms[:5])
        if len(missing_terms) > 5:
            preview += f", +{len(missing_terms) - 5} more"
        blocking_issues.append(
            f"Canonical dictionary terms are missing from translation: {preview}."
        )
        score_cap = min(score_cap, 6.0)

    found_slop = _found_ai_slop(translated)
    if found_slop:
        suggestions.append(
            "Remove generic AI-style phrasing: " + ", ".join(found_slop) + "."
        )
        score_cap = min(score_cap, 8.0)

    severity = "low"
    if blocking_issues:
        severity = "high" if score_cap <= 5.0 else "medium"

    return TranslationSanityResult(
        pass_check=not blocking_issues,
        severity=severity,
        blocking_issues=blocking_issues,
        suggestions=suggestions,
        score_cap=score_cap,
    )


def merge_severity(left: str, right: str) -> str:
    return left if SEVERITY_RANK.get(left, 0) >= SEVERITY_RANK.get(right, 0) else right


def _looks_truncated(original: str, translated: str) -> bool:
    if len(original) < 240:
        return False
    return len(translated) < len(original) * 0.35


def _latin_ratio(text: str) -> float:
    letters = re.findall(r"[A-Za-zА-Яа-яЁё]", text)
    if not letters:
        return 0.0
    latin = sum(1 for char in letters if "A" <= char <= "Z" or "a" <= char <= "z")
    return latin / len(letters)


def _latin_letter_count(text: str) -> int:
    return sum(1 for char in text if "A" <= char <= "Z" or "a" <= char <= "z")


def _missing_required_terms(
    original_text: str,
    translated_text: str,
    canonical_dictionary: dict[str, str],
) -> list[str]:
    missing: list[str] = []
    for english_term, russian_term in canonical_dictionary.items():
        english = str(english_term).strip()
        russian = str(russian_term).strip()
        if not english or not russian:
            continue
        if _term_occurs_in_text(english, original_text) and not _term_occurs_in_text(
            russian,
            translated_text,
        ):
            missing.append(f"{english} -> {russian}")
    return missing


def _russian_term_occurs_in_text(term: str, text: str) -> bool:
    normalized_term = term.lower().strip()
    normalized_text = text.lower().strip()
    if not normalized_term:
        return False

    word_char = r"A-Za-zА-Яа-яЁё0-9"
    term_words = re.findall(f"[{word_char}]+", normalized_term)
    if not term_words:
        return False

    def get_stem(w: str) -> str:
        if len(w) <= 3:
            if w[-1] in "аеёиоуыэюя":
                return w[:-1]
            return w
        if len(w) == 4:
            if w[-1] in "аеёиоуыэюя":
                return w[:-1]
            return w
        if len(w) == 5:
            return w[:-1]
        return w[:max(4, len(w) - 3)]

    parts = []
    for i, w in enumerate(term_words):
        stem = get_stem(w)
        escaped_stem = re.escape(stem)
        if i == 0:
            parts.append(rf"\b{escaped_stem}\w*")
        else:
            parts.append(rf"\w*{escaped_stem}\w*")

    pattern = r"[^\w]+".join(parts)
    pattern += r"\b"
    return re.search(pattern, normalized_text) is not None


def _term_occurs_in_text(term: str, text: str) -> bool:
    normalized_term = (term or "").strip()
    if not normalized_term:
        return False

    # Поддержка альтернативных вариантов/синонимов через / или ;
    if "/" in normalized_term or ";" in normalized_term:
        variants = [v.strip() for v in re.split(r"/|;", normalized_term) if v.strip()]
        if variants:
            return any(_term_occurs_in_text(variant, text) for variant in variants)

    if re.search(r"[А-Яа-яЁё]", normalized_term):
        return _russian_term_occurs_in_text(normalized_term, text)

    word_char = r"A-Za-zА-Яа-яЁё0-9"
    escaped_term = re.escape(normalized_term)
    starts_with_word_char = bool(re.match(f"[{word_char}]", normalized_term[0]))
    ends_with_word_char = bool(re.match(f"[{word_char}]", normalized_term[-1]))
    prefix = f"(?<![{word_char}])" if starts_with_word_char else ""
    suffix = f"(?![{word_char}])" if ends_with_word_char else ""
    return re.search(
        f"{prefix}{escaped_term}{suffix}",
        text,
        flags=re.IGNORECASE,
    ) is not None


def _found_ai_slop(translated_text: str) -> list[str]:
    lower_translation = translated_text.lower()
    return [phrase for phrase in AI_SLOP_PHRASES if phrase in lower_translation]
