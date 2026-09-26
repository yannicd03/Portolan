"""Store-neutral contract and helpers for the v1 research graph."""

from __future__ import annotations

import hashlib
import re
import secrets
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from .models import (
    AuthorNode,
    ConceptNode,
    GraphView,
    Inclusion,
    Project,
    ProjectStats,
    WorkNeighborhood,
    WorkNode,
    WorkSummary,
)


def _field(source: object | None, name: str) -> Any:
    """Read a field from either a graph model or a plain mapping."""

    if source is None:
        return None
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _clean(value: object | None) -> str | None:
    """Return a trimmed string, treating empty identifiers as missing."""

    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_title(title: str) -> str:
    """Normalize a title for deterministic fallback identity minting."""

    normalized = unicodedata.normalize("NFKC", title)
    return " ".join(normalized.casefold().split())


def normalize_arxiv_id(arxiv_id: str) -> str:
    """Remove the version suffix from an arXiv identifier."""

    return re.sub(r"v\d+$", "", arxiv_id.strip(), flags=re.IGNORECASE)


def mint_work_id(
    work: WorkNode | Mapping[str, Any] | None = None,
    *,
    id: str | None = None,
    title: str | None = None,
    year: int | None = None,
    doi: str | None = None,
    arxiv_id: str | None = None,
    openalex_id: str | None = None,
    s2_id: str | None = None,
) -> str:
    """Mint the stable global id for a work.

    An explicit ``id`` is retained when supplied.  New ids follow the v1
    precedence order: OpenAlex, DOI, arXiv, Semantic Scholar, then a title
    and year digest.  The keyword arguments make this helper convenient for
    callers that have not constructed a model yet.
    """

    explicit_id = id if id is not None else _field(work, "id")
    if (value := _clean(explicit_id)) is not None:
        return value

    values = {
        "title": title if title is not None else _field(work, "title"),
        "year": year if year is not None else _field(work, "year"),
        "doi": doi if doi is not None else _field(work, "doi"),
        "arxiv_id": arxiv_id if arxiv_id is not None else _field(work, "arxiv_id"),
        "openalex_id": openalex_id if openalex_id is not None else _field(work, "openalex_id"),
        "s2_id": s2_id if s2_id is not None else _field(work, "s2_id"),
    }

    if (value := _clean(values["openalex_id"])) is not None:
        return f"openalex:{value}"
    if (value := _clean(values["doi"])) is not None:
        return f"doi:{value.casefold()}"
    if (value := _clean(values["arxiv_id"])) is not None:
        return f"arxiv:{normalize_arxiv_id(value)}"
    if (value := _clean(values["s2_id"])) is not None:
        return f"s2:{value}"

    normalized_title = normalize_title(str(values["title"] or ""))
    normalized_year = "" if values["year"] is None else str(values["year"])
    digest = hashlib.sha1(f"{normalized_title}{normalized_year}".encode()).hexdigest()[:16]
    return f"title:{digest}"


def mint_author_id(
    author: AuthorNode | Mapping[str, Any] | None = None,
    *,
    id: str | None = None,
    name: str | None = None,
    orcid: str | None = None,
    openalex_id: str | None = None,
    s2_id: str | None = None,
) -> str:
    """Mint the stable global id for an author."""

    explicit_id = id if id is not None else _field(author, "id")
    if (value := _clean(explicit_id)) is not None:
        return value

    values = {
        "name": name if name is not None else _field(author, "name"),
        "orcid": orcid if orcid is not None else _field(author, "orcid"),
        "openalex_id": openalex_id if openalex_id is not None else _field(author, "openalex_id"),
        "s2_id": s2_id if s2_id is not None else _field(author, "s2_id"),
    }
    if (value := _clean(values["orcid"])) is not None:
        return f"orcid:{value}"
    if (value := _clean(values["openalex_id"])) is not None:
        return f"openalex:{value}"
    if (value := _clean(values["s2_id"])) is not None:
        return f"s2:{value}"

    normalized_name = " ".join(str(values["name"] or "").casefold().split())
    return f"name:{normalized_name}"


def mint_project_id(name: str) -> str:
    """Mint a human-readable project slug with a random collision suffix."""

    normalized = unicodedata.normalize("NFKC", name).casefold()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-") or "project"
    return f"{slug}-{secrets.token_hex(3)}"


_LUCENE_SPECIALS = frozenset('+-!(){}[]^"~*?:\\/')


def escape_lucene_query(query: str) -> str:
    """Escape Lucene query syntax while preserving ordinary user text."""

    return "".join(f"\\{char}" if char in _LUCENE_SPECIALS else char for char in query)


class ResearchGraph(Protocol):
    """The read/write boundary shared by the memory and Neo4j stores."""

    def ensure_schema(self) -> None: ...

    def create_project(self, name: str, description: str | None = None) -> Project: ...

    def get_project(self, id: str) -> Project | None: ...

    def list_projects(self) -> list[Project]: ...

    def delete_project(self, id: str) -> bool: ...

    def upsert_work(self, work: WorkNode) -> WorkNode: ...

    def project_works(self, project_id: str) -> list[WorkNode]: ...

    def include_work(self, inclusion: Inclusion) -> None: ...

    def add_citation(self, citing_id: str, cited_id: str) -> None: ...

    def upsert_author(self, author: AuthorNode) -> AuthorNode: ...

    def set_authors(self, work_id: str, authors: Sequence[tuple[str, int]]) -> None: ...

    def upsert_concept(self, concept: ConceptNode) -> ConceptNode: ...

    def set_concepts(self, work_id: str, concepts: Sequence[tuple[str, float]]) -> None: ...

    def set_document(self, work_id: str, sha256: str, source_url: str | None) -> None: ...

    def get_work(self, id: str) -> WorkNode | None: ...

    def search_works(
        self, project_id: str, query: str, *, limit: int = 20
    ) -> list[WorkSummary]: ...

    def work_neighborhood(
        self, work_id: str, project_id: str | None = None
    ) -> WorkNeighborhood: ...

    def works_by_author(self, author_id: str, project_id: str) -> list[WorkSummary]: ...

    def works_by_concept(self, concept_id: str, project_id: str) -> list[WorkSummary]: ...

    def project_graph(
        self, project_id: str, *, include_authors: bool = True, include_concepts: bool = True
    ) -> GraphView: ...

    def project_stats(self, project_id: str) -> ProjectStats: ...


__all__ = [
    "ResearchGraph",
    "escape_lucene_query",
    "mint_author_id",
    "mint_project_id",
    "mint_work_id",
    "normalize_arxiv_id",
    "normalize_title",
]
