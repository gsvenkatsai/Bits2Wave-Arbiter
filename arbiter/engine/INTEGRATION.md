# Branch 2 integration guide (Arbitration & Decision Engine)

Branch 2 now runs on the real shared models and has an adapter for the real
digital twin. Three seams remain relevant to other branches:

1. `arbiter/engine/contracts.py` — re-exports `arbiter.shared.models` and extends
   it with the arbitration-only fields.
2. `arbiter/engine/twin_adapter.py` — `TwinClientAdapter`, the `TwinClient`
   implementation over Branch 3's `DigitalTwinEvaluator` (the mock is kept for
   tests only).
3. `arbiter/queue/orchestrator.py` — `QueueOrchestrator.on_tick(...)`, the single
   call Dev 4's API route makes per tick.

Owner: Branch 2 (SW-31). Folders owned: `arbiter/queue/`, `arbiter/detector/`,
`arbiter/engine/` (including `arbiter/engine/mocks/`), `tests/branch2/`.

---

## 1. Integration markers

Marker audit: `grep -rn "TODO(" arbiter/queue arbiter/detector arbiter/engine`
returns no matches in code (the only hits are this guide). The single historic
integration marker in `arbiter/engine/contracts.py` ("drop these local contracts
once shared publishes them") is resolved:

* `ActionType`, `ConflictType`, `ReasonCode` and `ArbitrationDecision` are
  imported verbatim from `arbiter.shared.models`;
* `CommandProposal` subclasses the shared model (superset, see §4);
* `SHARED_CONTRACTS_AVAILABLE` is `True` and `CONTRACTS_SOURCE` is
  `"arbiter.shared.models"`.

Two seams are constructor-injected and need no marker:

* `arbiter/queue/orchestrator.py` — `QueueOrchestrator(twin=...)`; the default
  is still `MockDigitalTwinClient`, production passes `TwinClientAdapter`.
* `arbiter/detector/classifier.py` — `hop_radius=2` (SRS Q17).

The only deliberately local contract left is `UtilityWeights` (no shared
counterpart; see §4).

---

## 2. The real twin and its adapter

Branch 3 (`arbiter/twin/`, branch `feat/digital-twin-actuator`) exposes:

```python
DigitalTwinEvaluator(state_provider, calibrator=None, fail_safe_manager=None,
                     propagation_chain=None)
    .predict(candidate_deltas: List[CandidateDelta],
             timeout_budget_ms: Optional[float] = None,
             evaluation_id: Optional[str] = None) -> TwinEvaluationResponse

handle_twin_evaluate(payload: dict) -> dict      # POST /api/v1/twin/evaluate
```

Branch 3 schemas:

```python
CandidateDelta(cell_id: str, parameter: str, proposed_value: float)

PredictedKPIs(coverage_ratio, normalized_energy, normalized_interference,
              drop_rate_ratio, predicted_sinr_avg_db, affected_cell_ids)

TwinEvaluationResponse(evaluation_id, computation_time_ms,
                       predicted_kpis, safety_floors_satisfied,
                       confidence_margin)

TwinEvaluationRequest(evaluation_id, candidate_deltas, timeout_budget_ms=None)
```

`TwinClientAdapter` (`arbiter/engine/twin_adapter.py`) converts in both
directions. Wiring:

```python
from arbiter.engine.twin_adapter import TwinClientAdapter
from arbiter.queue import QueueOrchestrator
from arbiter.twin.evaluator import DigitalTwinEvaluator

evaluator = DigitalTwinEvaluator(state_provider=telemetry_snapshot_provider)
orchestrator = QueueOrchestrator(
    twin=TwinClientAdapter(evaluator),      # or TwinClientAdapter.from_handler(handle_twin_evaluate)
    adjacency=cell_adjacency,
)
```

What the adapter does:

| Branch 2 sends | Branch 3 receives |
| --- | --- |
| `{"command_id", "cell_id", "parameter", "from_value", "to_value"}` | `TwinDelta(cell_id, parameter.upper(), proposed_value=to_value)` — `command_id` and `from_value` are dropped |
| `evaluate(deltas)` | `predict(deltas, timeout_budget_ms=100.0)` (the default budget; pass a different one to `TwinClientAdapter`) |

| Branch 3 returns | Branch 2 receives |
| --- | --- |
| `TwinEvaluationResponse` (pydantic) or the handler's dict | plain dict with `evaluation_id`, `predicted_kpis` (only the four contract KPIs), `safety_floors_satisfied`, `confidence_margin`, `computation_time_ms` |
| `predicted_sinr_avg_db`, `affected_cell_ids` | dropped (not part of the utility function) |
| missing `computation_time_ms` | adapter's own measured wall time |

Behaviour that matters:

* `TwinDelta` is duck-type compatible with `CandidateDelta` (Branch 3 only reads
  `.cell_id`, `.parameter`, `.proposed_value`), so no import of `arbiter.twin`
  happens at module import time — Branch 2 keeps working before Branch 3 merges.
  If Branch 3 ever validates strictly, pass
  `TwinClientAdapter(evaluator, delta_factory=CandidateDelta)`.
* The default 100 ms budget keeps Branch 3 on its fast path (`predict` takes the
  analytical fast path when the budget is `None` or `>= 50`). The ladder still
  enforces its own 100 ms wall-clock guard and treats `computation_time_ms >
  100` as a timeout.
* Branch 3's fail-safe returns a last-known-good response instead of raising; the
  ladder treats that as a normal prediction (it cannot tell it apart). If Branch 2
  must distinguish it, Branch 3 has to flag the response and the adapter must
  forward the flag.
* Branch 3 reports no `limiting_factor` and no `active_handover_users`, so with
  the real twin every MODIFY is labelled `HEADROOM`. If those keys are added,
  forward them from `_normalise` to get `ACTIVE_HO` again (one line).
* A raising `predict` (e.g. `TimeoutError`) propagates out of
  `TwinClientAdapter.evaluate` and the ladder converts it to
  `REJECT/TWIN_TIMEOUT`.

If `POST /api/v1/twin/evaluate` changes shape, only `TwinClientAdapter` changes:

1. Method name (`predict`/`simulate`/…): adapt in the adapter; Branch 2 only
   calls `evaluate(candidate_deltas, timeout_budget_ms=None)`.
2. KPI names: map them onto `coverage_ratio`, `normalized_energy`,
   `normalized_interference`, `drop_rate_ratio` (`KPI_KEYS`). All four are
   load-bearing for `UtilityCalculator` and `SafetyFloorEnforcer`.
3. Candidate field names: translate in `TwinClientAdapter._to_delta`; leave
   `build_candidate_delta` untouched.
4. Never swallow a timeout: raise `TimeoutError` or report
   `computation_time_ms` above the budget.
5. Different floor units/thresholds: change `SafetyFloorEnforcer` /
   `UtilityCalculator`, never the ladder.

---

## 3. What Dev 4's API route must call

One orchestrator instance per process (it owns the queue, the ping-pong window
and the latency buffer).

```python
from arbiter.engine.contracts import CommandProposal, UtilityWeights
from arbiter.queue import QueueOrchestrator

orchestrator = QueueOrchestrator(
    twin=TwinClientAdapter(DigitalTwinEvaluator(state_provider=provider)),
    adjacency=cell_adjacency,          # {"A": ["B"], ...} or a networkx.Graph
    weights=UtilityWeights(),          # or UtilityWeights.from_preset("night_energy_saver")
)

# 1) Intake (one call per proposal; emergency ones bypass the queue).
accepted: bool = orchestrator.submit(
    CommandProposal(
        command_id="cmd-001",
        controller_id="ctrl-energy",        # or controller="ctrl-energy"
        controller_type="ENERGY_SAVING",
        target_cell_id="B",                 # or target_cell="B"
        parameter="SLEEP_STATE",
        requested_value=1.0,
        current_value=0.0,
        timestamp_epoch_ms=1_700_000_000_000,
        created_tick=42,                    # optional; derived from the timestamp if 0
        is_binary=True,
        emergency_flag=False,               # or is_emergency=False
    )
)

# 2) One arbitration tick.
result = orchestrator.on_tick(current_tick=42, timestamp_epoch_ms=1_700_000_000_000)
# {"batch_tick": 42, "decisions_count": 3, "decisions": [ArbitrationDecision, ...]}
```

`on_tick` performs: `queue.flush_batch(tick)` → detector
`build(batch, adjacency)` → `GraphPartitioner.components(graph)` → parallel
`DecisionLadderEngine.evaluate(...)` per component → `queue.hold(...)` for every
DELAY → `engine.record_handover(...)` for every APPROVE/MODIFY.

Contract details the route must honour:

* The ladder never raises: twin faults become `REJECT/TWIN_TIMEOUT`. An empty
  queue returns `decisions_count == 0` without calling the detector or the twin.
* Decisions are `ArbitrationDecision` models (the shared ones); serialise with
  `d.model_dump(mode="json")`.
* `orchestrator.latency_p95_ms()` exposes per-decision latency (NFR-1 < 100 ms);
  `latency_samples()` / `reset_latency()` support the dashboard.
* Emergency proposals produce only an audit record:
  `orchestrator.queue.emergency_lane.audit_count` / `peek_audit()`; they never
  appear in `on_tick()["decisions"]`.
* `current_tick` must advance monotonically; `flush_batch` releases parked
  commands when their due tick arrives.

---

## 4. Models: `contracts.py` on top of `arbiter/shared/models.py`

| Branch 2 (`arbiter/engine/contracts.py`) | `arbiter/shared/models.py` | Status |
| --- | --- | --- |
| `ActionType`, `ConflictType`, `ReasonCode` | same members | **imported verbatim** |
| `ArbitrationDecision` (10 fields) | identical (10 fields) | **imported verbatim** |
| `command_id` | `command_id` | inherited |
| `controller` (property) / `controller_id` (field) | `controller_id` | inherited field, both spellings accepted |
| `controller_type: ControllerType` | `controller_type: ControllerType` | inherited, required, unused by Branch 2 |
| `target_cell` (property) / `target_cell_id` (field) | `target_cell_id` | inherited field, both spellings accepted |
| `parameter: ParameterType` | `parameter: ParameterType` | inherited (closed enum, see below) |
| `requested_value: float` | `requested_value: float` | inherited |
| `timestamp_epoch_ms: int` | `timestamp_epoch_ms: int` | inherited, required |
| `hold_count: int` | `hold_count: int` | inherited |
| `is_emergency` (property) / `emergency_flag` (field) | `emergency_flag` | inherited field, both spellings accepted |
| `current_value: float` | — | **Branch 2 extra** (needed by `direction()`) |
| `is_binary: bool` | — | **Branch 2 extra** (skips MODIFY) |
| `created_tick: int` | — | **Branch 2 extra** (`timestamp_epoch_ms // TICK_MS` when 0) |
| `handover_from`, `handover_to` | — | **Branch 2 extra** (ping-pong detection) |
| `must_decide: bool` | — | **Branch 2 extra** (starvation latch) |
| `UtilityWeights(w1..w4, from_preset)` | — | **local to Branch 2** |

Notes:

* `CommandProposal` is a superset: `model_dump()` produces a dict that validates
  as `arbiter.shared.models.CommandProposal`, so a Branch 2 proposal can cross
  the shared boundary unchanged.
* `created_tick` is the arbitration clock. Instantiate with `created_tick` or let
  the model derive it from `timestamp_epoch_ms // TICK_MS` (100 ms tick).
* `parameter` is the shared `ParameterType` enum — a **closed set** of
  `TX_POWER_DBM`, `TILT_DEG`, `SLEEP_STATE`, `OFFLOAD_RATIO`. The detector's rule
  table also knows aliases (`TX_POWER`, `CELL_STATE`, `HANDOVER_SHARE`,
  `LOAD_SHARE`, …), but they cannot be constructed through the shared enum. An
  ingestion route that receives an alias must map it to the enum (or the shared
  owner must widen `ParameterType`).
* `controller_type` is required by the shared model and unused by the ladder: the
  controller class is implied by the parameter/behaviour rules. Supply it at
  ingestion.
* If shared later adds `current_value`/`is_binary`/`created_tick`/handover
  fields/`must_decide` or a `UtilityWeights`, drop the duplicate declarations
  here — the subclass then only keeps the alias properties.

---

## 5. Open assumptions

1. **Hold semantics.** A DELAY decision reports `hold_count = previous + 1`; the
   orchestrator then calls `ProposalQueue.hold(command, tick)`, which re-latches
   the same counter, parks the command for `tick + 1`, and sets `must_decide` at
   `MAX_HOLD_TICKS = 3`. After three holds the command must be APPROVEd or
   REJECTed on the next tick; the ladder refuses a fourth DELAY and the queue
   refuses to park it again. `queue.check_invariants()` enforces this and the
   500-batch fuzz asserts it. Emergency commands are never holdable.
2. **Baseline evaluation accounting.** The "do nothing" baseline costs one of the
   six twin evaluations of the component and is shared by every command in it;
   the joint APPROVE probe costs the second. A single-command component searches
   with at most four evaluations, a two-command component with two each. If
   Branch 3 exposes a cached baseline, `_evaluate` can skip that probe and gain
   one evaluation per component (`BASELINE_EVALUATIONS = 1` in `bisection.py`
   records the cost).
3. **Hop radius.** `ConflictClassifier` defaults to `hop_radius = 2` (SRS Q17),
   breadth-first over the adjacency map. Branch 2 needs a symmetric adjacency map
   of direct neighbours (or a `networkx.Graph`); with an empty map every command
   becomes its own component and no conflict is ever detected. Branch 3's twin
   uses its own `max_hops=2` neighbourhood, which matches.
4. **Weights** default to `(0.3, 0.2, 0.2, 0.3)` (coverage, energy, interference,
   drop), per orchestrator, not per command. Presets: `default`,
   `night_energy_saver`, `coverage_first`.
5. **Priority order inside a component.** Commands are ranked by standalone `dU`
   only when the remaining budget can pay for one probe per command and four
   evaluations per search; otherwise the documented fallback is non-binary first,
   then `(created_tick, command_id)`. With the default budget of six the fallback
   always applies.
6. **Uncertainty margin.** The ladder uses
   `max(UncertaintyGuard.default_margin, twin confidence_margin)`; the guard
   defaults to `0.0`, so only a margin reported by the twin can block an
   approval. Branch 3 reports its calibrated margin, which therefore takes effect
   automatically through the adapter.
7. **Twin fail-safe is invisible to Branch 2.** `LKGReversionManager` returns a
   last-known-good prediction instead of raising, and the adapter cannot tell it
   apart from a fresh prediction; the ladder's own wall-clock guard still
   applies, but a stale LKG response will be treated as current.
