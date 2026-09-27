"""Deterministic community detection for a project's work graph."""

from __future__ import annotations

import math
import re
import unicodedata
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
_CONCEPT_MIN_SUPPORT = 2
_CONCEPT_MIN_SHARE = 0.25
_CONCEPT_LARGE_CLUSTER_SIZE = 8
_CONCEPT_LARGE_CLUSTER_MIN_SHARE = 0.20
_MAX_LABEL_TERMS = 3
_MIN_LABEL_TERMS = 2

# Keep title extraction local to the analysis module so cluster labels do not
# depend on an optional NLP package.  These are deliberately conservative: a
# title word only disappears when it is common English or a generic paper
# descriptor.
_ENGLISH_STOPWORDS = frozenset(
    {
        "a",
        "about",
        "above",
        "after",
        "again",
        "against",
        "all",
        "am",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "because",
        "been",
        "before",
        "being",
        "below",
        "between",
        "both",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "doing",
        "down",
        "during",
        "each",
        "few",
        "for",
        "from",
        "further",
        "had",
        "has",
        "have",
        "having",
        "he",
        "her",
        "here",
        "hers",
        "herself",
        "him",
        "himself",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "itself",
        "just",
        "me",
        "more",
        "most",
        "my",
        "myself",
        "no",
        "nor",
        "not",
        "now",
        "of",
        "off",
        "on",
        "once",
        "only",
        "or",
        "other",
        "our",
        "ours",
        "ourselves",
        "out",
        "over",
        "own",
        "same",
        "she",
        "should",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "theirs",
        "them",
        "themselves",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "too",
        "under",
        "until",
        "up",
        "very",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "you",
        "your",
        "yours",
        "yourself",
        "yourselves",
    }
)
_GENERIC_PAPER_STOPWORDS = frozenset(
    {
        "analysis",
        "application",
        "applications",
        "approach",
        "approaches",
        "based",
        "benchmark",
        "benchmarks",
        "case",
        "cases",
        "comparative",
        "dataset",
        "datasets",
        "evaluation",
        "evaluations",
        "evaluating",
        "efficient",
        "fast",
        "framework",
        "frameworks",
        "investigation",
        "investigating",
        "improving",
        "language",
        "large",
        "learning",
        "method",
        "methods",
        "methodology",
        "model",
        "models",
        "new",
        "novel",
        "paper",
        "papers",
        "performance",
        "problem",
        "problems",
        "proposed",
        "proposal",
        "result",
        "results",
        "review",
        "reviews",
        "study",
        "studies",
        "system",
        "systems",
        "task",
        "tasks",
        "technique",
        "techniques",
        "title",
        "toward",
        "towards",
        "using",
        "via",
        "work",
        "works",
    }
)
_TITLE_STOPWORDS = _ENGLISH_STOPWORDS | _GENERIC_PAPER_STOPWORDS
_TITLE_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


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
    """Return eligible ``(score, label, concept_id)`` rows.

    A concept needs to occur in at least two cluster works and in a meaningful
    share of the cluster.  The share threshold is relaxed for larger clusters,
    where a fixed 25 percent would otherwise discard useful recurring topics.
    """

    if not cluster_work_ids or project_size <= 0:
        return []
    cluster_size = len(cluster_work_ids)
    minimum_share = (
        _CONCEPT_LARGE_CLUSTER_MIN_SHARE
        if cluster_size >= _CONCEPT_LARGE_CLUSTER_SIZE
        else _CONCEPT_MIN_SHARE
    )
    cluster_concepts = set().union(*(work_concepts[work_id] for work_id in cluster_work_ids))
    rows: list[tuple[float, str, str]] = []
    for concept_id in sorted(cluster_concepts):
        cluster_count = sum(concept_id in work_concepts[work_id] for work_id in cluster_work_ids)
        if cluster_count < _CONCEPT_MIN_SUPPORT or cluster_count / cluster_size < minimum_share:
            continue
        project_count = sum(concept_id in concepts for concepts in work_concepts.values())
        if project_count == 0:
            continue
        support_share = cluster_count / cluster_size
        distinctiveness = math.log(1.0 + project_size / project_count)
        score = support_share * distinctiveness
        label = concept_labels.get(concept_id, concept_id)
        rows.append((score, label, concept_id))
    rows.sort(key=lambda row: (-row[0], row[1].casefold(), row[1], row[2]))
    return rows


def _normalized_phrase(value: str) -> tuple[str, ...]:
    """Return punctuation and case independent tokens for label comparisons."""

    if not isinstance(value, str):
        return ()
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return tuple(_TITLE_TOKEN_RE.findall(normalized))


def _is_title_content_token(token: str) -> bool:
    return (
        len(token) > 1
        and token not in _TITLE_STOPWORDS
        and not any(character.isdigit() for character in token)
    )


def _title_phrases(title: str) -> set[tuple[str, ...]]:
    """Return title unigrams and bigrams after removing generic words.

    Bigrams only join words that are adjacent in the original title, so a
    removed stopword breaks the phrase instead of being bridged.
    """

    tokens = _normalized_phrase(title)
    phrases: set[tuple[str, ...]] = set()
    for index, token in enumerate(tokens):
        if not _is_title_content_token(token):
            continue
        phrases.add((token,))
        if index + 1 < len(tokens) and _is_title_content_token(tokens[index + 1]):
            phrases.add((token, tokens[index + 1]))
    return phrases


