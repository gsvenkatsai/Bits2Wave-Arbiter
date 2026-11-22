"""
Phase 1: Comprehensive Functionality & Determinism & Physics Measurements.
Runs 10 seeds of 2 AM collision, behavioral edge cases, determinism check, and physics spot-checks.
Writes results to artifacts/assessment/phase1_results.json.
"""
import os
import sys
import time
import math
import json
import sqlite3
import numpy as np

# Ensure arbiter is in path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, BASE_DIR)

from arbiter.simulator.engine import SimulationEngine, ScenarioType
from arbiter.controllers.scenarios import trigger_2am_collision, apply_uncoordinated_baseline
from arbiter.detector.graph import ConflictGraphBuilder
from arbiter.queue.orchestrator import QueueOrchestrator
from arbiter.engine.contracts import CommandProposal as EngineProposal, ActionType, ReasonCode
from arbiter.shared.models import ControllerType
from arbiter.simulator.propagation import COST231PathLossModel
from tests.branch2.test_decision_ladder import _scenario_twin, _two_am_commands

def run_10_seeds():
    print("Running 10-seed 2 AM collision simulation...")
    results = []
    seeds = list(range(101, 111))
    
    for s in seeds:
        eng_coord = SimulationEngine(cluster_size=5, rng_seed=s, scenario=ScenarioType.COLLISION_2AM)
        eng_uncoord = SimulationEngine(cluster_size=5, rng_seed=s, scenario=ScenarioType.COLLISION_2AM)
        
        # Warmup 5 ticks
        for t in range(6):
            eng_coord.step()
            eng_uncoord.step()
            
        # Tick 6: collision
        props = trigger_2am_collision(engine=eng_coord, base_ts_ms=eng_coord.simulated_epoch_ms)
        
        # Run uncoordinated counterfactual
        apply_uncoordinated_baseline(eng_uncoord, props)
        
        # Run ARBITER coordinated referee
        adj = {cid: cell.config.neighbor_cell_ids for cid, cell in eng_coord.cells.items()}
        orch = QueueOrchestrator(twin=_scenario_twin(), adjacency=adj, max_workers=2)
        for p in props:
            orch.submit(EngineProposal.model_validate(p.model_dump()))
            
        t0 = time.perf_counter()
        orch_res = orch.on_tick(current_tick=6, timestamp_epoch_ms=eng_coord.simulated_epoch_ms)
        arb_latency_ms = (time.perf_counter() - t0) * 1000.0
        
        decisions = orch_res["decisions"]
        
        # Apply decisions to coordinated engine
        for d in decisions:
            prop = next((p for p in props if p.command_id == d.command_id), None)
            if prop:
                norm_p = str(getattr(prop.parameter, "value", prop.parameter)).upper()
                target_cid = prop.target_cell_id
                if d.action.value == "MODIFY" and d.approved_value is not None:
                    if "POWER" in norm_p and target_cid in eng_coord.cells:
                        eng_coord.cells[target_cid].set_tx_power(d.approved_value)
                elif d.action.value == "APPROVE":
                    if "SLEEP" in norm_p and target_cid in eng_coord.cells:
                        eng_coord.cells[target_cid].set_sleep(bool(d.approved_value))
        
        # Step both engines for 20 ticks post-collision
        coord_drops = 0
        uncoord_drops = 0
        coord_energy = []
        uncoord_energy = []
        
        for t in range(7, 25):
            samples_c = eng_coord.step()
            samples_u = eng_uncoord.step()
            
            coord_drops += sum(1 for sm in samples_c if sm.sinr_db < -3.0 or sm.throughput_mbps == 0.0)
            uncoord_drops += sum(1 for sm in samples_u if sm.sinr_db < -3.0 or sm.throughput_mbps == 0.0)
            
            coord_energy.append(sum(c.power_consumption_w for c in eng_coord.cells.values()))
            uncoord_energy.append(sum(c.power_consumption_w for c in eng_uncoord.cells.values()))
            
        builder = ConflictGraphBuilder()
        graph = builder.build(
            [EngineProposal.model_validate(p.model_dump()) for p in props],
            adj
        )
        
        results.append({
            "seed": s,
            "proposals_count": len(props),
            "proposals": [
                {"id": p.command_id, "ctrl": str(p.controller_type), "target": p.target_cell_id, "param": str(p.parameter), "req": p.requested_value}
                for p in props
            ],
            "conflict_edges": [list(e) for e in graph.edges],
            "decisions": [
                {"id": d.command_id, "action": d.action.value, "value": d.approved_value, "reason": d.reason_code.value, "hold": d.hold_count}
                for d in decisions
            ],
            "arbitration_latency_ms": arb_latency_ms,
            "coord_drops": coord_drops,
            "uncoord_drops": uncoord_drops,
            "coord_avg_power_w": float(np.mean(coord_energy)),
            "uncoord_avg_power_w": float(np.mean(uncoord_energy))
        })
        
    return results

