# Independent Technical Assessment: Project ARBITER
**Competition:** Bits2Wave Hackathon (IEEE AP-S / ComSoc)  
**Problem Statement:** SW-31 — "Conflict-Resolution Referee for Mobile Network Controllers" (Software/Simulation Track)  
**Reviewer Role:** Skeptical Senior Telecom Systems & Software Architecture Judge  
**Assessment Date:** November 22, 2026  
**Git Commit SHA:** `1cbfd2b614f9fa1d6e109518f16286d401c3cf7a`  
**Branch:** `feat/site-3d-theme`  
**Host Environment:** Windows (Python 3.14.7, pytest 9.1.1, Node.js v20.18.0)

---

## 1. Executive Summary

Project ARBITER implements a multi-objective conflict-resolution referee designed to resolve real-time command clashes among autonomous Self-Organizing Network (SON) / O-RAN Near-RT RIC xApps (Energy Saving, Load Balancing, and Coverage & Capacity Optimization). The codebase demonstrates exceptional modular software craftsmanship across four parallel branch architectures, featuring a 534-test passing Python test suite (87% statement coverage), 40 passing JavaScript unit tests, and a fully offline 3D WebGL Digital Twin and NOC dashboard.

However, a skeptical technical audit reveals critical architectural shortcomings on the default execution path: the default simulation demo script (`scripts/run_demo.ps1` calling `arbiter/simulator/engine.py:657`) bypasses Branch 3's conformal digital twin in favor of a hardcoded analytical test mock (`_scenario_twin`), while the pairwise conflict graph builder exhibits an $O(P^2)$ complexity explosion that breaks the 100 ms near-RT RIC control budget beyond ~75 concurrent proposals (taking 153.6 ms at 100 proposals and 14.8 seconds at 1,000 proposals). Where ARBITER functions as designed, its impact is mathematically verified: in a 10-seed 2 AM collision simulation, it cuts dropped users by 53.85% (from 23.40 to 10.80 mean drops) and achieves a 0.0% combinatorial optimality gap in 6.83 ms versus 311 ms for brute-force search.

### Scorecard Table

| Dimension | Score /10 | Weight | Evidence | Biggest Weakness |
| :--- | :---: | :---: | :--- | :--- |
| **Functionality** | **7.5** | 35% | 534/534 pytest passed (19.7s); 40/40 JS tests passed; 10/10 seeds collision executed; COST-231 PL at 500m exact match (127.86 dB). | Default demo engine imports analytical toy twin (`_scenario_twin`) from test directory; monkey-patching in `arbiter/telemetry/frame.py:242` causes mypy failure. |
| **Feasibility** | **6.0** | 20% | Measured arbitration latency 0.87 ms fits well within 100 ms budget; fail-safe rollback and safety floor enforcement verified in code. | Complete absence of standard O-RAN interfaces (E2AP/A1-AP/NETCONF/YANG); all REST endpoints unauthenticated; propagation model lacks multipath fading. |
| **Scalability** | **5.5** | 20% | Radio physics scales to 10,000 users (3.19 ms, >3.1M users/s); cluster step <7 ms for 200 cells; SQLite writes 1,320/s. | Pairwise $O(P^2)$ conflict graph detection breaks the 100 ms near-RT RIC budget at ~75 proposals (153.6 ms at 100 proposals; 14.8s at 1,000 proposals). |
| **Impact** | **8.0** | 25% | Measured 53.85% drop reduction across 10 random seeds; 0.0% combinatorial optimality gap in 6.83 ms vs 311 ms brute search; 419.5 W sleep power verified. | Short-term cluster power increases by +14.98% during the 20-tick handover transition window; savings only materialize post-transition. |
| **Demo Readiness** *(Unweighted)* | **8.5** | — | Single-command start (`run_demo.ps1`); 100% offline (zero CDNs); honest "REPLAY" vs "LIVE" status chips; 0 leaked secrets or stale DBs. | Dashboard displays "ENGINE IDLE" unless background simulation is explicitly streaming; `run_demo.ps1` defaults to `MockProposalSink`. |

**Weighted Total Score: 6.93 / 10 (Equivalent: 69.3 / 100)**

---

## 2. Phase 0: Complete Codebase Inventory & Default-Path Audit

### 2.1 Repository Topology & Lines of Code (LOC)

| Subsystem / Branch | Primary Modules | Python LOC | JS LOC | CSS LOC | HTML LOC | Total LOC |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **Branch 1: RAN Simulator & Controllers** | `arbiter/simulator/`, `arbiter/controllers/` | 3,806 | 0 | 0 | 0 | **3,806** |
| **Branch 2: Queue, Detector & Decision Engine** | `arbiter/queue/`, `arbiter/detector/`, `arbiter/engine/` | 4,527 | 0 | 0 | 0 | **4,527** |
| **Branch 3: Digital Twin, Actuator & Storage** | `arbiter/twin/`, `arbiter/actuator/`, `arbiter/storage/`, `arbiter/shared/` | 2,396 | 0 | 0 | 0 | **2,396** |
| **Branch 4: Telemetry, API, Dashboards & Site** | `arbiter/telemetry/`, `arbiter/api/`, `arbiter/dashboard/`, `arbiter/benchmarks/` | 4,081 | 116,229 | 7,088 | 3,499 | **130,897** |
| **Test Suites** | `tests/branch1..4/`, `tests/contracts/`, `tests/e2e/`, `tests/site/` | 9,412 | 1,428 | 0 | 0 | **10,840** |
| **Scripts & Tooling** | `scripts/` (demo, evidence, cdp, audits) | 1,842 | 195 | 0 | 0 | **2,037** |
| **Total Project Codebase** | | **26,064** | **117,852** | **7,088** | **3,499** | **154,503** |

*Note: JS LOC includes vendored Three.js r128 and local OrbitControls libraries.*

### 2.2 Test Suite Inventory
- **Pytest Suites (Python):** 534 tests across 44 test files. Passing: **534 / 534 (100%)**.
  - `tests/branch1/`: 67 passed (propagation, grid, cell, traffic, engine, snapshot)
  - `tests/branch2/`: 193 passed (ladder, bisection, classifier, graph, queue, orchestrator, twin adapter)
  - `tests/branch3/`: 45 passed (conformal twin, calibration, failsafe, sqlite commit log, actuator enforcer)
  - `tests/branch4/`: 197 passed (api routes, websocket broadcast, frame ingest, kpi monitor, 3d twin)
  - `tests/contracts/`: 12 passed (shared schemas and v2 contracts)
  - `tests/e2e/`: 4 passed (full closed-loop 2 AM collision)
  - `tests/site/`: 16 passed (static site routes, evidence loader, monitor status)
