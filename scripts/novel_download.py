"""Bulk-download 'You Cultivate, I Farm' chapters 604..871 from novellive.app.

Uses undetected-chromedriver to pass Cloudflare, then walks the 'Next Chapter'
link. Chapter body is read from ``div.m-read`` via Selenium ``element.text``
and cleaned of nav/ad boilerplate. Output files match the existing
``Chapter NNN Title.txt`` convention in Eng_Fermer.

Resumable: skips chapters whose .txt already exists in the output dir.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import undetected_chromedriver as uc
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By

DEFAULT_START = "https://novellive.app/book/you-cultivate-i-farm/chapter-604-a-bountiful-harvest-of-sword-talismans"
DEFAULT_OUT = r"C:\Users\vanya\Desktop\kod\Eng_Fermer"
STOP_AT_CHAPTER = 871

# Lines that are pure boilerplate and must be stripped from the body.
BOILERPLATE_RE = re.compile(
    r"^(Prev Chapter|Next Chapter|Previous Chapter|Report chapter|Close|"
    r"You Cultivate, I Farm|Chapter \d+.*)$",
    re.IGNORECASE,
)
AD_HINTS = ("Sponsored", "adsbygoogle", "Please report", "War Thunder", "Vetob")
# Lines that look like ad blocks (sponsor placeholders are short uppercase noise).
AD_LINE_RE = re.compile(r"^(Play |20\+ |Sponsored$)")

# Anti-scraping watermarks: the site injects random exotic-Unicode tokens
# (e.g. "ṟΆ₦ộᛒĘś" = obfuscated "ranobes") into the body. A token is treated as a
# watermark if it is mostly non-ASCII AND contains chars outside the safe set
# (basic latin letters/digits, common punctuation, and the typographic quotes /
# apostrophe / ellipsis / em-dash that legitimately occur in English prose).
_SAFE_CATEGORIES = {"Ll", "Lu", "Nd"}
_SAFE_PUNCT = set(".,!?;:'\"()[]{}…—–-/")
_TYPOGRAPHIC = {0x2018, 0x2019, 0x201C, 0x201D, 0x2013, 0x2014, 0x2026}


def _is_watermark_token(tok: str) -> bool:
    if len(tok) < 3:
        return False
    bad = 0
    for ch in tok:
        o = ord(ch)
        if o < 128:
            continue
        if o in _TYPOGRAPHIC:
            continue
        if ch in _SAFE_PUNCT:
            continue
        bad += 1
    # >=2 exotic chars (outside safe latin+typography) -> watermark.
    return bad >= 2


def strip_watermarks(text: str) -> str:
    out_lines = []
    for line in text.splitlines():
        words = line.split()
        kept = [w for w in words if not _is_watermark_token(w)]
        if len(kept) != len(words):
            # A watermark was removed; collapse stray double spaces.
            out_lines.append(" ".join(kept))
        else:
            out_lines.append(line)
    return "\n".join(out_lines)


def clean_body(raw: str, chapter_label: str) -> str:
    """Drop nav/ad boilerplate and anti-scraping watermarks; keep narrative."""
    kept = []
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            kept.append("")  # preserve paragraph spacing
            continue
        if BOILERPLATE_RE.match(s):
            continue
        if any(hint in s for hint in AD_HINTS):
            continue
        if AD_LINE_RE.match(s) and len(s) < 80:
            continue
        kept.append(s)
    text = "\n".join(kept)
    text = strip_watermarks(text)
    # Collapse 3+ blank lines to one, strip edges.
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def parse_title(driver) -> tuple[int, str] | None:
    """Extract (chapter_number, chapter_title) from <title> or .chapter span."""
    # Prefer the .chapter span: "Chapter 604: A Bountiful Harvest..."
    try:
        els = driver.find_elements(By.CSS_SELECTOR, "span.chapter")
        if els:
            label = els[0].text.strip()
            m = re.match(r"Chapter\s+(\d+)\s*[:]?\s*(.*)", label, re.S)
            if m:
                num = int(m.group(1))
                title = re.sub(r"\s+", " ", m.group(2)).strip().strip(":.")
                return num, title
    except Exception:
        pass
    # Fallback: <title> "You Cultivate, I Farm - Chapter 604: ... - Novel Live"
    try:
        t = driver.title or ""
        m = re.search(r"Chapter\s+(\d+)\s*[:]?\s*(.*?)(?:\s+-\s+|$)", t, re.S)
        if m:
            num = int(m.group(1))
            title = re.sub(r"\s+", " ", m.group(2)).strip().strip(":.")
            return num, title
    except Exception:
        pass
    return None


def safe_filename(num: int, title: str) -> str:
    core = title.strip().rstrip(".")
    core = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", core)
    core = re.sub(r"\s+", " ", core).strip()
    if not core:
        return f"Chapter {num}.txt"
    return f"Chapter {num} {core}.txt"


def next_chapter_url(driver) -> str | None:
    """Return the href of the 'Next Chapter' link, or None if absent."""
    try:
        els = driver.find_elements(
            By.CSS_SELECTOR, "a[title*='Next' i], a[href*='chapter-']"
        )
    except Exception:
        return None
    for el in els:
        try:
            text = (el.text or "").strip()
            href = el.get_attribute("href") or ""
        except Exception:
            continue
        if "next" in text.lower() and "chapter-" in href:
            return href
    return None


def fetch_chapter(driver, wait_seconds: int = 12) -> str | None:
    """Wait for CF to clear + .m-read present; return its raw text or None."""
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        title = driver.title or ""
        if "Just a moment" not in title and "Cloudflare" not in title:
            try:
                els = driver.find_elements(By.CSS_SELECTOR, "div.m-read")
                if els and els[0].text.strip():
                    return els[0].text
            except Exception:
                pass
        time.sleep(0.7)
    # last attempt
    try:
        els = driver.find_elements(By.CSS_SELECTOR, "div.m-read")
        if els and els[0].text.strip():
            return els[0].text
    except Exception:
        pass
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-url", default=DEFAULT_START)
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    ap.add_argument("--stop-at", type=int, default=STOP_AT_CHAPTER)
    ap.add_argument("--max", type=int, default=300, help="safety cap on chapters")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    options = uc.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--lang=en-US")
    driver = uc.Chrome(options=options, version_main=None)

    url = args.start_url
    saved = 0
    skipped = 0
    failures: list[str] = []
    try:
        count = 0
        while url and count < args.max:
            count += 1
            try:
                driver.get(url)
            except WebDriverException as exc:
                print(f"[!] get failed {url}: {exc}")
                failures.append(url)
                break

            raw = fetch_chapter(driver)
            if not raw:
                print(f"[!] no body at {url}")
                failures.append(url)
                # Try to advance anyway in case the page is a soft error.
                url = next_chapter_url(driver)
                time.sleep(1.5)
                continue

            meta = parse_title(driver)
            if not meta:
                print(f"[!] cannot parse chapter number from {url}")
                failures.append(url)
                url = next_chapter_url(driver)
                time.sleep(1.5)
                continue

            num, title = meta
            if num > args.stop_at:
                print(f"[=] reached chapter {num} > stop-at {args.stop_at}; done.")
                break

            fname = safe_filename(num, title)
            fpath = out_dir / fname
            body = clean_body(raw, title)
            if fpath.exists() and fpath.stat().st_size > 500:
                print(f"[skip] {fname} (exists, {fpath.stat().st_size}B)")
                skipped += 1
            elif len(body) < 500:
                print(f"[!] {fname}: body too short ({len(body)}B), saving anyway for review")
                fpath.write_text(body, encoding="utf-8")
                failures.append(fname)
            else:
                fpath.write_text(body, encoding="utf-8")
                print(f"[ok] {fname}  ({len(body)} chars)")
                saved += 1

            # polite delay
            time.sleep(1.2)

            if num >= args.stop_at:
                print(f"[=] reached stop-at chapter {num}; done.")
                break

            url = next_chapter_url(driver)
            if not url:
                print("[=] no Next Chapter link; assuming end of novel.")
                break
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    print("\n=== SUMMARY ===")
    print(f"saved:   {saved}")
    print(f"skipped: {skipped}")
    print(f"failures: {len(failures)}")
    for f in failures:
        print(f"   - {f}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
