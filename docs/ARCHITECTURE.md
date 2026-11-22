# Project ARBITER: Technical Architecture Specification

## 1. System Overview

Project ARBITER is an autonomous multi-objective Conflict-Resolution Referee designed for Open Radio Access Networks (O-RAN) and Self-Organizing Networks (SON). When independent, uncoordinated control applications (rApps in Non-RT RIC or xApps in Near-RT RIC) issue competing parameter updates to the same cellular cluster, ARBITER acts as an online mediator. It intercepts proposals, predicts joint multi-cell impacts through a calibrated conformal digital twin, and synthesizes non-conflicting, Pareto-optimal joint actions within a strict sub-100 millisecond control loop.

---

## 2. Subsystem Breakdown by Ownership

### 2.1 Branch 1: Cellular RAN Simulator & Autonomous Controllers
- **Owner**: Akshay A
- **Primary Modules**: `arbiter/simulator/**`, `arbiter/controllers/**`
- **Core Responsibilities**:
  - `SimulationEngine` (`arbiter/simulator/engine.py`): Discrete-time 100 ms cellular network engine managing hexagonal 5-cell or 19-cell clusters, user mobility, downlink SINR computation, and native `frame()` and `cell_states()` generation.
  - `Cost231PropagationModel` (`arbiter/simulator/propagation.py`): Implements the standard COST-231 Hata empirical path loss model with carrier frequency $f_c = 2100\text{ MHz}$, base station height $h_b = 30\text{ m}$, user height $h_m = 1.5\text{ m}$, and 3D electrical downtilt antenna radiation patterns.
  - Autonomous Controllers (`arbiter/controllers/`):
    - `EnergySavingController`: Puts lightly loaded cells into standby mode when load stays below 20% for 5 consecutive ticks.
    - `LoadBalancingController`: Triggers offload handovers to adjacent neighbors when cell load exceeds 75%.
    - `CoverageCapacityController`: Detects coverage holes ($\text{SINR} < 0\text{ dB}$) and commands RF power boosts (up to 46 dBm).
    - `PublicSafetyController`: Emits emergency preemption commands tagged with `emergency_flag = True`.

---

### 2.2 Branch 2: Proposal Queue, Conflict Detector & Decision Engine
- **Owner**: H M Pranav
- **Primary Modules**: `arbiter/queue/**`, `arbiter/detector/**`, `arbiter/engine/**`
- **Core Responsibilities**:
  - `ProposalQueue` (`arbiter/queue/proposal_queue.py`): Ingress buffer with token-bucket rate limiting (`rate_limiter.py`) and priority queues indexed by controller type.
  - `EmergencyLane` (`arbiter/queue/emergency_lane.py`): Zero-latency bypass path routing life-safety public safety commands directly to actuation without waiting for arbitration windows.
  - `ConflictClassifier` & `ConflictGraphBuilder` (`arbiter/detector/`): Evaluates direct parameter collisions (same cell and parameter) and indirect spatial collisions (adjacent cells sharing RF interference boundaries).
  - `DecisionLadder` (`arbiter/engine/ladder.py`): Implements the four-step referee ladder:
    1. **APPROVE**: Fast-path approval if no conflict and positive predicted utility.
    2. **MODIFY**: Bisection optimization search (max 6 evaluations) scaling proposal values to feasible headroom.
    3. **DELAY**: Holding deferred actions (max 3 ticks) for transient conditions (e.g., active handovers in-flight).
    4. **REJECT**: Evicting unsafe proposals breaching network safety floors or oscillating in ping-pong loops.
  - `make_default_twin` (`arbiter/engine/default_twin.py`, Contract C1): Factory providing seamless access to the real `DigitalTwinEvaluator` or scripted analytical test twin.

---

### 2.3 Branch 3: Conformal Digital Twin, Actuator & Storage
- **Owner**: Aaditya V
- **Primary Modules**: `arbiter/twin/**`, `arbiter/actuator/**`, `arbiter/storage/**`
- **Core Responsibilities**:
  - `DigitalTwinEvaluator` (`arbiter/twin/evaluator.py`): Online digital replica evaluating candidate actions against live network snapshots in $\sim 4\text{ ms}$.
  - `ConformalPredictor` (`arbiter/twin/conformal.py`): Applies split-conformal prediction theory to quantify predictive uncertainty. Calculates non-conformity residuals $|y_t - \hat{y}_t|$ over a rolling window of 200 samples to provide valid finite-sample prediction intervals ($\pm 0.30\text{ dB}$) with 90% empirical coverage.
  - `ActuatorEnforcer` (`arbiter/actuator/enforcer.py`): Dispatches approved commands to network targets while maintaining safety floor invariants.
  - `SQLiteCommitLogger` (`arbiter/storage/commit_log.py`): Append-only transactional ledger operating under SQLite WAL mode (`PRAGMA journal_mode=WAL;`), guaranteeing crash resilience, high write concurrency (1,320 writes/s), and deterministic replay.
  - `LKGStore` & `RollbackManager` (`arbiter/storage/lkg_store.py`, `arbiter/actuator/rollback.py`): Maintains Last-Known-Good state snapshots; triggers atomic rollback if post-actuation telemetry violates SLA thresholds.

---

### 2.4 Branch 4: Telemetry Aggregator, REST/WebSocket API & Dashboards
- **Owner**: G S Venkat Sai
- **Primary Modules**: `arbiter/telemetry/**`, `arbiter/api/**`, `arbiter/dashboard/**`
- **Core Responsibilities**:
  - `FastAPI Server` (`arbiter/api/server.py`): Hosts REST endpoints and high-cadence WebSocket broadcast channels (`/api/v1/ws/live`, `/ws/replay`).
  - Native Frame Ingestion (`arbiter/telemetry/frame.py`): Serializes typed `FrameMessage` payloads carrying cells, users, proposals, decisions, conflicts, plans, and twin uncertainty.
  - 3D RAN Digital Twin Monitor (`arbiter/dashboard/3d/`): Interactive WebGL visualizer built with Three.js r128 rendering towers, active subscribers, coverage heatmaps, conflict arcs, and conformal uncertainty shells in real-time.
  - 2D Operations Console (`arbiter/dashboard/`): 100vh dense Network Operations Center dashboard tracking live KPIs, decision history, and counterfactual deltas.
  - Marketing & Documentation Site (`arbiter/dashboard/site/`): Multi-page static portal providing problem context, benchmark results, and architecture explainers.
