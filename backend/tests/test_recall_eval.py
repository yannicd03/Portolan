from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest  # noqa: E402
from eval.recall import screen_eval  # noqa: E402
from eval.recall.metrics import compute_metrics, identity_keys, shares_identity  # noqa: E402
from eval.recall.run_recall import (  # noqa: E402
    POOL_FORMAT,
    fetch_ground_truth,
    load_pool,
    run_survey,
    write_pool,
)
from eval.recall.screen_eval import evaluate_pool, load_pools, prepare_pool  # noqa: E402

from portolan.research import HeuristicScreener, ResearchRequest, ResearchSources  # noqa: E402


def test_identity_matching_accepts_openalex_doi_and_arxiv_spellings() -> None:
    truth = {
        "openalex_id": "https://openalex.org/W123",
        "doi": "10.1000/Example.",
        "arxiv_id": "2401.00001v2",
    }
    assert identity_keys("arxiv:2401.00001") >= {("arxiv", "2401.00001")}
    assert shares_identity(truth, {"identifiers": {"arxiv": "arxiv:2401.00001"}})
    assert shares_identity(truth, {"identifiers": {"openalex": "openalex:W123"}})
    assert shares_identity(truth, {"identifiers": {"doi": "doi:10.1000/example"}})


def test_recall_and_precision_use_set_intersections() -> None:
    ground_truth = [
        {"openalex_id": "W1", "title": "one"},
        {"doi": "10.1000/two", "title": "two"},
        {"arxiv_id": "2401.00003", "title": "three"},
    ]
    candidates = [
        {"identifiers": {"doi": "10.1000/two"}},
        {"identifiers": {"arxiv": "arxiv:2401.00003"}},
        {"openalex_id": "W404"},
    ]
    included = [{"doi": "10.1000/two"}, {"openalex_id": "W404"}]

    metrics = compute_metrics(ground_truth, candidates, included)

    assert metrics.ground_truth_count == 3
    assert metrics.candidates_count == 3
    assert metrics.included_count == 2
    assert metrics.recall_candidates == 2 / 3
    assert metrics.recall_included == 1 / 3
    assert metrics.precision_included == 1 / 2
    assert [item["title"] for item in metrics.missed_references] == ["one"]


def test_excluded_survey_is_removed_from_metrics() -> None:
    survey = {"arxiv_id": "2312.10997", "title": "Survey"}
    metrics = compute_metrics(
        [survey, {"openalex_id": "W1", "title": "Reference"}],
        [survey, {"openalex_id": "W1"}],
        [survey, {"openalex_id": "W1"}],
        excluded=[survey],
    )

    assert metrics.ground_truth_count == 1
    assert metrics.candidates_count == 1
    assert metrics.included_count == 1
    assert metrics.recall_candidates == 1.0
    assert metrics.precision_included == 1.0


def test_empty_ground_truth_has_zero_metrics() -> None:
    metrics = compute_metrics([], [{"openalex_id": "W1"}], [])

    assert metrics.ground_truth_count == 0
    assert metrics.recall_candidates == 0.0
    assert metrics.recall_included == 0.0
    assert metrics.precision_included == 0.0


class _FakeOpenAlex:
    def lookup_many(self, identifiers: list[str]) -> list[dict[str, Any]]:
        return []


class _FakeSemanticScholar:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int]] = []

    def lookup_by_id(self, identifier: str) -> dict[str, Any] | None:
        if identifier == "arxiv:2312.10997":
            return {"identifiers": {"s2": "S-SURVEY"}, "title": "Survey"}
        return None

    def references(self, identifier: str, *, limit: int, offset: int) -> list[dict[str, Any]]:
        self.calls.append((identifier, limit, offset))
        if offset:
            return []
        return [
            {"paper": {"paperId": "S1", "externalIds": {"ArXiv": "2001.00001"}}},
            {"paper": {"paperId": "S2", "externalIds": {"DOI": "10.1000/two"}}},
        ]


def test_semantic_scholar_reference_fallback_unwraps_papers() -> None:
    semantic = _FakeSemanticScholar()
    sources = SimpleNamespace(openalex=_FakeOpenAlex(), semanticscholar=semantic)
    survey = {"identifiers": {"arxiv": "2312.10997"}}

    references = fetch_ground_truth(survey, sources)

    assert len(references) == 2
    assert identity_keys(references[0]) == {("arxiv", "2001.00001")}
    assert identity_keys(references[1]) == {("doi", "10.1000/two")}
    assert semantic.calls == [("S-SURVEY", 100, 0)]


