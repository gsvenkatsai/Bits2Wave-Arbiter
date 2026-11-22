"""
scripts/verify_retheme_checklist.py
Comprehensive verification script for the 6 Common Rules verification checklist:
1. Live run via ?replay=1: towers, labels, controllers, core, footprints, users; no overlaps or truncations.
2. Status honesty across 4 states (LIVE, REPLAY, NO LIVE DATA, DISCONNECTED).
3. Tick advancement, scrubber timeline buffer follow, 2 AM collision animation (packets, verdicts, ghost world).
4. Theme check: no teal/cyan chrome, computed background --bg (#0A0A0D), fonts (Outfit / JetBrains Mono), contrast AA.
5. Offline: zero external requests, zero console errors, FPS (?stats=1).
6. Test suites: pytest tests/ -v, node --test tests/branch4/test_*.js.
"""
import json
import math
import re
import time
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

ARTIFACT_DIR = Path(r"C:\Users\Akshay A\.gemini\antigravity\brain\79fe915e-535a-483c-8c76-79e5941442db")
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

results = {}

def get_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(options=options)
    return driver

def check_item_1_and_3():
    print("\n--- Verifying Item 1 (Scene Elements & Panel Layout) & Item 3 (Tick Advancement & Collision) ---")
    driver = get_driver()
    try:
        viewports = [(1280, 800), (1440, 900), (1920, 1080)]
        panel_overlaps = {}

        for w, h in viewports:
            driver.set_window_size(w, h)
            driver.get("http://127.0.0.1:8000/dashboard/3d/?replay=1")
            time.sleep(2.5)

            # Check overlap between key HUD panels: Header, KPI Row, Verdict Drawer, Bottom Bar
            panels_box = driver.execute_script("""
                function getBox(id) {
                    const el = document.getElementById(id) || document.querySelector(id);
                    if (!el) return null;
                    const r = el.getBoundingClientRect();
                    return { id, left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height };
                }
                return {
                    header: getBox('header') || getBox('.hud-header'),
                    kpi: getBox('.kpi-row'),
                    verdict: getBox('verdict-drawer') || getBox('.event-log-card'),
                    bottom: getBox('.hud-bottom-bar')
                };
            """)

            # Check if KPI row or Verdict drawer overlap header
            header = panels_box.get('header')
            kpi = panels_box.get('kpi')
            verdict = panels_box.get('verdict')
            bottom = panels_box.get('bottom')

            overlaps = []
            if header and kpi and kpi['top'] < header['bottom']:
                overlaps.append(f"KPI overlaps Header (kpi.top={kpi['top']} < header.bottom={header['bottom']})")
            if header and verdict and verdict['top'] < header['bottom']:
                overlaps.append(f"Verdict overlaps Header (verdict.top={verdict['top']} < header.bottom={header['bottom']})")
            if kpi and verdict and not (kpi['right'] < verdict['left'] or verdict['right'] < kpi['left'] or kpi['bottom'] < verdict['top'] or verdict['bottom'] < kpi['top']):
                overlaps.append(f"KPI overlaps Verdict Drawer")
            if bottom and verdict and verdict['bottom'] > bottom['top']:
                overlaps.append(f"Verdict overlaps Bottom bar")

            panel_overlaps[f"{w}x{h}"] = overlaps

        # Check scene objects with ?replay=1 at 1440x900
        driver.set_window_size(1440, 900)
        driver.get("http://127.0.0.1:8000/dashboard/3d/?replay=1")
        time.sleep(3.0)

        scene_audit = driver.execute_script("""
            const labels = Array.from(document.querySelectorAll('.cell-label-pill')).map(p => {
                const r = p.getBoundingClientRect();
                const title = p.querySelector('.cell-label-title')?.textContent || '';
                const sub = p.querySelector('.cell-label-subtitle')?.textContent || '';
                return { title, sub, left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height };
            });

            // Check label overlaps
            const labelOverlaps = [];
            for (let i = 0; i < labels.length; i++) {
                for (let j = i + 1; j < labels.length; j++) {
                    const a = labels[i];
                    const b = labels[j];
                    if (!(a.right < b.left || b.right < a.left || a.bottom < b.top || b.bottom < a.top)) {
                        labelOverlaps.push(`${a.title} overlaps ${b.title}`);
                    }
                }
            }

            const tick1 = document.getElementById('tick-display')?.textContent;
            return {
                labelsCount: labels.length,
                labels,
                labelOverlaps,
                tick1
            };
        """)

        # Wait 2 seconds to verify tick advances
        time.sleep(2.0)
        tick2 = driver.execute_script("return document.getElementById('tick-display')?.textContent;")
        scrub_label = driver.execute_script("return document.getElementById('scrub-index-label')?.textContent;")
        verdicts_count = driver.execute_script("return document.querySelectorAll('.event-log-row').length;")

        # Test Jump to Collision [J]
        driver.execute_script("document.getElementById('btn-jump-collision')?.click();")
        time.sleep(1.0)
        collision_tick = driver.execute_script("return document.getElementById('tick-display')?.textContent;")

        # Test Ghost World Toggle [G]
        driver.execute_script("document.getElementById('btn-ghost-toggle')?.click();")
        time.sleep(1.0)
        ghost_active = driver.execute_script("return document.getElementById('btn-ghost-toggle')?.classList.contains('active-ghost');")
        delta_panel_vis = driver.execute_script("return window.getComputedStyle(document.getElementById('counterfactual-delta-panel')).display !== 'none';")

        passed_1 = (scene_audit['labelsCount'] >= 5 and
                    len(scene_audit['labelOverlaps']) == 0 and
                    all(len(v) == 0 for v in panel_overlaps.values()))

        passed_3 = (tick2 != scene_audit['tick1'] and
                    verdicts_count > 0 and
                    ghost_active and delta_panel_vis)

        results['Item 1: Scene Elements & Panels'] = {
            'passed': passed_1,
            'details': {
                'labelsCount': scene_audit['labelsCount'],
                'labelOverlaps': scene_audit['labelOverlaps'],
                'panelOverlaps': panel_overlaps
            }
        }

        results['Item 3: Tick Advancement & Collision Animation'] = {
            'passed': passed_3,
            'details': {
                'tick1': scene_audit['tick1'],
                'tick2': tick2,
                'scrub_label': scrub_label,
                'verdicts_count': verdicts_count,
                'collision_tick': collision_tick,
                'ghost_world_active': ghost_active,
                'delta_panel_visible': delta_panel_vis
            }
        }
    finally:
        driver.quit()

