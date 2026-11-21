"""Branch 2 conflict detection (FR-4): classifier, graph, cycles, partitioning."""
from __future__ import annotations

from arbiter.detector.classifier import (
    DEFAULT_HOP_RADIUS,
    INDIRECT_RULES,
    ConflictClassifier,
    IndirectRule,
    RuleKind,
    direction,
    normalize_parameter,
)
from arbiter.detector.cycle_detector import HandoverEdge, PingPongCycleDetector
from arbiter.detector.graph import ConflictGraphBuilder
from arbiter.detector.partitioner import GraphPartitioner, SolverFn
from arbiter.engine.contracts import PINGPONG_WINDOW

__all__ = [
    "ConflictClassifier",
    "DEFAULT_HOP_RADIUS",
    "INDIRECT_RULES",
    "IndirectRule",
    "RuleKind",
    "direction",
    "normalize_parameter",
    "ConflictGraphBuilder",
    "PingPongCycleDetector",
    "HandoverEdge",
    "GraphPartitioner",
    "SolverFn",
    "PINGPONG_WINDOW",
]
