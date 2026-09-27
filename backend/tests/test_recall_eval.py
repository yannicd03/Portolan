from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from eval.recall.metrics import compute_metrics, identity_keys, shares_identity  # noqa: E402
from eval.recall.run_recall import fetch_ground_truth, run_survey  # noqa: E402

from portolan.research import ResearchSources  # noqa: E402


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
