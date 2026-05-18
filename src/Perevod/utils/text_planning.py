import math
import re


def estimate_token_count(text: str, *, chars_per_token: float = 4.0) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / chars_per_token))


def split_text_by_token_budget(text: str, max_tokens: int) -> list[str]:
    if max_tokens <= 0:
        raise ValueError("max_tokens must be greater than zero")
    if estimate_token_count(text) <= max_tokens:
        return [text]

    paragraphs = [part for part in re.split(r"\n\s*\n", text) if part.strip()]
    chunks: list[str] = []
    current_parts: list[str] = []

    def flush_current() -> None:
        if current_parts:
            chunks.append("\n\n".join(current_parts).strip())
            current_parts.clear()

    for paragraph in paragraphs or [text]:
        paragraph = paragraph.strip()
        if not paragraph:
            continue

        if estimate_token_count(paragraph) > max_tokens:
            flush_current()
            chunks.extend(_split_long_paragraph(paragraph, max_tokens))
            continue

        candidate = "\n\n".join([*current_parts, paragraph])
        if current_parts and estimate_token_count(candidate) > max_tokens:
            flush_current()
        current_parts.append(paragraph)

    flush_current()
    return chunks or [text]


def _split_long_paragraph(paragraph: str, max_tokens: int) -> list[str]:
    max_chars = max(1, int(max_tokens * 4))
    sentences = re.split(r"(?<=[.!?。！？])\s+", paragraph)
    chunks: list[str] = []
    current = ""

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = sentence
        elif len(sentence) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(
                sentence[index : index + max_chars]
                for index in range(0, len(sentence), max_chars)
            )
        else:
            current = candidate

    if current:
        chunks.append(current)
    return chunks
