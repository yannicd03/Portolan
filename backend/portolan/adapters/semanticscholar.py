"""Thin Semantic Scholar Academic Graph adapter.

The paper records returned by this module use the common shape documented in
``portolan.adapters.base`` and retain the source-native JSON item in ``raw`` plus the
exact response body in ``raw_body``.  Relationship methods return edge mappings
with the normalized related paper under ``paper`` and preserve Semantic
Scholar's ``intents``, ``isInfluential``, and citation contexts.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from .base import HttpClient, canonical_arxiv_id, normalized_record


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _canonical_s2_input(identifier: str) -> str:
    value = str(identifier).strip()
    if not value:
        raise ValueError("Semantic Scholar identifier must not be empty")
    if value.lower().startswith("arxiv:"):
        arxiv_id = canonical_arxiv_id(value)
        if not arxiv_id:
            raise ValueError(f"invalid arXiv identifier: {identifier!r}")
        return f"ARXIV:{arxiv_id}"
    if value.lower().startswith("doi:"):
        return f"DOI:{value[4:].strip()}"
    return value


def _paper_identifiers(item: Mapping[str, Any]) -> dict[str, str | None]:
    external = item.get("externalIds") or item.get("external_ids") or {}
    if not isinstance(external, Mapping):
        external = {}
    identifiers: dict[str, str | None] = {
        "s2": item.get("paperId") or item.get("paper_id"),
        "doi": external.get("DOI") or external.get("doi"),
        "arxiv": external.get("ArXiv") or external.get("arxiv"),
        "url": item.get("url"),
    }
    # Keep useful source identifiers without making them part of the required
    # common vocabulary.  This is useful when a later resolver needs ACL, PMID,
    # or CorpusId to disambiguate a record.
    for key, value in external.items():
        if value is None:
            continue
        lowered = str(key).lower()
        if lowered in {"doi", "arxiv", "corpusid", "acl", "pmid", "pmcid", "mag"}:
            identifiers.setdefault(lowered, str(value))
    return identifiers


class SemanticScholarAdapter:
    """Use batch/detail, title search, reference, and citation endpoints."""

    source = "semanticscholar"
    default_endpoint = "https://api.semanticscholar.org/graph/v1"
    default_batch_limit = 500
    default_paper_fields = (
        "paperId,externalIds,title,year,abstract,tldr,venue,publicationTypes,"
        "openAccessPdf,url,references.externalIds,references.title"
    )
    default_relationship_fields = (
        "paperId,externalIds,title,year,abstract,tldr,venue,publicationTypes,openAccessPdf,url"
    )

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
        paper_fields: str | Sequence[str] = default_paper_fields,
        relationship_fields: str | Sequence[str] = default_relationship_fields,
        batch_limit: int = default_batch_limit,
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
        self.api_key = (
            api_key if api_key is not None else os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
        )
        self.paper_fields = self._field_string(paper_fields)
        self.relationship_fields = self._field_string(relationship_fields)
        self.batch_limit = int(batch_limit)
        if self.batch_limit < 1:
            raise ValueError("batch_limit must be at least 1")
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
            headers["x-api-key"] = self.api_key
        return headers

    def _record_http_result(self, response: httpx.Response) -> None:
        if response.headers.get("X-PTL-Cache", "").upper() == "HIT":
            self.cache_hits += 1
        else:
            self.http_requests += 1

    def _get(
        self, path: str, *, params: Mapping[str, Any], cache_params: Mapping[str, Any]
    ) -> tuple[Any, str]:
        response = self.client.get(
            self.source,
            f"{self.endpoint}/{path.lstrip('/')}",
            params=params,
            cache_params=cache_params,
            headers=self._headers(),
            offline=self.offline,
            refresh=self.refresh,
        )
        self._record_http_result(response)
        try:
            return response.json(), response.text
        except ValueError as exc:
            raise ValueError("Semantic Scholar returned malformed JSON") from exc

    def _post(
        self,
        path: str,
        *,
        params: Mapping[str, Any],
        payload: Mapping[str, Any],
        cache_params: Mapping[str, Any],
    ) -> tuple[Any, str]:
        response = self.client.post(
            self.source,
            f"{self.endpoint}/{path.lstrip('/')}",
            params=params,
            json=dict(payload),
            cache_params=cache_params,
            headers=self._headers(),
            offline=self.offline,
            refresh=self.refresh,
        )
        self._record_http_result(response)
        try:
            return response.json(), response.text
        except ValueError as exc:
            raise ValueError("Semantic Scholar returned malformed JSON") from exc

    @staticmethod
    def _items(payload: Any) -> list[Mapping[str, Any] | None]:
        if isinstance(payload, list):
            return [item if isinstance(item, Mapping) else None for item in payload]
        if isinstance(payload, Mapping):
            data = payload.get("data")
            if isinstance(data, list):
                return [item if isinstance(item, Mapping) else None for item in data]
            if "paperId" in payload or "paper_id" in payload:
                return [payload]
        return []

    @classmethod
    def _record_from_paper(
        cls, item: Mapping[str, Any], *, raw_body: str | None = None
    ) -> dict[str, Any]:
        open_access = item.get("openAccessPdf") or item.get("open_access_pdf") or {}
        if not isinstance(open_access, Mapping):
            open_access = {}
        tldr = item.get("tldr")
        if isinstance(tldr, Mapping):
            tldr = tldr.get("text")
        record = normalized_record(
            source=cls.source,
            identifiers=_paper_identifiers(item),
            title=item.get("title"),
            year=item.get("year"),
            abstract=item.get("abstract"),
            venue=item.get("venue"),
            publication_types=_as_list(item.get("publicationTypes")),
            open_access_pdf_url=open_access.get("url"),
            tldr=tldr,
            raw=dict(item),
            raw_body=raw_body,
        )
        if "references" in item:
            record["references"] = cls._batch_references(item.get("references"))
        return record

    @staticmethod
    def _batch_references(value: Any) -> list[dict[str, Any]]:
        """Return the bibliographic subset requested from batch references.

        A batch response should not be treated as an intent response.  Keeping
        only identifiers, titles, and the optional paper id also prevents a
        source response containing incidental relationship metadata from making
        keyless builds look as if they had been intent-enriched.
        """

        if not isinstance(value, (list, tuple)):
            return []
        references: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, Mapping):
                continue
            reference = {
                key: item[key]
                for key in ("paperId", "paper_id", "externalIds", "external_ids", "title")
                if key in item
            }
            if reference:
                references.append(reference)
        return references

    @staticmethod
    def _identifier_keys(item: Mapping[str, Any]) -> set[tuple[str, str]]:
        """Return comparable identifier keys for a paper or batch reference."""

        external = item.get("externalIds") or item.get("external_ids") or {}
        if not isinstance(external, Mapping):
            external = {}
        identifiers: set[tuple[str, str]] = set()

        paper_id = item.get("paperId") or item.get("paper_id")
        if paper_id:
            identifiers.add(("s2", str(paper_id).casefold().strip()))

        arxiv_id = external.get("ArXiv") or external.get("arxiv")
        if arxiv_id:
            canonical = canonical_arxiv_id(arxiv_id)
            if canonical:
                identifiers.add(("arxiv", canonical.casefold()))

        doi = external.get("DOI") or external.get("doi")
        if doi:
            identifiers.add(("doi", str(doi).casefold().strip()))

        normalized = item.get("identifiers")
        if isinstance(normalized, Mapping):
            for kind in ("s2", "arxiv", "doi"):
                value = normalized.get(kind)
                if not value:
                    continue
                if kind == "arxiv":
                    value = canonical_arxiv_id(value)
                if value:
                    identifiers.add((kind, str(value).casefold().strip()))
        return identifiers

    @staticmethod
    def _relationship_metadata(edge: Mapping[str, Any]) -> dict[str, Any]:
        """Extract metadata that is present in an intent response."""

        raw = edge.get("raw")
        raw = raw if isinstance(raw, Mapping) else {}
        metadata: dict[str, Any] = {}
        if "intents" in raw:
            value = edge.get("intents")
            metadata["intents"] = list(value) if isinstance(value, (list, tuple)) else value
        if "isInfluential" in raw or "is_influential" in raw:
            metadata["isInfluential"] = edge.get("isInfluential")
        if "contexts" in raw or "citationContexts" in raw:
            value = edge.get("contexts")
            metadata["contexts"] = list(value) if isinstance(value, (list, tuple)) else value
        return metadata

    def enrich_with_intents(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        with_intents: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Optionally enrich batch references with intent relationship data.

        Intent enrichment is deliberately opt-in and key-gated.  A missing key
        therefore produces the same bibliographic records as the default batch
        path and never attempts a per-paper relationship request.  Individual
        enrichment failures are retained as warnings on that record rather than
        making the batch lookup fail.
        """

        enriched: list[dict[str, Any]] = []
        for record in records:
            copied = dict(record)
            references = record.get("references")
            if isinstance(references, list):
                copied["references"] = [
                    dict(reference) if isinstance(reference, Mapping) else reference
                    for reference in references
                ]
            enriched.append(copied)
        if not with_intents or not self.api_key:
            return enriched

        for record in enriched:
            references = record.get("references")
            if not isinstance(references, list):
                continue
            record_keys = self._identifier_keys(record)
            s2_identifier = next(
                (value for kind, value in record_keys if kind == "s2"),
                None,
            )
            if not s2_identifier:
                continue
            try:
                relationships = self.references(s2_identifier, limit=limit)
            except Exception as exc:  # enrichment must not fail the verified record
                record.setdefault("warnings", []).append(
                    f"Semantic Scholar intent enrichment failed: {exc}"
                )
                continue

            by_identifier: dict[tuple[str, str], dict[str, Any]] = {}
            for relationship in relationships:
                if not isinstance(relationship, Mapping):
                    continue
                paper = relationship.get("paper") or relationship.get("citedPaper")
                if not isinstance(paper, Mapping):
                    continue
                metadata = self._relationship_metadata(relationship)
                if not metadata:
                    continue
                for identifier in self._identifier_keys(paper):
                    by_identifier[identifier] = metadata

            for reference in references:
                if not isinstance(reference, dict):
                    continue
                metadata = next(
                    (
                        by_identifier[identifier]
                        for identifier in self._identifier_keys(reference)
                        if identifier in by_identifier
                    ),
                    None,
                )
                if metadata:
                    reference.update(metadata)
        return enriched

    def lookup_many(
        self, identifiers: Sequence[str], *, with_intents: bool = False
    ) -> list[dict[str, Any]]:
        """Batch-resolve identifiers, optionally adding keyed intent metadata."""

        normalized_ids = [_canonical_s2_input(identifier) for identifier in identifiers]
        if not normalized_ids:
            return []
        records: list[dict[str, Any]] = []
        for offset in range(0, len(normalized_ids), self.batch_limit):
            chunk = normalized_ids[offset : offset + self.batch_limit]
            params = {"fields": self.paper_fields}
            cache_params = {"fields": self.paper_fields, "ids": sorted(chunk)}
            payload, raw_body = self._post(
                "paper/batch",
                params=params,
                payload={"ids": chunk},
                cache_params=cache_params,
            )
            for item in self._items(payload):
                if item is not None:
                    records.append(self._record_from_paper(item, raw_body=raw_body))
        return self.enrich_with_intents(records, with_intents=with_intents)

    def lookup_by_id(self, identifier: str, *, with_intents: bool = False) -> dict[str, Any] | None:
        records = self.lookup_many([identifier], with_intents=with_intents)
        return records[0] if records else None

    def search_title(self, title: str, *, limit: int = 10) -> list[dict[str, Any]]:
        """Search Semantic Scholar's title-aware paper search endpoint."""

        params = {
            "query": title,
            "limit": max(1, int(limit)),
            "fields": self.paper_fields,
        }
        payload, raw_body = self._get("paper/search", params=params, cache_params=params)
        return [
            self._record_from_paper(item, raw_body=raw_body)
            for item in self._items(payload)
            if item is not None
        ]

    def _relationships(
        self,
        identifier: str,
        *,
        direction: str,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        paper_id = _canonical_s2_input(identifier)
        other_key = "citedPaper" if direction == "references" else "citingPaper"
        path = f"paper/{quote(paper_id, safe=':')}/{direction}"
        related_fields = ",".join(
            f"{other_key}.{field.strip()}"
            for field in self.relationship_fields.split(",")
            if field.strip()
        )
        fields = f"{related_fields},contexts,intents,isInfluential,citationContexts"
        params = {
            "fields": fields,
            "limit": max(1, int(limit)),
            "offset": max(0, int(offset)),
        }
        payload, raw_body = self._get(path, params=params, cache_params=params)
        data = payload.get("data", []) if isinstance(payload, Mapping) else payload
        if not isinstance(data, list):
            return []

        edges: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, Mapping):
                continue
            related = item.get(other_key)
            paper = (
                self._record_from_paper(related, raw_body=raw_body)
                if isinstance(related, Mapping)
                else None
            )
            contexts = item.get("contexts") or item.get("citationContexts") or []
            intents = item.get("intents") or []
            influential = item.get("isInfluential")
            edge: dict[str, Any] = {
                "direction": direction,
                "paper": paper,
                "intents": list(intents) if isinstance(intents, (list, tuple)) else [intents],
                "isInfluential": influential,
                "is_influential": influential,
                "contexts": list(contexts) if isinstance(contexts, (list, tuple)) else [contexts],
                "citationContexts": list(contexts)
                if isinstance(contexts, (list, tuple))
                else [contexts],
                "citation_contexts": list(contexts)
                if isinstance(contexts, (list, tuple))
                else [contexts],
                "raw": dict(item),
                "raw_body": raw_body,
            }
            edge["citedPaper" if direction == "references" else "citingPaper"] = paper
            edges.append(edge)
        return edges

    def references(
        self, identifier: str, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        return self._relationships(identifier, direction="references", limit=limit, offset=offset)

    def citations(
        self, identifier: str, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        return self._relationships(identifier, direction="citations", limit=limit, offset=offset)

    get_by_id = lookup_by_id
    batch_lookup = lookup_many
    batch_get = lookup_many
    lookup_batch = lookup_many
    search = search_title
    search_by_title = search_title
    get_references = references
    get_citations = citations


S2Adapter = SemanticScholarAdapter
