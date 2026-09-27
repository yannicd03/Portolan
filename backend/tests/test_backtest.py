"""Offline checks for the time-sliced frontier and gap backtest (eval/backtest)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from eval.backtest.backtest import (  # noqa: E402
    backtest,
    evaluate_gaps,
    precision_at,
    relevant_works,
    spearman,
)
from eval.backtest.run_backtest import main as run_backtest_main  # noqa: E402
from eval.backtest.slice import slice_view  # noqa: E402

from portolan.analysis.gaps import GapHypothesis  # noqa: E402
from portolan.graph.models import GraphView  # noqa: E402


def _work(work_id: str, year: int | None, cited_by_count: int = 0) -> dict[str, Any]:
    return {
        "id": work_id,
        "kind": "work",
        "label": f"Title {work_id}",
        "data": {"year": year, "cited_by_count": cited_by_count, "in_degree": 0, "out_degree": 0},
    }


def _concept(concept_id: str) -> dict[str, Any]:
    return {"id": concept_id, "kind": "concept", "label": concept_id.upper(), "data": {}}


def _cites(source: str, target: str) -> dict[str, str]:
    return {"source": source, "target": target, "kind": "cites"}


def _has(work_id: str, concept_id: str) -> dict[str, str]:
    return {"source": work_id, "target": concept_id, "kind": "has_concept"}


def _gap(gap_id: str, gap_type: str, **evidence: list[str]) -> GapHypothesis:
    return GapHypothesis(
        id=gap_id,
        type=gap_type,
        statement=f"{gap_type} {gap_id}",
        evidence=evidence,
        metrics={},
        confidence=0.5,
    )


# ---------------------------------------------------------------------------
# Slicing


def test_slice_keeps_dated_works_up_to_cutoff_and_their_attachments() -> None:
    view = GraphView(
        nodes=[
            _work("old", 2018),
            _work("edge", 2020),
            _work("future", 2021),
            _work("undated", None),
            _concept("shared"),
            _concept("future_only"),
            {"id": "a1", "kind": "author", "label": "Ada", "data": {}},
        ],
        edges=[
            _cites("edge", "old"),
            _cites("future", "old"),
            _cites("future", "edge"),
            _cites("undated", "old"),
            _has("old", "shared"),
            _has("future", "shared"),
            _has("future", "future_only"),
            {"source": "edge", "target": "a1", "kind": "authored_by"},
        ],
    )
    view.nodes[0]["data"]["in_degree"] = 3

    sliced = slice_view(view, 2020)

    ids = {node["id"] for node in sliced.nodes}
    assert ids == {"old", "edge", "shared", "a1"}
    assert {(edge["source"], edge["target"], edge["kind"]) for edge in sliced.edges} == {
        ("edge", "old", "cites"),
        ("old", "shared", "has_concept"),
        ("edge", "a1", "authored_by"),
    }
    old = next(node for node in sliced.nodes if node["id"] == "old")
    edge = next(node for node in sliced.nodes if node["id"] == "edge")
    # Degrees are recounted on the slice so they do not reveal later citations.
    assert old["data"]["in_degree"] == 1
    assert edge["data"]["out_degree"] == 1
    # Pure: the input view is untouched.
    assert view.nodes[0]["data"]["in_degree"] == 3
    assert len(view.nodes) == 7


def test_slice_accepts_top_level_years_and_empty_views() -> None:
    view = GraphView(nodes=[{"id": "w", "kind": "work", "year": 2019}], edges=[])
    assert [node["id"] for node in slice_view(view, 2019).nodes] == ["w"]
    assert slice_view(view, 2018).nodes == []
    assert slice_view(GraphView(), 2020) == GraphView()


# ---------------------------------------------------------------------------
# Ranking statistics


def test_ranking_statistics() -> None:
    assert spearman([1, 2, 3], [10, 20, 30]) == pytest.approx(1.0)
    assert spearman([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
    assert spearman([1, 1, 1], [1, 2, 3]) is None
    assert spearman([1], [1]) is None
    # Top quartile with ties, never counting zero uptake as relevant.
    assert relevant_works({"a": 4, "b": 4, "c": 1, "d": 0}) == {"a", "b"}
    assert relevant_works({"a": 0, "b": 0}) == set()
    assert precision_at(["a", "b", "c"], {"a"}, 5) == pytest.approx(1 / 3)
    assert precision_at([], {"a"}, 5) is None


# ---------------------------------------------------------------------------
# Frontier check


def _frontier_project(future_cites: str) -> GraphView:
    """One old work and three works at T=2020; three later works all cite ``future_cites``.

    ``A`` is a fast-cited preprint, ``B`` is cited once inside the slice (by ``C``),
    so the frontier ranks ``A`` first while the local in-degree baseline ranks ``B``
    first.
    """

    preprint = _work("A", 2020, cited_by_count=500)
    preprint["data"]["work_type"] = "preprint"
    return GraphView(
        nodes=[
            _work("old", 2015),
            preprint,
            _work("B", 2020),
            _work("C", 2020),
            _work("f1", 2021),
            _work("f2", 2022),
            _work("f3", 2022),
            _work("late", 2023),
        ],
        edges=[
            _cites("A", "old"),
            _cites("B", "old"),
            _cites("C", "B"),
            _cites("f1", future_cites),
            _cites("f2", future_cites),
            _cites("f3", future_cites),
            # Outside the horizon: must not count as uptake.
            _cites("late", "B"),
        ],
    )


def test_frontier_work_cited_heavily_after_cutoff_scores_full_precision() -> None:
    result = backtest(_frontier_project("A"), 2020, ks=(1, 5))

    frontier = result["frontier"]
    assert frontier["top"][0]["work_id"] == "A"
    assert frontier["top"][0]["future_uptake"] == 3
    assert frontier["windowed_works"] == 3
    assert frontier["future_works"] == 3
    assert frontier["relevant_works"] == 1
    assert frontier["precision"]["@1"] == 1.0
    assert frontier["precision"]["@5"] == pytest.approx(1 / 3)
    assert frontier["spearman"] > 0
    # The in-slice in-degree baseline ranks B first and misses.
    assert frontier["baseline_local_in_degree_precision"]["@1"] == 0.0
    # Without cited_by_count the velocity component no longer lifts A.
    assert result["frontier_leak_free"]["precision"]["@1"] == 0.0


def test_frontier_work_not_cited_after_cutoff_scores_zero_precision() -> None:
    result = backtest(_frontier_project("C"), 2020, ks=(1,))

    frontier = result["frontier"]
    assert frontier["top"][0]["work_id"] == "A"
    assert frontier["top"][0]["future_uptake"] == 0
    assert frontier["precision"]["@1"] == 0.0
    assert frontier["spearman"] < 0


def test_default_cutoff_is_newest_year_minus_horizon() -> None:
    result = backtest(_frontier_project("A"))
    assert result["cutoff_year"] == 2021
    assert result["slice_works"] == 5


# ---------------------------------------------------------------------------
# Gap check


def _gap_project() -> tuple[GraphView, dict[str, set[str]]]:
    members = {
        "c1": {"a1", "a2", "a3"},
        "c2": {"b1", "b2", "b3"},
        "c3": {"s1", "s2", "s3", "s4", "s5"},
    }
    slice_works = sorted(set().union(*members.values()))
    nodes = [_work(work_id, 2018) for work_id in slice_works]
    nodes += [_work("f1", 2021), _work("f2", 2022), _work("f3", 2025)]
    nodes += [_concept(concept_id) for concept_id in ("x", "y", "z")]
    edges = [
        # f1 bridges c1 and c2 and fills the (x, y) void.
        _cites("f1", "a1"),
        _cites("f1", "b1"),
        _has("f1", "x"),
        _has("f1", "y"),
        # f2 cites into c1 only; one future citer of the stagnating c3 is still allowed.
        _cites("f2", "a2"),
        _cites("f2", "s1"),
        # f3 is beyond the horizon: its co-citation of c2 and c3 must not count.
        _cites("f3", "b2"),
        _cites("f3", "s2"),
        _has("f3", "x"),
        _has("f3", "z"),
    ]
    for work_id in ("a1", "a2", "a3"):
        edges.append(_has(work_id, "x"))
    for work_id in ("b1", "b2", "b3"):
        edges.append(_has(work_id, "y"))
    for work_id in ("s1", "s2", "s3"):
        edges.append(_has(work_id, "z"))
    view = GraphView(nodes=nodes, edges=edges)
    return view, members


def test_gap_hypotheses_are_checked_against_future_works() -> None:
    view, members = _gap_project()
    gaps = [
        _gap("bridge-yes", "bridging", cluster_ids=["c1", "c2"]),
        _gap("bridge-no", "bridging", cluster_ids=["c2", "c3"]),
        _gap("void-yes", "matrix_void", concept_ids=["x", "y"]),
        _gap("void-no", "matrix_void", concept_ids=["x", "z"]),
        _gap("stagnant-yes", "stagnation", cluster_ids=["c3"]),
        _gap("stagnant-no", "stagnation", cluster_ids=["c1"]),
    ]

    result = evaluate_gaps(gaps, members, slice_view(view, 2020), view, 2020)

    hits = {row["id"]: row["hit"] for row in result["hypotheses"]}
    assert hits == {
        "bridge-yes": True,
        "bridge-no": False,
        "void-yes": True,
        "void-no": False,
        "stagnant-yes": True,
        "stagnant-no": False,
    }
    by_type = result["by_type"]
    assert result["future_works"] == 2
    for gap_type in ("bridging", "matrix_void", "stagnation"):
        assert by_type[gap_type]["hypotheses"] == 2
        assert by_type[gap_type]["hits"] == 1
        assert by_type[gap_type]["rate"] == 0.5


def test_random_baseline_is_deterministic_for_a_seed() -> None:
    view, members = _gap_project()
    sliced = slice_view(view, 2020)
    gaps = [_gap("bridge", "bridging", cluster_ids=["c1", "c2"])]

    first = evaluate_gaps(gaps, members, sliced, view, 2020, seed=7)
    second = evaluate_gaps(gaps, members, sliced, view, 2020, seed=7)
    assert first == second
    rate = first["by_type"]["bridging"]["baseline_rate"]
    assert first["by_type"]["bridging"]["baseline_population"] == 3
    # One pick out of three pairs over 20 draws: a multiple of 1/20.
    assert rate is not None
    assert 0.0 <= rate <= 1.0
    assert (rate * 20) == pytest.approx(round(rate * 20))

    # Sampling the whole population gives the exact population rate for any seed:
    # (c1, c2) via f1 and (c1, c3) via f2 are co-cited, (c2, c3) is not.
    all_pairs = [
        _gap("p12", "bridging", cluster_ids=["c1", "c2"]),
        _gap("p13", "bridging", cluster_ids=["c1", "c3"]),
        _gap("p23", "bridging", cluster_ids=["c2", "c3"]),
    ]
    for seed in (1, 2, 3):
        result = evaluate_gaps(all_pairs, members, sliced, view, 2020, seed=seed)
        assert result["by_type"]["bridging"]["baseline_rate"] == pytest.approx(2 / 3)
    # Concepts x, y, z each have three slice works and never co-occur: three void pairs.
    assert result["by_type"]["matrix_void"]["baseline_population"] == 3
    # Only c3 is large enough to be a stagnation candidate.
    assert result["by_type"]["stagnation"]["baseline_population"] == 1
    # No hypotheses of a type: no rate and no baseline.
    assert result["by_type"]["matrix_void"]["rate"] is None
    assert result["by_type"]["matrix_void"]["baseline_rate"] is None


# ---------------------------------------------------------------------------
# Robustness


@pytest.mark.parametrize(
    ("view", "cutoff"),
    [
        (GraphView(), None),
        (GraphView(nodes=[_work("only", None)]), None),
        (GraphView(nodes=[_work("only", 2020)]), None),
        (GraphView(nodes=[_work("a", 2020), _work("b", 2021)], edges=[_cites("b", "a")]), 2020),
        # Cutoff before every work: an empty slice.
        (GraphView(nodes=[_work("a", 2020)]), 2010),
    ],
)
def test_empty_and_small_projects_do_not_crash(view: GraphView, cutoff: int | None) -> None:
    result = backtest(view, cutoff)
    json.dumps(result)
    if result["cutoff_year"] is None:
        assert result["frontier"] is None
    else:
        assert set(result["gaps"]["by_type"]) == {"bridging", "matrix_void", "stagnation"}


def test_backtest_runs_the_gap_detector_end_to_end() -> None:
    view, _members = _gap_project()
    result = backtest(view, 2020)
    assert result["slice_works"] == 11
    assert result["gaps"]["future_works"] == 2
    json.dumps(result)


def test_cli_golden_run_writes_results_without_local_paths(tmp_path: Path) -> None:
    assert run_backtest_main(["--golden", "--results-dir", str(tmp_path)]) == 0

    outputs = sorted(path.name for path in tmp_path.iterdir())
    assert "summary.md" in outputs
    json_files = [name for name in outputs if name.endswith(".json")]
    assert len(json_files) == 1 and json_files[0].startswith("golden-T")
    for path in tmp_path.iterdir():
        text = path.read_text(encoding="utf-8")
        assert str(REPO_ROOT) not in text
        assert str(Path.home()) not in text
    result = json.loads((tmp_path / json_files[0]).read_text(encoding="utf-8"))
    assert result["key"] == "golden"
    assert result["frontier"]["windowed_works"] > 0
