"""Thin Crossref REST adapter.

Returned records have the common plain-dict shape documented in
``portolan.adapters.base`` and retain the Crossref ``message`` mapping under ``raw``
plus the exact response body under ``raw_body``.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from .base import HttpClient, normalized_record, parse_year


class CrossrefAdapter:
    """Resolve DOI metadata and search Crossref's bibliographic index."""

    source = "crossref"
    default_endpoint = "https://api.crossref.org"

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
        mailto: str | None = None,
        email: str | None = None,
        endpoint: str = default_endpoint,
    ) -> None:
        if client is not None and http_client is not None:
            raise ValueError("pass only one of client or http_client")
        if mailto is not None and email is not None:
            raise ValueError("pass only one of mailto or email")
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
        self.offline = offline if injected_client is None or offline else None
        self.refresh = refresh if injected_client is None or refresh else None
        self.endpoint = endpoint.rstrip("/")
        configured_mailto = mailto if mailto is not None else email
        self.mailto = (
            configured_mailto
            if configured_mailto is not None
            else os.environ.get("UNPAYWALL_EMAIL")
        )

    def _params(self, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        result = dict(params or {})
        if self.mailto:
            result.setdefault("mailto", self.mailto.removeprefix("mailto:"))
        return result

    def _get(self, path: str, *, params: Mapping[str, Any] | None = None) -> tuple[Any, str]:
        actual_params = self._params(params)
        response = self.client.get(
            self.source,
            f"{self.endpoint}/{path.lstrip('/')}",
            params=actual_params,
            headers={"Accept": "application/json"},
            offline=self.offline,
            refresh=self.refresh,
        )
        try:
            return response.json(), response.text
        except ValueError as exc:
            raise ValueError("Crossref returned malformed JSON") from exc

    @staticmethod
    def _message(payload: Any) -> Mapping[str, Any] | None:
        if not isinstance(payload, Mapping):
            return None
        message = payload.get("message")
        if isinstance(message, Mapping):
            return message
        return payload if "title" in payload or "DOI" in payload else None

    @staticmethod
    def _record_from_work(work: Mapping[str, Any], raw_body: str) -> dict[str, Any]:
        titles = work.get("title") or []
        title = titles[0] if isinstance(titles, list) and titles else titles
        containers = work.get("container-title") or work.get("container_title") or []
        venue = containers[0] if isinstance(containers, list) and containers else containers
        doi = work.get("DOI") or work.get("doi")
        url = work.get("URL") or work.get("url")
        pdf_url: str | None = None
        links = work.get("link") or []
        if isinstance(links, list):
            for link in links:
                if not isinstance(link, Mapping) or not link.get("URL"):
                    continue
                content_type = str(link.get("content-type") or link.get("content_type") or "")
                link_type = str(link.get("content-version") or "")
                href = str(link["URL"])
                is_pdf = (
                    content_type.lower() == "application/pdf"
                    or link_type.lower() == "pdf"
                    or href.lower().endswith(".pdf")
                )
                if is_pdf:
                    pdf_url = href
                    break
        if pdf_url is None:
            resource = work.get("resource")
            if isinstance(resource, Mapping):
                primary = resource.get("primary")
                if isinstance(primary, Mapping):
                    resource_url = primary.get("URL") or primary.get("url")
                    if resource_url:
                        pdf_url = str(resource_url)

        publication_types: list[Any] = []
        work_type = work.get("type")
        if work_type:
            publication_types.append(work_type)
        subtype = work.get("subtype")
        if subtype:
            publication_types.append(subtype)
        date_value = (
            work.get("published")
            or work.get("published-print")
            or work.get("published-online")
            or work.get("issued")
            or work.get("created")
        )
        return normalized_record(
            source="crossref",
            identifiers={"doi": doi, "url": url},
            title=title,
            year=parse_year(date_value),
            abstract=work.get("abstract"),
            venue=venue,
            publication_types=publication_types,
            open_access_pdf_url=pdf_url,
            tldr=None,
            raw=dict(work),
            raw_body=raw_body,
        )

    def lookup_doi(self, doi: str) -> dict[str, Any] | None:
        """Fetch one Crossref work by DOI."""

        value = str(doi).strip()
        if value.lower().startswith("https://doi.org/"):
            value = value.split("/", 3)[-1]
        if value.lower().startswith("doi:"):
            value = value[4:].strip()
        if not value:
            raise ValueError("DOI must not be empty")
        payload, raw_body = self._get(f"works/{quote(value, safe='')}")
        message = self._message(payload)
        return self._record_from_work(message, raw_body) if message is not None else None

    def search_title(self, title: str, *, rows: int = 10) -> list[dict[str, Any]]:
        """Search Crossref's bibliographic title query."""

        payload, raw_body = self._get(
            "works",
            params={"query.bibliographic": title, "rows": max(1, int(rows))},
        )
        message = payload.get("message") if isinstance(payload, Mapping) else None
        items = message.get("items", []) if isinstance(message, Mapping) else []
        if not isinstance(items, list):
            return []
        return [
            self._record_from_work(item, raw_body) for item in items if isinstance(item, Mapping)
        ]

    get_by_doi = lookup_doi
    resolve_doi = lookup_doi
    lookup = lookup_doi
    search = search_title
    search_by_title = search_title