def _pipeline_work(
    work_id: str,
    title: str,
    *,
    references: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "source": "openalex",
        "identifiers": {"openalex": work_id, "doi": None, "arxiv": None},
        "openalex_id": work_id,
        "title": title,
        "year": 2024,
        "abstract": f"Research on {title.casefold()}.",
        "venue": "Test Journal",
        "publication_types": ["article"],
        "authors": [],
        "keywords": [],
        "referenced_works": references or [],
        "cited_by_count": 0,
        "pdf_candidates": [],
    }


class _PipelineOpenAlex:
    def __init__(self, works: list[dict[str, Any]]) -> None:
        self.works = {work["openalex_id"]: work for work in works}

    def _find(self, identifier: str) -> dict[str, Any] | None:
        key = identifier.rsplit("/", 1)[-1].removeprefix("openalex:")
        work = self.works.get(key)
        return None if work is None else dict(work)

    def lookup(self, identifier: str) -> dict[str, Any] | None:
        return self._find(identifier)

    def lookup_many(self, identifiers: list[str]) -> list[dict[str, Any]]:
        return [work for identifier in identifiers if (work := self._find(identifier))]

    def search(
        self,
        query: str,
        *,
        limit: int,
        from_year: int | None = None,
        to_year: int | None = None,
    ) -> list[dict[str, Any]]:
        del query, from_year, to_year
        return [work for work in (self._find("W100"), self._find("W102")) if work][:limit]


def test_run_survey_excludes_survey_search_hit_from_candidates_and_graph() -> None:
    survey = _pipeline_work("W100", "A Test Survey")
    survey["referenced_works"] = ["W102"]
    seed = _pipeline_work("W101", "A Seed Paper")
    reference = _pipeline_work("W102", "A Reference Paper")
    sources = ResearchSources(_PipelineOpenAlex([survey, seed, reference]))

    result = run_survey(
        {
            "key": "test",
            "survey": "W100",
            "title": "A Test Survey",
            "query": "test topic",
            "seeds": ["W101"],
            "to_year": 2024,
        },
        sources,
        max_works=10,
        snowball_depth=0,
    )

    assert result["ground_truth_count"] == 1
    assert "openalex:W100" not in result["candidate_identities"]
    assert "openalex:W100" not in result["included_identities"]
    assert result["report"]["excluded"] == 1


def test_run_survey_dumps_pool_that_round_trips(tmp_path: Path) -> None:
    survey = _pipeline_work("W100", "A Test Survey")
    survey["referenced_works"] = ["W102", "W103"]
    seed = _pipeline_work("W101", "A Seed Paper", references=["W103", "W104"])
    reference = _pipeline_work("W102", "A Reference Paper", references=["W103"])
    shared = _pipeline_work("W103", "A Shared Reference")
    other = _pipeline_work("W104", "An Unrelated Paper")
    sources = ResearchSources(_PipelineOpenAlex([survey, seed, reference, shared, other]))
    spec = {
        "key": "test",
        "survey": "W100",
        "title": "A Test Survey",
        "query": "reference paper",
        "seeds": ["W101"],
        "to_year": 2024,
    }

    result = run_survey(spec, sources, max_works=3, snowball_depth=1, pool_dir=tmp_path)

    pool = load_pool(tmp_path / "test.pool.json")
    assert pool["key"] == "test"
    assert pool["seed_keys"] == ["openalex:W101"]
    assert pool["core_keys"] == ["openalex:W101", "openalex:W102"]
    assert pool["core_reference_counts"]["openalex:W103"] == 2
    assert pool["ground_truth_identities"] == ["openalex:W102", "openalex:W103"]
    assert all("abstract" not in record for record in pool["ground_truth"])
    by_key = {item["key"]: item for item in pool["candidates"]}
    assert by_key["openalex:W102"]["search_rank"] == 2
    assert by_key["openalex:W102"]["core"] is True
    assert by_key["openalex:W101"]["is_seed"] is True
    assert by_key["openalex:W103"]["depth"] == 1
    assert by_key["openalex:W103"]["record"]["abstract"]
    assert "openalex:W100" not in by_key

    prepared = prepare_pool(pool)
    assert prepared.context["core_reference_counts"] == pool["core_reference_counts"]
    replay = evaluate_pool(prepared, HeuristicScreener())
    included = sorted(result["included_identities"])
    assert sorted(f"{key}" for key in replay["selected_keys"]) == included
    assert replay["recall_included"] == result["recall_included"]
    assert replay["precision_included"] == result["precision_included"]
    assert replay["recall_candidates"] == result["recall_candidates"]


