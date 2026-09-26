"""Open-access PDF acquisition and local document storage."""

from __future__ import annotations

from .fetch import PdfFetcher, rank_candidates
from .models import DocumentRecord, FetchAttempt, FetchResult, Outline, PdfCandidate, Section
from .outline import build_outline, outline_to_text
from .store import DocumentStore
from .text import DocumentTextError, ExtractedText, extract_text, find_passage, page_of_offset

__all__ = [
    "DocumentRecord",
    "DocumentTextError",
    "DocumentStore",
    "ExtractedText",
    "FetchAttempt",
    "FetchResult",
    "Outline",
    "PdfCandidate",
    "PdfFetcher",
    "Section",
    "build_outline",
    "extract_text",
    "find_passage",
    "outline_to_text",
    "page_of_offset",
    "rank_candidates",
]
