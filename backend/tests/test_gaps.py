"""Offline tests for structural gap detection and verification."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from portolan.analysis.gaps import detect_gaps
from portolan.graph.models import GraphView


def _view(
    works: list[tuple[str, int]],
    concepts: dict[str, str],
    work_concepts: dict[str, list[str]],
    citations: list[tuple[str, str]] | None = None,
) -> GraphView:
    citations = citations or []
    nodes: list[dict[str, Any]] = [
        {
            "id": work_id,
            "kind": "work",
            "label": work_id,
            "data": {"year": year, "cited_by_count": 1},
        }
        for work_id, year in works
    ]
    nodes.extend(
        {"id": concept_id, "kind": "concept", "label": label, "data": {}}
        for concept_id, label in concepts.items()
    )
    edges: list[dict[str, str]] = [
        {"source": work_id, "target": concept_id, "kind": "has_concept"}
        for work_id, concept_ids in work_concepts.items()
        for concept_id in concept_ids
    ]
    edges.extend(
        {"source": source, "target": target, "kind": "cites"} for source, target in citations
    )
    return GraphView(nodes=nodes, edges=edges)


def _analysis(clusters: list[tuple[str, str, list[str]]]) -> Any:
    return SimpleNamespace(
        clusters=[
            SimpleNamespace(id=cluster_id, label=label, work_ids=work_ids)
            for cluster_id, label, work_ids in clusters
        ],
        works={
            work_id: SimpleNamespace(
                cluster=cluster_id,
                pagerank=1.0 if index == 0 else 0.1,
                betweenness=0.0,
                local_in=0,
            )
            for cluster_id, _, work_ids in clusters
            for index, work_id in enumerate(work_ids)
        },
    )


def test_bridging_gap_has_shared_concepts_and_central_work_evidence() -> None:
    view = _view(
        [(f"a{i}", 2023) for i in range(3)] + [(f"b{i}", 2023) for i in range(3)],
        {"shared": "shared", "a": "cluster A", "b": "cluster B"},
        {
            **{f"a{i}": ["shared", "a"] for i in range(3)},
            **{f"b{i}": ["shared", "b"] for i in range(3)},
        },
    )
    gaps = detect_gaps(
        view,
        _analysis(
            [
                ("ca", "Cluster A", ["a0", "a1", "a2"]),
                ("cb", "Cluster B", ["b0", "b1", "b2"]),
            ]
        ),
    )

    bridging = [gap for gap in gaps if gap.type == "bridging"]
    assert bridging
    gap = bridging[0]
    assert gap.evidence["cluster_ids"] == ["ca", "cb"]
    assert gap.evidence["concept_ids"] == ["shared"]
    assert set(gap.evidence["work_ids"]) == {"a0", "b0"}
    assert gap.metrics["cross_links"] == 0
    assert "rarely cite" in gap.statement


def test_bridging_is_suppressed_when_clusters_cite_each_other() -> None:
    works = [(f"a{i}", 2023) for i in range(3)] + [(f"b{i}", 2023) for i in range(3)]
    cross = [(f"a{i}", f"b{j}") for i in range(3) for j in range(3)]
    view = _view(
        works,
        {"shared": "shared", "a": "cluster A", "b": "cluster B"},
        {
            **{f"a{i}": ["shared", "a"] for i in range(3)},
            **{f"b{i}": ["shared", "b"] for i in range(3)},
        },
        cross,
    )
    gaps = detect_gaps(
        view,
        _analysis(
            [
                ("ca", "Cluster A", ["a0", "a1", "a2"]),
                ("cb", "Cluster B", ["b0", "b1", "b2"]),
            ]
        ),
    )
    assert not [gap for gap in gaps if gap.type == "bridging"]


def test_matrix_void_finds_dense_disjoint_concepts_with_common_neighbour() -> None:
    view = _view(
        [(f"x{i}", 2023) for i in range(3)] + [(f"y{i}", 2023) for i in range(3)],
        {"x": "X", "y": "Y", "z": "shared neighbour"},
        {
            **{f"x{i}": ["x", "z"] for i in range(3)},
            **{f"y{i}": ["y", "z"] for i in range(3)},
        },
    )
    gaps = detect_gaps(view, _analysis([]))
    matrix = [gap for gap in gaps if gap.type == "matrix_void"]
    assert matrix
    gap = matrix[0]
    assert gap.evidence["concept_ids"] == ["x", "y"]
    assert gap.metrics["adamic_adar"] > 0
    assert "no work combines" in gap.statement


def test_stagnation_gap_uses_last_active_year() -> None:
    old = [(f"old{i}", 2020 + (i % 3)) for i in range(5)]
    recent = [("new0", 2024), ("new1", 2024)]
    view = _view(old + recent, {}, {})
    gaps = detect_gaps(
        view,
        _analysis([("old-cluster", "Old methods", [work_id for work_id, _ in old])]),
    )
    stagnation = [gap for gap in gaps if gap.type == "stagnation"]
    assert stagnation
    gap = stagnation[0]
    assert gap.evidence["cluster_ids"] == ["old-cluster"]
    assert gap.metrics["last_active_year"] == 2022
    assert "2022" in gap.statement


def test_ids_are_deterministic_and_small_projects_do_not_crash() -> None:
    view = _view(
        [(f"a{i}", 2023) for i in range(3)] + [(f"b{i}", 2023) for i in range(3)],
        {"shared": "Shared"},
        {work_id: ["shared"] for work_id in ("a0", "a1", "a2", "b0", "b1", "b2")},
    )
    analysis = _analysis([("ca", "A", ["a0", "a1", "a2"]), ("cb", "B", ["b0", "b1", "b2"])])
    first = detect_gaps(view, analysis)
    reordered = GraphView(nodes=list(reversed(view.nodes)), edges=list(reversed(view.edges)))
    second = detect_gaps(reordered, analysis)
    assert first
    assert [gap.id for gap in first] == [gap.id for gap in second]

    assert detect_gaps(GraphView(), _analysis([])) == []
    assert detect_gaps(_view([("a", 2024)], {}, {}), _analysis([])) == []


def test_bridging_search_terms_pair_top_concepts_of_each_cluster() -> None:
    view = _view(
        [(f"a{i}", 2023) for i in range(3)] + [(f"b{i}", 2023) for i in range(3)],
        {"s1": "graph neural networks", "s2": "message passing", "a": "chemistry"},
        {
            "a0": ["s1", "a"],
            "a1": ["s1", "a"],
            "a2": ["s1", "s2"],
            "b0": ["s2", "s1"],
            "b1": ["s2"],
            "b2": ["s2"],
        },
    )
    analysis = _analysis([("ca", "A", ["a0", "a1", "a2"]), ("cb", "B", ["b0", "b1", "b2"])])
    [gap] = [gap for gap in detect_gaps(view, analysis) if gap.type == "bridging"]
    assert gap.search_terms == ["graph neural networks", "message passing"]
    assert 0.0 < gap.confidence <= 1.0


def test_matrix_void_requires_disjoint_concepts_and_informative_neighbours() -> None:
    # X and Y co-occur on one work, so they are not a void.
    view = _view(
        [(f"x{i}", 2023) for i in range(3)] + [(f"y{i}", 2023) for i in range(3)],
        {"x": "X", "y": "Y", "z": "Z"},
        {
            "x0": ["x", "z", "y"],
            "x1": ["x", "z"],
            "x2": ["x", "z"],
            **{f"y{i}": ["y", "z"] for i in range(3)},
        },
    )
    assert not [gap for gap in detect_gaps(view, _analysis([])) if gap.type == "matrix_void"]

    # The only shared neighbour is a hub (degree 12): Adamic–Adar stays below
    # the threshold, so the pair is not reported.
    hubs = {f"h{i}": ["hub", f"n{i}"] for i in range(10)}
    view = _view(
        [(f"x{i}", 2023) for i in range(3)]
        + [(f"y{i}", 2023) for i in range(3)]
        + [(work_id, 2023) for work_id in hubs],
        {},
        {
            **{f"x{i}": ["x", "hub"] for i in range(3)},
            **{f"y{i}": ["y", "hub"] for i in range(3)},
            **hubs,
        },
    )
    assert not [gap for gap in detect_gaps(view, _analysis([])) if gap.type == "matrix_void"]


def test_active_or_small_clusters_do_not_stagnate() -> None:
    active = [(f"act{i}", 2020 + i) for i in range(5)]  # newest work 2024
    small = [(f"small{i}", 2019) for i in range(4)]
    view = _view(active + small, {}, {})
    analysis = _analysis(
        [
            ("active", "Active", [work_id for work_id, _ in active]),
            ("small", "Small", [work_id for work_id, _ in small]),
        ]
    )
    assert not [gap for gap in detect_gaps(view, analysis) if gap.type == "stagnation"]


def test_gap_ordering_and_ids_survive_recomputation() -> None:
    old = [(f"old{i}", 2018) for i in range(5)]
    view = _view(
        old + [(f"x{i}", 2024) for i in range(3)] + [(f"y{i}", 2024) for i in range(3)],
        {"x": "X", "y": "Y", "z": "Z"},
        {
            **{f"x{i}": ["x", "z"] for i in range(3)},
            **{f"y{i}": ["y", "z"] for i in range(3)},
        },
    )
    analysis = _analysis([("old", "Old · methods", [work_id for work_id, _ in old])])
    first = detect_gaps(view, analysis)
    second = detect_gaps(view, analysis)

    assert [gap.type for gap in first] == ["matrix_void", "stagnation"]
    assert [gap.id for gap in first] == [gap.id for gap in second]
    assert all(gap.status == "proposed" and gap.note is None for gap in first)
    stagnation = first[1]
    assert stagnation.search_terms == ["Old methods"]
    assert len(stagnation.evidence["work_ids"]) == 5
    assert "2018" in stagnation.statement
