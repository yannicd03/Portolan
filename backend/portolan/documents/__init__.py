"""Open-access PDF acquisition and local document storage."""

from __future__ import annotations

from .fetch import PdfFetcher, rank_candidates
from .models import DocumentRecord, FetchAttempt, FetchResult, PdfCandidate
from .store import DocumentStore
from .text import DocumentTextError, ExtractedText, extract_text, find_passage, page_of_offset

__all__ = [
    "DocumentRecord",
    "DocumentTextError",
    "DocumentStore",
    "ExtractedText",
    "FetchAttempt",
    "FetchResult",
    "PdfCandidate",
    "PdfFetcher",
    "extract_text",
    "find_passage",
    "page_of_offset",
    "rank_candidates",
]
