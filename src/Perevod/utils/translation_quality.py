from __future__ import annotations

import re
from dataclasses import dataclass, field


AI_SLOP_PHRASES = (
    # Канцелярит / пояснительные клише
    "стоит отметить",
    "важно понимать",
    "следует отметить",
    "неудивительно, что",
    "в заключение",
    "необходимо отметить",
    "следует понимать",
    "как уже упоминалось",
    "как известно",
    "само собой разумеется",
    # Пафосные афористичные хвосты (фирменный ИИ-приём)
    "и это было лишь началом",
    "так началось",
    "время покажет",
    "только время покажет",
    "и это лишь верхушка айсберга",
    "это была лишь прелюдия",
)

# Плотностные ИИ-маркеры (на 1000 слов). Пороги откалиброваны по переводу 591-603.
NARRATIVE_DASH_BLOCK_PER_1K = 5.0  # нарративное тире -> blocking (Refine)
NARRATIVE_DASH_WARN_PER_1K = 3.0  # -> suggestion
GERUND_BLOCK_PER_1K = 4.0  # деепричастные обороты -> blocking (Refine)
GERUND_WARN_PER_1K = 2.5  # -> suggestion
CONNECTOR_WARN_PER_1K = 6.0  # слова-плевелы -> suggestion
SIMILE_WARN_PER_1K = 4.0  # словно/будто/подобно -> suggestion

# Слова-коннекторы, которые ИИ пачкой вставляет для "связности".
_CONNECTOR_RE = re.compile(
    r"\b(?:впрочем|однако|тем\s+не\s+менее|тем\s+более|к\s+слову|к\s+сожалению|"
    r"к\s+счастью|к\s+удивлению|разумеется|безусловно|несомненно|очевидно|"
    r"действительно|конечно\s+же|естественно|итак|таким\s+образом|"
    r"в\s+конце\s+концов|в\s+общем|в\s+любом\s+случае|в\s+то\s+же\s+время|"
    r"между\s+тем|при\s+этом|не\s+говоря\s+уже|к\s+тому\s+же|"
    r"с\s+одной\s+стороны|с\s+другой\s+стороны|наконец|наконец-то)\b",
    re.IGNORECASE,
)

# Сравнения-заглушки.
_SIMILE_RE = re.compile(
    r"\b(?:словно|будто|как\s+будто|точно|подобно|напомина(?:л|ла|ло|ли))\b",
    re.IGNORECASE,
)

# Деепричастные обороты: высокоточные суффиксы (вши/вшись/рефлексивные),
# а также деепричастия на -в в начале предложения (любимый ИИ-шаблон:
# "Сосредоточив...", "Покинув..."). Минимальная длина корня отсекает имена ("Лев").
_GERUND_SUFFIX_RE = re.compile(
    r"\b\w+(?:вши|вшись|авшись|ившись|овавшись|увавшись|явшись|аясь|ясь)\b"
)
_GERUND_INITIAL_RE = re.compile(
    r"(?m)^[ \t«»\"'-]{0,3}[А-ЯЁ][а-яё]{4,}(?:в|вши|вшись)\b"
)

# Нарративное тире: буква/цифра, пробел(ы), тире. Не ловит диалоговые реплики
# (^—) и авторские ремарки (знак препинания перед тире: ",— ответил").
_NARRATIVE_DASH_RE = re.compile(r"[А-Яа-яЁёA-Za-z0-9]»?\s+—")

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
    # Машиночитаемые метрики стиля (плотность на 1000 слов). Потребляется Judge.
    style_metrics: dict = field(default_factory=dict)


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

    style_metrics = _style_density_metrics(translated)
    dash_per_1k = style_metrics["narrative_dash_per_1k"]
    gerund_per_1k = style_metrics["gerund_per_1k"]
    connector_per_1k = style_metrics["connector_per_1k"]
    simile_per_1k = style_metrics["simile_per_1k"]

    if dash_per_1k > NARRATIVE_DASH_BLOCK_PER_1K:
        blocking_issues.append(
            f"Excessive narrative em-dashes ({dash_per_1k:.1f}/1000 words; "
            f"target <= {NARRATIVE_DASH_WARN_PER_1K:.0f}). The dash is used as a "
            f"substitute for commas/periods/restructuring — a signature machine-style "
            f"marker. Rewrite the flagged clauses."
        )
        score_cap = min(score_cap, 7.0)
    elif dash_per_1k > NARRATIVE_DASH_WARN_PER_1K:
        suggestions.append(
            f"Narrative em-dash density is high ({dash_per_1k:.1f}/1000 words). "
            f"Prefer commas, periods, or restructuring over dashes."
        )
        score_cap = min(score_cap, 8.5)

    if gerund_per_1k > GERUND_BLOCK_PER_1K:
        blocking_issues.append(
            f"Excessive gerund clauses / деепричастные обороты ({gerund_per_1k:.1f}/1000 "
            f"words). Stacked gerunds are a strong machine-style marker in Russian prose. "
            f"Convert most gerunds to finite verbs or split into separate sentences."
        )
        score_cap = min(score_cap, 7.0)
    elif gerund_per_1k > GERUND_WARN_PER_1K:
        suggestions.append(
            f"Gerund density is high ({gerund_per_1k:.1f}/1000 words). "
            f"Vary sentence openings instead of leaning on деепричастные обороты."
        )
        score_cap = min(score_cap, 8.5)

    if connector_per_1k > CONNECTOR_WARN_PER_1K:
        suggestions.append(
            f"Filler connector density is high ({connector_per_1k:.1f}/1000 words: "
            f"впрочем/однако/ведь/разумеется и т.п.). Remove connectors where the "
            f"sentence works without them."
        )
        score_cap = min(score_cap, 8.5)

    if simile_per_1k > SIMILE_WARN_PER_1K:
        suggestions.append(
            f"Simile density is high ({simile_per_1k:.1f}/1000 words: "
            f"словно/будто/подобно). Keep at most one comparison per paragraph."
        )
        score_cap = min(score_cap, 8.5)

    severity = "low"
    if blocking_issues:
        severity = "high" if score_cap <= 5.0 else "medium"

    return TranslationSanityResult(
        pass_check=not blocking_issues,
        severity=severity,
        blocking_issues=blocking_issues,
        suggestions=suggestions,
        score_cap=score_cap,
        style_metrics=style_metrics,
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


def _word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", text))


def _style_density_metrics(translated_text: str) -> dict:
    """Плотность ИИ-стилевых маркеров на 1000 слов.

    Диалоговые реплики (^—) и авторские ремарки (знак препинания + — глагол речи)
    не учитываются — считаем только нарративную прозу.
    """
    words = _word_count(translated_text)
    if words < 50:
        return {
            "narrative_dash_per_1k": 0.0,
            "gerund_per_1k": 0.0,
            "connector_per_1k": 0.0,
            "simile_per_1k": 0.0,
            "word_count": words,
        }
    scale = 1000.0 / words
    narrative_dashes = len(_NARRATIVE_DASH_RE.findall(translated_text))
    gerunds = len(_GERUND_SUFFIX_RE.findall(translated_text)) + len(
        _GERUND_INITIAL_RE.findall(translated_text)
    )
    connectors = len(_CONNECTOR_RE.findall(translated_text))
    similes = len(_SIMILE_RE.findall(translated_text))
    return {
        "narrative_dash_per_1k": round(narrative_dashes * scale, 2),
        "gerund_per_1k": round(gerunds * scale, 2),
        "connector_per_1k": round(connectors * scale, 2),
        "simile_per_1k": round(similes * scale, 2),
        "word_count": words,
    }