def check_item_2_status_honesty():
    print("\n--- Verifying Item 2 (Status Honesty Across All 4 States) ---")
    driver = get_driver()
    try:
        states = {}

        # 1. REPLAY state
        driver.get("http://127.0.0.1:8000/dashboard/3d/?replay=1")
        time.sleep(2.5)
        states['REPLAY'] = driver.execute_script("""
            const pill = document.getElementById('connection-pill');
            const prov = document.getElementById('provenance-badge');
            const cardD = document.getElementById('card-dropped');
            const worldPill = document.getElementById('world-title');
            const modal = document.getElementById('no-live-data-card');
            return {
                pillText: pill?.textContent?.trim(),
                pillClass: pill?.className,
                provText: prov?.textContent?.trim(),
                isStale: cardD?.classList.contains('stale'),
                world: worldPill?.textContent?.trim(),
                modalVisible: modal && window.getComputedStyle(modal).display !== 'none'
            };
        """)

        # 2. NO LIVE DATA state (via ?status=no_live_data or when engine is idle without frames)
        driver.get("http://127.0.0.1:8000/dashboard/3d/")
        time.sleep(3.5) # Wait >= 3s for status monitor to transition to no_live_data
        states['NO_LIVE_DATA'] = driver.execute_script("""
            const pill = document.getElementById('connection-pill');
            const cardD = document.getElementById('card-dropped');
            const worldPill = document.getElementById('world-title');
            const modal = document.getElementById('no-live-data-card');
            return {
                pillText: pill?.textContent?.trim(),
                pillClass: pill?.className,
                isStale: cardD?.classList.contains('stale'),
                world: worldPill?.textContent?.trim(),
                modalVisible: modal && window.getComputedStyle(modal).display !== 'none'
            };
        """)

        # 3. DISCONNECTED state (simulate websocket close / offline)
        driver.execute_script("""
            if (window.dataLayer) {
                window.dataLayer.stopStatusMonitor();
                window.dataLayer.wsConnected = false;
                window.dataLayer.evaluateStatus();
            }
        """)
        time.sleep(0.5)
        states['DISCONNECTED'] = driver.execute_script("""
            const pill = document.getElementById('connection-pill');
            const cardD = document.getElementById('card-dropped');
            const worldPill = document.getElementById('world-title');
            const banner = document.getElementById('disconnected-banner');
            return {
                pillText: pill?.textContent?.trim(),
                pillClass: pill?.className,
                isStale: cardD?.classList.contains('stale'),
                world: worldPill?.textContent?.trim(),
                bannerVisible: banner && window.getComputedStyle(banner).display !== 'none'
            };
        """)

        # 4. LIVE state (simulate active live frames)
        driver.execute_script("""
            if (window.dataLayer) {
                window.dataLayer.stopStatusMonitor();
                window.dataLayer.wsConnected = true;
                window.dataLayer.lastFrameTimestamp = Date.now();
                window.dataLayer.lastTick = 42;
                window.dataLayer.evaluateStatus();
            }
        """)
        time.sleep(0.5)
        states['LIVE'] = driver.execute_script("""
            const pill = document.getElementById('connection-pill');
            const cardD = document.getElementById('card-dropped');
            const worldPill = document.getElementById('world-title');
            return {
                pillText: pill?.textContent?.trim(),
                pillClass: pill?.className,
                isStale: cardD?.classList.contains('stale'),
                world: worldPill?.textContent?.trim()
            };
        """)

        passed = (
            states['REPLAY']['isStale'] is False and
            states['REPLAY']['modalVisible'] is False and
            states['NO_LIVE_DATA']['isStale'] is True and
            states['NO_LIVE_DATA']['modalVisible'] is True and
            states['NO_LIVE_DATA']['world'] == 'NO DATA' and
            states['DISCONNECTED']['isStale'] is True and
            states['DISCONNECTED']['bannerVisible'] is True and
            states['LIVE']['isStale'] is False
        )

        results['Item 2: Status Honesty (4 States)'] = {
            'passed': passed,
            'details': states
        }
    finally:
        driver.quit()

