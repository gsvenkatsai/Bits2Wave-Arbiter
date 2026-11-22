"""
Task 1 Reproduction Script: Per-tick log for 80 ticks across seeds 101-110.
"""
import os
import sys
import json
import numpy as np

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, BASE_DIR)

from arbiter.simulator.engine import SimulationEngine, ScenarioType
from arbiter.controllers.scenarios import trigger_2am_collision, apply_uncoordinated_baseline
from arbiter.queue.orchestrator import QueueOrchestrator
from arbiter.engine.contracts import CommandProposal as EngineProposal
from tests.branch2.test_decision_ladder import _scenario_twin

def run_task1():
    seeds = list(range(101, 111))
    summary_table = []
    detailed_logs = {}

    for s in seeds:
        eng_coord = SimulationEngine(topology="5_CELL_CORE", seed=s, scenario=ScenarioType.QUIET_2AM)
        eng_uncoord = SimulationEngine(topology="5_CELL_CORE", seed=s, scenario=ScenarioType.QUIET_2AM)

        # Warmup
        for t in range(6):
            eng_coord.step()
            eng_uncoord.step()

        props = trigger_2am_collision(eng_coord, base_ts_ms=eng_coord.simulated_epoch_ms)
        apply_uncoordinated_baseline(eng_uncoord, props)

        adj = {cid: cell.config.neighbor_cell_ids for cid, cell in eng_coord.cells.items()}
        orch = QueueOrchestrator(twin=_scenario_twin(), adjacency=adj, max_workers=2)
        for p in props:
            orch.submit(EngineProposal.model_validate(p.model_dump()))
        res = orch.on_tick(current_tick=6, timestamp_epoch_ms=eng_coord.simulated_epoch_ms)

        # Apply decisions
        for d in res["decisions"]:
            prop = next((p for p in props if p.command_id == d.command_id), None)
            if prop:
                norm_p = str(getattr(prop.parameter, "value", prop.parameter)).upper()
                target_cid = prop.target_cell_id
                if d.action.value == "MODIFY" and d.approved_value is not None:
                    if "POWER" in norm_p and target_cid in eng_coord.cells:
                        eng_coord.cells[target_cid].set_tx_power(d.approved_value)
                    elif "OFFLOAD" in norm_p:
                        eng_coord.apply_actuation_deltas([{
                            "target_cell_id": target_cid,
                            "parameter": "OFFLOAD_RATIO",
                            "value": d.approved_value
                        }])
                elif d.action.value == "APPROVE":
                    if "SLEEP" in norm_p and target_cid in eng_coord.cells:
                        eng_coord.cells[target_cid].set_sleep(bool(d.approved_value))

        coord_tick_data = []
        uncoord_tick_data = []

        coord_unique_dropped = set()
        uncoord_unique_dropped = set()

        prev_coord_dropped = set()
        prev_uncoord_dropped = set()

        # Run ticks 7 to 80
        for t in range(7, 81):
            samples_c = eng_coord.step()
            samples_u = eng_uncoord.step()

            # Coord users
            act_c = eng_coord.traffic_model.get_active_users(eng_coord.tick - 1)
            drop_c = [u for u in act_c if u.is_dropped]
            curr_c_ids = {u.ue_id for u in drop_c}
            new_c_ids = curr_c_ids - prev_coord_dropped
            prev_coord_dropped = curr_c_ids
            coord_unique_dropped.update(curr_c_ids)

            # Uncoord users
            act_u = eng_uncoord.traffic_model.get_active_users(eng_uncoord.tick - 1)
            drop_u = [u for u in act_u if u.is_dropped]
            curr_u_ids = {u.ue_id for u in drop_u}
            new_u_ids = curr_u_ids - prev_uncoord_dropped
            prev_uncoord_dropped = curr_u_ids
            uncoord_unique_dropped.update(curr_u_ids)

            coord_tick_data.append({
                "tick": t,
                "currently_dropped": len(drop_c),
                "newly_dropped": len(new_c_ids),
                "dropped_details": [
                    {
                        "ue_id": u.ue_id,
                        "cell": u.attached_cell_id,
                        "sinr": round(float(u.sinr_db), 2),
                        "cell_asleep": eng_coord.cells[u.attached_cell_id].is_sleeping if u.attached_cell_id in eng_coord.cells else True,
                        "is_offloaded": u.ue_id in eng_coord.offload_overrides,
                        "sinr_breach": float(u.sinr_db) < -3.0
                    }
                    for u in drop_c
                ]
            })

            uncoord_tick_data.append({
                "tick": t,
                "currently_dropped": len(drop_u),
                "newly_dropped": len(new_u_ids),
                "dropped_details": [
                    {
                        "ue_id": u.ue_id,
                        "cell": u.attached_cell_id,
                        "sinr": round(float(u.sinr_db), 2),
                        "cell_asleep": eng_uncoord.cells[u.attached_cell_id].is_sleeping if u.attached_cell_id in eng_uncoord.cells else True,
                        "is_offloaded": u.ue_id in eng_uncoord.offload_overrides,
                        "sinr_breach": float(u.sinr_db) < -3.0
                    }
                    for u in drop_u
                ]
            })

        c_peak = max(d["currently_dropped"] for d in coord_tick_data)
        c_unique = len(coord_unique_dropped)
        c_summed = sum(d["currently_dropped"] for d in coord_tick_data)

        u_peak = max(d["currently_dropped"] for d in uncoord_tick_data)
        u_unique = len(uncoord_unique_dropped)
        u_summed = sum(d["currently_dropped"] for d in uncoord_tick_data)

        summary_table.append({
            "seed": s,
            "coord_peak": c_peak,
            "coord_unique": c_unique,
            "coord_summed": c_summed,
            "uncoord_peak": u_peak,
            "uncoord_unique": u_unique,
            "uncoord_summed": u_summed,
        })

        detailed_logs[s] = {
            "coord_ticks": coord_tick_data,
            "uncoord_ticks": uncoord_tick_data
        }

    out_json = os.path.join(BASE_DIR, "artifacts", "fix_round", "task1_investigation.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"summary": summary_table, "details": detailed_logs}, f, indent=2)

    print("Task 1 Reproduction Complete.")
    print("Summary:")
    for row in summary_table:
        print(f"Seed {row['seed']}: ARBITER peak={row['coord_peak']}, unique={row['coord_unique']}, sum={row['coord_summed']} | UNCOORD peak={row['uncoord_peak']}, unique={row['uncoord_unique']}, sum={row['uncoord_summed']}")

if __name__ == "__main__":
    run_task1()
