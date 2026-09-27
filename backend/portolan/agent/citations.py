"""Citation models and verification for paper-grounded Ask answers."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from contextlib import suppress
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..documents.store import DocumentStore
from ..documents.text import find_passage, page_of_offset
from ..graph.base import ResearchGraph


class Citation(BaseModel):
    """One citation supplied by the answer model."""

    marker: int
    work_id: str
    quote: str
    page: int


class AskAnswer(BaseModel):
    """The structured answer returned by the Ask agent before verification."""

    answer_markdown: str
    citations: list[Citation] = Field(default_factory=list)
    unsupported: list[str] = Field(default_factory=list)


class VerifiedCitation(BaseModel):
    """A citation annotated with the result of the server-side check."""

    marker: int
    work_id: str
    title: str
    year: int | None = None
    quote: str
    page: int
    sha256: str | None = None
    offset: int | None = None
    verified: bool = False
    source: Literal["paper", "abstract"] | None = None


class VerifiedAnswer(BaseModel):
    """An answer whose paper passages have been checked by the server."""

    answer_markdown: str
    citations: list[VerifiedCitation] = Field(default_factory=list)
    unsupported: list[str] = Field(default_factory=list)
    model: str | None = None
    tool_calls: int = 0


_MARKER_RE = re.compile(r"(?<!\w)\[(\d+)\]")
_QUOTE_CHARS = frozenset({"'", '"', "\u2018", "\u2019", "\u201c", "\u201d", "\u00ab", "\u00bb"})
_ELLIPSIS_RE = re.compile(r"(?:\.{3}|\u2026)")


def _field(value: object | None, name: str, default: Any = None) -> Any:
    """Read a field from a graph model or a mapping."""

    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _quote_variants(quote: str) -> list[str]:
    """Return the original quote and safe relaxed forms in deterministic order."""

    variants: list[str] = []

    def add(value: str) -> None:
        if value and value not in variants:
            variants.append(value)

    add(quote)
    stripped = quote.strip()
    # Model responses sometimes put the quote in Markdown-style quotation marks.
    while stripped and stripped[0] in _QUOTE_CHARS:
        stripped = stripped[1:].lstrip()
    while stripped and stripped[-1] in _QUOTE_CHARS:
        stripped = stripped[:-1].rstrip()
    add(stripped)

    # An ellipsis at either edge means that the model abbreviated the passage.
    edge_ellipsis = stripped
    edge_ellipsis = re.sub(r"^(?:\.{3}|\u2026)\s*", "", edge_ellipsis)
    edge_ellipsis = re.sub(r"\s*(?:\.{3}|\u2026)$", "", edge_ellipsis)
    add(edge_ellipsis.strip())

    # Removing an ellipsis used between words is useful when the model only
    # omitted punctuation or line wrapping.  This remains a conservative
    # contiguous match; it does not turn an ellipsis into a wildcard.
    without_ellipsis = _ELLIPSIS_RE.sub(" ", stripped)
    add(without_ellipsis.strip())
    return variants


def _line_break_hyphen(value: str, index: int) -> bool:
    """Return whether a hyphen joins words split at a line break."""

    if value[index] != "-":
        return False
    next_index = index + 1
    if next_index >= len(value) or not value[next_index].isspace():
        return False
    while next_index < len(value) and value[next_index].isspace():
        next_index += 1
    return next_index < len(value) and value[next_index].isalpha()


def _fold_relaxed(
    value: str,
    *,
    drop_line_hyphens: bool,
    drop_all_hyphens: bool,
    drop_quote_marks: bool,
    drop_ellipses: bool,
) -> tuple[str, list[int]]:
    """Fold text while retaining an offset for each resulting character."""

    folded: list[str] = []
    offsets: list[int] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character.isspace():
            index += 1
            continue
        if drop_quote_marks and character in _QUOTE_CHARS:
            index += 1
            continue
        if drop_all_hyphens and character == "-":
            index += 1
            continue
        if drop_line_hyphens and _line_break_hyphen(value, index):
            index += 1
            continue
        if drop_ellipses and (character == "\u2026" or value.startswith("...", index)):
            index += 1 if character == "\u2026" else 3
            continue

        normalized = unicodedata.normalize("NFKC", character).casefold()
        folded.append(normalized)
        offsets.extend([index] * len(normalized))
        index += 1
    return "".join(folded), offsets


def _find_relaxed_passage(text: str, quote: str) -> list[tuple[int, int]]:
    """Find a passage after relaxing punctuation and PDF line-break hyphens."""

    # The first relaxed form only repairs a word split by a PDF line break and
    # ignores quote marks/ellipsis punctuation.  The final form also drops all
    # hyphens for PDFs that retain a line-break hyphen without the newline.
    modes = (
        (True, False, True, True),
        (True, True, True, True),
    )
    for drop_line_hyphens, drop_all_hyphens, drop_quote_marks, drop_ellipses in modes:
        folded_text, offsets = _fold_relaxed(
            text,
            drop_line_hyphens=drop_line_hyphens,
            drop_all_hyphens=drop_all_hyphens,
            drop_quote_marks=drop_quote_marks,
            drop_ellipses=drop_ellipses,
        )
        folded_quote, _ = _fold_relaxed(
            quote,
            drop_line_hyphens=drop_line_hyphens,
            drop_all_hyphens=drop_all_hyphens,
            drop_quote_marks=drop_quote_marks,
            drop_ellipses=drop_ellipses,
        )
        if not folded_quote:
            continue
        start = 0
        while True:
            hit = folded_text.find(folded_quote, start)
            if hit < 0:
                break
            # ``offsets`` is non-empty whenever a non-empty folded quote can
            # match, but keeping this guard makes malformed input harmless.
            if hit < len(offsets):
                yield_page_offset = offsets[hit]
                with suppress(ValueError, IndexError):
                    yield page_of_offset(text, yield_page_offset), yield_page_offset
            start = hit + 1


def _paper_hits(text: str, quote: str) -> list[tuple[int, int]]:
    """Find exact and then relaxed occurrences, preserving document offsets."""

    for variant in _quote_variants(quote):
        try:
            hits = find_passage(text, variant)
        except (TypeError, ValueError):
            hits = []
        if hits:
            return hits

    for variant in _quote_variants(quote):
        hits = list(_find_relaxed_passage(text, variant))
        if hits:
            return hits
    return []


def _abstract_matches(abstract: str | None, quote: str) -> bool:
    """Check an abstract using the same exact then relaxed matching rules."""

    if not abstract:
        return False
    for variant in _quote_variants(quote):
        if _find_folded_abstract(abstract, variant, relaxed=False):
            return True
    for variant in _quote_variants(quote):
        if _find_folded_abstract(abstract, variant, relaxed=True):
            return True
    return False


def _find_folded_abstract(value: str, quote: str, *, relaxed: bool) -> bool:
    """Return whether a case/whitespace-insensitive abstract match exists."""

    if relaxed:
        folded_value, _ = _fold_relaxed(
            value,
            drop_line_hyphens=True,
            drop_all_hyphens=True,
            drop_quote_marks=True,
            drop_ellipses=True,
        )
        folded_quote, _ = _fold_relaxed(
            quote,
            drop_line_hyphens=True,
            drop_all_hyphens=True,
            drop_quote_marks=True,
            drop_ellipses=True,
        )
    else:
        folded_value, _ = _fold_relaxed(
            value,
            drop_line_hyphens=False,
            drop_all_hyphens=False,
            drop_quote_marks=False,
            drop_ellipses=False,
        )
        folded_quote, _ = _fold_relaxed(
            quote,
            drop_line_hyphens=False,
            drop_all_hyphens=False,
            drop_quote_marks=False,
            drop_ellipses=False,
        )
    return bool(folded_quote) and folded_quote in folded_value


def _closest_hit(hits: Iterable[tuple[int, int]], claimed_page: int) -> tuple[int, int] | None:
    """Choose the occurrence whose page is nearest the model's claimed page."""

    materialized = list(hits)
    if not materialized:
        return None
    return min(enumerate(materialized), key=lambda item: (abs(item[1][0] - claimed_page), item[0]))[
        1
    ]


