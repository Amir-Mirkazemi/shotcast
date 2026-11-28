"""Capture README screenshots from the live site. Run by the 'screenshots' workflow.

    python scripts/screenshots.py [BASE_URL] [OUT_DIR]
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = (sys.argv[1] if len(sys.argv) > 1 else "https://amir-mirkazemi.github.io/shotcast/").rstrip("/") + "/"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "docs/img")

PAGES = [
    ("site-home", "index.html", 1280, 900),
    ("site-forecast", "forecast.html", 1280, 1100),
    ("site-health", "health.html", 1280, 900),
    ("site-method", "method.html", 1280, 1000),
    ("site-mobile", "index.html", 390, 844),
]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, path, w, h in PAGES:
            page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=2)
            resp = page.goto(BASE + path, wait_until="networkidle")
            if resp is None or not resp.ok:
                print(f"skip {name}: {resp.status if resp else 'no response'}")
                page.close()
                continue
            page.screenshot(path=str(OUT / f"{name}.png"))
            print("saved", OUT / f"{name}.png")
            page.close()
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
