# Branch 2 integration guide (Arbitration & Decision Engine)

Everything Branch 2 needs from another branch enters through three seams:
`arbiter/engine/contracts.py`, the `TwinClient` protocol, and
`QueueOrchestrator.on_tick`. This file records the exact swaps, the assumed
shapes, and the open questions so the integrations are mechanical.

Owner: Branch 2 (SW-31). Folders owned: `arbiter/queue/`, `arbiter/detector/`,
`arbiter/engine/` (including `arbiter/engine/mocks/`), `tests/branch2/`.

---

## 1. TODO(INTEGRATION) sites

`grep -rn "TODO(INTEGRATION)" arbiter/queue arbiter/detector arbiter/engine` returns
exactly one site:

| Location | Marker | What to do |
| --- | --- | --- |
| `arbiter/engine/contracts.py:64` | `# TODO(INTEGRATION): drop these once arbiter.shared.models publishes the Branch 2 shapes.` | Delete the local contract block (`ActionType`, `ConflictType`, `ReasonCode`, `UtilityWeights`, `CommandProposal`, `ArbitrationDecision`) once `arbiter/shared/models.py` carries the Branch 2 shapes. Adoption is automatic: `_adopt_shared_contracts()` rebinds the module globals when `_is_spec_compatible()` passes. |

There are no other `TODO(INTEGRATION)` markers. Two seams deliberately need no
marker because they are constructor-injected rather than stubbed:

* `arbiter/queue/orchestrator.py` — `QueueOrchestrator(twin=...)`; the default is
  `MockDigitalTwinClient`.
* `arbiter/detector/classifier.py` — `hop_radius=2` (SRS Q17).

`SHARED_CONTRACTS_AVAILABLE` is currently `False`: adoption is all-or-nothing and
`arbiter/shared/models.py` does not publish the Branch 2 `CommandProposal` shape
(see §4). Branch 2 therefore runs on its own spec-identical definitions, which is
why every module imports contracts from `arbiter/engine/contracts.py` only.

---

## 2. Swapping `MockDigitalTwinClient` for Branch 3's real client

Branch 2 codes against this protocol (`arbiter/engine/mocks/mock_twin_client.py`):

```python
@runtime_checkable
class TwinClient(Protocol):
    def evaluate(
        self,
        candidate_deltas: list[dict[str, Any]],
        timeout_budget_ms: float = 15.0,
    ) -> dict[str, Any]: ...
```

### Candidate payload Branch 2 sends

One dict per applied change (`DecisionLadderEngine` /`build_candidate_delta`):

```json
{
  "command_id": "cmd-001",
  "cell_id": "B",
  "parameter": "OFFLOAD_RATIO",
  "from_value": 0.0,
  "to_value": 0.1528
}
```

`parameter` is upper-cased and stripped. An empty list means "do nothing" — that
is the shared baseline prediction.

### Response Branch 2 requires

```json
{
  "predicted_kpis": {
    "coverage_ratio": 0.94,
    "normalized_energy": 0.45,
    "normalized_interference": 0.305,
    "drop_rate_ratio": 0.0135
  },
  "safety_floors_satisfied": true,
  "confidence_margin": 0.0
}
```

Optional keys Branch 2 reads when present:

| Key | Effect |
| --- | --- |
| `computation_time_ms` | `> TWIN_TIMEOUT_MS` (100 ms) → no approval, `REJECT/TWIN_TIMEOUT`. |
| `confidence_margin` | Widens the uncertainty band (`|dU| < margin` blocks approval). |
| `limiting_factor: "active_ho"` | MODIFY reason becomes `ACTIVE_HO` instead of `HEADROOM`. |
| `active_handover_users: <number>` | `> 0` behaves exactly like `limiting_factor: "active_ho"`. |
| `evaluation_id` | Ignored (kept by mocks for traceability). |

Unknown keys are ignored. Missing KPI keys are treated pessimistically by
`UtilityCalculator` (the term is scored as its worst value), so a shape mismatch
degrades the decision rather than crashing.

### If `POST /api/v1/twin/evaluate` differs

Write an adapter that satisfies `TwinClient`; nothing else in Branch 2 changes.
Only this file then knows the HTTP contract:

```python
# arbiter/engine/adapters/twin_http.py  (new file, Branch 2-owned folder)
from typing import Any
import httpx


class TwinHttpClient:
    """Adapter from the Branch 2 TwinClient protocol to /api/v1/twin/evaluate."""

    def __init__(self, base_url: str, timeout_s: float = 0.2) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout_s)

    def evaluate(
        self, candidate_deltas: list[dict[str, Any]], timeout_budget_ms: float = 15.0
    ) -> dict[str, Any]:
        body = {
            "candidate_deltas": candidate_deltas,
            "timeout_ms": timeout_budget_ms,
        }
        payload = self._client.post(
            f"{self._base_url}/api/v1/twin/evaluate", json=body
        ).json()
        kpis = payload.get("predicted_kpis") or payload["kpis"]
        return {
            "predicted_kpis": kpis,
            "safety_floors_satisfied": payload.get("safety_floors_satisfied"),
            "confidence_margin": payload.get("confidence_margin", 0.0),
            "computation_time_ms": payload.get("computation_time_ms"),
            "limiting_factor": payload.get("limiting_factor"),
            "active_handover_users": payload.get("active_handover_users"),
        }
```

