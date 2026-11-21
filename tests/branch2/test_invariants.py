"""Seeded fuzz audit of the Branch 2 invariants (Task 6, Definition of Done).

500 random batches are pushed through the real queue, detector and ladder. For
every decision the test asserts the safety and bookkeeping invariants:

* ``hold_count`` never exceeds ``MAX_HOLD_TICKS`` and a command is never held
  twice in one tick or parked twice by the queue;
* no decision spends more than ``MAX_TWIN_EVALS`` twin evaluations;
* no APPROVE/MODIFY value breaches the coverage/drop-rate floors when replayed
  against the twin;
* every decision carries a reason code and a finite delta utility;
* emergency commands never reach the twin at all.
"""
from __future__ import annotations

import itertools
import math
import random
from typing import Any, Dict, Iterable, List, Tuple

from arbiter.engine.contracts import (
    MAX_HOLD_TICKS,
    MAX_TWIN_EVALS,
    ActionType,
    CommandProposal,
    ReasonCode,
)
from arbiter.engine.mocks import ScriptedTwin
from arbiter.queue import QueueOrchestrator

SEED = 20261121
BATCHES = 500
HEX = {
    "A": ["B"],
    "B": ["A", "C"],
    "C": ["B", "D"],
    "D": ["C", "E"],
    "E": ["D"],
}

#: (parameter, current, requested, is_binary, jitter)
PARAMETERS: Tuple[Tuple[str, float, float, bool], ...] = (
    ("TX_POWER_DBM", 30.0, 33.0, False),
    ("TILT_DEG", 4.0, 2.0, False),
    ("OFFLOAD_RATIO", 0.0, 0.4, False),
    ("SLEEP_STATE", 0.0, 1.0, True),
)


def _curve(candidate: List[dict[str, Any]]) -> Dict[str, float]:
    """Deterministic KPI curve used by both the arbitrated and replay twins."""
    coverage = 0.95
    energy = 0.50
    interference = 0.30
    drop_rate = 0.01
    for delta in candidate:
        parameter = str(delta.get("parameter", "")).upper()
        to_value = float(delta.get("to_value", 0.0))
        if parameter == "SLEEP_STATE":
            coverage -= 0.20 * to_value
            energy -= 0.20 * to_value
            interference -= 0.10 * to_value
            drop_rate += 0.02 * to_value
        elif parameter == "OFFLOAD_RATIO":
            coverage -= 0.02 * to_value
            energy -= 0.30 * to_value
            interference += 0.05 * to_value
            drop_rate += 0.15 * to_value * to_value
        elif parameter == "TX_POWER_DBM":
            step = to_value - 30.0
            coverage += 0.05 * step
            energy += 0.02 * step
            interference += 0.02 * step
        elif parameter == "TILT_DEG":
            step = 4.0 - to_value
            coverage += 0.01 * step
            interference -= 0.03 * step
    return {
        "coverage_ratio": max(0.0, min(1.0, coverage)),
        "normalized_energy": max(0.0, min(1.0, energy)),
        "normalized_interference": max(0.0, min(1.0, interference)),
        "drop_rate_ratio": max(0.0, min(1.0, drop_rate)),
    }


def _random_command(rng: random.Random, ids: Iterable[int]) -> CommandProposal:
    parameter, current, requested, is_binary = rng.choice(PARAMETERS)
    cell = rng.choice(list(HEX))
    value = requested if is_binary else requested + rng.uniform(-0.1, 0.1)
    handover_from: str | None = None
    handover_to: str | None = None
    if parameter == "OFFLOAD_RATIO":
        handover_from = cell
        handover_to = rng.choice([peer for peer in HEX[cell]])
    return CommandProposal(
        command_id=f"fuzz-{next(ids)}",
        controller=f"ctrl-{parameter.lower()}",
        target_cell=cell,
        parameter=parameter,
        current_value=current,
        requested_value=value,
        is_emergency=rng.random() < 0.08,
        is_binary=is_binary,
        created_tick=rng.randint(0, 3),
        handover_from=handover_from,
        handover_to=handover_to,
    )


def _decisions(result: Dict[str, Any]) -> List[Any]:
    return list(result["decisions"])


def test_random_batches_preserve_all_invariants() -> None:
    rng = random.Random(SEED)
    ids = itertools.count(1)
    verifier = ScriptedTwin(_curve)
    command_index: Dict[str, CommandProposal] = {}
    actions: set[ActionType] = set()

    for _ in range(BATCHES):
        count = rng.randint(1, 5)
        commands = [_random_command(rng, ids) for _ in range(count)]
        for command in commands:
            command_index[command.command_id] = command

        emergency_twin = ScriptedTwin(_curve)
        orchestrator = QueueOrchestrator(twin=emergency_twin, adjacency=HEX)
        for command in commands:
            assert orchestrator.submit(command) is True

        for tick in range(1, 6):
            result = orchestrator.on_tick(current_tick=tick, timestamp_epoch_ms=tick * 100)
            decisions = _decisions(result)

            seen: set[str] = set()
            for decision in decisions:
                actions.add(decision.action)
                # A command is decided at most once per tick.
                assert decision.command_id not in seen
                seen.add(decision.command_id)

                # Hold budget and evaluation budget.
                assert 0 <= decision.hold_count <= MAX_HOLD_TICKS
                assert 0 <= decision.twin_eval_count <= MAX_TWIN_EVALS

                # Audit fields are always populated.
                assert isinstance(decision.reason_code, ReasonCode)
                assert isinstance(decision.delta_utility, float)
                assert math.isfinite(decision.delta_utility)

                # Floors hold for every applied value, replayed standalone.
                if decision.action in (ActionType.APPROVE, ActionType.MODIFY):
                    assert decision.approved_value is not None
                    command = command_index[decision.command_id]
                    kpis = verifier.evaluate(
                        [
                            {
                                "command_id": command.command_id,
                                "cell_id": command.target_cell,
                                "parameter": command.parameter.strip().upper(),
                                "from_value": float(command.current_value),
                                "to_value": float(decision.approved_value),
                            }
                        ]
                    )["predicted_kpis"]
                    assert kpis["coverage_ratio"] >= 0.90
                    assert kpis["drop_rate_ratio"] <= 0.02

            # The queue agrees that no command is held twice or over budget.
            orchestrator.queue.check_invariants()
            if not orchestrator.queue.is_empty:
                assert orchestrator.queue.delayed_count >= 1

        # Emergency commands are approved by the bypass lane, never by the twin.
        emergencies = [command for command in commands if command.is_emergency]
        assert orchestrator.queue.emergency_lane.audit_count == len(emergencies)

    # The audit must not be vacuous: every rung was actually exercised.
    assert actions == set(ActionType)


def test_emergency_only_batches_never_call_the_twin() -> None:
    rng = random.Random(SEED + 1)
    ids = itertools.count(10_000)

    for _ in range(25):
        twin = ScriptedTwin(_curve)
        orchestrator = QueueOrchestrator(twin=twin, adjacency=HEX)
        commands = []
        for _ in range(rng.randint(1, 4)):
            command = _random_command(rng, ids).model_copy(update={"is_emergency": True})
            commands.append(command)
            orchestrator.submit(command)

        result = orchestrator.on_tick(current_tick=1, timestamp_epoch_ms=100)

        assert result["decisions_count"] == 0
        assert twin.call_count == 0
        assert orchestrator.queue.emergency_lane.audit_count == len(commands)
        assert orchestrator.queue.is_empty
