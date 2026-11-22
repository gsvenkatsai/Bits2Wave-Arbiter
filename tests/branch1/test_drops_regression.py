"""Regression tests for drop accounting and 2 AM collision arbitration.

Task 1 finding: Idle cells with 0.0 Mbps throughput (no attached users)
must never be counted as dropped users. ARBITER's arbitration prevents
Cell B from sleeping while carrying load, maintaining 0 physical drops.
"""
from __future__ import annotations

from arbiter.controllers.energy_saving import EnergySavingController
from arbiter.controllers.scenarios import trigger_2am_collision
from arbiter.engine.contracts import CommandProposal as EngineProposal
from arbiter.queue.orchestrator import QueueOrchestrator
from arbiter.simulator.engine import SimulationEngine
from arbiter.simulator.toy_twin import get_scripted_twin


def test_idle_zero_throughput_cell_not_dropped() -> None:
    """Zero throughput on an empty cell must not produce dropped users."""
    engine = SimulationEngine(
        topology="5_CELL_CORE",
        scenario="2am_collision",
        seed=101,
        use_real_queue=True,
    )
    samples = engine.step()
    for s in samples:
        if s.active_users == 0:
            assert s.throughput_mbps == 0.0
            assert s.dropped_users == 0, (
                f"Idle cell {s.cell_id} with 0 users reported drops!"
            )


def test_2am_collision_zero_drops_across_seeds() -> None:
    """ARBITER arbitration produces strictly 0 physical drops across seeds."""
    for seed in [101, 102, 105, 108]:
        engine = SimulationEngine(
            topology="5_CELL_CORE",
            scenario="2am_collision",
            seed=seed,
            use_real_queue=True,
            twin_kind="scripted",
        )
        ctrl_es = EnergySavingController()

        for t in range(25):
            samples = engine.step()
            loads = engine.get_cell_loads()
            sleeps = {cid: c.is_sleeping for cid, c in engine.cells.items()}

            if t < 6:
                ctrl_es.evaluate_telemetry(
                    loads, current_tick=t, cell_sleep_states=sleeps
                )

            if t == 6:
                proposals = trigger_2am_collision(
                    engine=engine, base_ts_ms=engine.simulated_epoch_ms
                )
                adj = {
                    cid: cell.config.neighbor_cell_ids
                    for cid, cell in engine.cells.items()
                }
                orchestrator = QueueOrchestrator(
                    twin=get_scripted_twin(), adjacency=adj, max_workers=2
                )
                for p in proposals:
                    orchestrator.submit(
                        EngineProposal.model_validate(p.model_dump())
                    )

                res = orchestrator.on_tick(
                    current_tick=t,
                    timestamp_epoch_ms=engine.simulated_epoch_ms,
                )
                for d in res["decisions"]:
                    prop = next(
                        (p for p in proposals if p.command_id == d.command_id),
                        None,
                    )
                    if not prop:
                        continue
                    norm_p = str(
                        getattr(prop.parameter, "value", prop.parameter)
                    ).upper()
                    target_cid = prop.target_cell_id
                    if (
                        d.action.value == "MODIFY"
                        and d.approved_value is not None
                    ):
                        if "POWER" in norm_p and target_cid in engine.cells:
                            engine.cells[target_cid].set_tx_power(
                                d.approved_value
                            )
                        elif "OFFLOAD" in norm_p:
                            engine.apply_actuation_deltas([{
                                "target_cell_id": target_cid,
                                "parameter": "OFFLOAD_RATIO",
                                "value": d.approved_value,
                            }])

            # Physical drop count per tick must remain 0
            tick_drops = sum(s.dropped_users for s in samples)
            assert tick_drops == 0, (
                f"Seed {seed} tick {t} had {tick_drops} drops!"
            )

        # Cell B must never have slept
        assert not engine.cells["cell_B"].is_sleeping, (
            f"Seed {seed}: Cell B was put to sleep prematurely!"
        )