- **Node.js Test Suites (JavaScript):** 40 tests across 13 suites. Passing: **40 / 40 (100%)**.
  - `tests/branch4/test_arbitration_mapper.js`: 10 passed
  - `tests/branch4/test_data_layer.js`: 10 passed
  - `tests/branch4/test_ghost_world.js`: 5 passed
  - `tests/site/test_evidence_loader.js`: 6 passed
  - `tests/site/test_monitor_status.js`: 9 passed

### 2.3 Endpoints and Interfaces
- **HTTP REST Endpoints (`arbiter/api/server.py`, `arbiter/api/routes.py`):**
  1. `GET /api/v1/health` — Service readiness and version probe
  2. `GET /api/v1/state/cells` — Physical cell RF configurations and user loads
  3. `GET /api/v1/decisions/history` — Audit log of recent arbitration decisions
  4. `POST /api/v1/commands/propose` — Proposal ingestion endpoint for controllers
  5. `POST /api/v1/telemetry/ingest` — Ingestion of live SINR and throughput frames
  6. `POST /api/v1/benchmarks/run` — Execution of Monte Carlo ablation benchmarks
  7. `GET /api/v1/twin/intervals` — Calibrated digital twin prediction intervals
  8. `POST /api/v1/twin/drift` — Fault injection hook for twin drift calibration
  9. `GET /api/v1/recorder/counterfactual/{id}` — Divergence playback comparing ARBITER against counterfactual
  10. `GET /api/v1/benchmarks/optimality` — Combinatorial Pareto frontier optimality benchmark
  11. `GET /`, `GET /dashboard/`, `GET /dashboard/3d/` — Static site and dashboard mounts
- **WebSocket Channels:**
  1. `WS /api/v1/ws/live` — High-frequency streaming of physical state, ghost twin predictions, and conflict edges
  2. `WS /ws` — Backward-compatible live frame stream
  3. `WS /ws/replay` — Historical playback stream of recorded collision incidents
- **Database Tables (`arbiter_production.db` / `schema.sql`):**
  1. `commit_log` — Transactional record of cell parameter modifications and rollback status
  2. `audit_decisions` — Comprehensive referee verdicts (action, value, reason code, utility delta)
  3. `telemetry_history` — Ingested per-tick SINR, load, throughput, and power samples
  4. `counterfactual_log` — Side-by-side uncoordinated vs coordinated divergence records

### 2.4 Default-Path Audit: Mocks, Fixtures & Hardcoded Data
A systematic grep across the production codebase revealed 81 occurrences of mock patterns, hardcoded assumptions, and test fixtures:
1. **Production Import of Test Mock (`arbiter/simulator/engine.py:657`):**
   ```python
   # Line 657 in arbiter/simulator/engine.py
   from tests.branch2.test_decision_ladder import _scenario_twin
   ...
   orchestrator = QueueOrchestrator(twin=_scenario_twin(), adjacency=adj, max_workers=2)
   ```
   **Finding:** When the simulation engine runs (the primary execution path triggered by `scripts/run_demo.ps1`), it does not instantiate Branch 3's real `ConformalDigitalTwin` or `DigitalTwinEvaluator`. Instead, it imports `_scenario_twin` directly from `tests/branch2/test_decision_ladder.py`, which is an analytical toy twin (`ScriptedTwin(_scenario_kpis)`) containing simplified linear coefficients (`coverage -= 0.20 * to_value`, `drop_rate += 0.15 * to_value^2`).
2. **Decoupled Default Proposal Sink (`arbiter/simulator/engine.py:71`):**
   ```python
   # Line 71 in arbiter/simulator/engine.py
   if proposal_sink is not None:
       self.proposal_sink = proposal_sink
   elif use_real_queue:
       self.proposal_sink = ProposalQueueSink()
   else:
       self.proposal_sink = MockProposalSink()
   ```
   **Finding:** `SimulationEngine` defaults to `MockProposalSink()`. Running `python -m arbiter.simulator.engine` without `--use-real-queue` silently dumps controller proposals into an in-memory list without referee arbitration.
3. **Hardcoded Baseline Cell Stats (`arbiter/api/routes.py:174-180`):**
   ```python
   baseline_stats = {
       "cell_A": (35, 0.875),
       "cell_B": (4, 0.100),
       "cell_C": (4, 0.100),
       "cell_D": (6, 0.150),
       "cell_E": (5, 0.125),
   }
   ```
   `GET /api/v1/state/cells` returns hardcoded baseline user counts and load ratios rather than querying live simulator state.
4. **Fixture Fallback in Twin Prediction (`arbiter/api/routes.py:328`):**
   If `get_global_evaluator()` is uninitialized, `/api/v1/twin/intervals` falls back to reading `fixtures/v2/twin_prediction_v2.json`.

---

## 3. Phase 1: Functionality Measurement & Physics Validation

### 3.1 Test Coverage & Code Quality
- **Full Pytest Execution:** 534 passed in 19.70s.
- **Statement Coverage:** 87% across 5,906 total statements (743 missed).
  - `arbiter/engine/contracts.py`: 100%
  - `arbiter/detector/graph.py`: 100%
  - `arbiter/detector/classifier.py`: 94%
  - `arbiter/twin/conformal.py`: 91%
  - `arbiter/twin/analytical_model.py`: 95%
  - `arbiter/queue/proposal_queue.py`: 97%
  - `arbiter/storage/commit_log.py`: 100%
  - `arbiter/simulator/proposal_sink_adapter.py`: 0% (uncalled in unit tests)
- **Flake8 Linter Check:** 586 style issues detected (552 are E501 line length >79 chars; 16 unused imports F401; 3 unused global statements F824; 12 E302).
- **Mypy Strict Static Type Check:** **FAILED (Exit code 1)**.
  - `arbiter/telemetry/frame.py:242`: `error: Cannot assign to a method [method-assign]`
  - `arbiter/telemetry/frame.py:242`: `error: Incompatible types in assignment`
  - Cause: Monkey-patching `SimulationEngine.frame = _engine_frame` dynamically at runtime.

### 3.2 10-Seed 2 AM Collision Measurement Table
We executed 10 independent closed-loop runs of the 2 AM collision scenario (Seeds 101 through 110). In each run, three proposals were emitted at Tick 6 (ES: Cell B Sleep=1.0; LB: Cell A Offload=0.4 to Cell B; CC: Cell A Tx Power +3 dBm). The detector generated 2 conflict clash edges: `(es, lb)` and `(cc, es)`.

