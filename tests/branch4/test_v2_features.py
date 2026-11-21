"""
tests/branch4/test_v2_features.py
Unit and integration tests for ARBITER v2 Feature Suite — Branch 4.

Coverage:
  1. #4 Flight Recorder:
     - GET /api/v1/recorder/decisions
     - GET /api/v1/recorder/counterfactual/{decision_id} (cache, aged-out error, fixtures)
     - GET /api/v1/plans (multi-tick plan timeline)
  2. #5 Uncertainty View:
     - GET /api/v1/twin/prediction
     - POST /api/v1/twin/drift (drift hook widening intervals, toggling calibrated flag)
  3. #6 Dynamic RiskDial & Multi-Objective Pareto Frontier:
     - POST /api/v1/risk-dial (valid presets, set_weights invocation, invalid weight sum rejection)
     - GET /api/v1/risk-dial
     - GET /api/v1/benchmarks/pareto (Pareto frontier sweep, determinism, active setting highlight)
  4. #7 Environmental & Economic Impact:
     - Time-of-Use tariff resolution
     - ImpactCalculator tick math & 1,000-site extrapolation
     - GET /api/v1/impact
     - Ingest telemetry auto-calculating impact
  5. #8 Offline Optimality Benchmark:
     - GET /api/v1/optimality
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any
import pytest
from fastapi.testclient import TestClient

from arbiter.api.routes import (
    get_db_path,
    get_decision_engine,
    get_monitor,
    set_decision_engine,
)
from arbiter.api.server import app
from arbiter.benchmarks.pareto import (
    ParetoFrontierSweeper,
    ParetoPoint,
    is_dominated,
)
from arbiter.shared.database_v2 import init_v2_tables
from arbiter.shared.v2_contracts import (
    ArbitrationPlan,
    CounterfactualResult,
    ImpactRecord,
    OptimalityResult,
    RiskDial,
    TwinPredictionV2,
)
from arbiter.telemetry.impact import (
    ImpactCalculator,
    resolve_tariff,
    store_impact_record,
)
from arbiter.telemetry.monitor import KPITelemetryMonitor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path: Any) -> str:
    """Fresh temporary SQLite database with both v1 and v2 tables initialized."""
    db_file = str(tmp_path / "test_v2_arbiter.db")
    conn = sqlite3.connect(db_file)
    try:
        from arbiter.shared.database import SCHEMA_DDL
        conn.executescript(SCHEMA_DDL)
        init_v2_tables(conn)
        conn.commit()
    finally:
        conn.close()
    return db_file


@pytest.fixture
def test_monitor(tmp_db: str) -> KPITelemetryMonitor:
    """Isolated KPITelemetryMonitor backed by temp DB."""
    return KPITelemetryMonitor(db_path=tmp_db)


@pytest.fixture
def client(tmp_db: str, test_monitor: KPITelemetryMonitor) -> Any:
    """TestClient with overridden DB and monitor dependencies."""
    app.dependency_overrides[get_monitor] = lambda: test_monitor
    app.dependency_overrides[get_db_path] = lambda: tmp_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    set_decision_engine(None)


# ---------------------------------------------------------------------------
# 1. Flight Recorder & Plans Tests
# ---------------------------------------------------------------------------

def test_recorder_decisions_empty_falls_back_to_fixture(client: TestClient) -> None:
    """GET /api/v1/recorder/decisions returns fixture decision when DB is empty."""
    resp = client.get("/api/v1/recorder/decisions")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]["decision_id"] == "dec-2am-collision-06"
    assert data[0]["batch_tick"] == 6


def test_recorder_decisions_from_database(client: TestClient, tmp_db: str) -> None:
    """GET /api/v1/recorder/decisions queries persisted audit_decisions in order."""
    conn = sqlite3.connect(tmp_db)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO audit_decisions (
                    decision_id, command_id, batch_tick, action, approved_value,
                    delta_utility, reason_code, hold_count, decided_at_epoch_ms
                ) VALUES ('dec-custom-1', 'cmd-1', 10, 'APPROVE', 42.0, 0.15, 'NONE', 0, 1700000000000)
                """
            )
    finally:
        conn.close()

    resp = client.get("/api/v1/recorder/decisions")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["decision_id"] == "dec-custom-1"
    assert data[0]["batch_tick"] == 10
    assert data[0]["action"] == "APPROVE"


