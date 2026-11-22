"""
Phase 0 Inventory Script for Project ARBITER Assessment.
Analyzes LOC, tests, endpoints, WS channels, DB tables, scripts, spec requirements, and default-path audit.
Outputs raw results to artifacts/assessment/phase0_inventory_results.json and .txt
"""
import os
import re
import json
import sqlite3
import pymupdf  # fitz

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

def count_loc(dir_path):
    stats = {}
    ext_map = {
        ".py": "python",
        ".js": "javascript",
        ".css": "css",
        ".html": "html",
        ".sql": "sql",
        ".md": "markdown",
        ".json": "json"
    }
    for root, dirs, files in os.walk(dir_path):
        if "node_modules" in root or ".git" in root or "__pycache__" in root or ".pytest_cache" in root or "artifacts" in root:
            continue
        rel = os.path.relpath(root, BASE_DIR)
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in ext_map:
                lang = ext_map[ext]
                fpath = os.path.join(root, f)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as fp:
                        lines = len(fp.readlines())
                    stats.setdefault(rel, {}).setdefault(lang, 0)
                    stats[rel][lang] += lines
                except Exception as e:
                    pass
    return stats

def analyze_endpoints():
    server_path = os.path.join(BASE_DIR, "arbiter", "api", "server.py")
    routes = []
    if os.path.exists(server_path):
        with open(server_path, "r", encoding="utf-8") as f:
            content = f.read()
        matches = re.findall(r'@app\.(get|post|put|delete|websocket)\(["\']([^"\']+)["\']', content)
        for method, route in matches:
            routes.append({"method": method.upper(), "path": route})
    return routes

def analyze_db_tables():
    # search schema.sql or database files
    tables = []
    for root, dirs, files in os.walk(os.path.join(BASE_DIR, "arbiter")):
        for f in files:
            if f.endswith(".sql") or "database" in f:
                fpath = os.path.join(root, f)
                with open(fpath, "r", encoding="utf-8", errors="ignore") as fp:
                    text = fp.read()
                found = re.findall(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-zA-Z0-9_]+)', text, re.IGNORECASE)
                for t in found:
                    tables.append({"table": t, "file": os.path.relpath(fpath, BASE_DIR)})
    return tables

def audit_default_path():
    findings = []
    keywords = ["mock_", "TODO", "DEMO DATA", "FIXME", "HARDCODED", "DEMO_DATA"]
    scan_dirs = ["arbiter", "scripts"]
    for sdir in scan_dirs:
        full_dir = os.path.join(BASE_DIR, sdir)
        for root, dirs, files in os.walk(full_dir):
            if "__pycache__" in root:
                continue
            for f in files:
                if f.endswith((".py", ".js", ".html")):
                    fpath = os.path.join(root, f)
                    rel_path = os.path.relpath(fpath, BASE_DIR)
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="ignore") as fp:
                            for idx, line in enumerate(fp, 1):
                                for kw in keywords:
                                    if kw.lower() in line.lower():
                                        # check if it's in a mocks folder or test
                                        findings.append({
                                            "file": rel_path,
                                            "line": idx,
                                            "keyword": kw,
                                            "snippet": line.strip()[:120]
                                        })
                    except Exception:
                        pass
    return findings

def extract_pdf_requirements():
    reqs = {}
    pdf_files = [f for f in os.listdir(BASE_DIR) if f.endswith(".pdf")]
    for pdf in pdf_files:
        doc = pymupdf.open(os.path.join(BASE_DIR, pdf))
        full_text = ""
        for page in doc:
            full_text += page.get_text() + "\n"
        
        # Search for requirement codes like FR-1.1, FR-2.3, NFR-, C-1, A-1, etc.
        fr_matches = re.findall(r'((?:FR|NFR|C|A|BR)-[0-9]+(?:\.[0-9]+)*[:\s–-]+[^\n\.\;]+)', full_text)
        reqs[pdf] = list(set([m.strip() for m in fr_matches if len(m.strip()) < 100]))
    return reqs

def main():
    print("Running Phase 0 Inventory...")
    loc_stats = count_loc(os.path.join(BASE_DIR, "arbiter"))
    test_loc = count_loc(os.path.join(BASE_DIR, "tests"))
    scripts_loc = count_loc(os.path.join(BASE_DIR, "scripts"))
    endpoints = analyze_endpoints()
    tables = analyze_db_tables()
    audit = audit_default_path()
    pdf_reqs = extract_pdf_requirements()

    # Aggregate LOC by branch/subsystem
    branch_map = {
        "Branch 1 (Sim & Controllers)": ["arbiter/simulator", "arbiter/controllers"],
        "Branch 2 (Queue, Detector, Engine)": ["arbiter/queue", "arbiter/detector", "arbiter/engine"],
        "Branch 3 (Twin, Actuator, Storage, Shared)": ["arbiter/twin", "arbiter/actuator", "arbiter/storage", "arbiter/shared"],
        "Branch 4 (Telemetry, API, Dashboard, Site, Benchmarks)": ["arbiter/telemetry", "arbiter/api", "arbiter/dashboard", "arbiter/benchmarks"]
    }
    loc_summary = {}
    for b_name, dirs in branch_map.items():
        loc_summary[b_name] = {"python": 0, "javascript": 0, "css": 0, "html": 0, "total": 0}
        for rel_dir, lang_counts in loc_stats.items():
            norm_rel = rel_dir.replace("\\", "/")
            if any(norm_rel.startswith(d) for d in dirs):
                for l, c in lang_counts.items():
                    if l in loc_summary[b_name]:
                        loc_summary[b_name][l] += c
                        loc_summary[b_name]["total"] += c

    results = {
        "loc_summary": loc_summary,
        "endpoints": endpoints,
        "tables": tables,
        "audit_findings_count": len(audit),
        "audit_findings_sample": audit[:30],
        "pdf_reqs": pdf_reqs
    }

    out_json = os.path.join(BASE_DIR, "artifacts", "assessment", "phase0_inventory_results.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print(f"Phase 0 Inventory written to {out_json}")
    print(f"Total endpoints found: {len(endpoints)}")
    print(f"Audit keywords matches: {len(audit)}")
    for b, stats in loc_summary.items():
        print(f"{b}: {stats}")

if __name__ == "__main__":
    main()
