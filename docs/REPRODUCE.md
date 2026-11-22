# Project ARBITER: Verification and Reproduction Guide

This guide provides exact, deterministic commands to reproduce all metrics, test passes, benchmarks, and captures cited in the ARBITER documentation.

All measurements in this repository are reproducible from a fresh clone without proprietary licenses or external cloud services.

---

## 1. System Requirements

- **Operating System**: Windows 10/11, Ubuntu 22.04 LTS, or macOS (Tested on Windows 11; Linux/macOS compatible).
- **Python**: 3.10, 3.11, or 3.12 (Virtual environment recommended).
- **Node.js**: v18.0.0 or higher (for frontend unit test execution).
- **RAM**: 4 GB minimum (8 GB recommended for parallel benchmarks).
- **Disk Space**: ~500 MB (including virtual environment and Playwright Chromium binary).

---

## 2. Clean Environment Setup

From an empty directory or fresh clone:

```powershell
# Clone the repository
git clone https://github.com/gsvenkatsai/Bits2Wave-Arbiter.git
cd Bits2Wave-Arbiter

# Check out the docs/readme branch
git checkout docs/readme

# Create and activate a clean virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1   # On Linux/macOS: source .venv/bin/activate

# Upgrade pip and install dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt

# (Optional) Install Playwright Chromium if re-running screenshot capture
python -m playwright install chromium
```

---

## 3. Verified Metrics Summary Table

<!-- METRICS:START -->
| Metric | Value | Unit | Status | Source Command |
|---|---|---|---|---|
| pytest_count | 579 | tests | MEASURED | `python -m pytest tests/ -q` |
| pytest_coverage | 87.0 | % | MEASURED | `python -m pytest --cov=arbiter --cov-report=term tests/ -q` |
| node_tests_count | 45 | tests | MEASURED | `node --test tests/branch4/*.js tests/site/*.js` |
| arbiter_decision_latency_mean | 4.17 | ms | MEASURED | `python -m arbiter.simulator.optimality --multi-seed --json` |
| target_ric_latency | 100 | ms | TARGET | O-RAN Near-RT RIC Control Loop Timing Spec (WG3) |
| optimality_gap_mean | 0.01 | % | MEASURED | `python -m arbiter.simulator.optimality --multi-seed --json` |
| optimality_gap_worst | 0.03 | % | MEASURED | `python -m arbiter.simulator.optimality --multi-seed --json` |
| brute_force_search_latency_mean | 311 | ms | MEASURED | `python -m arbiter.simulator.optimality --multi-seed --json` |
| conformal_coverage_nominal | 90.0 | % | TARGET | Conformal prediction configuration (alpha=0.10) |
| conformal_coverage_empirical | 90.0 | % | MEASURED | `python -m pytest tests/branch3/test_conformal_twin.py -k test_conformal_empirical_coverage_above_88_pct` |
| conformal_halfwidth_calibrated | 0.3 | dB | MEASURED | DigitalTwinEvaluator warmup on simulator state |
| collision_dropped_users_uncoord_peak | 14.5 | users | MEASURED | `python artifacts/readme/measure_10seeds.py` |
| collision_dropped_users_arbiter_peak | 0 | users | MEASURED | `python artifacts/readme/measure_10seeds.py` |
| collision_dropped_users_uncoord_unique | 14.5 | users | MEASURED | `python artifacts/readme/measure_10seeds.py` |
| collision_dropped_users_arbiter_unique | 0 | users | MEASURED | `python artifacts/readme/measure_10seeds.py` |
| collision_dropped_users_uncoord_summed | 1662.6 | user-ticks | MEASURED | `python artifacts/readme/measure_10seeds.py` |
| collision_dropped_users_arbiter_summed | 0 | user-ticks | MEASURED | `python artifacts/readme/measure_10seeds.py` |
| cell_b_sleep_uncoord_tick | 6 | tick | MEASURED | `python artifacts/readme/measure_10seeds.py` |
| cell_b_sleep_arbiter | Never (Protected) | state | MEASURED | `python artifacts/readme/measure_10seeds.py` |
| energy_none_200ticks_kj | 2 | kJ | MEASURED | `python artifacts/readme/measure_10seeds.py` |
| energy_uncoord_200ticks_kj | 1.99 | kJ | MEASURED | `python artifacts/readme/measure_10seeds.py` |
| energy_arbiter_200ticks_kj | 2 | kJ | MEASURED | `python artifacts/readme/measure_10seeds.py` |
| cluster_step_latency_200cells | 6.98 | ms | MEASURED | `python artifacts/assessment/phase3_scalability.py` |
| user_scaling_throughput_10k | 3139027 | UE/s | MEASURED | `python artifacts/assessment/phase3_scalability.py` |
| conflict_detector_latency_10p | 5.38 | ms | MEASURED | `python artifacts/assessment/phase3_scalability.py` |
| conflict_detector_latency_100p | 153.64 | ms | MEASURED | `python artifacts/assessment/phase3_scalability.py` |
| conflict_detector_limit_100ms | 75 | proposals | MEASURED | Interpolation of pairwise graph builder scaling curve |
| sqlite_write_throughput | 1320 | writes/s | MEASURED | `python artifacts/assessment/phase3_scalability.py` |
<!-- METRICS:END -->

