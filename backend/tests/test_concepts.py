from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import pytest

from portolan.concepts.merge import (
    DEFAULT_STOP_TERMS,
    KeywordOccurrence,
    merge_keywords,
    merge_keywords_with_report,
)
from portolan.concepts.normalize import acronym_of, normalize_keyword, slugify


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        ("Graph Neural Networks", "graph neural network"),
        ("graph-neural-networks", "graph neural network"),
        ("graph_neural_networks", "graph neural network"),
        ("graph/neural/networks", "graph neural network"),
        ("Queries", "query"),
        ("Transformers", "transformer"),
        ("LLMs", "llm"),
        ("  “Graph”  ", "graph"),
        ("ＡＩ／Ｒｅｓｅａｒｃｈ", "ai research"),
        ("multiple   spaces", "multiple space"),
    ],
)
def test_normalize_keyword_table(term: str, expected: str) -> None:
    assert normalize_keyword(term) == expected


@pytest.mark.parametrize(
    "term",
    [
        "analysis",
        "bias",
        "gas",
        "lens",
        "physics",
        "mathematics",
        "series",
        "species",
        "news",
        "process",
        "class",
        "access",
        "corpus",
        "status",
        "basis",
        "axis",
        "thesis",
        "glass",
        "virus",
        "crisis",
    ],
)
def test_normalize_keyword_preserves_singular_exceptions(term: str) -> None:
    assert normalize_keyword(term) == term


@pytest.mark.parametrize("term", ["", "   ", "---///___", "!!!", "“”"])
def test_normalize_keyword_drops_empty_or_garbage_input(term: str) -> None:
    assert normalize_keyword(term) == ""


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        ("large language model", "llm"),
        ("retrieval augmented generation", "rag"),
        ("graph neural network", "gnn"),
        ("the retrieval of information", "ri"),
    ],
)
def test_acronym_of_skips_stop_words(term: str, expected: str) -> None:
    assert acronym_of(term) == expected


@pytest.mark.parametrize("term", ["model", "", "of and the"])
def test_acronym_of_returns_none_without_contentful_multiple_words(term: str) -> None:
    assert acronym_of(term) is None


def test_slugify_returns_lowercase_ascii_hyphenated_ids() -> None:
    assert slugify("Café au lait / Graphs!") == "cafe-au-lait-graphs"


def test_exact_variants_merge_and_keep_scores_and_aliases() -> None:
    occurrences = [
        KeywordOccurrence("w2", "graph neural network"),
        KeywordOccurrence("w1", "Graph Neural Networks", score=0.4),
        KeywordOccurrence("w1", "graph-neural-networks", score=0.9),
    ]

    clusters = merge_keywords(occurrences)

    assert len(clusters) == 1
    cluster = clusters[0]
    assert cluster.label == "graph neural network"
    assert cluster.id == "graph-neural-network"
    assert cluster.aliases == ("Graph Neural Networks", "graph-neural-networks")
    assert cluster.work_ids == ("w1", "w2")
    assert cluster.occurrences == 3
    assert cluster.work_scores == {"w1": 0.9, "w2": 1.0}


def test_acronym_linking_merges_unique_acronym_and_can_be_disabled() -> None:
    occurrences = [
        KeywordOccurrence("w1", "LLM"),
        KeywordOccurrence("w2", "large language model"),
        KeywordOccurrence("w3", "large language model"),
    ]

    linked = merge_keywords(occurrences)
    assert len(linked) == 1
    assert linked[0].label == "large language model"
    assert linked[0].work_ids == ("w1", "w2", "w3")
    assert set(linked[0].aliases) == {"LLM", "llm"}

    separate = merge_keywords(occurrences, link_acronyms=False)
    assert {cluster.work_ids for cluster in separate} == {("w1",), ("w2", "w3")}


