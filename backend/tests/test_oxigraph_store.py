from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pyoxigraph import NamedNode
from rdflib import Graph, URIRef

from portolan.store.oxigraph_store import (
    BIBLIO_GRAPH,
    CITO_NS,
    PTL_NS,
    OxigraphStore,
    content_graph,
    model_quads,
    review_graph,
)


@dataclass
class Work:
    iri: str
    title: str
    issued: date
    source_tier: str
    doi: str | None = None
    url: str | None = None


@dataclass
class Author:
    iri: str
    display_name: str


@dataclass
class Organization:
    iri: str
    name: str
    domain: str
    org_kind: str


@dataclass
class Venue:
    iri: str
    name: str
    venue_kind: str


@dataclass
class Citation:
    iri: str
    citing_work: Work
    cited_work: Work
    citation_function: str
    is_influential: bool = False
    citation_context: str | None = None


@dataclass
class Concept:
    iri: str
    label: str


@dataclass
class Evidence:
    iri: str
    quote: str
    locator: str
    from_work: Work
    from_source_kind: str = "fullText"


@dataclass
class Contribution:
    iri: str
    of_work: Work
    contribution_kind: str = "method"
    addresses_limitation: list[str] = field(default_factory=list)


@dataclass
class ExtractionRun:
    iri: str
    run_id: str
    model: str = "test-model"
    prompt_version: str = "v1"
    ontology_version: str = "v0.1"
    ended_at_time: datetime | None = None


@dataclass
class Review:
    iri: str
    review_id: str
    seed_kind: str = "paper"
    seed_value: str = "seed"


@dataclass
class ReviewProtocol:
    iri: str
    review_id: str
    scope_statement: str = "test scope"


@dataclass
class Inclusion:
    iri: str
    review_id: str
    of_review: Review
    of_work: Work
    decision: str = "included"
    stage: str = "screening"


@dataclass
class Cluster:
    iri: str
    review_id: str
    of_review: Review
    level: int = 0
    label: str = "cluster"


@dataclass
class GapHypothesis:
    iri: str
    review_id: str
    of_review: Review
    gap_type: str
    statement: str
    about: Concept
    supported_by: list[Evidence] = field(default_factory=list)
    confidence: Decimal = Decimal("0.75")
    verification_outcome: str = "notChecked"
    user_status: str = "proposed"


@dataclass
class ClusterPair:
    iri: str
    of_review: str
    cluster_a: str
    cluster_b: str
    semantic_similarity: Decimal
    cross_citation_count: int


def _quad_values(repository: OxigraphStore, graph: object) -> list[object]:
    return list(repository.store.quads_for_pattern(None, None, None, graph))


def _term_value(term: object) -> str:
    return str(getattr(term, "value", term))


def test_named_graph_writes_and_citation_shortcut() -> None:
    repository = OxigraphStore()
    first = Work(
        "https://w3id.org/portolan/id/work/doi/10.1000%2Ffirst",
        "First work",
        date(2024, 1, 1),
        "peerReviewed",
        doi="10.1000/first",
    )
    second = Work(
        "https://w3id.org/portolan/id/work/arxiv/2401.00001",
        "Second work",
        date(2024, 2, 1),
        "preprint",
        url="https://arxiv.org/abs/2401.00001",
    )
    author = Author(
        "https://w3id.org/portolan/id/author/orcid/0000-0001",
        "Ada Example",
    )
    organization = Organization(
        "https://w3id.org/portolan/id/org/domain/example.org",
        "Example University",
        "example.org",
        "academic",
    )
    venue = Venue(
        "https://w3id.org/portolan/id/venue/slug/testconf",
        "TestConf",
        "conference",
    )
    for item, method in (
        (first, repository.upsert_work),
        (second, repository.upsert_work),
        (author, repository.upsert_author),
        (organization, repository.upsert_organization),
        (venue, repository.upsert_venue),
    ):
        method(item)

    citation = Citation(
        "https://w3id.org/portolan/id/citation/test",
        second,
        first,
        "usesMethodIn",
        True,
        "We use the method.",
    )
    repository.add_citation(citation)
    quads = _quad_values(repository, BIBLIO_GRAPH)
    assert any(
        _term_value(quad.subject) == second.iri
        and _term_value(quad.predicate) == f"{CITO_NS}cites"
        and _term_value(quad.object) == first.iri
        for quad in quads
    )
    assert any(
        _term_value(quad.subject) == citation.iri
        and _term_value(quad.predicate) == f"{PTL_NS}citingWork"
        and _term_value(quad.object) == second.iri
        for quad in quads
    )
    assert any(
        _term_value(quad.subject) == citation.iri
        and _term_value(quad.predicate) == f"{PTL_NS}citationFunction"
        and _term_value(quad.object) == f"{CITO_NS}usesMethodIn"
        for quad in quads
    )
    assert any(_term_value(quad.subject) == first.iri for quad in quads)
    assert any(_term_value(quad.subject) == author.iri for quad in quads)