def _document_text(documents: DocumentStore, sha256: str | None) -> str | None:
    """Read a stored paper text file, returning ``None`` for unavailable text."""

    if not sha256:
        return None
    try:
        record = documents.get(sha256)
    except (OSError, TypeError, ValueError):
        return None
    if record is None or record.text_path is None:
        return None
    try:
        return record.text_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def verify_citations(
    answer: AskAnswer,
    graph: ResearchGraph,
    documents: DocumentStore,
) -> VerifiedAnswer:
    """Verify every answer citation against local paper text or an abstract.

    Paper passages are matched with :func:`find_passage` first.  A second pass
    tolerates quote marks, ellipses, and PDF line-break hyphenation because
    those are common presentation differences in model-produced quotations.
    """

    verified_citations: list[VerifiedCitation] = []
    for citation in answer.citations:
        work: Any = None
        try:
            work = graph.get_work(citation.work_id)
        except (KeyError, OSError, ValueError):
            work = None

        title = str(_field(work, "title", "") or "")
        year = _field(work, "year")
        sha256 = _field(work, "document_sha256")
        page = citation.page
        offset: int | None = None
        source: Literal["paper", "abstract"] | None = None
        verified = False

        text = _document_text(documents, sha256)
        if text is not None:
            hit = _closest_hit(_paper_hits(text, citation.quote), citation.page)
            if hit is not None:
                page, offset = hit
                source = "paper"
                verified = True

        # Page zero is reserved for an abstract citation.  This fallback is
        # useful when a work has no acquired PDF, and also when extraction of a
        # local PDF failed.
        if (
            not verified
            and citation.page == 0
            and _abstract_matches(_field(work, "abstract"), citation.quote)
        ):
            page = 0
            offset = None
            sha256 = None
            source = "abstract"
            verified = True

        verified_citations.append(
            VerifiedCitation(
                marker=citation.marker,
                work_id=citation.work_id,
                title=title,
                year=year,
                quote=citation.quote,
                page=page,
                sha256=sha256 if source == "paper" else None,
                offset=offset,
                verified=verified,
                source=source,
            )
        )

    unsupported = list(answer.unsupported)
    cited_markers = {citation.marker for citation in answer.citations}
    for marker in (int(value) for value in _MARKER_RE.findall(answer.answer_markdown)):
        if marker not in cited_markers and f"[{marker}]" not in unsupported:
            unsupported.append(f"[{marker}]")

    return VerifiedAnswer(
        answer_markdown=answer.answer_markdown,
        citations=verified_citations,
        unsupported=unsupported,
    )


__all__ = [
    "AskAnswer",
    "Citation",
    "VerifiedAnswer",
    "VerifiedCitation",
    "verify_citations",
]