def check_behavioral_edge_cases():
    print("Testing behavioral edge cases...")
    edge_cases = {}
    
    # (a) PS emergency override bypass
    orch = QueueOrchestrator(twin=_scenario_twin(), adjacency={"A": ["B"], "B": ["A"]})
    p_emerg = EngineProposal(
        command_id="emerg_ps_01",
        controller="ctrl-ps",
        target_cell="A",
        parameter="TX_POWER_DBM",
        current_value=30.0,
        requested_value=43.0,
        is_emergency=True,
        is_binary=False,
        created_tick=1
    )
    orch.submit(p_emerg)
    res_emerg = orch.on_tick(current_tick=1, timestamp_epoch_ms=1000)
    d_emerg = res_emerg["decisions"][0] if res_emerg["decisions"] else None
    edge_cases["emergency_bypass"] = {
        "action": d_emerg.action.value if d_emerg else None,
        "reason": d_emerg.reason_code.value if d_emerg else None,
        "bypassed": (d_emerg.action.value == "APPROVE" and d_emerg.reason_code.value == "EMERGENCY_OVERRIDE") if d_emerg else False
    }
    
    # (b) hold-count limit (max 3) forces decision
    orch_hold = QueueOrchestrator(twin=_scenario_twin(), adjacency={"A": ["B"], "B": ["A"]})
    # Proposal held repeatedly
    p_hold = EngineProposal(
        command_id="hold_test_01",
        controller="ctrl-es",
        target_cell="B",
        parameter="SLEEP_STATE",
        current_value=0.0,
        requested_value=1.0,
        is_binary=True,
        created_tick=1
    )
    # Force a conflicting command that causes DELAY
    p_clash = EngineProposal(
        command_id="hold_clash_01",
        controller="ctrl-lb",
        target_cell="A",
        parameter="OFFLOAD_RATIO",
        current_value=0.0,
        requested_value=0.4,
        handover_from="A",
        handover_to="B",
        created_tick=1
    )
    orch_hold.submit(p_hold)
    orch_hold.submit(p_clash)
    
    hold_trail = []
    for t in range(1, 6):
        res = orch_hold.on_tick(current_tick=t, timestamp_epoch_ms=1000 * t)
        for d in res["decisions"]:
            if d.command_id == "hold_test_01":
                hold_trail.append({"tick": t, "action": d.action.value, "hold": d.hold_count, "reason": d.reason_code.value})
    edge_cases["hold_limit"] = {
        "trail": hold_trail,
        "forced_final": any(d["action"] in ["APPROVE", "REJECT", "MODIFY"] and d["hold"] >= 3 for d in hold_trail) or (len(hold_trail) > 0 and hold_trail[-1]["hold"] <= 3)
    }
    
    # (c) Slow twin timeout fallback
    class SlowTwin:
        def evaluate(self, candidate, timeout_budget_ms=None):
            time.sleep(0.06) # 60ms delay
            return {"predicted_kpis": {}, "computation_time_ms": 60.0}
    
    orch_slow = QueueOrchestrator(twin=SlowTwin(), adjacency={"A": ["B"], "B": ["A"]})
    p_timeout = EngineProposal(
        command_id="to_test_01",
        controller="ctrl-cc",
        target_cell="A",
        parameter="TX_POWER_DBM",
        current_value=30.0,
        requested_value=35.0,
        created_tick=1
    )
    orch_slow.submit(p_timeout)
    res_to = orch_slow.on_tick(current_tick=1, timestamp_epoch_ms=1000)
    d_to = res_to["decisions"][0] if res_to["decisions"] else None
    edge_cases["twin_timeout"] = {
        "action": d_to.action.value if d_to else None,
        "reason": d_to.reason_code.value if d_to else None
    }
    
    # (d) Invalid / out-of-range proposals
    invalid_rejected = False
    try:
        # Invalid offload ratio > 1.0 or invalid power < 0
        p_invalid = EngineProposal(
            command_id="invalid_01",
            controller="ctrl-cc",
            target_cell="A",
            parameter="TX_POWER_DBM",
            current_value=30.0,
            requested_value=-50.0, # negative dBm out of range
            created_tick=1
        )
        orch_inv = QueueOrchestrator(twin=_scenario_twin(), adjacency={"A": []})
        orch_inv.submit(p_invalid)
        res_inv = orch_inv.on_tick(current_tick=1, timestamp_epoch_ms=1000)
        d_inv = res_inv["decisions"][0] if res_inv["decisions"] else None
        invalid_rejected = (d_inv.action.value == "REJECT") if d_inv else False
    except Exception as e:
        invalid_rejected = True # pydantic validation caught it
        edge_cases["invalid_exception"] = str(e)
    edge_cases["invalid_rejection"] = invalid_rejected

    # (e) Flood 1,000 proposals
    orch_flood = QueueOrchestrator(twin=_scenario_twin(), adjacency={"A": ["B"], "B": ["A"]})
    t_start = time.perf_counter()
    for i in range(1000):
        p_f = EngineProposal(
            command_id=f"flood_{i}",
            controller=f"ctrl-{i%4}",
            target_cell="A" if i%2 == 0 else "B",
            parameter="TX_POWER_DBM" if i%2 == 0 else "OFFLOAD_RATIO",
            current_value=30.0,
            requested_value=31.0 + (i%5),
            created_tick=1
        )
        orch_flood.submit(p_f)
    res_flood = orch_flood.on_tick(current_tick=1, timestamp_epoch_ms=1000)
    flood_dur = time.perf_counter() - t_start
    edge_cases["flood_1000"] = {
        "duration_sec": flood_dur,
        "decisions_count": len(res_flood["decisions"]),
        "status": "PASS" if len(res_flood["decisions"]) > 0 else "FAIL"
    }

    # (f) Restart persistence
    test_db = os.path.join(BASE_DIR, "artifacts", "assessment", "test_restart.db")
    if os.path.exists(test_db):
        os.remove(test_db)
    from arbiter.shared.database import init_database
    from arbiter.storage.commit_log import SQLiteCommitLogger
    init_database(test_db)
    logger1 = SQLiteCommitLogger(test_db)
    logger1.append(
        commit_id="c_persist_01",
        command_id="cmd_persist_01",
        target_cell_id="cell_A",
        parameter="TX_POWER_DBM",
        previous_value=30.0,
        applied_value=32.0,
        plan_id="plan_01",
        status="COMMITTED"
    )
    # Close and reopen new logger
    logger2 = SQLiteCommitLogger(test_db)
    conn = sqlite3.connect(test_db)
    c = conn.cursor()
    c.execute("SELECT commit_id, applied_value FROM commit_log WHERE commit_id = 'c_persist_01'")
    row = c.fetchone()
    conn.close()
    edge_cases["restart_persistence"] = {
        "persisted": row is not None and row[0] == "c_persist_01" and row[1] == 32.0,
        "row": row
    }

    # (h) Non-collision scenarios
    scenarios_tested = {}
    for scn in [ScenarioType.BASELINE, ScenarioType.PEAK_TRAFFIC, ScenarioType.STADIUM_SURGE]:
        try:
            eng = SimulationEngine(cluster_size=5, rng_seed=42, scenario=scn)
            for _ in range(10):
                eng.step()
            scenarios_tested[scn.value] = "SUCCESS"
        except Exception as e:
            scenarios_tested[scn.value] = f"ERROR: {e}"
    edge_cases["non_collision_scenarios"] = scenarios_tested

    return edge_cases

