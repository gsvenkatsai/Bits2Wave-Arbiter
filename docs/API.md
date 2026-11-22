# Project ARBITER: API Reference

This document provides the complete API reference for Project ARBITER (FastAPI service, default port `8000`).

> [!NOTE]
> **Authentication Status**: All endpoints are currently unauthenticated. This service is designed as an O-RAN Near-RT RIC software prototype for simulation and evaluation. In a production deployment, endpoints would be secured via TLS and mutual O-RAN A1/E2 interface authentication.

---

## Service Overview

- **Base URL (Local)**: `http://127.0.0.1:8000`
- **Base URL (Live Cloud)**: `https://arbiter-h3oa.onrender.com`
- **OpenAPI Schema**: `GET /openapi.json`
- **Swagger UI**: `GET /docs`
- **ReDoc UI**: `GET /redoc`
- **CORS Support**: Configured via `CORS_ORIGINS` (defaults to `["http://localhost:8000","http://127.0.0.1:8000"]`).

---

## Summary of Endpoints

### System & Health
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/health` | System liveness probe and API version |

### Telemetry & Ingestion
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/telemetry/ingest` | Ingest raw KPI telemetry samples from RAN cells |
| `POST` | `/api/v1/frames/ingest` | Ingest complete multi-cell visual frames |
| `GET` | `/api/v1/frame/latest` | Retrieve latest visual state frame |
| `GET` | `/api/v1/state/cells` | Current state of all active radio cells |

### Decisions & Flight Recorder
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/decisions/history` | Historical arbitration decisions from SQLite DB |
| `GET` | `/api/v1/recorder/decisions` | Flight recorder indexed decisions with replay tokens |
| `GET` | `/api/v1/recorder/counterfactual/{decision_id}` | Counterfactual delta comparison (with vs without ARBITER) |
| `GET` | `/api/v1/recorder/counterfactual_frames/{decision_id}` | Synthetic visual frames for ghost-world replay |
| `GET` | `/api/v1/plans` | Multi-tick atomic arbitration plans |

### Digital Twin & Uncertainty
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/twin/intervals` | Conformal prediction intervals for cell KPIs |
| `GET` | `/api/v1/twin/prediction` | Latest forward prediction from digital twin |
| `POST` | `/api/v1/twin/drift` | Inject synthetic channel drift / shadow fading for testing |

### Policy & Trade-offs
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/risk-dial` | Get active multi-objective weighting configuration |
| `POST` | `/api/v1/risk-dial` | Update active risk weights across objectives |
| `GET` | `/api/v1/benchmarks/pareto` | Pareto optimal trade-off frontier curve |

### Benchmarking & Impact
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/impact` | Environmental and economic impact metrics |
| `GET` | `/api/v1/optimality` | Latest optimality gap comparison |
| `GET` | `/api/v1/benchmarks/optimality` | Full optimality gap benchmark result |
| `POST` | `/api/v1/benchmarks/run` | Execute on-demand Monte Carlo benchmark run |

### WebSocket Streams
| Method | Path | Description |
|---|---|---|
| `WS` | `/api/v1/ws/live` | Real-time push broadcast of engine frames (~100 ms) |
| `WS` | `/api/v1/ws/replay` | Deterministic replay frame stream for historical runs |

---

## REST Endpoints Detail

### 1. Health Probe

`GET /api/v1/health`

Returns service status and API version. Used by container orchestrators and health monitors.

**Response `200 OK`**:
```json
{
  "status": "healthy",
  "version": "1.0.0"
}
```

---

### 2. Telemetry Ingest

`POST /api/v1/telemetry/ingest`

Accepts a single `TelemetrySample` or a list of samples. Validates timestamps, types, and persists them into SQLite `kpi_telemetry` table.

**Request Body**:
```json
[
  {
    "cell_id": "cell_b",
    "timestamp": 1700000000.100,
    "rrc_connected_users": 18,
    "active_ues": 14,
    "prb_utilization": 0.42,
    "dl_throughput_mbps": 52.4,
    "energy_consumption_w": 380.0,
    "tx_power_dbm": 43.0,
    "sinr_db": 12.5,
    "mac_drop_rate": 0.002
  }
]
```

**Response `200 OK`**:
```json
{
  "status": "accepted",
  "samples_ingested": 1
}
```

---

### 3. Frame Ingest

`POST /api/v1/frames/ingest`

Allows external simulation engines to push a complete multi-cell visual frame directly to the API, immediately triggering WebSocket broadcasts.

