# ARBITER: Conflict-Resolution Referee for Mobile Network Controllers

ARBITER is a simulation-based, conflict-aware pre-actuation arbitration engine for autonomous cellular networks. It intercepts radio parameter proposals from independent controllers (Energy Saving, Load Balancing, Coverage/Capacity, Public Safety), models clashes on a conflict graph, evaluates joint multi-cell KPI impact in an analytical digital twin, and resolves collisions before touching the network.

---

## 4-Developer Parallel Branch Structure

| Workstream | Developer | Branch Name | Module Responsibility | Specification Document |
| :--- | :--- | :--- | :--- | :--- |
| **Branch 1** | Akshay A (Lead) | `feat/ran-simulator-controllers` | Radio Simulator, Milan Dynamic Traffic & Controllers | [Branch 1 Spec](Branch_1_RAN_Simulator_and_Controllers_Spec.pdf) |
| **Branch 2** | H M Pranav | `feat/arbitration-engine` | 100ms Proposal Queue, Conflict Graph & Decision Ladder | [Branch 2 Spec](Branch_2_Arbitration_and_Decision_Engine_Spec.pdf) |
| **Branch 3** | Aaditya V | `feat/digital-twin-actuator` | Analytical Digital Twin (<10ms), Actuator & SQLite Log | [Branch 3 Spec](Branch_3_Digital_Twin_and_Actuator_Spec.pdf) |
| **Branch 4** | G S Venkat Sai | `feat/telemetry-dashboard-benchmarking` | Telemetry Monitor, Live Dashboard, API & Benchmarks | [Branch 4 Spec](Branch_4_Telemetry_Dashboard_and_Benchmarking_Spec.pdf) |
| **Release** | Team FourSUM | `main` | Production Integration & Release Verification | [Merge Playbook](Merge_and_Integration_Playbook.pdf) |

---

## Quickstart

```bash
# 1. Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Initialize SQLite database
python3 -c "from arbiter.shared.database import init_database; init_database('./arbiter_production.db')"
```

Refer to `Merge_and_Integration_Playbook.pdf` for the exact integration runbook and merge sequence.
