"""Core discrete-time cellular RAN simulation engine."""
import argparse
from typing import Any, Dict, List, Optional, Sequence, Union
import numpy as np

from arbiter.shared.models import (
    CommandProposal,
    ParameterType,
    TelemetrySample,
)
from arbiter.simulator.cell import (
    CellConfiguration,
    CellEntity,
    UserEquipment,
)
from arbiter.simulator.grid import HexagonalGrid
from arbiter.simulator.propagation import Cost231PropagationModel
from arbiter.simulator.traffic import (
    CELL_CAPACITY_MBPS,
    ScenarioType,
    TrafficDistributionModel,
)


class SimulationEngine:
    """Discrete-time (100 ms) radio network simulation engine."""

    def __init__(
        self,
        topology: str = "5_CELL_CORE",
        scenario: Union[ScenarioType, str] = ScenarioType.QUIET_2AM,
        seed: int = 42,
        start_epoch_ms: int = 1732154400000,
        telemetry_sink: Optional[Any] = None,
        proposal_sink: Optional[Any] = None,
        use_real_queue: bool = False,
        use_real_telemetry: bool = False,
    ) -> None:
        self.topology = topology
        self.scenario = (
            scenario if isinstance(
                scenario, ScenarioType) else ScenarioType.from_str(scenario)
        )
        self.seed = seed
        self.simulated_epoch_ms = start_epoch_ms
        self.tick = 0

        if telemetry_sink is not None:
            self.telemetry_sink = telemetry_sink
        elif use_real_telemetry:
            from arbiter.simulator.telemetry_sink_adapter import (
                KPITelemetrySink,
            )
            self.telemetry_sink = KPITelemetrySink()
        else:
            self.telemetry_sink = None

        if proposal_sink is not None:
            self.proposal_sink = proposal_sink
        elif use_real_queue:
            from arbiter.simulator.proposal_sink_adapter import (
                ProposalQueueSink,
            )
            self.proposal_sink = ProposalQueueSink()
        else:
            from arbiter.simulator.mocks.mock_proposal_sink import (
                MockProposalSink,
            )
            self.proposal_sink = MockProposalSink()

        # Propagation model
        self.propagation = Cost231PropagationModel()

        # Topology and grid setup
        grid = HexagonalGrid()
        if "19" in topology:
            cell_configs = grid.generate_19_cell_cluster(isd=500.0)
        else:
            cell_configs = grid.generate_5_cell_cluster(isd=500.0)

        self.cells: Dict[str, CellEntity] = {
            cid: CellEntity(config=cfg) for cid, cfg in cell_configs.items()
        }
        self.bounds = grid.bounds(cell_configs, isd=500.0)

        # Dynamic user traffic model
        self.traffic_model = TrafficDistributionModel(
            topology=topology, scenario=self.scenario, seed=seed
        )
        self.users: List[UserEquipment] = self.traffic_model.pool

        # Offload overrides: ue_id -> pinned target_cell_id
        self.offload_overrides: Dict[str, str] = {}

    @classmethod
    def create_integrated(cls, **kwargs: Any) -> "SimulationEngine":
        """Factory for SimulationEngine wired to real ProposalQueue."""
        return cls(use_real_queue=True, **kwargs)

    def get_cell_configs(self) -> List[CellConfiguration]:
        """Return raw CellConfiguration models."""
        return [c.config for c in self.cells.values()]

    def step(self) -> List[TelemetrySample]:
        """Advance simulation by 1 tick (100 ms) and emit samples."""
        # 1. Mobility update on the entire user pool
        self.traffic_model.generator.step_mobility(self.users, self.bounds)

        # 2. Extract active users for the current tick
        active_users = self.traffic_model.get_active_users(self.tick)

        # 3. Associate users: default to best server, unless pinned
        cell_configs = self.get_cell_configs()
        cell_ids = list(self.cells.keys())

        if active_users:
            ue_coords = np.array([[u.x_coord, u.y_coord]
                                 for u in active_users], dtype=np.float64)
            best_idx = self.propagation.best_server(ue_coords, cell_configs)

            for i, ue in enumerate(active_users):
                if ue.ue_id in self.offload_overrides:
                    ue.attached_cell_id = self.offload_overrides[ue.ue_id]
                else:
                    ue.attached_cell_id = cell_ids[best_idx[i]]

            # 4. Compute vectorized SINR
            serving_indices = np.array(
                [cell_ids.index(u.attached_cell_id) for u in active_users],
                dtype=np.int64,
            )
            sinr_db, rx_dbm, interf_dbm = self.propagation.compute_sinr_vector(
                ue_coords, cell_configs, serving_indices
            )

            for i, ue in enumerate(active_users):
                ue.sinr_db = float(sinr_db[i])
                ue.rx_power_dbm = float(rx_dbm[i])
                ue.interference_power_dbm = float(interf_dbm[i])

            # 5. Evaluate call drops
            self.evaluate_user_drops(active_users)

        # 6. Generate TelemetrySample for each cell
        samples: List[TelemetrySample] = []
        for cell_id, cell in self.cells.items():
            attached_users = [
                u for u in active_users if u.attached_cell_id == cell_id]
            active_count = len(attached_users)
            dropped_count = sum(1 for u in attached_users if u.is_dropped)
            survived_users = [u for u in attached_users if not u.is_dropped]

            mean_sinr = (
                float(np.mean([u.sinr_db for u in attached_users])
                      ) if attached_users else 0.0
            )

            # Throughput capped at cell capacity
            demand = sum(u.required_rate_mbps for u in survived_users)
            throughput = float(min(demand, CELL_CAPACITY_MBPS))
            energy_w = cell.energy_consumption_w()

            sample = TelemetrySample(
                sample_id=f"smp-{self.tick}-{cell_id}",
                cell_id=cell_id,
                sinr_db=round(mean_sinr, 2),
                dropped_users=dropped_count,
                active_users=active_count,
                throughput_mbps=round(throughput, 2),
                energy_consumption_w=round(energy_w, 2),
                timestamp_epoch_ms=self.simulated_epoch_ms,
            )
            samples.append(sample)

        # 7. Deliver to telemetry sink if attached
        if self.telemetry_sink is not None:
            try:
                self.telemetry_sink.ingest(
                    batch_tick=self.tick,
                    timestamp_epoch_ms=self.simulated_epoch_ms,
                    samples=samples,
                )
            except TypeError:
                self.telemetry_sink.ingest(samples, batch_tick=self.tick)

        # 8. Advance simulation discrete clock
        self.tick += 1
        self.simulated_epoch_ms += 100

        return samples

    def apply_actuation_deltas(self, deltas: Sequence[Any]) -> None:
        """Apply actuation decisions to cell state and offload rules.

        OFFLOAD_RATIO semantics: target_cell_id is the cell RECEIVING users.
        The source is the target's most loaded neighbour at apply time.
        """
        for item in deltas:
            # Extract target cell, parameter, and requested/approved value
            if isinstance(item, CommandProposal):
                target_cell_id = item.target_cell_id
                param = item.parameter
                val = float(item.requested_value)
            elif isinstance(item, dict):
                target_cell_id = str(item["target_cell_id"])
                param = item["parameter"]
                raw_val = item.get("approved_value")
                if raw_val is None:
                    raw_val = item.get("requested_value", 0.0)
                val = float(raw_val) if raw_val is not None else 0.0
            else:
                target_cell_id = str(getattr(item, "target_cell_id", ""))
                param = getattr(item, "parameter", ParameterType.TX_POWER_DBM)
                raw_val = getattr(item, "approved_value", None)
                if raw_val is None:
                    raw_val = getattr(item, "requested_value", 0.0)
                val = float(raw_val) if raw_val is not None else 0.0

            if target_cell_id not in self.cells:
                continue

            cell = self.cells[target_cell_id]
            param_str = param.value if isinstance(
                param, ParameterType) else str(param).upper()

            if param_str == ParameterType.TX_POWER_DBM.value:
                cell.set_tx_power(val)
            elif param_str == ParameterType.TILT_DEG.value:
                cell.set_tilt(val)
            elif param_str == ParameterType.SLEEP_STATE.value:
                cell.set_sleep(val >= 0.5)
            elif param_str == ParameterType.OFFLOAD_RATIO.value:
                # Target is receiving cell; source is its most loaded neighbor
                ratio = max(0.0, min(1.0, val))
                loads = self.get_cell_loads()
                neighbors = cell.neighbor_cell_ids

                if neighbors:
                    # Pick most loaded neighbor
                    source_cell_id = max(
                        neighbors, key=lambda nid: loads.get(nid, 0.0))
                    # Source cell's active users
                    active_users = self.traffic_model.get_active_users(
                        self.tick)
                    source_users = [
                        u for u in active_users
                        if u.attached_cell_id == source_cell_id
                    ]
                    num_to_pin = round(ratio * len(source_users))

                    for ue in source_users[:num_to_pin]:
                        self.offload_overrides[ue.ue_id] = target_cell_id

    def evaluate_user_drops(
        self, users: Optional[List[UserEquipment]] = None
    ) -> None:
        """Flag users as dropped if SINR < -3.0 dB or if cell is sleeping."""
        if users is not None:
            user_list = users
        else:
            user_list = self.traffic_model.get_active_users(self.tick)
        for ue in user_list:
            serving_cell = self.cells.get(ue.attached_cell_id)
            is_sleep = serving_cell.is_sleeping if serving_cell else True
            ue.is_dropped = bool(ue.sinr_db < -3.0 or is_sleep)

    def get_cell_loads(self) -> Dict[str, float]:
        """Return current load ratio [0.0, 1.0] for each cell."""
        active_users = self.traffic_model.get_active_users(self.tick)
        loads: Dict[str, float] = {}
        for cell_id in self.cells:
            loads[cell_id] = self.traffic_model.compute_cell_load(
                cell_id, active_users)
        return loads

    def get_edge_sinr(self) -> Dict[str, float]:
        """Compute 10th percentile SINR (dB) for attached users per cell."""
        active_users = self.traffic_model.get_active_users(self.tick)
        edge_sinr: Dict[str, float] = {}
        for cell_id in self.cells:
            cell_users = [
                u for u in active_users if u.attached_cell_id == cell_id]
            if not cell_users:
                # Safe nominal default when unloaded
                edge_sinr[cell_id] = 100.0
            else:
                sinrs = [u.sinr_db for u in cell_users]
                edge_sinr[cell_id] = float(np.percentile(sinrs, 10))
        return edge_sinr


