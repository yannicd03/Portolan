"""Deterministic merging of source keyword occurrences into concept clusters."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from .normalize import acronym_of, normalize_keyword, slugify

DEFAULT_STOP_TERMS: frozenset[str] = frozenset(
    {
        "computer science",
        "artificial intelligence",
        "machine learning",
        "deep learning",
        "algorithm",
        "method",
        "model",
        "data",
        "paper",
        "study",
        "approach",
    }
)


@dataclass(frozen=True, slots=True)
class KeywordOccurrence:
    """One source keyword attached to a work."""

    work_id: str
    term: str
    score: float | None = None
    kind: str = "keyword"


@dataclass(frozen=True, slots=True)
class ConceptCluster:
    """A merged concept and the works in which it occurs."""

    id: str
    label: str
    aliases: tuple[str, ...]
    work_ids: tuple[str, ...]
    occurrences: int
    work_scores: Mapping[str, float]


class Embedder(Protocol):
    """Minimal interface required for optional semantic keyword merging."""

    def embed(self, texts: Sequence[str]) -> list[Sequence[float]]:
        """Return one embedding for each input text, in the same order."""


@dataclass(frozen=True, slots=True)
class MergeRecord:
    """One deterministic merge decision in a :class:`MergeReport`.

    ``groups`` contains the normalized group names participating in the decision.  For an
    exact normalization merge there is one normalized group, because the surface variants
    have already collapsed to that name.  For acronym and embedding merges it contains all
    normalized names brought together by that decision.
    """

    groups: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class MergeReport:
    """Audit trail for the merge decisions made by :func:`merge_keywords`."""

    merges: tuple[MergeRecord, ...]

    @property
    def reasons(self) -> tuple[str, ...]:
        """Return the reasons in the same order as the merge events."""

        return tuple(event.reason for event in self.merges)


@dataclass(frozen=True, slots=True)
class _PreparedOccurrence:
    work_id: str
    surface: str
    normalized: str
    score: float | None


@dataclass
class _Group:
    terms: set[str]
    occurrences: list[_PreparedOccurrence]


@dataclass(frozen=True, slots=True)
class _EmbeddingCluster:
    groups: tuple[_Group, ...]
    vectors: tuple[tuple[float, ...], ...]


def _surface_order(surface: str) -> tuple[str, str]:
    """Return a deterministic, case-insensitive alphabetical key."""

    return surface.casefold(), surface


def _group_key(group: _Group) -> tuple[str, ...]:
    return tuple(sorted(group.terms))


def _cluster_terms(cluster: _EmbeddingCluster) -> tuple[str, ...]:
    return tuple(sorted(term for group in cluster.groups for term in group.terms))


def _cluster_key(cluster: _EmbeddingCluster) -> tuple[str, ...]:
    return _cluster_terms(cluster)


def _label_for(group: _Group) -> str:
    counts = Counter(occurrence.surface for occurrence in group.occurrences)
    return min(
        counts,
        key=lambda surface: (-counts[surface], len(surface), *_surface_order(surface)),
    )


def _merge_groups(left: _Group, right: _Group) -> _Group:
    return _Group(
        terms=set(left.terms).union(right.terms),
        occurrences=[*left.occurrences, *right.occurrences],
    )


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """Compute cosine similarity without requiring a numerical dependency."""

    if len(left) != len(right) or len(left) == 0:
        return 0.0
    try:
        left_values = tuple(float(value) for value in left)
        right_values = tuple(float(value) for value in right)
    except (TypeError, ValueError):
        return 0.0
    if not all(math.isfinite(value) for value in (*left_values, *right_values)):
        return 0.0

    left_norm = math.sqrt(sum(value * value for value in left_values))
    right_norm = math.sqrt(sum(value * value for value in right_values))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return sum(a * b for a, b in zip(left_values, right_values, strict=True)) / (
        left_norm * right_norm
    )


def _average_linkage(left: _EmbeddingCluster, right: _EmbeddingCluster) -> float:
    similarities = [
        _cosine(left_vector, right_vector)
        for left_vector in left.vectors
        for right_vector in right.vectors
    ]
    return sum(similarities) / len(similarities)


def _embed_groups(groups: Sequence[_Group], embedder: Embedder) -> list[tuple[float, ...]]:
    ordered_groups = sorted(groups, key=_group_key)
    labels = [_label_for(group) for group in ordered_groups]
    raw_embeddings = list(embedder.embed(labels))
    if len(raw_embeddings) != len(labels):
        raise ValueError(
            "embedder returned a different number of embeddings "
            f"({len(raw_embeddings)}) than labels ({len(labels)})"
        )
    try:
        return [tuple(float(value) for value in embedding) for embedding in raw_embeddings]
    except (TypeError, ValueError) as exc:
        raise ValueError("embedder returned a non-numeric embedding") from exc


def _merge_by_embedding(
    groups: Sequence[_Group],
    embedder: Embedder,
    similarity_threshold: float,
    records: list[MergeRecord],
) -> list[_Group]:
    ordered_groups = sorted(groups, key=_group_key)
    embeddings = _embed_groups(ordered_groups, embedder)
    clusters = [
        _EmbeddingCluster(groups=(group,), vectors=(embedding,))
        for group, embedding in zip(ordered_groups, embeddings, strict=True)
    ]

    while len(clusters) > 1:
        clusters.sort(key=_cluster_key)
        best: tuple[float, int, int] | None = None
        for left_index, left in enumerate(clusters[:-1]):
            for right_index in range(left_index + 1, len(clusters)):
                right = clusters[right_index]
                similarity = _average_linkage(left, right)
                if similarity < similarity_threshold:
                    continue
                candidate = (similarity, left_index, right_index)
                if (
                    best is None
                    or similarity > best[0]
                    or (similarity == best[0] and (left_index, right_index) < (best[1], best[2]))
                ):
                    best = candidate
        if best is None:
            break

        similarity, left_index, right_index = best
        left = clusters[left_index]
        right = clusters[right_index]
        records.append(
            MergeRecord(
                groups=tuple(sorted((*_cluster_terms(left), *_cluster_terms(right)))),
                reason=f"embedding:{similarity:.2f}",
            )
        )
        merged = _EmbeddingCluster(
            groups=tuple(sorted((*left.groups, *right.groups), key=_group_key)),
            vectors=(*left.vectors, *right.vectors),
        )
        clusters = [
            cluster
            for index, cluster in enumerate(clusters)
            if index not in (left_index, right_index)
        ]
        clusters.append(merged)

    return [_merge_groups_in_cluster(cluster) for cluster in sorted(clusters, key=_cluster_key)]


def _merge_groups_in_cluster(cluster: _EmbeddingCluster) -> _Group:
    merged = _Group(terms=set(), occurrences=[])
    for group in cluster.groups:
        merged = _merge_groups(merged, group)
    return merged


def _normalized_stop_terms(stop_terms: Collection[str]) -> frozenset[str]:
    return frozenset(normalized for term in stop_terms if (normalized := normalize_keyword(term)))


def _prepare_groups(
    occurrences: Iterable[KeywordOccurrence], stop_terms: Collection[str]
) -> tuple[dict[str, _Group], list[MergeRecord]]:
    normalized_stops = _normalized_stop_terms(stop_terms)
    groups: dict[str, _Group] = {}
    for occurrence in occurrences:
        normalized = normalize_keyword(occurrence.term)
        if not normalized or normalized in normalized_stops:
            continue
        prepared = _PreparedOccurrence(
            work_id=occurrence.work_id,
            surface=occurrence.term,
            normalized=normalized,
            score=occurrence.score,
        )
        group = groups.setdefault(normalized, _Group(terms={normalized}, occurrences=[]))
        group.occurrences.append(prepared)

    records = [
        MergeRecord(groups=(normalized,), reason="exact")
        for normalized, group in sorted(groups.items())
        if len({occurrence.surface for occurrence in group.occurrences}) > 1
    ]
    return groups, records


def _link_acronyms(groups: dict[str, _Group], records: list[MergeRecord]) -> dict[str, _Group]:
    multiword = sorted(normalized for normalized in groups if " " in normalized)
    acronym_targets: dict[str, list[str]] = {}
    for normalized in multiword:
        acronym = acronym_of(normalized)
        if acronym:
            acronym_targets.setdefault(acronym, []).append(normalized)

    for acronym in sorted(acronym_targets):
        if acronym not in groups or len(acronym_targets[acronym]) != 1:
            continue
        target_name = acronym_targets[acronym][0]
        source = groups.pop(acronym)
        target = groups[target_name]
        merged = _merge_groups(target, source)
        groups[target_name] = merged
        records.append(
            MergeRecord(
                groups=tuple(sorted((acronym, target_name))),
                reason="acronym",
            )
        )
    return groups


def _work_scores(group: _Group) -> dict[str, float]:
    scores: dict[str, float] = {}
    for occurrence in group.occurrences:
        value = 1.0 if occurrence.score is None else float(occurrence.score)
        if math.isnan(value):
            value = 1.0
        previous = scores.get(occurrence.work_id)
        if previous is None or value > previous:
            scores[occurrence.work_id] = value
    return {work_id: scores[work_id] for work_id in sorted(scores)}


def _cluster_from_group(
    group: _Group, used_ids: dict[str, int], used_concept_ids: set[str]
) -> ConceptCluster:
    label = _label_for(group)
    normalized_label = normalize_keyword(label)
    base_id = slugify(normalized_label) or "concept"
    suffix = used_ids.get(base_id, 0) + 1
    concept_id = base_id if suffix == 1 else f"{base_id}-{suffix}"
    while concept_id in used_concept_ids:
        suffix += 1
        concept_id = f"{base_id}-{suffix}"
    used_ids[base_id] = suffix
    used_concept_ids.add(concept_id)

    surfaces = {occurrence.surface for occurrence in group.occurrences}
    aliases = sorted((surfaces | group.terms) - {label})
    scores = _work_scores(group)
    work_ids = tuple(scores)
    return ConceptCluster(
        id=concept_id,
        label=label,
        aliases=tuple(aliases),
        work_ids=work_ids,
        occurrences=len(group.occurrences),
        work_scores=scores,
    )


def merge_keywords_with_report(
    occurrences: Iterable[KeywordOccurrence],
    *,
    embedder: Embedder | None = None,
    similarity_threshold: float = 0.88,
    link_acronyms: bool = True,
    min_works: int = 1,
    stop_terms: Collection[str] = DEFAULT_STOP_TERMS,
) -> tuple[list[ConceptCluster], MergeReport]:
    """Merge keyword occurrences and return the resulting concepts plus an audit report."""

    groups, records = _prepare_groups(occurrences, stop_terms)
    if link_acronyms:
        groups = _link_acronyms(groups, records)

    group_values = list(groups.values())
    if embedder is not None and group_values:
        group_values = _merge_by_embedding(
            group_values,
            embedder,
            float(similarity_threshold),
            records,
        )

    eligible_groups = [
        group
        for group in group_values
        if len({occurrence.work_id for occurrence in group.occurrences}) >= min_works
    ]
    eligible_groups.sort(key=lambda group: (-len(_work_scores(group)), _label_for(group)))

    used_ids: dict[str, int] = {}
    used_concept_ids: set[str] = set()
    clusters = [_cluster_from_group(group, used_ids, used_concept_ids) for group in eligible_groups]
    return clusters, MergeReport(merges=tuple(records))


def merge_keywords(
    occurrences: Iterable[KeywordOccurrence],
    *,
    embedder: Embedder | None = None,
    similarity_threshold: float = 0.88,
    link_acronyms: bool = True,
    min_works: int = 1,
    stop_terms: Collection[str] = DEFAULT_STOP_TERMS,
) -> list[ConceptCluster]:
    """Merge keyword occurrences into deterministic concept clusters."""

    clusters, _ = merge_keywords_with_report(
        occurrences,
        embedder=embedder,
        similarity_threshold=similarity_threshold,
        link_acronyms=link_acronyms,
        min_works=min_works,
        stop_terms=stop_terms,
    )
    return clusters


def explain_merges(
    occurrences: Iterable[KeywordOccurrence],
    *,
    embedder: Embedder | None = None,
    similarity_threshold: float = 0.88,
    link_acronyms: bool = True,
    min_works: int = 1,
    stop_terms: Collection[str] = DEFAULT_STOP_TERMS,
) -> MergeReport:
    """Return only the audit report for a keyword merge operation."""

    _, report = merge_keywords_with_report(
        occurrences,
        embedder=embedder,
        similarity_threshold=similarity_threshold,
        link_acronyms=link_acronyms,
        min_works=min_works,
        stop_terms=stop_terms,
    )
    return report


__all__ = [
    "DEFAULT_STOP_TERMS",
    "ConceptCluster",
    "Embedder",
    "KeywordOccurrence",
    "MergeRecord",
    "MergeReport",
    "explain_merges",
    "merge_keywords",
    "merge_keywords_with_report",
]
