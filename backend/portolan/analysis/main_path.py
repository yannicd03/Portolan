"""Main path analysis for a project's citation graph."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import networkx as nx

from ..graph.models import GraphView


def _work_year(node: Mapping[str, Any]) -> int | None:
    """Read a work year from either the graph node data or its top-level fields."""

    data = node.get("data")
    value = data.get("year") if isinstance(data, Mapping) else node.get("year")
    if value is None and isinstance(data, Mapping):
        value = node.get("year")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _work_graph(view: GraphView) -> tuple[nx.DiGraph[str], dict[str, int | None]]:
    """Build the cited-to-citing flow graph used by main path analysis."""

    work_years: dict[str, int | None] = {}
    for node in view.nodes:
        if node.get("kind") != "work" or node.get("id") is None:
            continue
        work_id = str(node["id"])
        work_years[work_id] = _work_year(node)

    graph: nx.DiGraph[str] = nx.DiGraph()
    graph.add_nodes_from(sorted(work_years))

    citation_edges: list[tuple[str, str]] = []
    for edge in view.edges:
        if edge.get("kind") != "cites":
            continue
        citing = edge.get("source")
        cited = edge.get("target")
        if citing is None or cited is None:
            continue
        citing_id = str(citing)
        cited_id = str(cited)
        if citing_id not in work_years or cited_id not in work_years:
            continue
        # Graph edges are stored as citing -> cited; main path flow is the
        # knowledge direction, cited -> citing.
        cited_year = work_years[cited_id]
        citing_year = work_years[citing_id]
        if cited_year is not None and citing_year is not None and cited_year > citing_year:
            continue
        citation_edges.append((cited_id, citing_id))

    graph.add_edges_from(sorted(set(citation_edges)))
    return graph, work_years


def _remove_cycles(graph: nx.DiGraph[str]) -> None:
    """Make ``graph`` acyclic by removing the largest edge of each found cycle."""

    while True:
        try:
            cycle = nx.find_cycle(graph)
        except nx.NetworkXNoCycle:
            return

        cycle_edges = [(str(edge[0]), str(edge[1])) for edge in cycle]
        source, target = max(cycle_edges)
        graph.remove_edge(source, target)


def _largest_component(graph: nx.DiGraph[str]) -> nx.DiGraph[str] | None:
    """Return the deterministic largest weak component containing an edge."""

    components = [
        tuple(sorted(component))
        for component in nx.weakly_connected_components(graph)
        if len(component) >= 2
    ]
    if not components:
        return None

    # A graph can have equally sized components.  Pick the one with the
    # lexicographically smallest sorted ids so output remains reproducible.
    selected = min(components, key=lambda ids: (-len(ids), ids))
    return graph.subgraph(selected).copy()


def _spc_weights(graph: nx.DiGraph[str], topological: list[str]) -> dict[tuple[str, str], int]:
    """Calculate Search Path Count weights using arbitrary precision integers."""

    paths_from_source: dict[str, int] = {}
    for node in topological:
        predecessors = list(graph.predecessors(node))
        paths_from_source[node] = (
            1 if not predecessors else sum(paths_from_source[pred] for pred in predecessors)
        )

    paths_to_sink: dict[str, int] = {}
    for node in reversed(topological):
        successors = list(graph.successors(node))
        paths_to_sink[node] = (
            1 if not successors else sum(paths_to_sink[succ] for succ in successors)
        )

    return {
        (source, target): paths_from_source[source] * paths_to_sink[target]
        for source, target in graph.edges
    }


def _prefer(
    candidate: tuple[int, tuple[str, ...]], current: tuple[int, tuple[str, ...]] | None
) -> bool:
    """Return whether a candidate path is better under the specified tie rule."""

    if current is None:
        return True
    candidate_score, candidate_path = candidate
    current_score, current_path = current
    return candidate_score > current_score or (
        candidate_score == current_score and candidate_path < current_path
    )


def find_main_path(view: GraphView) -> dict[str, Any]:
    """Return the highest-scoring source-to-sink path in a citation graph.

    Citation edges in ``view`` point from citing work to cited work.  The path
    graph reverses them so a returned path follows older cited works toward the
    newer works that cite them.
    """

    graph, _ = _work_graph(view)
    _remove_cycles(graph)
    component = _largest_component(graph)
    if component is None:
        return {"work_ids": [], "edges": []}

    topological = list(nx.lexicographical_topological_sort(component))
    weights = _spc_weights(component, topological)

    best_to_node: dict[str, tuple[int, tuple[str, ...]]] = {}
    for node in topological:
        if component.in_degree(node) == 0:
            best_to_node[node] = (0, (node,))
        current = best_to_node.get(node)
        if current is None:
            continue
        score, path = current
        for successor in sorted(component.successors(node)):
            candidate = (
                score + weights[(node, successor)],
                path + (successor,),
            )
            if _prefer(candidate, best_to_node.get(successor)):
                best_to_node[successor] = candidate

    sink_paths = [
        best_to_node[node]
        for node in sorted(component)
        if component.out_degree(node) == 0 and node in best_to_node
    ]
    if not sink_paths:
        return {"work_ids": [], "edges": []}

    max_score = max(score for score, _ in sink_paths)
    path = min(path for score, path in sink_paths if score == max_score)
    work_ids = list(path)
    edges = [
        {
            "source": source,
            "target": target,
            "spc": int(weights[(source, target)]),
        }
        for source, target in zip(path, path[1:], strict=False)
    ]
    return {"work_ids": work_ids, "edges": edges}


__all__ = ["find_main_path"]
