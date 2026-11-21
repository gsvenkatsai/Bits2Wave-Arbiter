# ARBITER: Conflict-Resolution Referee for Autonomous Cellular Networks

[![Python 3.10+](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Tests Passing](https://img.shields.io/badge/tests-488%20passed-success.svg)](#testing--verification)
[![Architecture](https://img.shields.io/badge/architecture-O--RAN%20rApp%20Referee-orange.svg)](#system-architecture)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

> **ARBITER** is a simulation-based, conflict-aware, pre-actuation arbitration engine for autonomous cellular networks (O-RAN rApps/xApps). It intercepts radio parameter proposals from independent controllers (*Energy Saving*, *Load Balancing*, *Coverage/Capacity*, *Public Safety*), constructs a spatial-parameter conflict graph, evaluates joint multi-cell KPI trajectories inside a sub-10ms analytical digital twin with conformal uncertainty intervals, and resolves collisions before commands ever reach the physical network.

---

## Table of Contents

1. [Executive Summary & Problem Statement](#executive-summary--problem-statement)
2. [The Canonical "2 AM Collision" Problem](#the-canonical-2-am-collision-problem)
3. [System Architecture & Data Flow](#system-architecture--data-flow)
4. [Modular Deep Dive (Branch Breakdown)](#modular-deep-dive)
   - [Branch 1: RAN Simulator & Autonomous Controllers](#branch-1-ran-simulator--autonomous-controllers)
   - [Branch 2: Proposal Queue, Conflict Graph & Decision Ladder](#branch-2-proposal-queue-conflict-graph--decision-ladder)
   - [Branch 3: Conformal Digital Twin, Actuator & SQLite Log](#branch-3-conformal-digital-twin-actuator--sqlite-log)
   - [Branch 4: Telemetry Monitor, WebSocket Live Broadcast & NOC Dashboard](#branch-4-telemetry-monitor-websocket-live-broadcast--noc-dashboard)
5. [Advanced Engineering Features (v2 & v3)](#advanced-engineering-features-v2--v3)
   - [Split Conformal Prediction & Calibrated Safety Intervals](#1-split-conformal-prediction--calibrated-safety-intervals)
   - [Multi-Tick Sequential Plan Synthesis](#2-multi-tick-sequential-plan-synthesis)
   - [Counterfactual Replay & Flight Recorder](#3-counterfactual-replay--flight-recorder)
   - [Dynamic RiskDial & Pareto Frontier Optimization](#4-dynamic-riskdial--pareto-frontier-optimization)
   - [Offline Optimality Gap Benchmark](#5-offline-optimality-gap-benchmark)
   - [Environmental & Economic ESG Impact Accounting](#6-environmental--economic-esg-impact-accounting)
   - [Simulator Snapshotting, Forking & State Restoration](#7-simulator-snapshotting-forking--state-restoration)
6. [Mathematical & Algorithmic Foundations](#mathematical--algorithmic-foundations)
7. [Live NOC Dashboard Walkthrough](#live-noc-dashboard-walkthrough)
8. [Repository Directory Structure](#repository-directory-structure)
9. [Installation & Quickstart Guide](#installation--quickstart-guide)
10. [Configuration Reference (.env)](#configuration-reference-env)
11. [REST API & WebSocket Reference](#rest-api--websocket-reference)
12. [Testing & Verification](#testing--verification)
13. [Engineering Team & Parallel Development Structure](#engineering-team--parallel-development-structure)

---

## Executive Summary & Problem Statement

Modern cellular architectures (5G NR, O-RAN, Self-Organizing Networks) deploy specialized autonomous controllers that optimize individual network key performance indicators (KPIs) in closed loops:

* **Energy Saving (ES)**: Shuts down underutilized cells or dims radio frequency (RF) chains during nocturnal lulls to minimize power consumption.
* **Load Balancing (LB)**: Adjusts cell individual offsets (CIO) and offload ratios to shed traffic from congested macro cells to adjacent neighbors.
* **Coverage and Capacity Optimization (CCO)**: Boosts transmission power ($P_{\text{tx}}$) or modulates antenna downtilts to cure coverage holes and edge degradation.
* **Public Safety / Emergency (PS)**: Reconfigures radio resources dynamically to prioritize emergency responders and critical services.

### The Conflict Problem
When these independent, vendor-agnostic control loops operate uncoordinated over shared physical radio resources and coupled spatial topologies, **destructive control collisions** occur:

1. **Parameter Clashing**: Two controllers issue contradictory target values for the same antenna or cell concurrently (e.g., ES orders power down while CCO orders power up).
2. **Coupled Spatial Destabilization**: An energy-saving controller turns off Cell B, while simultaneously an adjacent load-balancing controller shifts 40% of Cell A's active calls into Cell B. In an uncoordinated network, the handover target is powered down, terminating active voice and data sessions instantly.
3. **Implicit Ping-Pong Cascades**: Handover threshold shifts trigger cyclic migrations of mobile user equipment (UEs) back and forth across cell boundaries, wasting backhaul signaling and triggering dropped calls.

**ARBITER acts as an online, sub-100ms pre-actuation referee.** Instead of allowing controllers to mutate radio parameters directly, ARBITER intercepts proposals into a temporal queue, evaluates their combined system-wide impact in an ultra-fast analytical digital twin, and systematically **Approves**, **Modifies** (via golden-section bisection), **Delays**, or **Rejects** actions to protect network invariants.

---

## The Canonical "2 AM Collision" Problem

To demonstrate the critical necessity of pre-actuation arbitration, ARBITER features an end-to-end benchmark scenario known as the **2 AM Collision**:

```
           [ Cell A ] (Surging User Count)
          /         \
         /           \  LB: Offload 40% users A -> B
        v             v
   [ Cell C ]      [ Cell B ] (Underutilized Night Traffic)
                      ^
                      |  ES: Power OFF (0 W / Sleep)
```

### Uncoordinated Execution (Baseline Failure)
1. **At Simulation Tick 6 (02:00:00)**:
   - Traffic on **Cell B** drops below the energy-saving threshold (quiet night). The **Energy Saving (ES)** controller orders `Cell B: SLEEP_STATE = OFF` (0 Watts).
   - An unexpected traffic surge occurs on adjacent **Cell A**. The **Load Balancing (LB)** controller proposes `Cell A: OFFLOAD_RATIO = 40%` towards neighbor `Cell B`.
   - The **Coverage/Capacity (CCO)** controller notices cell-edge interference and proposes `Cell A: TX_POWER_DBM = +3 dBm` (43 dBm $\to$ 46 dBm).
2. **Without ARBITER**:
   - Both commands execute raw against the physical layer.
   - Cell B's RF transmitter turns dark ($0\text{ W}$).
   - The 40% of users handed over from Cell A attempt attachment to Cell B, find no carrier signal, and **suffer immediate call drop** (14 users dropped in benchmark).
   - Cell A's $+3\text{ dBm}$ power boost causes adjacent channel interference spikes across Cells C, D, and E.
   - **Result**: Network blackout, severe drop rate spike ($>15\%$), and customer SLA breach.

### Coordinated Execution (ARBITER Arbitration)
1. **Ingress & Graph Construction**: Proposals land within the same $100\text{ ms}$ arbitration epoch. ARBITER's conflict classifier detects two clash edges: $(ES, LB)$ (capacity starvation) and $(CCO, ES)$ (cell-edge coupling).
2. **Decision Ladder Evaluation**:
   - **Emergency Lane**: No emergency flag present $\to$ proceed through standard ladder.
   - **Joint Twin Evaluation**: Turning Cell B OFF while offloading traffic fails hard safety floors ($D_{\text{drop}} > 0.02$).
   - **DELAY Energy Saving**: ES is delayed (`hold_count = 1`) because an active handover/transfer is requested on its cell. Cell B is held active.
   - **MODIFY Load Balancing**: Golden-section bisection evaluates offload candidate values and determines that a **$15.28\%$ offload** balances Cell A's load without congesting Cell B.
   - **MODIFY Coverage/Capacity**: Power boost is bounded down to **$+1.15\text{ dBm}$** ($44.15\text{ dBm}$), maintaining coverage while keeping interference below the $-104\text{ dBm}$ floor.
3. **Actuation & Verification**:
   - Only approved and modified actions are applied by the Actuator Enforcer.
   - **Zero users dropped ($0.0\%$ drop rate)**.
   - Handovers succeed seamlessly.
   - Energy savings are scheduled safely once handovers complete.

---

## System Architecture & Data Flow

ARBITER operates on a strict $100\text{ ms}$ discrete-time control loop:

```
 +-----------------------------------------------------------------------------------+
 |                             AUTONOMOUS CONTROLLERS                                |
 |   [ Energy Saving ]    [ Load Balancing ]    [ Coverage/Capacity ]    [ Emergency ]|
 +---------+--------------------+---------------------+--------------------+---------+
           |                    |                     |                    |
           +--------------------+----------+----------+--------------------+
                                           | Command Proposals (UUID, tick, params)
                                           v
 +-----------------------------------------------------------------------------------+
 | 1. TEMPORAL INGRESS & QUEUE (arbiter/queue/)                                      |
 |    * 100 ms Proposal Queue (Sliding Ingress Window)                               |
 |    * Emergency Priority Bypass Lane (Sub-5ms zero-ladder override)                |
 +-----------------------------------------+-----------------------------------------+
                                           |
                                           v
 +-----------------------------------------------------------------------------------+
 | 2. CONFLICT DETECTOR (arbiter/detector/)                                          |
 |    * Hop-2 Spatial Topology Graph (Hexagonal Adjacency)                           |
 |    * Pairwise Conflict Classifier (DIRECT, CELL_EDGE, STARVATION, CASCADE)        |
 |    * Tarjan Cycle Detector (Ping-Pong Loop Elimination)                           |
 |    * Connected Components Partitioner (Parallel Component Processing)             |
 +-----------------------------------------+-----------------------------------------+
                                           | Independent Clash Clusters
                                           v
 +-----------------------------------------------------------------------------------+
 | 3. ARBITRATION & DECISION ENGINE (arbiter/engine/)                                |
 |    * Multi-Branch Decision Ladder: APPROVE -> MODIFY -> DELAY -> REJECT            |
 |    * Bounded Golden-Section Bisection Solver (Max 6 Twin Invocations)             |
 |    * Uncertainty Guard (|dU| < margin triggers conservative hold/reject)          |
 |    * Dynamic RiskDial Utility Scoring: U = w_cov*Cov - w_nrg*Nrg - w_int*Int ...  |
 |    * Multi-Tick Sequential Plan Synthesizer (ArbitrationPlan & PlanSteps)         |
 +--------------------+--------------------------------------------------------------+
                      | Candidate Deltas              ^ Calibrated Uncertainty Bounds
                      v                               | (Split Conformal Prediction)
 +----------------------------------------------------+------------------------------+
 | 4. ANALYTICAL DIGITAL TWIN (arbiter/twin/)                                        |
 |    * Sub-10ms Closed-Form Analytical Radio Propagation & Load Model               |
 |    * Split Conformal Prediction Intervals (Coverage, Energy, Interference, Drops)  |
 |    * Online Recursive MAPE Calibration Loop (Tracks Ground Truth Residuals)       |
 |    * Fail-Safe Timeout Enforcer (Fallback to Last-Known-Good on Twin Failure)     |
 +--------------------+--------------------------------------------------------------+
                      | Approved / Modified Parameter Mutexes
                      v
 +-----------------------------------------------------------------------------------+
 | 5. ACTUATOR & PERSISTENCE (arbiter/actuator/ & arbiter/storage/)                  |
 |    * Actuator Enforcer with Pre-Flight Safety Checks & Rate Limiting              |
 |    * Atomic SQLite Commit Logger (audit_decisions, commit_log, lkg_store)         |
 |    * Automatic Rollback Engine on Downstream Network Assertion Failure            |
 +--------------------+--------------------------------------------------------------+
                      | Physical Parameter Actuation
                      v
 +-----------------------------------------------------------------------------------+
 | 6. CELLULAR RADIO SIMULATOR (arbiter/simulator/)                                  |
 |    * 5-Cell Core & 19-Cell Wrap-Around Hexagonal Topology                         |
 |    * Cost231 Hata Path Loss, Fast/Slow Log-Normal Fading, SINR & Shannon Capacity |
 |    * Milan Telecom Real-World Dynamic Traffic Profiles (Peak, Surge, Quiet 2 AM)  |
 |    * Snapshot & Forking Engine (Deterministic Checkpoint / State Restore)         |
 +--------------------+--------------------------------------------------------------+
                      | Telemetry Samples (SINR, Drops, Power, Load)
                      v
 +-----------------------------------------------------------------------------------+
 | 7. TELEMETRY, API & LIVE DASHBOARD (arbiter/telemetry/, api/, dashboard/)         |
 |    * Telemetry Monitor & Ingest Aggregator                                        |
 |    * FastAPI REST Engine (16+ Endpoints) & WebSocket Real-Time Broadcast (/ws/live) |
 |    * 100vh Dense NOC Dark Dashboard (7-Cell Hex Canvas, Sparklines, RiskDial)     |
 |    * Flight Recorder (Counterfactual Replay & Causal Explanation Engine)          |
 |    * Pareto Frontier Evaluator & Offline Optimality Gap Benchmark Suite           |
 +-----------------------------------------------------------------------------------+
```

---

## Modular Deep Dive

### Branch 1: RAN Simulator & Autonomous Controllers
*Located in `arbiter/simulator/` and `arbiter/controllers/`*

* **Propagation Model (`propagation.py`)**: Implements Cost-231 Hata path loss for urban cellular macro environments ($f = 2100\text{ MHz}$, $h_{\text{bs}} = 30\text{ m}$, $h_{\text{ue}} = 1.5\text{ m}$). Computes exact Euclidean and wrap-around distances, thermal noise floor ($-174\text{ dBm/Hz}$ over $20\text{ MHz}$ bandwidth $\approx -101\text{ dBm}$), and inter-cell interference sum:
  $$\text{SINR}_i = \frac{P_{\text{rx}, i}}{\sum_{j \neq i} P_{\text{rx}, j} + \sigma_{\text{noise}}^2}$$
* **Grid Topology (`grid.py`, `cell.py`)**: Models 5-cell core and 19-cell wrap-around hexagonal layouts with inter-site distance (ISD) of $500\text{ m}$, azimuth orientations, antenna tilts, and transmit powers ($30\text{ dBm}$ to $46\text{ dBm}$).
* **Dynamic Traffic Generator (`traffic.py`)**: Simulates spatial UE arrivals, mobility, active calls, and data demands based on real-world Milan Telecom hourly traffic distributions (Peak, Surge, and Quiet Night scenarios).
* **Autonomous Controllers (`arbiter/controllers/`)**:
  - `EnergySavingController`: Monitors load ratios; triggers sleep transitions ($P_{\text{tx}} = 0\text{ W}$) when load drops below $15\%$.
  - `LoadBalancingController`: Triggers offload handovers when macro cells exceed $80\%$ PRB utilization.
  - `CoverageCapacityController`: Detects edge SINR degradation ($<3\text{ dB}$) and increases transmit power or antenna tilt.
  - `PublicSafetyController`: Issues high-priority preemption and emergency power configurations.
* **Snapshot & Forking (`snapshot.py`, `engine.py`)**: Implements `SnapshotCapable` protocol, allowing the simulation engine to freeze its state into an in-memory `SimulationSnapshot`, branch off parallel futures for counterfactual validation, and restore instantly.

### Branch 2: Proposal Queue, Conflict Graph & Decision Ladder
*Located in `arbiter/queue/`, `arbiter/detector/`, and `arbiter/engine/`*

* **Proposal Queue (`proposal_queue.py`, `emergency_lane.py`)**: Buffers incoming asynchronous proposals into synchronous $100\text{ ms}$ windows. Proposals flagged with `emergency_flag = True` are diverted into an emergency lane that bypasses the arbitration ladder with sub-5ms latency.
* **Conflict Classifier (`classifier.py`, `graph.py`)**: Builds an undirected NetworkX conflict graph over proposals within a 2-hop topological radius ($R \le 2$). Classifies clashes into:
  - `DIRECT`: Two controllers altering the same cell and parameter.
  - `CELL_EDGE_COUPLING`: Alterations to adjacent cells that couple through mutual interference.
  - `CAPACITY_STARVATION`: Offloading users into a cell targeted for shutdown.
  - `CASCADE_HANDOVER`: Chained CIO adjustments creating handover loops.
* **Cycle Detector (`cycle_detector.py`)**: Implements Tarjan's strongly connected components algorithm to flag cyclic ping-pong migrations across cells, instantly rejecting oscillating proposals with reason code `CYCLIC_PINGPONG`.
* **Component Partitioner (`partitioner.py`)**: Splits the global conflict graph into disjoint connected components, allowing non-interfering clusters of cells to be solved in parallel worker threads.
* **Decision Ladder (`ladder.py`)**: Executes a 4-rung priority ladder per conflict component:
  1. **APPROVE**: Evaluates joint execution. If joint utility $\Delta U > 0$, confidence margin holds, and no safety floor is breached, approves the entire cluster.
  2. **UNCERTAINTY GUARD**: If predicted gain $|\Delta U| < \text{confidence\_margin}$, delays or rejects commands to prevent actuation on model noise.
  3. **MODIFY**: For continuous parameters ($P_{\text{tx}}$, offload ratio), executes a golden-section bisection search bounded by a strict 6-evaluation digital twin budget.
  4. **DELAY**: If a clash is transient (e.g., active handovers in flight), delays the command up to `MAX_HOLD_TICKS = 3`.
  5. **REJECT**: Rejects proposals causing unmitigated safety floor breaches (`FLOOR_BREACH`) or twin timeout (`TWIN_TIMEOUT`).

### Branch 3: Conformal Digital Twin, Actuator & SQLite Log
*Located in `arbiter/twin/`, `arbiter/actuator/`, and `arbiter/storage/`*

* **Analytical Digital Twin (`analytical_model.py`, `evaluator.py`)**: Evaluates candidate parameter deltas in under $10\text{ ms}$ using closed-form matrix formulations without running full discrete-event packet simulations. Predicts coverage ratio, normalized energy, interference, and drop rate.
* **Conformal Safety Intervals (`conformal.py`)**: Computes finite-sample non-parametric uncertainty intervals for predicted KPIs using split conformal prediction over a sliding window of ground-truth simulation residuals.
* **Online Calibration Loop (`calibration.py`)**: Compares predicted KPIs against actual simulation telemetry at every tick, calculating Mean Absolute Percentage Error (MAPE). Automatically falls back to conservative margins if MAPE exceeds $10\%$.
* **Actuator Enforcer (`enforcer.py`, `rollback.py`)**: Validates approved decisions against physical hardware boundaries (rate-of-change limits, min/max power bounds). Commits valid parameter updates to the target network or triggers automatic rollback to the **Last Known Good (LKG)** state stored in SQLite if post-actuation sanity checks fail.
* **SQLite Persistence (`database.py`, `commit_log.py`, `lkg_store.py`)**: Provides ACID transaction logging in SQLite WAL mode:
  - `audit_decisions`: Immutable audit trail of every arbitration decision, approved value, $\Delta U$, reason code, and consumed twin evaluations.
  - `commit_log`: Physical hardware actuation records with sequence numbers, cell IDs, old values, and new values.
  - `lkg_store`: High-speed snapshot of validated stable network states for rollback recovery.

### Branch 4: Telemetry Monitor, WebSocket Live Broadcast & NOC Dashboard
*Located in `arbiter/telemetry/`, `arbiter/api/`, `arbiter/dashboard/`, and `arbiter/benchmarks/`*

* **Telemetry Monitor (`monitor.py`, `shadow_mode.py`)**: Aggregates real-time cell KPIs (throughput, power, active users, call drops) and runs a parallel shadow-mode evaluator that tracks what network KPIs *would have been* without arbitration.
* **FastAPI Server (`server.py`, `routes.py`, `websocket.py`)**: Provides asynchronous REST endpoints and a high-throughput WebSocket broadcast channel (`/api/v1/ws/live`) delivering 100ms live telemetry updates to connected clients.
* **NOC Dashboard (`dashboard/index.html`, `app.js`, `style.css`)**: Dark-themed, 100vh dense Network Operations Center interface built in HTML5, Vanilla JavaScript, and SVG:
  - Interactive 7-cell hexagonal grid topology with real-time RF heatmaps and cell inspection.
  - Live Ingress Stream & Action Badges with controller filter chips (ES, LB, CCO, PS) and CSV export.
  - Real-time Chart.js / Canvas sparklines and dual time-series charts (Instantaneous Dropped Users & Power kW).
  - Multi-tab operational workspace featuring physical topology, Risk Dial / Pareto frontier, optimality benchmarks, and ESG environmental accounting.
* **Flight Recorder (`app.js`, `routes.py`)**: Interactive post-mortem inspection modal allowing NOC operators to click any historical decision, replay a counterfactual simulation rollout, compare side-by-side KPI deltas, and review automated causal explanations.
* **Benchmarking Suite (`runner.py`, `scenarios.py`, `pareto.py`, `ablations.py`)**: Comprehensive Monte Carlo harness running multi-seed statistical evaluations comparing ARBITER against Uncoordinated and Static Priority baselines across Milan Peak, Milan Surge, and Quiet 2 AM scenarios.

---

## Advanced Engineering Features (v2 & v3)

### 1. Split Conformal Prediction & Calibrated Safety Intervals
Project ARBITER v2 incorporates split conformal prediction to quantify digital twin uncertainty:
* Maintains a rolling calibration memory ($N = 200$) of absolute prediction residuals:
  $$R_t = |y_{\text{simulated}} - \hat{y}_{\text{twin}}|$$
* For a specified miscoverage error rate $\alpha = 0.10$ ($90\%$ confidence level), it computes the empirical conformal quantile:
  $$\hat{q} = \text{Quantile}\left(R, \frac{\lceil (n+1)(1-\alpha) \rceil}{n}\right)$$
* Generates prediction intervals $[\hat{y} - \hat{q}, \hat{y} + \hat{q}]$ with finite-sample marginal coverage guarantees:
  $$\mathbb{P}(Y_{n+1} \in C(X_{n+1})) \ge 1 - \alpha$$
* When fewer than 30 calibration samples are available, the system marks predictions as uncalibrated and expands intervals to wide conservative defaults, preventing premature overconfidence.
* Includes a live **Twin Drift Injection Hook** on the dashboard to test arbitrator resilience when model accuracy degrades.

### 2. Multi-Tick Sequential Plan Synthesis
When conflicting proposals cannot be resolved safely in a single $100\text{ ms}$ tick, ARBITER's v2 planner synthesizes a multi-tick sequential execution plan (`ArbitrationPlan`):
* Decomposes complex compound interactions into discrete, chronological `PlanSteps`.
* Assigns tick offsets and precondition guards to each step (e.g., `"load(cell_A) < 0.60"` or `"active_ho(cell_B) == 0"`).
* Continuously evaluates preconditions against live telemetry before actuating downstream steps; automatically aborts or re-arbitrates if environmental conditions diverge.

### 3. Counterfactual Replay & Flight Recorder
NOC operators can inspect any arbitration decision through the interactive Flight Recorder:
* Uses the simulator's checkpoint/forking engine to branch two identical parallel rollouts:
  - **With ARBITER**: Evaluates the coordinated decision trajectory over a 10-tick horizon.
  - **Without ARBITER**: Evaluates the uncoordinated raw proposal execution over the same horizon.
* Computes exact KPI differentials ($\Delta \text{Drops}$, $\Delta \text{SINR}$, $\Delta \text{Power}$, $\Delta \text{Utility}$).
* Synthesizes automated natural language attribution statements explaining why arbitration intervened (e.g., *"Uncoordinated execution triggered a blackout on Cell B (0 W) while receiving offloaded traffic from Cell A, causing 14 call drops. ARBITER delayed Cell B sleep and safely bounded offload, maintaining 0 dropped users."*).

### 4. Dynamic RiskDial & Pareto Frontier Optimization
ARBITER supports dynamic run-time tuning of multi-objective priorities via the **Risk Dial**:
* Operators select preset operational profiles or customize weights:
  - **Balanced**: $w_{\text{cov}} = 0.30$, $w_{\text{nrg}} = 0.20$, $w_{\text{int}} = 0.20$, $w_{\text{drop}} = 0.30$
  - **Night Saver**: $w_{\text{cov}} = 0.15$, $w_{\text{nrg}} = 0.50$, $w_{\text{int}} = 0.15$, $w_{\text{drop}} = 0.20$
  - **Rush-Hour Safe**: $w_{\text{cov}} = 0.35$, $w_{\text{nrg}} = 0.05$, $w_{\text{int}} = 0.20$, $w_{\text{drop}} = 0.40$
* Evaluates non-dominated Pareto frontiers over the 2D trade-off space between **Cluster Energy Consumption (kW)** and **User Drop Risk (%)**. Renders interactive Pareto frontiers with active operating point indicators in real time.

### 5. Offline Optimality Gap Benchmark
To prove that ARBITER's sub-10ms heuristic decision ladder does not sacrifice optimization quality, the system includes an exhaustive offline combinatorial solver:
* Explores the full combinatorial space of discretized candidate actions across all cells.
* Benchmarks online ARBITER performance against the theoretical offline Pareto optimum:
  - **Optimality Gap**: Typically **$< 4\%$** relative to the theoretical global maximum.
  - **Computation Time**: ARBITER achieves this in **$3.8\text{ ms}$** vs **$1.74\text{ s}$** for the combinatorial solver (**$>400\times$ speedup**), meeting strict real-time O-RAN latency constraints.

### 6. Environmental & Economic ESG Impact Accounting
Quantifies green cellular networking metrics in real time:
* **Energy Savings**: Cumulative and per-tick kilowatt-hours ($\text{kWh}$) saved through coordinated RF sleep cycles.
* **CO2 Avoidance**: Real-time greenhouse gas reduction calculated using Central Electricity Authority (CEA) grid emission factors ($820\text{ gCO}_2/\text{kWh}$).
* **Economic Savings**: Monetary savings calculated against dynamic commercial electricity tariffs (e.g., off-peak night rate $\text{₹}7.00/\text{kWh}$).
* **Fleet Extrapolation**: Projects annual environmental and economic savings scaled across a 1,000-site macro network ($176.4\text{ MWh/yr}$, $\text{₹}12.35\text{ Lakhs/yr}$, $144.7\text{ Tons CO}_2/\text{yr}$).

### 7. Simulator Snapshotting, Forking & State Restoration
Full compliance with the `SnapshotCapable` protocol allows zero-overhead simulation branching:
* Captures complete cell states, user positions, active channels, and fading realizations into opaque `SimulationSnapshot` instances.
* Enables Monte Carlo branch-and-bound rollouts without side-effects on the primary simulation timeline.

---

## Mathematical & Algorithmic Foundations

### 1. Multi-Objective Scalar Utility Function
At each arbitration tick, candidate parameter sets are ranked using a scalar utility function balancing conflicting operational objectives:

$$\Delta U = w_{\text{cov}} \cdot \Delta \text{KPI}_{\text{cov}} - w_{\text{nrg}} \cdot \Delta \text{KPI}_{\text{nrg}} - w_{\text{int}} \cdot \Delta \text{KPI}_{\text{int}} - w_{\text{drop}} \cdot \Delta \text{KPI}_{\text{drop}}$$

Where:
* $\Delta \text{KPI}_{\text{cov}} = \frac{\text{Cov}_{\text{cand}} - \text{Cov}_{\text{base}}}{\text{Cov}_{\text{base}}}$ (Coverage ratio improvement)
* $\Delta \text{KPI}_{\text{nrg}} = \frac{P_{\text{cand}} - P_{\text{base}}}{P_{\text{base}}}$ (Relative power increase)
* $\Delta \text{KPI}_{\text{int}} = \frac{I_{\text{cand}} - I_{\text{base}}}{I_{\text{base}}}$ (Relative interference increase)
* $\Delta \text{KPI}_{\text{drop}} = \frac{D_{\text{cand}} - D_{\text{base}}}{\max(1, D_{\text{base}})}$ (Relative drop rate increase)
* Subject to invariant safety floors: $\text{Cov} \ge 0.90$ and $\text{DropRate} \le 0.02$.

### 2. Bounded Golden-Section Bisection Algorithm
When continuous parameters clash (e.g., offload ratios or transmit power), ARBITER searches for the optimal compromise value $\theta^* \in [\theta_{\text{current}}, \theta_{\text{requested}}]$:

1. Initialize search interval $[a, b] = [\theta_{\text{current}}, \theta_{\text{requested}}]$.
2. Compute interior evaluation points using the golden ratio $\phi = \frac{\sqrt{5}-1}{2} \approx 0.618$:
   $$x_1 = b - \phi(b - a), \quad x_2 = a + \phi(b - a)$$
3. Query the digital twin for predicted utilities $U(x_1)$ and $U(x_2)$.
4. Narrow the search window while respecting safety floors until convergence or evaluation budget depletion ($K \le 6$ calls):
   $$\text{If } U(x_1) > U(x_2) \implies b \leftarrow x_2, \quad \text{else } a \leftarrow x_1$$
5. If a feasible improving point is found, issue `MODIFY(approved_value=theta*)`; otherwise fall back to `DELAY` or `REJECT`.

---

## Live NOC Dashboard Walkthrough

The ARBITER dashboard is served directly by FastAPI at `http://localhost:8000/`:

```
+--------------------------------------------------------------------------------------------------+
| ARBITER • 2 AM COLLISION MONITOR       [Inject Twin Drift]  [● Connected]  [14:22:06.100]        |
+--------------------------------------------------------------------------------------------------+
| TOTAL DROPPED USERS   | TOTAL POWER (kW)      | MEAN SINR (dB)        | TWIN MAPE (%)            |
| 0      [0.000 - 0.005]| 100.1 kW [98 - 102]   | 8.49 dB [8.2 - 8.8]   | 4.2%  [|||||||.....] 10% |
+-------------------------------------------------------+------------------------------------------+
| LIVE INGRESS & ACTION BADGES                          | INSTANTANEOUS DROPPED USERS              |
| [ALL] [ES] [LB] [CC] [PS] | [ALL] [APPROVE] [MODIFY]  |  14 |                                    |
| ----------------------------------------------------- |     |        Baseline (Uncoordinated)    |
| Time     Ctrl Target Param    Req    Verdict  Reason  |   0 +----------------------------------  |
| 02:00.10 ES   Cell_B SLEEP    0.00   DELAY    HO_ACT  |     0   10   20   30   40   50 (Ticks)   |
| 02:00.15 LB   Cell_A OFFLOAD  0.40   MODIFY   HEADRM  |------------------------------------------+
| 02:00.20 CC   Cell_A POWER    46.0   MODIFY   HEADRM  | CLUSTER POWER CONSUMPTION (kW)           |
|                                                       | 120 |                                    |
| ARBITRATION DECISIONS AUDIT (Click to Replay)         |  80 |~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~   |
| ----------------------------------------------------- |     0   10   20   30   40   50 (Ticks)   |
| Tick  Action  Approved  dU     Reason      Hold  Time |------------------------------------------+
| 06    DELAY   --        +0.00  ACTIVE_HO   1     02:00| [Physical Topology] [RiskDial] [ESG]    |
| 06    MODIFY  0.1528    +0.21  HEADROOM    0     02:00|   (A)---(B)      * Hexagonal Cell Grid   |
| 06    MODIFY  44.146    +0.18  HEADROOM    0     02:00|  /   \ /   \     * Interactive Inspector |
|                                                       | (C)---(D)---(E)  * Load, Tx, Tilt, Users |
+-------------------------------------------------------+------------------------------------------+
```

### Key UI Features:
1. **Fixed 100vh Dense Layout**: Clean Network Operations Center dark theme with zero vertical window scroll.
2. **Conformal Uncertainty Badges**: Every KPI displays calibrated lower and upper safety bounds `[lower - upper]` derived from real-time conformal prediction.
3. **Interactive Filter Strip**: Filter live incoming proposals by controller type or arbitration action; includes single-click **Export to CSV**.
4. **Flight Recorder Counterfactual Modal**: Click any row in the audit table to launch the side-by-side counterfactual viewer, comparing ARBITER's coordinated outcome directly against the uncoordinated failure.
5. **Multi-Tabbed Secondary Pane**:
   - **Physical Topology**: Real-time SVG rendering of the 7-cell hexagonal network with cell selection, active user count, and sleep state badges.
   - **Risk Dial & Pareto Frontier**: Interactive weight presets with live SVG Pareto trade-off curve.
   - **Optimality Gap**: Side-by-side visual bars comparing ARBITER against the offline Pareto solver.
   - **ESG Impact**: Real-time monetary savings (INR), carbon offset (gCO2), and 1,000-site fleet projections.

---

## Repository Directory Structure

```
Bits2Wave/
├── arbiter/                               # Core ARBITER Engine Package
│   ├── actuator/                          # Branch 3: Physical Actuation Enforcer
│   │   ├── enforcer.py                    # Hardware boundary checks & mutexes
│   │   └── rollback.py                    # Automatic rollback on assertion failure
│   ├── api/                               # Branch 4: REST API & WebSocket Server
│   │   ├── routes.py                      # 16+ REST route handlers
│   │   ├── server.py                      # FastAPI lifespan, CORS & static mount
│   │   └── websocket.py                   # High-cadence WebSocket broadcast
│   ├── benchmarks/                        # Branch 4: Evaluation & Benchmarking
│   │   ├── ablations.py                   # Component ablation study harness
│   │   ├── metrics.py                     # 95% CI statistical evaluators
│   │   ├── pareto.py                      # Multi-objective Pareto frontier solver
│   │   ├── runner.py                      # Multi-seed Monte Carlo scenario runner
│   │   └── scenarios.py                   # Benchmark scenarios (Peak, Surge, Quiet)
│   ├── controllers/                       # Branch 1: Autonomous Controllers
│   │   ├── base.py                        # Base autonomous controller class
│   │   ├── coverage_capacity.py           # Coverage & Capacity Optimization (CCO)
│   │   ├── energy_saving.py               # Energy Saving Controller (ES)
│   │   ├── load_balancing.py              # Load Balancing Controller (LB)
│   │   ├── public_safety.py               # Emergency & Public Safety (PS)
│   │   └── scenarios.py                   # Canonical 2 AM collision orchestrator
│   ├── dashboard/                         # Branch 4: NOC Web Dashboard
│   │   ├── app.js                         # State management, WS & charts
│   │   ├── index.html                     # Fixed 100vh dense NOC grid
│   │   └── style.css                      # Production NOC dark theme styling
│   ├── detector/                          # Branch 2: Conflict Detection & Graph
│   │   ├── classifier.py                  # Spatial-parameter conflict classifier
│   │   ├── cycle_detector.py              # Tarjan ping-pong cycle detector
│   │   ├── graph.py                       # NetworkX conflict graph builder
│   │   └── partitioner.py                 # Connected components partitioner
│   ├── engine/                            # Branch 2: Arbitration Referee
│   │   ├── actuator_adapter.py            # Bridge from engine to ActuatorEnforcer
│   │   ├── bisection.py                   # Golden-section bisection solver
│   │   ├── contracts.py                   # Arbitration schemas & reason codes
│   │   ├── ladder.py                      # 4-rung priority decision ladder
│   │   ├── plan_executor.py               # Multi-tick plan runtime executor
│   │   ├── planner.py                     # Multi-tick sequential plan synthesizer
│   │   ├── twin_adapter.py                # Bridge from engine to Digital Twin
│   │   └── utility.py                     # Multi-objective utility calculator
│   ├── queue/                             # Branch 2: Ingress Buffer
│   │   ├── emergency_lane.py              # High-priority sub-5ms bypass lane
│   │   ├── orchestrator.py                # Single-tick orchestration engine
│   │   └── proposal_queue.py              # 100ms temporal proposal buffer
│   ├── shared/                            # Cross-Branch Shared Core
│   │   ├── constants.py                   # Default thresholds, weights & bounds
│   │   ├── database.py                    # SQLite DDL & init scripts
│   │   ├── database_v2.py                 # v2 DDL (plans, intervals, impact)
│   │   ├── models.py                      # Frozen Pydantic data contracts
│   │   └── v2_contracts.py                # Frozen v2 protocols & data schemas
│   ├── simulator/                         # Branch 1: Cellular RAN Simulator
│   │   ├── cell.py                        # Cell state, power, tilt & user pools
│   │   ├── collision_search.py            # Automated collision scenario generator
│   │   ├── counterfactual.py              # Counterfactual rollout simulator
│   │   ├── engine.py                      # Discrete-time simulation engine (CLI)
│   │   ├── grid.py                        # Hexagonal topology & wrap-around math
│   │   ├── optimality.py                  # Offline combinatorial Pareto solver
│   │   ├── propagation.py                 # Cost-231 Hata RF propagation model
│   │   ├── snapshot.py                    # SnapshotRing buffer for state restore
│   │   └── traffic.py                     # Milan Telecom traffic generator
│   ├── storage/                           # Branch 3: SQLite Persistence Layer
│   │   ├── commit_log.py                  # Hardware actuation audit log
│   │   ├── database.py                    # SQLite connection & WAL management
│   │   └── lkg_store.py                   # Last Known Good state manager
│   ├── telemetry/                         # Branch 4: Telemetry Processing
│   │   ├── impact.py                      # ESG carbon & economic calculators
│   │   ├── monitor.py                     # Ingestion & KPI time-series storage
│   │   └── shadow_mode.py                 # Uncoordinated comparator engine
│   └── twin/                              # Branch 3: Analytical Digital Twin
│       ├── analytical_model.py            # Sub-10ms closed-form RF model
│       ├── calibration.py                 # Online MAPE calibration loop
│       ├── conformal.py                   # Split conformal safety intervals
│       ├── evaluator.py                   # DigitalTwinEvaluator engine
│       ├── fail_safe.py                   # Timeout fallback manager
│       └── schemas.py                     # Twin request & prediction schemas
├── fixtures/                              # Test & Benchmark Datasets
│   ├── traffic/                           # Milan hourly traffic distributions
│   └── v2/                                # Frozen mock payloads for API tests
├── tests/                                 # Complete Test Suite (488 Tests)
│   ├── branch1/                           # Simulator & controller unit tests
│   ├── branch2/                           # Queue, graph & decision ladder tests
│   ├── branch3/                           # Digital twin & actuator tests
│   ├── branch4/                           # Telemetry, API & dashboard tests
│   ├── contracts/                         # Cross-branch schema invariant tests
│   └── e2e/                               # End-to-end 2 AM collision tests
├── .env.example                           # Unified configuration template
├── arbiter_production.db                  # Local SQLite database (auto-generated)
├── Merge_and_Integration_Playbook.pdf     # Engineering integration runbook
├── requirements.txt                       # Production & test dependencies
└── README.md                              # Master Project Documentation
```

---

## Installation & Quickstart Guide

### Prerequisites
* **Python**: Python 3.10, 3.11, 3.12, 3.13, or 3.14.
* **Operating System**: Linux, macOS, or Windows (tested on PowerShell and bash).
* **Git**: For version control.

### Quickstart: One-Command Live Demonstration
To launch the complete closed-loop system (API server, live 3D visualizer, and simulation engine streaming the 2 AM collision scenario):

```powershell
# Windows PowerShell
scripts\run_demo.ps1
```
This automated demo script:
1. Starts the Uvicorn ASGI server on port `8000` and verifies health probe readiness.
2. Automatically opens your browser to the 3D Digital Twin visualization at `http://localhost:8000/dashboard/3d`.
3. Streams the discrete 2 AM collision scenario over WebSockets with real-time ARBITER multi-controller coordination.

---

### Step 1: Clone Repository & Create Virtual Environment
```bash
# Clone the repository
git clone https://github.com/gsvenkatsai/Bits2Wave-Arbiter.git
cd Bits2Wave-Arbiter

# Create virtual environment
python -m venv .venv

# Activate virtual environment
# On Linux/macOS:
source .venv/bin/activate
# On Windows PowerShell:
.venv\Scripts\Activate.ps1
```

### Step 2: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 3: Configure Environment Variables
Copy `.env.example` to create your local `.env`:
```bash
cp .env.example .env
```

### Step 4: Initialize SQLite Database
Initialize the required tables (`commit_log`, `audit_decisions`, `telemetry_history`, `lkg_store`) in SQLite:
```bash
python -c "from arbiter.shared.database import init_database; init_database('./arbiter_production.db')"
```

### Step 5: Start ARBITER FastAPI Server & Dashboard
Run the ASGI server hosting the REST API, WebSocket broadcast, and live NOC UI:
```bash
uvicorn arbiter.api.server:app --host 0.0.0.0 --port 8000 --reload
```
Open your browser and navigate to:
**`http://localhost:8000/`** or **`http://localhost:8000/dashboard/`**

### Step 6: Run the Discrete-Time Cellular Simulator
In a separate terminal, launch the simulation engine. To run the canonical 2 AM collision scenario and stream telemetry directly to your browser:
```bash
python -m arbiter.simulator.engine --scenario 2am_collision --ticks 50 --stream-ws
```

### Step 7: Offline Optimality & Collision Search CLIs

To evaluate combinatorial optimality against the full-state brute force benchmark:
```bash
python -m arbiter.simulator.optimality --scenario 2am_collision --seed 42
```

To search for uncoordinated collision anomalies across simulation runs:
```bash
python -m arbiter.simulator.counterfactual search --scenario 2am_collision
```

---

## Configuration Reference (.env)

| Parameter | Default Value | Description |
| :--- | :--- | :--- |
| **`SIM_TOPOLOGY`** | `5_CELL_CORE` | Radio grid layout (`5_CELL_CORE` or `19_CELL`). |
| **`SIM_CARRIER_FREQ_MHZ`** | `2100.0` | Operating RF carrier frequency in MHz. |
| **`SIM_TICK_DURATION_MS`** | `100` | Duration of one discrete simulation epoch in milliseconds. |
| **`SIM_TRAFFIC_SCENARIO`** | `2_AM_COLLISION` | Active traffic scenario (`2_AM_COLLISION`, `MILAN_PEAK`, `MILAN_SURGE`). |
| **`ARBITER_MAX_HOLD_TICKS`** | `3` | Maximum ticks a transient clashing proposal can be delayed before rejection. |
| **`ARBITER_BISECTION_MAX_EVALS`** | `6` | Maximum digital twin evaluations consumed during golden-section search. |
| **`ARBITER_WEIGHT_COVERAGE`** | `0.3` | RiskDial weight ($w_{\text{cov}}$) for coverage optimization. |
| **`ARBITER_WEIGHT_ENERGY`** | `0.2` | RiskDial weight ($w_{\text{nrg}}$) for energy saving. |
| **`ARBITER_WEIGHT_INTERFERENCE`** | `0.2` | RiskDial weight ($w_{\text{int}}$) for interference mitigation. |
| **`ARBITER_WEIGHT_DROP_RATE`** | `0.3` | RiskDial weight ($w_{\text{drop}}$) for call drop penalties. |
| **`TWIN_EVALUATION_TIMEOUT_MS`** | `100.0` | Hard deadline budget in milliseconds for digital twin evaluations. |
| **`TWIN_MAPE_THRESHOLD_PCT`** | `10.0` | Calibration threshold; twin triggers conservative fallback if MAPE exceeds this. |
| **`SQLITE_DB_PATH`** | `./arbiter_production.db` | File path for SQLite persistence. |
| **`SQLITE_WAL_MODE`** | `true` | Enables Write-Ahead Logging for high-concurrency read/write transactions. |
| **`FASTAPI_HOST`** | `0.0.0.0` | Binding host address for FastAPI. |
| **`FASTAPI_PORT`** | `8000` | Binding TCP port for FastAPI. |
| **`DASHBOARD_LIVE_BROADCAST_CADENCE_MS`** | `100` | Cadence in milliseconds for WebSocket telemetry broadcasts. |

---

## REST API & WebSocket Reference

### WebSocket Stream
* **`WS /api/v1/ws/live`**: Real-time bidirectional streaming channel broadcasting cell states, active UEs, live ingress proposals, arbitration action badges, and KPI sparklines every $100\text{ ms}$.

### Core REST Endpoints
| Method | Path | Description |
| :--- | :--- | :--- |
| `GET` | `/api/v1/health` | Healthcheck returning service status, version (`1.0.0`), and database connectivity. |
| `GET` | `/api/v1/state/cells` | Returns physical configuration and current RF states for all active cells. |
| `GET` | `/api/v1/decisions/history` | Fetches recent arbitration decisions from `audit_decisions` table (supports `limit=N`). |
| `POST` | `/api/v1/telemetry/ingest` | Ingestion endpoint called by the simulator to push tick telemetry into the API and broadcast over WebSocket. |
| `GET` | `/api/v1/recorder/decisions` | Lists all historical arbitration decisions eligible for counterfactual flight recording. |
| `GET` | `/api/v1/recorder/counterfactual/{id}` | Replays counterfactual rollout for a specific decision, returning side-by-side KPI diffs and causal explanations. |
| `GET` | `/api/v1/plans` | Retrieves synthesized multi-tick sequential plans and pending precondition steps. |
| `GET` | `/api/v1/twin/prediction` | Returns latest digital twin prediction with conformal safety intervals and calibration status. |
| `POST` | `/api/v1/twin/drift` | Diagnostic hook to simulate model drift by inflating uncertainty intervals. |
| `GET` | `/api/v1/risk-dial` | Returns active RiskDial multi-objective weights and operational profile label. |
| `POST` | `/api/v1/risk-dial` | Dynamically updates RiskDial weights at runtime. |
| `GET` | `/api/v1/benchmarks/pareto` | Sweeps multi-objective weights and returns non-dominated Pareto frontier points. |
| `GET` | `/api/v1/optimality` | Returns offline combinatorial optimality benchmark results and latency comparisons. |
| `GET` | `/api/v1/impact` | Returns ESG environmental savings (kWh saved, avoided CO2, monetary savings, 1,000-site fleet projections). |
| `POST` | `/api/v1/benchmarks/run` | Triggers Monte Carlo benchmark runner across multiple PRNG seeds and scenarios. |

---

## Testing & Verification

ARBITER includes a comprehensive test suite of **516 automated Python tests** and **30 Node.js 3D visualizer unit tests** spanning unit, integration, contract, and end-to-end verification.

### Run All Python Tests
```bash
python -m pytest tests/ -v
```

### Run Node.js 3D Unit Tests
```bash
node --test tests/branch4/test_data_layer.js tests/branch4/test_arbitration_mapper.js tests/branch4/test_ghost_world.js
```

### Run Offline Compliance Audit
```bash
python scripts/check_offline_3d.py
```

### Run by Specific Workstream / Branch
```bash
# Branch 1: Simulator, propagation, traffic, and autonomous controllers
pytest tests/branch1/ -v

# Branch 2: Queue, conflict graph, bisection, and decision ladder
pytest tests/branch2/ -v

# Branch 3: Conformal digital twin, actuator, and commit log
pytest tests/branch3/ -v

# Branch 4: Telemetry monitor, FastAPI routes, and WebSocket broadcasts
pytest tests/branch4/ -v

# Contracts: Verify frozen Pydantic models across branches
pytest tests/contracts/ -v
```

### Honest Notes on Assumptions & Methodology
* **Conformal Prediction**: Split conformal prediction yields empirical finite-sample coverage guarantees over the simulated distribution, assuming exchangeability of calibration and evaluation samples.
* **Environmental & Economic ESG Accounting**: Computations are based on a nominal Time-of-Day grid emission factor ($0.82\text{ kg CO}_2/\text{kWh}$) and commercial industrial peak tariff (₹8.50/kWh peak, ₹5.20/kWh off-peak).
* **Simulation-Based Evaluation**: Network states are generated by discrete-event cellular simulation grounded in standard Cost-231 Hata propagation and 3GPP TR 36.873 antenna patterns.

### Run End-to-End Closed-Loop 2 AM Collision Verification
```bash
pytest tests/e2e/test_2am_collision.py -v
```

### Run with Coverage Analysis
```bash
pytest --cov=arbiter --cov-report=term-missing
```

### System Invariants Verified by Tests
* **NFR-1 (Real-Time Ingress)**: The arbitration loop processes incoming batches in $< 100\text{ ms}$.
* **NFR-2 (Twin Latency)**: Analytical digital twin single-point evaluation executes in $< 10\text{ ms}$.
* **NFR-3 (Strict Evaluation Budget)**: Golden-section bisection never exceeds 6 twin evaluations per conflict component.
* **NFR-4 (Zero Call Drops)**: In the canonical 2 AM collision scenario, ARBITER ensures zero dropped calls ($0.0\%$ drop rate), whereas the uncoordinated baseline drops 14 calls.
* **NFR-5 (Statistical Significance)**: Multi-seed Monte Carlo benchmarks (10 random seeds) prove ARBITER achieves statistically significant utility gains over uncoordinated and static-priority baselines at a $95\%$ confidence level.
* **NFR-6 (Atomic Rollback)**: Any physical parameter corruption or downstream assertion failure triggers instantaneous state recovery to the Last Known Good (LKG) SQLite state.

---

## Engineering Team & Parallel Development Structure

ARBITER was engineered using a 4-developer concurrent branch architecture:

| Workstream | Developer | Branch Name | Primary Modules & Responsibilities |
| :--- | :--- | :--- | :--- |
| **Branch 1** | **Akshay A (Lead)** | `feat/ran-simulator-controllers` | Radio Simulator, Cost231 Hata, Milan Dynamic Traffic, 4 Controllers, Snapshot/Fork API |
| **Branch 2** | **H M Pranav** | `feat/arbitration-engine` | 100ms Ingress Queue, Emergency Lane, Conflict Graph, Tarjan Cycle Detector, Decision Ladder, Multi-Tick Plan Synthesis |
| **Branch 3** | **Aaditya V** | `feat/digital-twin-actuator` | Sub-10ms Analytical Digital Twin, Split Conformal Prediction, Actuator Enforcer, SQLite Commit Log & Rollback |
| **Branch 4** | **G S Venkat Sai**| `feat/telemetry-dashboard-benchmarking`| Telemetry Aggregator, FastAPI Server, WebSockets, 100vh NOC Dashboard, Flight Recorder, Pareto & Optimality Benchmarks |
| **Release** | **Team FourSUM** | `main` | Production Integration, End-to-End Closed-Loop Verification & Master Documentation |

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
