"""Offline checks for structural frontier scoring."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from portolan.analysis.frontier import frontier_concepts, frontier_scores
from portolan.api.models import AnalysisCluster, AnalysisWork, MainPath, ProjectAnalysis
from portolan.graph.models import GraphView


def _analysis() -> ProjectAnalysis:
    memberships = {"old": "c1", "w1": "c1", "w2": "c1", "w3": "c2"}
    return ProjectAnalysis(
        project_id="p1",
        computed_at=datetime(2026, 1, 1, tzinfo=UTC),
        clusters=[
            AnalysisCluster(
                id="c1",
                label="Established",
                size=3,
                top_concepts=["Old concept"],
                work_ids=["old", "w1", "w2"],
            ),
            AnalysisCluster(
                id="c2",
                label="New field",
                size=1,
                top_concepts=["New concept"],
                work_ids=["w3"],
            ),
        ],
        works={
            work_id: AnalysisWork(
                cluster=cluster,
                pagerank=0.0,
                betweenness=0.0,
                local_in=0,
                local_out=0,
                roles=[],
            )
            for work_id, cluster in memberships.items()
        },
        main_path=MainPath(
            work_ids=["old", "w1", "w2", "w3"],
            edges=[
                {"source": "old", "target": "w1", "spc": 1},
                {"source": "w1", "target": "w2", "spc": 1},
                {"source": "w2", "target": "w3", "spc": 1},
            ],
        ),
    )


def _view() -> GraphView:
    return GraphView(
        nodes=[
            {
                "id": "old",
                "kind": "work",
                "label": "Old",
                "data": {
                    "year": 2020,
                    "cited_by_count": 999,
                    "work_type": "article",
                    "venue": "Journal",
                    "doi": "10.1000/old",
                },
            },
            {
                "id": "w1",
                "kind": "work",
                "label": "Established recent",
                "data": {
                    "year": 2022,
                    "cited_by_count": 20,
                    "work_type": "article",
                    "venue": "Journal",
                    "doi": "10.1000/w1",
                },
            },
            {
                "id": "w2",
                "kind": "work",
                "label": "Emerging preprint",
                "data": {
                    "year": 2023,
                    "cited_by_count": 40,
                    "work_type": "preprint",
                },
            },
            {
                "id": "w3",
                "kind": "work",
                "label": "Newest arXiv work",
                "data": {
                    "year": 2024,
                    "cited_by_count": 80,
                    "arxiv_id": "2401.00001",
                    "doi": "10.48550/arXiv.2401.00001",
                },
            },
            {"id": "c-old", "kind": "concept", "label": "Old concept"},
            {"id": "c-new", "kind": "concept", "label": "New concept"},
        ],
        edges=[
            {"source": "w1", "target": "old", "kind": "cites"},
            {"source": "w2", "target": "w1", "kind": "cites"},
            {"source": "w3", "target": "w1", "kind": "cites"},
            {"source": "w3", "target": "w2", "kind": "cites"},
            {"source": "old", "target": "c-old", "kind": "has_concept"},
            {"source": "w1", "target": "c-old", "kind": "has_concept"},
            {"source": "w2", "target": "c-old", "kind": "has_concept"},
            {"source": "w2", "target": "c-new", "kind": "has_concept"},
            {"source": "w3", "target": "c-new", "kind": "has_concept"},
        ],
    )


def test_frontier_scores_expose_components_and_exclude_old_works() -> None:
    scores = frontier_scores(_view(), _analysis(), now_year=2024)

    assert [work.work_id for work in scores] == ["w3", "w2", "w1"]
    assert all(work.year >= 2022 for work in scores)
    assert scores[0].title == "Newest arXiv work"

    by_id = {work.work_id: work for work in scores}
    assert by_id["w1"].components == {
        "velocity": 0.0,
        "local_uptake": 1.0,
        # w1 is not the main-path end but cites the main-path node "old".
        "main_path_leaf": 0.5,
        "cluster_growth": pytest.approx(4 / 9),
        "new_concept": 0.0,
        "preprint": 0.0,
    }
    assert by_id["w2"].components["velocity"] == pytest.approx(0.5)
    assert by_id["w2"].components["local_uptake"] == pytest.approx(0.5)
    assert by_id["w2"].components["main_path_leaf"] == 0.5
    assert by_id["w2"].components["new_concept"] == 1.0
    assert by_id["w2"].components["preprint"] == 1.0
    assert by_id["w3"].components == {
        "velocity": 1.0,
        "local_uptake": 0.0,
        "main_path_leaf": 1.0,
        "cluster_growth": pytest.approx(2 / 3),
        "new_concept": 1.0,
        "preprint": 1.0,
    }
    assert by_id["w3"].score == pytest.approx(0.80)


def test_frontier_defaults_to_project_newest_year_and_is_reproducible() -> None:
    view = _view()
    analysis = _analysis()
    assert frontier_scores(view, analysis) == frontier_scores(view, analysis, now_year=2024)
    assert [
        work.work_id for work in frontier_scores(view, analysis, now_year=2022, window_years=2)
    ] == [
        "old",
        "w1",
    ]


def test_frontier_concepts_report_first_year_and_adoption_by_year() -> None:
    concepts = frontier_concepts(_view(), now_year=2024)

    assert concepts == [
        {
            "concept_id": "c-new",
            "label": "New concept",
            "first_year": 2023,
            "adoption_by_year": {2023: 1, 2024: 1},
        }
    ]


def test_empty_graph_has_no_frontier() -> None:
    empty = GraphView()
    analysis = ProjectAnalysis(
        project_id="empty",
        computed_at=datetime(2026, 1, 1, tzinfo=UTC),
        clusters=[],
        works={},
        main_path=MainPath(work_ids=[], edges=[]),
    )
    assert frontier_scores(empty, analysis) == []
    assert frontier_concepts(empty) == []


def test_main_path_leaf_and_preprint_components_for_off_path_works() -> None:
    view = _view()
    view.nodes.extend(
        [
            {
                "id": "journal-arxiv",
                "kind": "work",
                "label": "Published with arXiv copy",
                "data": {
                    "year": 2024,
                    "cited_by_count": 1,
                    "arxiv_id": "2402.00002",
                    "venue": "Journal",
                    "doi": "10.1000/journal",
                },
            },
            {
                "id": "openalex-arxiv",
                "kind": "work",
                "label": "arXiv-hosted",
                "data": {
                    "year": 2024,
                    "cited_by_count": 1,
                    "arxiv_id": "2403.00003",
                    "venue": "arXiv (Cornell University)",
                },
            },
        ]
    )
    view.edges.append({"source": "openalex-arxiv", "target": "w3", "kind": "cites"})

    by_id = {work.work_id: work for work in frontier_scores(view, _analysis(), now_year=2024)}

    assert by_id["journal-arxiv"].components["main_path_leaf"] == 0.0
    assert by_id["journal-arxiv"].components["preprint"] == 0.0
    assert by_id["journal-arxiv"].components["cluster_growth"] == 0.0
    assert by_id["openalex-arxiv"].components["main_path_leaf"] == 0.5
    assert by_id["openalex-arxiv"].components["preprint"] == 1.0
    for work in by_id.values():
        assert set(work.components) == {
            "velocity",
            "local_uptake",
            "main_path_leaf",
            "cluster_growth",
            "new_concept",
            "preprint",
        }
        assert 0.0 <= work.score <= 1.0
