"""Contract C1: the twin factory the demo and the API both build from.

``make_default_twin(topology, kind)`` returns an object that satisfies the
existing ``TwinClient`` protocol used by
:class:`arbiter.queue.orchestrator.QueueOrchestrator`:

* ``kind="real"`` (default) wraps Aaditya's real
  :class:`arbiter.twin.evaluator.DigitalTwinEvaluator` in
  :class:`arbiter.engine.twin_adapter.TwinClientAdapter` and reports
  :meth:`DefaultTwinClient.calibration_status` (contract C3). It fails loudly
  if the evaluator cannot be built - it never degrades to a mock.
* ``kind="scripted"`` is the old analytical toy twin from
  :mod:`arbiter.engine.scripted_twin`. It is only produced on an explicit
  request and always logs a warning.

The real twin needs a cluster snapshot to evaluate against. With no
``state_provider`` supplied, :class:`NominalTopologyStateProvider` builds one
from the topology's **real** adjacency and the simulator's **real** Cost-231
link budget evaluated at a documented reference point (the midpoint between a
cell and its nearest neighbour). It carries no live telemetry: it is a nominal
snapshot, and ``calibration_status()`` reports ``calibrated=False`` with
``n_calibration=0`` until real calibration data exists. Pass a live provider
(e.g. one reading the simulator) to get live predictions.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List

import numpy as np

from arbiter.engine.contracts import TWIN_TIMEOUT_MS
from arbiter.engine.twin_adapter import TwinClientAdapter

__all__ = [
    "DEFAULT_TOPOLOGY",
    "KINDS",
    "DefaultTwinClient",
    "NominalTopologyStateProvider",
    "TwinBuildError",
    "make_default_twin",
    "topology_adjacency",
]

logger = logging.getLogger(__name__)

#: Topology used when the caller does not name one.
DEFAULT_TOPOLOGY = "5_CELL_CORE"

#: Accepted values of ``kind``.
KINDS: tuple[str, str] = ("real", "scripted")

#: KPI whose conformal half-width is reported as ``interval_halfwidth_db``.
_INTERVAL_KPI = "mean_sinr_db"


class TwinBuildError(RuntimeError):
    """Raised when the requested twin cannot be built from real components."""


def _topology_configs(topology: str) -> Dict[str, Any]:
    """Cell configurations of ``topology`` from the simulator's grid."""
    try:
        from arbiter.simulator.grid import HexagonalGrid
    except Exception as exc:  # pragma: no cover - import-guard only
        raise TwinBuildError(
            "cannot resolve the topology: arbiter.simulator.grid is "
            f"unavailable ({exc})"
        ) from exc
    grid = HexagonalGrid()
    configs = (
        grid.generate_19_cell_cluster(isd=500.0)
        if "19" in topology
        else grid.generate_5_cell_cluster(isd=500.0)
    )
    if not configs:
        raise TwinBuildError(f"topology {topology!r} produced no cells")
    return configs


def topology_adjacency(topology: str = DEFAULT_TOPOLOGY) -> Dict[str, List[str]]:
    """Symmetric adjacency map of ``topology`` as the simulator defines it."""
    configs = _topology_configs(topology)
    return {
        str(cell_id): list(config.neighbor_cell_ids)
        for cell_id, config in configs.items()
    }


class NominalTopologyStateProvider:
    """``NetworkStateProvider`` over a static, nominal topology snapshot.

    One reference point per cell is used: the midpoint between the cell and
    its first neighbour (i.e. half the inter-site distance). Received powers
    come from the simulator's real Cost-231 model, so the snapshot is a
    nominal link budget - not measured telemetry. ``ue_counts`` and
    ``cell_loads`` are left empty on purpose: the twin's analytical model then
    applies its own documented defaults instead of numbers invented here.
    """

    def __init__(self, topology: str = DEFAULT_TOPOLOGY) -> None:
        self.topology = topology
        self._configs = _topology_configs(topology)
        self._adjacency = {
            str(cell_id): list(config.neighbor_cell_ids)
            for cell_id, config in self._configs.items()
        }
        self._snapshot = self._build_snapshot()

    @property
    def adjacency(self) -> Dict[str, List[str]]:
        """Adjacency map of this provider's topology."""
        return {cell: list(neighbours) for cell, neighbours in self._adjacency.items()}

    def _reference_points(self) -> List[Any]:
        points: List[Any] = []
        for cell_id in sorted(self._configs):
            config = self._configs[cell_id]
            neighbours = sorted(config.neighbor_cell_ids)
            if neighbours:
                other = self._configs[neighbours[0]]
                points.append(
                    (
                        (config.x_coord + other.x_coord) / 2.0,
                        (config.y_coord + other.y_coord) / 2.0,
                    )
                )
            else:  # isolated cell: fall back to its own coordinate
                points.append((config.x_coord, config.y_coord))
        return points

    def _build_snapshot(self) -> Any:
        from arbiter.simulator.propagation import Cost231PropagationModel
        from arbiter.twin.schemas import ClusterSnapshot

        propagation = Cost231PropagationModel()
        cell_ids = sorted(self._configs)
        configs = [self._configs[cell_id] for cell_id in cell_ids]
        coords = np.array(self._reference_points(), dtype=np.float64)
        rx_matrix = propagation.compute_rx_matrix(coords, list(configs))

        n_cells = len(cell_ids)
        coupling = np.array(rx_matrix, dtype=np.float64)
        signal_powers = np.zeros(n_cells, dtype=np.float64)
        interference = np.zeros(n_cells, dtype=np.float64)
        for index in range(n_cells):
            signal_powers[index] = float(coupling[index, index])
            interference[index] = float(
                np.sum(coupling[index]) - coupling[index, index]
            )
        np.fill_diagonal(coupling, 0.0)

        return ClusterSnapshot(
            version=1,
            cell_ids=cell_ids,
            coupling_matrix=coupling,
            tx_power_dbm={
                cid: float(self._configs[cid].current_tx_power_dbm)
                for cid in cell_ids
            },
            is_sleeping={
                cid: bool(self._configs[cid].is_sleeping) for cid in cell_ids
            },
            signal_powers_w=signal_powers,
            current_interference_w=interference,
            ue_counts={},
            noise_floor_w=float(Cost231PropagationModel.N0_WATTS),
            neighbor_map=self.adjacency,
            antenna_tilt_deg={
                cid: float(self._configs[cid].antenna_tilt_deg)
                for cid in cell_ids
            },
        )

    def get_snapshot(self) -> Any:
        """Return the nominal snapshot (cached; version is constant)."""
        return self._snapshot


