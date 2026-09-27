from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import pytest

from portolan.concepts.filter import filter_concepts
from portolan.concepts.grounding import is_grounded
from portolan.concepts.keyphrases import extract_keyphrases
from portolan.concepts.merge import (
    DEFAULT_STOP_TERMS,
    ConceptCluster,
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


def _concept_cluster(
    label: str,
    work_ids: Sequence[str],
    *,
    aliases: Sequence[str] = (),
) -> ConceptCluster:
    return ConceptCluster(
        id=slugify(label),
        label=label,
        aliases=tuple(aliases),
        work_ids=tuple(work_ids),
        occurrences=len(work_ids),
        work_scores={work_id: 1.0 for work_id in work_ids},
    )


def test_filter_drops_generic_labels_and_aliases() -> None:
    clusters = [
        _concept_cluster("Computer science", ("w1",)),
        _concept_cluster("broad topic", ("w1",), aliases=("World Wide Web",)),
        _concept_cluster("information retrieval", ("w1",)),
    ]

    kept, report = filter_concepts(clusters, total_works=1)

    assert [cluster.label for cluster in kept] == ["information retrieval"]
    assert report.generic == ("Computer science", "broad topic")
    assert report.counts == {"generic": 2, "too_common": 0, "too_rare": 0}
    assert report.total_dropped == 2


def test_filter_drops_concepts_above_document_frequency_ceiling() -> None:
    clusters = [
        _concept_cluster("large language model", tuple(f"w{i}" for i in range(11))),
        _concept_cluster("information retrieval", tuple(f"w{i}" for i in range(10))),
    ]

    kept, report = filter_concepts(clusters, total_works=20)

    assert [cluster.label for cluster in kept] == ["information retrieval"]
    assert report.too_common == ("large language model",)


def test_filter_drops_concepts_below_minimum_support() -> None:
    clusters = [
        _concept_cluster("singleton", ("w1",)),
        _concept_cluster("supported", ("w1", "w2")),
    ]

    kept, report = filter_concepts(clusters, total_works=15)

    assert [cluster.label for cluster in kept] == ["supported"]
    assert report.too_rare == ("singleton",)


def test_filter_thresholds_are_inactive_for_small_projects() -> None:
    clusters = [
        _concept_cluster("broad topic", tuple(f"w{i}" for i in range(14))),
        _concept_cluster("singleton", ("w1",)),
    ]

    kept, report = filter_concepts(clusters, total_works=14)

    assert kept == clusters
    assert report.total_dropped == 0


def test_filter_is_deterministic_and_does_not_mutate_input() -> None:
    clusters = [
        _concept_cluster("singleton", ("w1",)),
        _concept_cluster("Computer science", ("w1", "w2")),
        _concept_cluster("supported", ("w1", "w2")),
    ]
    original = clusters.copy()

    first = filter_concepts(clusters, total_works=15)
    second = filter_concepts(clusters, total_works=15)

    assert first == second
    assert clusters == original


def test_grounding_rejects_edge_misclassification() -> None:
    text = "Speculative decoding on EDGE devices at the edge of the network."

    assert not is_grounded("Enhanced Data Rates for GSM Evolution", text)


def test_grounding_requires_every_content_token() -> None:
    text = "The draft model proposes several tokens per step."

    assert not is_grounded("Security token", text)
    assert is_grounded("Draft model", text)


def test_grounding_strips_trailing_parenthetical_disambiguator() -> None:
    text = "We verify a token tree in one forward pass."

    assert is_grounded("Tree (set theory)", text)
    assert is_grounded("Token Tree (data structure)", text)
    assert not is_grounded("Latency (audio)", text)


def test_grounding_accepts_uppercase_acronym_only() -> None:
    assert is_grounded("Key value", "Reusing the KV cache across steps.")
    assert is_grounded("Large language models", "Serving LLMs cheaply.")
    assert not is_grounded("Key value", "A kv store for caching.")
    assert not is_grounded("Key value", "Acronyms like KVX do not count.")


def test_grounding_matches_plural_and_singular_forms() -> None:
    assert is_grounded("Draft models", "A single draft model is trained.")
    assert is_grounded("Token", "Draft tokens are verified in parallel.")
    assert is_grounded("mixture of experts", "Sparse Mixture-of-Experts layers.")


def test_grounding_rejects_empty_input() -> None:
    assert not is_grounded("", "some text")
    assert not is_grounded("Draft model", "")


_SPECULATIVE_WORKS = [
    (
        "w1",
        "Speculative decoding with a draft model",
        "We propose speculative decoding for large language models. A small draft model "
        "proposes tokens that the target model verifies, reducing latency.",
    ),
    (
        "w2",
        "Tree-based speculative decoding",
        "Speculative decoding accelerates inference of large language models. We build a "
        "token tree from the draft model and verify it with the KV cache.",
    ),
    (
        "w3",
        "Faster inference via draft models",
        "Draft models guess future tokens; the KV cache of the target model is reused.",
    ),
    ("w4", "Protein folding", "Protein folding with diffusion."),
]


def test_keyphrases_find_shared_field_terms() -> None:
    occurrences = extract_keyphrases(_SPECULATIVE_WORKS)
    by_work: dict[str, dict[str, float]] = {}
    for occurrence in occurrences:
        assert occurrence.kind == "keyphrase"
        assert occurrence.score is not None and 0.0 < occurrence.score <= 1.0
        by_work.setdefault(occurrence.work_id, {})[occurrence.term] = occurrence.score

    assert {"speculative decoding", "draft model"} <= set(by_work["w1"])
    assert "kv cache" in by_work["w2"]
    assert max(by_work["w1"].values()) == 1.0
    # Title phrases outrank abstract-only phrases.
    assert by_work["w1"]["speculative decoding"] > by_work["w1"]["language model"]


def test_keyphrases_drop_single_work_phrases() -> None:
    terms = {occurrence.term for occurrence in extract_keyphrases(_SPECULATIVE_WORKS)}

    assert "protein folding" not in terms
    assert "diffusion" not in terms
    assert "token tree" not in terms
    assert all(occurrence.work_id != "w4" for occurrence in extract_keyphrases(_SPECULATIVE_WORKS))


def test_keyphrases_drop_stopword_edged_and_generic_phrases() -> None:
    works = [
        ("a", "The proposed method", "We show results of the proposed method on a model."),
        ("b", "The proposed method", "We show results of the proposed method on a model."),
    ]

    assert extract_keyphrases(works) == []


def test_keyphrases_prefer_longer_phrase_with_similar_support() -> None:
    terms = {occurrence.term for occurrence in extract_keyphrases(_SPECULATIVE_WORKS)}

    assert "speculative decoding" in terms
    assert "speculative" not in terms
    assert "decoding" not in terms
    assert "draft" not in terms


def test_keyphrases_respect_max_per_work_and_are_deterministic() -> None:
    first = extract_keyphrases(_SPECULATIVE_WORKS, max_per_work=2)
    second = extract_keyphrases(list(_SPECULATIVE_WORKS), max_per_work=2)

    assert first == second
    counts: dict[str, int] = {}
    for occurrence in first:
        counts[occurrence.work_id] = counts.get(occurrence.work_id, 0) + 1
    assert max(counts.values()) == 2
    assert extract_keyphrases(_SPECULATIVE_WORKS, max_per_work=0) == []
