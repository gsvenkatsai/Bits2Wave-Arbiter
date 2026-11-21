"""Unit tests for ARBITER v2 contracts, schemas, protocols, and database.

Validates:
- All fixtures in fixtures/v2/*.json validate against their Pydantic models.
- RiskDial enforces weight sum invariants.
- SnapshotCapable and WeightSettable protocols are runtime checkable.
- Database v2 initialization is idempotent and creates all required tables.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any, Optional

import pytest
from pydantic import ValidationError

from arbiter.shared.database import init_database
from arbiter.shared.models import ParameterType
from arbiter.shared.v2_contracts import (
    ArbitrationPlan,
    CounterfactualResult,
    ImpactRecord,
    OptimalityResult,
    RiskDial,
    SimulationSnapshot,
    SnapshotCapable,
    TwinPredictionV2,
    WeightSettable,
)

FIXTURES_DIR = (
    Path(__file__).resolve().parent.parent.parent / "fixtures" / "v2"
)


def test_twin_prediction_v2_fixture_validates() -> None:
    """TwinPredictionV2 model successfully parses fixture."""
    fixture_path = FIXTURES_DIR / "twin_prediction_v2.json"
    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    pred = TwinPredictionV2.model_validate(data)
    assert pred.candidate_id == "cand-2am-lb-offload-modify"
    assert pred.calibrated is True
    assert pred.n_calibration == 50
    assert len(pred.intervals) == 4

    cov_int = next(i for i in pred.intervals if i.kpi == "coverage_ratio")
    assert cov_int.mean == pytest.approx(0.942)
    assert cov_int.lower == pytest.approx(0.915)
    assert cov_int.upper == pytest.approx(0.965)
    assert cov_int.coverage == pytest.approx(0.90)


def test_plan_fixture_validates() -> None:
    """ArbitrationPlan model successfully parses fixture."""
    fixture_path = FIXTURES_DIR / "plan.json"
    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    plan = ArbitrationPlan.model_validate(data)
    assert plan.plan_id == "plan-2am-staged-coordination"
    assert plan.source_command_id == "cmd-es-cell-b-sleep"
    assert plan.created_tick == 6
    assert plan.expires_tick == 12
    assert len(plan.steps) == 3

    step_1 = plan.steps[0]
    assert step_1.step_index == 1
    assert step_1.tick_offset == 0
    assert step_1.target_cell_id == "cell_A"
    assert step_1.parameter == ParameterType.TX_POWER_DBM
    assert step_1.value == pytest.approx(44.15)
    assert step_1.precondition == "sinr(cell_A)>=8.0"
    assert step_1.status == "COMPLETED"

    step_2 = plan.steps[1]
    assert step_2.parameter == ParameterType.OFFLOAD_RATIO
    assert step_2.value == pytest.approx(0.15)


def test_counterfactual_fixture_validates() -> None:
    """CounterfactualResult model successfully parses fixture."""
    fixture_path = FIXTURES_DIR / "counterfactual.json"
    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    res = CounterfactualResult.model_validate(data)
    assert res.decision_id == "dec-2am-collision-06"
    assert res.batch_tick == 6
    assert res.horizon_ticks == 10
    assert res.with_arbiter.dropped_users == 0
    assert res.with_arbiter.mean_sinr_db == pytest.approx(8.49)
    assert res.without_arbiter.dropped_users == 14
    assert res.without_arbiter.mean_sinr_db == pytest.approx(4.12)
    assert res.diff["dropped_users"] == pytest.approx(-14.0)
    assert "blackout" in res.explanation


def test_optimality_fixture_validates() -> None:
    """OptimalityResult model successfully parses fixture."""
    fixture_path = FIXTURES_DIR / "optimality.json"
    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    opt = OptimalityResult.model_validate(data)
    assert opt.scenario == "2_AM_COLLISION"
    assert opt.seed == 42
    assert opt.utility_baseline == pytest.approx(0.352)
    assert opt.utility_arbiter == pytest.approx(0.815)
    assert opt.utility_optimum == pytest.approx(0.845)
    assert opt.gap_pct == pytest.approx(3.55)
    assert opt.arbiter_ms == pytest.approx(3.82)
    assert opt.optimum_s == pytest.approx(1.74)
    assert opt.combos_evaluated == 64


def test_risk_dial_fixture_validates() -> None:
    """RiskDial model successfully parses fixture."""
    fixture_path = FIXTURES_DIR / "risk_dial.json"
    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    dial = RiskDial.model_validate(data)
    assert dial.weight_coverage == pytest.approx(0.30)
    assert dial.weight_energy == pytest.approx(0.20)
    assert dial.weight_interference == pytest.approx(0.20)
    assert dial.weight_drop_rate == pytest.approx(0.30)
    assert dial.label == "night_balanced"
    assert dial.total == pytest.approx(1.0)
    assert dial.as_tuple() == (0.30, 0.20, 0.20, 0.30)


def test_impact_fixture_validates() -> None:
    """ImpactRecord model successfully parses fixture."""
    fixture_path = FIXTURES_DIR / "impact.json"
    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    impact = ImpactRecord.model_validate(data)
    assert impact.tick == 6
    assert impact.energy_saved_kwh == pytest.approx(0.155)
    assert impact.cost_inr == pytest.approx(1.085)
    assert impact.co2_g == pytest.approx(127.1)
    assert impact.tariff_label == "COMMERCIAL_OFF_PEAK_NIGHT"


def test_risk_dial_rejects_weights_not_summing_to_one() -> None:
    """RiskDial raises ValidationError if weights do not sum to 1.0."""
    msg = "RiskDial weights must sum to 1.0"
    with pytest.raises(ValidationError, match=msg):
        RiskDial(
            weight_coverage=0.5,
            weight_energy=0.2,
            weight_interference=0.2,
            weight_drop_rate=0.2,  # sum is 1.1
            label="invalid_high",
        )

    with pytest.raises(ValidationError, match=msg):
        RiskDial(
            weight_coverage=0.2,
            weight_energy=0.2,
            weight_interference=0.2,
            weight_drop_rate=0.2,  # sum is 0.8
            label="invalid_low",
        )


def test_risk_dial_accepts_valid_floating_weights() -> None:
    """RiskDial accepts arbitrary floating weights that sum to 1.0."""
    dial = RiskDial(
        weight_coverage=0.25,
        weight_energy=0.25,
        weight_interference=0.25,
        weight_drop_rate=0.25,
        label="uniform",
    )
    assert dial.total == pytest.approx(1.0)


def test_simulation_snapshot_model() -> None:
    """SimulationSnapshot model correctly encapsulates opaque payload."""
    snap = SimulationSnapshot(
        tick=42,
        payload={"cells": {"cell_A": 43.0}, "users": [1, 2, 3]},
    )
    assert snap.tick == 42
    assert snap.payload["cells"]["cell_A"] == 43.0
    assert len(snap.payload["users"]) == 3


def test_protocols_runtime_checkable() -> None:
    """SnapshotCapable and WeightSettable satisfy runtime_checkable."""

    class DummySim:
        def snapshot(self) -> SimulationSnapshot:
            return SimulationSnapshot(tick=0, payload={})

        def restore(self, snap: SimulationSnapshot) -> None:
            pass

        def fork(self, seed: Optional[int] = None) -> Any:
            return self

    class DummyEngine:
        def __init__(self) -> None:
            self._dial = RiskDial(
                weight_coverage=0.25,
                weight_energy=0.25,
                weight_interference=0.25,
                weight_drop_rate=0.25,
            )

        def set_weights(self, dial: RiskDial) -> None:
            self._dial = dial

        def get_weights(self) -> RiskDial:
            return self._dial

    sim = DummySim()
    eng = DummyEngine()

    assert isinstance(sim, SnapshotCapable)
    assert isinstance(eng, WeightSettable)


def test_init_database_creates_v2_tables_and_is_idempotent(
    tmp_path: Path,
) -> None:
    """init_database creates both v1 and v2 tables, indexes, idempotently."""
    db_file = tmp_path / "test_arbiter_v2.db"
    db_path = str(db_file)

    # First call: creates database and all tables
    init_database(db_path)

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
        )
        tables = {row[0] for row in cur.fetchall()}

        # Verify v1 tables
        assert "commit_log" in tables
        assert "event_stream" in tables
        assert "audit_decisions" in tables

        # Verify v2 tables
        assert "v2_plans" in tables
        assert "v2_counterfactuals" in tables
        assert "v2_impact" in tables
        assert "v2_calibration" in tables

        # Verify indexes
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name;"
        )
        indexes = {row[0] for row in cur.fetchall()}
        assert "idx_v2_plans_source_cmd" in indexes
        assert "idx_v2_plans_tick" in indexes
        assert "idx_v2_counterfactuals_tick" in indexes
        assert "idx_v2_impact_tick" in indexes
        assert "idx_v2_calibration_candidate" in indexes
        assert "idx_v2_calibration_tick" in indexes
    finally:
        conn.close()

    # Second call: must be idempotent and not raise errors
    init_database(db_path)


def test_v2_database_insert_and_query(tmp_path: Path) -> None:
    """Test insert and query operations on v2 tables."""
    db_file = tmp_path / "test_crud_v2.db"
    db_path = str(db_file)
    init_database(db_path)

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        plan_payload = {"plan_id": "p-1", "steps": []}
        cur.execute(
            """
            INSERT INTO v2_plans (
                plan_id, source_command_id, created_tick, expires_tick, payload
            ) VALUES (?, ?, ?, ?, ?);
            """,
            ("p-1", "cmd-1", 10, 20, json.dumps(plan_payload)),
        )
        conn.commit()

        cur.execute(
            "SELECT plan_id, payload FROM v2_plans WHERE plan_id = ?",
            ("p-1",),
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "p-1"
        assert json.loads(row[1]) == plan_payload
    finally:
        conn.close()
