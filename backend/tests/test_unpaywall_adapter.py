"""Offline contract tests for the Unpaywall adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from portolan.adapters.unpaywall import UnpaywallAdapter

FIXTURES = Path(__file__).parent / "fixtures" / "unpaywall"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_missing_email_is_rejected_when_lookup_is_called(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PORTOLAN_CONTACT_EMAIL", raising=False)
    transport = httpx.MockTransport(
        lambda request: pytest.fail(f"missing-email lookup made a request: {request.url}")
    )
    adapter = UnpaywallAdapter(cache_dir=tmp_path / "cache", transport=transport)

    with pytest.raises(ValueError, match="(?i)contact email|PORTOLAN_CONTACT_EMAIL"):
        adapter.lookup("10.1234/example.2026.001")


def test_lookup_extracts_ordered_deduplicated_pdf_candidates_and_hides_email_in_cache_key(
    tmp_path: Path,
) -> None:
    payload = _fixture("lookup.json")
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=json.dumps(payload).encode("utf-8"),
            request=request,
        )

    cache_dir = tmp_path / "cache"
    endpoint = "https://api.unpaywall.test/v2"
    first = UnpaywallAdapter(
        cache_dir=cache_dir,
        transport=httpx.MockTransport(handler),
        email="first@example.org",
        endpoint=endpoint,
        source_intervals={"unpaywall": 0},
    )
    record = first.lookup("DOI:10.1234/EXAMPLE.2026.001")

    assert record is not None
    assert record["doi"] == "10.1234/example.2026.001"
    assert record["is_oa"] is True
    assert record["best_oa_url"] == ("https://repository.example/items/12345/download/paper.pdf")
    assert [candidate["url"] for candidate in record["pdf_candidates"]] == [
        "https://repository.example/items/12345/download/paper.pdf",
        "https://publisher.example/articles/example.2026.001.pdf",
        "https://repository.example/items/12345/accepted.pdf",
    ]
    assert record["pdf_candidates"][0] == {
        "url": "https://repository.example/items/12345/download/paper.pdf",
        "source": "unpaywall",
        "version": "publishedVersion",
        "license": "cc-by",
        "host_type": "repository",
    }
    assert record["pdf_candidates"][1]["license"] is None
    assert record["raw"] == payload
    assert len(calls) == 1
    assert calls[0].url.path == "/v2/10.1234/example.2026.001"
    assert calls[0].url.params["email"] == "first@example.org"

    second = UnpaywallAdapter(
        cache_dir=cache_dir,
        transport=httpx.MockTransport(
            lambda request: pytest.fail(f"cache lookup made a request: {request.url}")
        ),
        email="second@example.org",
        endpoint=endpoint,
        source_intervals={"unpaywall": 0},
    )
    cached = second.lookup("https://doi.org/10.1234/example.2026.001")

    assert cached == record
    assert second.cache_hits == 1
    assert len(calls) == 1


def test_lookup_returns_none_for_unpaywall_404(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request)

    adapter = UnpaywallAdapter(
        cache_dir=tmp_path / "cache",
        transport=httpx.MockTransport(handler),
        email="contact@example.org",
        source_intervals={"unpaywall": 0},
    )

    assert adapter.lookup("https://doi.org/10.9999/missing") is None
