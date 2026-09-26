from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "eval" / "golden" / "build_golden.py"
SPEC = importlib.util.spec_from_file_location("arg_golden_build", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
builder = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = builder
SPEC.loader.exec_module(builder)


class FakeArxiv:
    def __init__(self, records: dict[str, dict[str, Any]]) -> None:
        self.records = records
        self.lookup_many_calls: list[list[str]] = []

    def lookup_many(self, arxiv_ids: list[str]) -> list[dict[str, Any]]:
        self.lookup_many_calls.append(list(arxiv_ids))
        return [self.records[arxiv_id] for arxiv_id in arxiv_ids if arxiv_id in self.records]

    def lookup(self, arxiv_id: str) -> dict[str, Any] | None:
        return self.records.get(arxiv_id)


class FakeCrossref:
    def __init__(self, records: dict[str, dict[str, Any]] | None = None) -> None:
        self.records = records or {}

    def lookup(self, doi: str) -> dict[str, Any] | None:
        return self.records.get(doi)

    def search_title(self, title: str) -> list[dict[str, Any]]:
        return []


class FakeSemanticScholar:
    def __init__(
        self,
        batch_results: list[dict[str, Any]] | None = None,
        title_results: dict[str, list[dict[str, Any]]] | None = None,
        references: dict[str, list[dict[str, Any]]] | None = None,
        api_key: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.batch_results = batch_results or []
        self.title_results = title_results or {}
        self.reference_results = references or {}
        self.batch_calls: list[list[str]] = []
        self.title_calls: list[str] = []
        self.reference_calls: list[str] = []
        self.citation_calls: list[str] = []

    def lookup_many(self, identifiers: list[str]) -> list[dict[str, Any]]:
        self.batch_calls.append(list(identifiers))
        return self.batch_results

    def search_title(self, title: str) -> list[dict[str, Any]]:
        self.title_calls.append(title)
        return self.title_results.get(title, [])

    def references(self, paper_id: str) -> list[dict[str, Any]]:
        self.reference_calls.append(paper_id)
        if not self.api_key:
            raise AssertionError("keyless build must not call Semantic Scholar references")
        return self.reference_results.get(paper_id, [])

    def citations(self, paper_id: str) -> list[dict[str, Any]]:
        self.citation_calls.append(paper_id)
        if not self.api_key:
            raise AssertionError("keyless build must not call Semantic Scholar citations")
        return []


def _work(
    arxiv_id: str,
    s2_id: str,
    title: str,
    year: int,
    *,
    source_tier: str | None = None,
    is_survey: bool | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "identifiers": {"arxiv": arxiv_id, "s2": s2_id},
        "title": title,
        "year": year,
        "abstract": f"Abstract for {title}.",
        "venue": "arXiv",
        "publication_types": ["Preprint"],
        "open_access_pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
        "tldr": None,
        "categories": ["cs.AI"],
        "fetched_at": "2026-09-22T00:00:00+00:00",
    }
    if source_tier is not None:
        record["source_tier"] = source_tier
    if is_survey is not None:
        record["is_survey"] = is_survey
    return record


def _three_seed_fixture(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    alpha = _work("1000.00001", "s2-alpha", "Alpha Retrieval", 2020)
    beta = _work("1000.00002", "s2-beta", "Beta Graph", 2021)
    gamma = _work("1000.00003", "s2-gamma", "Gamma Reasoning", 2022)
    alpha["references"] = [
        {"paperId": "s2-beta", "title": "Beta Graph", "externalIds": {"ArXiv": "1000.00002"}},
        {"paperId": "outside", "title": "Outside the corpus"},
    ]
    beta["references"] = [
        {
            "paperId": "s2-gamma",
            "title": "Gamma Reasoning",
            "externalIds": {"ArXiv": "1000.00003"},
        }
    ]
    seeds = """\
version: 1
papers:
  - key: alpha
    title: "Alpha Retrieval"
    year: 2020
    arxiv: "1000.00001"
    expect: {source_tier: preprint, is_survey: false}
  - key: beta
    title: "Beta Graph"
    year: 2021
    arxiv: "1000.00002"
    expect: {source_tier: preprint, is_survey: false}
  - key: gamma
    title: "Gamma Reasoning"
    year: 2022
    arxiv: "1000.00003"
    expect: {source_tier: preprint, is_survey: false}
"""
    seed_path = tmp_path / "seeds.yaml"
    seed_path.write_text(seeds, encoding="utf-8")
    bundle = {
        "arxiv": FakeArxiv(
            {
                "1000.00001": alpha,
                "1000.00002": beta,
                "1000.00003": gamma,
            }
        ),
        "semanticscholar": FakeSemanticScholar(
            batch_results=[alpha, beta, gamma],
            references={
                "s2-alpha": [
                    {
                        "paper": {"paperId": "s2-beta"},
                        "intents": ["background", "usesMethodIn"],
                        "isInfluential": True,
                        "contexts": ["Alpha builds on Beta.", "A second context."],
                    }
                ],
                "s2-beta": [
                    {
                        "paper": {"paperId": "s2-gamma"},
                        "intents": ["extends"],
                        "isInfluential": False,
                        "contexts": ["Beta extends Gamma."],
                    }
                ],
            },
        ),
        "crossref": FakeCrossref(),
    }
    return seed_path, bundle


def test_title_normalization_and_strict_near_miss() -> None:
    assert builder.normalize_title("BERT: Pre-training — A Study") == "bertpretrainingastudy"
    assert builder.title_similarity("Attention Is All You Need", "Attention Is All You Need") == 1.0
    assert (
        builder.title_similarity("Attention Is All You Need", "Attention Is All")
        < builder.TITLE_MATCH_THRESHOLD
    )


def test_full_builder_writes_verified_outputs_and_citation_edges(tmp_path: Path) -> None:
    seed_path, bundle = _three_seed_fixture(tmp_path)
    output_dir = tmp_path / "out"
    result = builder.build_golden(seed_path, output_dir, tmp_path / "cache", adapters=bundle)

    assert result.ok
    works = json.loads((output_dir / "works.json").read_text(encoding="utf-8"))
    citations = json.loads((output_dir / "citations.json").read_text(encoding="utf-8"))
    report = (output_dir / "resolution_report.md").read_text(encoding="utf-8")
    assert set(works) == {"alpha", "beta", "gamma"}
    assert works["alpha"]["provenance"]["title"]["source"] == "semanticscholar"
    assert [(edge["citing"], edge["cited"]) for edge in citations] == [
        ("alpha", "beta"),
        ("beta", "gamma"),
    ]
    assert "intents" not in citations[0]
    assert "citationFunction" not in citations[0]
    assert "isInfluential" not in citations[0]
    assert bundle["semanticscholar"].reference_calls == []
    assert bundle["semanticscholar"].citation_calls == []
    assert bundle["semanticscholar"].batch_calls == [
        ["ARXIV:1000.00001", "ARXIV:1000.00002", "ARXIV:1000.00003"]
    ]
    assert "alpha — PASS" in report
    assert (output_dir / "resolution_report.md").exists()


def test_resolution_batches_hints_and_searches_only_unhinted_seed(tmp_path: Path) -> None:
    hinted = _work("1000.00001", "s2-hinted", "Hinted Paper", 2020)
    searched = _work("1000.00002", "s2-searched", "Searched Paper", 2021)
    seed_path = tmp_path / "seeds.yaml"
    seed_path.write_text(
        """\
version: 1
papers:
  - key: hinted
    title: "Hinted Paper"
    year: 2020
    arxiv: "1000.00001"
    expect: {source_tier: preprint, is_survey: false}
  - key: searched
    title: "Searched Paper"
    year: 2021
    arxiv: null
    expect: {source_tier: preprint, is_survey: false}
""",
        encoding="utf-8",
    )
    s2 = FakeSemanticScholar(
        batch_results=[hinted],
        title_results={"Searched Paper": [searched]},
    )
    bundle = {
        "arxiv": FakeArxiv({"1000.00001": hinted}),
        "semanticscholar": s2,
        "crossref": FakeCrossref(),
    }

    result = builder.build_golden(
        seed_path,
        tmp_path / "out",
        tmp_path / "cache",
        adapters=bundle,
        no_arxiv=True,
    )

    assert result.ok
    assert s2.batch_calls == [["ARXIV:1000.00001"]]
    assert s2.title_calls == ["Searched Paper"]


def test_cli_zero_exit_for_verified_subset(tmp_path: Path, monkeypatch: Any) -> None:
    seed_path, bundle = _three_seed_fixture(tmp_path)
    monkeypatch.setattr(builder, "create_adapters", lambda cache_dir, offline: bundle)
    output_dir = tmp_path / "cli-out"
    result = CliRunner().invoke(
        builder.app,
        [
            "--seeds",
            str(seed_path),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--output-dir",
            str(output_dir),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert (output_dir / "works.json").exists()
    assert "HTTP requests:" in result.stdout


def test_near_miss_and_expect_mismatch_fail_loudly(tmp_path: Path) -> None:
    seeds = """\
version: 1
papers:
  - key: near_miss
    title: "A Precise Research Paper"
    year: 2020
    arxiv: "2000.00001"
    expect: {source_tier: preprint, is_survey: false}
  - key: wrong_expectation
    title: "Correct Paper"
    year: 2020
    arxiv: "2000.00002"
    expect: {source_tier: peerReviewed, is_survey: false}
"""
    seed_path = tmp_path / "failing-seeds.yaml"
    seed_path.write_text(seeds, encoding="utf-8")
    bundle = {
        "arxiv": FakeArxiv(
            {
                "2000.00001": _work("2000.00001", "s2-near", "A Different Paper", 2020),
                "2000.00002": _work("2000.00002", "s2-correct", "Correct Paper", 2020),
            }
        ),
        "semanticscholar": FakeSemanticScholar(
            batch_results=[
                _work("2000.00001", "s2-near", "A Different Paper", 2020),
                _work("2000.00002", "s2-correct", "Correct Paper", 2020),
            ]
        ),
        "crossref": FakeCrossref(),
    }
    result = builder.build_golden(seed_path, tmp_path / "out", tmp_path / "cache", adapters=bundle)
    assert not result.ok
    assert {failure.split(":", 1)[0] for failure in result.failures} == {
        "near_miss",
        "wrong_expectation",
    }
    report = (tmp_path / "out" / "resolution_report.md").read_text(encoding="utf-8")
    assert "near_miss — FAILURE" in report
    assert "wrong_expectation — FAILURE" in report


def test_ambiguous_title_search_is_a_failure(tmp_path: Path) -> None:
    seeds = """\
version: 1
papers:
  - key: ambiguous
    title: "A Shared Research Title"
    year: 2020
    arxiv: null
    expect: {source_tier: preprint, is_survey: false}
"""
    seed_path = tmp_path / "ambiguous-seeds.yaml"
    seed_path.write_text(seeds, encoding="utf-8")
    candidate_a = _work("3000.00001", "s2-a", "A Shared Research Title", 2020)
    candidate_b = _work("3000.00002", "s2-b", "A Shared Research Title", 2020)
    bundle = {
        "arxiv": FakeArxiv({}),
        "semanticscholar": FakeSemanticScholar(
            title_results={"A Shared Research Title": [candidate_a, candidate_b]}
        ),
        "crossref": FakeCrossref(),
    }
    result = builder.build_golden(seed_path, tmp_path / "out", tmp_path / "cache", adapters=bundle)
    assert not result.ok
    assert "ambiguous search" in result.failures[0]


def test_arxiv_406_is_a_warning_not_a_seed_failure(tmp_path: Path) -> None:
    seed_path = tmp_path / "seeds.yaml"
    seed_path.write_text(
        """\
version: 1
papers:
  - key: alpha
    title: "Alpha Retrieval"
    year: 2020
    arxiv: "1000.00001"
    expect: {source_tier: preprint, is_survey: false}
""",
        encoding="utf-8",
    )

    class ThrottledArxiv(FakeArxiv):
        def lookup_many(self, arxiv_ids: list[str]) -> list[dict[str, Any]]:
            self.lookup_many_calls.append(list(arxiv_ids))
            raise RuntimeError("retryable HTTP status 406 for https://export.arxiv.org/api/query")

    record = _work("1000.00001", "s2-alpha", "Alpha Retrieval", 2020)
    bundle = {
        "arxiv": ThrottledArxiv({}),
        "semanticscholar": FakeSemanticScholar(batch_results=[record]),
        "crossref": FakeCrossref(),
    }
    result = builder.build_golden(seed_path, tmp_path / "out", tmp_path / "cache", adapters=bundle)

    assert result.ok
    report = (tmp_path / "out" / "resolution_report.md").read_text(encoding="utf-8")
    assert "alpha — PASS" in report
    assert "arxiv enrichment warning" in report


def test_no_arxiv_skips_optional_enrichment(tmp_path: Path) -> None:
    seed_path, bundle = _three_seed_fixture(tmp_path)
    result = builder.build_golden(
        seed_path,
        tmp_path / "out",
        tmp_path / "cache",
        adapters=bundle,
        no_arxiv=True,
    )

    assert result.ok
    assert bundle["arxiv"].lookup_many_calls == []
    assert "arxiv=0" in result.request_summary


def test_with_intents_requires_key_and_adds_optional_metadata(tmp_path: Path) -> None:
    seed_path, bundle = _three_seed_fixture(tmp_path)
    s2 = bundle["semanticscholar"]
    s2.api_key = "fake-key"
    output_dir = tmp_path / "out"
    result = builder.build_golden(
        seed_path,
        output_dir,
        tmp_path / "cache",
        adapters=bundle,
        with_intents=True,
    )

    assert result.ok
    citations = json.loads((output_dir / "citations.json").read_text(encoding="utf-8"))
    assert citations[0]["intents"] == ["background", "usesMethodIn"]
    assert citations[0]["citationFunction"] == "background"
    assert citations[0]["isInfluential"] is True
    assert s2.reference_calls == ["s2-alpha", "s2-beta", "s2-gamma"]


def test_cli_failing_subset_has_nonzero_exit(tmp_path: Path, monkeypatch: Any) -> None:
    seed_path = tmp_path / "seeds.yaml"
    seed_path.write_text(
        """\
version: 1
papers:
  - key: bad
    title: "Expected Title"
    year: 2020
    arxiv: "4000.00001"
    expect: {source_tier: preprint, is_survey: false}
""",
        encoding="utf-8",
    )
    bundle = {
        "arxiv": FakeArxiv({"4000.00001": _work("4000.00001", "s2-bad", "Wrong", 2020)}),
        "semanticscholar": FakeSemanticScholar(
            batch_results=[_work("4000.00001", "s2-bad", "Wrong", 2020)]
        ),
        "crossref": FakeCrossref(),
    }
    monkeypatch.setattr(builder, "create_adapters", lambda cache_dir, offline: bundle)
    result = CliRunner().invoke(
        builder.app,
        ["--seeds", str(seed_path), "--output-dir", str(tmp_path / "out")],
    )
    assert result.exit_code != 0
    assert (tmp_path / "out" / "resolution_report.md").exists()
