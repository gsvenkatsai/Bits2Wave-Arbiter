"""Actuator Rollback Manager.

SRS FR-7.2: Actuator SHALL roll back on error.
Restores baseline radio configuration upon actuation failures.
"""
from __future__ import annotations

import logging
from typing import Optional

from arbiter.twin.schemas import NetworkActuationTarget

logger = logging.getLogger(__name__)


class RollbackManager:
    """Manages restoration of cell parameters on actuation failure (FR-7.2)."""

    def rollback(
        self,
        target: NetworkActuationTarget,
        cell_id: str,
        parameter: str,
        old_value: float,
    ) -> bool:
        """Attempt to restore the previous known parameter value on the actuation target.

        Returns True if rollback successfully reapplied the old value, False otherwise.
        Never raises exceptions to caller.
        """
        try:
            success = target.apply_parameter(cell_id, parameter, old_value)
            if not success:
                logger.error(f"Rollback failed on target {cell_id} for parameter {parameter}={old_value}")
                return False
            logger.info(f"Successfully rolled back {cell_id} {parameter} to {old_value}")
            return True
        except Exception as ex:
            logger.exception(f"Exception during rollback on {cell_id} {parameter}: {ex}")
            return False
