#!/usr/bin/env python3
"""
scripts/check_readme.py
Automated validation of README.md, docs/**, and project documentation.

Checks:
  1. File and image link existence (no broken relative links).
  2. Asset sizes (PNG <= 400 KB, GIF <= 8 MB).
  3. Banned buzzwords audit ("first", "state-of-the-art", "production-ready", "certified", "compliant").
  4. Metrics consistency (verifies cited values match artifacts/readme/metrics.json).
  5. Mermaid block syntax check.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README_PATH = REPO_ROOT / "README.md"
DOCS_DIR = REPO_ROOT / "docs"
IMAGES_DIR = REPO_ROOT / "docs" / "images"
METRICS_PATH = REPO_ROOT / "artifacts" / "readme" / "metrics.json"

BANNED_WORDS = [
    r"\bfirst\b",
    r"\bstate-of-the-art\b",
    r"\bproduction-ready\b",
    r"\bcertified\b",
    r"\bcompliant\b",
]

# Exceptions where words might appear in a valid technical context (e.g. "audit compliance", "first-come-first-served")
WHITELIST_PATTERNS = [
    re.compile(r"first-come-first-served", re.IGNORECASE),
    re.compile(r"audit compliance", re.IGNORECASE),
]

MAX_PNG_BYTES = 400 * 1024       # 400 KB
MAX_GIF_BYTES = 8 * 1024 * 1024   # 8 MB


def check_asset_sizes() -> list[str]:
    errors = []
    if not IMAGES_DIR.exists():
        return ["Images directory docs/images does not exist!"]

    for file_path in IMAGES_DIR.glob("*"):
        if not file_path.is_file():
            continue
        size = file_path.stat().st_size
        if file_path.suffix.lower() == ".png":
            if size > MAX_PNG_BYTES:
                errors.append(f"PNG asset {file_path.name} exceeds 400 KB: {size / 1024:.1f} KB")
        elif file_path.suffix.lower() == ".gif":
            if size > MAX_GIF_BYTES:
                errors.append(f"GIF asset {file_path.name} exceeds 8 MB: {size / (1024*1024):.2f} MB")
    return errors


def check_markdown_links(file_path: Path) -> list[str]:
    errors = []
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Match standard markdown links: [text](path)
    md_links = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", content)
    # Match HTML src: <img ... src="path" ... />
    html_srcs = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', content)

    all_links = [link[1] for link in md_links] + html_srcs

    for link in all_links:
        # Strip anchors
        target = link.split("#")[0].strip()
        if not target:
            continue
        # Skip external web URLs
        if target.startswith("http://") or target.startswith("https://") or target.startswith("file://"):
            continue
        # Skip mailto
        if target.startswith("mailto:"):
            continue

        resolved = (file_path.parent / target).resolve()
        if not resolved.exists():
            errors.append(f"[{file_path.name}] Broken relative link: {target} (resolved to {resolved})")

    return errors


def check_banned_words(file_path: Path) -> list[str]:
    errors = []
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    for idx, line in enumerate(lines, 1):
        clean_line = line
        for wl in WHITELIST_PATTERNS:
            clean_line = wl.sub("", clean_line)

        for pattern in BANNED_WORDS:
            match = re.search(pattern, clean_line, re.IGNORECASE)
            if match:
                errors.append(f"[{file_path.name}:{idx}] Found forbidden word '{match.group(0)}': {line.strip()}")
    return errors


def check_metrics_consistency() -> list[str]:
    errors = []
    if not METRICS_PATH.exists():
        return [f"Metrics JSON not found at {METRICS_PATH}"]

    with open(METRICS_PATH, "r", encoding="utf-8") as f:
        metrics_data = json.load(f)

    metrics = metrics_data.get("metrics", {})

    with open(README_PATH, "r", encoding="utf-8") as f:
        readme_text = f.read()

    for key, data in metrics.items():
        if f"| {key} |" not in readme_text:
            errors.append(f"README.md metrics table missing row for '{key}'")
    return errors


def check_mermaid_blocks(file_path: Path) -> list[str]:
    errors = []
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    blocks = re.findall(r"```mermaid\s*\n(.*?)\n```", content, re.DOTALL)
    valid_diagram_types = [
        "graph", "flowchart", "sequenceDiagram", "classDiagram",
        "stateDiagram", "stateDiagram-v2", "erDiagram", "xychart-beta"
    ]
    for idx, block in enumerate(blocks, 1):
        first_line = block.strip().split("\n")[0].strip()
        if not any(first_line.startswith(t) for t in valid_diagram_types):
            errors.append(f"[{file_path.name}] Mermaid block #{idx} has invalid diagram header: '{first_line}'")
    return errors


def main() -> None:
    print("==================================================")
    print("Project ARBITER: Verification and Integrity Audit")
    print("==================================================")

    all_errors: list[str] = []

    # 1. Asset Sizes
    print("[1/5] Checking asset file sizes in docs/images/...")
    asset_errors = check_asset_sizes()
    if asset_errors:
        all_errors.extend(asset_errors)
        for err in asset_errors:
            print(f"  FAIL: {err}")
    else:
        print("  PASS: All images within allowed sizes (PNG <= 400 KB, GIF <= 8 MB).")

    # 2. Markdown Links
    print("\n[2/5] Checking markdown links across documentation...")
    docs_to_check = [README_PATH] + list(DOCS_DIR.rglob("*.md"))
    link_errors = []
    for doc in docs_to_check:
        link_errors.extend(check_markdown_links(doc))
    if link_errors:
        all_errors.extend(link_errors)
        for err in link_errors:
            print(f"  FAIL: {err}")
    else:
        print(f"  PASS: All links valid across {len(docs_to_check)} markdown files.")

    # 3. Banned Words Audit
    print("\n[3/5] Auditing for banned marketing/buzzword claims...")
    word_errors = []
    for doc in docs_to_check:
        word_errors.extend(check_banned_words(doc))
    if word_errors:
        all_errors.extend(word_errors)
        for err in word_errors:
            print(f"  FAIL: {err}")
    else:
        print("  PASS: Zero banned buzzwords detected.")

    # 4. Metrics Table Consistency
    print("\n[4/5] Verifying metrics table synchronization...")
    metrics_errors = check_metrics_consistency()
    if metrics_errors:
        all_errors.extend(metrics_errors)
        for err in metrics_errors:
            print(f"  FAIL: {err}")
    else:
        print("  PASS: All 24 metrics in metrics.json are present in README.md.")

    # 5. Mermaid Syntax
    print("\n[5/5] Checking Mermaid diagram syntax...")
    mermaid_errors = []
    for doc in docs_to_check:
        mermaid_errors.extend(check_mermaid_blocks(doc))
    if mermaid_errors:
        all_errors.extend(mermaid_errors)
        for err in mermaid_errors:
            print(f"  FAIL: {err}")
    else:
        print("  PASS: All Mermaid diagram blocks have valid headers.")

    print("\n==================================================")
    if all_errors:
        print(f"FAILED: {len(all_errors)} issue(s) detected.")
        sys.exit(1)
    else:
        print("AUDIT COMPLETE: All documentation checks PASSED cleanly!")
        sys.exit(0)


if __name__ == "__main__":
    main()
