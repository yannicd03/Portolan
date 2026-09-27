"""Compose project analysis and retain a small cache for repeated map reads."""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from datetime import UTC, datetime

from ..api.models import ProjectAnalysis
from ..graph.base import ResearchGraph
from ..graph.models import GraphView
from .centrality import analyze_centrality
from .clusters import cluster_works
from .main_path import find_main_path

_CACHE_LIMIT = 32
_CACHE_LOCK = threading.RLock()
_CACHE: OrderedDict[tuple[str, str], ProjectAnalysis] = OrderedDict()


def _cache_key(project_id: str, view: GraphView) -> tuple[str, str]:
    # Hash every work and edge, so any change that can move a result (a new citation,
    # a re-assigned concept) invalidates the entry, not only changes in the counts.
    works = sorted(str(node["id"]) for node in view.nodes if node.get("kind") == "work")
    edges = sorted(
        f"{edge.get('kind')}|{edge.get('source')}|{edge.get('target')}" for edge in view.edges
    )
    digest = hashlib.sha256("\0".join([*works, "--", *edges]).encode()).hexdigest()
    return project_id, digest


def analyze_project(graph: ResearchGraph, project_id: str) -> ProjectAnalysis:
    """Analyze works and local citations using either graph store's common view."""

    view = graph.project_graph(project_id, include_authors=False, include_concepts=True)
    key = _cache_key(project_id, view)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
            return cached

        clusters, memberships = cluster_works(view)
        analysis = ProjectAnalysis(
            project_id=project_id,
            computed_at=datetime.now(UTC),
            clusters=clusters,
            works=analyze_centrality(view, memberships),
            main_path=find_main_path(view),
        )
        _CACHE[key] = analysis
        if len(_CACHE) > _CACHE_LIMIT:
            _CACHE.popitem(last=False)
        return analysis
