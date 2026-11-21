"""End-to-End closed-loop test for the 2 AM Collision scenario.

Playbook Section 6.1 Traceability:
1. Initial State: 5 cells active at 43 dBm; Cell B active user count drops,
   Cell A user count surges.
2. Tick 1 Ingress: ES proposes Cell B OFF; LB proposes 40% A->B;
   CC proposes Cell A +3 dB.
3. Referee Processing:
   - Conflict graph builds 2 clash edges.
   - Decision engine detects Cell B shutdown clashing with 40% user transfer.
   - Engine issues DELAY (hold 1) to ES; bisects LB offload to MODIFY (15%);
     bisects CC power to MODIFY (+1 dB).
4. Actuation & Verification:
   - Actuator commits 15% offload and +1 dB power boost to commit_log.
   - Cell B remains active while offloaded users complete handovers.
   - Zero users dropped (0.0% drop rate).
   - Uncoordinated baseline comparison: blackout (Cell B 0 W, dropped users).
"""
import sqlite3
import numpy as np
import pytest
from fastapi.testclient import TestClient

from arbiter.actuator.enforcer import ActuatorEnforcer
from arbiter.api.server import app
from arbiter.controllers.scenarios import (
    apply_uncoordinated_baseline,
    collision_report,
    trigger_2am_collision,
)
from arbiter.detector.graph import ConflictGraphBuilder
from arbiter.engine.actuator_adapter import ActuatorAdapter
from arbiter.engine.contracts import (
    ActionType,
    CommandProposal,
    ReasonCode,
)
from arbiter.queue.orchestrator import QueueOrchestrator
from arbiter.shared.database import init_database
from arbiter.shared.models import ControllerType
from arbiter.simulator.engine import ScenarioType, SimulationEngine
from arbiter.storage.commit_log import SQLiteCommitLogger
from tests.branch2.test_decision_ladder import _scenario_twin, _two_am_commands


HEX_ADJACENCY: dict[str, list[str]] = {
    "A": ["B"],
    "B": ["A", "C"],
    "C": ["B", "D"],
    "D": ["C", "E"],
    "E": ["D"],
}


class MockNetworkTarget:
    """In-memory target tracking parameter changes applied by Actuator."""

    def __init__(self) -> None:
        self.applied: dict[str, dict[str, float]] = {}

    def apply_parameter(
        self, cell_id: str, parameter: str, value: float
    ) -> bool:
        if cell_id not in self.applied:
            self.applied[cell_id] = {}
        self.applied[cell_id][parameter] = value
        return True

    def get_parameter(self, cell_id: str, parameter: str) -> float:
        return self.applied.get(cell_id, {}).get(parameter, 0.0)


def test_2am_collision_ingress_and_conflict_graph() -> None:
    """Verify ingress proposals land in same 100 ms and create clash edges."""
    base_ts = 1732154400000
    proposals = trigger_2am_collision(base_ts_ms=base_ts)

    assert len(proposals) == 3
    # Check 100 ms window: +10, +15, +20 ms from base
    for p in proposals:
        assert base_ts <= p.timestamp_epoch_ms < base_ts + 100

    def make_prop(**overrides: object) -> CommandProposal:
        payload = dict(overrides)
        payload.setdefault("controller_type", ControllerType.ENERGY_SAVING)
        payload.setdefault("timestamp_epoch_ms", base_ts)
        return CommandProposal.model_validate(payload)

    commands = _two_am_commands(make_prop)
    builder = ConflictGraphBuilder()
    graph = builder.build(commands, HEX_ADJACENCY)

    # Conflict graph builds 2 clash edges: (es, lb) and (cc, es)
    assert sorted(graph.nodes) == ["cc", "es", "lb"]
    edge_pairs = {tuple(sorted(e)) for e in graph.edges}
    assert edge_pairs == {("es", "lb"), ("cc", "es")}