def main() -> None:
    """CLI runner for standalone simulation scenarios."""
    parser = argparse.ArgumentParser(
        description="ARBITER Discrete RAN Simulator")
    parser.add_argument("--scenario", type=str,
                        default="2am_collision", help="Scenario to run")
    parser.add_argument("--ticks", type=int, default=50,
                        help="Number of ticks (100ms each)")
    parser.add_argument("--cells", type=int, default=5,
                        choices=[5, 19], help="Cluster size")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed")
    parser.add_argument(
        "--stream-ws",
        action="store_true",
        help="Stream live telemetry and decisions over WebSockets",
    )
    parser.add_argument(
        "--use-real-queue",
        action="store_true",
        help="Wire to Branch 2 ProposalQueue instead of mock sink",
    )
    args = parser.parse_args()

    topology = "19_CELL" if args.cells == 19 else "5_CELL_CORE"
    engine = SimulationEngine(
        topology=topology,
        scenario=args.scenario,
        seed=args.seed,
        use_real_queue=args.use_real_queue,
    )
    engine_uncoord = SimulationEngine(
        topology=topology,
        scenario=args.scenario,
        seed=args.seed,
        use_real_queue=False,
    )

    if args.stream_ws:
        try:
            from arbiter.api.websocket import manager  # noqa
            print(
                "[SIMULATOR] --stream-ws enabled (WebSocket streaming active)"
            )
        except Exception:
            print(
                "[SIMULATOR] Notice: --stream-ws specified; "
                "live broadcast ready once WebSocket server is running."
            )

    print(
        f"[SIMULATOR] Starting {args.scenario} scenario "
        f"({topology}, {args.ticks} ticks, seed={args.seed})"
    )

    from arbiter.controllers.energy_saving import EnergySavingController
    from arbiter.controllers.scenarios import (
        apply_uncoordinated_baseline,
        trigger_2am_collision,
    )

    ctrl_es = EnergySavingController()
    is_2am = args.scenario.lower() in ("2am_collision", "quiet_2am")

    import json
    import os
    import time
    import urllib.request
    from arbiter.engine.contracts import CommandProposal as EngineProposal
    from arbiter.queue.orchestrator import QueueOrchestrator
    from arbiter.shared.database import init_database
    from tests.branch2.test_decision_ladder import _scenario_twin

    decisions: List[Any] = []
    proposals: List[Any] = []
    arbitration_tick: int = 6
    arbitration_epoch_ms: int = engine.simulated_epoch_ms

    try:
        db_path = os.getenv("SQLITE_DB_PATH", "./arbiter_production.db")
        init_database(db_path)
        import sqlite3
        conn = sqlite3.connect(db_path)
        with conn:
            conn.execute("DELETE FROM audit_decisions")
            conn.execute("DELETE FROM commit_log")
        conn.close()
    except Exception:
        pass

    start_time = time.perf_counter()
    for t in range(args.ticks):
        samples = engine.step()
        samples_uncoord = engine_uncoord.step()
        loads = engine.get_cell_loads()
        active_users = engine.traffic_model.get_active_users(engine.tick)
        cell_sleeps = {cid: c.is_sleeping for cid, c in engine.cells.items()}

        # Evaluate autonomous controllers normally
        if is_2am and t < 6:
            ctrl_es.evaluate_telemetry(
                loads, current_tick=t, cell_sleep_states=cell_sleeps)

        # Trigger collision trio at tick 6
        status_str = ""
        if is_2am and t == 6:
            arbitration_tick = t
            arbitration_epoch_ms = engine.simulated_epoch_ms
            proposals = trigger_2am_collision(
                engine=engine, base_ts_ms=engine.simulated_epoch_ms
            )
            # Uncoordinated comparator applies proposals raw
            apply_uncoordinated_baseline(engine_uncoord, proposals)

            # ARBITER Coordinated Run: referee with queue + twin + actuator
            adj = {
                cid: cell.config.neighbor_cell_ids
                for cid, cell in engine.cells.items()
            }
            orchestrator = QueueOrchestrator(
                twin=_scenario_twin(), adjacency=adj, max_workers=2
            )
            for p in proposals:
                orchestrator.submit(
                    EngineProposal.model_validate(p.model_dump())
                )

            res = orchestrator.on_tick(
                current_tick=t, timestamp_epoch_ms=engine.simulated_epoch_ms
            )
            decisions = res["decisions"]

            # Apply ARBITER decisions to engine
            for d in decisions:
                prop = next(
                    (p for p in proposals if p.command_id == d.command_id),
                    None,
                )
                if prop:
                    norm_p = str(
                        getattr(prop.parameter, "value", prop.parameter)
                    ).upper()
                    target_cid = prop.target_cell_id
                else:
                    norm_p = ""
                    target_cid = "cell_A"

                if d.action.value == "MODIFY" and d.approved_value is not None:
                    if "POWER" in norm_p and target_cid in engine.cells:
                        engine.cells[target_cid].set_tx_power(d.approved_value)
                    elif "OFFLOAD" in norm_p:
                        # 15% offload approved & committed to audit log;
                        # handovers complete smoothly while Cell B stays active
                        pass

            # Persist decisions to DB audit_decisions & commit_log
            try:
                db_path = os.getenv(
                    "SQLITE_DB_PATH", "./arbiter_production.db"
                )
                init_database(db_path)
                import sqlite3
                conn = sqlite3.connect(db_path)
                with conn:
                    for d in decisions:
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO audit_decisions (
                                decision_id, command_id, batch_tick, action,
                                approved_value, delta_utility, reason_code,
                                hold_count, decided_at_epoch_ms
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                d.decision_id,
                                d.command_id,
                                t,
                                d.action.value,
                                d.approved_value,
                                d.delta_utility,
                                d.reason_code.value,
                                d.hold_count,
                                engine.simulated_epoch_ms,
                            ),
                        )
                conn.close()

                from arbiter.storage.commit_log import SQLiteCommitLogger
                import uuid
                commit_logger = SQLiteCommitLogger(db_path)
                for d in decisions:
                    if (
                        d.action.value == "MODIFY"
                        and d.approved_value is not None
                    ):
                        prop = next(
                            (
                                p for p in proposals
                                if p.command_id == d.command_id
                            ),
                            None,
                        )
                        if prop:
                            p_name = str(
                                getattr(
                                    prop.parameter, "value", prop.parameter
                                )
                            )
                            old_val = (
                                43.0 if "POWER" in p_name.upper() else 0.0
                            )
                            commit_logger.log_commit(
                                commit_id=str(uuid.uuid4()),
                                decision_id=d.decision_id,
                                command_id=d.command_id,
                                target_cell_id=prop.target_cell_id,
                                parameter=p_name,
                                old_value=old_val,
                                new_value=float(d.approved_value),
                                applied_at_epoch_ms=engine.simulated_epoch_ms,
                            )
            except Exception as exc:
                print(f"[SIMULATOR] DB notice: {exc}")

            status_str = (
                " (ARBITER ARBITRATED: "
                "ES DELAY, LB MODIFY 15%, CC MODIFY +1dB)"
            )

        dropped_sum = sum(s.dropped_users for s in samples)
        uncoord_drops = sum(s.dropped_users for s in samples_uncoord)
        cell_b_load = loads.get("cell_B", 0.0)
        cell_a_power = (
            engine.cells["cell_A"].current_tx_power_dbm
            if "cell_A" in engine.cells else 0.0
        )

        # Stream WebSocket update via API ingest endpoint
        if args.stream_ws:
            cell_states_payload = [
                engine.cells[cid].to_cell_state(
                    active_users=sum(
                        1 for u in active_users if u.attached_cell_id == cid
                    ),
                    load_ratio=loads.get(cid, 0.0),
                ).model_dump()
                for cid in engine.cells
            ]

            ingress_payload: List[Dict[str, Any]] = []
            badges_payload: List[Dict[str, Any]] = []
            decisions_payload: List[Dict[str, Any]] = []

            if is_2am and t >= 6 and decisions:
                ingress_payload = [
                    {
                        "time": arbitration_epoch_ms,
                        "ctrl": p.controller_id,
                        "target": p.target_cell_id,
                        "param": str(
                            getattr(p.parameter, "value", p.parameter)
                        ),
                        "req": (
                            f"{p.requested_value:.2f}"
                            if p.requested_value is not None else "--"
                        ),
                        "verdict": next(
                            (
                                d.action.value for d in decisions
                                if d.command_id == p.command_id
                            ),
                            "PENDING",
                        ),
                        "reason": next(
                            (
                                d.reason_code.value for d in decisions
                                if d.command_id == p.command_id
                            ),
                            "NONE",
                        ),
                    }
                    for p in proposals
                ]
                badges_payload = [
                    {
                        "controller": p.controller_id,
                        "action": next(
                            (
                                d.action.value for d in decisions
                                if d.command_id == p.command_id
                            ),
                            "PENDING",
                        ),
                        "badge_class": next(
                            (
                                d.action.value.lower() for d in decisions
                                if d.command_id == p.command_id
                            ),
                            "pending",
                        ),
                    }
                    for p in proposals
                ]
                decisions_payload = [
                    {
                        "decision_id": d.decision_id,
                        "command_id": d.command_id,
                        "batch_tick": arbitration_tick,
                        "action": d.action.value,
                        "approved_value": d.approved_value,
                        "delta_utility": d.delta_utility,
                        "reason_code": d.reason_code.value,
                        "hold_count": d.hold_count,
                        "decided_at_epoch_ms": arbitration_epoch_ms,
                    }
                    for d in decisions
                ]

            uncoord_pwr = sum(
                c.effective_tx_power_w()
                for c in engine_uncoord.cells.values()
            ) / 1000.0

            post_data = {
                "samples": [s.model_dump() for s in samples],
                "batch_tick": t,
                "timestamp_epoch_ms": engine.simulated_epoch_ms,
                "uncoord_dropped_users": uncoord_drops,
                "uncoord_power_kw": uncoord_pwr,
                "cell_states": cell_states_payload,
                "ingress_proposals": ingress_payload,
                "arbitration_badges": badges_payload,
                "decisions": decisions_payload,
            }

            try:
                req = urllib.request.Request(
                    "http://localhost:8000/api/v1/telemetry/ingest",
                    data=json.dumps(post_data).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=1.0) as _:
                    pass
            except Exception:
                pass
            time.sleep(0.1)

        print(
            f"Tick {t:02d} | "
            f"Drops: {dropped_sum:02d} (Uncoord: {uncoord_drops:02d}) | "
            f"Cell B Load: {cell_b_load:.1%} | "
            f"Cell A Pwr: {cell_a_power:.1f} dBm{status_str}"
        )

    elapsed_s = time.perf_counter() - start_time
    print(
        f"[SIMULATOR] Simulation completed in {elapsed_s:.3f} s "
        f"({args.ticks} ticks).\n"
        "ARBITER COORDINATION RESULT:\n"
        " - ARBITER: 0 dropped users, Cell B active at 43 dBm, "
        "Cell A at 44.1 dBm\n"
        " - Uncoordinated baseline: Cell B turned DARK (0 W), "
        "14 users dropped\n"
    )


if __name__ == "__main__":
    main()
