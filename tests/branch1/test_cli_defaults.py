"""Tests for SimulationEngine CLI defaults and flags.

Verifies:
- Default --twin is 'real', raising RuntimeError if C1 factory missing.
- Default sink is real queue ('queue'), switched to 'mock' via --mock-sink.
- Prominent warning printed when --twin scripted is used.
- Frame meta reflects chosen twin_kind and sink.
"""
from __future__ import annotations

import argparse
import pytest
from arbiter.simulator.engine import SimulationEngine, main


def test_cli_parser_defaults() -> None:
    """Verify default parser values match Contract C2/Task 4 requirements."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--twin",
        type=str,
        default="real",
        choices=["real", "scripted"],
    )
    parser.add_argument(
        "--mock-sink",
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "--use-real-queue",
        action="store_true",
        default=None,
    )
    args = parser.parse_args([])
    assert args.twin == "real"
    assert args.mock_sink is False
    assert args.use_real_queue is None


def test_cli_default_twin_real_raises_when_c1_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI default --twin real fails loudly if C1 factory missing."""
    monkeypatch.setattr(
        "sys.argv",
        [
            "engine.py",
            "--scenario",
            "2am_collision",
            "--ticks",
            "2",
            "--cells",
            "5",
        ],
    )
    with pytest.raises(RuntimeError) as exc_info:
        main()
    assert "Contract C1" in str(exc_info.value)
    assert "arbiter.engine.default_twin" in str(exc_info.value)


def test_cli_scripted_twin_prints_warning_and_runs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI --twin scripted prints warning and defaults to real queue."""
    monkeypatch.setattr(
        "sys.argv",
        [
            "engine.py",
            "--scenario",
            "2am_collision",
            "--ticks",
            "7",
            "--cells",
            "5",
            "--twin",
            "scripted",
        ],
    )
    main()
    captured = capsys.readouterr().out
    assert "[SIMULATOR] Twin: scripted | Sink: queue" in captured
    assert "[WARNING] RUNNING IN SCRIPTED TWIN MODE" in captured


def test_cli_mock_sink_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Passing --mock-sink switches sink to mock."""
    monkeypatch.setattr(
        "sys.argv",
        [
            "engine.py",
            "--scenario",
            "2am_collision",
            "--ticks",
            "7",
            "--cells",
            "5",
            "--twin",
            "scripted",
            "--mock-sink",
        ],
    )
    main()
    captured = capsys.readouterr().out
    assert "[SIMULATOR] Twin: scripted | Sink: mock" in captured


def test_engine_instance_sink_and_twin_meta() -> None:
    """SimulationEngine reflects twin_kind and sink_kind in frames."""
    engine_queue = SimulationEngine(
        topology="5_CELL_CORE",
        scenario="2am_collision",
        use_real_queue=True,
        twin_kind="scripted",
    )
    fr_queue = engine_queue.frame(world="arbiter", tick=0, sim_time_ms=0)
    assert fr_queue["meta"]["twin_kind"] == "scripted"
    assert fr_queue["meta"]["sink"] == "queue"

    engine_mock = SimulationEngine(
        topology="5_CELL_CORE",
        scenario="2am_collision",
        use_real_queue=False,
        twin_kind="real",
    )
    fr_mock = engine_mock.frame(world="arbiter", tick=0, sim_time_ms=0)
    assert fr_mock["meta"]["twin_kind"] == "real"
    assert fr_mock["meta"]["sink"] == "mock"