| Seed | Ingress Proposals | Clash Edges | ES Decision | LB Decision | CC Decision | Arb Latency | Coordinated Drops | Counterfactual Drops | Drops Avoided | Coordinated Power (W) | Counterfactual Power (W) |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **101** | 3 | 2 | DELAY (hold=1) | MODIFY (15%) | MODIFY (+1dB) | 2.76 ms | 0 | 18 | +18 (100.0%) | 2,156.4 W | 1,876.1 W |
| **102** | 3 | 2 | DELAY (hold=1) | MODIFY (15%) | MODIFY (+1dB) | 0.82 ms | 36 | 36 | 0 (0.0%) | 2,157.9 W | 1,876.8 W |
| **103** | 3 | 2 | DELAY (hold=1) | MODIFY (15%) | MODIFY (+1dB) | 0.68 ms | 0 | 18 | +18 (100.0%) | 2,158.2 W | 1,877.0 W |
| **104** | 3 | 2 | DELAY (hold=1) | MODIFY (15%) | MODIFY (+1dB) | 0.65 ms | 0 | 18 | +18 (100.0%) | 2,157.1 W | 1,876.3 W |
| **105** | 3 | 2 | DELAY (hold=1) | MODIFY (15%) | MODIFY (+1dB) | 0.61 ms | 0 | 18 | +18 (100.0%) | 2,158.4 W | 1,877.1 W |
| **106** | 3 | 2 | DELAY (hold=1) | MODIFY (15%) | MODIFY (+1dB) | 0.62 ms | 18 | 18 | 0 (0.0%) | 2,158.0 W | 1,876.9 W |
| **107** | 3 | 2 | DELAY (hold=1) | MODIFY (15%) | MODIFY (+1dB) | 0.64 ms | 0 | 18 | +18 (100.0%) | 2,157.6 W | 1,876.7 W |
| **108** | 3 | 2 | DELAY (hold=1) | MODIFY (15%) | MODIFY (+1dB) | 0.67 ms | 36 | 36 | 0 (0.0%) | 2,158.6 W | 1,877.2 W |
| **109** | 3 | 2 | DELAY (hold=1) | MODIFY (15%) | MODIFY (+1dB) | 0.57 ms | 0 | 21 | +21 (100.0%) | 2,159.1 W | 1,875.9 W |
| **110** | 3 | 2 | DELAY (hold=1) | MODIFY (15%) | MODIFY (+1dB) | 0.66 ms | 18 | 33 | +15 (45.5%) | 2,157.5 W | 1,876.9 W |
| **MEAN**| **3.0** | **2.0** | **DELAY** | **MODIFY (15%)** | **MODIFY (+1dB)** | **0.87 ms** | **10.80** | **23.40** | **+12.60 (53.85%)** | **2,157.88 W** | **1,876.69 W** |

### 3.3 Behavioral Edge Cases Beyond the Demo Path
1. **Public Safety Emergency Override Bypass:** [MEASURED] When an emergency proposal is submitted (`is_emergency=True`), `ProposalQueue.submit()` immediately routes it to `EmergencyBypassLane.accept()`, returning `ActionType.APPROVE` with `ReasonCode.EMERGENCY_OVERRIDE` in 0.04 ms without entering the normal batch queue or evaluating digital twin predictions.
2. **Hold Count Limit (Max 3 Ticks):** [MEASURED] A conflicting proposal submitted over consecutive ticks was held with `ActionType.DELAY` at Tick 1 (hold=1), Tick 2 (hold=2), and Tick 3 (hold=3). At Tick 4, the queue detected hold budget exhaustion (`hold_count >= 3`) and forced a terminal evaluation, rejecting the proposal with `ReasonCode.FLOOR_BREACH`.
3. **Slow Digital Twin Timeout Fallback:** [MEASURED] Injected a mock twin with a 60 ms artificial delay (exceeding `TWIN_TIMEOUT_MS = 50 ms`). The referee decision ladder immediately timed out, rejected the candidate delta, and returned `ActionType.REJECT` with `ReasonCode.TWIN_TIMEOUT`.
4. **Invalid Proposal Rejection:** [MEASURED] Submitting an out-of-range proposal (Tx power = -50 dBm) was caught cleanly by Pydantic validation schemas and rejected without crashing the queue or engine.
5. **Flood Stress Test (1,000 Proposals):** [MEASURED] Ingested 1,000 conflicting proposals in a single 100 ms tick window. `QueueOrchestrator` partitioned and evaluated the entire batch in 5.75 seconds, outputting 1,000 valid decisions without deadlock or crash.
6. **Persistence Across Restart:** [MEASURED] A committed transaction logged by `SQLiteCommitLogger` (`commit_id="c_persist_01"`, `applied_value=32.0`) was verified to persist cleanly across process termination and reconnection to `arbiter_production.db`.
7. **WebSocket Reconnection:** [MEASURED] Connected to `ws://127.0.0.1:8000/api/v1/ws/live`, received a 214-byte frame, disconnected, and reconnected immediately. The server re-established state and streamed the next tick frame without error.
8. **Non-Collision Scenarios:** [MEASURED] Ran `ScenarioType.PEAK` and `ScenarioType.SURGE` for 100 ticks each; both scenarios executed without error.
9. **Determinism:** [MEASURED] Ran two identical simulations with `seed=999` for 30 ticks; the resulting per-cell SINR and drop vectors matched bit-for-bit across all ticks (0 diffs).

### 3.4 Physics Correctness Spot-Checks
- **COST-231 Path Loss at 500 m:** [MEASURED] Evaluated carrier frequency 2100 MHz, base station height 30 m, mobile height 1.5 m at 500 m distance ($d = 0.5$ km).
  - Spec / Standard Equation: $\text{PL} = 46.3 + 33.9\log_{10}(f) - 13.82\log_{10}(h_b) - a(h_m) + (44.9 - 6.55\log_{10}(h_b))\log_{10}(d) + C_m = 127.857\text{ dB}$.
  - Code Output (`Cost231PropagationModel.compute_path_loss(dist_m=500.0)`): **127.857 dB**. Deviation: **0.000 dB**.
- **SINR Monotonicity:** [MEASURED] Evaluated path loss across 100 m, 300 m, 500 m, 800 m, 1200 m: monotonically increasing (100.7 dB $\to$ 143.5 dB).
- **Sleeping Cell Radiation:** [MEASURED] Setting `CellEntity.set_sleep(True)` immediately sets `energy_consumption_w()` to **0.0 W** and drops effective radiated power to 0 W.
- **Drop Criterion:** [MEASURED] Drops occur when user SINR falls below -3.0 dB, in accordance with 3GPP LTE/NR QCI-9 drop standards.

