"""Transactional Actuator Enforcer and Commit API callable.

SRS FR-7.1, FR-7.2, FR-7.3.
Applies approved commands to power, tilt, sleep state, and offload ratio.
Enforces transactional atomicity, idempotency, and automatic rollback on failure.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Set, Union

from arbiter.actuator.rollback import RollbackManager
from arbiter.storage.commit_log import SQLiteCommitLogger
from arbiter.storage.lkg_store import LKGStore
from arbiter.twin.schemas import (
    ActuatorCommitRequest,
    ActuatorCommitResponse,
    NetworkActuationTarget,
)

# Canonical parameter names supported per FR-7.1 and spec
ALLOWED_PARAMETERS: Set[str] = {
    "TX_POWER_DBM",
    "POWER",
    "TILT_DEG",
    "TILT",
    "SLEEP_STATE",
    "SLEEP",
    "OFFLOAD_RATIO",
    "OFFLOAD",
}


def normalize_parameter_name(param: str) -> str:
    """Map parameter synonyms to canonical names."""
    p = param.upper().strip()
    if p in ("TX_POWER_DBM", "POWER"):
        return "TX_POWER_DBM"
    if p in ("TILT_DEG", "TILT"):
        return "TILT_DEG"
    if p in ("SLEEP_STATE", "SLEEP"):
        return "SLEEP_STATE"
    if p in ("OFFLOAD_RATIO", "OFFLOAD"):
        return "OFFLOAD_RATIO"
    return p


class ActuatorEnforcer:
    """Transactional executor enforcing approved arbitration commands (FR-7.1 to FR-7.3)."""

    def __init__(
        self,
        target: NetworkActuationTarget,
        commit_logger: SQLiteCommitLogger,
        lkg_store: Optional[LKGStore] = None,
        rollback_manager: Optional[RollbackManager] = None,
    ) -> None:
        self.target: NetworkActuationTarget = target
        self.commit_logger: SQLiteCommitLogger = commit_logger
        self.lkg_store: Optional[LKGStore] = lkg_store
        self.rollback_manager: RollbackManager = rollback_manager or RollbackManager()

    def validate_request(self, request: ActuatorCommitRequest) -> None:
        """Validate parameter names, numeric finiteness, and bounds."""
        norm_param = normalize_parameter_name(request.parameter)
        if norm_param not in ALLOWED_PARAMETERS:
            raise ValueError(f"Unsupported parameter '{request.parameter}'. Allowed: {ALLOWED_PARAMETERS}")

        for name, val in [("old_value", request.old_value), ("new_value", request.new_value)]:
            if math.isnan(val) or math.isinf(val):
                raise ValueError(f"Value for {name} must be finite: {val}")

        if norm_param == "TX_POWER_DBM":
            if request.new_value < 0.0 or request.new_value > 60.0:
                raise ValueError(f"TX_POWER_DBM out of range [0.0, 60.0]: {request.new_value}")
        elif norm_param == "TILT_DEG":
            if request.new_value < 0.0 or request.new_value > 90.0:
                raise ValueError(f"TILT_DEG out of range [0.0, 90.0]: {request.new_value}")
        elif norm_param == "OFFLOAD_RATIO":
            if request.new_value < 0.0 or request.new_value > 1.0:
                raise ValueError(f"OFFLOAD_RATIO out of range [0.0, 1.0]: {request.new_value}")

    def commit(self, request_data: Union[ActuatorCommitRequest, Dict[str, Any]]) -> Dict[str, Any]:
        """Atomically apply actuation change and persist to SQLite commit log.

        Guarantees:
          - Idempotency on commit_id: duplicate invocation returns prior result without re-applying.
          - Atomic Rollback: target failure triggers rollback and logs rollback_flag=1.
          - Persistence: every commit is audited in SQLite commit_log.
        """
        if isinstance(request_data, dict):
            request = ActuatorCommitRequest.model_validate(request_data)
        else:
            request = request_data

        # 1. Validation
        self.validate_request(request)
        norm_param = normalize_parameter_name(request.parameter)

        # 2. Idempotency check: duplicate commit_id returns original outcome
        existing = self.commit_logger.get_commit(request.commit_id)
        if existing is not None:
            is_rollback = bool(existing.get("rollback_flag", 0) == 1)
            return {
                "status": "ROLLED_BACK" if is_rollback else "COMMITTED",
                "commit_seq": int(existing["commit_seq"]),
                "applied": not is_rollback,
            }

        # 3. Apply to target
        apply_success = False
        try:
            apply_success = bool(self.target.apply_parameter(
                request.target_cell_id,
                norm_param,
                request.new_value,
            ))
        except Exception:
            apply_success = False

        # 4. Handle Failure / Rollback
        if not apply_success:
            # Trigger atomic rollback
            self.rollback_manager.rollback(
                self.target,
                request.target_cell_id,
                norm_param,
                request.old_value,
            )
            # Log failure with rollback_flag=1 for audit
            commit_seq = self.commit_logger.log_commit(
                commit_id=request.commit_id,
                decision_id=request.decision_id,
                command_id=request.command_id,
                target_cell_id=request.target_cell_id,
                parameter=norm_param,
                old_value=request.old_value,
                new_value=request.new_value,
                applied_at_epoch_ms=request.timestamp_epoch_ms,
                rollback_flag=1,
            )
            return {
                "status": "ROLLED_BACK",
                "commit_seq": commit_seq,
                "applied": False,
            }

        # 5. Handle Success
        commit_seq = self.commit_logger.log_commit(
            commit_id=request.commit_id,
            decision_id=request.decision_id,
            command_id=request.command_id,
            target_cell_id=request.target_cell_id,
            parameter=norm_param,
            old_value=request.old_value,
            new_value=request.new_value,
            applied_at_epoch_ms=request.timestamp_epoch_ms,
            rollback_flag=0,
        )

        if self.lkg_store is not None:
            self.lkg_store.set_lkg(
                cell_id=request.target_cell_id,
                parameter=norm_param,
                value=request.new_value,
                updated_at_epoch_ms=request.timestamp_epoch_ms,
            )

        return {
            "status": "COMMITTED",
            "commit_seq": commit_seq,
            "applied": True,
        }


# Global default actuator instance for standalone API callable
_global_actuator: Optional[ActuatorEnforcer] = None


def set_global_actuator(enforcer: ActuatorEnforcer) -> None:
    """Register active ActuatorEnforcer instance."""
    global _global_actuator
    _global_actuator = enforcer


def get_global_actuator() -> Optional[ActuatorEnforcer]:
    """Retrieve active ActuatorEnforcer instance."""
    global _global_actuator
    return _global_actuator


def handle_actuator_commit(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Plain Python callable for POST /api/v1/actuator/commit (Section 3.7).

    Dev 4 mounts this directly into FastAPI routing without schema changes.
    """
    enforcer = get_global_actuator()
    if enforcer is None:
        # Minimal in-memory target and logger fallback
        class InMemTarget:
            def __init__(self) -> None:
                self.params: Dict[str, Dict[str, float]] = {}

            def apply_parameter(self, cell_id: str, parameter: str, value: float) -> bool:
                if cell_id not in self.params:
                    self.params[cell_id] = {}
                self.params[cell_id][parameter] = value
                return True

            def get_parameter(self, cell_id: str, parameter: str) -> float:
                return self.params.get(cell_id, {}).get(parameter, 0.0)

        import tempfile
        tmp_db = tempfile.mktemp(suffix=".db")
        logger = SQLiteCommitLogger(tmp_db)
        enforcer = ActuatorEnforcer(target=InMemTarget(), commit_logger=logger)
        set_global_actuator(enforcer)

    return enforcer.commit(payload)
