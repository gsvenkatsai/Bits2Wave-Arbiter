"""Automated multi-controller collision search and floor breach discovery.

Generates random multi-controller proposal sets (2-6 proposals across cells)
under seeded pseudo-random distributions. Evaluates each proposal set through
ARBITER against the uncoordinated baseline to identify:
1. Safety floor breaches (drop rate > 2% or coverage < 90%).
2. Underperformance cases (utility_arbiter < utility_baseline).

Produces a structured JSON report including the gallery of the 5 worst cases.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from arbiter.engine.contracts import (
    CommandProposal as EngineProposal,
    UtilityWeights,
)
from arbiter.engine.utility import UtilityCalculator
from arbiter.queue.orchestrator import QueueOrchestrator
from arbiter.shared.models import (
    CommandProposal,
    ControllerType,
    ParameterType,
)
from arbiter.simulator.engine import SimulationEngine
from tests.branch2.test_decision_ladder import _scenario_twin

CELL_IDS = ["cell_A", "cell_B", "cell_C", "cell_D", "cell_E"]


def _random_proposal(
    rng: np.random.Generator,
    trial_idx: int,
    prop_idx: int,
    base_ts: int,
) -> CommandProposal:
    """Generate a single random command proposal within valid domains."""
    cid = rng.choice(CELL_IDS)
    param_choice = rng.choice(
        ["TX_POWER_DBM", "TILT_DEG", "SLEEP_STATE", "OFFLOAD_RATIO"]
    )

    if param_choice == "TX_POWER_DBM":
        param = ParameterType.TX_POWER_DBM
        ctrl_type = ControllerType.COVERAGE_CAPACITY
        ctrl_id = "ctrl-cc"
        val = round(float(rng.uniform(30.0, 46.0)), 1)
    elif param_choice == "TILT_DEG":
        param = ParameterType.TILT_DEG
        ctrl_type = ControllerType.COVERAGE_CAPACITY
        ctrl_id = "ctrl-tilt"
        val = round(float(rng.uniform(0.0, 15.0)), 1)
    elif param_choice == "SLEEP_STATE":
        param = ParameterType.SLEEP_STATE
        ctrl_type = ControllerType.ENERGY_SAVING
        ctrl_id = "ctrl-es"
        val = float(rng.choice([0.0, 1.0]))
    else:
        param = ParameterType.OFFLOAD_RATIO
        ctrl_type = ControllerType.LOAD_BALANCING
        ctrl_id = "ctrl-lb"
        val = round(float(rng.uniform(0.05, 0.50)), 2)

    return CommandProposal(
        command_id=f"cmd-search-{trial_idx}-{prop_idx}",
        controller_id=ctrl_id,
        controller_type=ctrl_type,
        target_cell_id=cid,
        parameter=param,
        requested_value=val,
        emergency_flag=False,
        timestamp_epoch_ms=base_ts + int(rng.integers(1, 50)),
    )


def _evaluate_trial(
    base_engine: SimulationEngine,
    proposals: List[CommandProposal],
    orchestrator: QueueOrchestrator,
    weights: UtilityWeights,
    horizon_ticks: int = 3,
) -> Tuple[float, float, float, float, List[Dict[str, Any]]]:
    """Evaluate one proposal batch on coordinated vs uncoordinated forks."""
    # 1. Arbitrate with ARBITER
    for p in proposals:
        orchestrator.submit(EngineProposal.model_validate(p.model_dump()))

    res = orchestrator.on_tick(
        current_tick=base_engine.tick,
        timestamp_epoch_ms=base_engine.simulated_epoch_ms,
    )
    decisions = res.get("decisions", [])

    # 2. Fork A: Coordinated
    fork_a = base_engine.fork()
    deltas_arbiter: List[Any] = []
    for d in decisions:
        prop = next(
            (p for p in proposals if p.command_id == d.command_id), None
        )
        if not prop:
            continue
        act = getattr(d.action, "value", str(d.action)).upper()
        if act == "APPROVE":
            deltas_arbiter.append(prop)
        elif act == "MODIFY":
            appr = (
                d.approved_value
                if d.approved_value is not None
                else prop.requested_value
            )
            param_str = str(getattr(prop.parameter, "value", prop.parameter))
            if "POWER" in param_str.upper():
                fork_a.cells[prop.target_cell_id].set_tx_power(appr)
            elif "SLEEP" in param_str.upper():
                fork_a.cells[prop.target_cell_id].set_sleep(appr >= 0.5)

    fork_a.apply_actuation_deltas(deltas_arbiter)

    # 3. Fork B: Uncoordinated
    fork_b = base_engine.fork()
    fork_b.apply_actuation_deltas(proposals)

    # 4. Step both forks
    max_drop_a = 0
    active_counts_a: List[int] = []
    energies_a: List[float] = []

    max_drop_b = 0
    active_counts_b: List[int] = []
    energies_b: List[float] = []

    for _ in range(horizon_ticks):
        s_a = fork_a.step()
        s_b = fork_b.step()
        max_drop_a = max(max_drop_a, sum(s.dropped_users for s in s_a))
        max_drop_b = max(max_drop_b, sum(s.dropped_users for s in s_b))
        active_counts_a.append(sum(s.active_users for s in s_a))
        active_counts_b.append(sum(s.active_users for s in s_b))
        energies_a.append(sum(s.energy_consumption_w for s in s_a))
        energies_b.append(sum(s.energy_consumption_w for s in s_b))

    avg_act_a = float(np.mean(active_counts_a)) if active_counts_a else 1.0
    avg_act_b = float(np.mean(active_counts_b)) if active_counts_b else 1.0

    drop_rate_a = min(1.0, max_drop_a / max(1.0, avg_act_a))
    drop_rate_b = min(1.0, max_drop_b / max(1.0, avg_act_b))
    cov_a = max(0.0, 1.0 - drop_rate_a)
    cov_b = max(0.0, 1.0 - drop_rate_b)

    calc = UtilityCalculator(weights=weights)
    u_a = calc.joint_utility(
        {
            "coverage_ratio": cov_a,
            "normalized_energy": min(1.0, np.mean(energies_a) / 125000.0),
            "normalized_interference": 0.25,
            "drop_rate_ratio": drop_rate_a,
        }
    )
    u_b = calc.joint_utility(
        {
            "coverage_ratio": cov_b,
            "normalized_energy": min(1.0, np.mean(energies_b) / 125000.0),
            "normalized_interference": 0.25,
            "drop_rate_ratio": drop_rate_b,
        }
    )

    dec_dicts = [
        {
            "command_id": d.command_id,
            "action": getattr(d.action, "value", str(d.action)),
            "approved_value": d.approved_value,
        }
        for d in decisions
    ]

    return u_a, u_b, drop_rate_a, cov_a, dec_dicts


def search_collisions(
    n_trials: int = 2000,
    seed: int = 1,
    weights: Optional[UtilityWeights] = None,
) -> Dict[str, Any]:
    """Execute collision discovery and record safety breaches & gallery."""
    rng = np.random.default_rng(seed)
    active_weights = weights or UtilityWeights(
        w1=0.30, w2=0.20, w3=0.20, w4=0.30
    )

    base_engine = SimulationEngine(
        topology="5_CELL_CORE",
        scenario="2_AM_COLLISION_QUIET_NIGHT",
        seed=seed,
        auto_snapshot=False,
    )
    for _ in range(5):
        base_engine.step()

    adj = {
        cid: cell.config.neighbor_cell_ids
        for cid, cell in base_engine.cells.items()
    }
    twin = _scenario_twin()
    orchestrator = QueueOrchestrator(
        twin=twin, adjacency=adj, max_workers=2
    )

    floor_breaches = 0
    underperforming = 0
    gallery: List[Dict[str, Any]] = []

    for trial_idx in range(n_trials):
        n_props = int(rng.integers(2, 7))  # 2 to 6 proposals
        proposals = [
            _random_proposal(
                rng,
                trial_idx,
                p_idx,
                base_engine.simulated_epoch_ms,
            )
            for p_idx in range(n_props)
        ]

        u_a, u_b, drop_rate_a, cov_a, decs = _evaluate_trial(
            base_engine, proposals, orchestrator, active_weights
        )

        is_breach = drop_rate_a > 0.02 or cov_a < 0.90
        is_under = u_a < u_b

        if is_breach:
            floor_breaches += 1
        if is_under:
            underperforming += 1

        severity = (drop_rate_a * 10.0) + max(0.0, u_b - u_a)
        scenario_record = {
            "trial_index": trial_idx,
            "trial_seed": seed + trial_idx,
            "proposals_count": n_props,
            "proposals": [
                {
                    "command_id": p.command_id,
                    "target_cell_id": p.target_cell_id,
                    "parameter": p.parameter.value,
                    "requested_value": p.requested_value,
                }
                for p in proposals
            ],
            "decisions": decs,
            "utility_arbiter": round(u_a, 3),
            "utility_baseline": round(u_b, 3),
            "utility_gap": round(u_b - u_a, 3),
            "drop_rate_arbiter": round(drop_rate_a, 4),
            "coverage_arbiter": round(cov_a, 4),
            "is_floor_breach": is_breach,
            "is_underperforming": is_under,
            "severity_score": round(severity, 4),
        }

        gallery.append(scenario_record)

    # Sort gallery by severity (highest first) and take top 5
    gallery.sort(key=lambda x: float(x["severity_score"]), reverse=True)
    worst_5 = gallery[:5]

    return {
        "n_tested": n_trials,
        "seed": seed,
        "floor_breaches": floor_breaches,
        "underperforming_cases": underperforming,
        "worst_scenarios": worst_5,
    }


def main() -> None:
    """CLI entrypoint for collision discovery."""
    parser = argparse.ArgumentParser(
        description="ARBITER Multi-Controller Collision Search"
    )
    parser.add_argument(
        "--n",
        type=int,
        default=2000,
        help="Number of proposals sets to test (default: 2000)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=1,
        help="PRNG seed for deterministic search (default: 1)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="",
        help="Path to save output JSON report",
    )
    args = parser.parse_args()

    print(
        f"[SEARCH] Running collision discovery "
        f"({args.n} trials, seed={args.seed})..."
    )
    start_time = time.perf_counter()
    report = search_collisions(n_trials=args.n, seed=args.seed)
    elapsed = time.perf_counter() - start_time
    rate = report["n_tested"] / max(1e-4, elapsed)

    print("\n" + "=" * 70)
    print(" ARBITER COLLISION DISCOVERY SEARCH REPORT")
    print("=" * 70)
    print(f"Trials Evaluated:          {report['n_tested']}")
    print(f"Random Seed:               {report['seed']}")
    print(f"Floor Breaches Detected:   {report['floor_breaches']}")
    print(f"Underperforming Cases:     {report['underperforming_cases']}")
    print(f"Search Execution Time:     {elapsed:.2f} s ({rate:.1f} trials/s)")
    print("-" * 70)
    print(" Top 5 Worst Scenarios (Replayable):")
    for idx, s in enumerate(report["worst_scenarios"], 1):
        print(
            f"  {idx}. Trial #{s['trial_index']} (Seed: {s['trial_seed']}) - "
            f"Props: {s['proposals_count']} | "
            f"U_Arb: {s['utility_arbiter']:.3f} | "
            f"U_Base: {s['utility_baseline']:.3f} | "
            f"DropRate: {s['drop_rate_arbiter']*100:.1f}%"
        )
    print("=" * 70)

    # Save output to specified file or default mock location
    out_path = args.output
    if not out_path:
        mocks_dir = Path(__file__).resolve().parent / "mocks"
        mocks_dir.mkdir(parents=True, exist_ok=True)
        out_path = str(mocks_dir / "collision_search_mock.json")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"[SEARCH] Saved report to {out_path}\n")


if __name__ == "__main__":
    main()
