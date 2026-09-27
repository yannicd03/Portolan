"""Frontier scores for the current edge of a project's literature graph.

The graph view intentionally contains only ordinary dictionaries.  This module
therefore keeps all of its extraction in a few small helpers so that it works
with both the in-memory graph view and enriched views supplied by API callers.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..api.models import ProjectAnalysis
from ..graph.models import GraphView

VELOCITY_WEIGHT = 0.30
LOCAL_UPTAKE_WEIGHT = 0.15
MAIN_PATH_LEAF_WEIGHT = 0.15
CLUSTER_GROWTH_WEIGHT = 0.15
NEW_CONCEPT_WEIGHT = 0.15
PREPRINT_WEIGHT = 0.10

FRONTIER_WEIGHTS = {
    "velocity": VELOCITY_WEIGHT,
    "local_uptake": LOCAL_UPTAKE_WEIGHT,
    "main_path_leaf": MAIN_PATH_LEAF_WEIGHT,
    "cluster_growth": CLUSTER_GROWTH_WEIGHT,
    "new_concept": NEW_CONCEPT_WEIGHT,
    "preprint": PREPRINT_WEIGHT,
}


@dataclass(frozen=True, slots=True)
class FrontierWork:
    """One scored work and the component values that produced its score."""

    work_id: str
    title: str
    year: int | None
    score: float
    components: dict[str, float]


def _get(value: object, key: str, default: Any = None) -> Any:
    """Read a field from a mapping or a small model-like object."""

    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _data(node: Mapping[str, Any]) -> Mapping[str, Any]:
    value = node.get("data")
    return value if isinstance(value, Mapping) else {}


def _node_field(node: Mapping[str, Any], name: str, default: Any = None) -> Any:
    """Read enriched fields from ``data`` while accepting hand-built top-level nodes."""

    data = _data(node)
    value = data.get(name)
    return node.get(name, default) if value is None else value


def _work_nodes(view: GraphView) -> dict[str, Mapping[str, Any]]:
    works: dict[str, Mapping[str, Any]] = {}
    for raw_node in view.nodes:
        if not isinstance(raw_node, Mapping) or raw_node.get("kind") != "work":
            continue
        work_id = raw_node.get("id")
        if work_id is None:
            continue
        works.setdefault(str(work_id), raw_node)
    return {work_id: works[work_id] for work_id in sorted(works)}


def _concept_labels(view: GraphView) -> dict[str, str]:
    labels: dict[str, str] = {}
    for raw_node in view.nodes:
        if not isinstance(raw_node, Mapping) or raw_node.get("kind") != "concept":
            continue
        concept_id = raw_node.get("id")
        if concept_id is None:
            continue
        concept_id = str(concept_id)
        label = raw_node.get("label")
        if label is None:
            label = _node_field(raw_node, "label", concept_id)
        labels.setdefault(concept_id, str(label))
    return labels


def _edges(view: GraphView, work_ids: set[str]) -> tuple[set[tuple[str, str]], dict[str, set[str]]]:
    citations: set[tuple[str, str]] = set()
    concepts: dict[str, set[str]] = defaultdict(set)
    for raw_edge in view.edges:
        if not isinstance(raw_edge, Mapping):
            continue
        source = raw_edge.get("source")
        target = raw_edge.get("target")
        if source is None or target is None:
            continue
        source, target = str(source), str(target)
        kind = raw_edge.get("kind")
        if kind == "cites" and source in work_ids and target in work_ids and source != target:
            citations.add((source, target))
        elif kind == "has_concept" and source in work_ids:
            concepts[source].add(target)
    return citations, concepts


def _work_years(work_nodes: Mapping[str, Mapping[str, Any]]) -> dict[str, int | None]:
    return {work_id: _as_int(_node_field(node, "year")) for work_id, node in work_nodes.items()}


def _window_start(now_year: int, window_years: int) -> int:
    # "Two years back from 2024" starts at 2022, including the boundary year.
    return now_year - window_years


def _percentile_ranks(values: Mapping[str, float]) -> dict[str, float]:
    """Return average-tie percentile ranks, with the maximum at one."""

    if not values:
        return {}
    if len(values) == 1:
        return {next(iter(values)): 1.0}

    ordered = sorted(values.values())
    ranks: dict[str, float] = {}
    denominator = len(ordered) - 1
    for work_id, value in values.items():
        lower = sum(candidate < value for candidate in ordered)
        equal = sum(candidate == value for candidate in ordered)
        rank = lower + (equal - 1) / 2
        ranks[work_id] = rank / denominator
    return ranks


def _analysis_cluster_members(
    analysis: ProjectAnalysis,
) -> tuple[dict[str, str | None], dict[str, set[str]]]:
    """Extract cluster membership and work sets from either Pydantic or plain records."""

    membership: dict[str, str | None] = {}
    cluster_members: dict[str, set[str]] = defaultdict(set)
    clusters = _get(analysis, "clusters", []) or []
    for cluster in clusters:
        cluster_id = _get(cluster, "id")
        if cluster_id is None:
            continue
        cluster_id = str(cluster_id)
        for raw_work_id in _get(cluster, "work_ids", []) or []:
            work_id = str(raw_work_id)
            membership[work_id] = cluster_id
            cluster_members[cluster_id].add(work_id)

    works = _get(analysis, "works", {}) or {}
    for raw_work_id, work_analysis in works.items():
        work_id = str(raw_work_id)
        cluster_id = _get(work_analysis, "cluster")
        if cluster_id is None:
            membership.setdefault(work_id, None)
            continue
        cluster_id = str(cluster_id)
        membership[work_id] = cluster_id
        cluster_members[cluster_id].add(work_id)
    return membership, cluster_members


def _is_preprint(node: Mapping[str, Any]) -> bool:
    work_type = str(_node_field(node, "work_type") or "").casefold()
    normalized_type = re.sub(r"[^a-z0-9]+", " ", work_type).strip()
    if any(
        marker in normalized_type
        for marker in ("preprint", "working paper", "workingpaper", "posted content")
    ):
        return True

    arxiv_id = str(_node_field(node, "arxiv_id") or "").strip()
    if not arxiv_id:
        return False

    venue = str(_node_field(node, "venue") or "").strip().casefold()
    # OpenAlex names the arXiv source "arXiv (Cornell University)".
    venue_is_arxiv = venue.startswith("arxiv")
    no_other_venue = not venue or venue_is_arxiv
    doi = str(_node_field(node, "doi") or "").strip().casefold()
    doi_without_prefix = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi)
    no_other_doi = not doi or doi_without_prefix.startswith("10.48550/")
    return no_other_venue and no_other_doi


def _frontier_context(
    view: GraphView, *, window_years: int, now_year: int | None
) -> tuple[
    dict[str, Mapping[str, Any]],
    dict[str, int | None],
    set[str],
    set[tuple[str, str]],
    dict[str, set[str]],
    dict[str, str],
    int | None,
]:
    works = _work_nodes(view)
    years = _work_years(works)
    effective_now = (
        now_year
        if now_year is not None
        else max((year for year in years.values() if year is not None), default=None)
    )
    if effective_now is None or window_years <= 0:
        return works, years, set(), set(), defaultdict(set), _concept_labels(view), effective_now
    start = _window_start(effective_now, window_years)
    windowed = {
        work_id
        for work_id, year in years.items()
        if year is not None and start <= year <= effective_now
    }
    citations, work_concepts = _edges(view, set(works))
    return works, years, windowed, citations, work_concepts, _concept_labels(view), effective_now


def frontier_scores(
    view: GraphView,
    analysis: ProjectAnalysis,
    *,
    window_years: int = 2,
    now_year: int | None = None,
) -> list[FrontierWork]:
    """Score recent works using independently inspectable structural components."""

    (
        works,
        years,
        windowed,
        citations,
        work_concepts,
        _concept_labels_by_id,
        effective_now,
    ) = _frontier_context(view, window_years=window_years, now_year=now_year)
    if effective_now is None or not windowed:
        return []

    incoming = {work_id: 0 for work_id in works}
    for _source, target in citations:
        if target in incoming:
            incoming[target] += 1

    velocity_values = {
        work_id: max(0.0, _as_float(_node_field(works[work_id], "cited_by_count")))
        / max(1, effective_now - (years[work_id] or effective_now) + 1)
        for work_id in windowed
    }
    velocity = _percentile_ranks(velocity_values)

    max_uptake = max((incoming[work_id] for work_id in windowed), default=0)
    local_uptake = {
        work_id: (incoming[work_id] / max_uptake if max_uptake else 0.0) for work_id in windowed
    }

    main_path = _get(analysis, "main_path")
    main_path_ids = [str(work_id) for work_id in _get(main_path, "work_ids", []) or []]
    main_path_set = set(main_path_ids)
    # Main-path edges run from the cited work to the citing one, so a node with
    # no outgoing main-path edge has no main-path successor.
    has_successor = {
        str(_get(edge, "source"))
        for edge in _get(main_path, "edges", []) or []
        if str(_get(edge, "target")) in main_path_set
    }
    main_path_leaves = {work_id for work_id in main_path_set if work_id not in has_successor}
    if main_path_ids:
        main_path_leaves.add(main_path_ids[-1])
    cites_main_path = {source for source, target in citations if target in main_path_set}
    main_path_leaf = {
        work_id: (
            1.0 if work_id in main_path_leaves else 0.5 if work_id in cites_main_path else 0.0
        )
        for work_id in windowed
    }

    membership, cluster_members = _analysis_cluster_members(analysis)
    project_share = len(windowed) / len(works) if works else 0.0
    cluster_growth: dict[str, float] = {}
    for work_id in windowed:
        cluster_id = membership.get(work_id)
        members = cluster_members.get(cluster_id, set()) if cluster_id is not None else set()
        members = members & set(works)
        if not members or project_share <= 0.0:
            cluster_growth[work_id] = 0.0
            continue
        cluster_share = len(members & windowed) / len(members)
        ratio = cluster_share / project_share
        cluster_growth[work_id] = min(2.0, max(0.0, ratio)) / 2.0

    concept_works: dict[str, set[str]] = defaultdict(set)
    for work_id, concept_ids in work_concepts.items():
        for concept_id in concept_ids:
            concept_works[concept_id].add(work_id)
    concept_first_year: dict[str, int] = {}
    for concept_id, concept_work_ids in concept_works.items():
        valid_years = [
            year for work_id in concept_work_ids if (year := years.get(work_id)) is not None
        ]
        if valid_years:
            concept_first_year[concept_id] = min(valid_years)
    start = _window_start(effective_now, window_years)
    new_concept_ids = {
        concept_id
        for concept_id, first_year in concept_first_year.items()
        if start <= first_year <= effective_now and len(concept_works[concept_id] & windowed) >= 2
    }
    new_concept = {
        work_id: float(bool(work_concepts.get(work_id, set()) & new_concept_ids))
        for work_id in windowed
    }

    preprint = {work_id: float(_is_preprint(works[work_id])) for work_id in windowed}
    component_map = {
        "velocity": velocity,
        "local_uptake": local_uptake,
        "main_path_leaf": main_path_leaf,
        "cluster_growth": cluster_growth,
        "new_concept": new_concept,
        "preprint": preprint,
    }

    result: list[FrontierWork] = []
    for work_id in sorted(windowed):
        components = {
            name: float(values.get(work_id, 0.0)) for name, values in component_map.items()
        }
        score = sum(FRONTIER_WEIGHTS[name] * value for name, value in components.items())
        node = works[work_id]
        title = node.get("label")
        if title is None:
            title = _node_field(node, "title", work_id)
        result.append(
            FrontierWork(
                work_id=work_id,
                title=str(title),
                year=years[work_id],
                score=float(score),
                components=components,
            )
        )
    result.sort(key=lambda item: (-item.score, item.work_id))
    return result


def frontier_concepts(
    view: GraphView,
    *,
    window_years: int = 2,
    now_year: int | None = None,
) -> list[dict[str, Any]]:
    """Return concepts first appearing in the frontier window and their adoption."""

    (
        works,
        years,
        _windowed,
        _citations,
        work_concepts,
        concept_labels,
        effective_now,
    ) = _frontier_context(view, window_years=window_years, now_year=now_year)
    if effective_now is None or window_years <= 0:
        return []

    concept_works: dict[str, set[str]] = defaultdict(set)
    for work_id, concept_ids in work_concepts.items():
        for concept_id in concept_ids:
            concept_works[concept_id].add(work_id)

    start = _window_start(effective_now, window_years)
    results: list[dict[str, Any]] = []
    for concept_id, work_ids in sorted(concept_works.items()):
        years_for_concept = [
            year for work_id in work_ids if (year := years.get(work_id)) is not None
        ]
        if not years_for_concept:
            continue
        first_year = min(years_for_concept)
        if not start <= first_year <= effective_now:
            continue
        adoption_by_year: dict[int, int] = defaultdict(int)
        for work_id in work_ids:
            year = years.get(work_id)
            if year is not None:
                adoption_by_year[year] += 1
        results.append(
            {
                "concept_id": concept_id,
                "label": concept_labels.get(concept_id, concept_id),
                "first_year": first_year,
                "adoption_by_year": dict(sorted(adoption_by_year.items())),
            }
        )
    results.sort(
        key=lambda item: (
            item["first_year"],
            str(item["label"]).casefold(),
            item["concept_id"],
        )
    )
    return results


__all__ = [
    "CLUSTER_GROWTH_WEIGHT",
    "FRONTIER_WEIGHTS",
    "FrontierWork",
    "LOCAL_UPTAKE_WEIGHT",
    "MAIN_PATH_LEAF_WEIGHT",
    "NEW_CONCEPT_WEIGHT",
    "PREPRINT_WEIGHT",
    "VELOCITY_WEIGHT",
    "frontier_concepts",
    "frontier_scores",
]
