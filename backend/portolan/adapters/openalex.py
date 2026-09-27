"""OpenAlex API adapter.

The adapter keeps the source-native work payload in ``raw`` while exposing the
small, source-neutral record shape shared by the other Portolan adapters.  All
HTTP state belongs to the injected :class:`~portolan.adapters.base.HttpClient`,
which makes the adapter usable with offline response snapshots and an
``httpx.MockTransport`` in tests.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx

from .base import HttpClient, canonical_arxiv_id, normalized_record

_OPENALEX_ID_RE = re.compile(r"^[WA]\d+$", re.IGNORECASE)
_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
_ARXIV_NEW_RE = re.compile(r"^\d{4}\.\d{4,5}(?:v\d+)?$", re.IGNORECASE)
_ARXIV_OLD_RE = re.compile(r"^[a-z][a-z0-9-]+(?:\.[a-z0-9-]+)?/\S+$", re.IGNORECASE)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _bare_openalex_id(value: Any) -> str | None:
    """Return an OpenAlex work or author id without its URL prefix."""

    if value is None:
        return None
    candidate = str(value).strip()
    if not candidate:
        return None
    if "://" in candidate:
        parsed = urlsplit(candidate)
        candidate = unquote(parsed.path).rstrip("/").split("/")[-1]
    elif candidate.lower().startswith("openalex:"):
        candidate = candidate.split(":", 1)[1].strip()
    else:
        candidate = candidate.rstrip("/").split("/")[-1]
    return candidate.upper() if _OPENALEX_ID_RE.fullmatch(candidate) else None


def _normalize_doi(value: Any) -> str | None:
    """Normalize a DOI URL, ``doi:`` value, or bare DOI to lowercase."""

    if value is None:
        return None
    candidate = str(value).strip()
    if not candidate:
        return None
    lowered = candidate.lower()
    if lowered.startswith("doi:"):
        candidate = candidate[4:].strip()
    elif lowered.startswith(
        ("http://doi.org/", "https://doi.org/", "http://dx.doi.org/", "https://dx.doi.org/")
    ):
        candidate = urlsplit(candidate).path.lstrip("/")
    candidate = unquote(candidate).strip().rstrip(".")
    return candidate.lower() if _DOI_RE.fullmatch(candidate) else None


def _arxiv_from_url(value: Any) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip()
    if "arxiv.org" not in candidate.lower():
        return None
    parsed = urlsplit(candidate)
    path = unquote(parsed.path).strip("/")
    parts = path.split("/")
    if len(parts) < 2 or parts[0].lower() not in {"abs", "pdf", "html"}:
        return None
    return canonical_arxiv_id("/".join(parts[1:]))


def _arxiv_from_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    match = re.fullmatch(r"10\.48550/arxiv\.(.+)", doi, flags=re.IGNORECASE)
    if not match:
        return None
    return canonical_arxiv_id(match.group(1))


def _canonical_identifier(identifier: str) -> tuple[str, str]:
    """Classify an OpenAlex work id, DOI, or arXiv id for API requests."""

    value = str(identifier).strip()
    if not value:
        raise ValueError("OpenAlex identifier must not be empty")

    openalex_id = _bare_openalex_id(value)
    if openalex_id and openalex_id.startswith("W"):
        return "openalex", openalex_id

    doi = _normalize_doi(value)
    if doi:
        arxiv_id = _arxiv_from_doi(doi)
        if arxiv_id:
            return "doi", f"10.48550/arxiv.{arxiv_id}".lower()
        return "doi", doi

    is_arxiv_url = "arxiv.org" in value.lower()
    is_arxiv_prefix = value.lower().startswith("arxiv:")
    bare_arxiv = bool(_ARXIV_NEW_RE.fullmatch(value) or _ARXIV_OLD_RE.fullmatch(value))
    if is_arxiv_url or is_arxiv_prefix or bare_arxiv:
        arxiv_id = _arxiv_from_url(value) if is_arxiv_url else canonical_arxiv_id(value)
        if arxiv_id:
            return "doi", f"10.48550/arxiv.{arxiv_id}".lower()

    raise ValueError(f"unsupported OpenAlex identifier: {identifier!r}")


def _strip_identifier(value: Any, *, prefix: str) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip()
    if not candidate:
        return None
    parsed = urlsplit(candidate)
    if parsed.scheme and parsed.netloc:
        candidate = unquote(parsed.path).rstrip("/").split("/")[-1]
    candidate = candidate.rstrip("/")
    if candidate.lower().startswith(prefix.lower()):
        candidate = candidate[len(prefix) :]
    return candidate or None


def _normalize_orcid(value: Any) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip().rstrip("/")
    if candidate.lower().startswith("https://orcid.org/") or candidate.lower().startswith(
        "http://orcid.org/"
    ):
        candidate = candidate.rsplit("/", 1)[-1]
    return candidate or None


def _reconstruct_abstract(value: Any) -> str | None:
    """Rebuild OpenAlex's word-to-position abstract representation."""

    if not isinstance(value, Mapping) or not value:
        return None
    words: dict[int, str] = {}
    for word, positions in value.items():
        if not isinstance(positions, (list, tuple)):
            continue
        for position in positions:
            try:
                index = int(position)
            except (TypeError, ValueError):
                continue
            if index >= 0:
                words.setdefault(index, str(word))
    if not words:
        return None
    return " ".join(words[index] for index in sorted(words))