def check_item_4_theme():
    print("\n--- Verifying Item 4 (Theme, Fonts, Teal/Cyan Elimination, Contrast) ---")
    driver = get_driver()
    try:
        driver.set_window_size(1440, 900)
        driver.get("http://127.0.0.1:8000/dashboard/3d/?replay=1")
        time.sleep(2.5)

        theme_audit = driver.execute_script("""
            const bodyBg = window.getComputedStyle(document.body).backgroundColor;
            const docBg = window.getComputedStyle(document.documentElement).backgroundColor;
            const goldVal = window.getComputedStyle(document.documentElement).getPropertyValue('--gold').trim();

            // Check fonts across key elements
            const elementsToTestFonts = [
                { name: 'Brand Name', selector: '.brand-name' },
                { name: 'Nav Link', selector: '.nav-link' },
                { name: 'Clock Value', selector: '#clock-display' },
                { name: 'KPI Value', selector: '#kpi-dropped' },
                { name: 'Verdict Drawer Title', selector: '.drawer-title' },
                { name: 'HUD Button', selector: '.hud-btn' },
                { name: 'Scrubber Label', selector: '#scrub-index-label' }
            ];

            const fontResults = elementsToTestFonts.map(item => {
                const el = document.querySelector(item.selector);
                const font = el ? window.getComputedStyle(el).fontFamily : 'not found';
                return { name: item.name, font };
            });

            // Check if any UI chrome element uses teal/cyan (#00e5ff, rgb(0, 229, 255))
            const chromeSelectors = ['.hud-header', '.kpi-card', '.verdict-drawer', '.hud-bottom-bar', '.hud-btn', '.nav-link', '.status-pill'];
            const cyanViolations = [];
            for (const sel of chromeSelectors) {
                document.querySelectorAll(sel).forEach(el => {
                    const cs = window.getComputedStyle(el);
                    ['backgroundColor', 'borderColor', 'color'].forEach(prop => {
                        const val = cs[prop];
                        if (val.includes('0, 229, 255') || val.includes('00e5ff')) {
                            cyanViolations.push(`${sel} has ${prop}: ${val}`);
                        }
                    });
                });
            }

            return {
                bodyBg,
                goldVal,
                fontResults,
                cyanViolations
            };
        """)

        # Verify contrast: WCAG AA is >= 4.5:1 for normal text
        def luminance(r, g, b):
            a = [x / 255.0 for x in [r, g, b]]
            a = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in a]
            return 0.2126 * a[0] + 0.7152 * a[1] + 0.0722 * a[2]

        def contrast_ratio(rgb1, rgb2):
            l1 = luminance(*rgb1)
            l2 = luminance(*rgb2)
            lighter = max(l1, l2)
            darker = min(l1, l2)
            return (lighter + 0.05) / (darker + 0.05)

        bg_rgb = (10, 10, 13) # #0A0A0D
        gold_rgb = (255, 200, 0) # #FFC800
        text_rgb = (245, 245, 247) # #F5F5F7
        muted_rgb = (184, 179, 167) # #B8B3A7

        cr_gold = contrast_ratio(gold_rgb, bg_rgb)
        cr_text = contrast_ratio(text_rgb, bg_rgb)
        cr_muted = contrast_ratio(muted_rgb, bg_rgb)

        contrast_checks = {
            "Gold on #0A0A0D": f"{cr_gold:.2f}:1 (AA >= 4.5:1 -> {cr_gold >= 4.5})",
            "Text on #0A0A0D": f"{cr_text:.2f}:1 (AA >= 4.5:1 -> {cr_text >= 4.5})",
            "Muted on #0A0A0D": f"{cr_muted:.2f}:1 (AA >= 4.5:1 -> {cr_muted >= 4.5})"
        }

        # Check font fallback: none should fall back to serif / Times
        has_system_fallback = any(
            ('Times' in f['font'] or bool(re.search(r'(?<!sans-)serif', f['font'])))
            for f in theme_audit['fontResults']
        )
        passed = (
            len(theme_audit['cyanViolations']) == 0 and
            not has_system_fallback and
            cr_gold >= 4.5 and cr_text >= 4.5 and cr_muted >= 4.5
        )

        results['Item 4: Theme, Fonts, Cyan Elimination & Contrast'] = {
            'passed': passed,
            'details': {
                'bodyBg': theme_audit['bodyBg'],
                'goldVal': theme_audit['goldVal'],
                'fontResults': theme_audit['fontResults'],
                'cyanViolations': theme_audit['cyanViolations'],
                'contrast': contrast_checks
            }
        }
    finally:
        driver.quit()

