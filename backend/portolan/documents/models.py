"""Models used by the local document store and PDF fetcher."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict


class _DocumentModel(BaseModel):
    """Base configuration shared by the small document domain models."""

    model_config = ConfigDict(extra="forbid")


class Section(_DocumentModel):
    """One navigable section in a stored paper outline."""

    title: str
    level: int
    page: int
    char_offset: int | None = None


class Outline(_DocumentModel):
    """A paper outline and the source used to construct it."""

    sections: list[Section]
    source: Literal["pdf_outline", "headings", "none"]


class PdfCandidate(_DocumentModel):
    """A possible open-access PDF URL returned by a metadata source."""

    url: str
    source: str
    version: str | None = None
    license: str | None = None
    host_type: str | None = None


class DocumentRecord(_DocumentModel):
    """Metadata for one content-addressed PDF in a :class:`DocumentStore`."""

    sha256: str
    pdf_path: Path
    text_path: Path | None
    meta_path: Path
    pages: int | None
    bytes: int
    source_url: str
    source: str
    version: str | None
    license: str | None
    retrieved_at: datetime
    text_status: Literal["ok", "empty", "failed"]


class FetchAttempt(_DocumentModel):
    """Outcome of one request made while trying a candidate URL."""

    url: str
    status: int | None
    outcome: Literal["ok", "http_error", "not_pdf", "too_large", "network_error"]
    detail: str | None = None


class FetchResult(_DocumentModel):
    """All candidate attempts and the document, if one was successfully fetched."""

    document: DocumentRecord | None
    attempts: list[FetchAttempt]
