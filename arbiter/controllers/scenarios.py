"""Scenario triggers and evaluation for multi-controller collisions."""
from typing import Any, Dict, List, Optional
import numpy as np

from arbiter.controllers.coverage_capacity import CoverageCapacityController
from arbiter.controllers.energy_saving import EnergySavingController
from arbiter.controllers.load_balancing import LoadBalancingController
from arbiter.shared.models import CommandProposal
from arbiter.simulator.engine import SimulationEngine


def trigger_2am_collision(
    engine: Optional[SimulationEngine] = None,
    base_ts_ms: int = 1732154400000,
) -> List[CommandProposal]:
    """Emit the 3 command proposals comprising the 2 AM Collision.

    1. Energy Saving: Cell B -> SLEEP_STATE = 1.0 (at base + 10 ms)
    2. Load Balancing: Cell B -> OFFLOAD_RATIO = 0.40 (at base + 15 ms)
    3. Coverage Capacity: Cell A -> TX_POWER_DBM = 46.0 (at base + 20 ms)
    """
    ctrl_es = EnergySavingController()
    ctrl_lb = LoadBalancingController()
    ctrl_cc = CoverageCapacityController()

    prop_es = ctrl_es.propose_sleep(
        cell_id="cell_B",
        timestamp_epoch_ms=base_ts_ms + 10,
    )
    prop_lb = ctrl_lb.propose_offload(
        source_cell="cell_A",
        target_cell="cell_B",
        ratio=0.40,
        timestamp_epoch_ms=base_ts_ms + 15,
    )
    prop_cc = ctrl_cc.propose_power_boost(
        cell_id="cell_A",
        delta_db=3.0,
        current_power_dbm=43.0,
        timestamp_epoch_ms=base_ts_ms + 20,
    )

    proposals = [prop_es, prop_lb, prop_cc]

    if engine is not None:
        sink = getattr(engine, "proposal_sink", None)
        if sink is not None:
            for p in proposals:
                sink.submit_proposal(p)

    return proposals


def apply_uncoordinated_baseline(
    engine: SimulationEngine,
    proposals: List[CommandProposal],
) -> None:
    """Enact the collision proposals directly without arbitration."""
    sorted_proposals = sorted(proposals, key=lambda p: p.timestamp_epoch_ms)
    engine.apply_actuation_deltas(sorted_proposals)


def collision_report(
    engine_before: SimulationEngine,
    engine_after: SimulationEngine,
) -> Dict[str, Any]:
    """Summarize the impact of the uncoordinated collision on network KPIs."""
    cell_b_power_w = engine_after.cells["cell_B"].effective_tx_power_w()

    edge_sinr_before = engine_before.get_edge_sinr()
    edge_sinr_after = engine_after.get_edge_sinr()

    # Active users in post-collision state
    active_users = engine_after.traffic_model.get_active_users(
        engine_after.tick
    )
    total_drops = sum(1 for u in active_users if u.is_dropped)
    cell_b_drops = sum(
        1 for u in active_users
        if u.attached_cell_id == "cell_B" and u.is_dropped
    )

    # Average interference across edge UEs
    interf_after = [
        u.interference_power_dbm for u in active_users if not u.is_dropped]
    mean_interf = float(np.mean(interf_after)) if interf_after else -104.0

    return {
        "cell_B_power_watts": cell_b_power_w,
        "total_dropped_users": total_drops,
        "cell_B_dropped_users": cell_b_drops,
        "cell_A_edge_sinr_before": edge_sinr_before.get("cell_A", 0.0),
        "cell_A_edge_sinr_after": edge_sinr_after.get("cell_A", 0.0),
        "mean_interference_dbm": mean_interf,
        "blackout_condition": (cell_b_power_w == 0.0 and cell_b_drops > 0),
    }
