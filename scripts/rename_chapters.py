"""Translate English chapter titles to Russian and rename output files.

Reads existing translated chapter files from an output directory, extracts the
chapter number + English title from each filename, batch-translates the titles
via Gemini flash-lite, and renames the files to "Глава NNN. Title.txt".

Usage:
    python scripts/rename_chapters.py --output-dir <dir> [--apply]
Without --apply, the script only prints the planned renames (dry run).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

# Allow `python scripts/rename_chapters.py` from repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

from Perevod.model_registry import GEMINI_FLASH_LITE  # noqa: E402

CHAPTER_RE = re.compile(r"^Chapter\s+(\d+)\s*(.*?)\s*$", re.IGNORECASE)

PROMPT = """Ты редактор, переводишь заголовки глав китайской веб-новеллы по культивации на русский язык.

Правила:
- Переводи только сам заголовок, без номера главы и без слова "Chapter/Глава".
- Сохраняй литературный стиль, краткость и атмосферу оригинала.
- Термины культивации переводи устоявшимися в жанре (spirit plant -> духовное растение, sword talisman -> талисман меча, thunder -> гром, core -> ядро и т.д.).
- Никаких пояснений в скобках, транслитерации быть не должно, кроме имён собственных (Лу Сюань и т.п.).
- Кавычки в русском стиле: «...».
- Верни СТРОГО один JSON-объект: {"<оригинал>": "<перевод>"} для каждого заголовка. Без markdown, без комментариев.