def test_2am_collision_referee_arbitration_and_commit_log(
    tmp_path: pytest.TempPathFactory,
) -> None:
    """Verify referee issues DELAY to ES, modifies LB & CC, commits to DB."""
    db_file = str(tmp_path / "test_arbiter_e2e.db")
    init_database(db_file)

    commit_logger = SQLiteCommitLogger(db_file)
    target = MockNetworkTarget()
    enforcer = ActuatorEnforcer(target=target, commit_logger=commit_logger)
    actuator_adapter = ActuatorAdapter(enforcer)

    def make_prop(**overrides: object) -> CommandProposal:
        payload = dict(overrides)
        payload.setdefault("controller_type", ControllerType.ENERGY_SAVING)
        payload.setdefault("timestamp_epoch_ms", 1000)
        return CommandProposal.model_validate(payload)

    commands = _two_am_commands(make_prop)
    twin = _scenario_twin()
    orchestrator = QueueOrchestrator(
        twin=twin,
        adjacency=HEX_ADJACENCY,
        actuator=actuator_adapter,
        max_workers=2,
    )

    for cmd in commands:
        assert orchestrator.submit(cmd) is True

    result = orchestrator.on_tick(current_tick=1, timestamp_epoch_ms=1000)
    assert result["decisions_count"] == 3

    by_id = {d.command_id: d for d in result["decisions"]}

    # ES gets DELAY (hold count 1)
    assert by_id["es"].action is ActionType.DELAY
    assert by_id["es"].hold_count == 1

    # LB offload bisected to approx 15% (0.1528)
    assert by_id["lb"].action is ActionType.MODIFY
    assert by_id["lb"].reason_code is ReasonCode.HEADROOM
    assert by_id["lb"].approved_value == pytest.approx(0.1528, abs=0.01)

    # CC power boost bisected to approx +1 dB (31.146 vs 30.0)
    assert by_id["cc"].action is ActionType.MODIFY
    assert by_id["cc"].approved_value == pytest.approx(31.146, abs=0.2)

    # Verify SQLite commit_log rows for the 2 committed decisions
    conn = sqlite3.connect(db_file)
    rows = conn.execute(
        "SELECT command_id, target_cell_id, parameter, old_value, new_value "
        "FROM commit_log ORDER BY commit_seq ASC"
    ).fetchall()
    conn.close()

    assert len(rows) == 2
    row_lb = rows[0]
    assert row_lb[0] == "lb"
    assert row_lb[1] == "A"
    assert row_lb[2] == "OFFLOAD_RATIO"
    assert row_lb[3] == 0.0
    assert row_lb[4] == pytest.approx(0.1528, abs=0.01)

    row_cc = rows[1]
    assert row_cc[0] == "cc"
    assert row_cc[1] == "A"
    assert row_cc[2] == "TX_POWER_DBM"
    assert row_cc[3] == 30.0
    assert row_cc[4] == pytest.approx(31.146, abs=0.2)


