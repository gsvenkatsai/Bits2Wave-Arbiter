"""
scripts/capture_all_viewports.py
Capture high-resolution screenshots of all 6 pages + 404 across 3 standard viewports:
- Mobile: 390x844
- Desktop Standard: 1440x900
- Desktop Widescreen: 1920x1080
"""
import time
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

ARTIFACT_DIR = Path(r"C:\Users\Akshay A\.gemini\antigravity\brain\79fe915e-535a-483c-8c76-79e5941442db")
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

PAGES = [
    ("home", "http://127.0.0.1:8000/"),
    ("problem", "http://127.0.0.1:8000/problem"),
    ("how_it_works", "http://127.0.0.1:8000/how-it-works"),
    ("results", "http://127.0.0.1:8000/results"),
    ("monitor", "http://127.0.0.1:8000/monitor"),
    ("team", "http://127.0.0.1:8000/team"),
    ("404", "http://127.0.0.1:8000/404"),
]

VIEWPORTS = [
    ("390", 390, 844),
    ("1440", 1440, 900),
    ("1920", 1920, 1080),
]


def main():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")

    driver = webdriver.Chrome(options=options)

    try:
        for vp_name, width, height in VIEWPORTS:
            driver.set_window_size(width, height)
            print(f"--- Viewport: {vp_name} ({width}x{height}) ---")

            for page_name, url in PAGES:
                driver.get(url)
                # Wait for animation/hydration/SVG
                time.sleep(1.2)

                out_filename = f"screenshot_{page_name}_{vp_name}.png"
                out_path = ARTIFACT_DIR / out_filename
                driver.save_screenshot(str(out_path))
                print(f"Captured: {out_filename} ({out_path.stat().st_size} bytes)")

    finally:
        driver.quit()

if __name__ == "__main__":
    main()
