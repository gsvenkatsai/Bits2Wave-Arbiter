"""
Investigation of 2 AM collision residual drops across seeds 101-110.
Analyzes:
1. Per-tick newly dropped vs currently dropped users, attached cell, SINR, sleep state.
2. How dropped users are aggregated in engine, telemetry samples, and counterfactual.py.
3. Why numbers are multiples of 18 and 3.
4. Classification of each non-zero seed (metric artifact vs genuine drop vs bug).
5. Output of peak, unique, and summed metrics for ARBITER vs uncoordinated counterfactual.
"""
import os
import sys
import numpy as np

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, BASE_DIR)

from arbiter.simulator.engine import SimulationEngine, ScenarioType
from arbiter.controllers.scenarios import trigger_2am_collision, apply_uncoordinated_baseline
from arbiter.queue.orchestrator import QueueOrchestrator
from arbiter.engine.contracts import CommandProposal as EngineProposal
from tests.branch2.test_decision_ladder import _scenario_twin

def run_investigation():
    seeds = list(range(101, 111))
    report = {}

    for s in seeds:
        print(f"=== Investigating Seed {s} ===")
        eng_coord = SimulationEngine(topology="5_CELL_CORE", seed=s, scenario=ScenarioType.QUIET_2AM)
        eng_uncoord = SimulationEngine(topology="5_CELL_CORE", seed=s, scenario=ScenarioType.QUIET_2AM)

        # Track user IDs ever dropped
        coord_unique_dropped_ues = set()
        uncoord_unique_dropped_ues = set()
        
        # Track per-tick history for 80 ticks
        coord_history = []
        uncoord_history = []

        # Step 0..5 (warmup)
        for t in range(6):
            s_c = eng_coord.step()
            s_u = eng_uncoord.step()

        # Tick 6: collision
        props = trigger_2am_collision(engine=eng_coord, base_ts_ms=eng_coord.simulated_epoch_ms)
        apply_uncoordinated_baseline(eng_uncoord, props)

        # ARBITER Coordinated referee
        adj = {cid: cell.config.neighbor_cell_ids for cid, cell in eng_coord.cells.items()}
        orch = QueueOrchestrator(twin=_scenario_twin(), adjacency=adj, max_workers=2)
        for p in props:
            orch.submit(EngineProposal.model_validate(p.model_dump()))

        orch_res = orch.on_tick(current_tick=6, timestamp_epoch_ms=eng_coord.simulated_epoch_ms)
        decisions = orch_res["decisions"]

        # Apply ARBITER decisions (same as engine.py)
        for d in decisions:
            prop = next((p for p in props if p.command_id == d.command_id), None)
            if prop:
                norm_p = str(getattr(prop.parameter, "value", prop.parameter)).upper()
                target_cid = prop.target_cell_id
                if d.action.value == "MODIFY" and d.approved_value is not None:
                    if "POWER" in norm_p and target_cid in eng_coord.cells:
                        eng_coord.cells[target_cid].set_tx_power(d.approved_value)
                    elif "OFFLOAD" in norm_p:
                        # apply offload ratio
                        eng_coord.apply_actuation_deltas([{
                            "target_cell_id": target_cid,
                            "parameter": "OFFLOAD_RATIO",
                            "value": d.approved_value
                        }])
                elif d.action.value == "APPROVE":
                    if "SLEEP" in norm_p and target_cid in eng_coord.cells:
                        eng_coord.cells[target_cid].set_sleep(bool(d.approved_value))

        # Ticks 7 to 80
        prev_coord_dropped = set()
        prev_uncoord_dropped = set()

        for t in range(7, 81):
            samples_c = eng_coord.step()
            samples_u = eng_uncoord.step()

            # Inspect active users in coord
            active_c = eng_coord.traffic_model.get_active_users(eng_coord.tick - 1)
            dropped_c = [u for u in active_c if u.is_dropped]
            curr_c_ids = {u.ue_id for u in dropped_c}
            new_c_ids = curr_c_ids - prev_coord_dropped
            prev_coord_dropped = curr_c_ids
            for u in dropped_c:
                coord_unique_dropped_ues.add(u.ue_id)

            # Inspect active users in uncoord
            active_u = eng_uncoord.traffic_model.get_active_users(eng_uncoord.tick - 1)
            dropped_u = [u for u in active_u if u.is_dropped]
            curr_u_ids = {u.ue_id for u in dropped_u}
            new_u_ids = curr_u_ids - prev_uncoord_dropped
            prev_uncoord_dropped = curr_u_ids
            for u in dropped_u:
                uncoord_unique_dropped_ues.add(u.ue_id)

            # Record per-tick info
            coord_history.append({
                "tick": t,
                "currently_dropped_users": len(dropped_c),
                "newly_dropped_users": len(new_c_ids),
                "sample_dropped_sum": sum(s.dropped_users for s in samples_c),
                "sample_active_sum": sum(s.active_users for s in samples_c),
                "dropped_details": [
                    {
                        "ue_id": u.ue_id,
                        "cell": u.attached_cell_id,
                        "sinr": u.sinr_db,
                        "is_cell_asleep": eng_coord.cells[u.attached_cell_id].is_sleeping if u.attached_cell_id in eng_coord.cells else True,
                        "is_offloaded": u.ue_id in eng_coord.offload_overrides,
                        "sinr_below_minus3": u.sinr_db < -3.0
                    }
                    for u in dropped_c
                ]
            })

            uncoord_history.append({
                "tick": t,
                "currently_dropped_users": len(dropped_u),
                "newly_dropped_users": len(new_u_ids),
                "sample_dropped_sum": sum(s.dropped_users for s in samples_u),
                "sample_active_sum": sum(s.active_users for s in samples_u),
                "dropped_details": [
                    {
                        "ue_id": u.ue_id,
                        "cell": u.attached_cell_id,
                        "sinr": u.sinr_db,
                        "is_cell_asleep": eng_uncoord.cells[u.attached_cell_id].is_sleeping if u.attached_cell_id in eng_uncoord.cells else True,
                        "is_offloaded": u.ue_id in eng_uncoord.offload_overrides,
                        "sinr_below_minus3": u.sinr_db < -3.0
                    }
                    for u in dropped_u
                ]
            })

        # Calculate metrics for coord
        coord_peak = max(h["currently_dropped_users"] for h in coord_history)
        coord_unique = len(coord_unique_dropped_ues)
        coord_summed = sum(h["currently_dropped_users"] for h in coord_history)
        coord_sample_summed = sum(h["sample_dropped_sum"] for h in coord_history)

        # Calculate metrics for uncoord
        uncoord_peak = max(h["currently_dropped_users"] for h in uncoord_history)
        uncoord_unique = len(uncoord_unique_dropped_ues)
        uncoord_summed = sum(h["currently_dropped_users"] for h in uncoord_history)
        uncoord_sample_summed = sum(h["sample_dropped_sum"] for h in uncoord_history)

        report[s] = {
            "coord": {
                "peak": coord_peak,
                "unique": coord_unique,
                "summed": coord_summed,
                "sample_summed": coord_sample_summed,
                "history_sample": [h for h in coord_history if h["currently_dropped_users"] > 0][:5]
            },
            "uncoord": {
                "peak": uncoord_peak,
                "unique": uncoord_unique,
                "summed": uncoord_summed,
                "sample_summed": uncoord_sample_summed,
                "history_sample": [h for h in uncoord_history if h["currently_dropped_users"] > 0][:5]
            }
        }
        print(f"Seed {s}:")
        print(f"  Coord:   Peak={coord_peak}, Unique={coord_unique}, Summed={coord_summed}, SampleSummed={coord_sample_summed}")
        print(f"  Uncoord: Peak={uncoord_peak}, Unique={uncoord_unique}, Summed={uncoord_summed}, SampleSummed={uncoord_sample_summed}")

    return report

if __name__ == "__main__":
    run_investigation()
