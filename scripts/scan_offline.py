"""
scripts/scan_offline.py
Deep offline integrity check: ensures zero external HTTP/HTTPS URLs, CDN imports, or remote dependencies exist in arbiter/dashboard/site.
"""
import re
from pathlib import Path


def main():
    site_dir = Path("arbiter/dashboard/site")
    url_pattern = re.compile(r'https?://(?!www\.w3\.org/)[\w.-]+')
    cdn_pattern = re.compile(r'(?:src|href|url|import)\s*=?\s*["\']https?://')

    findings = []
    checked = 0
    for p in site_dir.rglob("*"):
        if not p.is_file():
            continue
        if "vendor" in p.parts or "LICENSE" in p.name:
            continue
        if p.suffix.lower() not in (".html", ".css", ".js", ".json"):
            continue
        checked += 1
        content = p.read_text(encoding="utf-8", errors="ignore")
        m1 = url_pattern.findall(content)
        m2 = cdn_pattern.findall(content)
        if m1 or m2:
            findings.append((str(p), m1, m2))

    print(f"Checked {checked} site source files.")
    if findings:
        print(f"FAILED: Found {len(findings)} files with external references:")
        for fn, m1, m2 in findings:
            print(f"  - {fn}: urls={m1}, cdn={m2}")
        exit(1)
    else:
        print("PASS: Zero external URLs or CDN links detected. 100% offline compliant.")

if __name__ == "__main__":
    main()