def test_recorder_counterfactual_fixture_and_caching(
    client: TestClient, tmp_db: str
) -> None:
    """GET /api/v1/recorder/counterfactual/{id} loads fixture and caches in v2_counterfactuals."""
    resp = client.get("/api/v1/recorder/counterfactual/dec-2am-collision-06")
    assert resp.status_code == 200
    data = resp.json()
    cf = CounterfactualResult.model_validate(data)
    assert cf.decision_id == "dec-2am-collision-06"
    assert cf.batch_tick == 6
    assert cf.with_arbiter.dropped_users == 0
    assert cf.without_arbiter.dropped_users == 14
    assert cf.diff["dropped_users"] == -14.0

    # Verify cached in v2_counterfactuals SQLite table
    conn = sqlite3.connect(tmp_db)
    try:
        cur = conn.execute(
            "SELECT payload FROM v2_counterfactuals WHERE decision_id = 'dec-2am-collision-06'"
        )
        row = cur.fetchone()
        assert row is not None
        cached = json.loads(row[0])
        assert cached["decision_id"] == "dec-2am-collision-06"
    finally:
        conn.close()


def test_recorder_counterfactual_aged_out_error(client: TestClient) -> None:
    """GET /api/v1/recorder/counterfactual/{id} returns 404 when snapshot is aged out/absent."""
    resp = client.get("/api/v1/recorder/counterfactual/dec-aged-out-999")
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert "aged out" in detail or "does not exist" in detail


def test_get_plans_endpoint(client: TestClient, tmp_db: str) -> None:
    """GET /api/v1/plans returns multi-tick arbitration plans."""
    resp = client.get("/api/v1/plans")
    assert resp.status_code == 200
    plans = resp.json()
    assert isinstance(plans, list)
    assert len(plans) >= 1
    plan = ArbitrationPlan.model_validate(plans[0])
    assert plan.plan_id == "plan-2am-staged-coordination"
    assert len(plan.steps) == 3
    assert plan.steps[0].status == "COMPLETED"
    assert plan.steps[1].status == "PENDING"


# ---------------------------------------------------------------------------
# 2. Uncertainty View & Twin Drift Tests
# ---------------------------------------------------------------------------

def test_twin_prediction_endpoint(client: TestClient) -> None:
    """GET /api/v1/twin/prediction returns TwinPredictionV2 with uncertainty intervals."""
    resp = client.get("/api/v1/twin/prediction")
    assert resp.status_code == 200
    twin = TwinPredictionV2.model_validate(resp.json())
    assert len(twin.intervals) >= 4
    for intv in twin.intervals:
        assert intv.lower <= intv.mean <= intv.upper


def test_twin_drift_hook(client: TestClient) -> None:
    """POST /api/v1/twin/drift widens intervals and toggles calibrated flag to False."""
    base_resp = client.get("/api/v1/twin/prediction")
    base_intervals = {
        i["kpi"]: (i["lower"], i["upper"]) for i in base_resp.json()["intervals"]
    }

    drift_resp = client.post("/api/v1/twin/drift", json={"drift_scale": 2.0})
    assert drift_resp.status_code == 200
    drifted = TwinPredictionV2.model_validate(drift_resp.json())

    assert drifted.calibrated is False
    assert drifted.n_calibration == 0

    for intv in drifted.intervals:
        base_low, base_high = base_intervals[intv.kpi]
        # Drift must have widened or maintained bounds
        assert intv.lower <= base_low
        assert intv.upper >= base_high


# ---------------------------------------------------------------------------
# 3. Dynamic RiskDial & Multi-Objective Pareto Frontier Tests
# ---------------------------------------------------------------------------

class MockDecisionEngine:
    """Stub engine implementing set_weights protocol."""

    def __init__(self) -> None:
        self.dial: RiskDial | None = None

    def set_weights(self, dial: RiskDial) -> None:
        self.dial = dial