**Request Body**: Object conforming to the `FrameMessage` contract (see [docs/diagrams/frame-contract.md](file:///A:/Bits2Wave/docs/diagrams/frame-contract.md)).

**Response `200 OK`**:
```json
{
  "status": "accepted",
  "tick": 42
}
```

---

### 4. Latest Visual Frame

`GET /api/v1/frame/latest`

Returns the most recent complete frame cached in memory.

**Response `200 OK`**:
```json
{
  "type": "frame",
  "tick": 42,
  "simulated_time": "02:00:04.200",
  "cells": [
    {
      "cell_id": "cell_b",
      "pci": 102,
      "state": "ACTIVE",
      "tx_power_dbm": 43.0,
      "load_factor": 0.45,
      "connected_ues": 18,
      "energy_rate_w": 380.0
    }
  ],
  "proposals": [],
  "verdicts": [],
  "kpis": {
    "total_energy_w": 1840.0,
    "avg_sinr_db": 14.2,
    "dropped_users": 0
  }
}
```

---

### 5. Cell States

`GET /api/v1/state/cells`

Returns snapshot of all radio cells tracked by the active coordinator.

**Response `200 OK`**: Array of `CellState` objects.

---

### 6. Decisions History

`GET /api/v1/decisions/history`

Query parameters:
- `limit` (integer, default: 50, max: 500): Number of records to return.
- `offset` (integer, default: 0): Pagination offset.
- `cell_id` (string, optional): Filter by cell identifier.
- `verdict` (string, optional): Filter by `APPROVE`, `MODIFY`, `DELAY`, or `REJECT`.

**Response `200 OK`**:
```json
[
  {
    "decision_id": "dec_20261122_0001",
    "timestamp": 1700000004.200,
    "proposal_id": "prop_es_001",
    "controller_id": "energy_saving_01",
    "controller_type": "ENERGY_SAVING",
    "target_cell": "cell_b",
    "parameter": "ADMIN_STATE",
    "original_val": "SLEEP",
    "verdict": "DELAY",
    "final_val": "ACTIVE",
    "reason_code": "HOLD_POLICY",
    "explanation": "Cell B held active; load exceeds safe energy sleep threshold",
    "hold_count": 1
  }
]
```

---

### 7. Flight Recorder: Indexed Decisions

`GET /api/v1/recorder/decisions`

Returns indexed decisions with counterfactual availability status for UI navigation.

---

### 8. Flight Recorder: Counterfactual Analysis

`GET /api/v1/recorder/counterfactual/{decision_id}`

Computes or returns the cached counterfactual trajectory for a specific arbitration decision: what happened under ARBITER versus what would have happened uncoordinatedly.

**Response `200 OK`**:
```json
{
  "decision_id": "dec_20261122_0001",
  "proposal_id": "prop_es_001",
  "actual_outcome": {
    "dropped_users": 0,
    "avg_sinr_db": 14.2,
    "total_energy_kj": 2.0
  },
  "uncoordinated_outcome": {
    "dropped_users": 18,
    "avg_sinr_db": 8.1,
    "total_energy_kj": 1.99
  },
  "delta": {
    "prevented_drops": 18,
    "sinr_delta_db": 6.1,
    "energy_tradeoff_pct": 0.5
  }
}
```

---

### 9. Multi-Tick Plans

`GET /api/v1/plans`

Returns active multi-tick execution plans generated by the engine for complex coordination sequences (e.g. handover before shutdown).

---

### 10. Conformal Prediction Intervals

`GET /api/v1/twin/intervals`

Query parameters:
- `cell_id` (string, optional): Target cell (defaults to returning all cells).

**Response `200 OK`**:
```json
{
  "status": "ok",
  "calibrated": true,
  "intervals": {
    "cell_b": {
      "sinr": {
        "point": 12.5,
        "lower": 12.2,
        "upper": 12.8,
        "half_width": 0.3,
        "confidence": 0.90
      },
      "load": {
        "point": 0.42,
        "lower": 0.38,
        "upper": 0.46,
        "half_width": 0.04,
        "confidence": 0.90
      }
    }
  }
}
```

---

### 11. Twin Prediction

`GET /api/v1/twin/prediction`

Returns digital twin forward evaluation results, including whether guard conditions were breached.

---

### 12. Twin Drift Injection

`POST /api/v1/twin/drift`

Testing endpoint: injects synthetic channel degradation, shadowing, or model drift into the digital twin to observe conformal interval widening and safety guard triggering.

**Request Body**:
```json
{
  "cell_id": "cell_b",
  "drift_sinr_db": -4.0,
  "duration_ticks": 20
}
```

**Response `200 OK`**:
```json
{
  "status": "drift_injected",
  "cell_id": "cell_b",
  "half_width_updated": 0.72
}
```

---

### 13. Risk Dial Management

`GET /api/v1/risk-dial`

Returns active objective weights.

**Response `200 OK`**:
```json
{
  "weight_coverage": 0.30,
  "weight_energy": 0.20,
  "weight_interference": 0.20,
  "weight_drop_rate": 0.30,
  "label": "Balanced"
}
```

`POST /api/v1/risk-dial`

Update objective weights. The four weights must sum to `1.0` ($\pm 0.001$).

**Request Body**:
```json
{
  "weight_coverage": 0.40,
  "weight_energy": 0.10,
  "weight_interference": 0.20,
  "weight_drop_rate": 0.30,
  "label": "Coverage Priority"
}
```

---

### 14. Pareto Trade-Off Frontier

`GET /api/v1/benchmarks/pareto`

Returns precomputed or evaluated points along the multi-objective Pareto frontier, illustrating trade-offs between energy conservation and service drop risk.

---

### 15. Impact Telemetry

`GET /api/v1/impact`

Provides sustainability and financial auditing telemetry based on recorded arbitration decisions.

**Response `200 OK`**:
```json
{
  "latest": {
    "co2_saved_kg": 0.012,
    "outages_prevented": 1,
    "user_minutes_saved": 166.2
  },
  "extrapolation_1000_sites": {
    "annual_co2_tonnes_saved": 43.8,
    "annual_sla_penalties_prevented_usd": 124000.0
  },
  "assumptions": {
    "grid_carbon_intensity_g_per_kwh": 417.0,
    "sla_cost_per_outage_minute_usd": 1.25,
    "extrapolation_site_count": 1000
  }
}
```

---

### 16. Optimality Benchmark

`GET /api/v1/optimality` (or `/api/v1/benchmarks/optimality`)

Returns the optimality gap comparison between ARBITER's real-time ladder search and an exhaustive brute-force grid search.

**Response `200 OK`**:
```json
{
  "scenario": "2AM_COLLISION",
  "seed": 42,
  "utility_baseline": 0.54,
  "utility_arbiter": 0.98,
  "utility_optimum": 0.98,
  "gap_pct": 0.01,
  "arbiter_ms": 4.17,
  "optimum_s": 0.311,
  "combos_evaluated": 64
}
```

---

### 17. Run Benchmark

`POST /api/v1/benchmarks/run`

Triggers a Monte Carlo benchmark run across multiple random seeds in the background.

---

## WebSocket Channels

### 1. Real-time Telemetry Stream: `/api/v1/ws/live`

- **Protocol**: Standard WebSocket (`ws://` or `wss://`)
- **Direction**: Server $\rightarrow$ Client (read-only broadcast)
- **Message Frequency**: ~100 ms (synchronized with the simulation engine tick)

**Message Format**:
```json
{
  "type": "frame",
  "tick": 14,
  "simulated_time": "02:00:01.400",
  "cells": [ ... ],
  "proposals": [ ... ],
  "verdicts": [ ... ],
  "kpis": {
    "total_energy_w": 1820.0,
    "avg_sinr_db": 13.8,
    "dropped_users": 0
  }
}
```

### 2. Replay Stream: `/api/v1/ws/replay`

- **Protocol**: Standard WebSocket
- **Description**: Streams pre-recorded or deterministic replay runs frame-by-frame.
- **Client Controls**:
  - `{"action": "pause"}`
  - `{"action": "play"}`
  - `{"action": "step", "step": 1}`
  - `{"action": "seek", "tick": 6}`

---

## Static Web Routes

The application serves the following browser-facing user interfaces:
- `GET /`: Landing page showcasing project overview, architecture, and team.
- `GET /problem`: Interactive explanation of the 2 AM collision.
- `GET /how-it-works`: Step-by-step walkthrough of conflict detection and arbitration.
- `GET /results`: Live benchmark results, conformal prediction metrics, and impact.
- `GET /monitor`: Quick link to the 2D NOC and 3D visualizers.
- `GET /team`: Hackathon contributors and affiliations.
- `GET /dashboard/`: Real-time 2D NOC Operations Console.
- `GET /dashboard/3d` (or `/3d`): Real-time Three.js 3D Digital Twin visualization.