def test_load_pool_rejects_other_json(tmp_path: Path) -> None:
    path = tmp_path / "x.pool.json"
    path.write_text(json.dumps({"format": 99}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_pool(path)


_ON = ["speculative", "decoding", "draft", "verification", "language", "inference", "tokens"]
_OFF = ["soil", "chemistry", "wetland", "protein", "folding", "market", "poetry"]


def _synthetic_pool(key: str, seed: int, size: int = 60) -> dict[str, Any]:
    """A deterministic pool: on-topic works cite each other and are the truth."""

    rng = random.Random(seed)
    ids = [f"W{seed}{index:03d}" for index in range(size)]
    on_topic = {wid for index, wid in enumerate(ids) if index % 3 != 0}
    candidates: list[dict[str, Any]] = []
    for index, wid in enumerate(ids):
        words = _ON if wid in on_topic else _OFF
        title = " ".join(rng.sample(words, 3))
        neighbours = sorted(on_topic if wid in on_topic else set(ids) - on_topic)
        refs = rng.sample(neighbours, min(len(neighbours), rng.randint(0, 5)))
        work = _pipeline_work(wid, title.title(), references=[r for r in refs if r != wid])
        work["abstract"] = " ".join(rng.choice(words) for _ in range(12))
        work["cited_by_count"] = rng.choice([0, 3, 20, 150, 900])
        work["year"] = rng.choice([2021, 2022, 2023, 2025])
        candidates.append(
            {
                "key": f"openalex:{wid}",
                "discovered_via": "seed" if index in (1, 2) else "search",
                "depth": 0 if index < 12 else rng.choice([1, 2]),
                "is_seed": index in (1, 2),
                "core": 3 <= index < 8,
                "frontier_seed": index < 8,
                "search_rank": index - 2 if 3 <= index < 12 else None,
                "record": work,
            }
        )
    truth = sorted(wid for wid in on_topic if rng.random() < 0.7) + [f"W{seed}999"]
    request = ResearchRequest(
        seeds=[ids[1], ids[2]],
        query="speculative decoding inference",
        max_works=15,
        to_year=2023,
    )
    return {
        "format": POOL_FORMAT,
        "key": key,
        "request": request.model_dump(mode="json"),
        "seed_keys": [f"openalex:{ids[1]}", f"openalex:{ids[2]}"],
        "core_keys": [f"openalex:{wid}" for wid in ids[1:8]],
        "core_reference_counts": {},
        "core_size": 7,
        "relation_pairs": [],
        "candidates": candidates,
        "survey_record": {"openalex_id": f"W{seed}000"},
        "ground_truth": [{"openalex_id": wid, "title": wid} for wid in truth],
        "ground_truth_identities": sorted(f"openalex:{wid}" for wid in truth),
    }


def _write_pools(directory: Path) -> list[dict[str, Any]]:
    pools = [_synthetic_pool(key, seed) for key, seed in (("a", 1), ("b", 2), ("c", 3))]
    for pool in pools:
        write_pool(directory / f"{pool['key']}.pool.json", pool)
    return pools


def test_screen_eval_is_deterministic(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_pools(tmp_path)

    first = [evaluate_pool(pool, HeuristicScreener()) for pool in load_pools(tmp_path)]
    second = [evaluate_pool(pool, HeuristicScreener()) for pool in load_pools(tmp_path)]
    assert first == second
    assert [result["key"] for result in first] == ["a", "b", "c"]
    assert all(result["included"] == 15 for result in first)
    assert all(0 < result["recall_included"] <= 1 for result in first)

    assert screen_eval.main(["--pools", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert screen_eval.main(["--pools", str(tmp_path)]) == 0
    assert capsys.readouterr().out == output
    assert "| macro |" in output

    assert screen_eval.main(["--pools", str(tmp_path), "--fast"]) == 0
    assert capsys.readouterr().out == output
    fast = [screen_eval.FastPool(pool).result(HeuristicScreener()) for pool in load_pools(tmp_path)]
    for fast_result, result in zip(fast, first, strict=True):
        assert fast_result["selected_keys"] == result["selected_keys"]
        assert fast_result.keys() == result.keys()
        for name, value in result.items():
            if name not in ("key", "selected_keys"):
                assert fast_result[name] == pytest.approx(value), name

    json_path = tmp_path / "out.json"
    screen_eval.main(
        [
            "--pools",
            str(tmp_path),
            "--screener",
            "legacy",
            "--weight",
            "link=0",
            "--json",
            str(json_path),
        ]
    )
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["weights"]["link"] == 0
    assert data["weights"]["semantic"] == 0


def test_screen_eval_honours_max_works_and_min_score(tmp_path: Path) -> None:
    _write_pools(tmp_path)
    pool = load_pools(tmp_path, "b")[0]

    small = evaluate_pool(pool, HeuristicScreener(), max_works=5)
    assert small["included"] == 5
    strict = evaluate_pool(pool, HeuristicScreener(), min_score=1.0)
    assert strict["selected_keys"] == sorted(pool.seed_keys)


@pytest.mark.parametrize(
    "screener",
    [
        HeuristicScreener(),
        HeuristicScreener(token_weight=0.45, link_weight=0.2, citation_weight=0.1),
        HeuristicScreener(
            token_weight=0.45, link_weight=0.2, citation_weight=0.1, cocitation_weight=0.25
        ),
        HeuristicScreener(semantic_weight=0.5, link_weight=0.0, coupling_weight=0.5),
        HeuristicScreener(
            link_weight=1.0,
            semantic_weight=0.0,
            citation_weight=0.0,
            cocitation_weight=0.0,
            coupling_weight=0.0,
        ),
        HeuristicScreener(recency_weight=0.2),
    ],
)
def test_fast_selection_matches_runner_selection(
    tmp_path: Path, screener: HeuristicScreener
) -> None:
    _write_pools(tmp_path)
    for pool in load_pools(tmp_path):
        fast = screen_eval.FastPool(pool)
        for max_works, min_score in ((15, 0.15), (40, 0.0), (8, 0.4)):
            reference = evaluate_pool(pool, screener, max_works=max_works, min_score=min_score)
            indices = fast.select(screener, max_works, min_score)
            assert [fast.order[index].key for index in indices] == reference["selected_keys"]
            metrics = fast.metrics(indices)
            assert metrics["included"] == reference["included"]
            assert metrics["recall_included"] == pytest.approx(reference["recall_included"])
            assert metrics["precision_included"] == pytest.approx(reference["precision_included"])


def test_weight_grid_is_normalised_and_deduplicated() -> None:
    grid = screen_eval.weight_grid((0.0, 1.0, 2.0))

    assert len(grid) == 3**5 - 2**5  # all-zero dropped; (2,2,...) etc. collapse
    assert all(sum(weights.values()) == pytest.approx(1.0) for weights in grid)
    assert all(set(weights) == set(screen_eval.GRID_COMPONENTS) for weights in grid)
    assert len({tuple(weights.values()) for weights in grid}) == len(grid)


def test_leave_one_out_chooses_weights_on_training_surveys_only() -> None:
    def row(name: float, a: float, b: float, c: float) -> dict[str, Any]:
        weights = dict.fromkeys(screen_eval.GRID_COMPONENTS, 0.0)
        weights["semantic"] = name
        weights["link"] = 1.0 - name
        return {
            "weights": weights,
            "results": {
                key: {"recall_included": value, "precision_included": value / 2}
                for key, value in (("a", a), ("b", b), ("c", c))
            },
        }

    rows = [row(0.2, 0.9, 0.9, 0.1), row(0.5, 0.5, 0.5, 0.9), row(0.8, 0.8, 0.1, 0.8)]
    folds = screen_eval.leave_one_out(rows, ["a", "b", "c"])

    assert [fold["held_out"] for fold in folds] == ["a", "b", "c"]
    assert [fold["train"] for fold in folds] == [["b", "c"], ["a", "c"], ["a", "b"]]
    # a held out: row 1 wins on b, c (0.7) over row 0 (0.5) and row 2 (0.45).
    assert folds[0]["weights"]["semantic"] == 0.5
    assert folds[0]["held_out_recall"] == 0.5
    # b held out: row 2 (0.8) beats row 1 (0.7); c held out: row 0 (0.9).
    assert folds[1]["weights"]["semantic"] == 0.8
    assert folds[1]["held_out_recall"] == 0.1
    assert folds[2]["weights"]["semantic"] == 0.2
    assert folds[2]["held_out_recall"] == pytest.approx(0.1)
    assert folds[2]["held_out_precision"] == pytest.approx(0.05)

    robust = screen_eval.robust_weights(folds)
    assert robust["average"]["semantic"] == pytest.approx(0.5)
    assert robust["average"]["link"] == pytest.approx(0.5)
    assert "majority" not in robust
    robust = screen_eval.robust_weights([folds[0], folds[0], folds[1]])
    assert robust["majority"]["semantic"] == 0.5


def test_grid_report_runs_offline(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_pools(tmp_path)

    lines, data = screen_eval.grid_report(load_pools(tmp_path), values=(0.0, 1.0))

    assert data["grid_size"] == 2**5 - 1
    assert [fold["held_out"] for fold in data["folds"]] == ["a", "b", "c"]
    assert set(data["references"]) >= {"legacy", "default", "robust (average)"}
    assert any("Leave-one-survey-out" in line for line in lines)
    assert screen_eval.main(["--pools", str(tmp_path), "--grid", "--grid-values", "0", "1"]) == 0
    assert "| mean |" in capsys.readouterr().out
