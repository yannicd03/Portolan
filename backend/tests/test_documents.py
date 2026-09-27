"""Offline tests for PDF acquisition and the local document store."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from portolan.documents.fetch import PdfFetcher, rank_candidates
from portolan.documents.models import PdfCandidate
from portolan.documents.store import DocumentStore
from portolan.documents.text import extract_text, find_passage, page_of_offset


def make_pdf(*page_texts: str) -> bytes:
    writer = PdfWriter()
    for text in page_texts:
        page = writer.add_blank_page(width=612, height=792)
        if not text:
            continue
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_text_pages_and_blank_page() -> None:
    extracted = extract_text(make_pdf("Hello page one", "Second page", ""))
    assert extracted.pages == 3
    assert extracted.text.count("=== page ") == 3
    assert extracted.text.startswith("=== page 1 ===\nHello page one\n")
    assert "\n=== page 2 ===\nSecond page\n" in extracted.text
    assert extracted.text.endswith("=== page 3 ===\n")


def test_hyphen_repair_and_passage_matching() -> None:
    # PDF extraction itself is checked above; this text exercises the normalisation contract.
    from portolan.documents.text import _normalize_page_text

    assert _normalize_page_text("an exam-\nple\r\n\r\n\r\n\r\nNext  \n") == ("an example\n\n\nNext")
    text = "=== page 1 ===\nAn important result\n\n=== page 2 ===\nAnother RESULT here\n"
    hits = find_passage(text, "another\n result HERE")
    assert hits == [(2, text.index("Another"))]
    assert page_of_offset(text, text.index("important")) == 1
    assert page_of_offset(text, text.index("Another")) == 2


def test_store_layout_round_trip_and_idempotency(tmp_path: Path) -> None:
    store = DocumentStore(tmp_path)
    data = make_pdf("Stored text")
    first = store.put_pdf(data, source_url="https://example.org/a.pdf", source="test")
    second = store.put_pdf(data, source_url="https://example.org/b.pdf", source="other")
    assert second == first
    directory = tmp_path / first.sha256[:2] / first.sha256
    assert {path.name for path in directory.iterdir()} == {
        "paper.pdf",
        "paper.txt",
        "outline.json",
        "meta.json",
    }
    assert first.pdf_path == directory / "paper.pdf"
    assert first.text_path == directory / "paper.txt"
    assert first.pages == 1
    assert first.text_status == "ok"
    assert first.bytes == len(data)
    assert store.get(first.sha256) == first
    assert store.has(first.sha256)
    assert list(store.iter_documents()) == [first]
    metadata = json.loads(first.meta_path.read_text())
    assert metadata["pdf_path"] == f"{first.sha256[:2]}/{first.sha256}/paper.pdf"
    assert metadata["text_path"] == f"{first.sha256[:2]}/{first.sha256}/paper.txt"
    assert metadata["text_extractor"].startswith("pypdf ")
    assert not any(path.name.endswith(".tmp") for path in directory.iterdir())


def test_store_outline_and_backfill(tmp_path: Path) -> None:
    store = DocumentStore(tmp_path)
    record = store.put_pdf(
        make_pdf("Introduction\nA stored paper"),
        source_url="https://example.org/outline.pdf",
        source="test",
    )
    outline_path = store.outline_path(record.sha256)
    assert outline_path.is_file()
    assert store.get_outline(record.sha256) is not None

    outline_path.unlink()
    assert store.backfill_outlines() == 1
    assert outline_path.is_file()
    assert store.backfill_outlines() == 0
    assert store.backfill_outlines(rebuild=True) == 1


def test_store_empty_and_failed_extraction(tmp_path: Path) -> None:
    store = DocumentStore(tmp_path)
    empty = store.put_pdf(make_pdf(""), source_url="https://example.org/e.pdf", source="test")
    assert empty.text_status == "empty"
    assert empty.pages == 1
    assert empty.text_path is not None and empty.text_path.read_text() == "=== page 1 ===\n"

    failed = store.put_pdf(
        b"%PDF-not a real PDF", source_url="https://example.org/f.pdf", source="test"
    )
    assert failed.text_status == "failed"
    assert failed.text_path is None
    assert failed.pdf_path.read_bytes() == b"%PDF-not a real PDF"
    assert store.get(failed.sha256) == failed


@pytest.mark.parametrize("bad_sha", ["../", "a" * 63, "A" * 64, "g" * 64])
def test_store_rejects_invalid_sha(tmp_path: Path, bad_sha: str) -> None:
    store = DocumentStore(tmp_path)
    with pytest.raises(ValueError):
        store.get(bad_sha)
    with pytest.raises(ValueError):
        store.pdf_path(bad_sha)
    with pytest.raises(ValueError):
        store.text_path(bad_sha)
    with pytest.raises(ValueError):
        store.has(bad_sha)


def test_rank_candidates() -> None:
    candidates = [
        PdfCandidate(url="https://content.openalex.org/paper", source="openalex"),
        PdfCandidate(
            url="https://example.org/submitted.pdf", source="x", version="submittedVersion"
        ),
        PdfCandidate(url="https://arxiv.org/abs/1234.56789", source="arxiv"),
        PdfCandidate(url="https://arxiv.org/pdf/1234.56789", source="duplicate"),
        PdfCandidate(
            url="https://example.org/published.pdf", source="x", version="publishedVersion"
        ),
        PdfCandidate(url="https://example.org/accepted.pdf", source="x", version="acceptedVersion"),
    ]
    ranked = rank_candidates(candidates)
    assert [candidate.url for candidate in ranked] == [
        "https://arxiv.org/pdf/1234.56789",
        "https://example.org/published.pdf",
        "https://example.org/accepted.pdf",
        "https://example.org/submitted.pdf",
        "https://content.openalex.org/paper",
    ]


def test_fetch_html_then_pdf_and_user_agent(tmp_path: Path) -> None:
    pdf = make_pdf("A fetched paper")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/landing":
            return httpx.Response(200, content=b"<html>landing</html>")
        return httpx.Response(200, content=pdf)

    store = DocumentStore(tmp_path)
    clock = FakeTime()
    with PdfFetcher(
        store,
        transport=httpx.MockTransport(handler),
        contact_email="research@example.org",
        clock=clock.clock,
        sleep=clock.sleep,
    ) as fetcher:
        result = fetcher.fetch_first(
            [
                PdfCandidate(url="https://example.org/landing", source="test"),
                PdfCandidate(url="https://elsewhere.org/paper.pdf", source="test"),
            ]
        )
    assert result.document is not None
    assert [attempt.outcome for attempt in result.attempts] == ["not_pdf", "ok"]
    assert requests[0].headers["User-Agent"] == (
        "Portolan/0.1 (+https://github.com/yannicd03/Portolan; mailto:research@example.org)"
    )


def test_fetch_oversize_header_and_stream(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if request.url.path == "/header":
            return httpx.Response(200, headers={"Content-Length": "1000"}, content=b"%PDF-short")
        return httpx.Response(200, content=b"%PDF-" + b"x" * 100)

    with PdfFetcher(
        DocumentStore(tmp_path), transport=httpx.MockTransport(handler), max_bytes=20
    ) as fetcher:
        result = fetcher.fetch_first(
            [
                PdfCandidate(url="https://one.org/header", source="test"),
                PdfCandidate(url="https://two.org/stream", source="test"),
            ]
        )
    assert result.document is None
    assert [attempt.outcome for attempt in result.attempts] == ["too_large", "too_large"]
    assert calls == 2


def test_fetch_retry_after_and_host_interval(tmp_path: Path) -> None:
    pdf = make_pdf("Success")
    clock = FakeTime()
    seen: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(clock.now)
        if len(seen) == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, content=pdf)

    with PdfFetcher(
        DocumentStore(tmp_path),
        transport=httpx.MockTransport(handler),
        host_intervals={"example.org": 5.0},
        clock=clock.clock,
        sleep=clock.sleep,
    ) as fetcher:
        result = fetcher.fetch_first([PdfCandidate(url="https://example.org/p.pdf", source="test")])
    assert result.document is not None
    assert [attempt.status for attempt in result.attempts] == [429, 200]
    assert seen == [0.0, 5.0]
    assert sum(clock.sleeps) == 5.0
