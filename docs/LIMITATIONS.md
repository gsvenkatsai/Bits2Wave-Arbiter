# Project ARBITER: Limitations and Sim-to-Real Considerations

Honesty is a core design principle of Project ARBITER. This document transparently outlines the known architectural boundaries, algorithmic limits, and simulation-to-reality gaps in the current prototype.

---

## 1. Algorithmic and Computational Limits

### 1.1 Pairwise Conflict Detection Complexity ($O(P^2)$)
- **Current Behavior**: The conflict detector (`ConflictDetector`) builds a pairwise conflict graph across all incoming command proposals within each coordination window.
- **Complexity**: For $P$ concurrent proposals, detection requires $\frac{P(P - 1)}{2} = O(P^2)$ comparison evaluations.
- **Measured Latency**:
  - $P = 10$ proposals: $5.38\text{ ms}$
  - $P = 50$ proposals: $44.1\text{ ms}$
  - $P = 100$ proposals: $153.64\text{ ms}$
- **Boundary**: To respect the O-RAN Near-RT RIC control loop deadline of $\le 100\text{ ms}$, the single-threaded detector is bounded at approximately **75 concurrent proposals per window**.
- **Roadmap Mitigation**: Implementing geographical or topological cell clustering (e.g. spatial cell-adjacency KD-trees or disjoint graph partitioning) will decouple non-interfering cell clusters into parallel sub-graphs.

### 1.2 Digital Twin Surrogate Fidelity
- **Model**: The internal `DigitalTwinEvaluator` uses an analytical radio frequency surrogate derived from empirical COST-231 path loss and Shannon capacity approximations.
- **Gap**: It evaluates forward network state in $\approx 4\text{ ms}$, but does not simulate physical-layer (PHY) waveform processing, cyclic prefix OFDM symbols, or hybrid automatic repeat request (HARQ) retransmissions.
- **Mitigation via Conformal Prediction**: To account for surrogate error, ARBITER integrates split conformal prediction ($\alpha = 0.10$, providing $90\%$ nominal confidence). When surrogate uncertainty expands beyond safety tolerances, proposals are delayed rather than unsafely executed.

---

## 2. Radio Network and Simulation Modeling

### 2.1 Propagation and Channel Assumptions
- **Path Loss**: Calculated using the standard COST-231 Hata urban model ($2.1\text{ GHz}$ carrier frequency, base station height $30\text{ m}$, user height $1.5\text{ m}$).
- **Shadowing**: Log-normal shadow fading with zero-mean Gaussian distribution ($\sigma = 6.0\text{ dB}$) and exponential spatial auto-correlation.
- **Multipath Fading**: Fast fading (Rayleigh / Rician small-scale multipath) is not explicitly modeled on a millisecond basis; instead, it is abstracted into statistical SINR distribution variance.
- **Topology**: The standard simulation operates on a 5-cell heterogeneous cluster with wrap-around interference calculation, representative of dense urban micro-macro deployment.

### 2.2 User Traffic and Mobility
- **Traffic Model**: User traffic follows synthetic diurnal demand patterns (e.g., residential dip at 2:00 AM, commercial peak at 2:00 PM) driven by Poisson session arrivals.
- **Mobility**: Synthetic random walk and road-segment waypoint mobility. Dynamic handover hysteresis ($3\text{ dB}$ margin, time-to-trigger $160\text{ ms}$) is simulated at the RRC layer.

---

## 3. O-RAN Architecture and Interface Alignment

### 3.1 Transport Protocol Serialization
- **Current Prototype**: Serves structured JSON over standard HTTP REST (`FastAPI`) and WebSockets (`ws://`).
- **Standard O-RAN Gap**: Production Near-RT RIC xApps communicate over the E2 interface via ASN.1 PER-encoded messages over SCTP, using E2 Service Models such as E2SM-RC (RAN Control) and E2SM-KPM (KPI Monitoring).
- **Feasibility Path**: ARBITER's internal data models (`CommandProposal`, `ArbitrationDecision`, `CellState`) maintain a 1:1 schema alignment with E2SM-RC RAN Parameter IDs, allowing straightforward serialization into ASN.1 structures via an O-RAN E2 Termination (E2T) proxy.

### 3.2 Near-RT vs. Non-RT RIC Deployment
- **Latency Budget**: ARBITER's mean decision latency ($4.17\text{ ms}$) easily fits within the Near-RT RIC window ($10\text{ ms} - 1000\text{ ms}$).
- **Deployment Location**: In a production carrier environment, ARBITER's coordination ladder can be packaged as a Near-RT RIC platform component or an arbitration xApp, while the multi-objective Pareto sweeper and policy tuning run as Non-RT RIC rApps across the A1 interface.

---

## 4. Security, Identity, and Access Control

### 4.1 Prototype Authentication Status
- **Authentication**: REST and WebSocket endpoints in this prototype repository are unauthenticated to enable immediate local evaluation and automated testing.
- **Production Requirement**: A carrier-grade deployment must enforce mutual TLS (mTLS) with X.509 certificate validation, OAuth2 bearer tokens for REST clients, and role-based access control (RBAC) separating controller proposal submission rights from administrator policy overrides.

### 4.2 SQLite Persistence Concurrency
- **Database**: SQLite WAL (Write-Ahead Logging) mode is used for embedded telemetry recording and flight recorder auditing.
- **Throughput**: Measured at $1,320\text{ writes/s}$ on standard SSD storage.
- **Scaling Limit**: For network-wide deployments exceeding 50,000 cells, the audit sink should be routed to a distributed time-series database (such as TimescaleDB or Apache Cassandra) via Kafka / gRPC event streaming.

---

## 5. Environment and Testing Coverage

- **Primary OS**: Verified on Windows 11 (PowerShell 7).
- **Cross-Platform Compatibility**: Codebase uses Python `os.path` and standard POSIX paths. Linux (Ubuntu 22.04 LTS) and macOS testing is supported by standard Python and Node.js runtimes, but bare-metal carrier Linux hardware testing was not performed during this hackathon sprint.
- **Hardware Acceleration**: The prototype does not require GPU acceleration for decision logic; all conformal calibration and bisection ladder routines run on standard x86-64 / ARM64 CPUs.
