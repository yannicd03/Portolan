"""Structural gap hypotheses for a project's work graph.

The gap detector deliberately works from :class:`~portolan.graph.models.GraphView`
and the output of :func:`portolan.analysis.service.analyze_project`.  It does not
read paper text; the hypotheses here are the graph-level candidates that later
analysis agents can investigate.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..graph.models import GraphView

# The thresholds are named so callers and future analysis work can refer to the
# structural definitions without repeating magic numbers.
BRIDGING_SIMILARITY_THRESHOLD = 0.30
BRIDGING_EXPECTED_CITATION_RATIO = 0.25
# Each Adamic–Adar term is 1 / log(degree) of a shared neighbour, so 0.5 needs
# one low-degree neighbour (degree <= 7) or several better-connected ones.
MATRIX_VOID_SCORE_THRESHOLD = 0.50
MATRIX_VOID_MAX_RESULTS = 10
# Matrix-void endpoints must be specific: a concept on more than this share of the
# project's works (e.g. "decoding" in a speculative-decoding project) is part of the
# project's framing rather than a region that could be void.
MATRIX_VOID_MAX_CONCEPT_SHARE = 0.40
MATRIX_VOID_MIN_CONCEPT_WORKS = 3
STAGNATION_MIN_CLUSTER_SIZE = 5
STAGNATION_EVIDENCE_WORKS = 5
STAGNATION_WINDOW_YEARS = 2
STAGNATION_PROJECT_SHARE_RATIO = 0.50
MAX_GAPS_PER_TYPE = 20

# A compatibility alias makes the threshold's Adamic–Adar meaning explicit to
# callers that want to tune or report it.
MATRIX_VOID_MIN_ADAMIC_ADAR = MATRIX_VOID_SCORE_THRESHOLD

GapType = Literal["bridging", "matrix_void", "stagnation"]
GapStatus = Literal["proposed", "accepted", "rejected"]


class GapHypothesis(BaseModel):
    """One deterministic, user-reviewable structural gap hypothesis."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    id: str
    type: GapType
    statement: str
    evidence: dict[str, list[str]]
    metrics: dict[str, float]
    confidence: float = Field(ge=0.0, le=1.0)
    status: GapStatus = "proposed"
    note: str | None = None
    verification: dict[str, Any] | None = None
    stale: bool = False

    # The key terms the verification search combines, chosen by the detector so
    # a later verification does not need to reconstruct the graph view.
    search_terms: list[str] = Field(default_factory=list)

    @field_validator("evidence", mode="before")
    @classmethod
    def normalize_evidence(cls, value: Any) -> dict[str, list[str]]:
        if not isinstance(value, Mapping):
            raise ValueError("evidence must be a mapping")
        normalized: dict[str, list[str]] = {}
        for key in ("work_ids", "concept_ids", "cluster_ids"):
            raw = value.get(key, [])
            if raw is None:
                raw = []
            if isinstance(raw, (str, bytes)) or not isinstance(raw, Iterable):
                raise ValueError(f"evidence.{key} must be a list")
            normalized[key] = sorted({str(item) for item in raw})
        return normalized

    @field_validator("search_terms", mode="before")
    @classmethod
    def normalize_string_lists(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (str, bytes)):
            return [str(value)]
        if not isinstance(value, Iterable):
            raise ValueError("value must be a list")
        return [str(item) for item in value]


