from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pytest
from neo4j import Record
from neo4j.graph import Graph as Neo4jGraph
from neo4j.graph import Node as Neo4jNode
from neo4j.time import DateTime
from rdflib import RDF, Graph, Namespace, URIRef

from portolan.store.neo4j_store import (
    CITO_NS,
    PTL_NS,
    PTLR_NS,
    Neo4jStore,
    _cluster_pair_iri,
    _partition_selector,
    _relationship_endpoints,
    _relationship_properties,
    _relationship_type,
    build_node_upsert,
    build_relationship_merge,
    camel_case,
    label_for_model,
    property_name,
    records_to_turtle,
    relationship_type,
)


@dataclass
class Work:
    iri: str
    title: str
    issued: date
    source_tier: str = "preprint"
    doi: str | None = None
    published_by: str | None = None


@dataclass
class Organization:
    iri: str


@dataclass
class Citation:
    iri: str
    citing_work: Work
    cited_work: Work
    citation_function: str
    is_influential: bool = False
    citation_context: str | None = None


class FakeNode(Mapping[str, object]):
    """Small faithful stand-in for a Neo4j Node.

    Neo4j exposes node properties through the mapping interface while labels
    live on ``.labels``.  Its mapping iterator is value-oriented, so tests
    must not accidentally rely on iterating property names.
    """

    def __init__(self, properties: Mapping[str, object], labels: set[str]) -> None:
        self._properties = dict(properties)
        self.labels = frozenset(labels)

    def __getitem__(self, key: str) -> object:
        return self._properties[key]

    def __iter__(self) -> Iterator[object]:
        return iter(self._properties.values())

    def __len__(self) -> int:
        return len(self._properties)

    def keys(self) -> object:
        return self._properties.keys()

    def items(self) -> object:
        return self._properties.items()

    def get(self, key: str, default: object = None) -> object:
        return self._properties.get(key, default)


class FakeRecord(Mapping[str, object]):
    """Stand-in for a driver Record: named fields, value-oriented iteration."""

    def __init__(self, values: Mapping[str, object]) -> None:
        self._values = dict(values)

    def __getitem__(self, key: str | int) -> object:
        if isinstance(key, int):
            return list(self._values.values())[key]
        return self._values[key]

    def __iter__(self) -> Iterator[object]:
        return iter(self._values.values())

    def __len__(self) -> int:
        return len(self._values)

    def keys(self) -> object:
        return self._values.keys()

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)


class FakeRelationship:
    def __init__(
        self,
        rel_type: str,
        start_node: FakeNode,
        end_node: FakeNode,
        properties: Mapping[str, object] | None = None,
    ) -> None:
        self.type = rel_type
        self.start_node = start_node
        self.end_node = end_node
        self._properties = dict(properties or {})

    def items(self) -> object:
        return self._properties.items()


class FakeSession:
    def __init__(
        self,
        calls: list[tuple[str, dict[str, object]]],
        responses: list[object] | None = None,
    ) -> None:
        self.calls = calls
        self.responses = responses or []

    def run(self, query: str, **params: object) -> list[object]:
        self.calls.append((query, params))
        return list(self.responses)

    def close(self) -> None:
        return None


