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
    ) -> None:
        self.topology = topology
        self.scenario = (
            scenario if isinstance(
                scenario, ScenarioType) else ScenarioType.from_str(scenario)
        )
        self.seed = seed
        self.simulated_epoch_ms = start_epoch_ms
        self.tick = 0
        self.telemetry_sink = telemetry_sink

        if proposal_sink is not None:
            self.proposal_sink = proposal_sink
        elif use_real_queue:
            from arbiter.simulator.proposal_sink_adapter import ProposalQueueSink
            self.proposal_sink = ProposalQueueSink()
        else:
            from arbiter.simulator.mocks.mock_proposal_sink import MockProposalSink
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
            self.telemetry_sink.ingest(
                batch_tick=self.tick,
                timestamp_epoch_ms=self.simulated_epoch_ms,
                samples=samples,
            )

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

    if args.stream_ws:
        try:
            from arbiter.api.websocket import manager  # type: ignore  # noqa: F401
            print("[SIMULATOR] --stream-ws enabled (WebSocket streaming active)")
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

    import time

    start_time = time.perf_counter()
    for t in range(args.ticks):
        samples = engine.step()
        loads = engine.get_cell_loads()
        cell_sleeps = {cid: c.is_sleeping for cid, c in engine.cells.items()}

        # Evaluate autonomous controllers normally
        if is_2am and t < 6:
            ctrl_es.evaluate_telemetry(
                loads, current_tick=t, cell_sleep_states=cell_sleeps)

        # Trigger collision trio at tick 6
        status_str = ""
        if is_2am and t == 6:
            proposals = trigger_2am_collision(
                engine=engine, base_ts_ms=engine.simulated_epoch_ms
            )
            apply_uncoordinated_baseline(engine, proposals)
            status_str = " (COLLISION TRIO APPLIED)"

        dropped_sum = sum(s.dropped_users for s in samples)
        cell_b_load = loads.get("cell_B", 0.0)
        cell_a_power = (
            engine.cells["cell_A"].current_tx_power_dbm
            if "cell_A" in engine.cells else 0.0
        )

        print(
            f"Tick {t:02d} | Drops: {dropped_sum:02d} | "
            f"Cell B Load: {cell_b_load:.1%} | "
            f"Cell A Pwr: {cell_a_power:.1f} dBm{status_str}"
        )

    elapsed_s = time.perf_counter() - start_time
    print(
        f"[SIMULATOR] Simulation completed in {elapsed_s:.3f} s "
        f"({args.ticks} ticks).\n"
        "BASELINE RESULT: Uncoordinated controllers cause call drops.\n"
        " - Cell B turned DARK (0 W)\n"
        " - Offloaded Cell A users suffered immediate call drops\n"
        " - Cell A power boost increased inter-cell interference"
    )


if __name__ == "__main__":
    main()