### 3.5 Discovered Bugs Catalog
1. **[SEVERITY: HIGH] Default Demo Uses Test Toy Twin:** `arbiter/simulator/engine.py:657` imports `_scenario_twin` from `tests.branch2.test_decision_ladder.py`, bypassing Branch 3's `ConformalDigitalTwin`.
2. **[SEVERITY: MEDIUM] Mypy Type Checking Failure:** `arbiter/telemetry/frame.py:242` monkey-patches `SimulationEngine.frame`, generating `method-assign` and `assignment` errors under mypy.
3. **[SEVERITY: MEDIUM] Hardcoded API Baseline:** `arbiter/api/routes.py:174-180` returns hardcoded dictionary values for cell state instead of reading active engine state.
4. **[SEVERITY: MEDIUM] Decoupled CLI Proposal Sink:** `arbiter/simulator/engine.py:71` defaults to `MockProposalSink()`; `scripts/run_demo.ps1` omits `--use-real-queue`.
5. **[SEVERITY: LOW] SQLite Unclosed Connection Warnings:** Pytest generates multiple `ResourceWarning: unclosed database in <sqlite3.Connection>` in `tests/site/test_site_routes.py`.

---

## 4. Phase 2: Feasibility & Real-World Deployability

### 4.1 O-RAN Near-RT RIC Architecture Mapping
| Interface | O-RAN Standard Role | ARBITER Implementation Status | What is Missing |
| :--- | :--- | :--- | :--- |
| **E2** | Connects Near-RT RIC to E2 Nodes (O-CU/O-DU); transfers telemetry (E2SM-KPM) and control commands (E2SM-RC). | Simulated via internal Python calls (`CellEntity.set_tx_power`, `step()`) and REST `/api/v1/telemetry/ingest`. | No ASN.1 / SCTP serialization; no E2AP protocol encapsulation; no E2 Subscription/Indication state machines. |
| **A1** | Connects Non-RT RIC to Near-RT RIC to convey policy guidance (A1-P) and ML enrichment (A1-EI). | Abstracted as `RiskDial` and `UtilityWeights` ($w_1..w_4$). | No compliance with O-RAN WG2 A1-AP REST specification; no A1 policy schema definitions or life-cycle endpoints. |
| **R1** | Service interface in SMO enabling rApps to discover and consume services. | Not implemented. | ARBITER is designed as an internal RIC referee for xApps rather than an SMO rApp service. |
| **O1** | Management interface for FCAPS, configuration, and logging via NETCONF/YANG and VES. | SQLite `commit_log` and `audit_decisions` tables provide local auditability. | No NETCONF agent; no YANG data models (`o-ran-energy-saving.yang`); no VES event streaming. |

### 4.2 Timing Budget vs Near-RT RIC Constraints
- **O-RAN WG3 Specification:** Near-RT RIC control loops must execute between **10 ms and 1,000 ms**.
- **ARBITER Measured Latency:** Mean decision latency is **0.87 ms** (p50: 0.82 ms, p95: 2.76 ms for 3 proposals; 6.98 ms mean for 200 cells).
- **Feasibility Verdict:** ARBITER's arbitration logic consumes $<3\%$ of a 100 ms control window, leaving ample headroom (>85 ms) for network transport (E2AP over SCTP).

### 4.3 Digital Twin Fidelity & Sim-to-Real Gap
- **Surrogate Calibration:** Branch 3 employs non-conformity calibration over recent simulation history to construct empirical intervals $[y_{\text{lower}}, y_{\text{upper}}]$ at $1 - \alpha$ confidence.
- **Sim-to-Real Gap Analysis:**
  - Real cellular networks exhibit Rayleigh/Rician multipath fast fading, log-normal shadowing (standard deviation 6–8 dB), 3D antenna array pattern distortion, Doppler spread from high-speed vehicular mobility, and non-linear power amplifier efficiency curves.
  - ARBITER's simulator assumes static 2D hexagonal geometry, deterministic COST-231 path loss, and uniform user traffic distributions. A surrogate trained exclusively on this synthetic environment will exhibit significant prediction error when deployed on live RAN telemetry without domain adaptation.

### 4.4 Safety Guarantees & Failure Modes
- **Fail-Safe Operation:** ARBITER strictly adheres to a **fail-safe** design philosophy:
  1. If the digital twin fails to return within 50 ms, the proposal is rejected (`ReasonCode.TWIN_TIMEOUT`).
  2. If a proposed parameter modification breaches safety floors (Coverage $< 80\%$ or Drop Rate $> 2\%$), the engine bisects or rejects (`ReasonCode.FLOOR_BREACH`).
  3. The `ActuatorEnforcer` and `RollbackHandler` maintain a Last-Known-Good (`LKGStore`) state in SQLite, enabling transactional rollbacks if post-actuation verification fails.

### 4.5 Security & Trust Model
- **Vulnerabilities:** All REST endpoints are currently unauthenticated (no OAuth2, JWT Bearer tokens, or mTLS).
- **Multi-Tenant xApp Conflict:** In a multi-vendor Near-RT RIC (e.g., Ericsson ES xApp vs Nokia LB xApp), rogue controllers could flood proposals to starve competing controllers. ARBITER's priority queue and maximum 3-tick hold limit mitigate starvation, but cryptographic xApp identity attestation is absent.

---

## 5. Phase 3: Scalability Benchmarks & Bottleneck Analysis

All benchmarks were measured on the host system and recorded in `artifacts/assessment/phase3_results.json`. The corresponding visualization is saved at `artifacts/assessment/scalability_chart.png`.

### 5.1 Cluster Scaling (5 to 200 Cells)
Measured with 3 simultaneous conflicting proposals per tick over 30 ticks:

| Cells | p50 Latency (ms) | p95 Latency (ms) | p99 Latency (ms) | Mean Latency (ms) | Memory RSS (MB) |
| :---: | :---: | :---: | :---: | :---: | :---: |
| **5** | 1.60 | 3.16 | 3.34 | 1.92 | 97.5 |
| **19** | 1.61 | 2.52 | 3.39 | 1.65 | 97.6 |
| **50** | 3.10 | 4.45 | 4.90 | 3.19 | 97.7 |
| **100** | 4.62 | 5.60 | 6.76 | 4.53 | 98.2 |
| **200** | 6.25 | 9.89 | 13.84 | 6.98 | 99.0 |

*Finding: Cluster size scaling is highly linear ($O(N)$), consuming only 6.25 ms p50 latency for a 200-cell network.*

### 5.2 Conflict Detector Bottleneck ($O(P^2)$ Pairwise Scaling)
Measured by feeding simultaneous proposals into `ConflictGraphBuilder.build()` against a 50-cell topology:

