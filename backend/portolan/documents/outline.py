"""Build compact, page-addressable outlines for stored papers."""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from .models import Outline, Section
from .text import page_of_offset

_PAGE_MARKER = re.compile(r"^=== page \d+ ===$")
_NUMBERED_HEADING = re.compile(r"^(?P<number>\d+(?:\.\d+){0,2})\.?\s+(?P<title>[A-Z][^.]{1,80})$")
_TABLE_OR_FIGURE = re.compile(r"^(?:Table|Figure|Fig\.)", re.IGNORECASE)
_APPENDIX_HEADING = re.compile(r"^Appendix(?: [A-Z])?$", re.IGNORECASE)
_CANONICAL_HEADINGS = {
    "abstract",
    "introduction",
    "related work",
    "background",
    "method",
    "methods",
    "methodology",
    "approach",
    "experiments",
    "experimental setup",
    "results",
    "evaluation",
    "discussion",
    "limitations",
    "conclusion",
    "conclusions",
    "future work",
    "acknowledgments",
    "acknowledgements",
    "references",
    "bibliography",
}


def _reader_for(pdf: Path | bytes) -> PdfReader:
    """Open a path or byte string with pypdf."""

    return PdfReader(io.BytesIO(pdf) if isinstance(pdf, bytes) else str(pdf))


def _title_for(item: Any) -> str | None:
    """Return an outline item's title when it has one."""

    try:
        title = getattr(item, "title", None)
    except Exception:
        return None
    if title is None and isinstance(item, dict):
        title = item.get("/Title") or item.get("Title")
    if title is None:
        return None
    value = str(title).strip()
    return value or None


def _heading_offset(text: str, title: str, page: int) -> int | None:
    """Find a bookmark title's heading line on its destination page."""

    wanted = " ".join(title.split()).casefold()
    for line_start, raw_line in _lines_with_offsets(text):
        line = raw_line.strip()
        if not line or _PAGE_MARKER.fullmatch(line):
            continue
        try:
            line_page = page_of_offset(text, line_start)
        except ValueError:
            line_page = 1
        if line_page != page:
            continue
        normalized = " ".join(line.split()).casefold()
        if normalized == wanted:
            return line_start
        without_number = re.sub(r"^\d+(?:\.\d+){0,2}\.?\s+", "", normalized)
        if without_number == wanted:
            return line_start
    return None


def _pdf_sections(pdf: Path | bytes, text: str | None) -> list[Section]:
    """Read resolvable entries from a PDF's nested bookmark tree."""

    try:
        reader = _reader_for(pdf)
        outline = reader.outline
    except Exception:
        return []

    sections: list[Section] = []

    def visit(items: Any, level: int) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if isinstance(item, list):
                visit(item, level + 1)
                continue
            title = _title_for(item)
            if title is None:
                continue
            try:
                page_number = reader.get_destination_page_number(item)
            except Exception:
                continue
            if page_number is None or page_number < 0:
                continue
            page = int(page_number) + 1
            char_offset = None if text is None else _heading_offset(text, title, page)
            sections.append(Section(title=title, level=level, page=page, char_offset=char_offset))

    visit(outline, 1)
    return sections


def _lines_with_offsets(text: str):
    """Yield each text line without its newline and its original offset."""

    offset = 0
    for raw_line in text.splitlines(keepends=True):
        yield offset, raw_line.rstrip("\r\n")
        offset += len(raw_line)


def _page_for_offset(text: str, offset: int) -> int:
    """Get a line's page, tolerating text without page markers."""

    try:
        return page_of_offset(text, offset)
    except ValueError:
        return 1


def _heading_candidate(line: str) -> tuple[str, int, tuple[int, ...] | None] | None:
    """Parse one candidate heading into title, level, and optional number."""

    if not line or line.endswith(".") or _TABLE_OR_FIGURE.match(line):
        return None

    numbered = _NUMBERED_HEADING.fullmatch(line)
    if numbered is not None:
        number = numbered.group("number")
        return line, len(number.split(".")), tuple(int(part) for part in number.split("."))

    if line.casefold() in _CANONICAL_HEADINGS or _APPENDIX_HEADING.fullmatch(line) is not None:
        return line, 1, None
    return None


def _next_numbers(last: tuple[int, ...] | None) -> set[tuple[int, ...]]:
    """Section numbers that may follow ``last``: a sibling, a first child, or a parent's sibling."""

    if last is None:
        return {(1,)}
    successors = {last[:index] + (last[index] + 1,) for index in range(len(last))}
    successors.add((*last, 1))
    return successors


def _text_sections(text: str) -> list[Section]:
    """Detect headings in page-marked extracted text.

    pypdf text has no blank lines between paragraphs, so a numbered heading is accepted only
    when its number continues the section sequence; numbered lists and stray numerals fail that.
    """

    sections: list[Section] = []
    last_number: tuple[int, ...] | None = None
    seen_named: set[str] = set()
    for line_start, raw_line in _lines_with_offsets(text):
        line = raw_line.strip()
        if not line or _PAGE_MARKER.fullmatch(line) is not None:
            continue

        candidate = _heading_candidate(line)
        if candidate is None:
            continue
        title, level, number = candidate
        if number is not None:
            if number not in _next_numbers(last_number):
                continue
            last_number = number
        else:
            key = title.casefold()
            if key in seen_named:
                continue
            seen_named.add(key)
        sections.append(
            Section(
                title=title,
                level=level,
                page=_page_for_offset(text, line_start),
                char_offset=line_start,
            )
        )
    return sections


def build_outline(pdf: Path | bytes, text: str | None) -> Outline:
    """Build a paper outline from PDF bookmarks or extracted text headings."""

    sections = _pdf_sections(pdf, text)
    if sections:
        return Outline(sections=sections, source="pdf_outline")

    if text is not None:
        sections = _text_sections(text)
        if sections:
            return Outline(sections=sections, source="headings")

    return Outline(sections=[], source="none")


def outline_to_text(outline: Outline) -> str:
    """Render an outline as a short indented text list for reading agents."""

    return "\n".join(
        f"{'  ' * max(section.level - 1, 0)}{section.title} … p. {section.page}"
        for section in outline.sections
    )
