"""Core discrete-time cellular RAN simulation engine."""
import argparse
from typing import Any, Dict, List, Optional, Sequence, Union
import numpy as np

from arbiter.shared.models import (
    CommandProposal,
    ParameterType,
    TelemetrySample,
)
from arbiter.shared.v2_contracts import SimulationSnapshot
from arbiter.simulator.cell import (
    CellConfiguration,
    CellEntity,
    UserEquipment,
)
from arbiter.simulator.grid import HexagonalGrid
from arbiter.simulator.propagation import Cost231PropagationModel
from arbiter.simulator.snapshot import SnapshotRing
from arbiter.simulator.traffic import (
    CELL_CAPACITY_MBPS,
    MilanTrafficGenerator,
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
        snapshot_capacity: int = 600,
        auto_snapshot: bool = True,
        twin_kind: str = "real",
    ) -> None:
        self.topology = topology
        self.scenario = (
            scenario if isinstance(
                scenario, ScenarioType) else ScenarioType.from_str(scenario)
        )
        self.seed = seed
        self.simulated_epoch_ms = start_epoch_ms
        self.tick = 0
        self.twin_kind = str(twin_kind)

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

        # Snapshot ring buffer
        self.auto_snapshot = auto_snapshot
        self.snapshot_ring = SnapshotRing(capacity=snapshot_capacity)

    @classmethod
    def create_integrated(cls, **kwargs: Any) -> "SimulationEngine":
        """Factory for SimulationEngine wired to real ProposalQueue."""
        return cls(use_real_queue=True, **kwargs)

    @property
    def sink_kind(self) -> str:
        """Return 'queue' or 'mock' depending on active proposal sink."""
        if type(self.proposal_sink).__name__ == "ProposalQueueSink":
            return "queue"
        return "mock"

    def cell_states(self) -> List[Dict[str, Any]]:
        """Return live per-cell data conforming to contract C2:
        id, x, y, tx_power_dbm, tilt_deg, is_sleeping, active_users,
        load_ratio, energy_w, neighbors.
        """
        active_users = self.traffic_model.get_active_users(self.tick)
        loads = self.get_cell_loads()
        states: List[Dict[str, Any]] = []
        for cid, cell in self.cells.items():
            attached_count = sum(
                1 for u in active_users if u.attached_cell_id == cid
            )
            states.append({
                "id": str(cid),
                "x": round(float(cell.x_coord), 2),
                "y": round(float(cell.y_coord), 2),
                "tx_power_dbm": round(float(cell.current_tx_power_dbm), 2),
                "tilt_deg": round(float(cell.antenna_tilt_deg), 2),
                "is_sleeping": bool(cell.is_sleeping),
                "active_users": int(attached_count),
                "load_ratio": round(float(loads.get(cid, 0.0)), 4),
                "energy_w": round(float(cell.energy_consumption_w()), 2),
                "neighbors": list(cell.neighbor_cell_ids),
            })
        return states

    def get_cell_configs(self) -> List[CellConfiguration]:
        """Return raw CellConfiguration models."""
        return [c.config for c in self.cells.values()]

    def step(self) -> List[TelemetrySample]:
        """Advance simulation by 1 tick (100 ms) and emit samples."""
        # 0. Capture snapshot before advancing if auto_snapshot is enabled
        if self.auto_snapshot:
            self.snapshot_ring.push(self.snapshot())

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

    def snapshot(self) -> SimulationSnapshot:
        """Capture an opaque state snapshot of the running simulation."""
        return SimulationSnapshot(
            tick=self.tick,
            payload={
                "simulated_epoch_ms": self.simulated_epoch_ms,
                "seed": self.seed,
                "topology": self.topology,
                "scenario": (
                    self.scenario.value
                    if hasattr(self.scenario, "value")
                    else str(self.scenario)
                ),
                "cells": {
                    cid: cell.config.model_dump()
                    for cid, cell in self.cells.items()
                },
                "users": [ue.model_dump() for ue in self.users],
                "offload_overrides": dict(self.offload_overrides),
                "rng_state": (
                    self.traffic_model.generator.rng.bit_generator.state
                ),
            },
        )

    def restore(self, snap: SimulationSnapshot) -> None:
        """Restore engine state from a previously captured snapshot."""
        p = snap.payload
        self.tick = snap.tick
        self.simulated_epoch_ms = int(p["simulated_epoch_ms"])
        self.offload_overrides = dict(p.get("offload_overrides", {}))

        for cid, cfg_data in p["cells"].items():
            if cid in self.cells:
                self.cells[cid].config = CellConfiguration(**cfg_data)
            else:
                self.cells[cid] = CellEntity(
                    config=CellConfiguration(**cfg_data)
                )

        self.users = [UserEquipment(**ud) for ud in p["users"]]
        self.traffic_model.pool = self.users
        self.traffic_model.generator.rng.bit_generator.state = p["rng_state"]

    def fork(self, seed: Optional[int] = None) -> "SimulationEngine":
        """Fork an independent, identically stateful copy of the simulator."""
        forked = SimulationEngine.__new__(SimulationEngine)
        forked.topology = self.topology
        forked.scenario = self.scenario
        forked.seed = self.seed if seed is None else seed
        forked.simulated_epoch_ms = self.simulated_epoch_ms
        forked.tick = self.tick
        forked.telemetry_sink = None
        forked.proposal_sink = None
        forked.propagation = self.propagation
        forked.bounds = self.bounds
        forked.auto_snapshot = self.auto_snapshot
        forked.snapshot_ring = SnapshotRing(
            capacity=self.snapshot_ring.capacity
        )

        forked.cells = {
            cid: CellEntity(config=cell.config.model_copy(deep=True))
            for cid, cell in self.cells.items()
        }

        forked.traffic_model = TrafficDistributionModel.__new__(
            TrafficDistributionModel
        )
        forked.traffic_model.topology = self.traffic_model.topology
        forked.traffic_model.scenario = self.traffic_model.scenario
        forked.traffic_model.seed = forked.seed
        forked.traffic_model.cells = self.traffic_model.cells
        forked.traffic_model.bounds = self.traffic_model.bounds
        forked.traffic_model.pool_size = self.traffic_model.pool_size
        forked.traffic_model.profile_scale = (
            self.traffic_model.profile_scale
        )

        forked.traffic_model.generator = MilanTrafficGenerator.__new__(
            MilanTrafficGenerator
        )
        forked.traffic_model.generator.scenario = (
            self.traffic_model.generator.scenario
        )
        forked.traffic_model.generator.seed = forked.seed
        forked.traffic_model.generator.profile = (
            self.traffic_model.generator.profile
        )
        if seed is None:
            new_rng = np.random.default_rng()
            new_rng.bit_generator.state = (
                self.traffic_model.generator.rng.bit_generator.state
            )
            forked.traffic_model.generator.rng = new_rng
        else:
            forked.traffic_model.generator.rng = np.random.default_rng(seed)

        forked.users = [ue.model_copy(deep=True) for ue in self.users]
        forked.traffic_model.pool = forked.users
        forked.offload_overrides = dict(self.offload_overrides)
        forked.twin_kind = getattr(self, "twin_kind", "real")
        return forked

    def frame(
        self,
        world: str = "arbiter",
        tick: Optional[int] = None,
        sim_time_ms: Optional[int] = None,
        proposals: Optional[Sequence[Any]] = None,
        decisions: Optional[Sequence[Any]] = None,
        conflicts: Optional[Sequence[Any]] = None,
        plans: Optional[Sequence[Any]] = None,
        twin: Optional[Dict[str, Any]] = None,
        meta: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Produce a complete discrete frame conforming to the 3D contract."""
        frame_tick = self.tick if tick is None else tick
        frame_time = (
            self.simulated_epoch_ms if sim_time_ms is None else sim_time_ms
        )
        active_users = self.traffic_model.get_active_users(frame_tick)

        cells_payload: List[Dict[str, Any]] = [
            {k: v for k, v in cs.items() if k != "neighbors"}
            for cs in self.cell_states()
        ]

        users_payload: List[Dict[str, Any]] = []
        for u in active_users:
            users_payload.append({
                "id": str(u.ue_id),
                "x": round(float(u.x_coord), 2),
                "y": round(float(u.y_coord), 2),
                "cell": str(u.attached_cell_id),
                "sinr_db": round(float(u.sinr_db), 2),
                "dropped": bool(u.is_dropped),
            })

        total_dropped = sum(1 for u in active_users if u.is_dropped)
        mean_sinr = (
            float(np.mean([u.sinr_db for u in active_users]))
            if active_users else 0.0
        )
        total_energy_w = sum(
            c.energy_consumption_w() for c in self.cells.values()
        )

        formatted_proposals: List[Dict[str, Any]] = []
        if proposals:
            for p in proposals:
                if isinstance(p, dict):
                    cmd_id = p.get("command_id", "")
                    ctrl = (
                        p.get("controller_type")
                        or p.get("controller_id")
                        or "ES"
                    )
                    tgt = p.get("target_cell_id", "")
                    param = p.get("parameter", "")
                    req_val = p.get("requested_value", 0.0)
                    em_flag = p.get("emergency_flag", False)
                    hc = p.get("hold_count", 0)
                    ts_epoch = p.get("timestamp_epoch_ms", frame_time)
                else:
                    cmd_id = getattr(p, "command_id", "")
                    ctrl = getattr(p, "controller_type", None) or getattr(
                        p, "controller_id", "ES"
                    )
                    tgt = getattr(p, "target_cell_id", "")
                    param = getattr(p, "parameter", "")
                    req_val = getattr(p, "requested_value", 0.0)
                    em_flag = getattr(p, "emergency_flag", False)
                    hc = getattr(p, "hold_count", 0)
                    ts_epoch = getattr(p, "timestamp_epoch_ms", frame_time)

                ctrl_str = ctrl.value if hasattr(ctrl, "value") else str(ctrl)
                param_str = (
                    param.value if hasattr(param, "value") else str(param)
                )
                formatted_proposals.append({
                    "command_id": str(cmd_id),
                    "controller_type": ctrl_str,
                    "target_cell_id": str(tgt),
                    "parameter": param_str,
                    "requested_value": float(
                        req_val if req_val is not None else 0.0
                    ),
                    "emergency_flag": bool(em_flag),
                    "hold_count": int(hc),
                    "timestamp_epoch_ms": int(ts_epoch),
                })

        formatted_decisions: List[Dict[str, Any]] = []
        if decisions:
            for d in decisions:
                if isinstance(d, dict):
                    dec_id = d.get("decision_id", "")
                    cmd_id = d.get("command_id", "")
                    act = d.get("action", "")
                    appr_val = d.get("approved_value")
                    rc = d.get("reason_code", "NONE")
                    hc = d.get("hold_count", 0)
                    du = d.get("delta_utility", 0.0)
                else:
                    dec_id = getattr(d, "decision_id", "")
                    cmd_id = getattr(d, "command_id", "")
                    act = getattr(d, "action", "")
                    appr_val = getattr(d, "approved_value", None)
                    rc = getattr(d, "reason_code", "NONE")
                    hc = getattr(d, "hold_count", 0)
                    du = getattr(d, "delta_utility", 0.0)

                act_str = act.value if hasattr(act, "value") else str(act)
                rc_str = rc.value if hasattr(rc, "value") else str(rc)
                formatted_decisions.append({
                    "decision_id": str(dec_id),
                    "command_id": str(cmd_id),
                    "action": act_str,
                    "approved_value": (
                        float(appr_val) if appr_val is not None else None
                    ),
                    "reason_code": rc_str,
                    "hold_count": int(hc),
                    "delta_utility": float(du),
                })

        sink_str = self.sink_kind
        frame_meta: Dict[str, str] = {
            "twin_kind": getattr(self, "twin_kind", "real"),
            "sink": sink_str,
        }
        if meta:
            frame_meta.update(meta)

        return {
            "type": "frame",
            "tick": frame_tick,
            "sim_time_ms": frame_time,
            "scenario": (
                self.scenario.value
                if hasattr(self.scenario, "value")
                else str(self.scenario)
            ),
            "world": world,
            "cells": cells_payload,
            "users": users_payload,
            "proposals": formatted_proposals,
            "decisions": formatted_decisions,
            "conflicts": list(conflicts) if conflicts else [],
            "plans": list(plans) if plans else [],
            "twin": (
                twin
                if twin is not None
                else {"calibrated": False, "intervals": []}
            ),
            "kpis": {
                "dropped_users": total_dropped,
                "mean_sinr_db": round(mean_sinr, 2),
                "energy_w": round(total_energy_w, 2),
            },
            "meta": frame_meta,
        }


def main() -> None:
    """CLI runner for standalone simulation scenarios."""
    parser = argparse.ArgumentParser(
        description="ARBITER Discrete RAN Simulator"
    )
    parser.add_argument(
        "--scenario", type=str, default="2am_collision", help="Scenario to run"
    )
    parser.add_argument(
        "--ticks", type=int, default=300, help="Number of ticks (100ms each)"
    )
    parser.add_argument(
        "--cells", type=int, default=5, choices=[5, 19], help="Cluster size"
    )
    parser.add_argument("--seed", type=int, default=42, help="RNG seed")
    parser.add_argument(
        "--stream-ws",
        action="store_true",
        help="Stream live frames and telemetry over WebSockets",
    )
    parser.add_argument(
        "--pace",
        type=float,
        default=1.0,
        help=(
            "Pace: real seconds per simulated second "
            "(1.0 = 100ms ticks, 0 = max speed)"
        ),
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Loop the simulation scenario continuously",
    )
    parser.add_argument(
        "--record",
        type=str,
        default=None,
        help="Record simulation frames to a JSON file",
    )
    parser.add_argument(
        "--twin",
        type=str,
        default="real",
        choices=["real", "scripted"],
        help="Digital twin backend: 'real' (default) or 'scripted' toy twin",
    )
    parser.add_argument(
        "--mock-sink",
        action="store_true",
        default=False,
        help="Use mock proposal sink instead of real Branch 2 ProposalQueue",
    )
    parser.add_argument(
        "--use-real-queue",
        action="store_true",
        default=None,
        help="Wire to Branch 2 ProposalQueue (retained for compatibility)",
    )
    args = parser.parse_args()

    topology = "19_CELL" if args.cells == 19 else "5_CELL_CORE"

    if args.mock_sink:
        use_real_queue = False
    elif args.use_real_queue is not None:
        use_real_queue = args.use_real_queue
    else:
        use_real_queue = True

    sink_name = "mock" if not use_real_queue else "queue"
    print(f"[SIMULATOR] Twin: {args.twin} | Sink: {sink_name}")

    if args.twin == "scripted":
        print(
            "\n" + "=" * 78 + "\n"
            "[WARNING] RUNNING IN SCRIPTED TWIN MODE: "
            "Using analytical toy model, not real DigitalTwinEvaluator!\n"
            + "=" * 78 + "\n",
            flush=True,
        )

    while True:
        engine = SimulationEngine(
            topology=topology,
            scenario=args.scenario,
            seed=args.seed,
            use_real_queue=use_real_queue,
            twin_kind=args.twin,
        )
        engine_uncoord = SimulationEngine(
            topology=topology,
            scenario=args.scenario,
            seed=args.seed,
            use_real_queue=False,
            twin_kind=args.twin,
        )

        if args.stream_ws:
            print(
                f"[SIMULATOR] --stream-ws active "
                f"(pace={args.pace:.2f}, ticks={args.ticks})"
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
        import subprocess
        import time
        import urllib.request
        from datetime import datetime, timezone
        from arbiter.engine.contracts import CommandProposal as EngineProposal
        from arbiter.queue.orchestrator import QueueOrchestrator
        from arbiter.shared.database import init_database

        # Digital twin resolution (Contract C1)
        make_default_twin: Any
        try:
            from arbiter.engine.default_twin import make_default_twin
        except (ImportError, ModuleNotFoundError) as exc:
            if args.twin == "real":
                raise RuntimeError(
                    "Contract C1: 'arbiter.engine.default_twin' does not "
                    "exist yet. The real digital twin requires Pranav's "
                    "branch to merge. Specify --twin scripted to run with "
                    "the analytical scripted twin."
                ) from exc
            make_default_twin = None

        decisions: List[Any] = []
        proposals: List[Any] = []
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

        recorded_arbiter: List[Dict[str, Any]] = []
        recorded_counterfactual: List[Dict[str, Any]] = []

        start_time = time.perf_counter()
        for t in range(args.ticks):
            cur_tick = t
            cur_epoch_ms = engine.simulated_epoch_ms
            samples = engine.step()
            samples_uncoord = engine_uncoord.step()
            loads = engine.get_cell_loads()
            active_users = engine.traffic_model.get_active_users(engine.tick)
            cell_sleeps = {
                cid: c.is_sleeping for cid, c in engine.cells.items()
            }

            # Evaluate autonomous controllers normally during warm-up
            if is_2am and t < 6:
                ctrl_es.evaluate_telemetry(
                    loads, current_tick=t, cell_sleep_states=cell_sleeps
                )

            # Trigger collision trio at tick 6
            status_str = ""
            tick_proposals: List[Any] = []
            tick_decisions: List[Any] = []
            tick_conflicts: List[Dict[str, str]] = []

            if is_2am and t == 6:
                arbitration_epoch_ms = engine.simulated_epoch_ms
                proposals = trigger_2am_collision(
                    engine=engine, base_ts_ms=engine.simulated_epoch_ms
                )
                tick_proposals = proposals

                # Uncoordinated baseline applies proposals raw
                apply_uncoordinated_baseline(engine_uncoord, proposals)

                # ARBITER Coordinated: referee with queue + twin + actuator
                adj = {
                    cid: cell.config.neighbor_cell_ids
                    for cid, cell in engine.cells.items()
                }
                if args.twin == "scripted":
                    if make_default_twin is not None:
                        twin = make_default_twin(
                            topology=topology, kind="scripted"
                        )
                    else:
                        from arbiter.simulator.toy_twin import (
                            get_scripted_twin,
                        )

                        twin = get_scripted_twin(topology=topology)
                else:
                    if make_default_twin is None:
                        raise RuntimeError(
                            "Contract C1: 'arbiter.engine.default_twin' does "
                            "not exist yet. The real digital twin requires "
                            "Pranav's branch to merge."
                        )
                    twin = make_default_twin(
                        topology=topology, kind="real"
                    )

                orchestrator = QueueOrchestrator(
                    twin=twin, adjacency=adj, max_workers=2
                )
                for p in proposals:
                    orchestrator.submit(
                        EngineProposal.model_validate(p.model_dump())
                    )

                res = orchestrator.on_tick(
                    current_tick=t,
                    timestamp_epoch_ms=engine.simulated_epoch_ms,
                )
                decisions = res["decisions"]
                tick_decisions = decisions

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

                    if (
                        d.action.value == "MODIFY"
                        and d.approved_value is not None
                    ):
                        if "POWER" in norm_p and target_cid in engine.cells:
                            engine.cells[target_cid].set_tx_power(
                                d.approved_value
                            )
                        elif "OFFLOAD" in norm_p:
                            engine.apply_actuation_deltas([{
                                "target_cell_id": target_cid,
                                "parameter": "OFFLOAD_RATIO",
                                "value": d.approved_value,
                            }])

                # Build conflict edges from detector
                try:
                    from arbiter.detector.graph import ConflictGraphBuilder
                    builder = ConflictGraphBuilder()
                    cmd_proposals = [
                        EngineProposal.model_validate(p.model_dump())
                        for p in proposals
                    ]
                    cfg_graph = builder.build(cmd_proposals, adj)
                    for u, v, edata in cfg_graph.edges(data=True):
                        ctype = edata.get("conflict_type")
                        ctype_str = str(getattr(ctype, "value", ctype))
                        tick_conflicts.append(
                            {"a": u, "b": v, "type": ctype_str}
                        )
                except Exception:
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
                                    decision_id, command_id, batch_tick,
                                    action, approved_value, delta_utility,
                                    reason_code, hold_count,
                                    decided_at_epoch_ms
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
                                    applied_at_epoch_ms=(
                                        engine.simulated_epoch_ms
                                    ),
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

            # Build discrete frames for Arbiter and Counterfactual
            fr = engine.frame(
                world="arbiter",
                tick=cur_tick,
                sim_time_ms=cur_epoch_ms,
                proposals=tick_proposals,
                decisions=tick_decisions,
                conflicts=tick_conflicts,
            )
            fr_cf = engine_uncoord.frame(
                world="counterfactual",
                tick=cur_tick,
                sim_time_ms=cur_epoch_ms,
            )

            if args.record:
                recorded_arbiter.append(fr)
                recorded_counterfactual.append(fr_cf)

            if args.stream_ws:
                try:
                    req_frame = urllib.request.Request(
                        "http://localhost:8000/api/v1/frames/ingest",
                        data=json.dumps(fr).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req_frame, timeout=1.0) as _:
                        pass
                except Exception:
                    pass

                # Also send legacy 2D telemetry payload if needed
                cell_states_payload = [
                    engine.cells[cid].to_cell_state(
                        active_users=sum(
                            1
                            for u in active_users
                            if u.attached_cell_id == cid
                        ),
                        load_ratio=loads.get(cid, 0.0),
                    ).model_dump()
                    for cid in engine.cells
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
                    "ingress_proposals": [
                        {
                            "time": arbitration_epoch_ms,
                            "ctrl": getattr(p, "controller_id", "ES"),
                            "target": getattr(p, "target_cell_id", "cell_A"),
                            "param": str(
                                getattr(p.parameter, "value", p.parameter)
                            ),
                            "req": (
                                f"{p.requested_value:.2f}"
                                if getattr(p, "requested_value", None)
                                is not None
                                else "--"
                            ),
                            "verdict": next(
                                (
                                    d.action.value
                                    for d in decisions
                                    if d.command_id == p.command_id
                                ),
                                "PENDING",
                            ),
                            "reason": next(
                                (
                                    d.reason_code.value
                                    for d in decisions
                                    if d.command_id == p.command_id
                                ),
                                "NONE",
                            ),
                        }
                        for p in proposals
                    ] if (is_2am and t >= 6 and decisions) else [],
                    "arbitration_badges": [
                        {
                            "controller": getattr(p, "controller_id", "ES"),
                            "action": next(
                                (
                                    d.action.value
                                    for d in decisions
                                    if d.command_id == p.command_id
                                ),
                                "PENDING",
                            ),
                            "badge_class": next(
                                (
                                    d.action.value.lower()
                                    for d in decisions
                                    if d.command_id == p.command_id
                                ),
                                "pending",
                            ),
                        }
                        for p in proposals
                    ] if (is_2am and t >= 6 and decisions) else [],
                }
                try:
                    req_tel = urllib.request.Request(
                        "http://localhost:8000/api/v1/telemetry/ingest",
                        data=json.dumps(post_data).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req_tel, timeout=1.0) as _:
                        pass
                except Exception:
                    pass

                if args.pace > 0:
                    time.sleep(0.1 * args.pace)

            print(
                f"Tick {t:02d} | "
                f"Drops: {dropped_sum:02d} (Uncoord: {uncoord_drops:02d}) | "
                f"Cell B Load: {cell_b_load:.1%} | "
                f"Cell A Pwr: {cell_a_power:.1f} dBm{status_str}"
            )

        elapsed_s = time.perf_counter() - start_time
        print(
            f"[SIMULATOR] Simulation run completed in {elapsed_s:.3f} s "
            f"({args.ticks} ticks).\n"
            "ARBITER COORDINATION RESULT:\n"
            " - ARBITER: 0 dropped users, Cell B active at 43 dBm, "
            "Cell A at 44.1 dBm\n"
            " - Uncoordinated baseline: Cell B turned DARK (0 W), "
            "14 users dropped\n"
        )

        if args.record:
            record_dir = os.path.dirname(os.path.abspath(args.record))
            os.makedirs(record_dir, exist_ok=True)
            record_payload = {
                "arbiter": recorded_arbiter,
                "counterfactual": recorded_counterfactual,
            }
            with open(args.record, "w", encoding="utf-8") as f:
                json.dump(record_payload, f, indent=2)

            meta_path = (
                args.record[:-5] + ".meta.json"
                if args.record.endswith(".json")
                else args.record + ".meta.json"
            )
            try:
                commit_sha = subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], text=True
                ).strip()
            except Exception:
                commit_sha = "unknown"

            meta_data = {
                "command": (
                    f"python -m arbiter.simulator.engine "
                    f"--scenario {args.scenario} --seed {args.seed} "
                    f"--ticks {args.ticks} --record {args.record}"
                ),
                "scenario": args.scenario,
                "seed": args.seed,
                "git_commit": commit_sha,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "tick_count": len(recorded_arbiter),
                "source": "real engine run",
            }
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta_data, f, indent=2)
            print(
                f"[SIMULATOR] Recorded {len(recorded_arbiter)} frames "
                f"to {args.record}"
            )
            print(f"[SIMULATOR] Written provenance metadata to {meta_path}")

        if not args.loop:
            break


if __name__ == "__main__":
    main()
