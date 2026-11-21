"""Offline scanner for Project ARBITER v3 3D Visualization.

Verifies that the 3D dashboard contains strictly zero external runtime network
dependencies (CDNs, external fonts, external analytics, or remote scripts).
Fails with exit code 1 if any outbound URL outside localhost is detected.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_3D_DIR = (
    Path(__file__).resolve().parents[1] / "arbiter" / "dashboard" / "3d"
)

# Regex to detect absolute URLs
_URL_REGEX = re.compile(r"https?://[^\s\"'<>`]+", re.IGNORECASE)

# Safe patterns: localhost, 127.0.0.1, 0.0.0.0, or XML schemas
_SAFE_HOST_PATTERNS = [
    re.compile(r"^https?://localhost(?::\d+)?(?:/.*)?$", re.IGNORECASE),
    re.compile(r"^https?://127\.0\.0\.1(?::\d+)?(?:/.*)?$", re.IGNORECASE),
    re.compile(r"^https?://0\.0\.0\.0(?::\d+)?(?:/.*)?$", re.IGNORECASE),
    re.compile(r"^http://www\.w3\.org/2000/svg$", re.IGNORECASE),
    re.compile(r"^http://www\.w3\.org/1999/xhtml$", re.IGNORECASE),
]

# Patterns representing executable network imports/requests
_RUNTIME_NETWORK_PATTERNS = [
    re.compile(r'<script[^>]+src=["\'](https?://[^"\']+)["\']', re.IGNORECASE),
    re.compile(r'<link[^>]+href=["\'](https?://[^"\']+)["\']', re.IGNORECASE),
    re.compile(
        r'import\s+.*?from\s+["\'](https?://[^"\']+)["\']',
        re.IGNORECASE,
    ),
    re.compile(r'import\(["\'](https?://[^"\']+)["\']\)', re.IGNORECASE),
    re.compile(r'fetch\(["\'](https?://[^"\']+)["\']', re.IGNORECASE),
    re.compile(r'url\(["\']?(https?://[^"\'\)]+)["\']?\)', re.IGNORECASE),
    re.compile(r'new\s+WebSocket\(["\'](wss?://[^"\']+)["\']', re.IGNORECASE),
]


def is_safe_url(url: str) -> bool:
    """Return True if url is on localhost or a standard offline XML schema."""
    cleaned = url.rstrip(".,;:)\"'`}")
    for pattern in _SAFE_HOST_PATTERNS:
        if pattern.match(cleaned):
            return True
    return False


def strip_comments(text: str) -> str:
    """Strip single-line and multi-line comments from JS/CSS code."""
    # Strip /* ... */
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    # Strip // ... (ensure not part of http://)
    text = re.sub(r"(?<!:)//.*", "", text)
    # Strip <!-- ... -->
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    return text


def scan_3d_directory() -> list[str]:
    """Scan all files under arbiter/dashboard/3d/ for external URLs."""
    violations: list[str] = []

    if not _3D_DIR.is_dir():
        return [f"Directory {_3D_DIR} not found"]

    for path in _3D_DIR.rglob("*"):
        if not path.is_file():
            continue

        # Skip binary files if any
        if path.suffix.lower() in [
            ".png",
            ".jpg",
            ".jpeg",
            ".ico",
            ".woff",
            ".woff2",
        ]:
            continue

        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        # 1. Check runtime network invocations (strict: fails on any file)
        for pattern in _RUNTIME_NETWORK_PATTERNS:
            for match in pattern.findall(content):
                if not is_safe_url(match):
                    violations.append(
                        f"Runtime network call in {path.name}: {match}"
                    )

        # 2. Check all un-commented code in HTML/CSS and non-vendor JS
        rel_str = str(path.relative_to(_3D_DIR))
        is_vendor = "vendor" in rel_str

        # For non-vendor code, forbid all non-comment external URLs
        if not is_vendor:
            uncommented = strip_comments(content)
            for line_num, line in enumerate(uncommented.splitlines(), start=1):
                for match in _URL_REGEX.findall(line):
                    if not is_safe_url(match):
                        violations.append(f"{rel_str}:{line_num} -> {match}")

    return violations


def main() -> int:
    """Main verification entry point."""
    violations = scan_3d_directory()
    if violations:
        print("[FAIL] External network URLs detected in 3D dashboard:")
        for v in violations:
            print(f"  - {v}")
        return 1

    print(
        "[PASS] Zero external requests or CDNs detected. "
        "100% offline compliant."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
