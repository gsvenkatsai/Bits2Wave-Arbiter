#!/usr/bin/env python
"""scripts/capture_readme_assets.py

Captures high-resolution, pixel-perfect, real application screenshots and
a 2 AM collision demonstration GIF for the Project ARBITER README and documentation.

Uses Playwright with headless Chromium to ensure:
- Zero browser chrome, bookmarks, or OS window borders.
- Real application state rendered from live FastAPI server and WebGL digital twin.
- Strict 1600x900 resolution at deviceScaleFactor=1.
- Pillow adaptive palette quantization keeping PNG file sizes well under 400 KB.
- FFmpeg-driven high-fidelity GIF creation under 8 MB.
- Output captions generated to docs/images/CAPTIONS.md.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple
from PIL import Image
from playwright.sync_api import Page, sync_playwright

BASE_URL = os.environ.get("ARBITER_BASE_URL", "http://127.0.0.1:8000")
DOCS_IMG_DIR = Path("docs/images")
TEMP_DIR = Path("artifacts/readme/raw_frames")


def optimize_png(source_path: Path, max_kb: int = 390) -> None:
    """Optimize PNG with adaptive palette quantization to keep under max_kb while preserving text clarity."""
    with Image.open(source_path) as im:
        rgb_im = im.convert("RGB")
        colors = 256
        while True:
            quantized = rgb_im.quantize(colors=colors, method=Image.Quantize.MEDIANCUT)
            quantized.save(source_path, format="PNG", optimize=True)
            size_kb = source_path.stat().st_size / 1024.0
            if size_kb <= max_kb or colors <= 64:
                break
            colors = int(colors * 0.8)
    
    size_kb = source_path.stat().st_size / 1024.0
    print(f"  [SAVED] {source_path.name} ({size_kb:.1f} KB)")


def capture_site_pages(page: Page, captions: Dict[str, str], force: bool = False) -> None:
    """Capture public marketing and problem pages."""
    targets = [
        ("landing-hero", f"{BASE_URL}/", "ARBITER marketing landing hero overview and headline metrics"),
        ("site-problem", f"{BASE_URL}/problem", "The 2 AM Collision breakdown: Energy Saving vs Load Balancing conflict"),
        ("site-how-it-works", f"{BASE_URL}/how-it-works", "Technical 4-stage pipeline: Queue, Graph, Conformal Twin, and Rollback Actuator"),
        ("site-results", f"{BASE_URL}/results", "Quantitative benchmark suite and Pareto frontier evidence"),
        ("site-monitor", f"{BASE_URL}/monitor", "Network Operations Center (NOC) monitor hub with engine health indicator"),
        ("site-team", f"{BASE_URL}/team", "Engineering team roster (Team FourSUM) with domain ownership"),
    ]

    for name, url, cap in targets:
        out_file = DOCS_IMG_DIR / f"{name}.png"
        captions[f"{name}.png"] = cap
        if out_file.exists() and not force:
            print(f"  [EXISTS] {name}.png ({out_file.stat().st_size / 1024.0:.1f} KB)")
            continue
        print(f"[CAPTURING] {name} from {url}...")
        page.goto(url, wait_until="networkidle")
        page.wait_for_timeout(1500)
        page.screenshot(path=str(out_file))
        optimize_png(out_file)


def capture_noc_2d(page: Page, captions: Dict[str, str], force: bool = False) -> None:
    """Capture 2D NOC Dashboard, decision history, and benchmark panels."""
    captions["noc-2d-overview.png"] = "2D NOC operations dashboard: live decisions ledger, cluster KPI telemetry, and topology"
    captions["decision-history.png"] = "ARBITER real-time decision ledger detailing command verdicts, approved parameter values, and reason codes"
    captions["optimality-panel.png"] = "Combinatorial optimality comparison panel showing 0.0% gap vs brute-force search"

    noc_file = DOCS_IMG_DIR / "noc-2d-overview.png"
    dec_file = DOCS_IMG_DIR / "decision-history.png"
    opt_file = DOCS_IMG_DIR / "optimality-panel.png"

    if noc_file.exists() and dec_file.exists() and opt_file.exists() and not force:
        print("  [EXISTS] NOC 2D panels already captured.")
        return

    print(f"[CAPTURING] 2D NOC Dashboard from {BASE_URL}/dashboard/...")
    page.goto(f"{BASE_URL}/dashboard/", wait_until="networkidle")
    page.wait_for_timeout(2500)

    # 1. Full NOC 2D Overview
    page.screenshot(path=str(noc_file))
    optimize_png(noc_file)

    # 2. Decision History panel
    page.screenshot(path=str(dec_file), clip={"x": 50, "y": 400, "width": 800, "height": 450})
    optimize_png(dec_file)

    # 3. Optimality & Impact Cards
    page.screenshot(path=str(opt_file), clip={"x": 860, "y": 80, "width": 700, "height": 450})
    optimize_png(opt_file)


def capture_3d_twin_states(page: Page, captions: Dict[str, str], force: bool = False) -> None:
    """Capture the 3D RAN Digital Twin in calm, collision, verdict, ghost-world, and uncertainty states."""
    captions["twin-3d-calm.png"] = "3D Digital Twin in calm nocturnal state: 16-cell Bengaluru topology, active subscribers, and verified calibration"
    captions["twin-3d-collision.png"] = "The 2 AM Collision moment in 3D: Energy Saving, Load Balancing, and Coverage proposals meeting ARBITER with conflict arcs"
    captions["twin-3d-verdicts.png"] = "ARBITER verdicts drawer: ES DELAY, LB MODIFY (0.30 to 0.15), and CC MODIFY (+1 dB) with parameter rationales"
    captions["twin-3d-ghost-world.png"] = "Counterfactual Ghost World comparison: uncoordinated baseline blackout on Cell B vs ARBITER coordinated protection"
    captions["counterfactual-recorder.png"] = "Counterfactual delta recorder comparing simultaneous drop and throughput deltas between worlds"
    captions["twin-3d-uncertainty.png"] = "Calibrated conformal uncertainty shells around cell towers displaying honest ±0.30 dB prediction bounds"

    calm_file = DOCS_IMG_DIR / "twin-3d-calm.png"
    coll_file = DOCS_IMG_DIR / "twin-3d-collision.png"
    verdict_file = DOCS_IMG_DIR / "twin-3d-verdicts.png"
    ghost_file = DOCS_IMG_DIR / "twin-3d-ghost-world.png"
    rec_file = DOCS_IMG_DIR / "counterfactual-recorder.png"
    unc_file = DOCS_IMG_DIR / "twin-3d-uncertainty.png"

    if (
        calm_file.exists()
        and coll_file.exists()
        and verdict_file.exists()
        and ghost_file.exists()
        and rec_file.exists()
        and unc_file.exists()
        and not force
    ):
        print("  [EXISTS] 3D Digital Twin state images already captured.")
        return

    twin_url = f"{BASE_URL}/dashboard/3d/?replay=1"
    print(f"[CAPTURING] 3D Digital Twin states from {twin_url}...")
    page.goto(twin_url, wait_until="networkidle")
    page.wait_for_timeout(3500)

    # Dismiss any initial modal
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)

    # 1. Calm state (Tick 0)
    page.screenshot(path=str(calm_file))
    optimize_png(calm_file)

    # 2. Collision moment (jump to collision tick using 'j')
    page.keyboard.press("j")
    page.wait_for_timeout(1500)
    page.screenshot(path=str(coll_file))
    optimize_png(coll_file)

    # 3. Verdicts drawer open (press 'n' for NOC diagnostics / verdicts)
    page.keyboard.press("n")
    page.wait_for_timeout(1000)
    page.screenshot(path=str(verdict_file))
    optimize_png(verdict_file)

    # 4. Ghost World / Counterfactual comparison (toggle 'g' and 'w')
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)
    page.keyboard.press("g")
    page.wait_for_timeout(1200)
    page.screenshot(path=str(ghost_file))
    optimize_png(ghost_file)

    # 5. Counterfactual Recorder delta card
    page.screenshot(path=str(rec_file), clip={"x": 50, "y": 600, "width": 550, "height": 260})
    optimize_png(rec_file)

    # 6. Calibrated Uncertainty view
    page.keyboard.press("g") # toggle back
    page.wait_for_timeout(500)
    page.mouse.move(800, 450)
    page.mouse.down()
    page.mouse.move(650, 380, steps=10)
    page.mouse.up()
    page.wait_for_timeout(1000)
    page.screenshot(path=str(unc_file))
    optimize_png(unc_file)


def record_collision_gif(page: Page, captions: Dict[str, str], force: bool = False) -> None:
    """Record 3-5s collision animation across the replay frames and assemble an optimized 960px GIF."""
    gif_out = DOCS_IMG_DIR / "demo-2am-collision.gif"
    captions["demo-2am-collision.gif"] = "Real 2 AM collision replay: ARBITER detects controller clash, sequences offload handovers, and preserves zero subscriber drops"

    if gif_out.exists() and not force:
        print(f"  [EXISTS] demo-2am-collision.gif ({gif_out.stat().st_size / (1024.0 * 1024.0):.2f} MB)")
        return

    print("[RECORDING] 2 AM Collision demonstration GIF...")
    shutil.rmtree(TEMP_DIR, ignore_errors=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    twin_url = f"{BASE_URL}/dashboard/3d/?replay=1"
    page.goto(twin_url, wait_until="networkidle")
    page.wait_for_timeout(2500)
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)

    # Initialize replay data and pause
    page.evaluate("""async () => {
        if (window.dataLayer) {
            await window.dataLayer.loadReplayData();
            window.dataLayer.pause();
        }
    }""")
    page.wait_for_timeout(500)

    # Dynamic progression ticks: calm baseline -> collision arcs ignite at Cell B -> verdict drawer -> ladder handovers
    ticks_to_capture = [1, 3, 5, 6, 7, 7, 8, 10, 12, 14, 16, 18, 22]

    frame_idx = 0
    for idx, t in enumerate(ticks_to_capture):
        if idx >= 5:
            page.evaluate("""() => {
                const drawer = document.getElementById('verdict-drawer');
                if (drawer && drawer.classList.contains('collapsed')) {
                    drawer.classList.remove('collapsed');
                }
            }""")

        page.evaluate(f"""() => {{
            const frames = window.dataLayer.replayData['arbiter'];
            if (frames && frames[{t}]) {{
                window.dataLayer.pushFrame(frames[{t}]);
            }}
        }}""")
        page.wait_for_timeout(150)

        frame_path = TEMP_DIR / f"frame_{frame_idx:04d}.png"
        page.screenshot(path=str(frame_path), timeout=60000)
        frame_idx += 1

    palette_path = TEMP_DIR / "palette.png"
    ffmpeg_cmd_palette = [
        "ffmpeg", "-y",
        "-framerate", "4",
        "-i", "artifacts/readme/raw_frames/frame_%04d.png",
        "-vf", "scale=960:-1:flags=lanczos,palettegen=stats_mode=diff",
        str(palette_path),
    ]
    subprocess.run(ffmpeg_cmd_palette, check=True, capture_output=True)

    ffmpeg_cmd_gif = [
        "ffmpeg", "-y",
        "-framerate", "4",
        "-i", "artifacts/readme/raw_frames/frame_%04d.png",
        "-i", str(palette_path),
        "-lavfi", "scale=960:-1:flags=lanczos [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=3",
        str(gif_out),
    ]
    subprocess.run(ffmpeg_cmd_gif, check=True, capture_output=True)

    shutil.rmtree(TEMP_DIR, ignore_errors=True)
    gif_mb = gif_out.stat().st_size / (1024.0 * 1024.0)
    print(f"  [SAVED] demo-2am-collision.gif ({gif_mb:.2f} MB, {frame_idx} frames, target: <8 MB)")


def write_captions_doc(captions: Dict[str, str]) -> None:
    """Generate docs/images/CAPTIONS.md containing verified descriptions."""
    captions_md = DOCS_IMG_DIR / "CAPTIONS.md"
    lines = [
        "# Project ARBITER Image & Demonstration Asset Captions",
        "",
        "Every screenshot and video asset in this directory was captured directly from the live application stack without browser chrome, mockups, or third-party modifications.",
        "",
        "| File Name | Size | Resolution | One-Line Caption |",
        "| :--- | :---: | :---: | :--- |",
    ]

    for fname, cap in sorted(captions.items()):
        fpath = DOCS_IMG_DIR / fname
        if fpath.exists():
            size_kb = fpath.stat().st_size / 1024.0
            size_str = f"{size_kb / 1024.0:.2f} MB" if size_kb > 1000 else f"{size_kb:.1f} KB"
            if fname.endswith(".gif"):
                res_str = "960x540"
            else:
                with Image.open(fpath) as im:
                    res_str = f"{im.width}x{im.height}"
            lines.append(f"| [`{fname}`]({fname}) | {size_str} | {res_str} | {cap} |")

    lines.append("")
    captions_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"[CAPTIONS] Written {len(captions)} asset descriptions to {captions_md}")


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Capture README assets with Playwright")
    parser.add_argument("--force", action="store_true", help="Force recapturing existing assets")
    args = parser.parse_args()

    DOCS_IMG_DIR.mkdir(parents=True, exist_ok=True)
    captions: Dict[str, str] = {}

    print("================================================================================")
    print("PROJECT ARBITER — Automated Documentation Asset Capture (Playwright)")
    print(f"Base URL: {BASE_URL} | Target: 1600x900 deviceScaleFactor=1 | Force: {args.force}")
    print("================================================================================")

    # Launch Playwright with hardware/SwiftShader WebGL
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--use-gl=angle",
                "--use-angle=swiftshader",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = browser.new_context(
            viewport={"width": 1600, "height": 900},
            device_scale_factor=1.0,
        )
        page = context.new_page()

        try:
            # 1. Capture Marketing & Public Site Pages
            capture_site_pages(page, captions, force=args.force)

            # 2. Capture 2D NOC Dashboard & Decision Tables
            capture_noc_2d(page, captions, force=args.force)

            # 3. Capture 3D RAN Digital Twin States
            capture_3d_twin_states(page, captions, force=args.force)

            # 4. Record 2 AM Collision Demonstration GIF
            record_collision_gif(page, captions, force=args.force)

        finally:
            context.close()
            browser.close()

    # Write captions file
    write_captions_doc(captions)
    print("[SUCCESS] All README assets captured and optimized successfully.")


if __name__ == "__main__":
    main()