def test_content_and_review_partitions_are_populated_and_exportable() -> None:
    repository = OxigraphStore()
    work = Work(
        "https://w3id.org/portolan/id/work/arxiv/2401.00002",
        "A content work",
        date(2024, 3, 1),
        "peerReviewed",
        url="https://example.org/work",
    )
    evidence = Evidence(
        "https://w3id.org/portolan/id/evidence/e1",
        "A verifiable quote",
        "p. 2",
        work,
    )
    statement = Contribution(
        "https://w3id.org/portolan/id/contribution/work/1",
        work,
    )
    run = ExtractionRun(
        "https://w3id.org/portolan/id/extraction/run-1",
        "run/1",
        ended_at_time=datetime(2024, 3, 2, 12, 0, 0),
    )
    repository.write_extraction(run, [statement], [evidence], work=work)
    content = _quad_values(repository, content_graph("run/1"))
    assert any(_term_value(quad.subject) == statement.iri for quad in content)
    assert any(_term_value(quad.subject) == evidence.iri for quad in content)
    assert any(
        _term_value(quad.subject) == statement.iri
        and _term_value(quad.predicate) == f"{PTL_NS}hasEvidence"
        and _term_value(quad.object) == evidence.iri
        for quad in content
    )
    assert any(
        _term_value(quad.subject) == statement.iri
        and _term_value(quad.predicate) == f"{PTL_NS}ofWork"
        and _term_value(quad.object) == work.iri
        for quad in content
    )

    review = Review("https://w3id.org/portolan/id/review/r1", "r1")
    protocol = ReviewProtocol(
        "https://w3id.org/portolan/id/protocol/r1",
        "r1",
    )
    inclusion = Inclusion(
        "https://w3id.org/portolan/id/inclusion/r1/work",
        "r1",
        review,
        work,
    )
    cluster = Cluster(
        "https://w3id.org/portolan/id/cluster/r1/0",
        "r1",
        review,
    )
    concept = Concept(
        "https://w3id.org/portolan/id/concept/problem/test",
        "test problem",
    )
    gap = GapHypothesis(
        "https://w3id.org/portolan/id/gap/r1/0",
        "r1",
        review,
        "evaluationGap",
        "No later work evaluates this setting.",
        concept,
        [evidence],
    )
    repository.write_review(review, protocol)
    repository.write_inclusion(inclusion)
    repository.write_cluster(cluster)
    repository.write_gap_hypothesis(gap)
    repository.update_gap_status(gap, "accepted", "keep this hypothesis")
    review_quads = _quad_values(repository, review_graph("r1"))
    assert any(_term_value(quad.subject) == review.iri for quad in review_quads)
    assert any(_term_value(quad.subject) == inclusion.iri for quad in review_quads)
    assert any(_term_value(quad.subject) == cluster.iri for quad in review_quads)
    assert any(_term_value(quad.subject) == gap.iri for quad in review_quads)
    assert any(
        _term_value(quad.subject) == gap.iri
        and _term_value(quad.predicate) == f"{PTL_NS}userStatus"
        and _term_value(quad.object) == "accepted"
        for quad in review_quads
    )

    turtle = repository.export_turtle()
    parsed = Graph()
    parsed.parse(data=turtle, format="turtle")
    dcterms_title = URIRef("http://purl.org/dc/terms/title")
    assert (URIRef(work.iri), dcterms_title, None) in parsed


def test_cluster_pair_endpoints_are_iris_in_rdf_binding() -> None:
    repository = OxigraphStore()
    cluster_a = "https://w3id.org/portolan/id/cluster/r1/1"
    cluster_b = "https://w3id.org/portolan/id/cluster/r1/2"
    pair = ClusterPair(
        "https://w3id.org/portolan/id/cluster-pair/r1/1/2",
        "https://w3id.org/portolan/id/review/r1",
        cluster_a,
        cluster_b,
        Decimal("0.80"),
        0,
    )
    repository.write_cluster_pair(pair)
    graph = review_graph("r1")
    cluster_a_predicate = URIRef(f"{PTL_NS}clusterA")
    cluster_b_predicate = URIRef(f"{PTL_NS}clusterB")
    quads = _quad_values(repository, graph)
    assert any(
        quad.predicate.value == str(cluster_a_predicate)
        and isinstance(quad.object, NamedNode)
        and quad.object.value == cluster_a
        for quad in quads
    )
    assert any(
        quad.predicate.value == str(cluster_b_predicate)
        and isinstance(quad.object, NamedNode)
        and quad.object.value == cluster_b
        for quad in quads
    )


def test_addresses_limitation_string_is_an_iri_object() -> None:
    contribution = Contribution(
        "https://w3id.org/portolan/id/contribution/test/1",
        Work(
            "https://w3id.org/portolan/id/work/arxiv/test",
            "Test work",
            date(2024, 1, 1),
            "preprint",
            url="https://example.org/test",
        ),
        addresses_limitation=["https://w3id.org/portolan/id/limitation/test/1"],
    )
    quads = model_quads(contribution, BIBLIO_GRAPH)
    assert any(
        quad.predicate.value == f"{PTL_NS}addressesLimitation"
        and isinstance(quad.object, NamedNode)
        and quad.object.value == contribution.addresses_limitation[0]
        for quad in quads
    )


def test_validate_is_optional_until_shapes_exist() -> None:
    repository = OxigraphStore()
    shapes = Path(__file__).resolve().parents[2] / "ontology" / "shapes.ttl"
    if not shapes.exists():
        pytest.skip("ontology/shapes.ttl is written by the ontology agent")
    work = Work(
        "https://w3id.org/portolan/id/work/doi/10.1000%2Fvalid",
        "Valid work",
        date(2024, 1, 1),
        "peerReviewed",
        doi="10.1000/valid",
    )
    repository.upsert_work(work)
    report = repository.validate()
    conforms = getattr(report, "conforms", None)
    if conforms is None and isinstance(report, dict):
        conforms = report.get("conforms")
    assert conforms is True
