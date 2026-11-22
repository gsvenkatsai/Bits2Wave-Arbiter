import sys
sys.path.insert(0, ".")
import json
import time
from arbiter.simulator.engine import SimulationEngine
from arbiter.controllers.scenarios import trigger_2am_collision, apply_uncoordinated_baseline
from arbiter.engine.contracts import CommandProposal as EngineProposal
from arbiter.engine.default_twin import make_default_twin
from arbiter.queue.orchestrator import QueueOrchestrator

seeds = list(range(101, 111))
results = []

for s in seeds:
    # 1. ARBITER Run (120 ticks) with real twin
    eng = SimulationEngine(topology="5_CELL_CORE", scenario="2am_collision", seed=s, use_real_queue=True, twin_kind="real")
    adj = {cid: cell.config.neighbor_cell_ids for cid, cell in eng.cells.items()}
    twin = make_default_twin(topology="5_CELL_CORE", kind="real")
    orchestrator = QueueOrchestrator(twin=twin, adjacency=adj, max_workers=2)
    
    arb_drops_peak = 0
    arb_drops_summed = 0
    seen_arb_drops = set()
    cell_b_sleep_tick = None
    decisions_summary = []
    
    t_start = time.perf_counter()
    for t in range(120):
        if t == 6:
            proposals = trigger_2am_collision(engine=eng, base_ts_ms=eng.simulated_epoch_ms)
            for p in proposals:
                orchestrator.submit(EngineProposal.model_validate(p.model_dump()))
            res = orchestrator.on_tick(current_tick=t, timestamp_epoch_ms=eng.simulated_epoch_ms)
            decisions = res["decisions"]
            for d in decisions:
                decisions_summary.append({
                    "command_id": d.command_id,
                    "action": d.action.value,
                    "reason_code": d.reason_code.value,
                    "approved_value": d.approved_value,
                })
                prop = next((p for p in proposals if p.command_id == d.command_id), None)
                if prop:
                    norm_p = str(getattr(prop.parameter, "value", prop.parameter)).upper()
                    target_cid = prop.target_cell_id
                    if d.action.value == "MODIFY" and d.approved_value is not None:
                        if "POWER" in norm_p and target_cid in eng.cells:
                            eng.cells[target_cid].set_tx_power(d.approved_value)
                        elif "OFFLOAD" in norm_p:
                            eng.apply_actuation_deltas([{
                                "target_cell_id": target_cid,
                                "parameter": "OFFLOAD_RATIO",
                                "value": d.approved_value,
                            }])
        
        samples = eng.step()
        dropped_this_tick = sum(1 for ue in eng.users if ue.is_dropped)
        arb_drops_peak = max(arb_drops_peak, dropped_this_tick)
        arb_drops_summed += dropped_this_tick
        for ue in eng.users:
            if ue.is_dropped:
                seen_arb_drops.add(ue.ue_id)
        if eng.cells["cell_B"].is_sleeping and cell_b_sleep_tick is None:
            cell_b_sleep_tick = t
            
    arb_drops_unique = len(seen_arb_drops)
    arb_runtime_ms = (time.perf_counter() - t_start) * 1000.0
    
    # 2. Uncoordinated Run (120 ticks)
    eng_u = SimulationEngine(topology="5_CELL_CORE", scenario="2am_collision", seed=s, use_real_queue=False, twin_kind="real")
    uncoord_drops_peak = 0
    uncoord_drops_summed = 0
    seen_uncoord_drops = set()
    uncoord_cell_b_sleep_tick = None
    
    for t in range(120):
        if t == 6:
            proposals_u = trigger_2am_collision(engine=eng_u, base_ts_ms=eng_u.simulated_epoch_ms)
            apply_uncoordinated_baseline(eng_u, proposals_u)
        samples_u = eng_u.step()
        dropped_this_tick = sum(1 for ue in eng_u.users if ue.is_dropped)
        uncoord_drops_peak = max(uncoord_drops_peak, dropped_this_tick)
        uncoord_drops_summed += dropped_this_tick
        for ue in eng_u.users:
            if ue.is_dropped:
                seen_uncoord_drops.add(ue.ue_id)
        if eng_u.cells["cell_B"].is_sleeping and uncoord_cell_b_sleep_tick is None:
            uncoord_cell_b_sleep_tick = t
            
    uncoord_drops_unique = len(seen_uncoord_drops)
    
    # 3. Energy over 200 ticks
    # None
    eng_none = SimulationEngine(topology="5_CELL_CORE", scenario="quiet_2am", seed=s)
    energy_none_j = 0.0
    for _ in range(200):
        eng_none.step()
        energy_none_j += sum(c.effective_tx_power_w() for c in eng_none.cells.values()) * 0.1
        
    # Uncoordinated
    eng_u200 = SimulationEngine(topology="5_CELL_CORE", scenario="2am_collision", seed=s)
    energy_uncoord_j = 0.0
    for t in range(200):
        if t == 6:
            p = trigger_2am_collision(eng_u200, eng_u200.simulated_epoch_ms)
            apply_uncoordinated_baseline(eng_u200, p)
        eng_u200.step()
        energy_uncoord_j += sum(c.effective_tx_power_w() for c in eng_u200.cells.values()) * 0.1
        
    # ARBITER
    eng_a200 = SimulationEngine(topology="5_CELL_CORE", scenario="2am_collision", seed=s, use_real_queue=True, twin_kind="real")
    orch_a200 = QueueOrchestrator(twin=twin, adjacency=adj, max_workers=2)
    energy_arb_j = 0.0
    for t in range(200):
        if t == 6:
            p = trigger_2am_collision(eng_a200, eng_a200.simulated_epoch_ms)
            for item in p:
                orch_a200.submit(EngineProposal.model_validate(item.model_dump()))
            r = orch_a200.on_tick(current_tick=t, timestamp_epoch_ms=eng_a200.simulated_epoch_ms)
            for d in r["decisions"]:
                prop = next((x for x in p if x.command_id == d.command_id), None)
                if prop and d.action.value == "MODIFY" and d.approved_value is not None:
                    norm_p = str(getattr(prop.parameter, "value", prop.parameter)).upper()
                    target_cid = prop.target_cell_id
                    if "POWER" in norm_p and target_cid in eng_a200.cells:
                        eng_a200.cells[target_cid].set_tx_power(d.approved_value)
                    elif "OFFLOAD" in norm_p:
                        eng_a200.apply_actuation_deltas([{
                            "target_cell_id": target_cid,
                            "parameter": "OFFLOAD_RATIO",
                            "value": d.approved_value,
                        }])
        eng_a200.step()
        energy_arb_j += sum(c.effective_tx_power_w() for c in eng_a200.cells.values()) * 0.1

    results.append({
        "seed": s,
        "cell_b_sleep_tick_arbiter": cell_b_sleep_tick,
        "cell_b_sleep_tick_uncoord": uncoord_cell_b_sleep_tick,
        "arbiter_drops_peak": arb_drops_peak,
        "arbiter_drops_unique": arb_drops_unique,
        "arbiter_drops_summed": arb_drops_summed,
        "uncoord_drops_peak": uncoord_drops_peak,
        "uncoord_drops_unique": uncoord_drops_unique,
        "uncoord_drops_summed": uncoord_drops_summed,
        "energy_none_kj": round(energy_none_j / 1000.0, 2),
        "energy_uncoord_kj": round(energy_uncoord_j / 1000.0, 2),
        "energy_arbiter_kj": round(energy_arb_j / 1000.0, 2),
        "decisions": decisions_summary,
    })

with open("artifacts/readme/2am_collision_10seeds.json", "w") as f:
    json.dump(results, f, indent=2)

print("SUCCESS: 10 seeds processed with real twin.")