def test_ambiguous_acronym_stays_separate() -> None:
    occurrences = [
        KeywordOccurrence("w1", "QA"),
        KeywordOccurrence("w2", "quality assurance"),
        KeywordOccurrence("w3", "quantum algorithms"),
    ]

    clusters = merge_keywords(occurrences, stop_terms=())

    assert len(clusters) == 3
    assert {cluster.work_ids for cluster in clusters} == {
        ("w1",),
        ("w2",),
        ("w3",),
    }


def test_default_stop_terms_are_dropped_and_custom_terms_override_defaults() -> None:
    generic_terms = sorted(DEFAULT_STOP_TERMS)
    occurrences = [KeywordOccurrence(f"w{i}", term) for i, term in enumerate(generic_terms)]
    occurrences.append(KeywordOccurrence("graph-work", "graph"))

    clusters = merge_keywords(occurrences)

    assert [cluster.label for cluster in clusters] == ["graph"]

    custom = merge_keywords(
        [KeywordOccurrence("w1", "machine learning"), KeywordOccurrence("w2", "model")],
        stop_terms={"model"},
    )
    assert [cluster.label for cluster in custom] == ["machine learning"]


@dataclass
class _RecordingEmbedder:
    vectors: Mapping[str, Sequence[float]]

    def __post_init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def embed(self, texts: Sequence[str]) -> list[Sequence[float]]:
        self.calls.append(tuple(texts))
        return [self.vectors[text] for text in texts]


def test_embedding_merge_uses_average_linkage_to_prevent_chaining() -> None:
    vectors = {
        "alpha": (1.0, 0.0),
        "beta": (math.cos(math.radians(15)), math.sin(math.radians(15))),
        "gamma": (math.cos(math.radians(38)), math.sin(math.radians(38))),
    }
    embedder = _RecordingEmbedder(vectors)
    occurrences = [
        KeywordOccurrence("w-c", "gamma"),
        KeywordOccurrence("w-a", "alpha"),
        KeywordOccurrence("w-b", "beta"),
    ]

    clusters = merge_keywords(occurrences, embedder=embedder, similarity_threshold=0.9)

    assert len(embedder.calls) == 1
    assert set(embedder.calls[0]) == {"alpha", "beta", "gamma"}
    assert len({text for text in embedder.calls[0]}) == 3
    assert {frozenset(cluster.work_ids) for cluster in clusters} == {
        frozenset({"w-a", "w-b"}),
        frozenset({"w-c"}),
    }


def test_label_uses_frequency_then_shorter_surface_then_alphabetical() -> None:
    frequent = merge_keywords(
        [
            KeywordOccurrence("w1", "Networks"),
            KeywordOccurrence("w2", "Networks"),
            KeywordOccurrence("w3", "NETWORKS"),
            KeywordOccurrence("w4", "network"),
        ]
    )
    assert frequent[0].label == "Networks"

    short = merge_keywords(
        [
            KeywordOccurrence("w1", "Networks"),
            KeywordOccurrence("w2", "network"),
        ]
    )
    assert short[0].label == "network"

    alphabetical = merge_keywords(
        [
            KeywordOccurrence("w1", "foo_bar"),
            KeywordOccurrence("w2", "foo-bar"),
        ]
    )
    assert alphabetical[0].label == "foo-bar"


def test_id_collisions_get_stable_numeric_suffixes() -> None:
    clusters = merge_keywords(
        [
            KeywordOccurrence("w1", "cafe"),
            KeywordOccurrence("w2", "café"),
        ]
    )

    assert [(cluster.label, cluster.id) for cluster in clusters] == [
        ("cafe", "cafe"),
        ("café", "cafe-2"),
    ]


def test_id_suffixes_are_global_when_a_literal_suffix_also_exists() -> None:
    occurrences = [
        KeywordOccurrence("w1", "cafe"),
        KeywordOccurrence("w2", "café"),
        KeywordOccurrence("w3", "cafe-2"),
    ]

    clusters = merge_keywords(occurrences)

    assert [(cluster.label, cluster.id) for cluster in clusters] == [
        ("cafe", "cafe"),
        ("cafe-2", "cafe-2"),
        ("café", "cafe-3"),
    ]
    assert len({cluster.id for cluster in clusters}) == len(clusters)
    assert merge_keywords(list(reversed(occurrences))) == clusters


