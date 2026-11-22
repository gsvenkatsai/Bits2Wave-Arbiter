"""
scripts/capture_3d_theme_viewports.py
Capture high-resolution screenshots of the re-themed 3D Digital Twin Monitor across viewports:
- 1280x800 (Compact Desktop)
- 1440x900 (Standard Desktop)
- 1920x1080 (Full HD Desktop)
"""
import time
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

ARTIFACT_DIR = Path(r"C:\Users\Akshay A\.gemini\antigravity\brain\79fe915e-535a-483c-8c76-79e5941442db")
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

VIEWPORTS = [
    ("1280x800", 1280, 800),
    ("1440x900", 1440, 900),
    ("1920x1080", 1920, 1080),
]


def main():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--use-gl=angle")
    options.add_argument("--use-angle=swiftshader")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Chrome(options=options)

    try:
        for vp_name, width, height in VIEWPORTS:
            driver.set_window_size(width, height)
            print(f"--- Capturing 3D Monitor at Viewport: {vp_name} ({width}x{height}) ---")

            # Load 3D monitor in replay mode so real frames are loaded immediately
            driver.get("http://127.0.0.1:8000/dashboard/3d/?replay=1")
            
            # Wait for WebGL canvas and dataLayer to initialize and render frames
            time.sleep(3.5)

            # Trigger play replay if button is present or ensure replay playback
            driver.execute_script("""
                const btn = document.getElementById('btn-play-replay');
                if (btn && btn.offsetParent !== null) {
                    btn.click();
                }
            """)
            time.sleep(2.0)

            # Capture screenshot
            out_filename = f"rethemed_3d_monitor_{vp_name}.png"
            out_path = ARTIFACT_DIR / out_filename
            driver.save_screenshot(str(out_path))
            print(f"Captured: {out_filename} ({out_path.stat().st_size} bytes)")

    finally:
        driver.quit()

if __name__ == "__main__":
    main()
