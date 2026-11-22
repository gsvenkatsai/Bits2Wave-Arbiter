#!/usr/bin/env python3
"""
scripts/update_readme_metrics.py
Synchronizes metric tables in README.md and docs/REPRODUCE.md from artifacts/readme/metrics.json.

Usage:
    python scripts/update_readme_metrics.py [--check]
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
from pathlib import Path

START_MARKER = "<!-- METRICS:START -->"
END_MARKER = "<!-- METRICS:END -->"

REPO_ROOT = Path(__file__).resolve().parent.parent
METRICS_JSON_PATH = REPO_ROOT / "artifacts" / "readme" / "metrics.json"
TARGET_FILES = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "docs" / "REPRODUCE.md",
]


def load_metrics(json_path: Path) -> dict:
    if not json_path.exists():
        print(f"Error: metrics file not found at {json_path}", file=sys.stderr)
        sys.exit(1)
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_markdown_table(metrics_data: dict) -> str:
    metrics = metrics_data.get("metrics", {})
    lines = [
        "| Metric | Value | Unit | Status | Source Command |",
        "|---|---|---|---|---|",
    ]
    for key, data in metrics.items():
        val = data.get("value")
        unit = data.get("unit", "")
        status = str(data.get("status", "")).upper()
        cmd = data.get("source_command", "")
        if isinstance(val, float):
            val_str = f"{val:.2f}".rstrip("0").rstrip(".") if "." in f"{val:.2f}" else f"{val}"
            # Keep clean representation
            if val == int(val):
                val_str = f"{val:.1f}" if unit in ["%", "dB"] else f"{int(val)}"
            else:
                val_str = f"{val}"
        else:
            val_str = str(val)

        # Escape backticks in command if any
        if cmd.startswith("python") or cmd.startswith("node"):
            cmd_fmt = f"`{cmd}`"
        else:
            cmd_fmt = cmd

        lines.append(f"| {key} | {val_str} | {unit} | {status} | {cmd_fmt} |")

    return "\n".join(lines)


def update_file(path: Path, new_table: str, check_mode: bool) -> bool:
    if not path.exists():
        return False

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    start_idx = content.find(START_MARKER)
    end_idx = content.find(END_MARKER)

    if start_idx == -1 or end_idx == -1:
        print(f"Warning: Markers not found in {path.name}", file=sys.stderr)
        return False

    before = content[: start_idx + len(START_MARKER)]
    after = content[end_idx:]

    new_content = f"{before}\n{new_table}\n{after}"

    if new_content == content:
        print(f"[{path.name}] Up-to-date.")
        return False

    diff = difflib.unified_diff(
        content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=f"a/{path.name}",
        tofile=f"b/{path.name}",
    )
    diff_text = "".join(diff)

    if check_mode:
        print(f"[{path.name}] Diff detected:\n{diff_text}", file=sys.stderr)
        return True

    print(f"[{path.name}] Updating table:\n{diff_text}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_content)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Update README metrics table from metrics.json")
    parser.add_argument("--check", action="store_true", help="Fail if files are out of date")
    args = parser.parse_args()

    metrics_data = load_metrics(METRICS_JSON_PATH)
    table_md = build_markdown_table(metrics_data)

    has_diffs = False
    for target in TARGET_FILES:
        changed = update_file(target, table_md, args.check)
        if changed:
            has_diffs = True

    if args.check and has_diffs:
        print("Error: Metrics tables are out of date. Run python scripts/update_readme_metrics.py to update.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
