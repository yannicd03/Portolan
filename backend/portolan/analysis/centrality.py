"""Centrality metrics and structural roles for a project's works."""

from __future__ import annotations

from collections.abc import Mapping
from math import ceil
from typing import Any

import networkx as nx

from ..graph.models import GraphView

# Role thresholds are deliberately kept explicit so the labels remain stable as
# the analysis implementation evolves.
TOP_FRACTION = 0.10
PAGERANK_ALPHA = 0.85
FOUNDATIONAL_MIN_LOCAL_IN = 3
HUB_MIN_LOCAL_OUT = 5
EMERGING_MAX_LOCAL_IN = 1
EMERGING_YEAR_LAG = 1


def _work_data(node: Mapping[str, Any]) -> Mapping[str, Any]:
    data = node.get("data")
    return data if isinstance(data, Mapping) else {}


def _work_year(node: Mapping[str, Any]) -> int | None:
    value = _work_data(node).get("year", node.get("year"))
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _top_ids(values: Mapping[str, float | int], work_ids: list[str]) -> set[str]:
    """Return the deterministic top-ten-percent set for one metric."""

    if not work_ids:
        return set()
    count = 1 if len(work_ids) < 10 else max(1, ceil(TOP_FRACTION * len(work_ids)))
    ordered = sorted(work_ids, key=lambda work_id: (-values[work_id], work_id))
    return set(ordered[:count])


def analyze_centrality(
    view: GraphView, memberships: dict[str, str | None]
) -> dict[str, dict[str, Any]]:
    """Calculate citation centrality and structural roles for every work.

    ``GraphView`` citation edges point from the citing work to the cited work.
    The resulting dictionary is ordered by work id and contains only ordinary
    Python values so it can be passed directly to a response model or JSON
    encoder.
    """

    work_nodes: dict[str, Mapping[str, Any]] = {}
    for node in view.nodes:
        if node.get("kind") != "work":
            continue
        work_id = node.get("id")
        if isinstance(work_id, str) and work_id:
            work_nodes[work_id] = node

    work_ids = sorted(work_nodes)
    if not work_ids:
        return {}

    citations = nx.DiGraph()
    citations.add_nodes_from(work_ids)
    for edge in sorted(
        view.edges,
        key=lambda item: (
            str(item.get("source", "")),
            str(item.get("target", "")),
            str(item.get("kind", "")),
        ),
    ):
        if edge.get("kind") != "cites":
            continue
        source = edge.get("source")
        target = edge.get("target")
        if (
            isinstance(source, str)
            and isinstance(target, str)
            and source in work_nodes
            and target in work_nodes
            and source != target
        ):
            citations.add_edge(source, target)

    pagerank = nx.pagerank(citations, alpha=PAGERANK_ALPHA)
    citation_undirected = citations.to_undirected()
    betweenness = nx.betweenness_centrality(citation_undirected, normalized=True)

    local_in = {work_id: int(citations.in_degree(work_id)) for work_id in work_ids}
    local_out = {work_id: int(citations.out_degree(work_id)) for work_id in work_ids}
    top_pagerank = _top_ids(pagerank, work_ids)
    top_betweenness = _top_ids(betweenness, work_ids)
    top_out = _top_ids(local_out, work_ids)

    years = {
        work_id: _work_year(work_nodes[work_id])
        for work_id in work_ids
        if _work_year(work_nodes[work_id]) is not None
    }
    max_year = max(years.values()) if years else None

    result: dict[str, dict[str, Any]] = {}
    for work_id in work_ids:
        incoming = set(citations.predecessors(work_id))
        outgoing = set(citations.successors(work_id))
        neighbour_clusters = {
            memberships.get(neighbour)
            for neighbour in incoming | outgoing
            if memberships.get(neighbour) is not None
        }

        roles: list[str] = []
        if work_id in top_pagerank and local_in[work_id] >= FOUNDATIONAL_MIN_LOCAL_IN:
            roles.append("foundational")
        if (
            work_id in top_betweenness
            and betweenness[work_id] > 0.0
            and len(neighbour_clusters) >= 2
        ):
            roles.append("bridge")
        if work_id in top_out and local_out[work_id] >= HUB_MIN_LOCAL_OUT:
            roles.append("hub")
        year = years.get(work_id)
        if (
            max_year is not None
            and year is not None
            and year >= max_year - EMERGING_YEAR_LAG
            and local_in[work_id] <= EMERGING_MAX_LOCAL_IN
            and local_out[work_id] >= 2
        ):
            roles.append("emerging")
        if local_in[work_id] + local_out[work_id] == 0:
            roles.append("peripheral")

        result[work_id] = {
            "cluster": memberships.get(work_id),
            "pagerank": float(pagerank[work_id]),
            "betweenness": float(betweenness[work_id]),
            "local_in": local_in[work_id],
            "local_out": local_out[work_id],
            "roles": roles,
        }

    return result


__all__ = ["analyze_centrality"]
