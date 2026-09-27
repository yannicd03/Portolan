"""Offline contract tests for the plain Python research run."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
from io import BytesIO
from threading import Event
from typing import Any

import pytest
from pypdf import PdfWriter

from portolan.documents import DocumentStore, FetchResult, PdfCandidate
from portolan.graph.memory import InMemoryResearchGraph
from portolan.research import (
    HeuristicScreener,
    ResearchRequest,
    ResearchRunner,
    ResearchSources,
    RunCancelled,
    rebuild_project_concepts,
)


def record(
    wid: str,
    title: str,
    *,
    year: int = 2024,
    doi: str | None = None,
    arxiv: str | None = None,
    refs: list[str] | None = None,
    keywords: list[tuple[str, float]] | None = None,
    citations: int = 0,
    authors: list[dict[str, Any]] | None = None,
    pdf_url: str | None = None,
) -> dict[str, Any]:
    return {
        "source": "openalex",
        "identifiers": {"openalex": wid, "doi": doi, "arxiv": arxiv},
        "openalex_id": wid,
        "title": title,
        "year": year,
        "abstract": f"Research on {title.casefold()}.",
        "venue": "Test Journal",
        "publication_types": ["article"],
        "authors": authors or [],
        "keywords": [
            {"term": term, "score": score, "kind": "keyword"} for term, score in (keywords or [])
        ],
        "referenced_works": refs or [],
        "cited_by_count": citations,
        "pdf_candidates": [{"url": pdf_url, "source": "openalex", "version": "published"}]
        if pdf_url
        else [],
    }


class FakeOpenAlex:
    def __init__(
        self,
        works: list[dict[str, Any]],
        *,
        search: list[str] | None = None,
        citing: dict[str, list[str]] | None = None,
    ) -> None:
        self.works = {work["openalex_id"]: work for work in works}
        self.search_ids = search or []
        self.citing = citing or {}
        self.search_calls: list[tuple[str, int, int | None, int | None]] = []
        self.lookup_calls: list[list[str]] = []
        self.forward_calls: list[tuple[str, int]] = []

    def _find(self, identifier: str) -> dict[str, Any] | None:
        key = identifier.rsplit("/", 1)[-1] if "openalex.org/" in identifier else identifier
        key = key.removeprefix("doi:").removeprefix("https://doi.org/")
        if key in self.works:
            return deepcopy(self.works[key])
        for work in self.works.values():
            ids = work["identifiers"]
            if key in (work["openalex_id"], ids.get("doi"), ids.get("arxiv")):
                return deepcopy(work)
        return None

    def lookup(self, identifier: str) -> dict[str, Any] | None:
        return self._find(identifier)

    def lookup_many(self, identifiers: list[str]) -> list[dict[str, Any]]:
        self.lookup_calls.append(list(identifiers))
        found_records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for identifier in identifiers:
            found = self._find(identifier)
            if found is None:
                continue
            key = found["openalex_id"] or found["identifiers"].get("doi")
            if key not in seen:
                seen.add(key)
                found_records.append(found)
        return found_records

    def search(
        self,
        query: str,
        *,
        limit: int = 25,
        from_year: int | None = None,
        to_year: int | None = None,
    ) -> list[dict[str, Any]]:
        self.search_calls.append((query, limit, from_year, to_year))
        return [self._find(wid) for wid in self.search_ids[:limit]]

    def cited_by(self, identifier: str, *, limit: int = 200) -> list[dict[str, Any]]:
        self.forward_calls.append((identifier, limit))
        return [self._find(wid) for wid in self.citing.get(identifier, [])[:limit]]

    def close(self) -> None:
        pass


class FakeSemanticScholar:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self.lookup_calls: list[list[str]] = []
        self.lookup_by_id_calls: list[str] = []

    @staticmethod
    def _aliases(value: Any) -> set[str]:
        text = str(value).strip().casefold()
        aliases = {text} if text else set()
        for prefix in ("arxiv:", "doi:"):
            if text.startswith(prefix):
                aliases.add(text[len(prefix) :].strip())
        for prefix in ("https://doi.org/", "http://doi.org/"):
            if text.startswith(prefix):
                aliases.add(text[len(prefix) :].strip())
        return aliases

    @classmethod
    def _matches(cls, identifier: str, record: Mapping[str, Any]) -> bool:
        requested = cls._aliases(identifier)
        values: list[Any] = []
        identifiers = record.get("identifiers")
        if isinstance(identifiers, Mapping):
            values.extend(identifiers.values())
        for key in ("s2_id", "paperId", "doi", "arxiv_id"):
            value = record.get(key)
            if value:
                values.append(value)
        return bool(requested & {alias for value in values for alias in cls._aliases(value)})

    def lookup_many(self, identifiers: list[str]) -> list[dict[str, Any]]:
        self.lookup_calls.append(list(identifiers))
        return [
            deepcopy(item)
            for item in self.records
            if any(self._matches(identifier, item) for identifier in identifiers)
        ]

    def lookup_by_id(self, identifier: str) -> dict[str, Any] | None:
        self.lookup_by_id_calls.append(identifier)
        records = self.lookup_many([identifier])
        return records[0] if records else None


class FakeArxiv:
    def __init__(self, record: dict[str, Any]) -> None:
        self.record = record
        self.lookup_calls: list[str] = []

    @staticmethod
    def _canonical(value: Any) -> str:
        text = str(value).strip().casefold()
        if text.startswith("arxiv:"):
            text = text[6:]
        if "arxiv.org/" in text:
            text = text.rsplit("/", 1)[-1]
        return text.removesuffix(".pdf")

    def lookup_by_id(self, identifier: str) -> dict[str, Any] | None:
        self.lookup_calls.append(identifier)
        identifiers = self.record.get("identifiers")
        arxiv_id = identifiers.get("arxiv") if isinstance(identifiers, Mapping) else None
        if self._canonical(identifier) != self._canonical(arxiv_id):
            return None
        return deepcopy(self.record)

    def lookup_many(self, identifiers: list[str]) -> list[dict[str, Any]]:
        records = [self.lookup_by_id(identifier) for identifier in identifiers]
        return [record for record in records if record is not None]

    lookup = lookup_by_id


class FakeFetcher:
    def __init__(self, store: DocumentStore) -> None:
        self.store = store
        self.calls: list[list[str]] = []
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        stream = BytesIO()
        writer.write(stream)
        self.pdf = stream.getvalue()

    def fetch_first(self, candidates: list[PdfCandidate]) -> FetchResult:
        self.calls.append([candidate.url for candidate in candidates])
        if not candidates:
            return FetchResult(document=None, attempts=[])
        document = self.store.put_pdf(
            self.pdf,
            source_url=candidates[0].url,
            source=candidates[0].source,
        )
        return FetchResult(document=document, attempts=[])


def make_runner(
    graph: InMemoryResearchGraph,
    openalex: FakeOpenAlex,
    *,
    store: DocumentStore | None = None,
    fetcher: FakeFetcher | None = None,
    semanticscholar: FakeSemanticScholar | None = None,
    arxiv: FakeArxiv | None = None,
) -> ResearchRunner:
    sources = ResearchSources(openalex, semanticscholar=semanticscholar, arxiv=arxiv)
    return ResearchRunner(
        graph,
        sources,
        documents=store,
        fetcher=fetcher,
        clock=lambda: datetime(2026, 9, 26, tzinfo=UTC),
    )


def test_seed_snowball_writes_graph_and_is_idempotent() -> None:
    # W4 is only reachable through the default depth-2 chase of W2's references,
    # so it has no co-citation support from the core set and is screened out.
    seed = record(
        "W1",
        "Graph retrieval",
        refs=["W2"],
        keywords=[("Knowledge Graph", 0.9), ("Data", 0.8)],
        authors=[
            {"name": "Ada", "openalex_id": "A1", "orcid": None, "position": 1},
            {"name": "Bob", "openalex_id": "A2", "orcid": None, "position": 2},
        ],
    )
    backward = record("W2", "Knowledge graphs", refs=["W4"])
    forward = record("W3", "Retrieval survey", refs=["W1"])
    excluded = record("W4", "Unrelated paper")
    source = FakeOpenAlex([seed, backward, forward, excluded], citing={"W1": ["W3"]})
    graph = InMemoryResearchGraph()
    project = graph.create_project("Seed run")
    runner = make_runner(graph, source)
    stages: list[str] = []
    request = ResearchRequest(seeds=["W1"], max_works=3, acquire_pdfs=False)

    report = runner.run(project.id, request, progress=lambda item: stages.append(item.stage))

    assert report.candidates_found == 4
    assert report.screened_out == 1
    assert report.included == 3
    assert report.citations == 2
    assert report.authors == 2
    assert {work.openalex_id for work in graph.project_works(project.id)} == {"W1", "W2", "W3"}
    seed_node = next(work for work in graph.project_works(project.id) if work.openalex_id == "W1")
    neighbors = graph.work_neighborhood(seed_node.id, project.id)
    assert [(author.name, position) for author, position in neighbors.authors] == [
        ("Ada", 1),
        ("Bob", 2),
    ]
    assert [author.openalex_id for author, _ in neighbors.authors] == ["A1", "A2"]
    assert [work.title for work in neighbors.cites] == ["Knowledge graphs"]
    assert [work.title for work in neighbors.cited_by] == ["Retrieval survey"]
    assert stages[0] == "resolve"
    assert list(dict.fromkeys(stages)) == [
        "resolve",
        "search",
        "snowball",
        "screen",
        "write",
        "concepts",
        "acquire",
        "done",
    ]
    assert runner.run(project.id, request).included == 3
    assert graph.project_stats(project.id).works == 3
    assert graph.project_stats(project.id).citations == 2


def test_query_only_search_frontier_and_year_filter() -> None:
    top = record("W1", "Graph models", year=2023, refs=["W3"])
    old = record("W2", "Old graph model", year=2010)
    child = record("W3", "New graph model", year=2024)
    source = FakeOpenAlex([top, old, child], search=["W1", "W2"])
    graph = InMemoryResearchGraph()
    project = graph.create_project("Topic")

    report = make_runner(graph, source).run(
        project.id,
        ResearchRequest(query="graph models", from_year=2020, max_works=3, acquire_pdfs=False),
    )

    assert source.search_calls == [("graph models", 50, 2020, None)]
    assert {work.openalex_id for work in graph.project_works(project.id)} == {"W1", "W3"}
    assert report.candidates_found == 2


def test_excluded_forward_snowball_work_is_not_a_hub() -> None:
    seed = record("W1", "Seed")
    survey = record("W2", "Excluded survey", refs=["W3"])
    reference = record("W3", "Survey reference")
    source = FakeOpenAlex(
        [seed, survey, reference],
        citing={"W1": ["W2"], "W2": ["W3"]},
    )
    graph = InMemoryResearchGraph()
    project = graph.create_project("Excluded hub")

    report = make_runner(graph, source).run(
        project.id,
        ResearchRequest(
            seeds=["W1"],
            exclude=["W2"],
            max_works=10,
            snowball_depth=2,
            acquire_pdfs=False,
        ),
    )

    assert report.excluded == 1
    assert report.candidates_found == 1
    assert {work.openalex_id for work in graph.project_works(project.id)} == {"W1"}
    assert [identifier for identifier, _ in source.forward_calls] == ["W1"]


def test_excluded_search_hit_is_dropped_and_counted() -> None:
    included = record("W1", "Included result")
    excluded = record("W2", "Excluded result")
    source = FakeOpenAlex([included, excluded], search=["W1", "W2"])
    graph = InMemoryResearchGraph()
    project = graph.create_project("Excluded search hit")

    report = make_runner(graph, source).run(
        project.id,
        ResearchRequest(
            query="results",
            exclude=["W2"],
            snowball_depth=0,
            acquire_pdfs=False,
        ),
    )

    assert report.excluded == 1
    assert report.candidates_found == 1
    assert report.included == 1
    assert {work.openalex_id for work in graph.project_works(project.id)} == {"W1"}


def test_excluded_doi_drops_arxiv_only_candidate_after_identity_merge() -> None:
    resolved = record(
        "W2",
        "Excluded survey",
        doi="10.1000/excluded",
        arxiv="2401.00001",
    )
    arxiv_only = deepcopy(resolved)
    arxiv_only["openalex_id"] = None
    arxiv_only["identifiers"] = {"openalex": None, "doi": None, "arxiv": "2401.00001"}
    source = FakeOpenAlex([resolved])

    def search(
        query: str,
        *,
        limit: int = 25,
        from_year: int | None = None,
        to_year: int | None = None,
    ) -> list[dict[str, Any]]:
        return [deepcopy(arxiv_only)]

    source.search = search
    graph = InMemoryResearchGraph()
    project = graph.create_project("Excluded DOI")

    report = make_runner(graph, source).run(
        project.id,
        ResearchRequest(
            query="survey",
            exclude=["doi:10.1000/excluded"],
            snowball_depth=0,
            acquire_pdfs=False,
        ),
    )

    assert report.excluded == 1
    assert report.candidates_found == 0
    assert graph.project_works(project.id) == []


def test_seed_and_exclude_overlap_is_an_error() -> None:
    seed = record("W1", "Seed", doi="10.1000/seed")
    source = FakeOpenAlex([seed])
    graph = InMemoryResearchGraph()
    project = graph.create_project("Seed overlap")

    with pytest.raises(ValueError, match="excluded"):
        make_runner(graph, source).run(
            project.id,
            ResearchRequest(
                seeds=["W1"],
                exclude=["doi:10.1000/seed"],
                snowball_depth=0,
                acquire_pdfs=False,
            ),
        )

    assert graph.project_works(project.id) == []


def test_seed_priority_deterministic_cut_and_identity_dedupe() -> None:
    seed = record("W9", "Seed", doi="10.1000/seed")
    one = record("W1", "Same topic", doi="10.1000/one")
    duplicate = deepcopy(one)
    duplicate["openalex_id"] = None
    duplicate["identifiers"]["openalex"] = None
    other = record("W2", "Same topic")
    source = FakeOpenAlex([seed, one, other], search=["W1", "W2"], citing={"W9": ["W1-duplicate"]})
    source.works["W1-duplicate"] = duplicate
    graph = InMemoryResearchGraph()
    project = graph.create_project("Dedupe")

    report = make_runner(graph, source).run(
        project.id,
        ResearchRequest(
            seeds=["10.1000/seed"], query="Same topic", max_works=2, acquire_pdfs=False
        ),
    )

    assert report.included == 2
    assert report.screened_out == 1
    assert {work.openalex_id for work in graph.project_works(project.id)} == {"W9", "W1"}


def test_existing_reference_edge_without_snowball_and_duplicate_seed_inputs() -> None:
    first = record("W1", "First", doi="10.1000/first", refs=["W2"])
    second = record("W2", "Second")
    graph = InMemoryResearchGraph()
    project = graph.create_project("Direct citations")
    runner = make_runner(graph, FakeOpenAlex([first, second], search=["W2"]))

    report = runner.run(
        project.id,
        ResearchRequest(
            seeds=["W1", "10.1000/first"],
            query="Second",
            snowball_depth=0,
            max_works=2,
            acquire_pdfs=False,
        ),
    )

    assert report.candidates_found == 2
    assert report.citations == 1
    assert not any("10.1000/first" in warning for warning in report.warnings)
    assert graph.project_stats(project.id).citations == 1


def test_snowball_candidate_cap_bounds_batch_fetches() -> None:
    referenced = [record(f"W{index}", f"Reference {index}") for index in range(2, 22)]
    seed = record("W1", "Seed", refs=[work["openalex_id"] for work in referenced])
    source = FakeOpenAlex([seed, *referenced])
    graph = InMemoryResearchGraph()
    project = graph.create_project("Bounded")

    report = make_runner(graph, source).run(
        project.id,
        ResearchRequest(seeds=["W1"], max_works=1, forward_per_work=0, acquire_pdfs=False),
    )

    assert len(source.lookup_calls[1]) == 4
    assert report.candidates_found == 5
    assert any("candidate cap" in warning for warning in report.warnings)


def test_keyword_threshold_and_project_wide_concept_rebuild() -> None:
    first = record("W1", "First", keywords=[("Natural Language Processing", 0.9), ("Weak", 0.1)])
    second = record("W2", "Second", keywords=[("NLP", 0.8)])
    graph = InMemoryResearchGraph()
    project = graph.create_project("Concepts")
    runner = make_runner(graph, FakeOpenAlex([first, second]))
    base = {"snowball_depth": 0, "acquire_pdfs": False, "keyword_min_score": 0.3}

    runner.run(project.id, ResearchRequest(seeds=["W1"], **base))
    report = runner.run(project.id, ResearchRequest(seeds=["W2"], **base))

    assert report.concepts == 1
    works = graph.project_works(project.id)
    assert all(work.keywords for work in works)
    assert "Weak" not in works[0].keywords
    concept = next(
        concept
        for concept in graph._concepts.values()
        if {work.id for work in graph.works_by_concept(concept.id, project.id)}
        == {work.id for work in works}
    )
    assert {work.id for work in graph.works_by_concept(concept.id, project.id)} == {
        work.id for work in works
    }


def test_concept_filter_removes_generic_and_singleton_keywords() -> None:
    works = [
        record(
            f"W{index}",
            f"Graph paper {index}",
            keywords=(
                [("Library science", 0.9)]
                + ([("Knowledge graph", 0.8)] if index <= 2 else [])
                + ([("One-off method", 0.7)] if index == 1 else [])
            ),
        )
        for index in range(1, 16)
    ]
    graph = InMemoryResearchGraph()
    project = graph.create_project("Filtered concepts")
    runner = make_runner(graph, FakeOpenAlex(works))

    report = runner.run(
        project.id,
        ResearchRequest(
            seeds=[work["openalex_id"] for work in works],
            max_works=15,
            snowball_depth=0,
            acquire_pdfs=False,
        ),
    )

    assert report.included == 15
    assert report.concepts == 1
    assert report.concepts_filtered == 2
    assert [concept.label for concept in graph._concepts.values()] == ["Knowledge graph"]
    project_works = graph.project_works(project.id)
    kept_concept = next(iter(graph._concepts.values()))
    assert len(graph.works_by_concept(kept_concept.id, project.id)) == 2
    assert all(
        not graph.work_neighborhood(work.id, project.id).concepts
        for work in project_works
        if work.openalex_id not in {"W1", "W2"}
    )


def test_rebuild_project_concepts_returns_filter_counts_and_replaces_edges() -> None:
    works = [
        record(
            f"W{index}",
            f"Graph paper {index}",
            keywords=(
                [("Library science", 0.9)]
                + ([("Knowledge graph", 0.8)] if index <= 2 else [])
                + ([("One-off method", 0.7)] if index == 1 else [])
            ),
        )
        for index in range(1, 16)
    ]
    graph = InMemoryResearchGraph()
    project = graph.create_project("Direct concept rebuild")
    runner = make_runner(graph, FakeOpenAlex(works))
    runner.run(
        project.id,
        ResearchRequest(
            seeds=[work["openalex_id"] for work in works],
            max_works=15,
            snowball_depth=0,
            acquire_pdfs=False,
        ),
    )

    kept, filtered = rebuild_project_concepts(graph, project.id)

    assert (kept, filtered) == (1, 2)
    concept = next(iter(graph._concepts.values()))
    assert {work.id for work in graph.works_by_concept(concept.id, project.id)} == {
        work.id for work in graph.project_works(project.id) if work.openalex_id in {"W1", "W2"}
    }
    assert all(
        not graph.work_neighborhood(work.id, project.id).concepts
        for work in graph.project_works(project.id)
        if work.openalex_id not in {"W1", "W2"}
    )


def test_pdf_order_cap_skip_existing_and_enrichment(tmp_path: Any) -> None:
    seed = record("W1", "Seed", pdf_url="https://example.org/seed.pdf")
    other = record("W2", "Other", doi="10.1000/other", citations=100)
    other["abstract"] = None
    third = record("W3", "Third", arxiv="2401.12345")
    third["abstract"] = None
    source = FakeOpenAlex([seed, other, third], search=["W2", "W3"])
    s2 = FakeSemanticScholar(
        [
            {
                "identifiers": {"openalex": "W2", "doi": "10.1000/other", "arxiv": None},
                "abstract": "Enriched abstract",
                "open_access_pdf_url": "https://example.org/other.pdf",
                "tldr": None,
            }
        ]
    )
    graph = InMemoryResearchGraph()
    project = graph.create_project("PDFs")
    store = DocumentStore(tmp_path / "documents")
    fetcher = FakeFetcher(store)
    runner = make_runner(graph, source, store=store, fetcher=fetcher, semanticscholar=s2)
    request = ResearchRequest(
        seeds=["W1"],
        query="papers",
        snowball_depth=0,
        max_pdfs=2,
        max_works=3,
        min_score=0.0,
    )

    report = runner.run(project.id, request)

    assert report.pdfs_acquired == 2
    assert report.pdfs_skipped == 1
    assert fetcher.calls[0][0] == "https://example.org/seed.pdf"
    assert fetcher.calls[1][0] == "https://example.org/other.pdf"
    works = graph.project_works(project.id)
    assert sum(bool(work.document_sha256) for work in works) == 2
    assert next(work for work in works if work.openalex_id == "W2").abstract == "Enriched abstract"
    second_report = runner.run(project.id, request)
    assert second_report.pdfs_acquired == 1
    assert second_report.pdfs_skipped == 2
    assert len(fetcher.calls) == 3


def test_unresolved_seed_warning_and_cancel() -> None:
    source = FakeOpenAlex([record("W1", "Paper")], search=["W1"])
    graph = InMemoryResearchGraph()
    project = graph.create_project("Warnings")
    runner = make_runner(graph, source)
    report = runner.run(
        project.id,
        ResearchRequest(seeds=["W404"], query="Paper", snowball_depth=0, acquire_pdfs=False),
    )
    assert report.included == 1
    assert any("W404" in warning for warning in report.warnings)
    with pytest.raises(ValueError):
        runner.run(
            project.id,
            ResearchRequest(seeds=["W404"], snowball_depth=0, acquire_pdfs=False),
        )
    cancel = Event()
    cancel.set()
    with pytest.raises(RunCancelled):
        runner.run(project.id, ResearchRequest(seeds=["W1"]), cancel=cancel)


def test_progress_updates_inside_long_stages() -> None:
    works = [record(f"W{index}", f"Graph paper {index}") for index in range(1, 31)]
    source = FakeOpenAlex(works, search=[work["openalex_id"] for work in works])
    graph = InMemoryResearchGraph()
    project = graph.create_project("Progress")
    events: list[Any] = []

    make_runner(graph, source).run(
        project.id,
        ResearchRequest(
            query="Graph", max_works=30, snowball_depth=0, min_score=0.0, acquire_pdfs=False
        ),
        progress=events.append,
    )

    assert sum(event.stage == "screen" for event in events) >= 2
    assert sum(event.stage == "write" for event in events) >= 2
    assert sum(event.stage == "concepts" for event in events) >= 2

    seed = record("W100", "Seed", refs=[work["openalex_id"] for work in works])
    backward_source = FakeOpenAlex([seed, *works])
    backward_events: list[Any] = []
    make_runner(graph, backward_source).run(
        project.id,
        ResearchRequest(seeds=["W100"], max_works=30, forward_per_work=0, acquire_pdfs=False),
        progress=backward_events.append,
    )
    assert sum(event.stage == "snowball" for event in backward_events) >= 2


def test_request_requires_seed_or_query() -> None:
    with pytest.raises(ValueError):
        ResearchRequest()


def test_heuristic_screener_relevance_and_citation_signal() -> None:
    screener = HeuristicScreener()
    candidate = record("W2", "Graph retrieval", refs=["W1"], citations=100)
    unrelated = record("W3", "Organic chemistry", citations=0)
    context = {
        "profile": "graph retrieval",
        "seed_ids": {"W1"},
        "included_ids": {"W1"},
        "max_cited_by_count": 100,
    }
    assert 0 <= screener.score(candidate, context) <= 1
    assert screener.score(candidate, context) > screener.score(unrelated, context)


def test_heuristic_screener_reverse_links_and_log_citation_scale() -> None:
    seed = record("W1", "Seed", refs=["W2"])
    linked = record("W2", "Unrelated")
    unlinked = record("W3", "Unrelated")
    link_screener = HeuristicScreener(token_weight=0, link_weight=1, citation_weight=0)
    context = {"seed_records": [seed], "max_cited_by_count": 100}

    assert link_screener.score(linked, context) == 1.0
    assert link_screener.score(unlinked, context) == 0.0

    citation_screener = HeuristicScreener(token_weight=0, link_weight=0, citation_weight=1)
    lightly_cited = record("W4", "Unrelated", citations=1)
    highly_cited = record("W5", "Unrelated", citations=100)
    assert 0 < citation_screener.score(lightly_cited, context) < 1
    assert citation_screener.score(highly_cited, context) == 1.0


def test_arxiv_seed_falls_back_through_semantic_scholar_doi(tmp_path: Any) -> None:
    title = "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding"
    openalex = record("W2963341956", title, year=2018, doi="10.18653/v1/N19-1423")
    semantic_scholar = FakeSemanticScholar(
        [
            {
                "source": "semanticscholar",
                "identifiers": {
                    "s2": "S2-BERT",
                    "doi": "10.18653/v1/N19-1423",
                    "arxiv": "1810.04805",
                },
                "title": title,
                "year": 2018,
            }
        ]
    )
    graph = InMemoryResearchGraph()
    project = graph.create_project("BERT")
    store = DocumentStore(tmp_path / "documents")
    fetcher = FakeFetcher(store)
    runner = make_runner(
        graph,
        FakeOpenAlex([openalex]),
        store=store,
        fetcher=fetcher,
        semanticscholar=semantic_scholar,
    )
    events: list[Any] = []

    report = runner.run(
        project.id,
        ResearchRequest(
            seeds=["arxiv:1810.04805"],
            max_works=1,
            snowball_depth=0,
            max_pdfs=1,
        ),
        progress=events.append,
    )

    work = graph.project_works(project.id)[0]
    inclusion = graph._inclusions[(project.id, work.id)]
    assert work.doi == "10.18653/v1/n19-1423"
    assert work.arxiv_id == "1810.04805"
    assert inclusion.discovered_via == "seed"
    assert inclusion.depth == 0
    assert not any("unresolvable seed" in warning for warning in report.warnings)
    assert any(
        event.stage == "resolve"
        and "semantic scholar" in event.message.casefold()
        and "10.18653/v1/n19-1423" in event.message.casefold()
        for event in events
    )
    assert report.pdfs_acquired == 1
    assert fetcher.calls == [["https://arxiv.org/pdf/1810.04805"]]


def test_arxiv_seed_falls_back_by_matching_openalex_title() -> None:
    title = "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding"
    arxiv = FakeArxiv(
        {
            "source": "arxiv",
            "identifiers": {"arxiv": "1810.04805", "doi": None},
            "title": title,
            "year": 2018,
        }
    )
    openalex = record(
        "W2963341956", title.replace("BERT:", "BERT"), year=2018, doi="10.18653/v1/N19-1423"
    )
    source = FakeOpenAlex([openalex], search=["W2963341956"])
    graph = InMemoryResearchGraph()
    project = graph.create_project("BERT title")

    report = make_runner(graph, source, arxiv=arxiv).run(
        project.id,
        ResearchRequest(seeds=["arxiv:1810.04805"], max_works=1, snowball_depth=0),
    )

    work = graph.project_works(project.id)[0]
    assert work.doi == "10.18653/v1/n19-1423"
    assert work.arxiv_id == "1810.04805"
    assert not any("unresolvable seed" in warning for warning in report.warnings)
    assert source.search_calls[0][:2] == (title, 5)


def test_arxiv_seed_rejects_near_miss_openalex_title() -> None:
    title = "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding"
    arxiv = FakeArxiv(
        {
            "source": "arxiv",
            "identifiers": {"arxiv": "1810.04805", "doi": None},
            "title": title,
            "year": 2018,
        }
    )
    wrong = record("W404", "A Different Paper About Language Models", year=2018)
    source = FakeOpenAlex([wrong], search=["W404"])
    graph = InMemoryResearchGraph()
    project = graph.create_project("BERT near miss")

    report = make_runner(graph, source, arxiv=arxiv).run(
        project.id,
        ResearchRequest(
            seeds=["arxiv:1810.04805"],
            query="unrelated query",
            max_works=1,
            snowball_depth=0,
            acquire_pdfs=False,
        ),
    )

    assert any("unresolvable seed: arxiv:1810.04805" in warning for warning in report.warnings)
    assert source.search_calls[0][:2] == (title, 5)


def test_default_sources_only_builds_unpaywall_with_contact_email(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PORTOLAN_CONTACT_EMAIL", "")
    without_email = ResearchSources.default(tmp_path)
    try:
        assert without_email.unpaywall is None
    finally:
        without_email.close()

    with_email = ResearchSources.default(tmp_path, contact_email="reader@example.org")
    try:
        assert with_email.unpaywall is not None
        assert with_email.unpaywall.email == "reader@example.org"
    finally:
        with_email.close()


def test_core_search_hits_are_snowballed_alongside_seeds() -> None:
    seed = record("W1", "Retrieval models", refs=["W10"])
    hits = [
        record("W2", "Retrieval models core", refs=["W20"]),
        record("W3", "Retrieval models second", refs=["W30"]),
        record("W4", "Retrieval models tail", refs=["W40"]),
    ]
    references = [
        record(wid, f"Retrieval models reference {wid}") for wid in ("W10", "W20", "W30", "W40")
    ]
    source = FakeOpenAlex(
        [seed, *hits, *references],
        search=["W2", "W3", "W4"],
        citing={"W2": ["W5"]},
    )
    source.works["W5"] = record("W5", "Retrieval models follow-up", refs=["W2"])
    graph = InMemoryResearchGraph()
    project = graph.create_project("Core set")

    report = make_runner(graph, source).run(
        project.id,
        ResearchRequest(
            seeds=["W1"],
            query="retrieval models",
            core_search_hits=2,
            snowball_depth=1,
            max_works=20,
            acquire_pdfs=False,
        ),
    )

    assert source.search_calls[0][1] == 50
    backward = {identifier for call in source.lookup_calls[1:] for identifier in call}
    assert backward == {"W10", "W20", "W30"}
    assert sorted(identifier for identifier, _ in source.forward_calls) == ["W1", "W2", "W3"]
    included = {work.openalex_id for work in graph.project_works(project.id)}
    assert {"W20", "W30", "W5"} <= included
    assert "W40" not in included
    assert report.candidates_found == 8


def test_search_breadth_scales_with_max_works() -> None:
    source = FakeOpenAlex([record("W1", "Paper")], search=["W1"])
    graph = InMemoryResearchGraph()
    project = graph.create_project("Breadth")
    runner = make_runner(graph, source)
    for max_works in (10, 60, 150):
        runner.run(
            project.id,
            ResearchRequest(
                query="paper", max_works=max_works, snowball_depth=0, acquire_pdfs=False
            ),
        )
    assert [call[1] for call in source.search_calls] == [50, 120, 200]


def _chase_fixture() -> tuple[FakeOpenAlex, list[str]]:
    """A seed whose references have references of their own.

    The strong references share the seed's topic; the weak ones do not, so the
    chase round ranks the strong ones first.
    """

    strong = [
        record(f"W{index}", f"Graph retrieval method {index}", refs=[f"W{index}0"])
        for index in range(2, 5)
    ]
    weak = [record(f"W{index}", "Organic chemistry", refs=[f"W{index}0"]) for index in (5, 6)]
    second = [record(f"W{index}0", f"Graph retrieval origin {index}") for index in range(2, 7)]
    seed = record(
        "W1",
        "Graph retrieval",
        refs=[work["openalex_id"] for work in [*strong, *weak]],
    )
    return FakeOpenAlex([seed, *strong, *weak, *second]), [work["openalex_id"] for work in second]


def test_chase_round_expands_references_of_top_screened_candidates() -> None:
    source, _ = _chase_fixture()
    graph = InMemoryResearchGraph()
    project = graph.create_project("Chase")
    events: list[Any] = []

    report = make_runner(graph, source).run(
        project.id,
        ResearchRequest(
            seeds=["W1"],
            chase_top=3,
            forward_per_work=0,
            max_works=20,
            min_score=0.0,
            acquire_pdfs=False,
        ),
        progress=events.append,
    )

    assert len(source.lookup_calls) == 3
    assert set(source.lookup_calls[2]) == {"W20", "W30", "W40"}
    assert any(
        event.stage == "snowball" and event.message == "Chasing references of 3 top candidates"
        for event in events
    )
    assert report.candidates_found == 9
    chased = next(work for work in graph.project_works(project.id) if work.openalex_id == "W20")
    inclusion = graph._inclusions[(project.id, chased.id)]
    assert inclusion.discovered_via == "backward"
    assert inclusion.depth == 2
    source_work = next(work for work in graph.project_works(project.id) if work.openalex_id == "W2")
    cites = graph.work_neighborhood(source_work.id, project.id).cites
    assert [work.title for work in cites] == ["Graph retrieval origin 2"]


def test_chase_round_respects_depth_chase_top_and_forward_scope() -> None:
    source, _ = _chase_fixture()
    source.citing = {"W2": ["W99"]}
    source.works["W99"] = record("W99", "Graph retrieval citing paper", refs=["W2"])
    graph = InMemoryResearchGraph()
    project = graph.create_project("Chase bounds")
    runner = make_runner(graph, source)

    depth_one = runner.run(
        project.id,
        ResearchRequest(seeds=["W1"], snowball_depth=1, max_works=20, acquire_pdfs=False),
    )
    assert depth_one.candidates_found == 6
    assert len(source.lookup_calls) == 2

    source.lookup_calls.clear()
    no_chase = runner.run(
        project.id,
        ResearchRequest(seeds=["W1"], chase_top=0, max_works=20, acquire_pdfs=False),
    )
    assert no_chase.candidates_found == 6
    assert len(source.lookup_calls) == 2

    source.lookup_calls.clear()
    source.forward_calls.clear()
    runner.run(
        project.id,
        ResearchRequest(seeds=["W1"], max_works=20, acquire_pdfs=False),
    )
    # Forward citations are fetched for the core set only, never in the chase.
    assert [identifier for identifier, _ in source.forward_calls] == ["W1"]


def test_chase_round_respects_candidate_cap() -> None:
    source, _ = _chase_fixture()
    graph = InMemoryResearchGraph()
    project = graph.create_project("Chase cap")

    report = make_runner(graph, source).run(
        project.id,
        ResearchRequest(
            seeds=["W1"], max_works=1, forward_per_work=0, min_score=0.0, acquire_pdfs=False
        ),
    )

    assert report.candidates_found == 5
    assert len(source.lookup_calls) == 2
    assert any("candidate cap" in warning for warning in report.warnings)


def test_chase_round_skips_excluded_references() -> None:
    source, _ = _chase_fixture()
    graph = InMemoryResearchGraph()
    project = graph.create_project("Chase exclusions")

    report = make_runner(graph, source).run(
        project.id,
        ResearchRequest(
            seeds=["W1"],
            exclude=["W20"],
            chase_top=3,
            forward_per_work=0,
            max_works=20,
            min_score=0.0,
            acquire_pdfs=False,
        ),
    )

    assert "W20" not in source.lookup_calls[-1]
    assert set(source.lookup_calls[-1]) == {"W30", "W40"}
    assert report.excluded == 1
    assert "W20" not in {work.openalex_id for work in graph.project_works(project.id)}


def test_chase_round_honours_cancellation() -> None:
    source, _ = _chase_fixture()
    graph = InMemoryResearchGraph()
    project = graph.create_project("Chase cancel")
    cancel = Event()

    def on_progress(event: Any) -> None:
        if event.message.startswith("Chasing references"):
            cancel.set()

    with pytest.raises(RunCancelled):
        make_runner(graph, source).run(
            project.id,
            ResearchRequest(seeds=["W1"], forward_per_work=0, max_works=20, acquire_pdfs=False),
            progress=on_progress,
            cancel=cancel,
        )

    assert len(source.lookup_calls) == 2
    assert graph.project_works(project.id) == []


def test_min_score_leaves_slots_empty_but_keeps_seeds() -> None:
    seed = record("W1", "Graph retrieval", refs=["W2", "W3"])
    related = record("W2", "Graph retrieval benchmark")
    unrelated = record("W3", "Organic chemistry")
    unrelated["abstract"] = None
    source = FakeOpenAlex([seed, related, unrelated])
    graph = InMemoryResearchGraph()
    project = graph.create_project("Screen")
    runner = make_runner(graph, source)
    base = {"seeds": ["W1"], "max_works": 10, "forward_per_work": 0, "acquire_pdfs": False}

    report = runner.run(project.id, ResearchRequest(**base, min_score=0.5))
    assert report.candidates_found == 3
    assert report.included == 2
    assert report.screened_out == 1
    assert {work.openalex_id for work in graph.project_works(project.id)} == {"W1", "W2"}

    strict = graph.create_project("Strict")
    report = runner.run(strict.id, ResearchRequest(**base, min_score=1.0))
    assert report.included == 1
    assert {work.openalex_id for work in graph.project_works(strict.id)} == {"W1"}

    lenient = graph.create_project("Lenient")
    report = runner.run(lenient.id, ResearchRequest(**base, min_score=0.0))
    assert report.included == 3


def test_cocitation_by_core_set_raises_candidate_score() -> None:
    screener = HeuristicScreener()
    candidate = record("W5", "Unrelated title")
    context = {"profile": "graph retrieval", "max_cited_by_count": 10}
    baseline = screener.score(candidate, context)
    cocited = screener.score(
        candidate,
        {**context, "core_reference_counts": {"openalex:W5": 2}, "core_size": 4},
    )
    fully_cocited = screener.score(
        candidate,
        {**context, "core_reference_counts": {"openalex:W5": 4}, "core_size": 4},
    )
    assert baseline < cocited < fully_cocited
    assert fully_cocited - baseline == pytest.approx(0.25)

    only_cocitation = HeuristicScreener(
        token_weight=0, link_weight=0, citation_weight=0, cocitation_weight=1
    )
    assert only_cocitation.score(
        candidate, {"core_reference_counts": {"openalex:W5": 9}, "core_size": 3}
    ) == pytest.approx(1.0)


def test_runner_puts_core_reference_counts_in_screening_context() -> None:
    seed = record("W1", "Seed", refs=["W3"])
    hit = record("W2", "Hit", refs=["W3", "W4"])
    shared = record("W3", "Shared reference")
    single = record("W4", "Single reference")
    source = FakeOpenAlex([seed, hit, shared, single], search=["W2"])
    contexts: list[dict[str, Any]] = []

    class RecordingScreener(HeuristicScreener):
        def score(self, candidate: Mapping[str, Any], context: Mapping[str, Any]) -> float:
            contexts.append(dict(context))
            return super().score(candidate, context)

    graph = InMemoryResearchGraph()
    project = graph.create_project("Co-citation")
    runner = ResearchRunner(graph, ResearchSources(source), screener=RecordingScreener())
    runner.run(
        project.id,
        ResearchRequest(
            seeds=["W1"], query="seed", snowball_depth=1, forward_per_work=0, acquire_pdfs=False
        ),
    )

    context = contexts[-1]
    assert context["core_size"] == 2
    assert context["core_reference_counts"]["openalex:W3"] == 2
    assert context["core_reference_counts"]["openalex:W4"] == 1
    assert "openalex:W1" not in context["core_reference_counts"]
    works = {work.openalex_id: work for work in graph.project_works(project.id)}
    scores = {
        openalex_id: graph._inclusions[(project.id, work.id)].score
        for openalex_id, work in works.items()
    }
    assert scores["W3"] > scores["W4"]


def test_new_request_fields_default_for_existing_callers() -> None:
    request = ResearchRequest(seeds=["W1"])
    assert request.snowball_depth == 2
    assert request.core_search_hits == 10
    assert request.chase_top == 20
    assert request.min_score == pytest.approx(0.15)
    with pytest.raises(ValueError):
        ResearchRequest(seeds=["W1"], core_search_hits=51)
    with pytest.raises(ValueError):
        ResearchRequest(seeds=["W1"], chase_top=101)
    with pytest.raises(ValueError):
        ResearchRequest(seeds=["W1"], min_score=1.5)

    default = HeuristicScreener()
    assert (
        default.token_weight,
        default.link_weight,
        default.cocitation_weight,
        default.citation_weight,
    ) == pytest.approx((0.45, 0.2, 0.25, 0.10))
    legacy = HeuristicScreener(token_weight=0.6, link_weight=0.25, citation_weight=0.15)
    assert legacy.cocitation_weight == 0
    assert (legacy.token_weight, legacy.link_weight, legacy.citation_weight) == pytest.approx(
        (0.6, 0.25, 0.15)
    )