def test_min_works_filters_clusters_using_distinct_work_ids() -> None:
    occurrences = [
        KeywordOccurrence("w1", "graphs", score=0.2),
        KeywordOccurrence("w1", "graph", score=0.8),
        KeywordOccurrence("w2", "graph", score=0.5),
        KeywordOccurrence("w3", "transformer"),
    ]

    clusters = merge_keywords(occurrences, min_works=2)

    assert len(clusters) == 1
    assert clusters[0].label == "graph"
    assert clusters[0].work_ids == ("w1", "w2")
    assert clusters[0].work_scores == {"w1": 0.8, "w2": 0.5}
    assert clusters[0].occurrences == 3


def test_output_is_independent_of_input_order() -> None:
    occurrences = [
        KeywordOccurrence("w2", "Graph Neural Networks", score=0.4),
        KeywordOccurrence("w1", "graph neural network", score=0.8),
        KeywordOccurrence("w4", "LLM", score=0.7),
        KeywordOccurrence("w3", "large language model", score=0.6),
        KeywordOccurrence("w5", "retrieval augmented generation", score=0.9),
    ]
    expected = merge_keywords(occurrences)

    for seed in range(10):
        shuffled = occurrences.copy()
        random.Random(seed).shuffle(shuffled)
        assert merge_keywords(shuffled) == expected


def _report_reasons(report: object) -> list[str]:
    """Read the reason field from the report's documented merge entries."""
    entries = getattr(report, "merges", None)
    if entries is None:
        entries = getattr(report, "entries", None)
    if entries is None:
        entries = getattr(report, "reasons", None)
    if entries is None and isinstance(report, Mapping):
        entries = report.get("merges", report.get("entries", report.get("reasons")))
    if entries is None:
        entries = ()
    if isinstance(entries, Mapping):
        entries = entries.values()
    if isinstance(entries, str):
        entries = (entries,)

    reasons: list[str] = []
    for entry in entries:  # type: ignore[union-attr]
        if isinstance(entry, str):
            reason = entry
        elif isinstance(entry, Mapping):
            reason = entry.get("reason")
        else:
            reason = getattr(entry, "reason", None)
            if reason is None and isinstance(entry, tuple) and entry:
                reason = entry[-1]
        if isinstance(reason, str):
            reasons.append(reason)
    return reasons


def test_merge_report_lists_exact_acronym_and_embedding_reasons() -> None:
    exact_clusters, exact_report = merge_keywords_with_report(
        [
            KeywordOccurrence("w1", "Graph Neural Networks"),
            KeywordOccurrence("w2", "graph neural network"),
        ]
    )
    assert len(exact_clusters) == 1
    assert "exact" in _report_reasons(exact_report)

    acronym_clusters, acronym_report = merge_keywords_with_report(
        [
            KeywordOccurrence("w1", "LLM"),
            KeywordOccurrence("w2", "large language model"),
        ]
    )
    assert len(acronym_clusters) == 1
    assert "acronym" in _report_reasons(acronym_report)

    embedder = _RecordingEmbedder(
        {
            "alpha": (1.0, 0.0),
            "beta": (math.cos(math.radians(15)), math.sin(math.radians(15))),
        }
    )
    embedding_clusters, embedding_report = merge_keywords_with_report(
        [KeywordOccurrence("w1", "alpha"), KeywordOccurrence("w2", "beta")],
        embedder=embedder,
        similarity_threshold=0.9,
    )
    assert len(embedding_clusters) == 1
    embedding_reasons = _report_reasons(embedding_report)
    assert len(embedding_reasons) == 1
    assert embedding_reasons[0].startswith("embedding:")
    assert float(embedding_reasons[0].split(":", 1)[1]) >= 0.9
