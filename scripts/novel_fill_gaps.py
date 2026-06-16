"""Fill missing chapters: navigate Prev to find chapter 788 URL, then walk
Next from 788 to 871 saving any chapter whose .txt is missing.

Why Prev first: chapter 788 has no slug in its filename, so we cannot build its
URL directly. Starting from chapter 794 (known slug) and walking Prev reaches it.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By

# Re-use the proven parsing/cleaning logic from the main downloader.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from novel_download import (  # noqa: E402
    clean_body,
    fetch_chapter,
    next_chapter_url,
    parse_title,
    safe_filename,
)

OUT = Path(r"C:\Users\vanya\Desktop\kod\Eng_Fermer")
STOP_AT = 871
# Start from a chapter whose URL slug we know.
START_URL = "https://novellive.app/book/you-cultivate-i-farm/chapter-794-treasure-hunting-alone"
TARGET_START_NUM = 788  # walk Prev until we reach this chapter


def prev_chapter_url(driver) -> str | None:
    try:
        els = driver.find_elements(
            By.CSS_SELECTOR, "a[title*='Previous' i], a[title*='Privious' i], a[href*='chapter-']"
        )
    except Exception:
        return None
    for el in els:
        try:
            text = (el.text or "").strip().lower()
            href = el.get_attribute("href") or ""
            title = (el.get_attribute("title") or "").lower()
        except Exception:
            continue
        if ("prev" in text or "prev" in title) and "chapter-" in href:
            return href
    return None


def save_current(driver) -> tuple[int, str] | None:
    raw = fetch_chapter(driver, wait_seconds=15)
    if not raw:
        return None
    meta = parse_title(driver)
    if not meta:
        return None
    num, title = meta
    fname = safe_filename(num, title)
    fpath = OUT / fname
    if fpath.exists() and fpath.stat().st_size > 500:
        print(f"[skip] {fname} exists")
        return num, title
    body = clean_body(raw, title)
    if len(body) < 500:
        print(f"[!] {fname}: short body {len(body)}B")
    fpath.write_text(body, encoding="utf-8")
    print(f"[ok] {fname}  ({len(body)} chars)")
    return num, title


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    options = uc.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--lang=en-US")
    driver = uc.Chrome(options=options, version_main=None)

    try:
        # Phase 1: walk Prev from 794 down to TARGET_START_NUM (788).
        url = START_URL
        print(f"-> Phase1 Prev-walk from {url}")
        driver.get(url)
        if not fetch_chapter(driver, 20):
            print("[!] could not load start page")
            return 3
        for _ in range(20):
            meta = parse_title(driver)
            if meta and meta[0] <= TARGET_START_NUM:
                print(f"   reached chapter {meta[0]}")
                break
            prev = prev_chapter_url(driver)
            if not prev:
                print("   no Prev link")
                break
            print(f"   prev -> {prev}")
            driver.get(prev)
            time.sleep(1.0)
            fetch_chapter(driver, 15)

        # Phase 2: walk Next from 788 up to STOP_AT, saving missing chapters.
        print(f"-> Phase2 Next-walk saving missing chapters up to {STOP_AT}")
        for _ in range(120):
            meta = parse_title(driver)
            if not meta:
                print("[!] cannot parse chapter; advancing via Next")
            else:
                num = meta[0]
                if num >= STOP_AT:
                    # save the boundary chapter too if missing, then stop
                    save_current(driver)
                    print(f"[=] reached chapter {num}; stop-at {STOP_AT}; done.")
                    break
                save_current(driver)

            nxt = next_chapter_url(driver)
            if not nxt:
                print("[=] no Next link; end.")
                break
            driver.get(nxt)
            time.sleep(1.0)
            fetch_chapter(driver, 15)

        return 0
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