def test_risk_dial_presets_and_set_weights(client: TestClient) -> None:
    """POST /api/v1/risk-dial applies weights and invokes set_weights on engine."""
    mock_engine = MockDecisionEngine()
    set_decision_engine(mock_engine)

    # 1. Night saver preset
    night_saver = {
        "weight_coverage": 0.15,
        "weight_energy": 0.50,
        "weight_interference": 0.15,
        "weight_drop_rate": 0.20,
        "label": "Night saver",
    }
    resp = client.post("/api/v1/risk-dial", json=night_saver)
    assert resp.status_code == 200
    dial = RiskDial.model_validate(resp.json())
    assert dial.label == "Night saver"
    assert mock_engine.dial is not None
    assert mock_engine.dial.label == "Night saver"

    # Verify GET /api/v1/risk-dial reflects the change
    get_resp = client.get("/api/v1/risk-dial")
    assert get_resp.status_code == 200
    assert get_resp.json()["label"] == "Night saver"


def test_risk_dial_rejects_invalid_weights_sum(client: TestClient) -> None:
    """POST /api/v1/risk-dial returns 422 if weights do not sum to 1.0."""
    invalid = {
        "weight_coverage": 0.50,
        "weight_energy": 0.50,
        "weight_interference": 0.50,
        "weight_drop_rate": 0.50,
        "label": "Invalid",
    }
    resp = client.post("/api/v1/risk-dial", json=invalid)
    assert resp.status_code == 422


def test_pareto_sweeper_dominance_logic() -> None:
    """Pareto domination logic correctly identifies non-dominated points."""
    p_better = ParetoPoint(
        weight_coverage=0.3,
        weight_energy=0.2,
        weight_interference=0.2,
        weight_drop_rate=0.3,
        energy_w=80000.0,
        energy_kw=80.0,
        dropped_users=1.0,
        drop_rate_pct=2.0,
        mean_sinr_db=9.0,
        utility=0.8,
    )
    p_worse = ParetoPoint(
        weight_coverage=0.3,
        weight_energy=0.2,
        weight_interference=0.2,
        weight_drop_rate=0.3,
        energy_w=100000.0,
        energy_kw=100.0,
        dropped_users=5.0,
        drop_rate_pct=10.0,
        mean_sinr_db=7.0,
        utility=0.4,
    )
    assert is_dominated(p_worse, p_better) is True
    assert is_dominated(p_better, p_worse) is False


def test_pareto_frontier_endpoint_and_determinism(client: TestClient) -> None:
    """GET /api/v1/benchmarks/pareto returns deterministic computed frontier points."""
    resp = client.get("/api/v1/benchmarks/pareto?seed=42")
    assert resp.status_code == 200
    data = resp.json()
    points = data["points"]
    assert len(points) >= 3
    assert data["active_point"] is not None

    # Check that numbers are computed, sorted, and non-empty
    for p in points:
        assert p["energy_w"] > 0
        assert p["dropped_users"] >= 0
        assert "is_pareto_optimal" in p

    # Determinism check with same seed
    resp2 = client.get("/api/v1/benchmarks/pareto?seed=42")
    assert resp.json() == resp2.json()


# ---------------------------------------------------------------------------
# 4. Environmental & Economic Impact Tests
# ---------------------------------------------------------------------------

def test_time_of_use_tariff_resolution() -> None:
    """TOU tariff schedule maps hours to correct tariff tiers and rates."""
    label_night, rate_night = resolve_tariff(2)
    assert label_night == "COMMERCIAL_OFF_PEAK_NIGHT"
    assert rate_night == pytest.approx(7.00)

    label_day, rate_day = resolve_tariff(12)
    assert label_day == "COMMERCIAL_NORMAL_DAY"
    assert rate_day == pytest.approx(9.50)

    label_peak, rate_peak = resolve_tariff(19)
    assert label_peak == "COMMERCIAL_PEAK_EVENING"
    assert rate_peak == pytest.approx(12.00)


