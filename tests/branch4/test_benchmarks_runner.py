"""
tests/branch4/test_benchmarks_runner.py
BenchmarkRunner, AblationSuite, and MonteCarloScenarioHarness tests — Branch 4.

Sources:
  Branch 4 Spec §5.2 page 7:
    - 10 fixed PRNG seeds: seed=101..110
    - CI_95 formula
    - Sub-60s execution requirement (Spec §7.2 page 8)
  Branch 4 Spec §6 page 7:
    - SYNTHETIC generator explicitly labeled "synthetic"
  Branch 4 Spec §2.2 FR-9.3 page 3:
    - AblationSuite: no-twin and no-modify configurations
"""
from __future__ import annotations

import time
from fastapi.testclient import TestClient

from arbiter.api.server import app
from arbiter.benchmarks.ablations import AblationSuite
from arbiter.benchmarks.runner import BenchmarkResult, BenchmarkRunner
from arbiter.benchmarks.scenarios import (
    DEFAULT_SEEDS,
    MODE_ARBITER,
    MODE_NO_MODIFY,
    MODE_NO_TWIN,
    MODE_STATIC_PRIORITY,
    MODE_UNCOORDINATED,
    SCENARIO_2_AM_COLLISION,
    SCENARIO_PEAK,
    SCENARIO_SURGE,
    SYNTHETIC_DATA_SOURCE,
    generate_synthetic_run,
)


class TestBenchmarkRunner:

    def test_default_seeds_are_exact_101_to_110(self):
        """
        Spec §5.2 Page 7:
          'The benchmarking harness executes 10 distinct simulation runs using fixed PRNG seeds (seed=101..110)'
        """
        assert DEFAULT_SEEDS == [101, 102, 103, 104, 105, 106, 107, 108, 109, 110]
        assert len(DEFAULT_SEEDS) == 10

    def test_synthetic_label_present_in_every_result(self):
        """
        Spec §6 Page 7:
          All synthetic runs must carry 'data_source': 'synthetic'.
        """
        runner = BenchmarkRunner()
        results = runner.run(
            scenario=SCENARIO_2_AM_COLLISION,
            modes=[MODE_UNCOORDINATED, MODE_STATIC_PRIORITY, MODE_ARBITER],
            seeds=[101, 102],
        )
        for mode_name, res in results.items():
            assert res.data_source == SYNTHETIC_DATA_SOURCE
            assert res.data_source == "synthetic"
            dict_rep = res.model_dump()
            assert dict_rep["data_source"] == "synthetic"

    def test_deterministic_reproducibility(self):
        """
        Identical PRNG seed produces identical KPI outputs (NFR-14).
        """
        run1 = generate_synthetic_run(scenario=SCENARIO_2_AM_COLLISION, mode=MODE_ARBITER, seed=105)
        run2 = generate_synthetic_run(scenario=SCENARIO_2_AM_COLLISION, mode=MODE_ARBITER, seed=105)
        assert run1 == run2

        # Different seeds produce varying values
        run3 = generate_synthetic_run(scenario=SCENARIO_2_AM_COLLISION, mode=MODE_ARBITER, seed=106)
        assert run1["dropped_users"] != run3["dropped_users"]

    def test_execution_time_under_60_seconds(self):
        """
        Spec §7.2 Page 8:
          '[x] Monte Carlo harness executes 10 seeds across 3 scenarios in < 60 seconds.'
        """
        runner = BenchmarkRunner()
        t0 = time.perf_counter()
        for sc in [SCENARIO_2_AM_COLLISION, SCENARIO_PEAK, SCENARIO_SURGE]:
            runner.run(scenario=sc, seeds=DEFAULT_SEEDS)
        elapsed = time.perf_counter() - t0
        assert elapsed < 60.0, f"Execution took too long: {elapsed:.2f} s >= 60 s"

    def test_benchmark_result_schema_fields(self):
        """
        BenchmarkResult must contain scenario, mode, seeds, data_source, metrics, nfr_evaluations.
        """
        runner = BenchmarkRunner()
        results = runner.run(
            scenario=SCENARIO_2_AM_COLLISION,
            modes=[MODE_STATIC_PRIORITY, MODE_ARBITER],
            seeds=[101, 102, 103],
        )
        arb_res = results[MODE_ARBITER]
        assert arb_res.scenario == SCENARIO_2_AM_COLLISION
        assert arb_res.mode == MODE_ARBITER
        assert arb_res.seeds == [101, 102, 103]
        assert arb_res.data_source == "synthetic"
        assert "dropped_users" in arb_res.metrics
        assert "energy_consumption_w" in arb_res.metrics
        assert "oscillations_per_10min" in arb_res.metrics
        assert "coverage_ratio" in arb_res.metrics
        assert "decision_latency_p95_ms" in arb_res.metrics

        # MetricSummary sub-fields
        drop_summary = arb_res.metrics["dropped_users"]
        assert hasattr(drop_summary, "mean")
        assert hasattr(drop_summary, "ci_lower")
        assert hasattr(drop_summary, "ci_upper")
        assert hasattr(drop_summary, "std_dev")
        assert len(drop_summary.samples) == 3

    def test_nfr_hypotheses_validated_on_arbiter_in_2am_scenario(self):
        """
        In 2_AM_COLLISION, ARBITER meets all hypotheses against static priority:
        NFR-1, NFR-5, NFR-6, NFR-7, NFR-8.
        """
        runner = BenchmarkRunner()
        results = runner.run(
            scenario=SCENARIO_2_AM_COLLISION,
            modes=[MODE_STATIC_PRIORITY, MODE_ARBITER],
            seeds=DEFAULT_SEEDS,
        )
        nfr = results[MODE_ARBITER].nfr_evaluations
        assert nfr["nfr_1_latency_p95"] is True
        assert nfr["nfr_5_drop_reduction"] is True
        assert nfr["nfr_6_night_energy_savings"] is True
        assert nfr["nfr_7_oscillation_suppression"] is True
        assert nfr["nfr_8_coverage_floor"] is True


