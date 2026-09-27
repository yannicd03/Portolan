"""Offline checks for the project citation analysis."""

from __future__ import annotations

from portolan.analysis.centrality import analyze_centrality
from portolan.analysis.clusters import cluster_works
from portolan.analysis.main_path import find_main_path
from portolan.analysis.service import analyze_project
from portolan.graph import ConceptNode, Inclusion, WorkNode
from portolan.graph.memory import InMemoryResearchGraph


def _project(graph: InMemoryResearchGraph, name: str) -> str:
    return graph.create_project(name).id


def _work(graph: InMemoryResearchGraph, project_id: str, work_id: str, year: int = 2020) -> None:
    graph.upsert_work(WorkNode(id=work_id, title=f"Title {work_id}", year=year))
    graph.include_work(Inclusion(project_id=project_id, work_id=work_id, discovered_via="seed"))


def _titled_work(graph: InMemoryResearchGraph, project_id: str, work_id: str, title: str) -> None:
    graph.upsert_work(WorkNode(id=work_id, title=title))
    graph.include_work(Inclusion(project_id=project_id, work_id=work_id, discovered_via="seed"))


def _cite(graph: InMemoryResearchGraph, citing: str, cited: str) -> None:
    graph.add_citation(citing, cited)


def test_clusters_distinctive_labels_and_bridge() -> None:
    graph = InMemoryResearchGraph()
    project_id = _project(graph, "Two fields")
    for work_id in ("a0", "a1", "a2", "b0", "b1", "b2", "x", "z"):
        _work(graph, project_id, work_id)
    for citing, cited in (
        ("a1", "a0"),
        ("a2", "a0"),
        ("a2", "a1"),
        ("b1", "b0"),
        ("b2", "b0"),
        ("b2", "b1"),
        ("x", "a2"),
        ("x", "b2"),
    ):
        _cite(graph, citing, cited)
    graph.upsert_concept(ConceptNode(id="field-a", label="Field A"))
    graph.upsert_concept(ConceptNode(id="field-b", label="Field B"))
    for work_id in ("a0", "a1", "a2"):
        graph.set_concepts(work_id, [("field-a", 1.0)])
    for work_id in ("b0", "b1", "b2"):
        graph.set_concepts(work_id, [("field-b", 1.0)])

    result = analyze_project(graph, project_id)
    assert len(result.clusters) == 2
    assert [cluster.id for cluster in result.clusters] == ["c1", "c2"]
    assert {cluster.label for cluster in result.clusters} == {"Field A", "Field B"}
    assert {cluster.top_concepts[0] for cluster in result.clusters} == {"Field A", "Field B"}
    assert "bridge" in result.works["x"].roles
    assert result.works["z"].cluster is None
    assert result.works["z"].roles == ["peripheral"]


def test_hub_foundational_emerging_and_peripheral_roles() -> None:
    graph = InMemoryResearchGraph()
    project_id = _project(graph, "Roles")
    for work_id, year in (
        ("old", 2018),
        ("p1", 2019),
        ("p2", 2020),
        ("p3", 2021),
        ("p4", 2022),
        ("p5", 2023),
        ("hub", 2024),
        ("isolated", 2024),
    ):
        _work(graph, project_id, work_id, year)
    for cited in ("old", "p1", "p2", "p3", "p4"):
        _cite(graph, "hub", cited)
    for citing in ("p1", "p2", "p3"):
        _cite(graph, citing, "old")

    result = analyze_project(graph, project_id)
    assert "hub" in result.works["hub"].roles
    assert "emerging" in result.works["hub"].roles
    assert "foundational" in result.works["old"].roles
    assert result.works["old"].local_in == 4
    assert result.works["hub"].local_out == 5
    assert result.works["isolated"].roles == ["peripheral"]


def test_spc_main_path_uses_path_counts_and_lexical_tie_break() -> None:
    graph = InMemoryResearchGraph()
    project_id = _project(graph, "DAG")
    for work_id in ("a", "b", "c", "d", "e", "z"):
        _work(graph, project_id, work_id)
    for citing, cited in (
        ("b", "a"),
        ("c", "a"),
        ("d", "b"),
        ("d", "c"),
        ("d", "z"),
        ("e", "d"),
    ):
        _cite(graph, citing, cited)

    path = analyze_project(graph, project_id).main_path
    assert path.work_ids == ["a", "b", "d", "e"]
    assert [(edge.source, edge.target, edge.spc) for edge in path.edges] == [
        ("a", "b", 1),
        ("b", "d", 1),
        ("d", "e", 3),
    ]


def test_main_path_drops_anachronisms_and_breaks_cycles() -> None:
    graph = InMemoryResearchGraph()
    project_id = _project(graph, "Cycles")
    for work_id, year in (("a", 2019), ("b", 2020), ("c", 2020)):
        _work(graph, project_id, work_id, year)
    for citing, cited in (("b", "a"), ("c", "b"), ("b", "c"), ("a", "c")):
        _cite(graph, citing, cited)

    path = analyze_project(graph, project_id).main_path
    assert path.work_ids == ["a", "b", "c"]
    assert [(edge.source, edge.target) for edge in path.edges] == [
        ("a", "b"),
        ("b", "c"),
    ]


