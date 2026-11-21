"""Shared pytest configuration for Branch 2 tests."""
from __future__ import annotations

import itertools
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # pragma: no cover - import bootstrap only
    sys.path.insert(0, str(ROOT))

from arbiter.engine.contracts import (  # noqa: E402
    CommandProposal,
    ControllerType,
    ParameterType,
)

ProposalFactory = Callable[..., CommandProposal]


@pytest.fixture
def make_proposal() -> ProposalFactory:
    """Factory producing valid proposals with per-test overrides."""
    counter = itertools.count(1)

    def _make(**overrides: Any) -> CommandProposal:
        payload: dict[str, Any] = {
            "command_id": f"cmd-{next(counter):03d}",
            "controller_id": "ctrl-energy",
            "controller_type": ControllerType.ENERGY_SAVING,
            "target_cell_id": "cell-a",
            "parameter": ParameterType.TX_POWER_DBM,
            "requested_value": 40.0,
            "emergency_flag": False,
            "timestamp_epoch_ms": 1000,
            "hold_count": 0,
        }
        payload.update(overrides)
        return CommandProposal(**payload)

    return _make
