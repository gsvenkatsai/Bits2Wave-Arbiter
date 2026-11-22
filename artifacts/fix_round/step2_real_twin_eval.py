"""Step 2 Evaluation with Real Digital Twin.

Computes:
1. 2 AM collision over 120 ticks for 10 seeds (101-110) with the REAL TWIN:
   - Decisions & plan steps
   - Tick at which Cell B sleeps (if ever)
   - Peak dropped users (Arbiter vs Uncoord)
   - Unique users ever dropped (Arbiter vs Uncoord)
   - Summed dropped users (users x ticks) (Arbiter vs Uncoord)
2. Cumulative energy over 200 ticks:
   - None (baseline network, no controller triggers)
   - Uncoordinated
   - ARBITER
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Set

sys.path.insert(0, os.path.abspath("."))

from arbiter.controllers.energy_saving import EnergySavingController
from arbiter.controllers.scenarios import (
    apply_uncoordinated_baseline,
    trigger_2am_collision,
)
from arbiter.engine.contracts import CommandProposal as EngineProposal
from arbiter.engine.default_twin import make_default_twin
from arbiter.queue.orchestrator import QueueOrchestrator
from arbiter.simulator.engine import SimulationEngine


def evaluate_seed_120_ticks_real_twin(seed: int) -> Dict[str, Any]:
    engine = SimulationEngine(
        topology="5_CELL_CORE",
        scenario="2am_collision",
        seed=seed,
        use_real_queue=True,
        twin_kind="real",
    )
    engine_uncoord = SimulationEngine(
        topology="5_CELL_CORE",
        scenario="2am_collision",
        seed=seed,
        use_real_queue=False,
        twin_kind="real",
    )

    ctrl_es = EnergySavingController()
    decisions: List[Any] = []
    proposals: List[Any] = []
    cell_b_sleep_tick: int | None = None
    cell_b_sleep_uncoord_tick: int | None = None

    per_tick_arbiter_drops: List[int] = []
    per_tick_uncoord_drops: List[int] = []

    unique_arbiter_dropped_users: Set[str] = set()
    unique_uncoord_dropped_users: Set[str] = set()

    plan_steps: List[Dict[str, Any]] = []

    twin = make_default_twin(topology="5_CELL_CORE", kind="real")

    for t in range(120):
        samples = engine.step()
        samples_uncoord = engine_uncoord.step()

        if engine.cells["cell_B"].is_sleeping and cell_b_sleep_tick is None:
            cell_b_sleep_tick = t
        if engine_uncoord.cells["cell_B"].is_sleeping and cell_b_sleep_uncoord_tick is None:
            cell_b_sleep_uncoord_tick = t

        # Drops accounting
        arb_drops = sum(s.dropped_users for s in samples)
        unc_drops = sum(s.dropped_users for s in samples_uncoord)
        per_tick_arbiter_drops.append(arb_drops)
        per_tick_uncoord_drops.append(unc_drops)

        for u in engine.traffic_model.pool:
            if u.is_dropped:
                unique_arbiter_dropped_users.add(u.ue_id)
        for u in engine_uncoord.traffic_model.pool:
            if u.is_dropped:
                unique_uncoord_dropped_users.add(u.ue_id)

        loads = engine.get_cell_loads()
        cell_sleeps = {cid: c.is_sleeping for cid, c in engine.cells.items()}

        if t < 6:
            ctrl_es.evaluate_telemetry(
                loads, current_tick=t, cell_sleep_states=cell_sleeps
            )

        if t == 6:
            proposals = trigger_2am_collision(
                engine=engine, base_ts_ms=engine.simulated_epoch_ms
            )
            apply_uncoordinated_baseline(engine_uncoord, proposals)

            adj = {
                cid: cell.config.neighbor_cell_ids
                for cid, cell in engine.cells.items()
            }
            orchestrator = QueueOrchestrator(
                twin=twin, adjacency=adj, max_workers=2
            )
            for p in proposals:
                orchestrator.submit(
                    EngineProposal.model_validate(p.model_dump())
                )

            res = orchestrator.on_tick(
                current_tick=t, timestamp_epoch_ms=engine.simulated_epoch_ms
            )
            decisions = res["decisions"]

            for d in decisions:
                prop = next(
                    (p for p in proposals if p.command_id == d.command_id),
                    None,
                )
                target_cid = prop.target_cell_id if prop else "unknown"
                param_name = (
                    str(getattr(prop.parameter, "value", prop.parameter))
                    if prop else "unknown"
                )
                plan_steps.append({
                    "decision_id": d.decision_id,
                    "command_id": d.command_id,
                    "target_cell_id": target_cid,
                    "parameter": param_name,
                    "action": d.action.value,
                    "approved_value": d.approved_value,
                    "reason_code": d.reason_code.value,
                    "delta_utility": d.delta_utility,
                })

                if d.action.value == "MODIFY" and d.approved_value is not None:
                    norm_p = param_name.upper()
                    if "POWER" in norm_p and target_cid in engine.cells:
                        engine.cells[target_cid].set_tx_power(d.approved_value)
                    elif "OFFLOAD" in norm_p:
                        engine.apply_actuation_deltas([{
                            "target_cell_id": target_cid,
                            "parameter": "OFFLOAD_RATIO",
                            "value": d.approved_value,
                        }])

    return {
        "seed": seed,
        "cell_b_sleep_tick": cell_b_sleep_tick,
        "cell_b_sleep_uncoord_tick": cell_b_sleep_uncoord_tick,
        "decisions_count": len(decisions),
        "plan_steps": plan_steps,
        "arbiter_peak_drops": max(per_tick_arbiter_drops),
        "uncoord_peak_drops": max(per_tick_uncoord_drops),
        "arbiter_unique_drops": len(unique_arbiter_dropped_users),
        "uncoord_unique_drops": len(unique_uncoord_dropped_users),
        "arbiter_summed_drops": sum(per_tick_arbiter_drops),
        "uncoord_summed_drops": sum(per_tick_uncoord_drops),
    }


def compute_energy_200_ticks(seed: int = 101) -> Dict[str, float]:
    """Compute cumulative energy consumption over 200 ticks (20s) in kWh or Joules."""
    # 1. None: baseline network running without 2am collision controller trigger
    eng_none = SimulationEngine(
        topology="5_CELL_CORE", scenario="quiet_2am", seed=seed
    )
    # 2. Uncoordinated: 2am collision applied raw at tick 6
    eng_unc = SimulationEngine(
        topology="5_CELL_CORE", scenario="2am_collision", seed=seed
    )
    # 3. ARBITER: 2am collision arbitrated with real twin
    eng_arb = SimulationEngine(
        topology="5_CELL_CORE", scenario="2am_collision", seed=seed, use_real_queue=True
    )
    twin = make_default_twin(topology="5_CELL_CORE", kind="real")

    energy_none_joules = 0.0
    energy_unc_joules = 0.0
    energy_arb_joules = 0.0

    # 1 tick = 0.1s. Energy = Power (W) * 0.1s
    for t in range(200):
        # eng_none: just steps
        eng_none.step()
        energy_none_joules += sum(c.energy_consumption_w() for c in eng_none.cells.values()) * 0.1

        # eng_unc
        eng_unc.step()
        if t == 6:
            props = trigger_2am_collision(engine=eng_unc, base_ts_ms=eng_unc.simulated_epoch_ms)
            apply_uncoordinated_baseline(eng_unc, props)
        energy_unc_joules += sum(c.energy_consumption_w() for c in eng_unc.cells.values()) * 0.1

        # eng_arb
        eng_arb.step()
        if t == 6:
            props = trigger_2am_collision(engine=eng_arb, base_ts_ms=eng_arb.simulated_epoch_ms)
            adj = {cid: cell.config.neighbor_cell_ids for cid, cell in eng_arb.cells.items()}
            orchestrator = QueueOrchestrator(twin=twin, adjacency=adj, max_workers=2)
            for p in props:
                orchestrator.submit(EngineProposal.model_validate(p.model_dump()))
            res = orchestrator.on_tick(current_tick=t, timestamp_epoch_ms=eng_arb.simulated_epoch_ms)
            for d in res["decisions"]:
                p = next((x for x in props if x.command_id == d.command_id), None)
                if p and d.action.value == "MODIFY" and d.approved_value is not None:
                    p_name = str(getattr(p.parameter, "value", p.parameter)).upper()
                    if "POWER" in p_name and p.target_cell_id in eng_arb.cells:
                        eng_arb.cells[p.target_cell_id].set_tx_power(d.approved_value)
                    elif "OFFLOAD" in p_name:
                        eng_arb.apply_actuation_deltas([{
                            "target_cell_id": p.target_cell_id,
                            "parameter": "OFFLOAD_RATIO",
                            "value": d.approved_value,
                        }])
        energy_arb_joules += sum(c.energy_consumption_w() for c in eng_arb.cells.values()) * 0.1

    return {
        "energy_none_kj": energy_none_joules / 1000.0,
        "energy_unc_kj": energy_unc_joules / 1000.0,
        "energy_arb_kj": energy_arb_joules / 1000.0,
        "energy_none_kwh": energy_none_joules / 3.6e6,
        "energy_unc_kwh": energy_unc_joules / 3.6e6,
        "energy_arb_kwh": energy_arb_joules / 3.6e6,
    }


def main() -> None:
    seeds = list(range(101, 111))
    print(f"Running Real Twin Evaluation for 10 seeds: {seeds}")
    results = []
    for s in seeds:
        res = evaluate_seed_120_ticks_real_twin(s)
        results.append(res)
        print(
            f"Seed {s:3d} | Cell B Sleep: Arbiter={res['cell_b_sleep_tick']} Uncoord={res['cell_b_sleep_uncoord_tick']} | "
            f"Peak Drops: {res['arbiter_peak_drops']} vs {res['uncoord_peak_drops']} | "
            f"Unique Drops: {res['arbiter_unique_drops']} vs {res['uncoord_unique_drops']} | "
            f"Summed Drops: {res['arbiter_summed_drops']} vs {res['uncoord_summed_drops']}"
        )

    energy = compute_energy_200_ticks(101)
    print("\nCumulative Energy over 200 ticks (20s) [Seed 101]:")
    print(f"  None (No controllers):     {energy['energy_none_kj']:.2f} kJ ({energy['energy_none_kwh']:.6f} kWh)")
    print(f"  Uncoordinated Baseline:    {energy['energy_unc_kj']:.2f} kJ ({energy['energy_unc_kwh']:.6f} kWh)")
    print(f"  ARBITER Coordinated:       {energy['energy_arb_kj']:.2f} kJ ({energy['energy_arb_kwh']:.6f} kWh)")

    with open("artifacts/fix_round/step2_real_twin_eval.json", "w", encoding="utf-8") as f:
        json.dump({"seeds_eval": results, "energy_200_ticks": energy}, f, indent=2)


if __name__ == "__main__":
    main()
