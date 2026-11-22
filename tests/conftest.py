"""Global pytest configuration and compatibility guards for Python 3.13+."""
from __future__ import annotations

import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Safe typing guard for Python 3.13 networkx generics if evaluated
try:
    import networkx as _nx  # type: ignore[import-untyped]
    if not hasattr(_nx.Graph, "__class_getitem__"):
        setattr(
            _nx.Graph,
            "__class_getitem__",
            classmethod(lambda cls, item: cls),
        )
except ImportError:
    pass
