"""Time-sliced backtest of the frontier scores and gap hypotheses.

The graph is cut back to a cutoff year ``T`` (:func:`slice_view`), analysed as
the application would analyse it, and the resulting frontier ranking and gap
hypotheses are checked against the works published in ``(T, T + horizon]``.
Everything here is pure: callers pass the full project view and get a plain,
JSON-serializable result.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from portolan.analysis.centrality import analyze_centrality
from portolan.analysis.clusters import cluster_works
from portolan.analysis.frontier import FrontierWork, frontier_scores
from portolan.analysis.gaps import STAGNATION_MIN_CLUSTER_SIZE, GapHypothesis, detect_gaps
from portolan.analysis.main_path import find_main_path
from portolan.api.models import ProjectAnalysis
from portolan.graph.models import GraphView

try:  # Works both as ``eval.backtest.backtest`` and next to the script.
    from .slice import slice_view, work_year
except ImportError:  # pragma: no cover - exercised by the documented command.
    from slice import slice_view, work_year  # type: ignore[no-redef]

DEFAULT_HORIZON_YEARS = 2
DEFAULT_KS = (5, 10)
DEFAULT_SEED = 13
DEFAULT_DRAWS = 20
# Hypotheses of these sizes are also what the detector itself would consider.
MATRIX_VOID_MIN_CONCEPT_WORKS = 3
STAGNATION_MAX_FUTURE_CITERS = 1


# ---------------------------------------------------------------------------
# Graph helpers


def _work_years(view: GraphView) -> dict[str, int | None]:
    return {
        str(node["id"]): work_year(node)
        for node in view.nodes
        if isinstance(node, Mapping) and node.get("kind") == "work" and node.get("id") is not None
    }


def _citations(view: GraphView) -> set[tuple[str, str]]:
    return {
        (str(edge["source"]), str(edge["target"]))
        for edge in view.edges
        if isinstance(edge, Mapping)
        and edge.get("kind") == "cites"
        and edge.get("source") is not None
        and edge.get("target") is not None
        and str(edge["source"]) != str(edge["target"])
    }


def _work_concepts(view: GraphView) -> dict[str, set[str]]:
    concepts: dict[str, set[str]] = defaultdict(set)
    for edge in view.edges:
        if (
            isinstance(edge, Mapping)
            and edge.get("kind") == "has_concept"
            and edge.get("source") is not None
            and edge.get("target") is not None
        ):
            concepts[str(edge["source"])].add(str(edge["target"]))
    return concepts


def future_work_ids(
    view: GraphView, cutoff_year: int, horizon_years: int = DEFAULT_HORIZON_YEARS
) -> set[str]:
    """Return the works published in ``(cutoff_year, cutoff_year + horizon_years]``."""

    return {
        work_id
        for work_id, year in _work_years(view).items()
        if year is not None and cutoff_year < year <= cutoff_year + horizon_years
    }


def future_references(view: GraphView, future_ids: set[str]) -> dict[str, set[str]]:
    """Map each future work to the set of works it cites."""

    references: dict[str, set[str]] = {work_id: set() for work_id in future_ids}
    for source, target in _citations(view):
        if source in future_ids:
            references[source].add(target)
    return references


def analyze_slice(view: GraphView, project_id: str = "backtest") -> ProjectAnalysis:
    """Analyse a view with the same composition as ``analyze_project`` (without caching)."""

    clusters, memberships = cluster_works(view)
    return ProjectAnalysis(
        project_id=project_id,
        computed_at=datetime.now(UTC),
        clusters=clusters,
        works=analyze_centrality(view, memberships),
        main_path=find_main_path(view),
    )


def _mask_citation_counts(view: GraphView) -> GraphView:
    """Drop ``cited_by_count`` so the velocity component cannot see the future."""

    nodes: list[dict[str, Any]] = []
    for node in view.nodes:
        copied = dict(node)
        if copied.get("kind") == "work":
            copied.pop("cited_by_count", None)
            data = copied.get("data")
            if isinstance(data, Mapping):
                copied["data"] = {
                    key: value for key, value in data.items() if key != "cited_by_count"
                }
        nodes.append(copied)
    return GraphView(nodes=nodes, edges=list(view.edges))


# ---------------------------------------------------------------------------
# Ranking statistics


def _average_ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position
        while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
            end += 1
        average = (position + end) / 2 + 1
        for index in order[position : end + 1]:
            ranks[index] = average
        position = end + 1
    return ranks


def spearman(first: Sequence[float], second: Sequence[float]) -> float | None:
    """Spearman rank correlation with average ranks for ties.

    Returns ``None`` when it is undefined: fewer than two points or a constant side.
    """

    if len(first) != len(second) or len(first) < 2:
        return None
    first_ranks = _average_ranks(first)
    second_ranks = _average_ranks(second)
    mean = (len(first) + 1) / 2
    numerator = sum((a - mean) * (b - mean) for a, b in zip(first_ranks, second_ranks, strict=True))
    first_spread = math.sqrt(sum((a - mean) ** 2 for a in first_ranks))
    second_spread = math.sqrt(sum((b - mean) ** 2 for b in second_ranks))
    if first_spread == 0.0 or second_spread == 0.0:
        return None
    return numerator / (first_spread * second_spread)


def relevant_works(uptake: Mapping[str, int]) -> set[str]:
    """Works whose future uptake is in the top quartile (ties included) and non-zero."""

    if not uptake:
        return set()
    ordered = sorted(uptake.values(), reverse=True)
    threshold = ordered[math.ceil(len(ordered) / 4) - 1]
    return {work_id for work_id, value in uptake.items() if value >= threshold and value > 0}


def precision_at(ranking: Sequence[str], relevant: set[str], k: int) -> float | None:
    """Share of the top ``min(k, len(ranking))`` ranked works that are relevant."""

    top = list(ranking[:k])
    if not top:
        return None
    return sum(work_id in relevant for work_id in top) / len(top)


# ---------------------------------------------------------------------------
# Frontier check


def evaluate_frontier(
    frontier: Sequence[FrontierWork],
    slice_: GraphView,
    full_view: GraphView,
    cutoff_year: int,
    *,
    horizon_years: int = DEFAULT_HORIZON_YEARS,
    ks: Iterable[int] = DEFAULT_KS,
    top_n: int = 10,
) -> dict[str, Any]:
    """Compare a frontier ranking on the slice with the future uptake of each work."""

    future_ids = future_work_ids(full_view, cutoff_year, horizon_years)
    references = future_references(full_view, future_ids)
    windowed = [item.work_id for item in frontier]
    uptake = {
        work_id: sum(work_id in cited for cited in references.values()) for work_id in windowed
    }
    relevant = relevant_works(uptake)

    local_in: dict[str, int] = dict.fromkeys(windowed, 0)
    for _source, target in _citations(slice_):
        if target in local_in:
            local_in[target] += 1
    baseline_ranking = sorted(windowed, key=lambda work_id: (-local_in[work_id], work_id))
    frontier_ranking = [item.work_id for item in frontier]
    scores = {item.work_id: item.score for item in frontier}

    ks = list(ks)
    return {
        "windowed_works": len(windowed),
        "future_works": len(future_ids),
        "relevant_works": len(relevant),
        # The precision a random ranking of the windowed works would expect.
        "base_rate": len(relevant) / len(windowed) if windowed else None,
        "precision": {f"@{k}": precision_at(frontier_ranking, relevant, k) for k in ks},
        "baseline_local_in_degree_precision": {
            f"@{k}": precision_at(baseline_ranking, relevant, k) for k in ks
        },
        "spearman": spearman(
            [scores[work_id] for work_id in windowed], [float(uptake[w]) for w in windowed]
        ),
        "baseline_local_in_degree_spearman": spearman(
            [float(local_in[w]) for w in windowed], [float(uptake[w]) for w in windowed]
        ),
        "top": [
            {
                "work_id": item.work_id,
                "title": item.title,
                "year": item.year,
                "score": round(item.score, 4),
                "future_uptake": uptake[item.work_id],
                "relevant": item.work_id in relevant,
            }
            for item in frontier[:top_n]
        ],
    }


# ---------------------------------------------------------------------------
# Gap check


def _cluster_members(analysis: Any) -> dict[str, set[str]]:
    members: dict[str, set[str]] = defaultdict(set)
    clusters = analysis.get("clusters", []) if isinstance(analysis, Mapping) else analysis.clusters
    for cluster in clusters or []:
        cluster_id = cluster.get("id") if isinstance(cluster, Mapping) else cluster.id
        work_ids = cluster.get("work_ids") if isinstance(cluster, Mapping) else cluster.work_ids
        if cluster_id is not None:
            members[str(cluster_id)].update(str(work_id) for work_id in work_ids or [])
    return dict(members)


def bridging_anticipated(
    first: str,
    second: str,
    cluster_members: Mapping[str, set[str]],
    references: Mapping[str, set[str]],
) -> bool:
    """True when some future work cites a work in each of the two clusters."""

    first_members = cluster_members.get(first, set())
    second_members = cluster_members.get(second, set())
    return any(cited & first_members and cited & second_members for cited in references.values())


def void_anticipated(
    first: str, second: str, future_ids: set[str], work_concepts: Mapping[str, set[str]]
) -> bool:
    """True when some future work carries both concepts."""

    return any(
        first in (concepts := work_concepts.get(work_id, set())) and second in concepts
        for work_id in future_ids
    )


def future_citers(
    cluster_id: str, cluster_members: Mapping[str, set[str]], references: Mapping[str, set[str]]
) -> int:
    """Number of future works citing at least one work of the cluster."""

    members = cluster_members.get(cluster_id, set())
    return sum(bool(cited & members) for cited in references.values())


def stagnation_confirmed(
    cluster_id: str, cluster_members: Mapping[str, set[str]], references: Mapping[str, set[str]]
) -> bool:
    return future_citers(cluster_id, cluster_members, references) <= STAGNATION_MAX_FUTURE_CITERS


def _random_rate(
    population: Sequence[Any],
    count: int,
    check: Any,
    rng: random.Random,
    draws: int,
) -> float | None:
    """Average hit rate of ``count`` random picks from ``population`` over ``draws``."""

    if count <= 0 or not population:
        return None
    size = min(count, len(population))
    rates = []
    for _ in range(draws):
        sample = rng.sample(list(population), size)
        rates.append(sum(bool(check(item)) for item in sample) / size)
    return sum(rates) / len(rates)


def evaluate_gaps(
    gaps: Sequence[GapHypothesis],
    cluster_members: Mapping[str, set[str]],
    slice_: GraphView,
    full_view: GraphView,
    cutoff_year: int,
    *,
    horizon_years: int = DEFAULT_HORIZON_YEARS,
    seed: int = DEFAULT_SEED,
    draws: int = DEFAULT_DRAWS,
) -> dict[str, Any]:
    """Check each gap hypothesis against the future works and a random baseline.

    Clusters come from the slice's analysis; concepts are per work from the full
    view, so a future work's concepts are known when it is checked.
    """

    future_ids = future_work_ids(full_view, cutoff_year, horizon_years)
    references = future_references(full_view, future_ids)
    concepts = _work_concepts(full_view)
    rng = random.Random(seed)

    def check_bridging(pair: tuple[str, str]) -> bool:
        return bridging_anticipated(pair[0], pair[1], cluster_members, references)

    def check_void(pair: tuple[str, str]) -> bool:
        return void_anticipated(pair[0], pair[1], future_ids, concepts)

    def check_stagnation(cluster_id: str) -> bool:
        return stagnation_confirmed(cluster_id, cluster_members, references)

    # Baseline populations mirror what each detector may propose.
    cluster_ids = sorted(cluster_id for cluster_id, ids in cluster_members.items() if ids)
    cluster_pairs = [
        (first, second)
        for index, first in enumerate(cluster_ids)
        for second in cluster_ids[index + 1 :]
    ]
    slice_concepts = _work_concepts(slice_)
    concept_works: dict[str, set[str]] = defaultdict(set)
    for work_id, work_concepts in slice_concepts.items():
        for concept_id in work_concepts:
            concept_works[concept_id].add(work_id)
    eligible_concepts = sorted(
        concept_id
        for concept_id, ids in concept_works.items()
        if len(ids) >= MATRIX_VOID_MIN_CONCEPT_WORKS
    )
    concept_pairs = [
        (first, second)
        for index, first in enumerate(eligible_concepts)
        for second in eligible_concepts[index + 1 :]
        if not concept_works[first] & concept_works[second]
    ]
    stagnation_population = [
        cluster_id
        for cluster_id in cluster_ids
        if len(cluster_members[cluster_id]) >= STAGNATION_MIN_CLUSTER_SIZE
    ]

    per_type: dict[str, dict[str, Any]] = {}
    hypotheses: list[dict[str, Any]] = []
    specs = (
        ("bridging", "cluster_ids", check_bridging, cluster_pairs),
        ("matrix_void", "concept_ids", check_void, concept_pairs),
        ("stagnation", "cluster_ids", check_stagnation, stagnation_population),
    )
    for gap_type, evidence_key, check, population in specs:
        typed = [gap for gap in gaps if gap.type == gap_type]
        hits = 0
        for gap in typed:
            ids = gap.evidence.get(evidence_key, [])
            if gap_type == "stagnation":
                hit = bool(ids) and check(ids[0])
            else:
                hit = len(ids) >= 2 and check((ids[0], ids[1]))
            hits += hit
            hypotheses.append(
                {
                    "id": gap.id,
                    "type": gap_type,
                    "statement": gap.statement,
                    "confidence": round(gap.confidence, 4),
                    "hit": hit,
                }
            )
        per_type[gap_type] = {
            "hypotheses": len(typed),
            "hits": hits,
            "rate": hits / len(typed) if typed else None,
            "baseline_population": len(population),
            "baseline_rate": _random_rate(population, len(typed), check, rng, draws),
        }
    return {
        "future_works": len(future_ids),
        "seed": seed,
        "draws": draws,
        "by_type": per_type,
        "hypotheses": hypotheses,
    }


# ---------------------------------------------------------------------------
# Composition


def default_cutoff(view: GraphView, horizon_years: int = DEFAULT_HORIZON_YEARS) -> int | None:
    """Newest known work year minus the horizon, or ``None`` for an undated project."""

    years = [year for year in _work_years(view).values() if year is not None]
    return max(years) - horizon_years if years else None


def backtest(
    view: GraphView,
    cutoff_year: int | None = None,
    *,
    project_id: str = "backtest",
    horizon_years: int = DEFAULT_HORIZON_YEARS,
    window_years: int = 2,
    ks: Iterable[int] = DEFAULT_KS,
    seed: int = DEFAULT_SEED,
    draws: int = DEFAULT_DRAWS,
) -> dict[str, Any]:
    """Run the full frontier and gap backtest of one project view."""

    ks = list(ks)
    if cutoff_year is None:
        cutoff_year = default_cutoff(view, horizon_years)
    all_years = [year for year in _work_years(view).values() if year is not None]
    result: dict[str, Any] = {
        "project_id": project_id,
        "cutoff_year": cutoff_year,
        "horizon_years": horizon_years,
        "frontier_window_years": window_years,
        "works_total": len(_work_years(view)),
        "works_undated": sum(year is None for year in _work_years(view).values()),
        "year_range": [min(all_years), max(all_years)] if all_years else None,
    }
    if cutoff_year is None:
        result.update(slice_works=0, frontier=None, frontier_leak_free=None, gaps=None)
        result["note"] = "no dated works"
        return result

    slice_ = slice_view(view, cutoff_year)
    result["slice_works"] = len(_work_years(slice_))
    result["slice_citations"] = len(_citations(slice_))
    analysis = analyze_slice(slice_, project_id)
    result["slice_clusters"] = len(analysis.clusters)

    frontier = frontier_scores(slice_, analysis, window_years=window_years, now_year=cutoff_year)
    result["frontier"] = evaluate_frontier(
        frontier, slice_, view, cutoff_year, horizon_years=horizon_years, ks=ks
    )
    # cited_by_count is today's total; re-rank without it to show how much the
    # velocity component's look-ahead contributes.
    leak_free = frontier_scores(
        _mask_citation_counts(slice_), analysis, window_years=window_years, now_year=cutoff_year
    )
    result["frontier_leak_free"] = evaluate_frontier(
        leak_free, slice_, view, cutoff_year, horizon_years=horizon_years, ks=ks
    )

    gaps = detect_gaps(slice_, analysis)
    result["gaps"] = evaluate_gaps(
        gaps,
        _cluster_members(analysis),
        slice_,
        view,
        cutoff_year,
        horizon_years=horizon_years,
        seed=seed,
        draws=draws,
    )
    return result


__all__ = [
    "analyze_slice",
    "backtest",
    "bridging_anticipated",
    "default_cutoff",
    "evaluate_frontier",
    "evaluate_gaps",
    "future_citers",
    "future_references",
    "future_work_ids",
    "precision_at",
    "relevant_works",
    "spearman",
    "stagnation_confirmed",
    "void_anticipated",
]