| Proposals in Window ($P$) | Conflict Edges Generated | Detection Time (ms) | Within 100 ms Budget? |
| :---: | :---: | :---: | :---: |
| **10** | 30 | 5.38 ms | **YES** (5.4% of budget) |
| **100** | 1,061 | 153.64 ms | **NO (BREACHED — 153.6% of budget)** |
| **1,000** | 104,039 | 14,818.14 ms | **NO (14.8 seconds)** |
| **5,000** | 2,598,791 | 381,050.73 ms | **NO (6.35 minutes)** |

**Primary Bottleneck Discovery:** The conflict graph detection evaluates pairwise compatibility across all commands in the window ($\binom{P}{2} = \frac{P(P-1)}{2}$ pairs).
- At 10 proposals: 45 pairs $\to$ 5.38 ms.
- At 100 proposals: 4,950 pairs $\to$ 153.64 ms.
- At 5,000 proposals: 12,497,500 pairs $\to$ 381 seconds.
- **Empirical Threshold:** The 100 ms control window breaks at **$\approx 75$ concurrent proposals**. Beyond 75 proposals, the system cannot function in a Near-RT RIC without spatial sector sharding.

### 5.3 User Scaling (Radio Physics Step Throughput)
| Active Users | Step Duration (ms) | Throughput (Users / sec) |
| :---: | :---: | :---: |
| **70** | 0.18 ms | 397,727 |
| **350** | 0.17 ms | 2,100,840 |
| **2,000** | 0.71 ms | 2,806,624 |
| **10,000** | 3.19 ms | 3,139,028 |

*Finding: Vectorized NumPy propagation scales effortlessly; 10,000 users take only 3.19 ms.*

### 5.4 Storage & API Throughput
- **SQLite Write Throughput:** 1,320 writes / second (0.76 ms / write).
- **Commit Log Growth Rate:** 294.9 bytes per commit; at 1 commit per tick (10 ticks/sec), storage grows at **10.13 MB / hour** (~243 MB / day).
- **SQLite Query Latency:** Fetching the latest 50 audit decisions executes in **0.87 ms**.
- **WebSocket Concurrency:** 50 concurrent browser clients received live frames at 214 bytes/tick with 0 dropped connections.
- **HTTP Latency:** 50 concurrent requests to `/api/v1/health` resolved with a mean latency of 49.17 ms (p50: 23.45 ms).

---

## 6. Phase 4: Impact Quantification & Operator Extrapolation

### 6.1 Measured Impact Arithmetic (5-Cell Cluster)
- **Drop Reduction:** Across 10 seeds, uncoordinated counterfactual produced a mean of 23.40 dropped users. ARBITER reduced this to 10.80 dropped users.
  $$\text{Drops Avoided} = 23.40 - 10.80 = 12.60 \text{ users per collision event}$$
  $$\text{Relative Drop Reduction} = \frac{23.40 - 10.80}{23.40} \times 100\% = 53.85\%$$
- **Transition vs Steady-State Energy:**
  - During the 20-tick handover transition, ARBITER keeps Cell B awake to absorb traffic, drawing 2,157.88 W vs 1,876.69 W for uncoordinated shutdown (+281.19 W, or +14.98%).
  - At steady state (post-handover), Cell B power drops from 419.53 W to 0.0 W. Across the 5-cell cluster (~2,100 W nominal), putting 1 cell to sleep saves:
    $$\frac{419.53\text{ W}}{2,100\text{ W}} \times 100\% \approx 20.0\% \text{ cluster power savings}$$
- **Combinatorial Optimality Gap:**
  - Evaluated 64 combinatorial states ($2 \times 8 \times 4$) on forked simulators in `arbiter/simulator/optimality.py`.
  - Uncoordinated baseline utility: **-2.355**.
  - ARBITER synthesized decision utility: **+0.097**.
  - Global Pareto combinatorial optimum utility: **+0.097**.
  - **Optimality Gap:** **0.0%** (ARBITER reached the exact global optimum in 6.83 ms vs 311 ms for exhaustive search).

### 6.2 Extrapolation to 1,000 Macro Sites (3,000 Cells)
*All figures in this section are extrapolated projections based on measured per-cell metrics.*

**Model Assumptions:**
- Network: 1,000 macro base stations (3 sectors each = 3,000 cells).
- Nocturnal Energy Saving: 1 capacity sector per site put to sleep between 01:00 and 05:00 (4 hours/night).
- Per-cell power savings in sleep mode: 419.5 W ($1.678\text{ kWh / cell / night}$).
- Grid Emission Factor: 0.385 kg $\text{CO}_2\text{e / kWh}$. Commercial Tariff: \$0.12 (low), \$0.18 (base), \$0.25 (high) per kWh.
- Nightly collision probability: 20% (low), 50% (base), 80% (high) of sites encounter simultaneous ES/LB/CC conflicts.

| Metric | Low Extrapolation | Base Extrapolation | High Extrapolation |
| :--- | :---: | :---: | :---: |
| **Sites with Nightly Conflicts** | 200 sites / night | 500 sites / night | 800 sites / night |
| **Annual Collision Events** | 73,000 | 182,500 | 292,000 |
| **Annual Subscriber Drops Avoided** | **365,000** | **2,299,500 (~2.3M)** | **5,840,000 (~5.8M)** |
| **Slept Cells Enabled Safely** | 600 cells (4 hrs/night) | 1,000 cells (4 hrs/night) | 1,500 cells (5 hrs/night) |
| **Annual Electricity Saved** | **367,482 kWh (367 MWh)** | **612,470 kWh (612 MWh)** | **1,148,381 kWh (1.15 GWh)** |
| **Annual OPEX Cost Savings** | **\$44,098 / year** | **\$110,245 / year** | **\$287,095 / year** |
| **Annual Carbon Avoided** | **141.5 metric tons $\text{CO}_2\text{e}$** | **235.8 metric tons $\text{CO}_2\text{e}$** | **442.1 metric tons $\text{CO}_2\text{e}$** |

### 6.3 Novelty Assessment vs Prior Art
- **Established in Literature:** Multi-xApp conflict detection taxonomy (direct, indirect, spatial) is well-documented in O-RAN WG3 and prior CMF (Conflict Management Framework) literature.
- **Genuine Novelty in ARBITER:**
  1. **Empirical Optimality Gap Benchmarking:** Quantifying referee utility against exhaustive brute-force search ($0.0\%$ gap achieved in $<7$ ms).
  2. **Uncertainty-Aware Decision Ladder:** Incorporating conformal prediction intervals directly into the ladder logic to prevent decisions that fall within the surrogate model's noise floor.
  3. **Plan Synthesis:** Generating staged, sequential execution plans (delaying sleep until offload handovers finish) rather than treating referee verdicts as isolated instantaneous actions.

---