---

## 4. Step-by-Step Reproduction Instructions

### 4.1 Unit and Integration Test Suites

Run the full Python test suite (579 tests):
```powershell
python -m pytest tests/ -q
```
*Expected result*: `579 passed in ~17-25s`.

Measure statement coverage:
```powershell
python -m pytest --cov=arbiter --cov-report=term tests/ -q
```
*Expected result*: `TOTAL 6451 stmts, 841 missed, 87% coverage`.

Run the JavaScript frontend and dashboard unit tests:
```powershell
node --test tests/branch4/*.js tests/site/*.js
```
*Expected result*: `ℹ tests 45, ℹ suites 0, ℹ pass 45, ℹ fail 0, ℹ cancelled 0, ℹ skipped 0, ℹ todo 0`.

---

### 4.2 Multi-Seed Optimality Gap Benchmark

Evaluates ARBITER's bisection ladder versus an exhaustive brute-force search over all parameter combinations across 10 random seeds (seeds 1 to 10):

```powershell
python -m arbiter.simulator.optimality --multi-seed --json
```

*Expected output structure*:
```json
{
  "multi_seed": true,
  "seeds_evaluated": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
  "mean_gap_pct": 0.01,
  "worst_gap_pct": 0.03,
  "mean_arbiter_ms": 4.17,
  "mean_optimum_s": 0.311
}
```

---

### 4.3 10-Seed 2 AM Collision Simulation

Simulates 120 ticks of the 2 AM collision under three regimes (Uncoordinated Baseline, ARBITER Coordinated, and Idle None) across 10 seeds:

```powershell
python artifacts/readme/measure_10seeds.py
```

*Expected summary*:
- **Uncoordinated**: Mean peak drops = 14.5 users, Mean cumulative drops = 1,662.6 user-ticks. Cell B sleeps at tick 6.
- **ARBITER**: Mean peak drops = 0.0 users, Mean cumulative drops = 0.0 user-ticks. Cell B never sleeps (`HOLD_POLICY` triggered).

---

### 4.4 Conformal Prediction Empirical Coverage

Verifies that the conformal prediction calibration set maintains $\ge 88\%$ empirical coverage under an error budget $\alpha = 0.10$ ($90\%$ nominal confidence):

```powershell
python -m pytest tests/branch3/test_conformal_twin.py -k test_conformal_empirical_coverage_above_88_pct -v
```

*Expected result*: `1 passed`.

---

### 4.5 Scalability and Limits Benchmark

Runs performance profiling across cluster size, user density, conflict proposal volume, and database persistence:

```powershell
python artifacts/assessment/phase3_scalability.py
```

*Expected outputs*:
- 200 cells step latency $\approx 6.98\text{ ms}$
- 10,000 UEs pathloss computation $\approx 3.14\times 10^6\text{ UE/s}$
- Conflict detection latency:
  - 10 proposals: $5.38\text{ ms}$
  - 100 proposals: $153.64\text{ ms}$
  - 100 ms RIC loop deadline threshold: $\approx 75\text{ proposals/window}$.

---

## 5. Live Demo Stack Execution

To start the interactive web application, live 2D NOC, and 3D digital twin:

```powershell
# Start server in background
.\scripts\run_demo.ps1
```

Once running, access:
- **Landing Page**: [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- **Interactive 2D NOC Operations Console**: [http://127.0.0.1:8000/dashboard/](http://127.0.0.1:8000/dashboard/)
- **Real-Time 3D Digital Twin Visualizer**: [http://127.0.0.1:8000/dashboard/3d](http://127.0.0.1:8000/dashboard/3d)
- **Interactive OpenAPI Documentation**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

To stop the background server:
```powershell
.\scripts\stop_demo.ps1
```

---

## 6. Updating and Validating Documentation

To synchronize metric tables across documentation files from `artifacts/readme/metrics.json`:
```powershell
python scripts/update_readme_metrics.py
```

To validate that the README and all documentation satisfy every constraint (no broken links, real assets, no banned buzzwords, matching metrics):
```powershell
python scripts/check_readme.py
```