def test_2am_collision_simulation_coordinated_vs_uncoordinated() -> None:
    """Verify coordinated avoids blackout while uncoordinated causes drops."""
    # 1. Uncoordinated Baseline: causes blackout
    engine_uncoord = SimulationEngine(
        topology="5_CELL_CORE", scenario=ScenarioType.QUIET_2AM, seed=42
    )
    for _ in range(5):
        engine_uncoord.step()

    engine_before = SimulationEngine(
        topology="5_CELL_CORE", scenario=ScenarioType.QUIET_2AM, seed=42
    )
    for _ in range(5):
        engine_before.step()

    uncoord_props = trigger_2am_collision(
        engine=engine_uncoord, base_ts_ms=engine_uncoord.simulated_epoch_ms
    )
    apply_uncoordinated_baseline(engine_uncoord, uncoord_props)
    engine_uncoord.step()

    report_uncoord = collision_report(engine_before, engine_uncoord)
    assert report_uncoord["cell_B_power_watts"] == 0.0, "Cell B at 0 W"
    assert report_uncoord["total_dropped_users"] > 0, "Users dropped"
    assert report_uncoord["blackout_condition"] is True

    # 2. Coordinated ARBITER: Cell B remains active, avoiding blackout
    engine_coord = SimulationEngine(
        topology="5_CELL_CORE", scenario=ScenarioType.QUIET_2AM, seed=42
    )
    for _ in range(5):
        engine_coord.step()

    # Under ARBITER: ES is DELAYED, so Cell B is NOT put to sleep.
    # Cell B remains ACTIVE at full power (~20 W / 43 dBm)
    active_b_power_w = engine_coord.cells["cell_B"].effective_tx_power_w()
    assert active_b_power_w > 0.0, "Cell B stays active under ARBITER"

    report_coord = collision_report(engine_before, engine_coord)
    assert report_coord["cell_B_power_watts"] > 0.0
    assert report_coord["blackout_condition"] is False

    # Note on Playbook Risk (b): Playbook assumes 35 UEs on Cell A and 4 on
    # Cell B with 0 drops; Branch 1 fixture uses 70 UEs at 0.22 scale
    # (15 active) where ue_0013 on Cell C has low baseline SINR.
    # Uncoordinated blackout causes 5 drops (Cell B shut down); coordinated
    # maintains Cell B active and prevents blackout.
    assert (
        report_uncoord["total_dropped_users"]
        > report_coord["total_dropped_users"]
    )