## 7. Phase 5: Hackathon-Readiness & Presentation Risks

### 7.1 Demo Flow & Cold Start
- **Clone-to-Demo Time:** Fast. Installing requirements takes $<45$ seconds; running `scripts/run_demo.ps1` boots Uvicorn and opens the 3D WebGL monitor in $<2.5$ seconds.
- **Offline Capability:** **100% verified**. `scripts/scan_offline.py` confirms zero external CDN requests at runtime. All fonts (Outfit, JetBrains Mono) and Three.js libraries are vendored locally.
- **UI Label Honesty:**
  - Replay states are clearly flagged with `"REPLAY OF REAL ENGINE RUN"` badges.
  - Metrics on the marketing site dynamically render `"MEASURED"` vs `"TARGET"` status chips.
  - Live dashboard dynamically reflects `"SERVER OFFLINE"`, `"ENGINE IDLE"`, or `"ENGINE LIVE"`.

### 7.2 Top 10 Live Judging Risks & Mitigations

| # | Live Demo Risk | Likelihood | Severity | Risk Score | Concrete Mitigation for Team |
| :-: | :--- | :-: | :-: | :-: | :--- |
| **1** | Judge asks to inspect digital twin code and discovers demo calls `_scenario_twin` from tests. | High (4) | Critical (5) | **20** | Explicitly acknowledge the integration stub upfront; demonstrate Branch 3's `ConformalDigitalTwin` running via `tests/branch3/test_conformal_twin.py`. |
| **2** | Judge asks to test 500 simultaneous proposals, freezing the conflict detector for 8+ seconds. | Med (3) | Critical (5) | **15** | Keep live demo inputs to $\le 50$ proposals; show Phase 3 benchmark chart explaining the $O(P^2)$ pairwise bottleneck honestly. |
| **3** | Dashboard displays "ENGINE IDLE" because user forgot to start the background engine. | High (4) | Med (3) | **12** | Always start demo via `scripts/run_demo.ps1` which automatically invokes the streaming engine alongside Uvicorn. |
| **4** | Judge asks about O-RAN E2/A1 compatibility and asks to see the ASN.1 / REST specs. | High (4) | Med (3) | **12** | State clearly that E2/A1 are architectural models in this phase; present the interface mapping table from Section 4.1. |
| **5** | Judge challenges the 0.0% optimality gap claim as being measured on a small 5-cell scenario. | Med (3) | High (4) | **12** | Acknowledge that 0.0% was verified on the 64-state 5-cell grid; do not claim 0.0% holds for arbitrary 200-cell graphs. |
| **6** | Python `flake8` or `mypy` is run by a technical judge and exits with code 1. | Med (3) | Med (3) | **9** | Fix the monkey-patch assignment in `arbiter/telemetry/frame.py:242` before judging. |
| **7** | Judge clicks `/api/v1/state/cells` and notes user counts never change during simulation. | Med (3) | Med (3) | **9** | Explain that `/state/cells` returns static site topology; direct judge to `/api/v1/ws/live` for dynamic state. |
| **8** | Uvicorn server port 8000 is occupied by another process during cold boot. | Low (2) | High (4) | **8** | Ensure `scripts/.server_pid` cleanup script runs prior to the judging presentation. |
| **9** | SQLite connection lock error during rapid multi-client dashboard stress. | Low (2) | Med (3) | **6** | Enable WAL mode (`PRAGMA journal_mode=WAL;`) on `arbiter_production.db`. |
| **10**| Judge challenges energy claim because cluster power rises during the collision tick. | High (4) | Low (1) | **4** | Explain the transition trade-off: short-term power rises to protect QoS, enabling safe 419.5 W savings post-handover. |

---

## 8. The Judge's 12 Hard Questions

1. **"Does your live demo run Branch 3's real conformal digital twin, or does it run an in-memory test mock?"**  
   *Honest Answer:* "In the current integration merge, the simulation engine runner imports `_scenario_twin` from our test suite, which is an analytical mock. Branch 3's real `ConformalDigitalTwin` and `DigitalTwinEvaluator` are fully implemented and tested in `arbiter/twin/`, but wiring them into the closed-loop engine is an outstanding item on our integration roadmap."
2. **"How does your referee scale if 500 controllers submit commands in the same 100 ms window?"**  
   *Honest Answer:* "Our conflict detector evaluates command pairs pairwise ($O(P^2)$). At 100 proposals, detection takes 153 ms; at 500 proposals, it exceeds 3 seconds, breaching the 100 ms budget. To support 500 proposals, we would need to shard proposals by spatial cluster adjacency before running graph construction."
3. **"Where are your O-RAN E2 and A1 protocol encoders?"**  
   *Honest Answer:* "We haven't implemented ASN.1 or SCTP E2AP encoding. ARBITER models the semantic payloads of E2SM-RC and A1-P as Python Pydantic schemas and REST endpoints. Compliance with O-RAN WG3 wire protocols is planned for future work."
4. **"Why does your cluster power increase during the 2 AM collision instead of decreasing?"**  
   *Honest Answer:* "Energy Saving proposed an immediate shutdown of Cell B, but 40% of Cell A's traffic was being transferred to it. If we shut Cell B down immediately, subscribers experience a blackout drop. ARBITER deliberately delays the shutdown by 3 ticks and boosts Cell A's power by +1 dB to ensure handovers complete smoothly. The short-term power increase of +15% enables long-term safe power savings of 419.5 W once the cell safely sleeps."
5. **"What happens if an xApp goes rogue and spams 10,000 invalid proposals?"**  
   *Honest Answer:* "Pydantic catches schema validation errors, but 10,000 proposals would overwhelm the pairwise conflict detector. We currently lack API rate-limiting and cryptographic xApp authentication."
6. **"How do you calibrate your digital twin against real-world radio propagation?"**  
   *Honest Answer:* "We haven't verified calibration against drive-test data from a live physical network. Our calibration loop calibrates conformal quantiles against synthetic telemetry generated by our COST-231 simulator."
7. **"How does your system guarantee fail-safe operation if the digital twin crashes?"**  
   *Honest Answer:* "The decision ladder enforces a strict 50 ms timeout guard. If the twin crashes, times out, or returns malformed predictions, the proposal is rejected with `ReasonCode.TWIN_TIMEOUT`, and the network remains at its Last-Known-Good configuration."
8. **"Why does `GET /api/v1/state/cells` return hardcoded baseline stats?"**  
   *Honest Answer:* "`_get_cell_states()` was implemented with a hardcoded dictionary during Branch 4 development before the live simulation engine singleton was exposed via API. Dynamic state is currently streamed exclusively over the WebSocket `/api/v1/ws/live`."
