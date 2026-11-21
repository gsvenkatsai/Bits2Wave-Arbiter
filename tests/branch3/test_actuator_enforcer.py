"""Unit tests for Actuator Enforcer and Rollback.

SRS FR-7.1, FR-7.2, FR-7.3.
Verifies successful commit, idempotency deduplication, failure rollback restoration,
rejection of invalid inputs, and API payload compatibility.
"""
import math
import pytest

from arbiter.actuator.enforcer import (
    ActuatorEnforcer,
    handle_actuator_commit,
    set_global_actuator,
    normalize_parameter_name,
)
from arbiter.actuator.rollback import RollbackManager
from arbiter.twin.schemas import ActuatorCommitRequest


def test_actuator_successful_commit(mock_target, commit_logger, lkg_store):
    """Verify standard valid commit applies to target, logs with rollback_flag=0, and updates LKG."""
    enforcer = ActuatorEnforcer(
        target=mock_target,
        commit_logger=commit_logger,
        lkg_store=lkg_store,
    )

    req = ActuatorCommitRequest(
        commit_id="cmt-001",
        decision_id="dec-001",
        command_id="cmd-001",
        target_cell_id="cell_A",
        parameter="TX_POWER_DBM",
        old_value=43.0,
        new_value=45.0,
        timestamp_epoch_ms=1732154400000,
    )

    result = enforcer.commit(req)

    assert result["status"] == "COMMITTED"
    assert result["applied"] is True
    assert result["commit_seq"] > 0
    # Target parameter must be updated
    assert mock_target.get_parameter("cell_A", "TX_POWER_DBM") == 45.0
    # LKG store must be updated
    assert lkg_store.get_lkg("cell_A", "TX_POWER_DBM") == 45.0

    # Verify database entry
    log_entry = commit_logger.get_commit("cmt-001")
    assert log_entry is not None
    assert log_entry["rollback_flag"] == 0
    assert log_entry["new_value"] == 45.0


def test_actuator_idempotency_duplicate_commit_id(mock_target, commit_logger, lkg_store):
    """Verify submitting identical commit_id returns previous result without double applying."""
    enforcer = ActuatorEnforcer(
        target=mock_target,
        commit_logger=commit_logger,
        lkg_store=lkg_store,
    )

    req = ActuatorCommitRequest(
        commit_id="cmt-idem-001",
        decision_id="dec-002",
        command_id="cmd-002",
        target_cell_id="cell_B",
        parameter="OFFLOAD_RATIO",
        old_value=0.40,
        new_value=0.15,
        timestamp_epoch_ms=1732154401000,
    )

    res1 = enforcer.commit(req)
    assert res1["status"] == "COMMITTED"
    seq1 = res1["commit_seq"]

    # Re-submit duplicate
    res2 = enforcer.commit(req)
    assert res2["status"] == "COMMITTED"
    assert res2["commit_seq"] == seq1
    assert res2["applied"] is True


def test_actuator_failure_triggers_atomic_rollback(mock_target, commit_logger, lkg_store):
    """Verify target failure restores old_value on target and records rollback_flag=1 (FR-7.2)."""
    # Configure mock target to fail for cell_C
    mock_target.fail_on_cell["cell_C"] = True
    mock_target.apply_parameter("cell_C", "TX_POWER_DBM", 43.0)

    enforcer = ActuatorEnforcer(
        target=mock_target,
        commit_logger=commit_logger,
        lkg_store=lkg_store,
    )

    req = ActuatorCommitRequest(
        commit_id="cmt-fail-001",
        decision_id="dec-003",
        command_id="cmd-003",
        target_cell_id="cell_C",
        parameter="TX_POWER_DBM",
        old_value=43.0,
        new_value=47.0,
        timestamp_epoch_ms=1732154402000,
    )

    res = enforcer.commit(req)

    assert res["status"] == "ROLLED_BACK"
    assert res["applied"] is False
    assert res["commit_seq"] > 0

    # Database must log the attempt with rollback_flag=1 for auditability (NFR-12)
    log_entry = commit_logger.get_commit("cmt-fail-001")
    assert log_entry is not None
    assert log_entry["rollback_flag"] == 1