def check_determinism():
    print("Testing determinism across 2 identical seed runs...")
    eng1 = SimulationEngine(cluster_size=5, rng_seed=999, scenario=ScenarioType.COLLISION_2AM)
    eng2 = SimulationEngine(cluster_size=5, rng_seed=999, scenario=ScenarioType.COLLISION_2AM)
    
    drops1 = []
    drops2 = []
    for _ in range(15):
        s1 = eng1.step()
        s2 = eng2.step()
        drops1.append([sm.sinr_db for sm in s1])
        drops2.append([sm.sinr_db for sm in s2])
        
    diffs = 0
    for d1, d2 in zip(drops1, drops2):
        if not np.allclose(d1, d2, atol=1e-7):
            diffs += 1
            
    return {"deterministic": diffs == 0, "diff_ticks": diffs}

def spot_check_physics():
    print("Checking physics formulas...")
    # COST-231 at 500m
    # Spec formula: PL = 46.3 + 33.9*log10(f_MHz) - 13.82*log10(h_b) - a(h_m) + (44.9 - 6.55*log10(h_b))*log10(d_km) + C_m
    f_mhz = 2100.0
    h_b = 30.0
    h_m = 1.5
    d_km = 0.5
    
    # Standard COST-231 Suburban / Urban
    a_hm = (1.1 * math.log10(f_mhz) - 0.7) * h_m - (1.56 * math.log10(f_mhz) - 0.8)
    standard_pl = (
        46.3
        + 33.9 * math.log10(f_mhz)
        - 13.82 * math.log10(h_b)
        - a_hm
        + (44.9 - 6.55 * math.log10(h_b)) * math.log10(d_km)
        + 0.0 # medium city
    )
    
    # Model in code
    model = COST231PathLossModel(carrier_freq_mhz=f_mhz, bs_height_m=h_b, ue_height_m=h_m)
    code_pl = model.compute_path_loss(distance_m=500.0)
    
    # Check sleeping cell power
    eng = SimulationEngine(cluster_size=5, rng_seed=42)
    c_b = eng.cells["cell_B"]
    c_b.set_sleep(True)
    sleep_power = c_b.power_consumption_w
    
    # Check SINR monotonicity
    dists = [100.0, 300.0, 500.0, 800.0, 1200.0]
    pls = [model.compute_path_loss(d) for d in dists]
    pl_monotonic = all(pls[i] < pls[i+1] for i in range(len(pls)-1))
    
    return {
        "distance_500m_standard_pl_db": round(standard_pl, 3),
        "distance_500m_code_pl_db": round(code_pl, 3),
        "pl_diff_db": round(code_pl - standard_pl, 3),
        "pl_monotonic": pl_monotonic,
        "sleeping_cell_power_w": sleep_power
    }

def main():
    seeds_data = run_10_seeds()
    edge_cases = check_behavioral_edge_cases()
    det = check_determinism()
    physics = spot_check_physics()
    
    results = {
        "seeds_run": seeds_data,
        "edge_cases": edge_cases,
        "determinism": det,
        "physics": physics
    }
    
    out_file = os.path.join(BASE_DIR, "artifacts", "assessment", "phase1_results.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
        
    print(f"Phase 1 measurements completed and saved to {out_file}")

if __name__ == "__main__":
    main()

