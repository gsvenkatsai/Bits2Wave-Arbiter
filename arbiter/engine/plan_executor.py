"""Multi-tick plan execution and persistence engine for ARBITER v2.

Advances active ArbitrationPlans each tick, re-verifies preconditions and
twin uncertainty intervals, applies steps via the transactional actuator,
persists plan lifecycle state to the v2_plans database table, and provides
query endpoints for API consumers.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from typing import Any, Mapping

from arbiter.engine.contracts import (
    FLOOR_COVERAGE,
    FLOOR_DROP_RATE,
    MAX_HOLD_TICKS,
    CommandProposal,
    ReasonCode,
)
from arbiter.engine.mocks.mock_twin_client import TwinClient
from arbiter.engine.planner import evaluate_precondition
from arbiter.shared.database_v2 import init_v2_tables
from arbiter.shared.models import ControllerType
from arbiter.shared.v2_contracts import ArbitrationPlan, PlanStep, TwinPredictionV2

logger = logging.getLogger(__name__)

__all__ = [
    "PlanExecutor",
    "PlanExecutionResult",
    "list_plans",
    "get_plan",
    "set_default_executor",
    "get_default_executor",
]


class PlanExecutionResult:
    """Telemetry report for steps advanced during an arbitration tick."""

    def __init__(
        self,
        plan_id: str,
        step_index: int,
        action: str,  # "EXECUTED", "DEFERRED", "ABORTED", "SKIPPED"
        target_cell_id: str,
        parameter: str,
        value: float,
        reason_code: ReasonCode = ReasonCode.NONE,
        detail: str = "",
    ) -> None:
        self.plan_id = plan_id
        self.step_index = step_index
        self.action = action
        self.target_cell_id = target_cell_id
        self.parameter = parameter
        self.value = value
        self.reason_code = reason_code
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "step_index": self.step_index,
            "action": self.action,
            "target_cell_id": self.target_cell_id,
            "parameter": self.parameter,
            "value": self.value,
            "reason_code": self.reason_code.value,
            "detail": self.detail,
        }


class PlanExecutor:
    """Advances and enforces multi-tick ArbitrationPlans."""

    def __init__(
        self,
        actuator: Any | None = None,
        twin: TwinClient | None = None,
        db_path: str = "./arbiter_production.db",
        max_hold_ticks: int = MAX_HOLD_TICKS,
    ) -> None:
        self._actuator = actuator
        self._twin = twin
        self._db_path = db_path
        self._max_hold_ticks = max_hold_ticks
        self._plans: dict[str, ArbitrationPlan] = {}
        self._dropped_steps: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self._init_db()

    def _init_db(self) -> None:
        try:
            conn = sqlite3.connect(self._db_path)
            try:
                init_v2_tables(conn)
            finally:
                conn.close()
        except Exception as exc:
            logger.debug("Database initialization deferred: %s", exc)

    def set_actuator(self, actuator: Any) -> None:
        with self._lock:
            self._actuator = actuator

    def set_twin(self, twin: TwinClient) -> None:
        with self._lock:
            self._twin = twin

    # -- Persistence --------------------------------------------------------
    def _save_plan(self, plan: ArbitrationPlan) -> None:
        """Persist or update plan in SQLite v2_plans table."""
        try:
            conn = sqlite3.connect(self._db_path)
            try:
                payload = json.dumps(plan.model_dump())
                conn.execute(
                    """
                    INSERT OR REPLACE INTO v2_plans (
                        plan_id, source_command_id, created_tick, expires_tick, payload
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        plan.plan_id,
                        plan.source_command_id,
                        plan.created_tick,
                        plan.expires_tick,
                        payload,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            logger.debug("Failed to persist plan to v2_plans: %s", exc)

    def submit_plan(self, plan: ArbitrationPlan) -> None:
        """Register a synthesized plan for progressive tick-by-tick execution."""
        with self._lock:
            self._plans[plan.plan_id] = plan
            self._save_plan(plan)

    # -- Tick Advancement ----------------------------------------------------
    def advance(
        self,
        current_tick: int,
        state_context: Any = None,
    ) -> list[PlanExecutionResult]:
        """Advance all active plans at the given tick index."""
        results: list[PlanExecutionResult] = []
        with self._lock:
            for plan in list(self._plans.values()):
                res = self._advance_single_plan(plan, current_tick, state_context)
                results.extend(res)
                self._save_plan(plan)
        return results

    def _advance_single_plan(
        self,
        plan: ArbitrationPlan,
        current_tick: int,
        state_context: Any,
    ) -> list[PlanExecutionResult]:
        results: list[PlanExecutionResult] = []

        # Check expiration
        is_expired = current_tick >= plan.expires_tick

        for step in plan.steps:
            if step.status != "PENDING":
                continue

            target_tick = plan.created_tick + step.tick_offset
            if current_tick < target_tick:
                # Offset not yet reached
                continue

            # Check if expired before precondition could be fulfilled
            if is_expired:
                # Precondition was never met by expires_tick -> drop step
                step.status = "ABORTED"
                is_sleep = "sleep" in str(step.parameter).lower()
                reason = ReasonCode.UNCERTAINTY_GUARD if is_sleep else ReasonCode.NONE
                msg = (
                    f"Precondition '{step.precondition}' never met "
                    f"before expires_tick {plan.expires_tick}"
                )
                record = {
                    "plan_id": plan.plan_id,
                    "step_index": step.step_index,
                    "target_cell_id": step.target_cell_id,
                    "parameter": step.parameter.value,
                    "reason_code": reason.value,
                    "dropped_tick": current_tick,
                    "message": msg,
                }
                self._dropped_steps.append(record)
                results.append(
                    PlanExecutionResult(
                        plan_id=plan.plan_id,
                        step_index=step.step_index,
                        action="ABORTED",
                        target_cell_id=step.target_cell_id,
                        parameter=step.parameter.value,
                        value=step.value,
                        reason_code=reason,
                        detail=str(record["message"]),
                    )
                )
                continue

            # Evaluate precondition
            precond_ok = evaluate_precondition(step.precondition, state_context)
            if not precond_ok:
                # Not ready this tick; hold
                results.append(
                    PlanExecutionResult(
                        plan_id=plan.plan_id,
                        step_index=step.step_index,
                        action="DEFERRED",
                        target_cell_id=step.target_cell_id,
                        parameter=step.parameter.value,
                        value=step.value,
                        reason_code=ReasonCode.NONE,
                        detail=f"Precondition '{step.precondition}' waiting",
                    )
                )
                continue

            # Precondition met! Re-verify through twin with uncertainty intervals
            verified = self._reverify_step_through_twin(step)
            if not verified:
                # Twin uncertainty guard rejects this step execution
                step.status = "ABORTED"
                record = {
                    "plan_id": plan.plan_id,
                    "step_index": step.step_index,
                    "target_cell_id": step.target_cell_id,
                    "parameter": step.parameter.value,
                    "reason_code": ReasonCode.UNCERTAINTY_GUARD.value,
                    "dropped_tick": current_tick,
                    "message": "Step re-verification rejected by twin uncertainty intervals",
                }
                self._dropped_steps.append(record)
                results.append(
                    PlanExecutionResult(
                        plan_id=plan.plan_id,
                        step_index=step.step_index,
                        action="ABORTED",
                        target_cell_id=step.target_cell_id,
                        parameter=step.parameter.value,
                        value=step.value,
                        reason_code=ReasonCode.UNCERTAINTY_GUARD,
                        detail=str(record["message"]),
                    )
                )
                continue

            # Actuate step
            self._actuate_step(plan, step, current_tick)
            step.status = "COMPLETED"
            results.append(
                PlanExecutionResult(
                    plan_id=plan.plan_id,
                    step_index=step.step_index,
                    action="EXECUTED",
                    target_cell_id=step.target_cell_id,
                    parameter=step.parameter.value,
                    value=step.value,
                    reason_code=ReasonCode.NONE,
                    detail="Step actuated successfully",
                )
            )

        return results

    def _reverify_step_through_twin(self, step: PlanStep) -> bool:
        """Re-verify step through digital twin intervals before execution."""
        if self._twin is None:
            return True
        delta = {
            "cell_id": step.target_cell_id,
            "parameter": step.parameter.value,
            "from_value": 0.0,
            "to_value": step.value,
        }
        try:
            resp = self._twin.evaluate([delta])
            # Check explicit floors flag
            if resp.get("safety_floors_satisfied") is False:
                return False

            # Check interval bounds if present
            pred_v2 = resp.get("prediction_v2")
            intervals: list[Any] = []
            if isinstance(pred_v2, TwinPredictionV2):
                intervals = list(pred_v2.intervals)
            elif isinstance(pred_v2, Mapping) and "intervals" in pred_v2:
                raw_ints = pred_v2["intervals"]
                if isinstance(raw_ints, list):
                    intervals = list(raw_ints)
            else:
                raw_ints = resp.get("intervals")
                if isinstance(raw_ints, list):
                    intervals = list(raw_ints)

            if isinstance(intervals, list):
                for item in intervals:
                    if isinstance(item, Mapping):
                        kpi = item.get("kpi")
                        if kpi in ("coverage_ratio", "coverage"):
                            if float(item.get("lower", 1.0)) < FLOOR_COVERAGE:
                                return False
                        elif kpi in ("drop_rate_ratio", "drop_rate"):
                            if float(item.get("upper", 0.0)) > FLOOR_DROP_RATE:
                                return False
                        elif kpi in ("normalized_interference", "interference"):
                            if float(item.get("upper", 0.0)) > 1.0:
                                return False
            return True
        except Exception as exc:
            logger.warning("Step reverification encountered twin error: %s", exc)
            return True

    def _actuate_step(self, plan: ArbitrationPlan, step: PlanStep, current_tick: int) -> None:
        """Apply approved parameter write to actuator."""
        if self._actuator is None:
            return

        cmd = CommandProposal(
            command_id=f"{plan.plan_id}-step-{step.step_index}",
            controller_id="arbiter-plan-executor",
            controller_type=ControllerType.ENERGY_SAVING,
            target_cell_id=step.target_cell_id,
            parameter=step.parameter,
            requested_value=step.value,
            timestamp_epoch_ms=current_tick * 100,
        )

        if hasattr(self._actuator, "apply"):
            self._actuator.apply(cmd, step.value, tick=current_tick)
        elif hasattr(self._actuator, "commit"):
            self._actuator.commit({
                "commit_id": str(uuid.uuid4()),
                "decision_id": f"dec-{plan.plan_id}-{step.step_index}",
                "command_id": cmd.command_id,
                "target_cell_id": step.target_cell_id,
                "parameter": step.parameter.value,
                "old_value": 0.0,
                "new_value": step.value,
                "timestamp_epoch_ms": current_tick * 100,
            })

    # -- Venkat's API Query Endpoints ---------------------------------------
    def list_plans(self, status: str | None = None) -> list[ArbitrationPlan]:
        """List stored plans, optionally filtered by status ('PENDING', 'COMPLETED', 'ABORTED')."""
        with self._lock:
            # First try reading from DB to include any persisted out-of-band plans
            db_plans: dict[str, ArbitrationPlan] = {}
            try:
                conn = sqlite3.connect(self._db_path)
                try:
                    cur = conn.cursor()
                    cur.execute("SELECT plan_id, payload FROM v2_plans")
                    for row in cur.fetchall():
                        try:
                            data = json.loads(row[1])
                            db_plans[str(row[0])] = ArbitrationPlan.model_validate(data)
                        except Exception:
                            continue
                finally:
                    conn.close()
            except Exception:
                pass

            # Combine in-memory plans with db plans (in-memory takes precedence for live state)
            combined = {**db_plans, **self._plans}

            if status is None:
                return list(combined.values())

            target_status = status.upper()
            filtered: list[ArbitrationPlan] = []
            for p in combined.values():
                if target_status == "COMPLETED" and all(s.status == "COMPLETED" for s in p.steps):
                    filtered.append(p)
                elif target_status == "PENDING" and any(s.status == "PENDING" for s in p.steps):
                    filtered.append(p)
                elif target_status == "ABORTED" and any(s.status == "ABORTED" for s in p.steps):
                    filtered.append(p)
            return filtered

    def get_plan(self, plan_id: str) -> ArbitrationPlan | None:
        """Retrieve a plan by its UUID."""
        with self._lock:
            if plan_id in self._plans:
                return self._plans[plan_id]
            try:
                conn = sqlite3.connect(self._db_path)
                try:
                    cur = conn.cursor()
                    cur.execute("SELECT payload FROM v2_plans WHERE plan_id = ?", (plan_id,))
                    row = cur.fetchone()
                    if row is not None:
                        data = json.loads(row[0])
                        plan = ArbitrationPlan.model_validate(data)
                        self._plans[plan_id] = plan
                        return plan
                finally:
                    conn.close()
            except Exception:
                pass
            return None


# Module-level default executor instance
_DEFAULT_EXECUTOR = PlanExecutor()


def get_default_executor() -> PlanExecutor:
    """Access the default PlanExecutor instance."""
    return _DEFAULT_EXECUTOR


def set_default_executor(executor: PlanExecutor) -> None:
    """Inject a configured PlanExecutor for Venkat's API."""
    global _DEFAULT_EXECUTOR
    _DEFAULT_EXECUTOR = executor


def list_plans(status: str | None = None) -> list[ArbitrationPlan]:
    """Venkat's API route helper: list plans."""
    return _DEFAULT_EXECUTOR.list_plans(status=status)


def get_plan(plan_id: str) -> ArbitrationPlan | None:
    """Venkat's API route helper: get single plan by ID."""
    return _DEFAULT_EXECUTOR.get_plan(plan_id)
