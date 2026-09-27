"""Deterministic community detection for a project's work graph."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import networkx as nx

from portolan.graph.models import GraphView

_LOUVAIN_SEED = 42
_MIN_CLUSTER_SIZE = 3
_CONCEPT_OVERLAP_THRESHOLD = 0.2
_CONCEPT_WEIGHT = 0.5
_BIBLIOGRAPHIC_COUPLING_WEIGHT = 0.5
_BIBLIOGRAPHIC_COUPLING_CAP = 2.0
_FALLBACK_TITLE_LENGTH = 60


def _node_data(node: Mapping[str, Any]) -> Mapping[str, Any]:
    data = node.get("data")
    return data if isinstance(data, Mapping) else {}


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _work_nodes(view: GraphView) -> dict[str, Mapping[str, Any]]:
    """Return work nodes keyed by id, in deterministic input-independent order."""

    works: dict[str, Mapping[str, Any]] = {}
    for node in view.nodes:
        if not isinstance(node, Mapping) or node.get("kind") != "work":
            continue
        work_id = node.get("id")
        if work_id is None:
            continue
        work_id = str(work_id)
        # GraphView is expected to contain one node per id.  Keeping the first
        # record makes malformed input deterministic while preserving the view's
        # ordinary semantics.
        works.setdefault(work_id, node)
    return {work_id: works[work_id] for work_id in sorted(works)}


def _concept_labels(view: GraphView) -> dict[str, str]:
    labels: dict[str, str] = {}
    for node in view.nodes:
        if not isinstance(node, Mapping) or node.get("kind") != "concept":
            continue
        concept_id = node.get("id")
        if concept_id is None:
            continue
        concept_id = str(concept_id)
        label = node.get("label")
        labels.setdefault(concept_id, str(label) if label is not None else concept_id)
    return labels


def _edges(
    view: GraphView, work_ids: set[str]
) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """Extract project citation edges and work-to-concept edges."""

    citations: set[tuple[str, str]] = set()
    concept_edges: set[tuple[str, str]] = set()
    for edge in view.edges:
        if not isinstance(edge, Mapping):
            continue
        source = edge.get("source")
        target = edge.get("target")
        kind = edge.get("kind")
        if source is None or target is None:
            continue
        source, target = str(source), str(target)
        if kind == "cites" and source in work_ids and target in work_ids and source != target:
            citations.add((source, target))
        elif kind == "has_concept" and source in work_ids:
            concept_edges.add((source, target))
    return citations, concept_edges


def _pair_key(first: str, second: str) -> tuple[str, str]:
    return (first, second) if first < second else (second, first)


def _concept_scores(
    cluster_work_ids: list[str],
    work_concepts: Mapping[str, set[str]],
    concept_labels: Mapping[str, str],
    project_size: int,
) -> list[tuple[float, str, str]]:
    """Return ``(score, label, concept_id)`` rows ordered by distinctiveness."""

    if not cluster_work_ids or project_size <= 0:
        return []
    cluster_size = len(cluster_work_ids)
    cluster_concepts = set().union(*(work_concepts[work_id] for work_id in cluster_work_ids))
    rows: list[tuple[float, str, str]] = []
    for concept_id in cluster_concepts:
        cluster_count = sum(concept_id in work_concepts[work_id] for work_id in cluster_work_ids)
        project_count = sum(concept_id in concepts for concepts in work_concepts.values())
        if project_count == 0:
            continue
        score = (cluster_count / cluster_size) * math.log(1.0 + project_size / project_count)
        label = concept_labels.get(concept_id, concept_id)
        rows.append((score, label, concept_id))
    rows.sort(key=lambda row: (-row[0], row[1].casefold(), row[1], row[2]))
    return rows


def _fallback_label(cluster_work_ids: list[str], works: Mapping[str, Mapping[str, Any]]) -> str:
    def key(work_id: str) -> tuple[float, str, str]:
        node = works[work_id]
        data = _node_data(node)
        title = str(node.get("label") or work_id)
        # Highest cited-by count wins.  Titles and ids provide stable tie breaks.
        citation_count = data.get("cited_by_count")
        if citation_count is None:
            citation_count = data.get("in_degree")
        return (-_as_float(citation_count), title.casefold(), work_id)

    title = min(cluster_work_ids, key=key)
    return str(works[title].get("label") or title)[:_FALLBACK_TITLE_LENGTH]


def cluster_works(view: GraphView) -> tuple[list[dict[str, Any]], dict[str, str | None]]:
    """Find deterministic Louvain communities and assign cluster membership.

    The returned membership map contains every work node in ``view``.  Communities
    smaller than three works remain unclustered, as required by the analysis
    contract.
    """

    works = _work_nodes(view)
    work_ids = set(works)
    membership: dict[str, str | None] = {work_id: None for work_id in sorted(work_ids)}
    if not works:
        return [], membership

    concept_labels = _concept_labels(view)
    citations, concept_edges = _edges(view, work_ids)
    work_concepts: dict[str, set[str]] = {work_id: set() for work_id in sorted(work_ids)}
    for work_id, concept_id in sorted(concept_edges):
        work_concepts[work_id].add(concept_id)

    references: dict[str, set[str]] = {work_id: set() for work_id in sorted(work_ids)}
    for citing, cited in sorted(citations):
        references[citing].add(cited)

    pair_weights: dict[tuple[str, str], float] = {}

    def add_weight(first: str, second: str, weight: float) -> None:
        if first == second or weight <= 0.0:
            return
        pair = _pair_key(first, second)
        pair_weights[pair] = pair_weights.get(pair, 0.0) + weight

    # A citation in either direction contributes one unit to the undirected graph.
    for first, second in sorted({_pair_key(citing, cited) for citing, cited in citations}):
        add_weight(first, second, 1.0)

    # Shared in-project references form bibliographic coupling.  The cap applies
    # to this component for each work pair before other components are added.
    for index, first in enumerate(sorted(work_ids)):
        for second in sorted(work_ids)[index + 1 :]:
            shared = len(references[first] & references[second])
            if shared:
                add_weight(
                    first,
                    second,
                    min(_BIBLIOGRAPHIC_COUPLING_CAP, _BIBLIOGRAPHIC_COUPLING_WEIGHT * shared),
                )

    # Concept Jaccard overlap also links works that have no direct citation.
    sorted_work_ids = sorted(work_ids)
    for index, first in enumerate(sorted_work_ids):
        first_concepts = work_concepts[first]
        for second in sorted_work_ids[index + 1 :]:
            second_concepts = work_concepts[second]
            union_size = len(first_concepts | second_concepts)
            if union_size == 0:
                continue
            jaccard = len(first_concepts & second_concepts) / union_size
            if jaccard >= _CONCEPT_OVERLAP_THRESHOLD:
                add_weight(first, second, _CONCEPT_WEIGHT * jaccard)

    graph = nx.Graph()
    graph.add_nodes_from(sorted_work_ids)
    graph.add_edges_from(
        (first, second, {"weight": pair_weights[(first, second)]})
        for first, second in sorted(pair_weights)
    )
    communities = nx.community.louvain_communities(
        graph,
        weight="weight",
        resolution=1.0,
        seed=_LOUVAIN_SEED,
    )
    qualifying = [sorted(str(work_id) for work_id in community) for community in communities]
    qualifying = [community for community in qualifying if len(community) >= _MIN_CLUSTER_SIZE]
    qualifying.sort(key=lambda community: (-len(community), tuple(community)))

    clusters: list[dict[str, Any]] = []
    project_size = len(sorted_work_ids)
    for index, cluster_work_ids in enumerate(qualifying, start=1):
        cluster_id = f"c{index}"
        for work_id in cluster_work_ids:
            membership[work_id] = cluster_id
        scores = _concept_scores(
            cluster_work_ids,
            work_concepts,
            concept_labels,
            project_size,
        )
        top_concepts: list[str] = []
        seen_labels: set[str] = set()
        for _, concept_label, _ in scores:
            label_key = concept_label.casefold()
            if label_key not in seen_labels:
                top_concepts.append(concept_label)
                seen_labels.add(label_key)
            if len(top_concepts) == 5:
                break
        label = (
            " · ".join(top_concepts[:3])
            if top_concepts
            else _fallback_label(cluster_work_ids, works)
        )
        clusters.append(
            {
                "id": cluster_id,
                "label": label,
                "size": len(cluster_work_ids),
                "top_concepts": top_concepts,
                "work_ids": cluster_work_ids,
            }
        )
    return clusters, membership


__all__ = ["cluster_works"]