Wiring is a constructor argument, in Dev 4's route or in a factory:

```python
from arbiter.queue import QueueOrchestrator

orchestrator = QueueOrchestrator(twin=TwinHttpClient("http://twin:8000"))
# tests keep using the mock:
# queue_orchestrator = QueueOrchestrator(twin=MockDigitalTwinClient())
```

Rules if the real client's names or payloads differ:

1. Map method names in the adapter (`predict`, `simulate`, `post_evaluate`, … →
   `evaluate`). Branch 2 only ever calls `evaluate(candidate_deltas)`.
2. Map KPI names in the adapter to `coverage_ratio`, `normalized_energy`,
   `normalized_interference`, `drop_rate_ratio`. Those four keys are load-bearing
   (`UtilityCalculator` and `SafetyFloorEnforcer`).
3. Map candidate field names both ways. If Branch 3 wants `cell`/`param`/`value`
   instead of `cell_id`/`parameter`/`to_value`, translate in the adapter and
   leave `build_candidate_delta` untouched.
4. Never let the client swallow a timeout: raise `TimeoutError` (or return
   `computation_time_ms` above the budget). The ladder converts that into
   `REJECT/TWIN_TIMEOUT` and keeps the last known good state.
5. If Branch 3's floors are stricter or use different units, put the conversion
   in `SafetyFloorEnforcer`/`UtilityCalculator`, not in the ladder.

---

## 3. What Dev 4's API route must call

One orchestrator instance per process (it owns the queue, the ping-pong window
and the latency buffer). Per tick:

```python
from arbiter.engine.contracts import CommandProposal, UtilityWeights
from arbiter.queue import QueueOrchestrator

orchestrator = QueueOrchestrator(
    twin=TwinHttpClient("http://twin:8000"),
    adjacency=cell_adjacency,          # {"A": ["B"], ...} or a networkx.Graph
    weights=UtilityWeights(),          # or UtilityWeights.from_preset("night_energy_saver")
)

# 1) Intake (one call per proposal; emergency ones bypass the queue).
accepted: bool = orchestrator.submit(
    CommandProposal(
        command_id="cmd-001",
        controller="ctrl-energy",
        target_cell="B",
        parameter="SLEEP_STATE",
        requested_value=1.0,
        current_value=0.0,
        is_emergency=False,
        is_binary=True,
        created_tick=42,
    )
)

# 2) One arbitration tick.
result = orchestrator.on_tick(current_tick=42, timestamp_epoch_ms=1_700_000_000_000)
# {
#   "batch_tick": 42,
#   "decisions_count": 3,
#   "decisions": [ArbitrationDecision, ...],
# }
```

`on_tick` performs, in order: `queue.flush_batch(tick)` → detector
`build(batch, adjacency)` → `GraphPartitioner.components(graph)` →
parallel `DecisionLadderEngine.evaluate(...)` per component → `queue.hold(...)`
for every DELAY → `engine.record_handover(...)` for every APPROVE/MODIFY.

Contract details the route must honour:

* The ladder never raises: twin faults become `REJECT/TWIN_TIMEOUT`. An empty
  queue returns `decisions_count == 0` without calling the detector or the twin.
* Decisions are returned as the actual `ArbitrationDecision` models (not
  serialised); serialise with `d.model_dump(mode="json")` for the HTTP response.
* `orchestrator.latency_p95_ms()` exposes per-decision latency (NFR-1 < 100 ms);
  `latency_samples()` and `reset_latency()` support the dashboard.
* Emergency proposals produce a decision only in the bypass-lane audit:
  `orchestrator.queue.emergency_lane.audit_count` /
  `peek_audit()`; they never appear in `on_tick()["decisions"]`.
* `current_tick` must advance monotonically per tick; `queue.flush_batch`
  releases parked commands whose due tick has arrived.

---

## 4. Model fields assumed in `contracts.py` vs `arbiter/shared/models.py`

`arbiter/shared/models.py` has the same `ActionType`, `ConflictType` and
`ReasonCode` members, and `ArbitrationDecision` is field-for-field identical.
`CommandProposal` is not: it uses different names and omits six fields Branch 2
needs, plus there is no `UtilityWeights`. That single mismatch is why
`SHARED_CONTRACTS_AVAILABLE` is `False`.

