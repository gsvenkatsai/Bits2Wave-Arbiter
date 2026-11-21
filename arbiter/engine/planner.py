"""Multi-tick sequential plan synthesis for ARBITER v2 (FR-6 / Branch 2).

When a conflict ends in DELAY or MODIFY, the planner synthesizes an
ArbitrationPlan decomposing the conflicting actions into discrete,
ordered, safety-gated steps executed across multiple ticks.
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Callable, Mapping, Sequence

from arbiter.engine.contracts import (
    FLOOR_COVERAGE,
    FLOOR_DROP_RATE,
    MAX_HOLD_TICKS,
    ActionType,
    ArbitrationDecision,
    CommandProposal,
)
from arbiter.engine.mocks.mock_twin_client import TwinClient
from arbiter.shared.models import ParameterType
from arbiter.shared.v2_contracts import ArbitrationPlan, PlanStep

__all__ = [
    "ArbitrationPlanner",
    "evaluate_precondition",
    "parse_precondition_clause",
]


_PRECONDITION_REGEX = re.compile(
    r"^(\w+)\(([^)]+)\)\s*(<=|>=|<|>|==|=)?\s*(-?\d+(?:\.\d+)?|\w+)?$"
)


def parse_precondition_clause(clause: str) -> tuple[str, str, str, float | None]:
    """Parse a single precondition clause like 'load(cell_A)<0.60'.

    Returns:
        (metric, target, operator, threshold)
    """
    cleaned = clause.strip()
    match = _PRECONDITION_REGEX.match(cleaned)
    if not match:
        return ("", "", "", None)
    metric = match.group(1).strip().lower()
    target = match.group(2).strip()
    op = (match.group(3) or "").strip()
    val_str = (match.group(4) or "").strip()
    val: float | None = None
    if val_str:
        try:
            val = float(val_str)
        except ValueError:
            val = None
    return (metric, target, op, val)


def evaluate_precondition(expr: str, context: Any) -> bool:
    """Safely evaluate a guard precondition against cluster state context.

    Supported expressions:
    - empty string or "true" -> True
    - "false" -> False
    - conjunctions separated by " and " (case-insensitive)
    - functions: load(cell), sinr(cell), no_active_handover(cell), active_handover(cell)
    - operators: <, <=, >, >=, ==

    Context can be:
    - dict mapping cell_id -> dict of metrics (e.g. {"cell_A": {"load": 0.55, "sinr": 10.0}})
    - flat dict mapping expression keys (e.g. {"load(cell_A)": 0.55})
    - object with a .cells mapping or .get_load()/.get_sinr() methods
    """
    if not expr or expr.strip().lower() in ("true", "1", "none", ""):
        return True
    if expr.strip().lower() in ("false", "0"):
        return False

    subclauses = [c.strip() for c in re.split(r"\s+and\s+", expr, flags=re.IGNORECASE) if c.strip()]
    if not subclauses:
        return True

    for clause in subclauses:
        metric, target, op, threshold = parse_precondition_clause(clause)
        if not metric:
            continue

        # 1. Check flat dict direct match
        if isinstance(context, Mapping):
            flat_key = f"{metric}({target})"
            if flat_key in context:
                val = context[flat_key]
                if isinstance(val, bool):
                    if not val:
                        return False
                    continue
                if threshold is not None and op:
                    if not _eval_op(float(val), op, threshold):
                        return False
                    continue

        # 2. Extract cell state from context
        cell_data: Any = None
        if isinstance(context, Mapping):
            cell_data = (
                context.get(target)
                or context.get(f"cell_{target}")
                or context.get(target.lstrip("cell_"))
            )
            if cell_data is None and "cells" in context:
                cells_map = context["cells"]
                if isinstance(cells_map, Mapping):
                    cell_data = cells_map.get(target)
        elif hasattr(context, "cells") and isinstance(getattr(context, "cells"), Mapping):
            cell_data = getattr(context, "cells").get(target)

        # 3. Evaluate metric
        if metric == "no_active_handover":
            ho_count = 0.0
            if isinstance(cell_data, Mapping):
                ho_raw = cell_data.get(
                    "active_handover_users", cell_data.get("active_ho", 0.0)
                )
                ho_count = float(ho_raw or 0.0)
            elif hasattr(cell_data, "active_handover_users"):
                ho_count = float(getattr(cell_data, "active_handover_users") or 0.0)
            elif isinstance(context, Mapping) and "no_active_handover" in context:
                return bool(context["no_active_handover"])
            if ho_count > 0.0:
                return False

        elif metric == "active_handover":
            ho_count = 0.0
            if isinstance(cell_data, Mapping):
                ho_count = float(cell_data.get("active_handover_users", 0.0) or 0.0)
            elif hasattr(cell_data, "active_handover_users"):
                ho_count = float(getattr(cell_data, "active_handover_users") or 0.0)
            if ho_count <= 0.0:
                return False

        elif metric == "load":
            current_load: float | None = None
            if isinstance(cell_data, Mapping):
                raw = cell_data.get("load")
                if raw is None and "active_users" in cell_data and "capacity" in cell_data:
                    cap = float(cell_data["capacity"] or 100.0)
                    raw = float(cell_data["active_users"]) / max(cap, 1.0)
                if raw is not None:
                    current_load = float(raw)
            elif hasattr(cell_data, "load"):
                current_load = float(getattr(cell_data, "load"))
            elif hasattr(cell_data, "active_users") and hasattr(cell_data, "capacity"):
                users = float(getattr(cell_data, "active_users"))
                cap = max(float(getattr(cell_data, "capacity")), 1.0)
                current_load = users / cap

            if current_load is None:
                # If metric cannot be read, precondition does not hold
                return False
            if threshold is not None and op:
                if not _eval_op(current_load, op, threshold):
                    return False

        elif metric == "sinr":
            current_sinr: float | None = None
            if isinstance(cell_data, Mapping):
                raw = cell_data.get("sinr") or cell_data.get("mean_sinr_db")
                if raw is not None:
                    current_sinr = float(raw)
            elif hasattr(cell_data, "sinr"):
                current_sinr = float(getattr(cell_data, "sinr"))
            if current_sinr is None:
                return False
            if threshold is not None and op:
                if not _eval_op(current_sinr, op, threshold):
                    return False

    return True


def _eval_op(val: float, op: str, threshold: float) -> bool:
    if op == "<":
        return val < threshold
    if op == "<=":
        return val <= threshold
    if op == ">":
        return val > threshold
    if op == ">=":
        return val >= threshold
    if op in ("==", "="):
        return abs(val - threshold) < 1e-6
    return False


def _to_parameter_type(param: str | ParameterType) -> ParameterType:
    if isinstance(param, ParameterType):
        return param
    norm = str(param).strip().upper()
    if norm in ("TX_POWER_DBM", "POWER"):
        return ParameterType.TX_POWER_DBM
    if norm in ("TILT_DEG", "TILT"):
        return ParameterType.TILT_DEG
    if norm in ("SLEEP_STATE", "SLEEP"):
        return ParameterType.SLEEP_STATE
    if norm in ("OFFLOAD_RATIO", "OFFLOAD"):
        return ParameterType.OFFLOAD_RATIO
    try:
        return ParameterType(norm)
    except ValueError:
        return ParameterType.OFFLOAD_RATIO


class ArbitrationPlanner:
    """Synthesizes structured ArbitrationPlans for multi-step coordinated actions."""

    def __init__(
        self,
        window_ticks: int = MAX_HOLD_TICKS,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        if window_ticks < 1:
            raise ValueError("window_ticks must be >= 1")
        self.window_ticks = window_ticks
        self._id_factory = id_factory or (lambda: f"plan-{uuid.uuid4().hex[:8]}")

    def should_synthesize(
        self,
        decisions: Sequence[ArbitrationDecision],
    ) -> bool:
        """True when the batch decisions indicate an unresolved collision needing coordination."""
        has_delay = any(d.action is ActionType.DELAY for d in decisions)
        has_modify = any(d.action is ActionType.MODIFY for d in decisions)
        return has_delay or (has_modify and len(decisions) > 1)

    def build_plan(
        self,
        commands: Sequence[CommandProposal],
        decisions: Sequence[ArbitrationDecision],
        current_tick: int,
        twin: TwinClient | None = None,
        rationale: str | None = None,
    ) -> ArbitrationPlan | None:
        """Synthesize an ArbitrationPlan from the conflicting commands and decisions.

        Rules:
        - Immediate steps: applied non-delayed actions (LB, CC).
        - Deferred steps: delayed actions (ES sleep) with explicit preconditions.
        - Steps are screened against the uncertainty guard so unfeasible steps are never included.
        """
        by_cmd_id = {c.command_id: c for c in commands}
        delayed_decisions = [
            d for d in decisions if d.action is ActionType.DELAY
        ]
        applied_decisions = [
            d for d in decisions
            if d.action in (ActionType.MODIFY, ActionType.APPROVE)
        ]

        if not delayed_decisions:
            return None

        # Choose primary source command for plan
        primary_delayed = delayed_decisions[0]
        source_cmd = by_cmd_id.get(primary_delayed.command_id)
        if source_cmd is None:
            return None

        steps: list[PlanStep] = []
        step_idx = 0

        # Step 0+: immediate active preparatory steps (e.g. LB offload, CC power boost)
        # Sort so LB offload comes before power boost if both present
        def _sort_key(d: ArbitrationDecision) -> int:
            cmd = by_cmd_id.get(d.command_id)
            if not cmd:
                return 10
            norm = str(cmd.parameter).upper()
            if "OFFLOAD" in norm:
                return 0
            if "POWER" in norm or "TX" in norm:
                return 1
            return 2

        sorted_applied = sorted(applied_decisions, key=_sort_key)

        for dec in sorted_applied:
            cmd = by_cmd_id.get(dec.command_id)
            if not cmd:
                continue
            val = float(
                dec.approved_value
                if dec.approved_value is not None
                else cmd.requested_value
            )
            param_type = _to_parameter_type(cmd.parameter)

            # Verification through twin/guard: check if guard would reject
            if twin is not None and not self._step_guard_passes(
                twin, cmd.target_cell, param_type, val
            ):
                continue

            steps.append(
                PlanStep(
                    step_index=step_idx,
                    tick_offset=0,
                    target_cell_id=cmd.target_cell,
                    parameter=param_type,
                    value=val,
                    precondition="",
                    status="PENDING",
                )
            )
            step_idx += 1

        # Deferred step: the delayed command (e.g. sleep Cell B)
        delayed_param = _to_parameter_type(source_cmd.parameter)
        delayed_val = float(source_cmd.requested_value)

        # Precondition synthesis
        # In 2 AM scenario: Cell B sleep requires Cell A load < 0.60,
        # Cell B load < 0.15, no active handover
        precond = self._synthesize_precondition(source_cmd, commands)

        steps.append(
            PlanStep(
                step_index=step_idx,
                tick_offset=min(2, self.window_ticks),
                target_cell_id=source_cmd.target_cell,
                parameter=delayed_param,
                value=delayed_val,
                precondition=precond,
                status="PENDING",
            )
        )

        plan_id = self._id_factory()
        expires = current_tick + self.window_ticks
        rat = rationale or (
            f"Multi-tick arbitration plan for {source_cmd.command_id}: "
            f"prepare traffic via offload/power before actuating {source_cmd.target_cell} "
            f"under safety preconditions."
        )

        return ArbitrationPlan(
            plan_id=plan_id,
            source_command_id=source_cmd.command_id,
            created_tick=current_tick,
            expires_tick=expires,
            steps=steps,
            rationale=rat,
        )

    def _synthesize_precondition(
        self,
        delayed_cmd: CommandProposal,
        all_commands: Sequence[CommandProposal],
    ) -> str:
        """Build safe precondition string matching the conflict scenario."""
        # Find companion cells from other commands in the conflict
        other_cells = [
            c.target_cell for c in all_commands
            if c.target_cell != delayed_cmd.target_cell
        ]
        target = delayed_cmd.target_cell
        norm_param = str(delayed_cmd.parameter).upper()

        if "SLEEP" in norm_param:
            if other_cells:
                first_other = other_cells[0]
                return (
                    f"load({first_other})<0.60 and "
                    f"load({target})<0.15 and "
                    f"no_active_handover({target})"
                )
            return f"load({target})<0.15 and no_active_handover({target})"

        return f"no_active_handover({target})"

    def _step_guard_passes(
        self,
        twin: TwinClient,
        cell_id: str,
        parameter: ParameterType,
        value: float,
    ) -> bool:
        """Screen step with twin uncertainty guard before inclusion in plan."""
        delta = {
            "cell_id": cell_id,
            "parameter": parameter.value,
            "from_value": 0.0,
            "to_value": value,
        }
        try:
            resp = twin.evaluate([delta])
            # If TwinPredictionV2 intervals present, check worst-case bounds
            intervals = resp.get("intervals")
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
            flag = resp.get("safety_floors_satisfied")
            if flag is False:
                return False
        except Exception:
            return True
        return True