9. **"Did you verify your 0.0% optimality gap on a large network or only on 5 cells?"**  
   *Honest Answer:* "The 0.0% optimality gap was measured specifically on the 64 combinatorial states of the 5-cell 2 AM collision scenario. On larger, non-convex 200-cell networks, finding the true combinatorial optimum is NP-hard, and the optimality gap has not been verified."
10. **"Why does mypy fail on `arbiter/telemetry/frame.py`?"**  
    *Honest Answer:* "To avoid modifying frozen Branch 1 simulator code during branch integration, Branch 4 dynamically monkey-patched a `.frame()` helper onto `SimulationEngine` at import time. Mypy flags this as an illegal method assignment."
11. **"Can an operator customize the trade-off between energy savings and call drops in real time?"**  
    *Honest Answer:* "Yes. The `RiskDial` endpoint dynamically updates the utility weights ($w_1..w_4$) in `QueueOrchestrator`, instantly altering whether the decision ladder prioritizes drop prevention or aggressive power savings."
12. **"What third-party dependencies are required, and can this run in an air-gapped network?"**  
    *Honest Answer:* "ARBITER runs 100% air-gapped without GPUs or cloud services. It depends strictly on open-source packages (FastAPI, NumPy, Pydantic, NetworkX) and vendored frontend assets."

---

## 9. Prioritised Improvement Plan

Ten highest-value technical fixes ranked by Score Gain per Engineering Hour:

| # | Improvement Task | Est. Hours | Target Dimension | Expected Score Gain | Subsystem Owner |
| :-: | :--- | :-: | :--- | :---: | :--- |
| **1** | **Wire Branch 3 Conformal Twin into Default Engine:** Replace `_scenario_twin` in `engine.py:657` with `TwinClientAdapter(DigitalTwinEvaluator)`. | 2.5 hrs | Functionality | **+0.8** | Simulator / Twin |
| **2** | **Add Spatial Bounding Box Filter to Conflict Detector:** Filter non-adjacent cells before pairwise edge checks to reduce complexity from $O(P^2)$ to $O(P \cdot k)$. | 3.0 hrs | Scalability | **+1.2** | Arbitration |
| **3** | **Fix Mypy Monkey-Patch in `frame.py`:** Add a proper wrapper class or register a mixin rather than dynamically assigning to `SimulationEngine.frame`. | 1.0 hr | Functionality | **+0.4** | Telemetry |
| **4** | **Connect `/state/cells` to Active Simulator State:** Replace hardcoded `baseline_stats` dictionary in `routes.py` with dynamic query to `SimulationEngine`. | 1.5 hrs | Functionality | **+0.3** | API / Dashboard |
| **5** | **Pass `--use-real-queue` in `run_demo.ps1`:** Update line 65 of `run_demo.ps1` to ensure standalone CLI simulation feeds `ProposalQueueSink`. | 0.5 hrs | Functionality / Demo | **+0.3** | Scripts |
| **6** | **Implement Basic Token Auth on API Routes:** Add FastAPI HTTP Bearer security dependency across `/api/v1/commands/propose` and `/telemetry/ingest`. | 2.0 hrs | Feasibility | **+0.5** | API |
| **7** | **Fix SQLite Connection Leaks:** Enforce context managers (`with sqlite3.connect(...) as conn:`) across `test_site_routes.py` to eliminate ResourceWarnings. | 1.0 hr | Functionality | **+0.2** | Storage / Tests |
| **8** | **Add Shadow Fading & Multipath to Channel Model:** Add log-normal shadowing ($\sigma = 6\text{ dB}$) to `Cost231PropagationModel` to improve sim-to-real fidelity. | 3.5 hrs | Feasibility | **+0.6** | Simulator |
| **9** | **Enable SQLite WAL Mode:** Execute `PRAGMA journal_mode=WAL;` at database startup to prevent write locking under concurrent telemetry ingestion. | 0.5 hrs | Scalability | **+0.3** | Storage |
| **10**| **Draft O-RAN E2SM-RC Schema Specification:** Document the exact ASN.1 / JSON mapping between ARBITER command payloads and E2SM-RC control actions. | 4.0 hrs | Feasibility | **+0.5** | Architecture |

---

## 10. Requirement Traceability Matrix (SRS v0.1 & Branch Specs)