class TestAblationSuite:

    def test_ablation_suite_returns_same_benchmark_result_shape(self):
        """
        FR-9.3 (Spec §2.2 page 3):
          AblationSuite must evaluate NO_TWIN, NO_MODIFY, and ARBITER,
          returning the exact same dict[str, BenchmarkResult] schema as BenchmarkRunner.
        """
        suite = AblationSuite()
        res = suite.run(scenario=SCENARIO_2_AM_COLLISION, seeds=[101, 102])
        assert set(res.keys()) == {MODE_NO_TWIN, MODE_NO_MODIFY, MODE_ARBITER}
        for mode_name, result in res.items():
            assert isinstance(result, BenchmarkResult)
            assert result.data_source == "synthetic"
            assert "dropped_users" in result.metrics
            assert "twin_mape_pct" in result.metrics


class TestRouteBenchmarksRunIntegration:

    def test_post_benchmarks_run_executes_and_returns_synthetic_results(self):
        """
        POST /api/v1/benchmarks/run executes BenchmarkRunner and returns
        conforming payload with 'data_source': 'synthetic'.
        """
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/benchmarks/run",
                json={
                    "scenario": "2_AM_COLLISION",
                    "seeds_count": 3,
                    "modes": ["STATIC_PRIORITY", "ARBITER"],
                },
            )
            assert resp.status_code == 202
            body = resp.json()
            assert body["status"] == "accepted"
            assert body["scenario"] == "2_AM_COLLISION"
            assert "results" in body
            assert "ARBITER" in body["results"]
            assert body["results"]["ARBITER"]["data_source"] == "synthetic"
            assert len(body["results"]["ARBITER"]["seeds"]) == 3
