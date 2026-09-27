"""Offline contract tests for the OpenAlex adapter."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

from portolan.adapters.openalex import OpenAlexAdapter

FIXTURES = Path(__file__).parent / "fixtures" / "openalex"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _response(request: httpx.Request, payload: dict, *, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload, request=request)


def _work(work_id: str, *, doi: str | None = None) -> dict:
    item = copy.deepcopy(_fixture("work.json"))
    item["id"] = f"https://openalex.org/{work_id}"
    item["ids"]["openalex"] = item["id"]
    item["doi"] = doi or f"https://doi.org/10.5555/{work_id.lower()}"
    item["ids"]["doi"] = item["doi"]
    item["display_name"] = f"Work {work_id}"
    item["title"] = item["display_name"]
    item["referenced_works"] = []
    return item


def _adapter(tmp_path: Path, handler, *, api_key: str | None = None) -> OpenAlexAdapter:
    return OpenAlexAdapter(
        cache_dir=tmp_path / "cache",
        transport=httpx.MockTransport(handler),
        api_key=api_key,
        source_intervals={"openalex": 0},
    )


def test_lookup_reconstructs_and_normalizes_work(tmp_path: Path) -> None:
    fixture = _fixture("work.json")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["authorization"] == "Bearer test-openalex-key"
        assert "test-openalex-key" not in str(request.url)
        return _response(request, fixture)

    adapter = _adapter(tmp_path, handler, api_key="test-openalex-key")
    record = adapter.lookup("W123456789")

    assert record is not None
    assert record["title"] == "A small example of open metadata"
    assert record["year"] == 2024
    assert record["abstract"] == "Open metadata makes research portable"
    assert record["identifiers"]["openalex"] == "W123456789"
    assert record["identifiers"]["doi"] == "10.5555/example.1"
    assert record["identifiers"]["arxiv"] == "2401.12345"
    assert record["identifiers"]["pmid"] == "12345678"
    assert record["identifiers"]["pmcid"] == "PMC1234567"
    assert record["identifiers"]["url"] == "https://arxiv.org/abs/2401.12345v2"
    assert record["openalex_id"] == "W123456789"
    assert record["authors"] == [
        {
            "name": "Ada Lovelace",
            "openalex_id": "A111",
            "orcid": "0000-0001-2345-6789",
            "position": 1,
        },
        {
            "name": "Grace Hopper",
            "openalex_id": "A222",
            "orcid": "0000-0002-9876-5432",
            "position": 2,
        },
    ]
    assert record["keywords"] == [
        {"term": "open data", "score": 0.91, "kind": "keyword"},
        {"term": "Research metadata", "score": 0.82, "kind": "topic"},
    ]
    assert record["referenced_works"] == ["W456", "W789"]
    assert record["cited_by_count"] == 42
    assert [candidate["url"] for candidate in record["pdf_candidates"]] == [
        "https://arxiv.org/pdf/2401.12345v2.pdf",
        "https://repository.example/accepted/example-1.pdf",
    ]
    assert record["open_access_pdf_url"] == "https://arxiv.org/pdf/2401.12345v2.pdf"
    assert record["raw"] == fixture
    assert len(requests) == 1


def test_lookup_accepts_identifier_forms_and_404_is_none(tmp_path: Path) -> None:
    fixture = _fixture("work.json")
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(urlsplit(str(request.url)).path)
        if request.url.path.endswith("W404"):
            return _response(request, {"error": "not found"}, status_code=404)
        return _response(request, fixture)

    adapter = _adapter(tmp_path, handler)
    assert adapter.lookup("https://openalex.org/W123456789") is not None
    assert adapter.lookup("doi:10.5555/Example.1") is not None
    assert adapter.lookup("https://doi.org/10.5555/Example.1") is not None
    assert adapter.lookup("1706.03762v2") is not None
    assert adapter.lookup("W404") is None

    assert "/works/W123456789" in paths
    assert "/works/doi:10.5555/example.1" in paths
    assert "/works/doi:10.48550/arxiv.1706.03762" in paths


def test_lookup_many_batches_deduplicates_and_preserves_input_order(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        filter_value = request.url.params["filter"]
        calls.append(filter_value)
        values = filter_value.split(":", 1)[1].split("|")
        results = [_work(value) for value in values if value != "W005"]
        return _response(request, {"meta": {"count": len(results)}, "results": results})

    identifiers = [f"W{number:03d}" for number in range(1, 121)]
    identifiers.insert(25, "https://openalex.org/W001")
    adapter = _adapter(tmp_path, handler)
    records = adapter.lookup_many(identifiers)

    assert len(calls) == 3
    expected: list[str] = []
    seen: set[str] = set()
    for identifier in identifiers:
        value = identifier.rsplit("/", 1)[-1]
        if value == "W005" or value in seen:
            continue
        seen.add(value)
        expected.append(value)
    assert [record["openalex_id"] for record in records] == expected
    assert all(len(filter_value.split(":", 1)[1].split("|")) <= 50 for filter_value in calls)


def test_lookup_many_groups_dois_and_openalex_ids(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        filter_value = request.url.params["filter"]
        calls.append(filter_value)
        kind, values = filter_value.split(":", 1)
        results = []
        for value in values.split("|"):
            if kind == "openalex":
                results.append(_work(value, doi="10.7777/shared"))
            else:
                item = _work("W999", doi=value)
                results.append(item)
        return _response(request, {"results": results})

    adapter = _adapter(tmp_path, handler)
    records = adapter.lookup_many(["10.7777/shared", "W001", "W001"])

    assert len(calls) == 2
    assert [record["openalex_id"] for record in records] == ["W999", "W001"]


def test_search_uses_cursor_and_year_filters_until_limit(tmp_path: Path) -> None:
    cursors: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cursors.append(request.url.params["cursor"])
        assert request.url.params["filter"] == (
            "from_publication_date:2020-01-01,to_publication_date:2024-12-31"
        )
        if len(cursors) == 1:
            return _response(
                request,
                {"meta": {"next_cursor": "next-page"}, "results": [_work("W001"), _work("W002")]},
            )
        return _response(
            request,
            {"meta": {"next_cursor": "unused"}, "results": [_work("W003"), _work("W004")]},
        )

    adapter = _adapter(tmp_path, handler)
    records = adapter.search("open metadata", limit=3, from_year=2020, to_year=2024)

    assert cursors == ["*", "next-page"]
    assert [record["openalex_id"] for record in records] == ["W001", "W002", "W003"]


def test_cited_by_uses_cursor_paging(tmp_path: Path) -> None:
    cursors: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["filter"] == "cites:W123456789"
        cursors.append(request.url.params["cursor"])
        if len(cursors) == 1:
            return _response(
                request,
                {"meta": {"next_cursor": "page-2"}, "results": [_work("W201")]},
            )
        return _response(request, {"meta": {}, "results": [_work("W202")]})

    adapter = _adapter(tmp_path, handler)
    records = adapter.cited_by("W123456789", limit=2)

    assert cursors == ["*", "page-2"]
    assert [record["openalex_id"] for record in records] == ["W201", "W202"]


def test_references_look_up_and_batch_referenced_works(tmp_path: Path) -> None:
    root = _fixture("work.json")
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/works/W123456789"):
            return _response(request, root)
        values = request.url.params["filter"].split(":", 1)[1].split("|")
        return _response(request, {"results": [_work(value) for value in values]})

    adapter = _adapter(tmp_path, handler)
    records = adapter.references("W123456789")

    assert [record["openalex_id"] for record in records] == ["W456", "W789"]
    assert paths == ["/works/W123456789", "/works"]


def test_empty_abstract_index_is_none() -> None:
    record = OpenAlexAdapter._record_from_work(
        {"id": "https://openalex.org/W1", "abstract_inverted_index": {}}
    )
    assert record["abstract"] is None


def test_lookup_without_api_key_sends_no_secret_in_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        assert "authorization" not in request.headers
        return _response(request, _fixture("work.json"))

    adapter = _adapter(tmp_path, handler)
    assert adapter.lookup("W123456789") is not None
    assert urls and "api_key" not in urls[0]