Заголовки для перевода:
"""


def parse_filename(name: str) -> tuple[int | None, str | None]:
    """Return (chapter_number, english_title) or (None, None) if not a chapter."""
    stem = Path(name).stem
    m = CHAPTER_RE.match(stem)
    if not m:
        return None, None
    num = int(m.group(1))
    title = m.group(2).strip().strip("'\"")
    return num, (title or None)


def collect_titles(output_dir: Path) -> dict[int, str]:
    """Map chapter_number -> english_title for all chapter files."""
    titles: dict[int, str] = {}
    for f in sorted(output_dir.glob("Chapter *.txt")):
        num, title = parse_filename(f.name)
        if num is None:
            continue
        titles[num] = title or ""
    return titles


def batched(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def translate_titles(
    titles: dict[int, str], api_key: str, batch_size: int = 10
) -> dict[int, str]:
    """Translate english titles -> russian, chapter_number keyed."""
    client = genai.Client(api_key=api_key)
    result: dict[int, str] = {}
    # Only translate chapters that actually have an english title.
    todo = [(num, en) for num, en in sorted(titles.items()) if en]
    print(f"Перевод {len(todo)} заголовков (батчи по {batch_size})...")
    for chunk in batched(todo, batch_size):
        lines = [f"{i + 1}. {en}" for i, (num, en) in enumerate(chunk)]
        prompt = PROMPT + "\n".join(lines)
        response = client.models.generate_content(
            model=GEMINI_FLASH_LITE,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.3,
                topP=0.9,
                httpOptions=types.HttpOptions(timeout=120000),
                safetySettings=[
                    types.SafetySetting(
                        category=cat,
                        threshold=types.HarmBlockThreshold.BLOCK_NONE,
                    )
                    for cat in (
                        types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                        types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                        types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                        types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                    )
                ],
            ),
        )
        text = (response.text or "").strip()
        # Strip accidental code fences.
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            mapping = json.loads(text)
        except json.JSONDecodeError as exc:
            print(f"  WARN: не удалось распарсить JSON: {exc}")
            print(f"  RAW: {text[:200]}")
            time.sleep(2)
            continue
        for num, en in chunk:
            ru = mapping.get(en)
            if not ru:
                # Fallback: try case-insensitive match.
                ru = next(
                    (v for k, v in mapping.items() if k.lower() == en.lower()),
                    None,
                )
            if ru:
                ru = ru.strip().strip('"').strip("'")
                # Avoid leaking "Глава NNN" prefix from model.
                ru = re.sub(r"^Глава\s+\d+[.:]?\s*", "", ru, flags=re.IGNORECASE)
                result[num] = ru
            else:
                print(f"  WARN: нет перевода для '{en}'")
        time.sleep(2)  # Respect flash-lite RPM.
    return result


def build_new_name(num: int, ru_title: str) -> str:
    if not ru_title:
        return f"Глава {num}.txt"
    # Sanitize illegal filename chars on Windows.
    safe = re.sub(r'[<>:"/\\|?*]', "", ru_title).strip()
    safe = safe.rstrip(".")
    return f"Глава {num}. {safe}.txt"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-dir", required=True, help="Translated chapters dir")
    p.add_argument(
        "--api-key",
        default=os.environ.get("GEMINI_API_KEY", ""),
        help="Gemini API key (or GEMINI_API_KEY env)",
    )
    p.add_argument(
        "--cache",
        default=None,
        help="Optional JSON cache file for translated titles (resumable)",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Actually rename files (default: dry run)",
    )
    p.add_argument(
        "--only",
        default=None,
        help="Comma-separated chapter numbers to process (e.g. 604,605)",
    )
    args = p.parse_args()

    output_dir = Path(args.output_dir)
    if not output_dir.is_dir():
        print(f"ERROR: output dir not found: {output_dir}")
        return 2

    only_set = None
    if args.only:
        only_set = {int(x) for x in args.only.split(",") if x.strip().isdigit()}

    titles = collect_titles(output_dir)
    if only_set is not None:
        titles = {n: t for n, t in titles.items() if n in only_set}

    print(f"Найдено глав: {len(titles)}")

    cache: dict[int, str] = {}
    if args.cache and Path(args.cache).exists():
        try:
            raw = json.loads(Path(args.cache).read_text(encoding="utf-8"))
            cache = {int(k): v for k, v in raw.items()}
            print(f"Из кэша загружено {len(cache)} переводов")
        except (json.JSONDecodeError, OSError) as exc:
            print(f"WARN: не удалось прочитать кэш: {exc}")

    todo = {n: t for n, t in titles.items() if n not in cache}
    if todo and not args.api_key:
        print("ERROR: API key required (--api-key or GEMINI_API_KEY env)")
        return 2
    if todo:
        translated = translate_titles(todo, args.api_key)
        cache.update(translated)
        if args.cache:
            Path(args.cache).write_text(
                json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"Кэш сохранён: {args.cache}")
    else:
        print("Все заголовки уже в кэше, перевод не требуется")

    # Plan renames.
    plan: list[tuple[Path, Path]] = []
    skipped = 0
    for num, en in sorted(titles.items()):
        ru = cache.get(num)
        if not ru:
            # No translation available (e.g. untitled chapter). Use bare number.
            new_name = f"Глава {num}.txt"
        else:
            new_name = build_new_name(num, ru)
        src = output_dir / (
            f"Chapter {num}.txt" if not en else f"Chapter {num} {en}.txt"
        )
        # Handle apostrophes/special chars in source filename.
        if not src.exists():
            matches = list(output_dir.glob(f"Chapter {num}*.txt"))
            matches = [m for m in matches if parse_filename(m.name)[0] == num]
            if matches:
                src = matches[0]
            else:
                skipped += 1
                continue
        dst = output_dir / new_name
        if src.resolve() == dst.resolve():
            continue
        plan.append((src, dst))

    print(f"\nПлан переименования ({len(plan)} файлов):")
    for src, dst in plan[:30]:
        print(f"  {src.name}")
        print(f"    -> {dst.name}")
    if len(plan) > 30:
        print(f"  ... и ещё {len(plan) - 30}")

    if skipped:
        print(f"Пропущено (нет исходного файла): {skipped}")

    if not args.apply:
        print("\n[Сухой прогон. Для реального переименования добавьте --apply]")
        return 0

    # Check for collisions.
    dst_names = [dst.name for _, dst in plan]
    dupes = {n for n in dst_names if dst_names.count(n) > 1}
    if dupes:
        print(f"ERROR: коллизия имён: {dupes}")
        return 3

    applied = 0
    for src, dst in plan:
        try:
            src.rename(dst)
            applied += 1
        except OSError as exc:
            print(f"  FAIL {src.name}: {exc}")
    print(f"\nПереименовано: {applied}/{len(plan)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