class DefaultTwinClient(TwinClientAdapter):
    """``TwinClient`` over the real evaluator, plus calibration reporting."""

    def calibration_status(self) -> Dict[str, Any]:
        """Contract C3: ``{calibrated, n_calibration, interval_halfwidth_db}``."""
        delegate = getattr(self._evaluator, "calibration_status", None)
        if callable(delegate):
            raw = delegate()
            if isinstance(raw, Dict):
                return {
                    "calibrated": bool(raw.get("calibrated", False)),
                    "n_calibration": int(raw.get("n_calibration", 0)),
                    "interval_halfwidth_db": raw.get("interval_halfwidth_db"),
                }

        conformal = getattr(self._evaluator, "conformal_predictor", None)
        if conformal is None:
            return {
                "calibrated": False,
                "n_calibration": 0,
                "interval_halfwidth_db": None,
            }

        minimum = int(getattr(conformal, "min_calibration_points", 30))
        counts: List[int] = []
        for kpi in ("coverage_ratio", "drop_rate_ratio", _INTERVAL_KPI):
            counter = getattr(conformal, "get_calibration_count", None)
            if callable(counter):
                counts.append(int(counter(kpi)))
        n_calibration = min(counts) if counts else 0

        halfwidth: float | None = None
        quantile = getattr(conformal, "compute_quantile", None)
        if callable(quantile):
            value, is_calibrated, _ = quantile(_INTERVAL_KPI, 0.1)
            if is_calibrated:
                halfwidth = float(value)

        return {
            "calibrated": n_calibration >= minimum,
            "n_calibration": n_calibration,
            "interval_halfwidth_db": halfwidth,
        }


def _build_real_evaluator(
    topology: str,
    state_provider: Any | None,
) -> Any:
    """Evaluator instance: explicit provider, else the global one, else nominal."""
    try:
        from arbiter.twin.evaluator import (
            DigitalTwinEvaluator,
            get_global_evaluator,
        )
    except Exception as exc:
        raise TwinBuildError(
            "cannot build the real twin: arbiter.twin.evaluator is unavailable "
            f"({exc})"
        ) from exc

    if state_provider is not None:
        try:
            return DigitalTwinEvaluator(state_provider=state_provider)
        except Exception as exc:
            raise TwinBuildError(
                f"cannot build the real twin over the supplied state provider: {exc}"
            ) from exc

    existing = get_global_evaluator()
    if existing is not None:
        return existing

    provider = NominalTopologyStateProvider(topology)
    try:
        return DigitalTwinEvaluator(state_provider=provider)
    except Exception as exc:
        raise TwinBuildError(
            f"cannot build the real twin for topology {topology!r}: {exc}"
        ) from exc


def make_default_twin(
    topology: str = DEFAULT_TOPOLOGY,
    kind: str = "real",
    *,
    state_provider: Any | None = None,
    timeout_budget_ms: float = TWIN_TIMEOUT_MS,
    clock: Callable[[], float] | None = None,
) -> Any:
    """Build the default twin for ``topology``.

    Args:
        topology: simulator topology name (``"5_CELL_CORE"`` or a 19-cell one).
        kind: ``"real"`` (digital twin) or ``"scripted"`` (analytical toy).
        state_provider: optional ``NetworkStateProvider`` for live predictions.
        timeout_budget_ms: twin evaluation budget handed to the evaluator.
        clock: injectable monotonic clock for the adapter's latency measurement.

    Raises:
        ValueError: unknown ``kind``.
        TwinBuildError: the real evaluator could not be constructed.
    """
    normalized = str(kind).strip().lower()
    if normalized == "scripted":
        from arbiter.engine.scripted_twin import make_scripted_twin

        return make_scripted_twin()
    if normalized != "real":
        raise ValueError(
            f"unknown twin kind {kind!r}; expected one of {list(KINDS)}"
        )

    evaluator = _build_real_evaluator(topology, state_provider)
    kwargs: Dict[str, Any] = {"timeout_budget_ms": timeout_budget_ms}
    if clock is not None:
        kwargs["clock"] = clock
    model = DefaultTwinClient(evaluator, **kwargs)
    logger.info(
        "real twin ready for topology %s (calibration: %s)",
        topology,
        model.calibration_status(),
    )
    return model
