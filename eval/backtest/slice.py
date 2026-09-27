"""Cut a project graph view back to what was known at a cutoff year."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from portolan.graph.models import GraphView


def work_year(node: Mapping[str, Any]) -> int | None:
    """Return a work node's year from ``data`` or the top level, if it is an integer."""

    data = node.get("data")
    value = data.get("year") if isinstance(data, Mapping) else None
    if value is None:
        value = node.get("year")
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def slice_view(view: GraphView, cutoff_year: int) -> GraphView:
    """Return the part of ``view`` that existed at the end of ``cutoff_year``.

    Works with a year after the cutoff, or with no known year, are dropped.  The
    slice keeps the citation edges among the remaining works and the concept (and
    author) nodes and edges attached to them.  The ``in_degree``/``out_degree``
    fields in a work's ``data`` are recounted on the slice so that they do not
    reveal later citations.  ``cited_by_count`` is copied unchanged: it is the
    source's current total and cannot be cut back to a year (see the README).
    The input view is not modified.
    """

    kept_works: set[str] = set()
    for node in view.nodes:
        if not isinstance(node, Mapping) or node.get("kind") != "work":
            continue
        node_id = node.get("id")
        year = work_year(node)
        if node_id is not None and year is not None and year <= cutoff_year:
            kept_works.add(str(node_id))

    edges: list[dict[str, Any]] = []
    attached: set[str] = set()
    for edge in view.edges:
        if not isinstance(edge, Mapping):
            continue
        source, target = edge.get("source"), edge.get("target")
        if source is None or target is None:
            continue
        source, target = str(source), str(target)
        if edge.get("kind") == "cites":
            if source in kept_works and target in kept_works:
                edges.append(dict(edge))
        elif source in kept_works:
            # has_concept, authored_by and any other work-to-entity link.
            edges.append(dict(edge))
            attached.add(target)

    in_degree = dict.fromkeys(kept_works, 0)
    out_degree = dict.fromkeys(kept_works, 0)
    for edge in edges:
        if edge.get("kind") == "cites":
            out_degree[str(edge["source"])] += 1
            in_degree[str(edge["target"])] += 1

    nodes: list[dict[str, Any]] = []
    for node in view.nodes:
        if not isinstance(node, Mapping) or node.get("id") is None:
            continue
        node_id = str(node["id"])
        if node.get("kind") == "work":
            if node_id not in kept_works:
                continue
            copied = copy.deepcopy(dict(node))
            data = copied.get("data")
            if isinstance(data, dict):
                if "in_degree" in data:
                    data["in_degree"] = in_degree[node_id]
                if "out_degree" in data:
                    data["out_degree"] = out_degree[node_id]
            nodes.append(copied)
        elif node_id in attached:
            nodes.append(copy.deepcopy(dict(node)))
    return GraphView(nodes=nodes, edges=edges)


__all__ = ["slice_view", "work_year"]
