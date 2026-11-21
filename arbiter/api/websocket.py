"""
arbiter/api/websocket.py
WebSocket endpoint and broadcaster — Branch 4.

SRS traceability:
  FR-9.1  Live "2 AM Collision Monitor" dashboard with ARBITER action badges.
  §4.2    WebSocket route and message format (pages 5-6).
  §5.1    Action badge colour encodings (pages 6-7).
  §7.2    Supports 20 concurrent dashboard client connections (page 7).

Responsibility (confirmed by user):
  ConnectionManager, ws_live endpoint, broadcast_tick_update helper.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

if TYPE_CHECKING:
    from arbiter.shared.models import TelemetrySample

# ---------------------------------------------------------------------------
# ASSUMPTIONS — one constant / one place each; easy to change.
# ---------------------------------------------------------------------------
_ASSUMPTIONS: dict[str, str] = {
    "A5": (
        "WS timestamp format 'HH:MM:SS.mmm' inferred from spec §4.2 page 5 "
        "example '02:00:00.120'.  Change _now_hhmmss_ms() only."
    ),
    "A6": (
        "Controller abbreviations in ingress_proposals.controller field. "
        "Spec §4.2 page 5 shows only 'ES' for ENERGY_SAVING. "
        "LB / CC / PS inferred.  Change CONTROLLER_ABBREV only."
    ),
    "A7": (
        "Initial TICK_UPDATE on connect has empty lists + zero KPIs. "
        "Spec §4.2 says 'real-time scrolling' but defines no connect-time "
        "handshake message."
    ),
}

# ---------------------------------------------------------------------------
# Spec-sourced constants
# ---------------------------------------------------------------------------

# Badge color map — spec §5.1 pages 6-7 (all four colours explicitly named).
BADGE_COLOR: dict[str, str] = {
    "APPROVE": "green",   # spec §5.1 page 6: "Green badge … joint ΔU > 0"
    "MODIFY":  "blue",    # spec §5.1 page 6: "Blue badge showing optimized value"
    "DELAY":   "amber",   # spec §5.1 page 6: "Amber badge showing active hold counter"
    "REJECT":  "red",     # spec §5.1 page 7: "Red badge displaying reason code"
}

# Controller abbreviations in ingress_proposals — ASSUMPTION A6.
# "ES" is the only abbreviation shown explicitly in spec §4.2 page 5.
CONTROLLER_ABBREV: dict[str, str] = {
    "ENERGY_SAVING":     "ES",   # explicit in spec §4.2 page 5 example
    "LOAD_BALANCING":    "LB",   # ASSUMPTION A6
    "COVERAGE_CAPACITY": "CC",   # ASSUMPTION A6
    "PUBLIC_SAFETY":     "PS",   # ASSUMPTION A6
}

router = APIRouter()


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _now_hhmmss_ms() -> str:
    """
    Return current UTC time formatted as HH:MM:SS.mmm.
    ASSUMPTION A5: format inferred from spec §4.2 page 5 example "02:00:00.120".
    Uses UTC for unambiguous 24-hour monitoring context.
    Change only this function if the format requirement changes.
    """
    now = datetime.now(timezone.utc)
    ms = now.microsecond // 1000
    return now.strftime("%H:%M:%S.") + f"{ms:03d}"


def format_simulated_time(epoch_ms: int) -> str:
    """Format epoch milliseconds as HH:MM:SS.mmm in UTC."""
    dt = datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc)
    ms = epoch_ms % 1000
    return dt.strftime("%H:%M:%S.") + f"{ms:03d}"


# ---------------------------------------------------------------------------
# Public message builder
# ---------------------------------------------------------------------------

def build_tick_update(
    *,
    batch_tick: int = 0,
    ingress_proposals: list[dict[str, Any]] | None = None,
    arbitration_badges: list[dict[str, Any]] | None = None,
    samples: list[TelemetrySample] | None = None,
    twin_mape_pct: float = 0.0,
    timestamp_epoch_ms: int | None = None,
    uncoord_dropped_users: int | None = None,
    uncoord_power_kw: float | None = None,
) -> dict[str, Any]:
    """
    Build a TICK_UPDATE message conforming to spec §4.2 pages 5-6.
    """
    if samples:
        total_dropped: int    = sum(s.dropped_users for s in samples)
        # energy_consumption_w (shared/models.py:69) → kW (÷ 1000)
        total_power_kw: float = sum(s.energy_consumption_w for s in samples) / 1000.0
        mean_sinr: float      = sum(s.sinr_db for s in samples) / len(samples)
    else:
        total_dropped  = 0
        total_power_kw = 0.0
        mean_sinr      = 0.0

    if timestamp_epoch_ms is not None:
        ts_str = format_simulated_time(timestamp_epoch_ms)
    elif samples and len(samples) > 0 and samples[0].timestamp_epoch_ms:
        ts_str = format_simulated_time(samples[0].timestamp_epoch_ms)
    else:
        ts_str = _now_hhmmss_ms()

    agg_kpis: dict[str, Any] = {
        "total_dropped_users": total_dropped,
        "total_power_kw":      total_power_kw,
        "mean_sinr_db":        mean_sinr,
        "twin_mape_pct":       twin_mape_pct,
    }
    if uncoord_dropped_users is not None:
        agg_kpis["uncoord_dropped_users"] = uncoord_dropped_users
    if uncoord_power_kw is not None:
        agg_kpis["uncoord_power_kw"] = uncoord_power_kw

    return {
        "type":               "TICK_UPDATE",
        "batch_tick":         batch_tick,
        "timestamp":          ts_str,
        "ingress_proposals":  ingress_proposals or [],
        "arbitration_badges": arbitration_badges or [],
        "aggregate_kpis":     agg_kpis,
    }


# ---------------------------------------------------------------------------
# Connection Manager
# ---------------------------------------------------------------------------

class ConnectionManager:
    """
    Tracks active WebSocket connections for the live dashboard.

    Thread-safety: all access is from the same ASGI event loop in a single
    worker process, so no explicit locking is needed.
    Spec §7.2 page 7: must support 20 concurrent client connections.

    Uses a plain set; `discard()` is safe even if the socket is not registered.
    """

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        """Accept the WebSocket handshake and register the connection."""
        await websocket.accept()
        self._connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        """
        Remove a connection.  Safe to call even if the socket was never
        registered (e.g., if accept() raised before connect() completed).
        """
        self._connections.discard(websocket)

    async def broadcast(self, message: dict[str, Any]) -> None:
        """
        Send a JSON message to all connected clients.

        Any client that raises during send_json (e.g., disconnected without
        a proper close frame) is silently removed from the set.
        Remaining clients are not affected.

        Source: spec §7.2 page 7 — "without dropped frames" to connected clients.
        """
        stale: set[WebSocket] = set()
        for ws in list(self._connections):
            try:
                await ws.send_json(message)
            except Exception:
                stale.add(ws)
        self._connections -= stale

    @property
    def connection_count(self) -> int:
        """Current number of registered connections."""
        return len(self._connections)


# Module-level singleton — shared across all requests in the same process.
# Tests access this directly to verify connection counts.
manager: ConnectionManager = ConnectionManager()


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@router.websocket("/api/v1/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    """
    WebSocket endpoint: ws://localhost:8000/api/v1/ws/live
    Source: spec §4.2 page 5.

    On connect: accepts and immediately sends an initial TICK_UPDATE
    (ASSUMPTION A7 — spec does not define a connect-time handshake).

    Remains open; server pushes subsequent updates via manager.broadcast(),
    which is called by ingest_telemetry() in routes.py after each batch.

    Client disconnect (WebSocketDisconnect) is handled gracefully in `finally`.
    """
    await manager.connect(websocket)
    try:
        # Send initial state immediately on connect (ASSUMPTION A7)
        await websocket.send_json(build_tick_update())
        # Keep alive — wait for client disconnect or ping
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)


# ---------------------------------------------------------------------------
# Public broadcast helper (called from routes.py after ingest)
# ---------------------------------------------------------------------------

async def broadcast_tick_update(payload: dict[str, Any]) -> None:
    """
    Broadcast a prepared TICK_UPDATE payload to all connected WS clients.
    Called by routes.ingest_telemetry() after persisting.
    """
    await manager.broadcast(payload)
