"""Offline verification tests for paper-grounded Ask citations."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from portolan.agent.citations import AskAnswer, Citation, verify_citations
from portolan.documents.store import DocumentStore
from portolan.graph.memory import InMemoryResearchGraph
from portolan.graph.models import Inclusion, WorkNode


def make_pdf(*page_texts: str) -> bytes:
    """Create the small text PDF fixture used by the document tests."""

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


def _local_work(
    tmp_path: Path,
    *pages: str,
    title: str = "A local paper",
    abstract: str | None = None,
) -> tuple[InMemoryResearchGraph, DocumentStore, str]:
    graph = InMemoryResearchGraph()
    store = DocumentStore(tmp_path / "documents")
    project = graph.create_project("Citation project")
    document = store.put_pdf(
        make_pdf(*pages), source_url="https://example.org/paper.pdf", source="test"
    )
    work = graph.upsert_work(
        WorkNode(
            title=title,
            year=2025,
            abstract=abstract,
            document_sha256=document.sha256,
        )
    )
    graph.include_work(Inclusion(project_id=project.id, work_id=work.id, discovered_via="seed"))
    return graph, store, work.id


def test_exact_case_and_whitespace_match_corrects_the_claimed_page(tmp_path: Path) -> None:
    graph, store, work_id = _local_work(
        tmp_path,
        "Introduction",
        "The important result is robust.",
    )
    answer = AskAnswer(
        answer_markdown="The paper reports the result [1].",
        citations=[
            Citation(
                marker=1,
                work_id=work_id,
                quote="the important\n RESULT is robust.",
                page=99,
            )
        ],
    )

    result = verify_citations(answer, graph, store)

    citation = result.citations[0]
    assert citation.verified is True
    assert citation.source == "paper"
    assert citation.page == 2
    assert citation.sha256 is not None
    assert citation.offset is not None
    assert result.unsupported == []


def test_relaxed_match_repairs_quotes_ellipses_and_line_break_hyphenation(tmp_path: Path) -> None:
    graph, store, work_id = _local_work(tmp_path, "A paper")
    document = store.get(graph.get_work(work_id).document_sha256)
    assert document is not None and document.text_path is not None
    document.text_path.write_text(
        "=== page 1 ===\nThe state-\nof-the-art method works.\n", encoding="utf-8"
    )
    answer = AskAnswer(
        answer_markdown="The method is described [1].",
        citations=[
            Citation(
                marker=1,
                work_id=work_id,
                quote="“The state-of-the-art method works...”",
                page=1,
            )
        ],
    )

    citation = verify_citations(answer, graph, store).citations[0]

    assert citation.verified is True
    assert citation.source == "paper"
    assert citation.page == 1


def test_abstract_fallback_fabricated_quote_and_missing_marker(tmp_path: Path) -> None:
    graph = InMemoryResearchGraph()
    store = DocumentStore(tmp_path / "documents")
    project = graph.create_project("Abstract project")
    work = graph.upsert_work(
        WorkNode(
            title="Abstract-only paper",
            year=2024,
            abstract="This paper studies reliable graph retrieval.",
        )
    )
    graph.include_work(Inclusion(project_id=project.id, work_id=work.id, discovered_via="seed"))
    answer = AskAnswer(
        answer_markdown="The abstract studies retrieval [1]. A second claim [2].",
        citations=[
            Citation(
                marker=1,
                work_id=work.id,
                quote="this paper studies reliable\nGRAPH retrieval.",
                page=0,
            ),
            Citation(
                marker=3,
                work_id=work.id,
                quote="This sentence was invented.",
                page=0,
            ),
        ],
    )

    result = verify_citations(answer, graph, store)

    abstract_citation, fabricated = result.citations
    assert abstract_citation.verified is True
    assert abstract_citation.source == "abstract"
    assert abstract_citation.page == 0
    assert abstract_citation.sha256 is None
    assert abstract_citation.offset is None
    assert fabricated.verified is False
    assert fabricated.source is None
    assert result.unsupported == ["[2]"]


def test_unknown_work_is_reported_as_unverified(tmp_path: Path) -> None:
    answer = AskAnswer(
        answer_markdown="Unknown source [1].",
        citations=[Citation(marker=1, work_id="missing", quote="No source.", page=1)],
    )

    result = verify_citations(answer, InMemoryResearchGraph(), DocumentStore(tmp_path))

    assert result.citations[0].verified is False
    assert result.citations[0].work_id == "missing"
    assert result.citations[0].title == ""
