"""tests/site/test_site_routes.py

Validates routes, offline compliance, and schema for the PROJECT ARBITER product/marketing site.
Traceability:
  - Route validation: /, /dashboard/site/, static assets (CSS, JS, fonts, data)
  - Schema validation for evidence.json & team.json
  - Offline compliance: zero external runtime CDN or http(s) dependencies
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from arbiter.api.server import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestSiteRoutes:
    def test_root_serves_marketing_site_200(self, client: TestClient) -> None:
        """GET / serves the marketing/product site with status 200 and text/html."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        assert "PROJECT ARBITER" in resp.text
        assert "2 AM Collision Monitor" in resp.text
        assert "Autonomous RAN Multi-Controller Conflict Arbitration" in resp.text

    def test_dashboard_site_alias_200(self, client: TestClient) -> None:
        """GET /dashboard/site/ serves index.html with status 200."""
        resp = client.get("/dashboard/site/")
        assert resp.status_code == 200
        assert "PROJECT ARBITER" in resp.text

    def test_site_stylesheets_and_scripts_200(self, client: TestClient) -> None:
        """GET static style and script files return 200 with appropriate mime types."""
        css_resp = client.get("/style.css")
        assert css_resp.status_code == 200
        assert "text/css" in css_resp.headers.get("content-type", "")

        tokens_resp = client.get("/css/tokens.css")
        assert tokens_resp.status_code == 200
        assert "text/css" in tokens_resp.headers.get("content-type", "")
        assert "--gold: #FFC800;" in tokens_resp.text

        comp_resp = client.get("/css/components.css")
        assert comp_resp.status_code == 200
        assert "text/css" in comp_resp.headers.get("content-type", "")

        app_js = client.get("/app.js")
        assert app_js.status_code == 200
        assert "javascript" in app_js.headers.get("content-type", "")

        js_comp = client.get("/js/components.js")
        assert js_comp.status_code == 200
        assert "javascript" in js_comp.headers.get("content-type", "")

        js_ev = client.get("/js/evidence.js")
        assert js_ev.status_code == 200
        assert "javascript" in js_ev.headers.get("content-type", "")

    def test_site_data_endpoints_200(self, client: TestClient) -> None:
        """GET /data/evidence.json and /data/team.json return valid JSON with status 200."""
        ev_resp = client.get("/data/evidence.json")
        assert ev_resp.status_code == 200
        assert "application/json" in ev_resp.headers.get("content-type", "")
        ev_data = ev_resp.json()
        assert "metrics" in ev_data
        assert "optimality_gap" in ev_data["metrics"]

        team_resp = client.get("/data/team.json")
        assert team_resp.status_code == 200
        assert "application/json" in team_resp.headers.get("content-type", "")
        team_data = team_resp.json()
        assert "members" in team_data
        assert len(team_data["members"]) == 4

    def test_self_hosted_fonts_200(self, client: TestClient) -> None:
        """GET self-hosted woff2 font files returns 200."""
        font_outfit = client.get("/fonts/outfit-latin-400-normal.woff2")
        assert font_outfit.status_code == 200
        assert len(font_outfit.content) > 1000

        font_mono = client.get("/fonts/jetbrains-mono-latin-400-normal.woff2")
        assert font_mono.status_code == 200
        assert len(font_mono.content) > 1000

    def test_dashboards_remain_accessible(self, client: TestClient) -> None:
        """Verify 2D NOC Console and 3D Digital Twin remain fully reachable."""
        r_2d = client.get("/dashboard/")
        assert r_2d.status_code == 200
        assert "2 AM Collision Monitor" in r_2d.text

        r_3d = client.get("/dashboard/3d/")
        assert r_3d.status_code == 200
        assert "3D Digital Twin" in r_3d.text

    def test_problem_page_routes_200(self, client: TestClient) -> None:
        """GET /problem and /problem.html return 200 with problem explanation and timeline."""
        r_clean = client.get("/problem")
        assert r_clean.status_code == 200
        assert "text/html" in r_clean.headers.get("content-type", "")
        assert "THE 2 AM COLLISION" in r_clean.text
        assert "O-RAN WG3" in r_clean.text
        assert "Concurrent Controller Action Timeline" in r_clean.text
        assert "Uncoordinated Baseline" in r_clean.text
        assert "With ARBITER" in r_clean.text

        r_ext = client.get("/problem.html")
        assert r_ext.status_code == 200
        assert "THE 2 AM COLLISION" in r_ext.text

    def test_how_it_works_page_routes_200(self, client: TestClient) -> None:
        """GET /how-it-works and /how-it-works.html return 200 with architecture diagram and tick loops."""
        r_clean = client.get("/how-it-works")
        assert r_clean.status_code == 200
        assert "text/html" in r_clean.headers.get("content-type", "")
        assert "HOW ARBITER WORKS" in r_clean.text
        assert "End-to-End Control Loop" in r_clean.text
        assert "The 100 ms Tick Feedback Loops" in r_clean.text
        assert "The Decision Ladder" in r_clean.text

        r_ext = client.get("/how-it-works.html")
        assert r_ext.status_code == 200
        assert "HOW ARBITER WORKS" in r_ext.text

    def test_reason_codes_match_shared_enum(self, client: TestClient) -> None:
        """Validate that all ReasonCode enum entries from arbiter.shared.models are rendered on how-it-works."""
        from arbiter.shared.models import ReasonCode

        resp = client.get("/how-it-works")
        assert resp.status_code == 200

        for code in ReasonCode:
            # Verify enum member name and string value are present in the page HTML
            assert code.value in resp.text, f"ReasonCode value {code.value} not found on how-it-works.html"
            assert f'data-reason-code="{code.value}"' in resp.text, (
                f'Expected data-reason-code="{code.value}" attribute on how-it-works.html'
            )

    def test_results_page_routes_200(self, client: TestClient) -> None:
        """GET /results and /results.html return 200 with evidence cards and disclosures."""
        r_clean = client.get("/results")
        assert r_clean.status_code == 200
        assert "text/html" in r_clean.headers.get("content-type", "")
        assert "RESULTS &amp; PROOFS" in r_clean.text or "RESULTS & PROOFS" in r_clean.text
        assert "Optimality Gap" in r_clean.text
        assert "empirical coverage under approximate exchangeability" in r_clean.text
        assert "guaranteed" not in r_clean.text.lower()
        assert "Assumptions Disclosure" in r_clean.text
        assert "How to Reproduce" in r_clean.text
        assert "Limitations" in r_clean.text

        r_ext = client.get("/results.html")
        assert r_ext.status_code == 200
        assert "Optimality Gap" in r_ext.text

    def test_monitor_page_routes_200(self, client: TestClient) -> None:
        """GET /monitor and /monitor.html return 200 with dashboard hub and status bar."""
        r_clean = client.get("/monitor")
        assert r_clean.status_code == 200
        assert "text/html" in r_clean.headers.get("content-type", "")
        assert "OPERATIONS MONITOR" in r_clean.text
        assert "NOC Dashboard (2D)" in r_clean.text
        assert "3D Digital Twin" in r_clean.text
        assert "Start a Replay" in r_clean.text
        assert "[J]" in r_clean.text
        assert "[G]" in r_clean.text

        r_ext = client.get("/monitor.html")
        assert r_ext.status_code == 200
        assert "OPERATIONS MONITOR" in r_ext.text

    def test_team_page_renders_every_entry(self, client: TestClient) -> None:
        """GET /team renders every team member and module entry in team.json."""
        team_file = Path("arbiter/dashboard/site/data/team.json")
        team_data = json.loads(team_file.read_text(encoding="utf-8"))

        r_clean = client.get("/team")
        assert r_clean.status_code == 200
        assert "text/html" in r_clean.headers.get("content-type", "")
        assert "ENGINEERING TEAM" in r_clean.text
        assert "Built for Bits2Wave" in r_clean.text
        assert "github.com/Bits2Wave/ARBITER" in r_clean.text

        for member in team_data["members"]:
            assert member["name"] in r_clean.text, f"Member {member['name']} missing on /team"
            mod = member["module"]
            assert mod in r_clean.text or mod.replace("&", "&amp;") in r_clean.text, f"Module {mod} missing on /team"

        r_ext = client.get("/team.html")
        assert r_ext.status_code == 200
        assert "ENGINEERING TEAM" in r_ext.text

    def test_404_page_and_handler(self, client: TestClient) -> None:
        """Unknown site URLs return 404 with custom themed HTML page; unknown API routes return JSON."""
        # 1. Explicit /404 route
        r_404 = client.get("/404")
        assert r_404.status_code == 404
        assert "PAGE NOT FOUND" in r_404.text
        assert "PROJECT ARBITER" in r_404.text

        # 2. Random unknown site route returns 404 with themed page
        r_random = client.get("/non-existent-page-xyz")
        assert r_random.status_code == 404
        assert "PAGE NOT FOUND" in r_random.text

        # 3. Random unknown API route returns JSON 404
        r_api = client.get("/api/v1/non-existent-route")
        assert r_api.status_code == 404
        assert r_api.json().get("detail") == "Not Found"

    def test_favicon_endpoints(self, client: TestClient) -> None:
        """Favicon is served at /favicon.ico and /img/favicon.svg."""
        r_ico = client.get("/favicon.ico")
        assert r_ico.status_code == 200
        assert "image/svg+xml" in r_ico.headers.get("content-type", "")

        r_svg = client.get("/img/favicon.svg")
        assert r_svg.status_code == 200

    def test_dashboards_have_home_navigation_links(self) -> None:
        """Both 2D and 3D dashboards have a Home link navigating back to '/'."""
        p_2d = Path("arbiter/dashboard/index.html")
        assert p_2d.is_file()
        assert 'href="/"' in p_2d.read_text(encoding="utf-8")

        p_3d = Path("arbiter/dashboard/3d/index.html")
        assert p_3d.is_file()
        assert 'href="/"' in p_3d.read_text(encoding="utf-8")


