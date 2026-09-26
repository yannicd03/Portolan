"""Load the golden mini-graph into a repository.

The composer lives with its fixtures in ``eval/golden/compose_golden.py``; it is loaded
by path so the package does not depend on ``eval/`` being importable.  Until the M1
pipeline exists, the golden graph is the only content the API and frontend can show.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .store import GraphRepository


def _load_composer(repo_root: Path) -> Any:
    path = repo_root / "eval" / "golden" / "compose_golden.py"
    if not path.is_file():
        raise FileNotFoundError(f"golden composer not found at {path}")
    spec = importlib.util.spec_from_file_location("portolan_compose_golden", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load golden composer from {path}")
    module = importlib.util.module_from_spec(spec)
    # Registered before execution: the composer's dataclasses resolve their module by name.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def seed_golden(repository: GraphRepository, repo_root: Path) -> dict[str, Any]:
    """Compose the golden graph into ``repository`` and return its counts."""

    stats = _load_composer(repo_root).compose(repository, repo_root=repo_root)
    if is_dataclass(stats) and not isinstance(stats, type):
        return {key: value for key, value in asdict(stats).items() if isinstance(value, int)}
    return {}
