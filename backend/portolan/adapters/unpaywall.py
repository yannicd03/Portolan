"""Unpaywall open-access location adapter."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from .base import HttpClient


def _canonical_doi(identifier: str) -> str:
    """Return a lower-case DOI without a resolver URL or ``doi:`` prefix."""

    value = str(identifier).strip()
    if not value:
        raise ValueError("DOI must not be empty")

    if value.casefold().startswith("doi:"):
        value = value[4:].strip()
    elif value.casefold().startswith(("http://", "https://")):
        parsed = urlsplit(value)
        if parsed.netloc.casefold() not in {"doi.org", "dx.doi.org"}:
            raise ValueError(f"invalid DOI URL: {identifier!r}")
        value = parsed.path.lstrip("/")
    elif value.casefold().startswith("doi.org/"):
        value = value[8:]

    value = value.split("?", 1)[0].split("#", 1)[0].strip().strip("/")
    if not value:
        raise ValueError(f"invalid DOI: {identifier!r}")
    return value.casefold()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class UnpaywallAdapter:
    """Resolve a DOI to Unpaywall's open-access PDF locations."""

    source = "unpaywall"
    default_endpoint = "https://api.unpaywall.org/v2"

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
        email: str | None = None,
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
        self.offline = offline if injected_client is None or offline else None
        self.refresh = refresh if injected_client is None or refresh else None
        self.endpoint = endpoint.rstrip("/")
        self.email = email if email is not None else os.environ.get("PORTOLAN_CONTACT_EMAIL")
        self.http_requests = 0
        self.cache_hits = 0

    def _contact_email(self) -> str:
        email = _optional_text(self.email)
        if not email:
            raise ValueError(
                "Unpaywall requires a contact email; pass email= or set PORTOLAN_CONTACT_EMAIL"
            )
        return email

    def _record_http_result(self, response: httpx.Response) -> None:
        if response.headers.get("X-PTL-Cache", "").upper() == "HIT":
            self.cache_hits += 1
        else:
            self.http_requests += 1

    def _get(self, doi: str, *, email: str) -> Mapping[str, Any]:
        # The contact email is required on the wire but deliberately omitted
        # from cache_params so changing it does not duplicate a DOI snapshot.
        endpoint = f"{self.endpoint}/{quote(doi, safe='/')}"
        response = self.client.get(
            self.source,
            endpoint,
            params={"email": email},
            cache_params={},
            headers={"Accept": "application/json"},
            offline=self.offline,
            refresh=self.refresh,
        )
        self._record_http_result(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ValueError("Unpaywall returned malformed JSON") from exc
        if not isinstance(payload, Mapping):
            raise ValueError("Unpaywall returned an unexpected JSON payload")
        return payload

    @classmethod
    def _pdf_candidates(cls, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        best = payload.get("best_oa_location")
        locations: list[Any] = [best] if isinstance(best, Mapping) else []
        other_locations = payload.get("oa_locations")
        if isinstance(other_locations, (list, tuple)):
            locations.extend(other_locations)

        candidates: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for location in locations:
            if not isinstance(location, Mapping):
                continue
            url = _optional_text(location.get("url_for_pdf"))
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            candidates.append(
                {
                    "url": url,
                    "source": cls.source,
                    "version": _optional_text(location.get("version")),
                    "license": _optional_text(location.get("license")),
                    "host_type": _optional_text(location.get("host_type")),
                }
            )
        return candidates

    def lookup(self, doi: str) -> dict[str, Any] | None:
        """Look up one DOI, returning ``None`` when Unpaywall answers 404."""

        email = self._contact_email()
        normalized_doi = _canonical_doi(doi)
        try:
            payload = self._get(normalized_doi, email=email)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

        best_location = payload.get("best_oa_location")
        best_oa_url = (
            _optional_text(best_location.get("url_for_pdf"))
            if isinstance(best_location, Mapping)
            else None
        )
        is_oa = payload.get("is_oa")
        if not isinstance(is_oa, bool):
            is_oa = bool(is_oa)
        return {
            "doi": normalized_doi,
            "is_oa": is_oa,
            "pdf_candidates": self._pdf_candidates(payload),
            "best_oa_url": best_oa_url,
            "raw": dict(payload),
        }

    lookup_by_doi = lookup


Unpaywall = UnpaywallAdapter