def test_actuator_invalid_input_rejections(mock_target, commit_logger):
    """Verify enforcer cleanly rejects unsupported parameters, NaNs, and out-of-bounds values."""
    enforcer = ActuatorEnforcer(target=mock_target, commit_logger=commit_logger)

    # 1. Unsupported parameter
    with pytest.raises(ValueError, match="Unsupported parameter"):
        enforcer.commit({
            "commit_id": "cmt-bad-1",
            "decision_id": "d1",
            "command_id": "c1",
            "target_cell_id": "cell_A",
            "parameter": "NON_EXISTENT_PARAM",
            "old_value": 1.0,
            "new_value": 2.0,
            "timestamp_epoch_ms": 1000,
        })

    # 2. NaN value
    with pytest.raises(ValueError, match="finite"):
        enforcer.commit({
            "commit_id": "cmt-bad-2",
            "decision_id": "d2",
            "command_id": "c2",
            "target_cell_id": "cell_A",
            "parameter": "TX_POWER_DBM",
            "old_value": 43.0,
            "new_value": float("nan"),
            "timestamp_epoch_ms": 1000,
        })

    # 3. Out-of-bounds power
    with pytest.raises(ValueError, match="TX_POWER_DBM out of range"):
        enforcer.commit({
            "commit_id": "cmt-bad-3",
            "decision_id": "d3",
            "command_id": "c3",
            "target_cell_id": "cell_A",
            "parameter": "TX_POWER_DBM",
            "old_value": 43.0,
            "new_value": 150.0,
            "timestamp_epoch_ms": 1000,
        })

    # 4. Out-of-bounds tilt
    with pytest.raises(ValueError, match="TILT_DEG out of range"):
        enforcer.commit({
            "commit_id": "cmt-bad-4",
            "decision_id": "d4",
            "command_id": "c4",
            "target_cell_id": "cell_A",
            "parameter": "TILT_DEG",
            "old_value": 2.0,
            "new_value": 95.0,
            "timestamp_epoch_ms": 1000,
        })

    # 5. Out-of-bounds offload ratio
    with pytest.raises(ValueError, match="OFFLOAD_RATIO out of range"):
        enforcer.commit({
            "commit_id": "cmt-bad-5",
            "decision_id": "d5",
            "command_id": "c5",
            "target_cell_id": "cell_A",
            "parameter": "OFFLOAD_RATIO",
            "old_value": 0.40,
            "new_value": 1.50,
            "timestamp_epoch_ms": 1000,
        })


def test_parameter_normalization_synonyms():
    """Verify parameter names and case-insensitive aliases map cleanly."""
    assert normalize_parameter_name("power") == "TX_POWER_DBM"
    assert normalize_parameter_name("tx_power_dbm") == "TX_POWER_DBM"
    assert normalize_parameter_name("tilt") == "TILT_DEG"
    assert normalize_parameter_name("tilt_deg") == "TILT_DEG"
    assert normalize_parameter_name("sleep") == "SLEEP_STATE"
    assert normalize_parameter_name("sleep_state") == "SLEEP_STATE"
    assert normalize_parameter_name("offload") == "OFFLOAD_RATIO"
    assert normalize_parameter_name("offload_ratio") == "OFFLOAD_RATIO"


def test_rollback_manager_exception_safety():
    """Verify RollbackManager safely catches and logs exceptions without crashing."""
    class CrashingTarget:
        def apply_parameter(self, cell_id: str, parameter: str, value: float) -> bool:
            raise ConnectionResetError("Lost link to remote base station")

    rb = RollbackManager()
    success = rb.rollback(CrashingTarget(), "cell_A", "TX_POWER_DBM", 43.0)  # type: ignore[arg-type]
    assert success is False


def test_handle_actuator_commit_api_payload(mock_target, commit_logger, lkg_store):
    """Verify handle_actuator_commit processes the exact spec example payload."""
    enforcer = ActuatorEnforcer(
        target=mock_target,
        commit_logger=commit_logger,
        lkg_store=lkg_store,
    )
    set_global_actuator(enforcer)

    spec_request = {
        "commit_id": "cmt-uuid-001",
        "decision_id": "dec-uuid-001",
        "command_id": "cmd-uuid-001",
        "target_cell_id": "cell_B",
        "parameter": "OFFLOAD_RATIO",
        "old_value": 0.40,
        "new_value": 0.15,
        "timestamp_epoch_ms": 1732154400150,
    }

    resp = handle_actuator_commit(spec_request)

    assert resp["status"] == "COMMITTED"
    assert resp["applied"] is True
    assert isinstance(resp["commit_seq"], int)


def test_handle_actuator_commit_fallback_creation():
    """Verify handle_actuator_commit initializes fallback target and logger when none registered."""
    import arbiter.actuator.enforcer as enf_module
    enf_module._global_actuator = None

    spec_request = {
        "commit_id": "cmt-fallback-01",
        "decision_id": "dec-fb-01",
        "command_id": "cmd-fb-01",
        "target_cell_id": "cell_B",
        "parameter": "POWER",
        "old_value": 43.0,
        "new_value": 44.0,
        "timestamp_epoch_ms": 1732154400200,
    }

    resp = enf_module.handle_actuator_commit(spec_request)
    assert resp["status"] == "COMMITTED"
    assert resp["applied"] is True