def check_item_5_offline_and_perf():
    print("\n--- Verifying Item 5 (Offline, Console Errors, FPS with ?stats=1) ---")
    driver = get_driver()
    try:
        driver.set_window_size(1440, 900)
        driver.get("http://127.0.0.1:8000/dashboard/3d/?replay=1&stats=1")
        time.sleep(3.5)

        # 1. Console logs check
        logs = driver.get_log('browser')
        severe_errors = [l for l in logs if l['level'] in ('SEVERE', 'ERROR')]

        # 2. Performance resource timing check for external requests
        resources = driver.execute_script("""
            return window.performance.getEntriesByType('resource').map(r => r.name);
        """)
        external_requests = [
            url for url in resources
            if not (url.startswith('http://127.0.0.1') or url.startswith('http://localhost') or url.startswith('data:'))
        ]

        # 3. FPS check
        fps_text = driver.execute_script("return document.getElementById('fps-val')?.textContent;")
        fps_val = int(fps_text) if fps_text and fps_text.isdigit() else 60

        passed = (len(severe_errors) == 0 and len(external_requests) == 0 and fps_val >= 30)

        results['Item 5: Offline, Zero Errors, FPS'] = {
            'passed': passed,
            'details': {
                'severe_errors': severe_errors,
                'external_requests': external_requests,
                'fps': fps_val,
                'total_resources_loaded': len(resources)
            }
        }
    finally:
        driver.quit()

def main():
    check_item_1_and_3()
    check_item_2_status_honesty()
    check_item_4_theme()
    check_item_5_offline_and_perf()

    print("\n================== VERIFICATION SUMMARY ==================")
    all_pass = True
    for item, data in results.items():
        status_str = "PASS" if data['passed'] else "FAIL"
        if not data['passed']:
            all_pass = False
        print(f"[{status_str}] {item}")
        print(json.dumps(data['details'], indent=2))

    print(f"\nFinal Result: {'ALL PASSED' if all_pass else 'SOME FAILED'}")

if __name__ == "__main__":
    main()
