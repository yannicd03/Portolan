"""Offline tests for PDF bookmarks and extracted-text paper outlines."""

from __future__ import annotations

from io import BytesIO

from pypdf import PdfWriter

from portolan.documents.outline import build_outline, outline_to_text


def make_bookmarked_pdf() -> bytes:
    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=612, height=792)
    chapter = writer.add_outline_item("1 Introduction", 0)
    writer.add_outline_item("1.1 Motivation", 1, parent=chapter)
    writer.add_outline_item("2 Results", 2)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_pdf_bookmarks_are_preferred_and_nested() -> None:
    text = "=== page 1 ===\n1 Introduction\n\n=== page 2 ===\n1.1 Motivation\n"
    outline = build_outline(make_bookmarked_pdf(), text)

    assert outline.source == "pdf_outline"
    assert [(section.title, section.level, section.page) for section in outline.sections] == [
        ("1 Introduction", 1, 1),
        ("1.1 Motivation", 2, 2),
        ("2 Results", 1, 3),
    ]
    assert outline.sections[0].char_offset == text.index("1 Introduction")
    assert outline.sections[1].char_offset == text.index("1.1 Motivation")
    assert outline.sections[2].char_offset is None
    assert outline_to_text(outline) == (
        "1 Introduction … p. 1\n  1.1 Motivation … p. 2\n2 Results … p. 3"
    )


def test_text_headings_fallback_and_rejections() -> None:
    text = (
        "=== page 1 ===\n"
        "Abstract\n"
        "A short abstract.\n\n"
        "1 Introduction\n"
        "Body text.\n\n"
        "1.1 Prior Work\n\n"
        "Table 1 Overview\n\n"
        "This sentence ends with a period.\n\n"
        "=== page 2 ===\n"
        "2 Methods\n\n"
        "Results\n\n"
        "1 Earlier Number\n"
    )

    outline = build_outline(b"not a PDF", text)

    assert outline.source == "headings"
    assert [(section.title, section.level, section.page) for section in outline.sections] == [
        ("Abstract", 1, 1),
        ("1 Introduction", 1, 1),
        ("1.1 Prior Work", 2, 1),
        ("2 Methods", 1, 2),
        ("Results", 1, 2),
    ]
    assert all(section.char_offset is not None for section in outline.sections)


def test_no_bookmarks_or_headings_returns_none() -> None:
    text = "=== page 1 ===\nThis is ordinary prose.\n\nTable 1 Data\n"
    outline = build_outline(b"not a PDF", text)

    assert outline.source == "none"
    assert outline.sections == []
    assert outline_to_text(outline) == ""


def test_missing_text_still_reads_pdf_bookmarks() -> None:
    outline = build_outline(make_bookmarked_pdf(), None)

    assert outline.source == "pdf_outline"
    assert outline.sections[0].char_offset is None


def test_headings_in_pypdf_text_without_blank_lines() -> None:
    text = (
        "=== page 1 ===\n"
        "Fast Inference via Speculative Decoding\n"
        "Abstract\n"
        "Inference from large models is slow.\n"
        "1. Introduction\n"
        "Large models are slow to decode.\n"
        "2. Speculative Decoding\n"
        "2.1. Overview\n"
        "=== page 2 ===\n"
        "3. Analysis\n"
        "Figure 2. Speedups\n"
        "References\n"
        "1. Sample x from q(x)\n"
        "2. If r below p(x)\n"
        "7 Ghost Number\n"
    )

    outline = build_outline(b"not a PDF", text)

    assert outline.source == "headings"
    assert [(section.title, section.level, section.page) for section in outline.sections] == [
        ("Abstract", 1, 1),
        ("1. Introduction", 1, 1),
        ("2. Speculative Decoding", 1, 1),
        ("2.1. Overview", 2, 1),
        ("3. Analysis", 1, 2),
        ("References", 1, 2),
    ]