def _phrase_contains(longer: tuple[str, ...], shorter: tuple[str, ...]) -> bool:
    if not shorter or len(shorter) > len(longer):
        return False
    width = len(shorter)
    return any(longer[index : index + width] == shorter for index in range(len(longer) - width + 1))


def _near_duplicate_phrase(candidate: tuple[str, ...], selected: list[tuple[str, ...]]) -> bool:
    return any(
        _phrase_contains(candidate, previous) or _phrase_contains(previous, candidate)
        for previous in selected
    )


def _title_phrase_scores(
    cluster_work_ids: list[str], works: Mapping[str, Mapping[str, Any]], project_size: int
) -> list[tuple[float, int, int, int, str, tuple[str, ...]]]:
    """Score title phrases using cluster support and project-wide rarity.

    Rows contain score, n-gram length, cluster support, project support, display
    label, and normalized tokens.  One phrase is counted at most once per work,
    so repeated words in one title do not inflate its support.
    """

    if not cluster_work_ids or project_size <= 0:
        return []

    work_phrases: dict[str, set[tuple[str, ...]]] = {}
    for work_id, node in works.items():
        title = str(node.get("label") or work_id)
        work_phrases[work_id] = _title_phrases(title)

    cluster_phrases = set().union(*(work_phrases[work_id] for work_id in cluster_work_ids))
    rows: list[tuple[float, int, int, int, str, tuple[str, ...]]] = []
    for phrase in sorted(cluster_phrases):
        cluster_count = sum(phrase in work_phrases[work_id] for work_id in cluster_work_ids)
        project_count = sum(phrase in phrases for phrases in work_phrases.values())
        if cluster_count == 0 or project_count == 0:
            continue
        support_share = cluster_count / len(cluster_work_ids)
        distinctiveness = math.log(1.0 + project_size / project_count)
        score = support_share * distinctiveness
        display = " ".join(token.title() for token in phrase)
        rows.append((score, len(phrase), cluster_count, project_count, display, phrase))

    # A phrase repeated by multiple cluster works is more useful than a phrase
    # unique to one title.  Single-work phrases remain available only when no
    # repeated candidate exists at all.
    if any(row[2] >= _CONCEPT_MIN_SUPPORT for row in rows):
        rows = [row for row in rows if row[2] >= _CONCEPT_MIN_SUPPORT]
    # Prefer a surviving bigram over its constituent unigrams, which always have
    # at least the same support and would otherwise suppress the bigram.
    bigram_tokens = {token for row in rows if row[1] == 2 for token in row[5]}
    rows = [row for row in rows if row[1] != 1 or row[5][0] not in bigram_tokens]
    rows.sort(
        key=lambda row: (
            -row[0],
            -row[1],
            -row[2],
            row[3],
            row[4].casefold(),
            row[4],
            row[5],
        )
    )
    return rows


def _label_terms(
    concept_scores: list[tuple[float, str, str]],
    title_scores: list[tuple[float, int, int, int, str, tuple[str, ...]]],
) -> tuple[list[str], list[str]]:
    """Return ``(label_terms, top_concepts)`` with stable containment deduping."""

    top_concepts: list[str] = []
    concept_phrases: list[tuple[str, ...]] = []
    for _, concept_label, _ in concept_scores:
        label = str(concept_label).strip()
        phrase = _normalized_phrase(label)
        if not label or _near_duplicate_phrase(phrase, concept_phrases):
            continue
        top_concepts.append(label)
        concept_phrases.append(phrase)
        if len(top_concepts) == 5:
            break

    label_terms = top_concepts[:_MAX_LABEL_TERMS]
    selected_phrases = [_normalized_phrase(term) for term in label_terms]
    target_count = min(_MAX_LABEL_TERMS, len(label_terms))
    if len(label_terms) < _MIN_LABEL_TERMS:
        target_count = _MIN_LABEL_TERMS

    for _, _, _, _, title_label, phrase in title_scores:
        if len(label_terms) >= target_count:
            break
        if not title_label or _near_duplicate_phrase(phrase, selected_phrases):
            continue
        label_terms.append(title_label)
        selected_phrases.append(phrase)

    return label_terms[:_MAX_LABEL_TERMS], top_concepts


def _fallback_label(cluster_work_ids: list[str], works: Mapping[str, Mapping[str, Any]]) -> str:
    """Return a non-empty title when every title token is filtered."""

    def key(work_id: str) -> tuple[float, str, str]:
        node = works[work_id]
        data = _node_data(node)
        title = str(node.get("label") or work_id)
        citation_count = data.get("cited_by_count")
        if citation_count is None:
            citation_count = data.get("in_degree")
        return (-_as_float(citation_count), title.casefold(), work_id)

    work_id = min(cluster_work_ids, key=key)
    return str(works[work_id].get("label") or work_id)[:_FALLBACK_TITLE_LENGTH]


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
        title_scores = _title_phrase_scores(cluster_work_ids, works, project_size)
        label_terms, top_concepts = _label_terms(scores, title_scores)
        label = " · ".join(label_terms) if label_terms else _fallback_label(cluster_work_ids, works)
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