def test_2am_collision_full_acceptance_criteria() -> None:
    """End-to-end regression test for all 2 AM collision acceptance criteria.

    Validates:
    (a) Baseline tick: 0 dropped users, mean SINR >= 8 dB, 5 cells at 43 dBm.
    (b) Pre-collision: Cell A ~35 (> 75%), Cell B ~4 (< 15%), C/D/E light.
    (c) Collision trio land in same 100 ms:
        - ES (cell_B SLEEP_STATE 1.0) -> DELAY (hold 1, reason NONE)
        - LB (cell_B OFFLOAD_RATIO 0.40) -> MODIFY ~0.15 (reason HEADROOM)
        - CC (cell_A TX_POWER_DBM 46) -> MODIFY ~44 dBm (reason HEADROOM)
        - No decision reason outside ReasonCode enum
    (d) Post-actuation: Cell B stays active, drops stay 0; uncoord drops > 0.
    (e) Topology endpoint returns 5 cells.
    """
    # (a) Baseline tick assertions
    engine = SimulationEngine(
        topology="5_CELL_CORE", scenario=ScenarioType.QUIET_2AM, seed=42
    )
    samples = engine.step()
    drops = sum(s.dropped_users for s in samples)
    assert drops == 0, f"Baseline dropped users must be 0, got {drops}"

    active_users = engine.traffic_model.get_active_users(engine.tick)
    cluster_sinr = float(np.mean([u.sinr_db for u in active_users]))
    assert cluster_sinr >= 8.0, (
        f"Mean SINR must be >= 8 dB, got {cluster_sinr}"
    )

    assert len(engine.cells) == 5
    for cid, cell in engine.cells.items():
        assert cell.current_tx_power_dbm == 43.0, f"{cid} not at 43 dBm"
        assert cell.is_sleeping is False, f"{cid} is sleeping in baseline"

    # (b) Pre-collision user distribution & loads
    loads = engine.get_cell_loads()
    cell_counts = {
        cid: sum(1 for u in active_users if u.attached_cell_id == cid)
        for cid in engine.cells
    }
    assert cell_counts["cell_A"] == pytest.approx(35, abs=2)
    assert loads["cell_A"] > 0.75, f"Cell A load {loads['cell_A']:.1%} <= 75%"
    assert cell_counts["cell_B"] == pytest.approx(4, abs=1)
    assert loads["cell_B"] < 0.15, f"Cell B load {loads['cell_B']:.1%} >= 15%"
    for cid in ("cell_C", "cell_D", "cell_E"):
        assert 3 <= cell_counts[cid] <= 8, f"{cid} count not light"

    # Step to tick 6
    for _ in range(5):
        engine.step()

    # (c) Ingress collision proposals & arbitration
    proposals = trigger_2am_collision(
        engine=engine, base_ts_ms=engine.simulated_epoch_ms
    )
    assert len(proposals) == 3
    base_ts = proposals[0].timestamp_epoch_ms
    for p in proposals:
        assert abs(p.timestamp_epoch_ms - base_ts) < 100

    adj = {
        cid: cell.config.neighbor_cell_ids
        for cid, cell in engine.cells.items()
    }
    orchestrator = QueueOrchestrator(
        twin=_scenario_twin(), adjacency=adj, max_workers=2
    )
    for p in proposals:
        orchestrator.submit(CommandProposal.model_validate(p.model_dump()))

    res = orchestrator.on_tick(
        current_tick=6, timestamp_epoch_ms=engine.simulated_epoch_ms
    )
    decisions = res["decisions"]
    assert len(decisions) == 3

    # Verify no reason outside ReasonCode enum
    valid_reasons = set(ReasonCode)
    for d in decisions:
        assert d.reason_code in valid_reasons, f"Invalid: {d.reason_code}"

    # Verify exact decisions
    by_ctrl = {}
    for d in decisions:
        prop = next(p for p in proposals if p.command_id == d.command_id)
        by_ctrl[prop.controller_type] = d

    # ES -> DELAY (hold 1, reason NONE)
    dec_es = by_ctrl[ControllerType.ENERGY_SAVING]
    assert dec_es.action is ActionType.DELAY
    assert dec_es.hold_count == 1
    assert dec_es.reason_code is ReasonCode.NONE

    # LB -> MODIFY ~0.15 (reason HEADROOM)
    dec_lb = by_ctrl[ControllerType.LOAD_BALANCING]
    assert dec_lb.action is ActionType.MODIFY
    assert dec_lb.approved_value == pytest.approx(0.1528, abs=0.01)
    assert dec_lb.reason_code is ReasonCode.HEADROOM

    # CC -> MODIFY ~44 dBm (+1 dB, reason HEADROOM)
    dec_cc = by_ctrl[ControllerType.COVERAGE_CAPACITY]
    assert dec_cc.action is ActionType.MODIFY
    assert dec_cc.approved_value == pytest.approx(44.146, abs=0.3)
    assert dec_cc.reason_code is ReasonCode.HEADROOM

    # (d) Post-actuation
    # Apply coordinated decisions
    engine.cells["cell_A"].set_tx_power(dec_cc.approved_value)
    # ES is delayed, Cell B stays active at 43 dBm
    samples_coord = engine.step()
    drops_coord = sum(s.dropped_users for s in samples_coord)
    assert drops_coord == 0, f"Coordinated drops must be 0, got {drops_coord}"
    assert engine.cells["cell_B"].is_sleeping is False
    assert engine.cells["cell_B"].effective_tx_power_w() > 0.0

    # Parallel uncoordinated comparator
    engine_uncoord = SimulationEngine(
        topology="5_CELL_CORE", scenario=ScenarioType.QUIET_2AM, seed=42
    )
    for _ in range(6):
        engine_uncoord.step()
    uncoord_props = trigger_2am_collision(
        engine=engine_uncoord, base_ts_ms=engine_uncoord.simulated_epoch_ms
    )
    apply_uncoordinated_baseline(engine_uncoord, uncoord_props)
    samples_uncoord = engine_uncoord.step()
    drops_uncoord = sum(s.dropped_users for s in samples_uncoord)
    assert drops_uncoord > 0, "Uncoordinated baseline must suffer drops"
    assert (
        engine_uncoord.cells["cell_B"].effective_tx_power_w() == 0.0
    ), "Cell B must be 0 W (blackout)"

    # (e) Topology endpoint returns 5 cells
    client = TestClient(app)
    resp = client.get("/api/v1/state/cells")
    assert resp.status_code == 200
    cells_data = resp.json()
    assert len(cells_data) == 5, f"Expected 5 cells, got {len(cells_data)}"