def _field(value: Any, name: str, default: Any = None) -> Any:
    """Read a field from a mapping or a Pydantic/ordinary object."""

    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_year(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _node_data(node: Mapping[str, Any]) -> Mapping[str, Any]:
    data = node.get("data")
    return data if isinstance(data, Mapping) else {}


def _node_value(node: Mapping[str, Any], name: str, default: Any = None) -> Any:
    data = _node_data(node)
    value = data.get(name)
    return node.get(name, default) if value is None else value


def _node_id(node: Mapping[str, Any]) -> str | None:
    value = node.get("id")
    return None if value is None else str(value)


def _graph_parts(
    view: GraphView,
) -> tuple[
    dict[str, Mapping[str, Any]],
    dict[str, str],
    dict[str, set[str]],
    set[tuple[str, str]],
]:
    """Extract works, concept labels, concept links, and citation links."""

    works: dict[str, Mapping[str, Any]] = {}
    concept_labels: dict[str, str] = {}
    for raw_node in view.nodes:
        if not isinstance(raw_node, Mapping):
            continue
        kind = str(raw_node.get("kind", "")).casefold()
        node_id = _node_id(raw_node)
        if node_id is None:
            continue
        if kind == "work":
            works.setdefault(node_id, raw_node)
        elif kind == "concept":
            label = raw_node.get("label")
            if label is None:
                label = _node_value(raw_node, "label", node_id)
            concept_labels.setdefault(node_id, str(label))

    work_concepts: dict[str, set[str]] = {work_id: set() for work_id in works}
    citations: set[tuple[str, str]] = set()
    for raw_edge in view.edges:
        if not isinstance(raw_edge, Mapping):
            continue
        source = raw_edge.get("source")
        target = raw_edge.get("target")
        if source is None or target is None:
            continue
        source_id, target_id = str(source), str(target)
        kind = str(raw_edge.get("kind", "")).casefold()
        if kind == "cites" and source_id in works and target_id in works:
            if source_id != target_id:
                citations.add((source_id, target_id))
        elif kind == "has_concept" and source_id in works:
            work_concepts[source_id].add(target_id)

    # A hand-built GraphView may omit concept nodes while still providing
    # concept edges.  Falling back to the id keeps such views useful.
    for concept_id in set().union(*work_concepts.values()) if work_concepts else set():
        concept_labels.setdefault(concept_id, concept_id)
    return works, concept_labels, work_concepts, citations


def _analysis_clusters(
    analysis: Any,
    work_ids: Iterable[str],
) -> tuple[dict[str, list[str]], dict[str, str], dict[str, str | None], Mapping[str, Any]]:
    """Normalize analysis clusters and work membership into plain mappings."""

    clusters_by_id: dict[str, list[str]] = {}
    labels: dict[str, str] = {}
    membership: dict[str, str | None] = {work_id: None for work_id in work_ids}

    raw_clusters = _field(analysis, "clusters", []) or []
    for raw_cluster in raw_clusters:
        cluster_id = _field(raw_cluster, "id")
        if cluster_id is None:
            continue
        cluster_id = str(cluster_id)
        members = _field(raw_cluster, "work_ids", []) or []
        members = sorted({str(work_id) for work_id in members if str(work_id) in membership})
        clusters_by_id[cluster_id] = members
        label = _field(raw_cluster, "label", cluster_id)
        labels[cluster_id] = str(label) if label is not None else cluster_id
        for work_id in members:
            membership[work_id] = cluster_id

    raw_works = _field(analysis, "works", {}) or {}
    if not isinstance(raw_works, Mapping):
        raw_works = {}
    for work_id in sorted(membership):
        raw_work = raw_works.get(work_id)
        cluster_id = _field(raw_work, "cluster") if raw_work is not None else None
        if cluster_id is None:
            continue
        cluster_id = str(cluster_id)
        membership[work_id] = cluster_id
        clusters_by_id.setdefault(cluster_id, []).append(work_id)
        labels.setdefault(cluster_id, cluster_id)

    for cluster_id, members in list(clusters_by_id.items()):
        clusters_by_id[cluster_id] = sorted(set(members))
    return clusters_by_id, labels, membership, raw_works


def _work_rank(
    work_id: str,
    works: Mapping[str, Mapping[str, Any]],
    analysis_works: Mapping[str, Any],
) -> tuple[float, float, float, float, str, str]:
    """Sort works by available centrality, then stable graph properties."""

    centrality = analysis_works.get(work_id)
    return (
        -_as_float(_field(centrality, "pagerank"), 0.0),
        -_as_float(_field(centrality, "betweenness"), 0.0),
        -_as_float(_field(centrality, "local_in"), 0.0),
        -_as_float(_node_value(works[work_id], "cited_by_count"), 0.0),
        str(_node_value(works[work_id], "title", works[work_id].get("label", work_id))).casefold(),
        work_id,
    )


def _concept_frequency(
    members: Iterable[str], work_concepts: Mapping[str, set[str]]
) -> dict[str, int]:
    frequency: dict[str, int] = defaultdict(int)
    for work_id in members:
        for concept_id in work_concepts.get(work_id, set()):
            frequency[concept_id] += 1
    return dict(frequency)


def _cosine(first: Mapping[str, int], second: Mapping[str, int]) -> float:
    if not first or not second:
        return 0.0
    common = set(first) & set(second)
    numerator = sum(first[key] * second[key] for key in common)
    denominator = math.sqrt(sum(value * value for value in first.values())) * math.sqrt(
        sum(value * value for value in second.values())
    )
    return numerator / denominator if denominator else 0.0


def _gap_id(gap_type: str, evidence: Mapping[str, Iterable[str]]) -> str:
    """Return the stable identifier for a type and its sorted evidence ids."""

    evidence_ids = sorted(
        str(item)
        for key in ("work_ids", "concept_ids", "cluster_ids")
        for item in evidence.get(key, [])
    )
    material = json.dumps([gap_type, evidence_ids], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def gap_id(gap_type: str, evidence: Mapping[str, Iterable[str]]) -> str:
    """Public helper for callers that need to calculate a gap id."""

    return _gap_id(gap_type, evidence)


def _make_gap(
    *,
    gap_type: GapType,
    statement: str,
    evidence: Mapping[str, Iterable[str]],
    metrics: Mapping[str, float],
    confidence: float,
    search_terms: Iterable[str],
) -> GapHypothesis:
    normalized_evidence = {
        "work_ids": sorted({str(item) for item in evidence.get("work_ids", [])}),
        "concept_ids": sorted({str(item) for item in evidence.get("concept_ids", [])}),
        "cluster_ids": sorted({str(item) for item in evidence.get("cluster_ids", [])}),
    }
    return GapHypothesis(
        id=_gap_id(gap_type, normalized_evidence),
        type=gap_type,
        statement=statement,
        evidence=normalized_evidence,
        metrics={str(key): float(value) for key, value in metrics.items()},
        confidence=max(0.0, min(1.0, float(confidence))),
        search_terms=[str(term) for term in search_terms if str(term).strip()],
    )


def _top_concept(
    frequency: Mapping[str, int],
    candidates: Iterable[str],
    concept_labels: Mapping[str, str],
) -> str | None:
    ranked = sorted(
        candidates,
        key=lambda concept_id: (
            -frequency.get(concept_id, 0),
            concept_labels.get(concept_id, concept_id).casefold(),
            concept_id,
        ),
    )
    return ranked[0] if ranked else None


def _bridging_terms(
    first_frequency: Mapping[str, int],
    second_frequency: Mapping[str, int],
    concept_labels: Mapping[str, str],
) -> list[str]:
    """Return the top shared concept of A and the top (other) concept of B."""

    shared = set(first_frequency) & set(second_frequency)
    first_term = _top_concept(first_frequency, shared, concept_labels)
    if first_term is None:
        return []
    others = shared - {first_term} or set(second_frequency) - {first_term}
    second_term = _top_concept(second_frequency, others, concept_labels)
    terms = [first_term] if second_term is None else [first_term, second_term]
    return [concept_labels.get(concept_id, concept_id) for concept_id in terms]


def _detect_bridging(
    works: Mapping[str, Mapping[str, Any]],
    concept_labels: Mapping[str, str],
    work_concepts: Mapping[str, set[str]],
    citations: set[tuple[str, str]],
    clusters: Mapping[str, list[str]],
    cluster_labels: Mapping[str, str],
    membership: Mapping[str, str | None],
    analysis_works: Mapping[str, Any],
    project_work_ids: list[str],
) -> list[GapHypothesis]:
    cluster_ids = sorted(cluster_id for cluster_id, ids in clusters.items() if ids)
    if len(cluster_ids) < 2:
        return []
    project_size = len(project_work_ids)
    overall_density = (
        len(citations) / (project_size * (project_size - 1)) if project_size > 1 else 0.0
    )
    gaps: list[GapHypothesis] = []
    for index, first_id in enumerate(cluster_ids):
        first_members = clusters[first_id]
        first_frequency = _concept_frequency(first_members, work_concepts)
        for second_id in cluster_ids[index + 1 :]:
            second_members = clusters[second_id]
            second_frequency = _concept_frequency(second_members, work_concepts)
            similarity = _cosine(first_frequency, second_frequency)
            if similarity < BRIDGING_SIMILARITY_THRESHOLD:
                continue
            denominator = len(first_members) * len(second_members)
            if denominator <= 0:
                continue
            cross_links = sum(
                1
                for source, target in citations
                if (membership.get(source) == first_id and membership.get(target) == second_id)
                or (membership.get(source) == second_id and membership.get(target) == first_id)
            )
            cross_density = cross_links / denominator
            expected_cross = overall_density * denominator
            allowed_density = overall_density * BRIDGING_EXPECTED_CITATION_RATIO
            rare = overall_density <= 0.0 or cross_density <= allowed_density + 1e-12
            if not rare:
                continue

            shared = sorted(
                set(first_frequency) & set(second_frequency),
                key=lambda concept_id: (
                    -(first_frequency[concept_id] + second_frequency[concept_id]),
                    concept_labels.get(concept_id, concept_id).casefold(),
                    concept_id,
                ),
            )
            shared = shared[:3]
            central_first = min(
                first_members, key=lambda work_id: _work_rank(work_id, works, analysis_works)
            )
            central_second = min(
                second_members, key=lambda work_id: _work_rank(work_id, works, analysis_works)
            )
            first_label = cluster_labels.get(first_id, first_id)
            second_label = cluster_labels.get(second_id, second_id)
            shared_labels = ", ".join(concept_labels.get(item, item) for item in shared)
            statement = (
                f"Clusters “{first_label}” and “{second_label}” share concepts "
                f"({shared_labels}) but rarely cite each other ({cross_links} links)."
            )
            link_score = (
                1.0
                if overall_density <= 0.0
                else max(0.0, 1.0 - cross_density / max(allowed_density, 1e-12))
            )
            confidence = 0.5 * similarity + 0.5 * link_score
            gaps.append(
                _make_gap(
                    gap_type="bridging",
                    statement=statement,
                    evidence={
                        "work_ids": [central_first, central_second],
                        "concept_ids": shared,
                        "cluster_ids": [first_id, second_id],
                    },
                    metrics={
                        "concept_similarity": similarity,
                        "citation_density": cross_density,
                        "cross_links": float(cross_links),
                        "expected_cross_citations": expected_cross,
                        "overall_citation_density": overall_density,
                    },
                    confidence=confidence,
                    search_terms=_bridging_terms(first_frequency, second_frequency, concept_labels),
                )
            )
    return gaps


def _is_lowercase_word(term: str) -> bool:
    return len(term.split()) == 1 and not any(character.isupper() for character in term)


def _unspecific_concepts(view: GraphView) -> set[str]:
    """Return concept ids that are single-word, text-derived keyphrases.

    Concept nodes carry no provenance, so the source is read off the surface forms:
    text keyphrases are emitted casefolded (acronyms in uppercase), while source
    keywords keep their capitalisation ("Speedup").  A concept counts as a
    text-derived single word when its label and every alias are one lowercase word;
    acronyms ("GPU") and concepts also backed by a source keyword are not included.
    """

    unspecific: set[str] = set()
    for raw_node in view.nodes:
        if not isinstance(raw_node, Mapping):
            continue
        if str(raw_node.get("kind", "")).casefold() != "concept":
            continue
        node_id = _node_id(raw_node)
        label = raw_node.get("label")
        if node_id is None or not isinstance(label, str) or not label.strip():
            continue
        aliases = _node_value(raw_node, "aliases", []) or []
        surfaces = [label, *(str(alias) for alias in aliases if alias is not None)]
        if all(_is_lowercase_word(surface.strip()) for surface in surfaces):
            unspecific.add(node_id)
    return unspecific


def _detect_matrix_voids(
    works: Mapping[str, Mapping[str, Any]],
    concept_labels: Mapping[str, str],
    work_concepts: Mapping[str, set[str]],
    analysis_works: Mapping[str, Any],
    unspecific_concepts: set[str] | frozenset[str] = frozenset(),
) -> list[GapHypothesis]:
    works_by_concept: dict[str, set[str]] = defaultdict(set)
    for work_id, concepts in work_concepts.items():
        for concept_id in concepts:
            works_by_concept[concept_id].add(work_id)
    # Endpoints must be studied (at least three works) but specific: not on more
    # than MATRIX_VOID_MAX_CONCEPT_SHARE of the project, and not a bare text-derived
    # word such as "adaptive", which pairs with anything into a meaningless void.
    max_works = MATRIX_VOID_MAX_CONCEPT_SHARE * len(works)
    candidate_concepts = sorted(
        concept_id
        for concept_id, ids in works_by_concept.items()
        if MATRIX_VOID_MIN_CONCEPT_WORKS <= len(ids) <= max_works
        and concept_id not in unspecific_concepts
    )
    if len(candidate_concepts) < 2:
        return []

    # Candidate endpoints need three works each.  Their Adamic–Adar neighbours
    # may be less frequent, so retain every concept in the co-occurrence graph.
    neighbours: dict[str, set[str]] = {concept_id: set() for concept_id in works_by_concept}
    for work_concepts_for_work in work_concepts.values():
        present = sorted(set(work_concepts_for_work))
        for index, first in enumerate(present):
            for second in present[index + 1 :]:
                neighbours[first].add(second)
                neighbours[second].add(first)

    candidates: list[tuple[float, str, str, set[str]]] = []
    for index, first in enumerate(candidate_concepts):
        for second in candidate_concepts[index + 1 :]:
            # A matrix void is specifically a pair that has no direct work-level
            # co-occurrence, even when both concepts have related neighbours.
            if second in neighbours[first]:
                continue
            common = neighbours[first] & neighbours[second]
            score = sum(
                1.0 / math.log(len(neighbours[neighbour]))
                for neighbour in common
                if len(neighbours[neighbour]) > 1
            )
            if score <= MATRIX_VOID_SCORE_THRESHOLD:
                continue
            candidates.append((score, first, second, common))
    candidates.sort(
        key=lambda row: (
            -row[0],
            concept_labels.get(row[1], row[1]).casefold(),
            concept_labels.get(row[2], row[2]).casefold(),
            row[1],
            row[2],
        )
    )

    gaps: list[GapHypothesis] = []
    for score, first, second, common in candidates[:MATRIX_VOID_MAX_RESULTS]:
        first_works = sorted(
            works_by_concept[first], key=lambda work_id: _work_rank(work_id, works, analysis_works)
        )[:2]
        second_works = sorted(
            works_by_concept[second], key=lambda work_id: _work_rank(work_id, works, analysis_works)
        )[:2]
        # List neighbours by their Adamic–Adar contribution: low degree first.
        shared_neighbours = sorted(
            common,
            key=lambda concept_id: (
                len(neighbours[concept_id]),
                concept_labels.get(concept_id, concept_id).casefold(),
                concept_id,
            ),
        )[:3]
        first_label = concept_labels.get(first, first)
        second_label = concept_labels.get(second, second)
        neighbour_labels = ", ".join(
            concept_labels.get(concept_id, concept_id) for concept_id in shared_neighbours
        )
        evidence_work_ids = first_works + second_works
        statement = (
            f"“{first_label}” and “{second_label}” are each well studied here and share "
            f"neighbours ({neighbour_labels}) but no work combines them."
        )
        gaps.append(
            _make_gap(
                gap_type="matrix_void",
                statement=statement,
                evidence={
                    "work_ids": evidence_work_ids,
                    "concept_ids": [first, second],
                },
                metrics={
                    "adamic_adar": score,
                    "first_work_count": float(len(works_by_concept[first])),
                    "second_work_count": float(len(works_by_concept[second])),
                    "common_neighbour_count": float(len(common)),
                },
                confidence=min(1.0, score / (score + 1.0)),
                search_terms=[first_label, second_label],
            )
        )
    return gaps


def _detect_stagnation(
    works: Mapping[str, Mapping[str, Any]],
    concept_labels: Mapping[str, str],
    work_concepts: Mapping[str, set[str]],
    clusters: Mapping[str, list[str]],
    cluster_labels: Mapping[str, str],
    project_work_ids: list[str],
    project_newest_year: int | None,
    analysis_works: Mapping[str, Any],
) -> list[GapHypothesis]:
    if project_newest_year is None or not project_work_ids:
        return []
    window_start = project_newest_year - STAGNATION_WINDOW_YEARS + 1
    project_window_count = sum(
        (year := _as_year(_node_value(works[work_id], "year"))) is not None and year >= window_start
        for work_id in project_work_ids
    )
    project_share = project_window_count / len(project_work_ids)
    if project_share <= 0.0:
        return []

    gaps: list[GapHypothesis] = []
    for cluster_id in sorted(clusters):
        cluster_work_ids = clusters[cluster_id]
        if len(cluster_work_ids) < STAGNATION_MIN_CLUSTER_SIZE:
            continue
        years = [
            year
            for work_id in cluster_work_ids
            if (year := _as_year(_node_value(works[work_id], "year"))) is not None
        ]
        if not years:
            continue
        last_active_year = max(years)
        if last_active_year > project_newest_year - STAGNATION_WINDOW_YEARS:
            continue
        cluster_window_count = sum(
            (year := _as_year(_node_value(works[work_id], "year"))) is not None
            and year >= window_start
            for work_id in cluster_work_ids
        )
        cluster_share = cluster_window_count / len(cluster_work_ids)
        if cluster_share >= STAGNATION_PROJECT_SHARE_RATIO * project_share:
            continue
        deficit = 1.0 - cluster_share / max(STAGNATION_PROJECT_SHARE_RATIO * project_share, 1e-12)
        age_factor = min(
            1.0,
            (project_newest_year - last_active_year) / max(float(STAGNATION_WINDOW_YEARS * 2), 1.0),
        )
        confidence = 0.5 * max(0.0, min(1.0, deficit)) + 0.5 * age_factor
        frequency = _concept_frequency(cluster_work_ids, work_concepts)
        concept_ids = sorted(
            frequency,
            key=lambda concept_id: (
                -frequency[concept_id],
                concept_labels.get(concept_id, concept_id).casefold(),
                concept_id,
            ),
        )[:5]
        # The most central works stand in for the cluster; listing every member
        # would make the evidence (and so the gap id) as large as the cluster.
        evidence_work_ids = sorted(
            cluster_work_ids, key=lambda work_id: _work_rank(work_id, works, analysis_works)
        )[:STAGNATION_EVIDENCE_WORKS]
        label = cluster_labels.get(cluster_id, cluster_id)
        statement = (
            f"Cluster “{label}” has stagnated; its last active year was {last_active_year}. "
            f"Only {cluster_window_count} of {len(cluster_work_ids)} works are in the last "
            f"{STAGNATION_WINDOW_YEARS} years."
        )
        gaps.append(
            _make_gap(
                gap_type="stagnation",
                statement=statement,
                evidence={
                    "work_ids": evidence_work_ids,
                    "concept_ids": concept_ids,
                    "cluster_ids": [cluster_id],
                },
                metrics={
                    "cluster_share": cluster_share,
                    "project_share": project_share,
                    "last_active_year": float(last_active_year),
                    "cluster_size": float(len(cluster_work_ids)),
                },
                confidence=confidence,
                # Cluster labels join their terms with " · "; search on the terms.
                search_terms=[" ".join(part.strip() for part in label.split("·"))],
            )
        )
    return gaps


def detect_gaps(view: GraphView, analysis: Any) -> list[GapHypothesis]:
    """Detect deterministic structural gap hypotheses from one graph view."""

    works, concept_labels, work_concepts, citations = _graph_parts(view)
    project_work_ids = sorted(works)
    years = [
        year
        for work_id in project_work_ids
        if (year := _as_year(_node_value(works[work_id], "year"))) is not None
    ]
    project_newest_year = max(years) if years else None
    clusters, cluster_labels, membership, analysis_works = _analysis_clusters(
        analysis, project_work_ids
    )

    all_gaps = [
        *_detect_bridging(
            works,
            concept_labels,
            work_concepts,
            citations,
            clusters,
            cluster_labels,
            membership,
            analysis_works,
            project_work_ids,
        ),
        *_detect_matrix_voids(
            works, concept_labels, work_concepts, analysis_works, _unspecific_concepts(view)
        ),
        *_detect_stagnation(
            works,
            concept_labels,
            work_concepts,
            clusters,
            cluster_labels,
            project_work_ids,
            project_newest_year,
            analysis_works,
        ),
    ]
    by_type: dict[str, list[GapHypothesis]] = defaultdict(list)
    for gap in all_gaps:
        by_type[gap.type].append(gap)
    ordered: list[GapHypothesis] = []
    for gap_type in sorted(by_type):
        candidates = sorted(by_type[gap_type], key=lambda gap: (-gap.confidence, gap.id))
        ordered.extend(candidates[:MAX_GAPS_PER_TYPE])
    return ordered


__all__ = [
    "BRIDGING_EXPECTED_CITATION_RATIO",
    "BRIDGING_SIMILARITY_THRESHOLD",
    "GapHypothesis",
    "MAX_GAPS_PER_TYPE",
    "MATRIX_VOID_MAX_CONCEPT_SHARE",
    "MATRIX_VOID_MAX_RESULTS",
    "MATRIX_VOID_MIN_ADAMIC_ADAR",
    "MATRIX_VOID_MIN_CONCEPT_WORKS",
    "MATRIX_VOID_SCORE_THRESHOLD",
    "STAGNATION_EVIDENCE_WORKS",
    "STAGNATION_MIN_CLUSTER_SIZE",
    "STAGNATION_PROJECT_SHARE_RATIO",
    "STAGNATION_WINDOW_YEARS",
    "detect_gaps",
    "gap_id",
]