class TestEvidenceSchema:
    def test_evidence_json_schema_conformance(self) -> None:
        """Validate evidence.json structure and metric attributes."""
        evidence_file = Path("arbiter/dashboard/site/data/evidence.json")
        assert evidence_file.is_file(), "evidence.json must exist"

        data: Dict[str, Any] = json.loads(evidence_file.read_text(encoding="utf-8"))
        assert data.get("project") == "PROJECT ARBITER"
        assert "git_commit" in data
        assert "generated_at" in data
        assert isinstance(data.get("metrics"), dict)

        metrics = data["metrics"]
        assert len(metrics) >= 10, "Should contain at least 10 key empirical metrics"

        for key, m in metrics.items():
            assert "value" in m, f"Metric {key} missing 'value'"
            assert "unit" in m, f"Metric {key} missing 'unit'"
            assert m.get("status") in ("measured", "target"), f"Metric {key} status must be 'measured' or 'target'"
            assert "source_file" in m, f"Metric {key} missing 'source_file'"
            assert "git_sha" in m, f"Metric {key} missing 'git_sha'"
            assert "generated_at" in m, f"Metric {key} missing 'generated_at'"

    def test_team_json_schema_conformance(self) -> None:
        """Validate team.json contains required member entries."""
        team_file = Path("arbiter/dashboard/site/data/team.json")
        assert team_file.is_file(), "team.json must exist"

        data: Dict[str, Any] = json.loads(team_file.read_text(encoding="utf-8"))
        assert "members" in data
        assert "team_name" in data

        names = [m["name"] for m in data["members"]]
        assert "Akshay A" in names
        assert "Aaditya V" in names
        assert "H M Pranav" in names
        assert "G S Venkat Sai" in names


