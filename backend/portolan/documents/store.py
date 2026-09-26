"""Content-addressed storage for downloaded PDFs and their extracted text."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pypdf

from .models import DocumentRecord
from .text import extract_text

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_RECORD_FIELDS = (
    "sha256",
    "pdf_path",
    "text_path",
    "meta_path",
    "pages",
    "bytes",
    "source_url",
    "source",
    "version",
    "license",
    "retrieved_at",
    "text_status",
)


def _validate_sha256(sha256: str) -> str:
    """Validate a content hash before using it as a path component."""

    if not isinstance(sha256, str) or _SHA256_RE.fullmatch(sha256) is None:
        raise ValueError("sha256 must be exactly 64 lowercase hexadecimal characters")
    return sha256


def _atomic_write(path: Path, data: bytes) -> None:
    """Write bytes to ``path`` through a same-directory temporary file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            with suppress(FileNotFoundError):
                temporary.unlink()


def _relative_path(root: Path, path: Path) -> str:
    """Return a path in the portable form used by ``meta.json``."""

    return path.relative_to(root).as_posix()


def _has_page_text(text: str) -> bool:
    """Return whether extracted text contains content beyond page markers."""

    for line in text.splitlines():
        if re.fullmatch(r"=== page \d+ ===", line.strip()):
            continue
        if line.strip():
            return True
    return False


class DocumentStore:
    """Store PDFs by SHA-256 with a text and metadata sidecar."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _document_dir(self, sha256: str) -> Path:
        sha256 = _validate_sha256(sha256)
        return self.root / sha256[:2] / sha256

    def pdf_path(self, sha256: str) -> Path:
        """Return the canonical PDF path for a validated content hash."""

        return self._document_dir(sha256) / "paper.pdf"

    def text_path(self, sha256: str) -> Path:
        """Return the canonical extracted-text path for a validated hash."""

        return self._document_dir(sha256) / "paper.txt"

    def _meta_path(self, sha256: str) -> Path:
        return self._document_dir(sha256) / "meta.json"

    def put_pdf(
        self,
        data: bytes,
        *,
        source_url: str,
        source: str,
        version: str | None = None,
        license: str | None = None,
    ) -> DocumentRecord:
        """Store a PDF and return its content-addressed record.

        Extraction is deliberately attempted before writing the sidecars, but a failed
        extraction still leaves the original PDF available for later processing.
        """

        sha256 = hashlib.sha256(data).hexdigest()
        existing = self.get(sha256)
        if existing is not None:
            return existing

        document_dir = self._document_dir(sha256)
        document_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = document_dir / "paper.pdf"
        text_path = document_dir / "paper.txt"
        meta_path = document_dir / "meta.json"

        pages: int | None = None
        text_status: str
        extracted_text: str | None = None
        try:
            extracted = extract_text(data)
            pages = extracted.pages
            extracted_text = extracted.text
            text_status = "ok" if _has_page_text(extracted_text) else "empty"
        except Exception:
            # A malformed or encrypted PDF must not prevent retaining the source bytes.
            text_status = "failed"

        _atomic_write(pdf_path, data)
        if text_status == "failed":
            with suppress(FileNotFoundError):
                text_path.unlink()
        else:
            assert extracted_text is not None
            _atomic_write(text_path, extracted_text.encode("utf-8"))

        retrieved_at = datetime.now(UTC)
        record = DocumentRecord(
            sha256=sha256,
            pdf_path=pdf_path,
            text_path=None if text_status == "failed" else text_path,
            meta_path=meta_path,
            pages=pages,
            bytes=len(data),
            source_url=source_url,
            source=source,
            version=version,
            license=license,
            retrieved_at=retrieved_at,
            text_status=text_status,
        )
        metadata = self._metadata_for(record)
        _atomic_write(
            meta_path,
            json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"),
        )
        return record

    def _metadata_for(self, record: DocumentRecord) -> dict[str, Any]:
        """Serialize a record while keeping filesystem paths relative to the store."""

        return {
            "sha256": record.sha256,
            "pdf_path": _relative_path(self.root, record.pdf_path),
            "text_path": (
                None if record.text_path is None else _relative_path(self.root, record.text_path)
            ),
            "meta_path": _relative_path(self.root, record.meta_path),
            "pages": record.pages,
            "bytes": record.bytes,
            "source_url": record.source_url,
            "source": record.source,
            "version": record.version,
            "license": record.license,
            "retrieved_at": record.retrieved_at.isoformat(),
            "text_status": record.text_status,
            "text_extractor": f"pypdf {pypdf.__version__}",
        }

    def get(self, sha256: str) -> DocumentRecord | None:
        """Load a record from metadata, returning ``None`` for a missing entry."""

        sha256 = _validate_sha256(sha256)
        meta_path = self._meta_path(sha256)
        pdf_path = self.pdf_path(sha256)
        if not meta_path.is_file() or not pdf_path.is_file():
            return None

        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or any(field not in payload for field in _RECORD_FIELDS):
            return None
        if payload.get("sha256") != sha256:
            return None

        stored_pdf = payload.get("pdf_path")
        stored_meta = payload.get("meta_path")
        expected_pdf = _relative_path(self.root, pdf_path)
        expected_meta = _relative_path(self.root, meta_path)
        if stored_pdf != expected_pdf or stored_meta != expected_meta:
            return None

        stored_text = payload.get("text_path")
        if stored_text is None:
            if payload.get("text_status") != "failed":
                return None
            text_path: Path | None = None
        elif stored_text == _relative_path(self.root, self.text_path(sha256)):
            text_path = self.text_path(sha256)
            if payload.get("text_status") == "failed" or not text_path.is_file():
                return None
        else:
            return None

        record_data = {
            "sha256": sha256,
            "pdf_path": pdf_path,
            "text_path": text_path,
            "meta_path": meta_path,
            "pages": payload["pages"],
            "bytes": payload["bytes"],
            "source_url": payload["source_url"],
            "source": payload["source"],
            "version": payload["version"],
            "license": payload["license"],
            "retrieved_at": payload["retrieved_at"],
            "text_status": payload["text_status"],
        }
        try:
            return DocumentRecord(**record_data)
        except (TypeError, ValueError):
            return None

    def has(self, sha256: str) -> bool:
        """Return whether a complete PDF record exists for ``sha256``."""

        return self.get(sha256) is not None

    def iter_documents(self) -> Iterator[DocumentRecord]:
        """Yield valid records in deterministic content-hash order."""

        if not self.root.is_dir():
            return
        for prefix in sorted(self.root.iterdir(), key=lambda path: path.name):
            if (
                not prefix.is_dir()
                or len(prefix.name) != 2
                or not re.fullmatch(r"[0-9a-f]{2}", prefix.name)
            ):
                continue
            for document_dir in sorted(prefix.iterdir(), key=lambda path: path.name):
                if not document_dir.is_dir() or _SHA256_RE.fullmatch(document_dir.name) is None:
                    continue
                record = self.get(document_dir.name)
                if record is not None:
                    yield record

    def __iter__(self) -> Iterator[DocumentRecord]:
        return self.iter_documents()