| Branch 2 (`arbiter/engine/contracts.py`) | `arbiter/shared/models.py` | Status |
| --- | --- | --- |
| `command_id: str` | `command_id: str` | match |
| `controller: str` | `controller_id: str` | renamed |
| — | `controller_type: ControllerType` | **missing in Branch 2** (see below) |
| `target_cell: str` | `target_cell_id: str` | renamed |
| `parameter: str` | `parameter: ParameterType` | str vs enum |
| `requested_value: float` | `requested_value: float` | match |
| `current_value: float` | — | **missing in shared** |
| `is_emergency: bool` | `emergency_flag: bool` | renamed |
| `is_binary: bool` | — | **missing in shared** |
| `created_tick: int` | — (has `timestamp_epoch_ms`) | **missing in shared** |
| `hold_count: int` | `hold_count: int` | match |
| `handover_from: str \| None` | — | **missing in shared** |
| `handover_to: str \| None` | — | **missing in shared** |
| `must_decide: bool` | — | **missing in shared** |
| `UtilityWeights(w1..w4, from_preset)` | — | **missing in shared** |
| `ArbitrationDecision` (10 fields) | `ArbitrationDecision` (10 fields) | match |

Notes:

* `controller_type` is not needed by the ladder: controller class is implied by
  the parameter/behaviour rules in `arbiter/detector/classifier.py`. If shared
  models become authoritative, either add `controller_type` to Branch 2 or derive
  it at ingestion in Dev 4's route.
* `parameter` is compared case-insensitively after `.strip().upper()`, so a
  `ParameterType` enum (`str`-valued) is accepted by the classifier/ladder
  without changes — but it would have to keep the same spellings
  (`TX_POWER_DBM`, `TILT_DEG`, `SLEEP_STATE`, `OFFLOAD_RATIO`).
* `created_tick` is required for the starvation guard and batch ordering; the
  shared model's `timestamp_epoch_ms` is not a tick index. A route that only has
  the timestamp must derive the tick (`timestamp // TICK_MS`).

### Making adoption flip

Add to `arbiter/shared/models.py` (owner of that file, not Branch 2):
`current_value`, `is_binary`, `created_tick`, `handover_from`, `handover_to`,
`must_decide`; rename `controller_id`→`controller`, `target_cell_id`→`target_cell`
(or add aliases), `emergency_flag`→`is_emergency`; and add `UtilityWeights` with
`w1..w4` and a `from_preset` classmethod. `_is_spec_compatible()` then passes,
`SHARED_CONTRACTS_AVAILABLE` becomes `True`, and the local block plus its
`TODO(INTEGRATION)` can be deleted — no other file changes.

---

## 5. Open assumptions

1. **Hold semantics.** A DELAY decision reports `hold_count = previous + 1`; the
   orchestrator then calls `ProposalQueue.hold(command, tick)`, which re-latches
   the same counter (so the decision and the queue agree), parks the command for
   `tick + 1`, and sets `must_decide` once the counter reaches
   `MAX_HOLD_TICKS = 3`. After three holds the command must be APPROVEd or
   REJECTed on the next tick — the ladder refuses a fourth DELAY and the queue
   refuses to park it again (`is_holdable` returns `False` for exhausted ids and
   for already parked ids). `queue.check_invariants()` enforces this and the 500
   batch fuzz asserts it. Emergency commands are never holdable.
2. **Baseline evaluation accounting.** The "do nothing" baseline costs one of the
   six twin evaluations of the component and is shared by every command in it;
   the joint APPROVE probe costs the second. A single-command component therefore
   searches with at most four evaluations, and a two-command component with two
   each (documented in `ladder.py`; `BASELINE_EVALUATIONS = 1` in `bisection.py`
   records the cost). If Branch 3 exposes a cached baseline, the ladder can skip
   that probe in `_evaluate` and gain one evaluation per component.
3. **Hop radius.** `ConflictClassifier` defaults to `hop_radius = 2` (SRS Q17),
   breadth-first over the adjacency map, so an INDIRECT clash exists for cells
   1–2 hops apart. Branch 2 needs a symmetric adjacency map of direct neighbours
   (or a `networkx.Graph`); with an empty map every command becomes its own
   component and no conflict is ever detected. If the real topology has cells
   further apart, raise the radius in the constructor rather than in the ladder.
4. **Weights** default to `(0.3, 0.2, 0.2, 0.3)` (coverage, energy, interference,
   drop) and are per-orchestrator, not per-command. Presets:
   `default`, `night_energy_saver`, `coverage_first`.
5. **Priority order inside a component.** Commands are ranked by standalone
   `dU` only when the remaining budget can pay for one probe per command and four
   evaluations per search; otherwise the documented fallback is non-binary first,
   then `(created_tick, command_id)`. With the default budget of six the fallback
   always applies, so ranking is dormant in production configuration.
6. **Uncertainty margin.** The ladder uses
   `max(UncertaintyGuard.default_margin, twin confidence_margin)`; the guard
   defaults to `0.0`, so today only a margin reported by the twin can block an
   approval. A non-zero default is a one-argument change on `DecisionLadderEngine`.