class TestOfflineCompliance:
    def test_no_external_http_or_https_in_site_files(self) -> None:
        """Verify no site file references external CDN or third-party web URLs at runtime."""
        site_dir = Path("arbiter/dashboard/site")
        assert site_dir.is_dir()

        # Regular expression detecting http:// or https://, ignoring XML namespaces
        url_pattern = re.compile(r'https?://(?!www\.w3\.org/)[\w.-]+')

        # 1. Author-written runtime assets (HTML, CSS, JS, JSON) must contain zero external URLs
        for file_path in site_dir.rglob("*"):
            if not file_path.is_file():
                continue
            if "vendor" in file_path.parts or "LICENSE" in file_path.name:
                continue
            if file_path.suffix.lower() not in (".html", ".css", ".js", ".json"):
                continue

            content = file_path.read_text(encoding="utf-8", errors="ignore")
            matches = url_pattern.findall(content)
            assert not matches, f"External URL reference found in {file_path}: {matches}"

        # 2. Strict runtime check: no external script/link tags or CDN imports across the whole tree
        cdn_pattern = re.compile(r'(?:src|href|url|import)\s*=?\s*["\']https?://')
        for file_path in site_dir.rglob("*"):
            if not file_path.is_file() or file_path.suffix.lower() not in (".html", ".css", ".js"):
                continue
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            matches = cdn_pattern.findall(content)
            assert not matches, f"Runtime CDN request found in {file_path}: {matches}"