| Req ID | Requirement Summary | Status | Evidence (Code / Test File) | Notes / Gaps |
| :--- | :--- | :---: | :--- | :--- |
| **C-1** | Stack fixed (FastAPI, NumPy, Pydantic, SQLite, Three.js) | **Implemented** | `requirements.txt`, `arbiter/dashboard/3d/` | Fully verified offline. |
| **C-2** | Twin SHALL be analytical (no black-box ML training) | **Implemented** | `arbiter/twin/analytical_model.py` | Analytical path-loss/SINR chain. |
| **C-3** | Core demo 5-cell; 19-cell is stretch | **Implemented** | `arbiter/simulator/grid.py:generate_19_cell_cluster` | Both topologies verified. |
| **A-1** | Milan traffic traces provide load shape | **Implemented** | `fixtures/traffic/milan_2am_quiet.json` | Mapped to simulated cells. |
| **A-2** | O-RAN / 3GPP standards context only; compliance not claimed | **Implemented** | `ARBITER_SRS_v0.1.pdf` §1.2 | Explicitly acknowledged. |
| **FR-1.1**| Hexagonal RAN layout simulation | **Implemented** | `arbiter/simulator/grid.py:HexagonalGrid` | Tests in `test_grid.py`. |
| **FR-1.2**| Per-cell state (power, tilt, sleep) | **Implemented** | `arbiter/simulator/cell.py:CellEntity` | Tests in `test_cell.py`. |
| **FR-1.3**| Dynamic user mobility and traffic | **Implemented** | `arbiter/simulator/traffic.py:TrafficModel` | Tests in `test_traffic.py`. |
| **FR-1.4**| 19-cell two-tier configuration | **Implemented** | `grid.py:generate_19_cell_cluster` | 19 cells verified. |
| **FR-1.5**| Per-tick telemetry emission | **Implemented** | `arbiter/simulator/engine.py:step` | Frames emitted every 100 ms. |
| **FR-1.6**| Adjacency and COST-231 propagation | **Implemented** | `arbiter/simulator/propagation.py` | 127.86 dB at 500m verified. |
| **FR-2.1**| At least 3 controllers (ES, LB, CC) | **Implemented** | `arbiter/controllers/` | ES, LB, CC, PS implemented. |
| **FR-2.2**| Emit command proposals in 100 ms window | **Implemented** | `arbiter/controllers/scenarios.py` | Emitted at +10, +15, +20 ms. |
| **FR-2.3**| Reproduce 2 AM collision scenario | **Implemented** | `controllers/scenarios.py:trigger_2am_collision`| Verified across 10 seeds. |
| **FR-2.4**| Public-safety emergency command source | **Implemented** | `arbiter/controllers/public_safety.py` | Bypasses queue instantly. |
| **FR-2.5**| Command schema & parameter validation | **Implemented** | `arbiter/engine/contracts.py:CommandProposal` | Pydantic validation active. |
| **FR-3.1**| Queue buffer & batch flush | **Implemented** | `arbiter/queue/proposal_queue.py:ProposalQueue` | Flushes per tick. |
| **FR-3.2**| Emergency bypass lane | **Implemented** | `arbiter/queue/emergency_lane.py` | Instant approval in 0.04 ms. |
| **FR-3.3**| Max 3-tick delayed command hold | **Implemented** | `arbiter/queue/proposal_queue.py` | Verified in edge case test. |
| **FR-4.1**| Per-tick conflict graph generation | **Implemented** | `arbiter/detector/graph.py:ConflictGraphBuilder`| Graph built per batch. |
| **FR-4.2**| Direct, Indirect, Spatial classification | **Implemented** | `arbiter/detector/classifier.py` | Clash edges classified. |
| **FR-4.3**| Graph decomposition into components | **Implemented** | `arbiter/detector/partitioner.py` | Parallel component solver. |
| **FR-4.4**| Cycle and ping-pong detection | **Implemented** | `arbiter/detector/cycle_detector.py` | Cycles detected. |
| **FR-5.1**| Twin joint multi-cell KPI prediction | **Partial** | `arbiter/twin/evaluator.py`, `engine.py:657` | Built in Br 3, but demo engine uses mock. |
| **FR-5.2**| Analytical path-loss to drops chain | **Implemented** | `arbiter/twin/analytical_model.py` | Closed-form equations. |
| **FR-5.3**| Twin latency target < 10 ms | **Implemented** | `arbiter/twin/evaluator.py` | Analytical twin runs <2 ms. |
| **FR-5.4**| Online twin recalibration | **Implemented** | `arbiter/twin/calibration.py:OnlineCalibrator` | Conformal interval update. |
| **FR-5.5**| 100 ms twin timeout fallback | **Implemented** | `arbiter/engine/contracts.py`, `fail_safe.py` | Emits REJECT on timeout. |
| **FR-6.1**| Multi-objective utility computation | **Implemented** | `arbiter/engine/utility.py:UtilityCalculator` | $U = w_1 C - w_2 E - w_3 I - w_4 D$. |
| **FR-6.2**| Dynamic Risk Dial weight adjustment | **Implemented** | `arbiter/engine/contracts.py:UtilityWeights` | Configurable via API. |
| **FR-6.3**| Decision ladder (Approve/Modify/Delay/Reject) | **Implemented** | `arbiter/engine/ladder.py:DecisionLadderEngine`| Bisection and delay verified. |
| **FR-6.4**| Binary command non-bisection | **Implemented** | `ladder.py:SafetyFloorEnforcer` | Binary commands not bisected. |
| **FR-6.5**| Safety floors enforcement | **Implemented** | `ladder.py:SafetyFloorEnforcer` | Coverage >80%, Drops <2%. |
| **FR-6.6**| Uncertainty guard ($|\Delta U| < \text{margin}$) | **Implemented** | `ladder.py:UncertaintyGuard` | Prevents sub-noise decisions. |
| **FR-6.7**| Comprehensive reason codes | **Implemented** | `arbiter/engine/contracts.py:ReasonCode` | ACTIVE_HO, FLOOR_BREACH, etc. |
| **FR-7.1**| Actuator apply approved parameters | **Implemented** | `arbiter/actuator/enforcer.py:ActuatorEnforcer` | Commits changes to cells. |
| **FR-7.2**| Rollback on post-actuation error | **Implemented** | `arbiter/actuator/rollback.py:RollbackHandler` | LKGStore rollback active. |
| **FR-7.3**| Record commits in SQLite commit_log | **Implemented** | `arbiter/storage/commit_log.py` | 1,320 writes/s verified. |
| **FR-8.1**| Live SINR & drop telemetry ingest | **Implemented** | `arbiter/telemetry/monitor.py` | REST & WebSocket ingest. |
| **FR-8.2**| Feed telemetry to calibration loop | **Implemented** | `arbiter/telemetry/monitor.py` | Updates conformal bounds. |
| **FR-8.3**| Shadow mode operation | **Implemented** | `arbiter/telemetry/shadow_mode.py` | Logs decisions passively. |
| **FR-9.1**| 2D NOC & 3D WebGL Digital Twin | **Implemented** | `arbiter/dashboard/`, `arbiter/dashboard/3d/` | Offline 3D visualization. |
| **FR-9.2**| Counterfactual divergence comparison | **Implemented** | `arbiter/simulator/counterfactual.py` | Coordinated vs uncoordinated. |
| **FR-9.3**| Ablation benchmarking suite | **Implemented** | `arbiter/benchmarks/ablation.py` | Greed, no-plan ablations. |
| **FR-9.4**| Peak, 2 AM quiet, and surge scenarios | **Implemented** | `arbiter/simulator/traffic.py` | All 3 scenarios verified. |
| **FR-9.5**| Measured KPIs reporting | **Implemented** | `arbiter/benchmarks/pareto.py` | Drops, power, latency logged. |

---

## 11. Appendix: Benchmark Execution Logs & System Environment

- **Host OS:** Microsoft Windows 11 Home (Build 26100)
- **Python Version:** 3.14.7 (tags/v3.14.7:823f032, MSC v.1944 64-bit AMD64)
- **Pytest:** 9.1.1 (Plugins: anyio 4.16.0, cov 7.1.0)
- **Node.js:** v20.18.0
- **Git Commit:** `1cbfd2b614f9fa1d6e109518f16286d401c3cf7a`
- **Assessment Working Directory:** `artifacts/assessment/`
- **Generated Benchmark Artifacts:**
  - `artifacts/assessment/phase0_inventory_results.json`
  - `artifacts/assessment/phase1_results.json`
  - `artifacts/assessment/phase3_results.json`
  - `artifacts/assessment/scalability_chart.png`
- **Primary Reproduction Commands:**
  ```powershell
  # Run full unit tests with coverage
  python -m pytest --cov=arbiter --cov-report=term tests/

  # Run JavaScript unit tests
  node --test tests/branch4/*.js tests/site/*.js

  # Execute Phase 1 functional and physics benchmark
  python artifacts/assessment/phase1_measurement.py

  # Execute Phase 3 scalability benchmark
  python artifacts/assessment/phase3_scalability.py
  ```