def test_impact_calculator_tick_and_extrapolation() -> None:
    """ImpactCalculator converts Watts to kWh, ₹, gCO2 and scales to 1,000 sites."""
    calc = ImpactCalculator(grid_emission_factor_g_per_kwh=820.0)

    # 20,140 W saved over 0.1 s at 2 AM (night tariff ₹7.00/kWh)
    record = calc.calculate_tick_impact(
        tick=6, energy_saved_w=20140.0, duration_s=0.1, hour=2
    )
    expected_kwh = (20140.0 * 0.1) / 3_600_000.0
    assert record.energy_saved_kwh == pytest.approx(expected_kwh, rel=1e-3)
    assert record.cost_inr == pytest.approx(expected_kwh * 7.00, rel=1e-3)
    assert record.co2_g == pytest.approx(expected_kwh * 820.0, rel=1e-3)
    assert record.tariff_label == "COMMERCIAL_OFF_PEAK_NIGHT"

    # 1,000 sites extrapolation
    extrap = calc.calculate_extrapolation_1000_sites(
        cluster_energy_saved_w=20140.0, cluster_sites=5, target_sites=1000
    )
    assert extrap["extrapolation"] is True
    assert extrap["target_sites"] == 1000
    assert extrap["annual_kwh_saved"] > 0
    assert extrap["annual_cost_savings_inr"] > 0
    assert "EXTRAPOLATION ONLY" in extrap["assumptions_disclaimer"]


def test_impact_endpoint_and_database_persistence(
    client: TestClient, tmp_db: str
) -> None:
    """GET /api/v1/impact returns latest record and extrapolation."""
    resp = client.get("/api/v1/impact?hour=2")
    assert resp.status_code == 200
    data = resp.json()
    assert "latest" in data
    assert "extrapolation_1000_sites" in data
    assert "assumptions" in data

    latest = ImpactRecord.model_validate(data["latest"])
    assert latest.energy_saved_kwh > 0
    assert latest.cost_inr > 0
    assert latest.co2_g > 0

    # Verify stored in v2_impact SQLite table
    conn = sqlite3.connect(tmp_db)
    try:
        cur = conn.execute("SELECT COUNT(*) FROM v2_impact")
        count = cur.fetchone()[0]
        assert count >= 1
    finally:
        conn.close()


def test_telemetry_ingest_auto_computes_impact(
    client: TestClient, tmp_db: str
) -> None:
    """POST /api/v1/telemetry/ingest computes and records impact when baseline power is present."""
    payload = {
        "batch_tick": 1,
        "samples": [
            {
                "sample_id": "sample-1",
                "cell_id": "cell_A",
                "sinr_db": 10.0,
                "dropped_users": 0,
                "active_users": 35,
                "throughput_mbps": 50.0,
                "energy_consumption_w": 20000.0,
                "timestamp_epoch_ms": 1700000000100,
            }
        ],
        "uncoord_power_kw": 40.0,  # 40 kW = 40,000 W -> saves 20,000 W
    }
    resp = client.post("/api/v1/telemetry/ingest", json=payload)
    assert resp.status_code == 200

    conn = sqlite3.connect(tmp_db)
    try:
        cur = conn.execute(
            "SELECT payload FROM v2_impact ORDER BY impact_id DESC LIMIT 1"
        )
        row = cur.fetchone()
        assert row is not None
        rec = ImpactRecord.model_validate(json.loads(row[0]))
        assert rec.tick == 1
        assert rec.energy_saved_kwh > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 5. Offline Optimality Benchmark Tests
# ---------------------------------------------------------------------------

def test_optimality_endpoint(client: TestClient) -> None:
    """GET /api/v1/optimality returns OptimalityResult from fixture."""
    resp = client.get("/api/v1/optimality")
    assert resp.status_code == 200
    opt = OptimalityResult.model_validate(resp.json())
    assert opt.scenario == "2_AM_COLLISION"
    assert opt.utility_baseline == pytest.approx(0.352)
    assert opt.utility_arbiter == pytest.approx(0.815)
    assert opt.utility_optimum == pytest.approx(0.845)
    assert opt.gap_pct == pytest.approx(3.55)
    assert opt.arbiter_ms > 0
    assert opt.optimum_s > 0
    assert opt.combos_evaluated == 64


# ---------------------------------------------------------------------------
# 6. Additional Regression & Edge Case Tests
# ---------------------------------------------------------------------------

def test_all_three_risk_dial_presets_validate(client: TestClient) -> None:
    """Verify all 3 presets (Night saver, Balanced, Rush-hour safe) succeed."""
    presets = [
        {"weight_coverage": 0.15, "weight_energy": 0.50, "weight_interference": 0.15, "weight_drop_rate": 0.20, "label": "Night saver"},
        {"weight_coverage": 0.30, "weight_energy": 0.20, "weight_interference": 0.20, "weight_drop_rate": 0.30, "label": "Balanced"},
        {"weight_coverage": 0.40, "weight_energy": 0.05, "weight_interference": 0.15, "weight_drop_rate": 0.40, "label": "Rush-hour safe"},
    ]
    for p in presets:
        resp = client.post("/api/v1/risk-dial", json=p)
        assert resp.status_code == 200
        applied = resp.json()
        assert applied["label"] == p["label"]
        dial = RiskDial.model_validate(applied)
        assert dial.total == pytest.approx(1.0)