class OpenAlexAdapter:
    """Fetch and normalize OpenAlex works, searches, and relationships."""

    source = "openalex"
    default_endpoint = "https://api.openalex.org"
    default_select = (
        "id,doi,ids,display_name,title,publication_year,abstract_inverted_index,type,"
        "primary_location,authorships,keywords,topics,primary_topic,referenced_works,"
        "cited_by_count,best_oa_location,locations,open_access"
    )
    batch_limit = 50

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
        api_key: str | None = None,
        endpoint: str = default_endpoint,
        select: str | Sequence[str] = default_select,
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
        self.api_key = api_key if api_key is not None else os.environ.get("OPENALEX_API_KEY")
        self.select = self._field_string(select)
        self.http_requests = 0
        self.cache_hits = 0

    @staticmethod
    def _field_string(fields: str | Sequence[str]) -> str:
        if isinstance(fields, str):
            return fields
        return ",".join(str(field) for field in fields)

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _record_http_result(self, response: httpx.Response) -> None:
        if response.headers.get("X-PTL-Cache", "").upper() == "HIT":
            self.cache_hits += 1
        else:
            self.http_requests += 1

    def _get(
        self,
        path: str,
        *,
        params: Mapping[str, Any],
        cache_params: Mapping[str, Any] | None = None,
    ) -> tuple[Any, str]:
        response = self.client.get(
            self.source,
            f"{self.endpoint}/{path.lstrip('/')}",
            params=params,
            cache_params=cache_params if cache_params is not None else params,
            headers=self._headers(),
            offline=self.offline,
            refresh=self.refresh,
        )
        self._record_http_result(response)
        try:
            return response.json(), response.text
        except ValueError as exc:
            raise ValueError("OpenAlex returned malformed JSON") from exc

    @staticmethod
    def _items(payload: Any) -> list[Mapping[str, Any]]:
        if isinstance(payload, Mapping):
            value = payload.get("results")
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping)]
            if payload.get("id") or payload.get("doi") or payload.get("ids"):
                return [payload]
        elif isinstance(payload, list):
            return [item for item in payload if isinstance(item, Mapping)]
        return []

    @classmethod
    def _record_from_work(
        cls, item: Mapping[str, Any], *, raw_body: str | None = None
    ) -> dict[str, Any]:
        ids = item.get("ids")
        ids = ids if isinstance(ids, Mapping) else {}
        openalex_id = _bare_openalex_id(item.get("id") or ids.get("openalex"))
        doi = _normalize_doi(item.get("doi") or ids.get("doi"))

        locations = [
            location
            for location in _as_list(item.get("locations"))
            if isinstance(location, Mapping)
        ]
        best_location = item.get("best_oa_location")
        best_location = best_location if isinstance(best_location, Mapping) else {}
        location_values = [best_location, *locations]

        arxiv_id = _arxiv_from_doi(doi)
        if arxiv_id is None:
            for location in location_values:
                for url in (
                    location.get("pdf_url"),
                    location.get("landing_page_url"),
                ):
                    arxiv_id = _arxiv_from_url(url)
                    if arxiv_id:
                        break
                if arxiv_id:
                    break
        if arxiv_id is None:
            arxiv_id = canonical_arxiv_id(ids.get("arxiv"))

        pmid = _strip_identifier(ids.get("pmid"), prefix="pmid:")
        pmcid = _strip_identifier(ids.get("pmcid"), prefix="pmcid:")
        if pmid and pmid.lower().startswith("pubmed.ncbi.nlm.nih.gov"):
            pmid = pmid.rsplit("/", 1)[-1]
        if pmcid and pmcid.lower().startswith("articles"):
            pmcid = pmcid.rsplit("/", 1)[-1]

        primary_location = item.get("primary_location")
        primary_location = primary_location if isinstance(primary_location, Mapping) else {}
        primary_source = primary_location.get("source")
        primary_source = primary_source if isinstance(primary_source, Mapping) else {}
        venue = primary_source.get("display_name")
        if not venue:
            venue = next(
                (
                    location.get("source", {}).get("display_name")
                    for location in locations
                    if isinstance(location.get("source"), Mapping)
                    and location.get("source", {}).get("display_name")
                ),
                None,
            )

        landing_page_url = best_location.get("landing_page_url")
        open_access = item.get("open_access")
        if not landing_page_url and isinstance(open_access, Mapping):
            landing_page_url = open_access.get("oa_url")
        if not landing_page_url:
            landing_page_url = primary_location.get("landing_page_url")

        identifiers: dict[str, Any] = {
            "openalex": openalex_id,
            "doi": doi,
            "arxiv": arxiv_id,
            "pmid": pmid,
            "pmcid": pmcid,
            "url": landing_page_url,
        }
        publication_type = item.get("type")
        publication_types = _as_list(publication_type) if publication_type else []
        authors: list[dict[str, Any]] = []
        for position, authorship in enumerate(_as_list(item.get("authorships")), start=1):
            if not isinstance(authorship, Mapping):
                continue
            author = authorship.get("author")
            author = author if isinstance(author, Mapping) else {}
            authors.append(
                {
                    "name": author.get("display_name") or authorship.get("raw_author_name"),
                    "openalex_id": _bare_openalex_id(author.get("id")),
                    "orcid": _normalize_orcid(author.get("orcid")),
                    "position": position,
                }
            )

        keywords: list[dict[str, Any]] = []
        for key, kind in (("keywords", "keyword"), ("topics", "topic")):
            for value in _as_list(item.get(key)):
                if not isinstance(value, Mapping):
                    continue
                keywords.append(
                    {
                        "term": value.get("display_name"),
                        "score": value.get("score"),
                        "kind": kind,
                    }
                )

        referenced_works: list[str] = []
        for reference in _as_list(item.get("referenced_works")):
            reference_id = _bare_openalex_id(reference)
            if reference_id:
                referenced_works.append(reference_id)

        pdf_candidates: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for index, location in enumerate(location_values):
            if not isinstance(location, Mapping):
                continue
            pdf_url = location.get("pdf_url")
            if not pdf_url:
                continue
            if index > 0 and not location.get("is_oa"):
                continue
            url = str(pdf_url).strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            source = location.get("source")
            source = source if isinstance(source, Mapping) else {}
            pdf_candidates.append(
                {
                    "url": url,
                    "source": cls.source,
                    "version": location.get("version"),
                    "license": location.get("license"),
                    "host_type": source.get("type"),
                }
            )

        cited_by_count = item.get("cited_by_count")
        try:
            cited_by_count = int(cited_by_count) if cited_by_count is not None else None
        except (TypeError, ValueError):
            cited_by_count = None

        record = normalized_record(
            source=cls.source,
            identifiers=identifiers,
            title=item.get("display_name") or item.get("title"),
            year=item.get("publication_year"),
            abstract=_reconstruct_abstract(item.get("abstract_inverted_index")),
            venue=venue,
            publication_types=publication_types,
            open_access_pdf_url=best_location.get("pdf_url"),
            tldr=None,
            raw=dict(item),
            raw_body=raw_body,
        )
        record.update(
            {
                "openalex_id": openalex_id,
                "authors": authors,
                "keywords": keywords,
                "referenced_works": referenced_works,
                "cited_by_count": cited_by_count,
                "pdf_candidates": pdf_candidates,
            }
        )
        return record

    @staticmethod
    def _record_keys(record: Mapping[str, Any]) -> set[tuple[str, str]]:
        identifiers = record.get("identifiers")
        identifiers = identifiers if isinstance(identifiers, Mapping) else {}
        keys: set[tuple[str, str]] = set()
        openalex_id = _bare_openalex_id(record.get("openalex_id") or identifiers.get("openalex"))
        doi = _normalize_doi(identifiers.get("doi"))
        if openalex_id:
            keys.add(("openalex", openalex_id))
        if doi:
            keys.add(("doi", doi))
        return keys

    @classmethod
    def _record_identity(cls, record: Mapping[str, Any]) -> tuple[str, str] | None:
        keys = cls._record_keys(record)
        return next((key for key in keys if key[0] == "openalex"), next(iter(keys), None))

    def lookup(self, identifier: str) -> dict[str, Any] | None:
        kind, value = _canonical_identifier(identifier)
        api_identifier = value if kind == "openalex" else f"doi:{value}"
        path = f"works/{api_identifier}"
        try:
            payload, raw_body = self._get(path, params={}, cache_params={})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        items = self._items(payload)
        return self._record_from_work(items[0], raw_body=raw_body) if items else None

    def lookup_many(self, identifiers: Sequence[str]) -> list[dict[str, Any]]:
        normalized: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for identifier in identifiers:
            key = _canonical_identifier(identifier)
            if key not in seen:
                seen.add(key)
                normalized.append(key)
        if not normalized:
            return []

        grouped: dict[str, list[str]] = {"openalex": [], "doi": []}
        for kind, value in normalized:
            grouped[kind].append(value)

        records_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        for kind, values in grouped.items():
            for offset in range(0, len(values), self.batch_limit):
                chunk = values[offset : offset + self.batch_limit]
                params = {
                    "filter": f"{kind}:{'|'.join(chunk)}",
                    "per_page": self.batch_limit,
                    "select": self.select,
                }
                payload, raw_body = self._get("works", params=params, cache_params=params)
                for item in self._items(payload):
                    record = self._record_from_work(item, raw_body=raw_body)
                    for key in self._record_keys(record):
                        records_by_key.setdefault(key, record)
                    # A mixed batch can contain an OpenAlex id and a DOI for
                    # the same work.  Prefer the response from the matching
                    # filter when both requests return a record under that
                    # key; this keeps each input's order deterministic.
                    for requested in chunk:
                        if (kind, requested) in self._record_keys(record):
                            records_by_key[(kind, requested)] = record

        records: list[dict[str, Any]] = []
        emitted: set[tuple[str, str]] = set()
        for key in normalized:
            record = records_by_key.get(key)
            if record is None:
                continue
            identity = self._record_identity(record)
            if identity is not None and identity in emitted:
                continue
            if identity is not None:
                emitted.add(identity)
            records.append(record)
        return records

    def search(
        self,
        query: str,
        *,
        limit: int = 25,
        from_year: int | None = None,
        to_year: int | None = None,
    ) -> list[dict[str, Any]]:
        remaining = max(0, int(limit))
        if remaining == 0:
            return []
        results: list[dict[str, Any]] = []
        cursor = "*"
        while len(results) < remaining:
            per_page = min(100, remaining - len(results))
            filters: list[str] = []
            if from_year is not None:
                filters.append(f"from_publication_date:{int(from_year)}-01-01")
            if to_year is not None:
                filters.append(f"to_publication_date:{int(to_year)}-12-31")
            params: dict[str, Any] = {
                "search": query,
                "per_page": per_page,
                "cursor": cursor,
                "select": self.select,
            }
            if filters:
                params["filter"] = ",".join(filters)
            payload, raw_body = self._get("works", params=params, cache_params=params)
            items = self._items(payload)
            for item in items[: remaining - len(results)]:
                results.append(self._record_from_work(item, raw_body=raw_body))
            meta = payload.get("meta") if isinstance(payload, Mapping) else {}
            next_cursor = meta.get("next_cursor") if isinstance(meta, Mapping) else None
            if not next_cursor or next_cursor == cursor or not items:
                break
            cursor = str(next_cursor)
        return results

    def references(self, identifier: str) -> list[dict[str, Any]]:
        record = self.lookup(identifier)
        if record is None:
            return []
        references = record.get("referenced_works")
        if not isinstance(references, list):
            return []
        return self.lookup_many(
            [reference for reference in references if isinstance(reference, str)]
        )

    def cited_by(self, identifier: str, *, limit: int = 200) -> list[dict[str, Any]]:
        kind, value = _canonical_identifier(identifier)
        if kind == "openalex":
            openalex_id = value
        else:
            record = self.lookup(value)
            if record is None:
                return []
            openalex_id = record.get("openalex_id")
            if not isinstance(openalex_id, str):
                return []

        remaining = max(0, int(limit))
        if remaining == 0:
            return []
        results: list[dict[str, Any]] = []
        cursor = "*"
        while len(results) < remaining:
            per_page = min(100, remaining - len(results))
            params = {
                "filter": f"cites:{openalex_id}",
                "per_page": per_page,
                "cursor": cursor,
                "select": self.select,
            }
            payload, raw_body = self._get("works", params=params, cache_params=params)
            items = self._items(payload)
            for item in items[: remaining - len(results)]:
                results.append(self._record_from_work(item, raw_body=raw_body))
            meta = payload.get("meta") if isinstance(payload, Mapping) else {}
            next_cursor = meta.get("next_cursor") if isinstance(meta, Mapping) else None
            if not next_cursor or next_cursor == cursor or not items:
                break
            cursor = str(next_cursor)
        return results

    get_by_id = lookup
    batch_lookup = lookup_many
    batch_get = lookup_many
    lookup_batch = lookup_many
    get_references = references
    get_cited_by = cited_by
    citations = cited_by


OpenAlex = OpenAlexAdapter