def test_empty_and_uncited_projects() -> None:
    graph = InMemoryResearchGraph()
    empty_id = _project(graph, "Empty")
    empty = analyze_project(graph, empty_id)
    assert empty.clusters == []
    assert empty.works == {}
    assert empty.main_path.work_ids == []
    assert empty.main_path.edges == []

    uncited_id = _project(graph, "Uncited")
    for work_id in ("one", "two"):
        _work(graph, uncited_id, work_id)
    uncited = analyze_project(graph, uncited_id)
    assert uncited.clusters == []
    assert set(uncited.works) == {"one", "two"}
    assert all(work.cluster is None for work in uncited.works.values())
    assert uncited.main_path.work_ids == []
    assert uncited.main_path.edges == []


def test_concept_overlap_clusters_works_without_citations() -> None:
    graph = InMemoryResearchGraph()
    project_id = _project(graph, "Concept only")
    graph.upsert_concept(ConceptNode(id="shared", label="Shared topic"))
    for work_id in ("a", "b", "c"):
        _work(graph, project_id, work_id)
        graph.set_concepts(work_id, [("shared", 1.0)])

    result = analyze_project(graph, project_id)
    assert len(result.clusters) == 1
    assert result.clusters[0].work_ids == ["a", "b", "c"]
    assert result.clusters[0].label == "Shared topic"
    assert result.main_path.work_ids == []


def test_cluster_labels_drop_single_work_concepts_and_use_title_phrases() -> None:
    graph = InMemoryResearchGraph()
    project_id = _project(graph, "Title fallback")
    title = "A Study of Graph Retrieval for Neural Networks"
    for work_id in ("a", "b", "c"):
        _titled_work(graph, project_id, work_id, title)
    _cite(graph, "b", "a")
    _cite(graph, "c", "b")

    for concept_id, work_id in (("junk-a", "a"), ("junk-b", "b"), ("junk-c", "c")):
        graph.upsert_concept(ConceptNode(id=concept_id, label=concept_id))
        graph.set_concepts(work_id, [(concept_id, 1.0)])

    result = analyze_project(graph, project_id)

    assert len(result.clusters) == 1
    cluster = result.clusters[0]
    assert cluster.top_concepts == []
    assert cluster.label == "Graph Retrieval · Neural Networks"


def test_cluster_title_phrases_prefer_bigrams_over_more_frequent_unigrams() -> None:
    graph = InMemoryResearchGraph()
    project_id = _project(graph, "Bigram preference")
    titles = {
        "a": "Speculative Decoding with Draft Trees",
        "b": "Speculative Decoding for Transformers",
        "c": "Decoding Draft Heads",
    }
    for work_id, title in titles.items():
        _titled_work(graph, project_id, work_id, title)
    _cite(graph, "b", "a")
    _cite(graph, "c", "b")

    result = analyze_project(graph, project_id)

    assert len(result.clusters) == 1
    assert result.clusters[0].top_concepts == []
    assert result.clusters[0].label == "Speculative Decoding · Draft"


def test_cluster_labels_use_title_phrase_as_complement_without_polluting_top_concepts() -> None:
    graph = InMemoryResearchGraph()
    project_id = _project(graph, "Title complement")
    title = "A Study of Graph Retrieval for Neural Networks"
    for work_id in ("a", "b", "c"):
        _titled_work(graph, project_id, work_id, title)
    _cite(graph, "b", "a")
    _cite(graph, "c", "b")
    graph.upsert_concept(ConceptNode(id="field", label="Field"))
    for work_id in ("a", "b"):
        graph.set_concepts(work_id, [("field", 1.0)])

    result = analyze_project(graph, project_id)

    assert result.clusters[0].top_concepts == ["Field"]
    assert result.clusters[0].label == "Field · Graph Retrieval"


def test_deterministic_algorithms_and_cache_invalidation() -> None:
    graph = InMemoryResearchGraph()
    project_id = _project(graph, "Cache")
    for work_id in ("a", "b", "c"):
        _work(graph, project_id, work_id)
    _cite(graph, "b", "a")
    _cite(graph, "c", "b")
    view = graph.project_graph(project_id, include_authors=False, include_concepts=True)
    clusters_a, membership_a = cluster_works(view)
    clusters_b, membership_b = cluster_works(view)
    assert (clusters_a, membership_a) == (clusters_b, membership_b)
    reversed_view = view.model_copy(
        update={"nodes": list(reversed(view.nodes)), "edges": list(reversed(view.edges))}
    )
    assert cluster_works(reversed_view) == (clusters_a, membership_a)
    assert analyze_centrality(view, membership_a) == analyze_centrality(view, membership_b)
    assert find_main_path(view) == find_main_path(view)

    first = analyze_project(graph, project_id)
    assert analyze_project(graph, project_id) is first
    _work(graph, project_id, "d")
    updated = analyze_project(graph, project_id)
    assert updated is not first
    assert "d" in updated.works