def test_pareto_sweeper_forked_simulation() -> None:
    """ParetoFrontierSweeper executes forked simulation if engine implements fork()."""
    class MockSample:
        def __init__(self) -> None:
            self.dropped_users = 0
            self.energy_consumption_w = 90000.0
            self.sinr_db = 8.5
            self.active_users = 35

    class MockForkedSim:
        def step(self) -> list[MockSample]:
            return [MockSample()]

    class MockSnapshotSim:
        def snapshot(self) -> Any:
            return {}
        def restore(self, snap: Any) -> None:
            pass
        def fork(self, seed: Any = None) -> MockForkedSim:
            return MockForkedSim()

    sim = MockSnapshotSim()
    sweeper = ParetoFrontierSweeper(seed=42)
    dial = RiskDial(
        weight_coverage=0.3,
        weight_energy=0.2,
        weight_interference=0.2,
        weight_drop_rate=0.3,
        label="Test"
    )
    point = sweeper.evaluate_dial(dial, simulation_engine=sim)
    assert point.energy_w == pytest.approx(90000.0)
    assert point.dropped_users == 0.0


def test_impact_config_environment_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """ImpactCalculator respects custom emission factors and tariff rates."""
    custom_factor = 950.0
    calc = ImpactCalculator(grid_emission_factor_g_per_kwh=custom_factor)
    rec = calc.calculate_tick_impact(tick=1, energy_saved_w=3600000.0, duration_s=1.0, hour=2)
    # 3,600,000 W for 1 s = 1.0 kWh
    assert rec.energy_saved_kwh == pytest.approx(1.0)
    assert rec.co2_g == pytest.approx(custom_factor)


def test_counterfactual_metric_differentials(client: TestClient) -> None:
    """Counterfactual diffs exactly match with_arbiter minus without_arbiter."""
    resp = client.get("/api/v1/recorder/counterfactual/dec-2am-collision-06")
    assert resp.status_code == 200
    cf = CounterfactualResult.model_validate(resp.json())
    w = cf.with_arbiter
    wo = cf.without_arbiter
    assert cf.diff["dropped_users"] == pytest.approx(w.dropped_users - wo.dropped_users)
    assert cf.diff["mean_sinr_db"] == pytest.approx(w.mean_sinr_db - wo.mean_sinr_db, abs=1e-2)
    assert cf.diff["energy_w"] == pytest.approx(w.energy_w - wo.energy_w, abs=1.0)
    assert cf.diff["interference_db"] == pytest.approx(w.interference_db - wo.interference_db, abs=1e-2)
    assert cf.diff["utility"] == pytest.approx(w.utility - wo.utility, abs=1e-3)


def test_plan_steps_preconditions_and_parameters(client: TestClient) -> None:
    """Multi-tick plan steps contain valid parameters, preconditions, and statuses."""
    resp = client.get("/api/v1/plans")
    assert resp.status_code == 200
    plans = [ArbitrationPlan.model_validate(p) for p in resp.json()]
    for plan in plans:
        assert plan.expires_tick >= plan.created_tick
        assert len(plan.steps) > 0
        for step in plan.steps:
            assert step.parameter in {"TX_POWER_DBM", "OFFLOAD_RATIO", "SLEEP_STATE", "ANTENNA_TILT_DEG"}
            assert len(step.precondition) > 0
            assert step.status in {"PENDING", "COMPLETED", "ABORTED"}


def test_optimality_gap_calculation_consistency(client: TestClient) -> None:
    """Verify optimality gap arithmetic against utility values."""
    resp = client.get("/api/v1/optimality")
    assert resp.status_code == 200
    opt = resp.json()
    # gap_pct = ((optimum - arbiter) / optimum) * 100
    expected_gap = ((opt["utility_optimum"] - opt["utility_arbiter"]) / opt["utility_optimum"]) * 100.0
    assert opt["gap_pct"] == pytest.approx(expected_gap, rel=1e-2)

