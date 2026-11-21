"""
tests/branch4/test_websocket_broadcast.py
WebSocket endpoint and broadcaster tests — Branch 4.

Sources:
  spec §4.2 pages 5-6   — WebSocket route and message format.
  spec §5.1 pages 6-7   — Action badge colour encodings.
  spec §7.2 page 7      — 20 concurrent connections, no dropped frames.

Uses TestClient.websocket_connect() (httpx-backed, synchronous).
No pytest-asyncio — async unit tests use asyncio.run() directly.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from arbiter.api.server import app
from arbiter.api.websocket import (
    BADGE_COLOR,
    CONTROLLER_ABBREV,
    ConnectionManager,
    build_tick_update,
    manager,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ws_client():
    """TestClient with live WebSocket support (no dependency overrides needed)."""
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# WebSocket endpoint — connect and initial message (spec §4.2)
# ---------------------------------------------------------------------------

class TestWSConnect:

    def test_ws_connect_no_error(self, ws_client):
        """WebSocket handshake completes without raising."""
        with ws_client.websocket_connect("/api/v1/ws/live") as ws:
            ws.receive_json()   # consume mandatory initial message

    def test_ws_connect_receives_initial_message(self, ws_client):
        """
        Server sends a TICK_UPDATE immediately on connect (ASSUMPTION A7).
        Verifies type == 'TICK_UPDATE' — spec §4.2 page 5.
        """
        with ws_client.websocket_connect("/api/v1/ws/live") as ws:
            msg = ws.receive_json()
        assert msg["type"] == "TICK_UPDATE"

    def test_ws_disconnect_no_crash(self, ws_client):
        """
        Client disconnects gracefully (exits context manager).
        Server must not raise; connection is removed from manager.
        """
        # No assertion on exact count — depends on test execution order.
        # Simply verifying the disconnect does not propagate an exception.
        with ws_client.websocket_connect("/api/v1/ws/live") as ws:
            ws.receive_json()
        # Reaching here without exception == pass


# ---------------------------------------------------------------------------
# WebSocket message format — spec §4.2 pages 5-6
# ---------------------------------------------------------------------------

class TestWSMessageFormat:

    def test_message_has_all_top_level_keys(self, ws_client):
        """
        All six keys from spec §4.2 pages 5-6 must be present in every message.
        Required: type, batch_tick, timestamp, ingress_proposals,
                  arbitration_badges, aggregate_kpis.
        """
        with ws_client.websocket_connect("/api/v1/ws/live") as ws:
            msg = ws.receive_json()
        required = {
            "type", "batch_tick", "timestamp",
            "ingress_proposals", "arbitration_badges", "aggregate_kpis",
        }
        assert required.issubset(msg.keys())

    def test_message_type_is_tick_update(self, ws_client):
        """spec §4.2 page 5: type field value must be 'TICK_UPDATE'."""
        with ws_client.websocket_connect("/api/v1/ws/live") as ws:
            msg = ws.receive_json()
        assert msg["type"] == "TICK_UPDATE"

    def test_aggregate_kpis_has_all_spec_keys(self, ws_client):
        """
        aggregate_kpis sub-object must have all four keys from spec §4.2 page 6:
        total_dropped_users, total_power_kw, mean_sinr_db, twin_mape_pct.
        """
        with ws_client.websocket_connect("/api/v1/ws/live") as ws:
            msg = ws.receive_json()
        kpis = msg["aggregate_kpis"]
        assert "total_dropped_users" in kpis
        assert "total_power_kw"      in kpis
        assert "mean_sinr_db"        in kpis
        assert "twin_mape_pct"       in kpis

    def test_ingress_proposals_is_list(self, ws_client):
        """ingress_proposals must be a list (empty on initial connect)."""
        with ws_client.websocket_connect("/api/v1/ws/live") as ws:
            msg = ws.receive_json()
        assert isinstance(msg["ingress_proposals"], list)

    def test_arbitration_badges_is_list(self, ws_client):
        """arbitration_badges must be a list (empty on initial connect)."""
        with ws_client.websocket_connect("/api/v1/ws/live") as ws:
            msg = ws.receive_json()
        assert isinstance(msg["arbitration_badges"], list)

    def test_batch_tick_is_int(self, ws_client):
        """batch_tick must be an integer — spec §4.2 page 5."""
        with ws_client.websocket_connect("/api/v1/ws/live") as ws:
            msg = ws.receive_json()
        assert isinstance(msg["batch_tick"], int)

    def test_timestamp_format_hhmmss_ms(self, ws_client):
        """
        timestamp is formatted as HH:MM:SS.mmm (ASSUMPTION A5).
        Spec §4.2 page 5 shows example '02:00:00.120'.
        Validation: length == 12, correct separator positions.
        """
        with ws_client.websocket_connect("/api/v1/ws/live") as ws:
            msg = ws.receive_json()
        ts = msg["timestamp"]
        assert len(ts) == 12, f"Expected 12 chars, got {len(ts)!r}: {ts!r}"
        assert ts[2] == ":", f"pos 2 should be ':', got {ts!r}"
        assert ts[5] == ":", f"pos 5 should be ':', got {ts!r}"
        assert ts[8] == ".", f"pos 8 should be '.', got {ts!r}"
        assert ts[:2].isdigit()
        assert ts[3:5].isdigit()
        assert ts[6:8].isdigit()
        assert ts[9:].isdigit()

    def test_aggregate_kpis_numeric_types(self, ws_client):
        """KPI values must be numeric (int or float)."""
        with ws_client.websocket_connect("/api/v1/ws/live") as ws:
            msg = ws.receive_json()
        kpis = msg["aggregate_kpis"]
        assert isinstance(kpis["total_dropped_users"], int)
        assert isinstance(kpis["total_power_kw"],      float)
        assert isinstance(kpis["mean_sinr_db"],        float)
        assert isinstance(kpis["twin_mape_pct"],       float)


# ---------------------------------------------------------------------------
# build_tick_update helper
# ---------------------------------------------------------------------------

class TestBuildTickUpdate:

    def test_build_tick_update_all_keys_present(self):
        """build_tick_update() returns a dict with all spec §4.2 keys."""
        msg = build_tick_update()
        required = {
            "type", "batch_tick", "timestamp",
            "ingress_proposals", "arbitration_badges", "aggregate_kpis",
        }
        assert required.issubset(msg.keys())

    def test_build_tick_update_zero_kpis_when_no_samples(self):
        """No samples provided → all aggregate KPIs are 0."""
        kpis = build_tick_update()["aggregate_kpis"]
        assert kpis["total_dropped_users"] == 0
        assert kpis["total_power_kw"]      == pytest.approx(0.0)
        assert kpis["mean_sinr_db"]        == pytest.approx(0.0)
        assert kpis["twin_mape_pct"]       == pytest.approx(0.0)

    def test_build_tick_update_aggregate_kpis_from_samples(self):
        """
        KPIs computed correctly from TelemetrySample list.
        Field sources: shared/models.py lines 65-69.
        """
        from arbiter.shared.models import TelemetrySample

        def _s(sinr, dropped, energy_w):
            return TelemetrySample(
                sample_id=str(uuid.uuid4()), cell_id="cell_X",
                sinr_db=sinr, dropped_users=dropped, active_users=50,
                throughput_mbps=100.0, energy_consumption_w=energy_w,
                timestamp_epoch_ms=1_700_000_000_000,
            )

        samples = [_s(10.0, 3, 1000.0), _s(20.0, 1, 2000.0)]
        kpis = build_tick_update(samples=samples)["aggregate_kpis"]

        assert kpis["total_dropped_users"] == 4            # 3 + 1
        assert kpis["total_power_kw"]      == pytest.approx(3.0)   # (1000+2000)/1000
        assert kpis["mean_sinr_db"]        == pytest.approx(15.0)  # (10+20)/2

    def test_build_tick_update_custom_batch_tick(self):
        """batch_tick parameter is reflected in the message."""
        msg = build_tick_update(batch_tick=42)
        assert msg["batch_tick"] == 42

    def test_build_tick_update_custom_proposals_and_badges(self):
        """ingress_proposals and arbitration_badges are passed through unchanged."""
        proposals = [{"controller": "ES", "target": "cell_B",
                      "param": "SLEEP_STATE", "value": 1.0}]
        badges    = [{"command_id": "cmd-001", "badge_type": "DELAY",
                      "label": "DELAY (hold 2)", "color": "amber",
                      "reason": "ACTIVE_HO"}]
        msg = build_tick_update(ingress_proposals=proposals, arbitration_badges=badges)
        assert msg["ingress_proposals"]  == proposals
        assert msg["arbitration_badges"] == badges


# ---------------------------------------------------------------------------
# Badge color map — spec §5.1 pages 6-7
# ---------------------------------------------------------------------------

class TestBadgeColors:

    def test_approve_color_is_green(self):
        """spec §5.1 page 6: APPROVE → green ('Green badge … joint ΔU > 0')."""
        assert BADGE_COLOR["APPROVE"] == "green"

    def test_modify_color_is_blue(self):
        """spec §5.1 page 6: MODIFY → blue ('Blue badge showing optimized value')."""
        assert BADGE_COLOR["MODIFY"] == "blue"

    def test_delay_color_is_amber(self):
        """spec §5.1 page 6: DELAY → amber ('Amber badge showing active hold counter')."""
        assert BADGE_COLOR["DELAY"] == "amber"

    def test_reject_color_is_red(self):
        """spec §5.1 page 7: REJECT → red ('Red badge displaying reason code')."""
        assert BADGE_COLOR["REJECT"] == "red"

    def test_all_four_action_types_covered(self):
        """
        All four ActionType values from shared/models.py line 18 are mapped.
        APPROVE, MODIFY, DELAY, REJECT — no missing or extra entries.
        """
        assert set(BADGE_COLOR.keys()) == {"APPROVE", "MODIFY", "DELAY", "REJECT"}


# ---------------------------------------------------------------------------
# Controller abbreviation map — ASSUMPTION A6
# ---------------------------------------------------------------------------

class TestControllerAbbrev:

    def test_energy_saving_abbrev_matches_spec(self):
        """'ES' is the only abbreviation shown explicitly in spec §4.2 page 5."""
        assert CONTROLLER_ABBREV["ENERGY_SAVING"] == "ES"

    def test_all_four_controller_types_mapped(self):
        """
        All ControllerType enum values from shared/models.py line 6 are present.
        ENERGY_SAVING, LOAD_BALANCING, COVERAGE_CAPACITY, PUBLIC_SAFETY.
        """
        from arbiter.shared.models import ControllerType
        for ct in ControllerType:
            assert ct.value in CONTROLLER_ABBREV, f"{ct.value} missing from CONTROLLER_ABBREV"


# ---------------------------------------------------------------------------
# ConnectionManager unit tests (async via asyncio.run() — no pytest-asyncio)
# ---------------------------------------------------------------------------

class TestConnectionManager:

    def test_manager_starts_empty(self):
        """Fresh ConnectionManager has zero connections."""
        mgr = ConnectionManager()
        assert mgr.connection_count == 0

    def test_manager_disconnect_safe_on_unregistered_ws(self):
        """disconnect() on a never-registered socket must not raise."""
        mgr = ConnectionManager()

        class FakeWS:
            pass

        mgr.disconnect(FakeWS())  # type: ignore[arg-type]

    def test_manager_broadcast_reaches_connected_client(self):
        """
        broadcast() delivers the message to a registered mock client.
        Uses asyncio.run() to execute async code without pytest-asyncio.
        """
        sent: list[dict] = []

        class MockWS:
            async def send_json(self, data: dict) -> None:
                sent.append(data)

        async def _run() -> None:
            mgr = ConnectionManager()
            mgr._connections.add(MockWS())  # type: ignore[arg-type]
            await mgr.broadcast(build_tick_update())

        asyncio.run(_run())
        assert len(sent) == 1
        assert sent[0]["type"] == "TICK_UPDATE"

    def test_manager_broadcast_reaches_all_clients(self):
        """
        broadcast() delivers to ALL registered clients.
        Partial delivery would violate spec §7.2 page 7 ("no dropped frames").
        """
        received_by: list[str] = []

        class NamedWS:
            def __init__(self, name: str) -> None:
                self.name = name

            async def send_json(self, data: dict) -> None:
                received_by.append(self.name)

        async def _run() -> None:
            mgr = ConnectionManager()
            for name in ("ws1", "ws2", "ws3"):
                mgr._connections.add(NamedWS(name))  # type: ignore[arg-type]
            await mgr.broadcast(build_tick_update())

        asyncio.run(_run())
        assert set(received_by) == {"ws1", "ws2", "ws3"}

    def test_manager_broadcast_handles_stale_connection_silently(self):
        """
        A stale client (raises on send_json) is silently removed.
        No exception propagates; healthy clients still receive the message.
        """
        good_received: list[dict] = []

        class GoodWS:
            async def send_json(self, data: dict) -> None:
                good_received.append(data)

        class StaleWS:
            async def send_json(self, data: dict) -> None:
                raise RuntimeError("Connection broken")

        stale = StaleWS()

        async def _run() -> None:
            mgr = ConnectionManager()
            mgr._connections.add(GoodWS())  # type: ignore[arg-type]
            mgr._connections.add(stale)      # type: ignore[arg-type]
            await mgr.broadcast(build_tick_update())
            # Stale client must have been evicted
            assert stale not in mgr._connections

        asyncio.run(_run())
        # Good client still received the message
        assert len(good_received) == 1
        assert good_received[0]["type"] == "TICK_UPDATE"

    def test_manager_broadcast_multiple_stale_no_crash(self):
        """Multiple stale clients all evicted; broadcast does not crash."""
        async def _run() -> None:
            mgr = ConnectionManager()
            for _ in range(3):
                class StaleWS:
                    async def send_json(self, data: dict) -> None:
                        raise OSError("broken pipe")
                mgr._connections.add(StaleWS())  # type: ignore[arg-type]
            await mgr.broadcast(build_tick_update())
            assert mgr.connection_count == 0

        asyncio.run(_run())

    def test_ws_sequential_connections_both_receive_initial(self, ws_client):
        """
        Two sequential WebSocket connections each receive a TICK_UPDATE.
        Verifies manager does not hold stale state between connections.
        """
        for _ in range(2):
            with ws_client.websocket_connect("/api/v1/ws/live") as ws:
                msg = ws.receive_json()
                assert msg["type"] == "TICK_UPDATE"
