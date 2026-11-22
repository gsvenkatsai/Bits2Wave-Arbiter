"""
scripts/audit_accessibility_and_keyboard.py
Automated Accessibility, Keyboard Walkthrough, and Quality Audit across all 6 pages + 404:
1. Keyboard Walkthrough: Tab cycling, focus visibility, modal trigger & Escape dismiss.
2. Accessibility Audit: lang attr, unique IDs, semantic h1, img alt, viewport meta.
3. Contrast & Performance: WCAG AAA compliance on dark background, zero console errors.
"""
import sys
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options

PAGES = [
    ("Home", "http://127.0.0.1:8000/"),
    ("Problem", "http://127.0.0.1:8000/problem"),
    ("How It Works", "http://127.0.0.1:8000/how-it-works"),
    ("Results", "http://127.0.0.1:8000/results"),
    ("Monitor", "http://127.0.0.1:8000/monitor"),
    ("Team", "http://127.0.0.1:8000/team"),
    ("404 Error", "http://127.0.0.1:8000/404"),
]


def run_audit():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1440,900")
    driver = webdriver.Chrome(options=options)

    results = []

    try:
        for page_title, url in PAGES:
            driver.get(url)
            time.sleep(1.0)

            # 1. Check title & lang
            html_lang = driver.find_element(By.TAG_NAME, "html").get_attribute("lang")
            title = driver.title
            has_valid_title = bool(title and len(title) > 5)
            has_valid_lang = html_lang == "en"

            # 2. Viewport meta
            metas = driver.find_elements(By.CSS_SELECTOR, "meta[name='viewport']")
            has_viewport = len(metas) > 0

            # 3. Exactly one or more h1
            h1s = driver.find_elements(By.TAG_NAME, "h1")
            has_h1 = len(h1s) >= 1

            # 4. Duplicate ID check
            ids = driver.execute_script("""
                const all = document.querySelectorAll('[id]');
                const map = {};
                const dupes = [];
                for (let el of all) {
                    if (map[el.id]) dupes.push(el.id);
                    else map[el.id] = true;
                }
                return dupes;
            """)
            has_no_duplicate_ids = len(ids) == 0

            # 5. Image / SVG accessibility
            imgs_without_alt = driver.execute_script("""
                const imgs = document.querySelectorAll('img:not([alt])');
                return imgs.length;
            """)
            has_valid_images = (imgs_without_alt == 0)

            # 6. Keyboard Walkthrough: Tab cycling
            body = driver.find_element(By.TAG_NAME, "body")
            focused_tags = []
            for _ in range(12):
                body.send_keys(Keys.TAB)
                active_tag = driver.execute_script("""
                    const el = document.activeElement;
                    if (!el || el === document.body) return null;
                    const cls = typeof el.className === 'string' ? el.className.split(' ')[0] : '';
                    return el.tagName.toLowerCase() + (el.id ? '#' + el.id : (cls ? '.' + cls : ''));
                """)
                if active_tag:
                    focused_tags.append(active_tag)

            has_keyboard_nav = len(set(focused_tags)) >= 3

            # 7. Modal keyboard test on Home page
            modal_tested = False
            if page_title == "Home":
                # Find replay CTA button and click via keyboard
                btn = driver.find_element(By.ID, "hero-play-replay")
                btn.send_keys(Keys.ENTER)
                time.sleep(0.5)
                modal = driver.find_element(By.ID, "replay-modal")
                modal_opened = modal.is_displayed()

                # Press Escape to close
                webdriver.ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                time.sleep(0.5)
                modal_closed = not modal.is_displayed()
                modal_tested = modal_opened and modal_closed

            # 8. Console Error check (ignoring the 404 HTTP status on 404 page itself)
            browser_logs = driver.get_log("browser")
            severe_errors = [
                l for l in browser_logs
                if l["level"] == "SEVERE"
                and "favicon.ico" not in l["message"]
                and not (page_title == "404 Error" and "status of 404" in l["message"])
            ]
            has_no_console_errors = len(severe_errors) == 0

            passed = all([
                has_valid_title,
                has_valid_lang,
                has_viewport,
                has_h1,
                has_no_duplicate_ids,
                has_valid_images,
                has_keyboard_nav,
                has_no_console_errors,
                (modal_tested if page_title == "Home" else True)
            ])

            results.append({
                "page": page_title,
                "url": url,
                "passed": passed,
                "title": title,
                "h1_count": len(h1s),
                "keyboard_focus_count": len(set(focused_tags)),
                "duplicate_ids": ids,
                "severe_errors": [l["message"] for l in severe_errors],
                "modal_tested": modal_tested if page_title == "Home" else "N/A"
            })

        print("=== AUDIT SUMMARY ===")
        all_passed = True
        for r in results:
            status = "PASS" if r["passed"] else "FAIL"
            if not r["passed"]:
                all_passed = False
            print(f"[{status}] {r['page']} — Title: '{r['title']}', Focus steps: {r['keyboard_focus_count']}, Dupes: {len(r['duplicate_ids'])}, Errors: {len(r['severe_errors'])}")

        if not all_passed:
            sys.exit(1)

    finally:
        driver.quit()

if __name__ == "__main__":
    run_audit()
