"""Research graph contract tests.

The optional Neo4j fixture uses a THROWAWAY database and deletes every node before each test.
Set PORTOLAN_TEST_NEO4J_URI (and credentials) only for a disposable instance.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from portolan.graph.base import escape_lucene_query, mint_author_id, mint_project_id, mint_work_id
from portolan.graph.memory import InMemoryResearchGraph
from portolan.graph.models import AuthorNode, ConceptNode, Inclusion, WorkNode
from portolan.graph.neo4j_graph import Neo4jResearchGraph


@pytest.fixture(params=["memory", pytest.param("neo4j", marks=pytest.mark.neo4j)])
def graph(request: pytest.FixtureRequest) -> Iterator[InMemoryResearchGraph | Neo4jResearchGraph]:
    if request.param == "memory":
        yield InMemoryResearchGraph()
        return
    uri = os.getenv("PORTOLAN_TEST_NEO4J_URI")
    if not uri:
        pytest.skip("PORTOLAN_TEST_NEO4J_URI is not set")
    store = Neo4jResearchGraph(
        uri,
        os.getenv("PORTOLAN_TEST_NEO4J_USER", "neo4j"),
        os.getenv("PORTOLAN_TEST_NEO4J_PASSWORD", ""),
    )
    store._driver.execute_query("MATCH (n) DETACH DELETE n", database_=store._database)
    store.ensure_schema()
    try:
        yield store
    finally:
        store.close()


def test_minting() -> None:
    assert (
        mint_work_id(WorkNode(title=" A  Title ", year=2024, openalex_id="W123")) == "openalex:W123"
    )
    assert mint_work_id(WorkNode(title="A", doi="10.1000/ABC")) == "doi:10.1000/abc"
    assert mint_work_id(WorkNode(title="A", arxiv_id="2401.01234v3")) == "arxiv:2401.01234"
    assert mint_work_id(WorkNode(title="A", s2_id="xyz")) == "s2:xyz"
    assert mint_work_id(WorkNode(title=" A  Title ", year=2024)) == mint_work_id(
        WorkNode(title="a title", year=2024)
    )
    assert mint_author_id(AuthorNode(name="A B", orcid="0000-0001")) == "orcid:0000-0001"
    assert mint_author_id(AuthorNode(name="  Ada   Lovelace ")) == "name:ada lovelace"
    assert mint_project_id("A Research Project").startswith("a-research-project-")
    assert len(mint_project_id("A Research Project").rsplit("-", 1)[1]) == 6


def test_lucene_escaping() -> None:
    assert escape_lucene_query('graph:(AI) + "maps"') == r"graph\:\(AI\) \+ \"maps\""


def test_identity_resolution_and_nulls(graph: InMemoryResearchGraph | Neo4jResearchGraph) -> None:
    first = graph.upsert_work(WorkNode(title="First", doi="10.1/ABC", abstract="Known"))
    second = graph.upsert_work(
        WorkNode(title="Revised", doi="10.1/ABC", openalex_id="W42", abstract=None)
    )
    assert second.id == first.id
    assert second.abstract == "Known"
    assert second.openalex_id == "W42"
    assert graph.upsert_work(WorkNode(title="Third", openalex_id="W42")).id == first.id
    assert graph.get_work(first.id).abstract == "Known"
    author = graph.upsert_author(AuthorNode(name="Ada", orcid="0000-1"))
    assert (
        graph.upsert_author(AuthorNode(name="Ada L", orcid="0000-1", openalex_id="A1")).id
        == author.id
    )
    assert graph.upsert_author(AuthorNode(name="Ada", openalex_id="A1")).id == author.id


def test_inclusion_depth_and_project_isolation(
    graph: InMemoryResearchGraph | Neo4jResearchGraph,
) -> None:
    a = graph.create_project("A")
    b = graph.create_project("B")
    w = graph.upsert_work(WorkNode(title="Shared", year=2024))
    graph.include_work(Inclusion(project_id=a.id, work_id=w.id, discovered_via="forward", depth=3))
    graph.include_work(Inclusion(project_id=a.id, work_id=w.id, discovered_via="seed", depth=1))
    assert graph.project_stats(a.id).works == 1
    assert graph.project_stats(b.id).works == 0
    assert graph.search_works(b.id, "Shared") == []
    assert graph.project_graph(b.id).nodes == []
    assert graph.work_neighborhood(w.id, b.id).cites == []
    if isinstance(graph, InMemoryResearchGraph):
        assert graph._inclusions[(a.id, w.id)].depth == 1
        assert graph._inclusions[(a.id, w.id)].discovered_via == "forward"
    else:
        records, _, _ = graph._driver.execute_query(
            "MATCH (:Project {id: $pid})-[i:INCLUDES]->(:Work {id: $wid}) "
            "RETURN i.depth AS depth, i.discovered_via AS via",
            pid=a.id,
            wid=w.id,
            database_=graph._database,
        )
        assert (records[0]["depth"], records[0]["via"]) == (1, "forward")


def test_authors_and_concepts_replaced(graph: InMemoryResearchGraph | Neo4jResearchGraph) -> None:
    p = graph.create_project("P")
    w = graph.upsert_work(WorkNode(title="Paper"))
    graph.include_work(Inclusion(project_id=p.id, work_id=w.id, discovered_via="seed"))
    a = graph.upsert_author(AuthorNode(name="Ada"))
    b = graph.upsert_author(AuthorNode(name="Bob"))
    c = graph.upsert_concept(ConceptNode(id="graph", label="Graph", aliases=["network"]))
    c = graph.upsert_concept(ConceptNode(id="graph", label="Graph", aliases=["network", "graphs"]))
    assert c.aliases == ["graphs", "network"]
    graph.set_authors(w.id, [(a.id, 1), (b.id, 2)])
    graph.set_concepts(w.id, [(c.id, 0.7)])
    assert [item.id for item in graph.works_by_concept(c.id, p.id)] == [w.id]
    graph.set_authors(w.id, [(b.id, 1)])
    graph.set_concepts(w.id, [])
    n = graph.work_neighborhood(w.id, p.id)
    assert [(author.id, pos) for author, pos in n.authors] == [(b.id, 1)]
    assert n.concepts == []
    assert [item.id for item in graph.works_by_author(b.id, p.id)] == [w.id]
    assert graph.works_by_author(a.id, p.id) == []
    assert graph.works_by_concept(c.id, p.id) == []


def test_search_rank_and_project_filter(graph: InMemoryResearchGraph | Neo4jResearchGraph) -> None:
    p = graph.create_project("Search")
    other = graph.create_project("Other")
    best = graph.upsert_work(WorkNode(title="Graph graph models", abstract="Graph methods"))
    lesser = graph.upsert_work(WorkNode(title="Graph methods", abstract="Trees"))
    hidden = graph.upsert_work(WorkNode(title="Graph graph graph hidden"))
    for w in (best, lesser):
        graph.include_work(Inclusion(project_id=p.id, work_id=w.id, discovered_via="search"))
    graph.include_work(Inclusion(project_id=other.id, work_id=hidden.id, discovered_via="seed"))
    hits = graph.search_works(p.id, "graph")
    assert [hit.id for hit in hits] == [best.id, lesser.id]
    assert len(graph.search_works(p.id, "graph", limit=1)) == 1


def test_neighborhood_graph_and_stats(graph: InMemoryResearchGraph | Neo4jResearchGraph) -> None:
    p = graph.create_project("Graph")
    other = graph.create_project("Elsewhere")
    a = graph.upsert_work(WorkNode(title="A", cited_by_count=7))
    b = graph.upsert_work(WorkNode(title="B"))
    c = graph.upsert_work(WorkNode(title="C"))
    for w in (a, b):
        graph.include_work(Inclusion(project_id=p.id, work_id=w.id, discovered_via="seed"))
    graph.include_work(Inclusion(project_id=other.id, work_id=c.id, discovered_via="seed"))
    graph.add_citation(a.id, b.id)
    graph.add_citation(c.id, a.id)
    graph.add_citation(a.id, a.id)
    author = graph.upsert_author(AuthorNode(name="Ada"))
    concept = graph.upsert_concept(ConceptNode(id="graph", label="Graph"))
    graph.set_authors(a.id, [(author.id, 1)])
    graph.set_concepts(a.id, [(concept.id, 0.8)])
    graph.set_document(a.id, "abc", "https://example.org/a.pdf")
    n = graph.work_neighborhood(a.id, p.id)
    assert [w.id for w in n.cites] == [b.id]
    assert n.cited_by == []
    assert [w.id for w in graph.work_neighborhood(a.id).cited_by] == [c.id]
    view = graph.project_graph(p.id)
    assert {(e["source"], e["target"], e["kind"]) for e in view.edges} == {
        (a.id, b.id, "cites"),
        (a.id, author.id, "authored_by"),
        (a.id, concept.id, "has_concept"),
    }
    assert {node["id"] for node in view.nodes} == {a.id, b.id, author.id, concept.id}
    assert view.nodes == sorted(
        view.nodes,
        key=lambda node: ({"work": 0, "author": 1, "concept": 2}[node["kind"]], node["id"]),
    )
    assert view.edges == sorted(
        view.edges, key=lambda edge: (edge["source"], edge["target"], edge["kind"])
    )
    a_view = next(node for node in view.nodes if node["id"] == a.id)
    assert a_view["data"]["out_degree"] == 1
    assert a_view["data"]["in_degree"] == 0
    assert graph.project_stats(p.id).model_dump() == {
        "works": 2,
        "citations": 1,
        "authors": 1,
        "concepts": 1,
        "documents": 1,
    }
    assert {node["kind"] for node in graph.project_graph(p.id, include_authors=False).nodes} == {
        "work",
        "concept",
    }


def test_delete_project_keeps_global_work(
    graph: InMemoryResearchGraph | Neo4jResearchGraph,
) -> None:
    p = graph.create_project("Delete me")
    assert graph.get_project(p.id) == p
    assert p.id in [project.id for project in graph.list_projects()]
    w = graph.upsert_work(WorkNode(title="Persistent"))
    graph.include_work(Inclusion(project_id=p.id, work_id=w.id, discovered_via="seed"))
    assert graph.delete_project(p.id)
    assert graph.get_project(p.id) is None
    assert graph.get_work(w.id) is not None
    assert not graph.delete_project(p.id)