class FakeDriver:
    def __init__(self, responses: list[object] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.responses = responses or []

    def session(self, **_: object) -> FakeSession:
        return FakeSession(self.calls, self.responses)

    def close(self) -> None:
        return None


def test_exact_lpg_name_mapping_and_parameterized_cypher() -> None:
    assert label_for_model("GapHypothesis") == "GapHypothesis"
    assert label_for_model("ExtractionRun") == "Activity"
    assert relationship_type("authored_by") == "AUTHORED_BY"
    assert relationship_type("has_evidence") == "HAS_EVIDENCE"
    assert relationship_type("from_work") == "FROM_WORK"
    assert relationship_type("addresses_limitation") == "ADDRESSES_LIMITATION"
    assert relationship_type("CITES") == "CITES"
    assert camel_case("source_tier") == "sourceTier"
    assert property_name("citation_function") == "citationFunction"
    assert property_name("claims_sota") == "claimsSOTA"
    assert ":Work" in build_node_upsert("Work")
    assert "MERGE (n {iri: $iri})" in build_node_upsert("Work")
    assert "SET n:Work" in build_node_upsert("Work")
    assert "SET n:Problem, n:Concept" in build_node_upsert("Problem")
    assert "MERGE (a)-[r:CITES]->(b)" in build_relationship_merge("CITES")
    assert "MERGE (a {iri: $from_iri})" in build_relationship_merge("CITES")
    assert "$props" in build_node_upsert("Work")
    assert "$from_iri" in build_relationship_merge("CITES")


def test_partition_selector_uses_binding_partition_values() -> None:
    condition, parameters = _partition_selector("https://w3id.org/portolan/graph/content/run%2F1")
    assert "extractionRun IN $contentRuns" in condition
    assert parameters["contentRuns"] == ["https://w3id.org/portolan/id/extraction/run%2F1"]
    review_condition, review_parameters = _partition_selector(
        "https://w3id.org/portolan/graph/review/kgqa-rag"
    )
    assert "reviewId IN $reviewIds" in review_condition
    assert review_parameters["reviewIds"] == ["kgqa-rag"]


def test_write_calls_use_labels_properties_and_cites_relationship() -> None:
    driver = FakeDriver()
    repository = Neo4jStore(driver=driver)
    first = Work(
        "https://w3id.org/portolan/id/work/doi/10.1000%2Ffirst",
        "First",
        date(2024, 1, 1),
        "peerReviewed",
        "10.1000/first",
    )
    second = Work(
        "https://w3id.org/portolan/id/work/arxiv/2401.00001",
        "Second",
        date(2024, 2, 1),
    )
    repository.upsert_work(first)
    repository.add_citation(
        Citation(
            "https://w3id.org/portolan/id/citation/unused-in-lpg",
            second,
            first,
            "usesMethodIn",
            True,
            "context",
        )
    )
    assert len(driver.calls) == 2
    node_query, node_params = driver.calls[0]
    assert "SET n:Work" in node_query
    assert node_params["iri"] == first.iri
    assert node_params["props"]["sourceTier"] == "peerReviewed"  # type: ignore[index]
    assert node_params["props"]["title"] == "First"  # type: ignore[index]
    citation_query, citation_params = driver.calls[1]
    assert "MERGE (a)-[r:CITES]->(b)" in citation_query
    assert citation_params["from_iri"] == second.iri
    assert citation_params["to_iri"] == first.iri
    assert citation_params["rel_props"] == {
        "citationFunction": "usesMethodIn",
        "isInfluential": True,
        "citationContext": "context",
    }


def test_write_work_emits_published_by_relationship() -> None:
    driver = FakeDriver()
    repository = Neo4jStore(driver=driver)
    publisher = Organization("https://w3id.org/portolan/id/org/domain/example.org")
    work = Work(
        "https://w3id.org/portolan/id/work/arxiv/published",
        "Published work",
        date(2024, 1, 1),
        published_by=publisher.iri,
    )

    repository.upsert_work(work)

    assert len(driver.calls) == 2
    query, params = driver.calls[1]
    assert "MERGE (a)-[r:PUBLISHED_BY]" in query
    assert params["from_iri"] == work.iri
    assert params["to_iri"] == publisher.iri


def test_fake_records_export_to_shortcut_and_reified_citation() -> None:
    first_iri = "https://w3id.org/portolan/id/work/arxiv/1706.03762"
    second_iri = "https://w3id.org/portolan/id/work/arxiv/2211.17192"
    first = FakeNode(
        {"iri": first_iri, "title": "Attention", "sourceTier": "peerReviewed"}, {"Work"}
    )
    second = FakeNode({"iri": second_iri, "title": "Follow-up", "sourceTier": "preprint"}, {"Work"})
    relationship = FakeRelationship(
        "CITES",
        second,
        first,
        {
            "citationFunction": "usesMethodIn",
            "isInfluential": True,
            "citationContext": "We build on it.",
        },
    )
    turtle = records_to_turtle(
        [
            FakeRecord({"n": first}),
            FakeRecord({"n": second}),
            FakeRecord({"a": second, "r": relationship, "b": first}),
        ]
    )
    graph = Graph()
    graph.parse(data=turtle, format="turtle")
    cito = Namespace(CITO_NS)
    ptl = Namespace(PTL_NS)
    assert (URIRef(second_iri), cito.cites, URIRef(first_iri)) in graph
    digest = hashlib.sha1(
        (second_iri + first_iri).encode(),
        usedforsecurity=False,
    ).hexdigest()
    citation_iri = URIRef(f"{PTLR_NS}citation/{digest}")
    assert (citation_iri, URIRef(f"{PTL_NS}citingWork"), URIRef(second_iri)) in graph
    assert (citation_iri, ptl.citationFunction, URIRef(f"{CITO_NS}usesMethodIn")) in graph
    assert (URIRef(first_iri), URIRef(f"{PTL_NS}sourceTier"), None) in graph


def test_store_export_accepts_fake_records_without_connecting() -> None:
    repository = Neo4jStore()
    node = FakeNode(
        {
            "iri": "https://w3id.org/portolan/id/work/arxiv/offline",
            "title": "Offline export",
        },
        {"Work"},
    )
    turtle = repository.export_turtle(records=[FakeRecord({"n": node})])
    assert "Offline export" in turtle
    assert repository._driver is None


def test_cypher_defaults_loading_and_missing_directory(tmp_path: Path) -> None:
    competency = tmp_path / "ontology" / "competency" / "cypher"
    competency.mkdir(parents=True)
    (competency / "cq01_test.cypher").write_text(
        '// DEFAULTS: {"reviewId": "default-review", "limit": 3}\n'
        "MATCH (n:Work) RETURN n LIMIT $limit",
        encoding="utf-8",
    )
    driver = FakeDriver([FakeRecord({"n": "node"})])
    repository = Neo4jStore(driver=driver, ontology_root=tmp_path / "ontology")
    result = repository.run_competency_question(1, {"reviewId": "actual-review"})
    assert result.rows == [{"n": "node"}]  # type: ignore[union-attr]
    query, params = driver.calls[0]
    assert "DEFAULTS" not in query
    assert params == {"reviewId": "actual-review", "limit": 3}

    missing = Neo4jStore(ontology_root=tmp_path / "missing")
    with pytest.raises(FileNotFoundError, match="Cypher competency-question directory"):
        missing.run_competency_question(1, {})


@dataclass
class ExtractionRun:
    iri: str
    run_id: str
    model: str = "test-model"
    prompt_version: str = "v1"
    ontology_version: str = "v0.2"


@dataclass
class Evidence:
    iri: str
    quote: str
    from_work: str
    from_source_kind: str = "abstract"


@dataclass
class Contribution:
    iri: str
    of_work: str
    has_evidence: list[Evidence]
    reports: list[str]
    addresses_limitation: list[str] = field(default_factory=list)


@dataclass
class Result:
    iri: str
    of_work: str
    has_evidence: list[Evidence]


def test_extraction_writer_emits_forward_relationships_and_provenance_labels() -> None:
    driver = FakeDriver()
    repository = Neo4jStore(driver=driver)
    work_iri = "https://w3id.org/portolan/id/work/arxiv/fixture"
    work = Work(work_iri, "Fixture", date(2024, 1, 1), doi="10.1000/fixture")
    run_iri = "https://w3id.org/portolan/id/extraction/run-1"
    run = ExtractionRun(run_iri, "run-1")
    evidence = Evidence("https://w3id.org/portolan/id/evidence/e1", "quote", work_iri)
    result_iri = "https://w3id.org/portolan/id/result/arxiv-fixture/2"
    result = Result(result_iri, work_iri, [evidence])
    contribution = Contribution(
        "https://w3id.org/portolan/id/contribution/arxiv-fixture/1",
        work_iri,
        [evidence],
        [result_iri],
    )

    repository.write_extraction(work, run, [contribution, result], [evidence])

    report_call = next(params for query, params in driver.calls if "MERGE (a)-[r:REPORTS]" in query)
    assert report_call["from_iri"] == contribution.iri
    assert report_call["to_iri"] == result.iri
    from_work_call = next(
        params for query, params in driver.calls if "MERGE (a)-[r:FROM_WORK]" in query
    )
    assert from_work_call["from_iri"] == evidence.iri
    assert from_work_call["to_iri"] == work_iri
    activity_query, activity_params = next(
        (query, params)
        for query, params in driver.calls
        if params.get("iri") == run_iri and "SET n:Activity" in query
    )
    assert "MERGE (n {iri: $iri})" in activity_query
    assert activity_params["props"]["model"] == "test-model"  # type: ignore[index]
    content_params = next(
        params
        for query, params in driver.calls
        if params.get("iri") == contribution.iri and "extractionRun" in params["props"]
    )
    assert content_params["props"]["extractionRun"] == run_iri  # type: ignore[index]


def test_extraction_writer_scopes_limitation_edges_to_analysis_review() -> None:
    driver = FakeDriver()
    repository = Neo4jStore(driver=driver)
    work_iri = "https://w3id.org/portolan/id/work/arxiv/fixture"
    work = Work(work_iri, "Fixture", date(2024, 1, 1))
    evidence = Evidence("https://w3id.org/portolan/id/evidence/e1", "quote", work_iri)
    contribution = Contribution(
        "https://w3id.org/portolan/id/contribution/arxiv-fixture/1",
        work_iri,
        [evidence],
        [],
        ["https://w3id.org/portolan/id/limitation/1"],
    )

    repository.write_extraction(
        work,
        ExtractionRun("https://w3id.org/portolan/id/extraction/run-1", "run-1"),
        [contribution],
        [evidence],
        review_id="https://w3id.org/portolan/id/review/kgqa-rag",
    )

    limitation_call = next(
        params for query, params in driver.calls if "MERGE (a)-[r:ADDRESSES_LIMITATION]" in query
    )
    assert limitation_call["rel_props"] == {"reviewId": "kgqa-rag"}


def test_records_export_activity_concept_and_cluster_pair_binding() -> None:
    activity = FakeNode(
        {"iri": "https://w3id.org/portolan/id/extraction/run-1"},
        {"Activity"},
    )
    concept_iri = "https://w3id.org/portolan/id/concept/method/demo"
    concept = FakeNode({"iri": concept_iri, "altLabel": ["Demo"]}, {"Method", "Concept"})
    cluster_a = FakeNode(
        {"iri": "https://w3id.org/portolan/id/cluster/r/1", "label": "A"},
        {"Cluster"},
    )
    cluster_b = FakeNode(
        {"iri": "https://w3id.org/portolan/id/cluster/r/2", "label": "B"},
        {"Cluster"},
    )
    pair = FakeRelationship(
        "CLUSTER_PAIR",
        cluster_a,
        cluster_b,
        {"reviewId": "r", "semanticSimilarity": 0.8, "crossCitationCount": 0},
    )
    generated = FakeRelationship("WAS_GENERATED_BY", concept, activity)
    turtle = records_to_turtle(
        [
            FakeRecord({"n": activity}),
            FakeRecord({"n": concept}),
            FakeRecord({"a": cluster_a, "r": pair, "b": cluster_b}),
            FakeRecord({"a": concept, "r": generated, "b": activity}),
        ]
    )
    graph = Graph()
    graph.parse(data=turtle, format="turtle")
    ptl = Namespace(PTL_NS)
    prov = Namespace("http://www.w3.org/ns/prov#")
    assert (
        URIRef(activity["iri"]),
        URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#type"),
        URIRef("http://www.w3.org/ns/prov#Activity"),
    ) in graph
    assert (URIRef(concept_iri), RDF.type, ptl.Method) in graph
    assert (
        URIRef(concept_iri),
        RDF.type,
        URIRef("http://www.w3.org/2004/02/skos/core#Concept"),
    ) not in graph
    assert (URIRef(concept_iri), prov.wasGeneratedBy, URIRef(activity["iri"])) in graph
    assert any(
        subject
        for subject, predicate, object_ in graph
        if predicate == URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
        and object_ == ptl.ClusterPair
    )


def _real_node(
    graph: Neo4jGraph,
    element_id: str,
    identity: int,
    labels: set[str],
    properties: Mapping[str, object],
) -> Neo4jNode:
    return Neo4jNode(graph, element_id, identity, labels, dict(properties))


def _real_relationship(
    graph: Neo4jGraph,
    element_id: str,
    identity: int,
    rel_type: str,
    start: Neo4jNode,
    end: Neo4jNode,
    properties: Mapping[str, object] | None = None,
) -> object:
    relationship_class = graph.relationship_type(rel_type)
    relationship = relationship_class(graph, element_id, identity, dict(properties or {}))
    # These are the same endpoint slots populated by the driver's hydration helper.
    relationship._start_node = start  # type: ignore[attr-defined]
    relationship._end_node = end  # type: ignore[attr-defined]
    return relationship


@pytest.mark.parametrize(
    ("rel_type", "properties"),
    [
        ("OF_WORK", {}),
        ("HAS_EVIDENCE", {}),
        (
            "CITES",
            {
                "citationFunction": "usesMethodIn",
                "isInfluential": True,
                "citationContext": "context",
            },
        ),
        (
            "CLUSTER_PAIR",
            {"reviewId": "review-1", "semanticSimilarity": 0.81, "crossCitationCount": 2},
        ),
        ("WAS_GENERATED_BY", {}),
    ],
)
def test_real_driver_relationship_helpers_keep_mapping_trap_data(
    rel_type: str, properties: Mapping[str, object]
) -> None:
    driver_graph = Neo4jGraph()
    start = _real_node(driver_graph, "start", 1, {"Contribution"}, {"iri": "urn:start"})
    end = _real_node(driver_graph, "end", 2, {"Work"}, {"iri": "urn:end"})
    relationship = _real_relationship(
        driver_graph, "relationship", 3, rel_type, start, end, properties
    )

    assert isinstance(relationship, Mapping)
    assert _relationship_type(relationship) == rel_type
    assert _relationship_endpoints(relationship) == (start, end)
    assert _relationship_properties(relationship) == dict(properties)


def test_real_driver_relationships_export_to_expected_triples() -> None:
    driver_graph = Neo4jGraph()
    work = _real_node(driver_graph, "work", 1, {"Work"}, {"iri": "urn:work"})
    citing_work = _real_node(driver_graph, "citing-work", 2, {"Work"}, {"iri": "urn:citing-work"})
    cited_work = _real_node(driver_graph, "cited-work", 3, {"Work"}, {"iri": "urn:cited-work"})
    statement = _real_node(
        driver_graph,
        "statement",
        4,
        {"Contribution"},
        {"iri": "urn:statement"},
    )
    evidence = _real_node(driver_graph, "evidence", 5, {"Evidence"}, {"iri": "urn:evidence"})
    activity = _real_node(
        driver_graph,
        "activity",
        6,
        {"Activity"},
        {"iri": "urn:activity", "endedAtTime": DateTime(2026, 9, 22, 0, 0, 0)},
    )
    result = _real_node(
        driver_graph,
        "result",
        7,
        {"Result"},
        {"iri": "urn:result", "claimsSota": True},
    )
    cluster_a = _real_node(
        driver_graph,
        "cluster-a",
        8,
        {"Cluster"},
        {"iri": "urn:cluster-a", "label": "A"},
    )
    cluster_b = _real_node(
        driver_graph,
        "cluster-b",
        9,
        {"Cluster"},
        {"iri": "urn:cluster-b", "label": "B"},
    )
    relationships = [
        _real_relationship(driver_graph, "of-work", 10, "OF_WORK", statement, work),
        _real_relationship(driver_graph, "has-evidence", 11, "HAS_EVIDENCE", statement, evidence),
        _real_relationship(
            driver_graph,
            "cites",
            12,
            "CITES",
            citing_work,
            cited_work,
            {
                "citationFunction": "usesMethodIn",
                "isInfluential": True,
                "citationContext": "context",
            },
        ),
        _real_relationship(
            driver_graph,
            "cluster-pair",
            13,
            "CLUSTER_PAIR",
            cluster_a,
            cluster_b,
            {"reviewId": "review-1", "semanticSimilarity": 0.81, "crossCitationCount": 2},
        ),
        _real_relationship(driver_graph, "generated", 14, "WAS_GENERATED_BY", statement, activity),
    ]
    records = [
        Record([("n", node)])
        for node in (
            work,
            citing_work,
            cited_work,
            statement,
            evidence,
            activity,
            result,
            cluster_a,
            cluster_b,
        )
    ]
    records.extend(
        Record([("a", rel.start_node), ("r", rel), ("b", rel.end_node)]) for rel in relationships
    )

    graph = Graph()
    graph.parse(data=records_to_turtle(records), format="turtle")
    ptl = Namespace(PTL_NS)
    cito = Namespace(CITO_NS)
    prov = Namespace("http://www.w3.org/ns/prov#")
    citation_iri = URIRef(
        f"{PTLR_NS}citation/"
        f"{hashlib.sha1(b'urn:citing-workurn:cited-work', usedforsecurity=False).hexdigest()}"
    )
    pair_iri = _cluster_pair_iri("review-1", "urn:cluster-a", "urn:cluster-b")

    assert (URIRef("urn:statement"), ptl.ofWork, URIRef("urn:work")) in graph
    assert (URIRef("urn:statement"), ptl.hasEvidence, URIRef("urn:evidence")) in graph
    assert (URIRef("urn:citing-work"), cito.cites, URIRef("urn:cited-work")) in graph
    assert (citation_iri, RDF.type, ptl.Citation) in graph
    assert (citation_iri, ptl.citingWork, URIRef("urn:citing-work")) in graph
    assert (citation_iri, ptl.citedWork, URIRef("urn:cited-work")) in graph
    assert (citation_iri, ptl.citationFunction, URIRef(f"{CITO_NS}usesMethodIn")) in graph
    assert (pair_iri, RDF.type, ptl.ClusterPair) in graph
    assert (pair_iri, ptl.clusterA, URIRef("urn:cluster-a")) in graph
    assert (pair_iri, ptl.clusterB, URIRef("urn:cluster-b")) in graph
    assert (URIRef("urn:statement"), prov.wasGeneratedBy, URIRef("urn:activity")) in graph
    assert graph.value(
        URIRef("urn:cluster-a"), URIRef("http://www.w3.org/2000/01/rdf-schema#label")
    )
    assert graph.value(URIRef("urn:result"), ptl.claimsSOTA) is not None
    assert graph.value(URIRef("urn:result"), ptl.claimsSota) is None
    assert graph.value(URIRef("urn:activity"), prov.endedAtTime).datatype is not None


@pytest.mark.neo4j
def test_live_neo4j_smoke_only_when_configured() -> None:
    if not os.getenv("NEO4J_URI"):
        pytest.skip("NEO4J_URI is not configured")
    repository = Neo4jStore()
    repository._run("RETURN 1 AS answer")
