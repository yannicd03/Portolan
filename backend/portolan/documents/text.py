"""Plain-text extraction helpers for stored PDFs."""

from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader


class DocumentTextError(RuntimeError):
    """Raised when a PDF cannot be read or decrypted for text extraction."""


@dataclass(frozen=True)
class ExtractedText:
    """Extracted text with explicit page markers and the source page count."""

    text: str
    pages: int


_PAGE_MARKER = re.compile(r"^=== page (\d+) ===$", re.MULTILINE)


def _normalize_page_text(text: str) -> str:
    """Normalize one page while retaining meaningful leading whitespace."""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n"))
    # A line ending is part of the word when a lowercase continuation follows.
    normalized = re.sub(r"-\n(?=[a-z])", "", normalized)
    # Keep at most two blank lines in extracted page text.  This also removes
    # trailing blank lines, which keeps the page marker format deterministic.
    normalized = re.sub(r"\n{4,}", "\n\n\n", normalized)
    return normalized.rstrip("\n")


def extract_text(pdf: Path | bytes) -> ExtractedText:
    """Extract PDF text and add one grep-friendly marker for every page.

    ``pypdf`` may report an encrypted document that can still be opened with an
    empty password.  We try that password before treating the document as
    unreadable.
    """

    try:
        source = io.BytesIO(pdf) if isinstance(pdf, bytes) else str(pdf)
        reader = PdfReader(source)
        if reader.is_encrypted:
            try:
                decrypted = reader.decrypt("")
            except Exception as exc:  # pypdf exposes several version-specific errors
                raise DocumentTextError("encrypted PDF could not be decrypted") from exc
            if not decrypted:
                raise DocumentTextError("encrypted PDF requires a password")

        page_texts: list[str] = []
        for page in reader.pages:
            try:
                extracted = page.extract_text()
            except Exception as exc:  # pypdf's page errors vary by PDF and release
                raise DocumentTextError("PDF page text could not be extracted") from exc
            page_texts.append(_normalize_page_text(extracted or ""))
    except DocumentTextError:
        raise
    except Exception as exc:
        raise DocumentTextError("PDF could not be read") from exc

    blocks = [
        f"=== page {number} ===\n" + (f"{page_text}\n" if page_text else "")
        for number, page_text in enumerate(page_texts, 1)
    ]
    return ExtractedText(text="\n".join(blocks), pages=len(page_texts))


def page_of_offset(text: str, offset: int) -> int:
    """Return the page containing ``offset`` in marker-formatted extracted text."""

    if offset < 0 or offset > len(text):
        raise ValueError("offset is outside the text")
    markers = list(_PAGE_MARKER.finditer(text))
    if not markers:
        raise ValueError("text contains no page markers")
    for marker in reversed(markers):
        if marker.start() <= offset:
            return int(marker.group(1))
    return int(markers[0].group(1))


def _fold_with_offsets(value: str) -> tuple[str, list[int]]:
    """Drop whitespace, fold ligatures and case while retaining source offsets."""

    folded: list[str] = []
    offsets: list[int] = []
    for index, character in enumerate(value):
        if character.isspace():
            continue
        # NFKC per character folds PDF ligatures ("\ufb01" -> "fi") while keeping offsets.
        casefolded = unicodedata.normalize("NFKC", character).casefold()
        folded.append(casefolded)
        offsets.extend([index] * len(casefolded))
    return "".join(folded), offsets


def find_passage(text: str, quote: str) -> list[tuple[int, int]]:
    """Find case-insensitive, whitespace-insensitive quote occurrences.

    Returned offsets point into the original ``text`` and pages are attributed
    from the marker immediately preceding each occurrence.  Overlapping hits
    are retained.
    """

    folded_text, offsets = _fold_with_offsets(text)
    folded_quote, _ = _fold_with_offsets(quote)
    if not folded_quote:
        return []

    hits: list[tuple[int, int]] = []
    start = 0
    while True:
        hit = folded_text.find(folded_quote, start)
        if hit < 0:
            break
        original_offset = offsets[hit]
        hits.append((page_of_offset(text, original_offset), original_offset))
        start = hit + 1
    return hits
