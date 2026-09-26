"""Thin arXiv Atom adapter.

Each returned record follows the common adapter shape documented in
``portolan.adapters.base``: ``identifiers``, ``title``, ``year``, ``abstract``,
``venue``, ``publication_types``, ``open_access_pdf_url``, ``tldr``, ``source``,
``raw``, and ``raw_body``.  The raw entry mapping and exact Atom response are
kept alongside normalized values for auditability.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import httpx

from .base import (
    HttpClient,
    canonical_arxiv_id,
    clean_text,
    normalized_record,
    parse_year,
)

ATOM_NS = "http://www.w3.org/2005/Atom"
ARXIV_NS = "http://arxiv.org/schemas/atom"
ATOM = f"{{{ATOM_NS}}}"
ARXIV = f"{{{ARXIV_NS}}}"


def _text(element: ET.Element | None) -> str | None:
    return clean_text(element.text if element is not None else None)


def _entry_raw(entry: ET.Element) -> dict[str, Any]:
    links = [
        {
            "href": link.attrib.get("href"),
            "rel": link.attrib.get("rel"),
            "type": link.attrib.get("type"),
            "title": link.attrib.get("title"),
        }
        for link in entry.findall(f"{ATOM}link")
    ]
    authors: list[dict[str, str | None]] = []
    for author in entry.findall(f"{ATOM}author"):
        authors.append(
            {
                "name": _text(author.find(f"{ATOM}name")),
                "orcid": _text(author.find(f"{ARXIV}orcid")),
            }
        )
    return {
        "id": _text(entry.find(f"{ATOM}id")),
        "title": _text(entry.find(f"{ATOM}title")),
        "summary": _text(entry.find(f"{ATOM}summary")),
        "published": _text(entry.find(f"{ATOM}published")),
        "updated": _text(entry.find(f"{ATOM}updated")),
        "doi": _text(entry.find(f"{ARXIV}doi")),
        "journal_ref": _text(entry.find(f"{ARXIV}journal_ref")),
        "comment": _text(entry.find(f"{ARXIV}comment")),
        "primary_category": (
            entry.find(f"{ARXIV}primary_category").attrib.get("term")
            if entry.find(f"{ARXIV}primary_category") is not None
            else None
        ),
        "categories": [
            category.attrib.get("term")
            for category in entry.findall(f"{ATOM}category")
            if category.attrib.get("term")
        ],
        "authors": authors,
        "links": links,
    }


class ArxivAdapter:
    """Resolve arXiv identifiers and title searches through the Atom API."""

    source = "arxiv"
    default_endpoint = "https://export.arxiv.org/api/query"

    def __init__(
        self,
        client: HttpClient | None = None,
        *,
        http_client: HttpClient | None = None,
        cache_dir: Path | str = ".cache/portolan-http",
        offline: bool = False,
        refresh: bool = False,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        wall_clock: Callable[[], float] | None = None,
        source_intervals: Mapping[str, float] | None = None,
        endpoint: str = default_endpoint,
    ) -> None:
        if client is not None and http_client is not None:
            raise ValueError("pass only one of client or http_client")
        injected_client = client or http_client
        self.client = injected_client or HttpClient(
            cache_dir=cache_dir,
            offline=offline,
            refresh=refresh,
            transport=transport,
            clock=clock or time.monotonic,
            sleep=sleep or time.sleep,
            wall_clock=wall_clock or time.time,
            source_intervals=source_intervals,
        )
        # A supplied client owns its own offline/refresh policy.  ``True`` is
        # still an explicit adapter-level override, which is useful in callers
        # that share an online client for a one-off snapshot read.
        self.offline = offline if injected_client is None or offline else None
        self.refresh = refresh if injected_client is None or refresh else None
        self.endpoint = endpoint
        self.http_requests = 0
        self.cache_hits = 0

    def _get(self, params: Mapping[str, Any]) -> str:
        response = self.client.get(
            self.source,
            self.endpoint,
            params=params,
            headers={"Accept": "application/atom+xml"},
            offline=self.offline,
            refresh=self.refresh,
        )
        if response.headers.get("X-PTL-Cache", "").upper() == "HIT":
            self.cache_hits += 1
        else:
            self.http_requests += 1
        return response.text

    @staticmethod
    def _parse_entries(body: str) -> list[ET.Element]:
        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            raise ValueError("arXiv returned malformed Atom XML") from exc
        return list(root.findall(f"{ATOM}entry"))

    @classmethod
    def _record_from_entry(cls, entry: ET.Element, raw_body: str) -> dict[str, Any]:
        raw = _entry_raw(entry)
        arxiv_id = canonical_arxiv_id(raw.get("id"))
        doi = clean_text(raw.get("doi"))
        links = raw.get("links") or []
        pdf_url: str | None = None
        abstract_url: str | None = None
        for link in links:
            if not isinstance(link, Mapping):
                continue
            href = link.get("href")
            if not href:
                continue
            link_type = str(link.get("type") or "").lower()
            link_title = str(link.get("title") or "").lower()
            if (
                link_title == "pdf"
                or link_type == "application/pdf"
                or str(href).lower().endswith(".pdf")
            ):
                pdf_url = str(href)
            elif link.get("rel") == "alternate":
                abstract_url = str(href)
        if pdf_url is None and arxiv_id:
            pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        if abstract_url is None and arxiv_id:
            abstract_url = f"https://arxiv.org/abs/{arxiv_id}"

        record = normalized_record(
            source=cls.source,
            identifiers={
                "arxiv": arxiv_id,
                "doi": doi,
                "url": abstract_url,
            },
            title=raw.get("title"),
            year=parse_year(raw.get("published")),
            abstract=raw.get("summary"),
            venue=raw.get("journal_ref"),
            publication_types=["preprint"],
            open_access_pdf_url=pdf_url,
            tldr=None,
            raw=raw,
            raw_body=raw_body,
        )
        record["categories"] = list(raw.get("categories") or [])
        record["primary_category"] = raw.get("primary_category")
        return record

    def search_title(self, title: str, *, max_results: int = 10) -> list[dict[str, Any]]:
        """Search title terms using arXiv's exact title field query."""

        escaped = re.sub(r'(["\\])', r"\\\1", title.strip())
        body = self._get(
            {
                "search_query": f'ti:"{escaped}"',
                "start": 0,
                "max_results": max(1, int(max_results)),
            }
        )
        return [self._record_from_entry(entry, body) for entry in self._parse_entries(body)]

    def lookup_many(self, arxiv_ids: list[str] | tuple[str, ...]) -> list[dict[str, Any]]:
        """Fetch several arXiv records in one ``id_list`` request."""

        canonical_ids: list[str] = []
        for arxiv_id in arxiv_ids:
            canonical = canonical_arxiv_id(arxiv_id)
            if not canonical:
                raise ValueError("arXiv identifier must not be empty")
            if canonical not in canonical_ids:
                canonical_ids.append(canonical)
        if not canonical_ids:
            return []
        body = self._get(
            {
                "id_list": ",".join(canonical_ids),
                "max_results": len(canonical_ids),
            }
        )
        return [self._record_from_entry(entry, body) for entry in self._parse_entries(body)]

    def lookup_by_id(self, arxiv_id: str) -> dict[str, Any] | None:
        """Fetch one arXiv record by identifier, or ``None`` when absent."""

        records = self.lookup_many([arxiv_id])
        return records[0] if records else None

    # Descriptive aliases keep call sites readable and make the adapter pleasant
    # to use without introducing a second implementation.
    get_by_id = lookup_by_id
    lookup = lookup_by_id
    batch_lookup = lookup_many
    lookup_batch = lookup_many
    search = search_title
    search_by_title = search_title


ArXivAdapter = ArxivAdapter
