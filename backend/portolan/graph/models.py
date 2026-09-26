"""Pydantic models for Portolan's v1 research graph.

The graph package deliberately has its own small model layer.  The RDF shaped
models in :mod:`portolan.models` describe the M0 spike and are not used here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class GraphModel(BaseModel):
    """Common configuration for the store-neutral graph DTOs."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Project(GraphModel):
    """A user-owned research project."""

    id: str
    name: str
    description: str | None = None
    created_at: datetime


class WorkNode(GraphModel):
    """Bibliographic work stored as a global ``Work`` node."""

    id: str | None = None
    title: str
    year: int | None = None
    abstract: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    openalex_id: str | None = None
    s2_id: str | None = None
    venue: str | None = None
    work_type: str | None = None
    source_tier: str | None = None
    cited_by_count: int | None = None
    keywords: list[str] = Field(default_factory=list)
    keyword_scores: list[float] = Field(default_factory=list)
    document_sha256: str | None = None
    document_source_url: str | None = None


class AuthorNode(GraphModel):
    """Author node shared by every project containing one of its works."""

    id: str | None = None
    name: str
    orcid: str | None = None
    openalex_id: str | None = None
    s2_id: str | None = None


class ConceptNode(GraphModel):
    """Flat keyword concept with the variants seen in source data."""

    id: str
    label: str
    aliases: list[str] = Field(default_factory=list)


_DISCOVERY_METHODS = frozenset({"seed", "search", "backward", "forward", "manual"})


class Inclusion(GraphModel):
    """Project membership and the provenance of a work's discovery."""

    project_id: str
    work_id: str
    discovered_via: str
    depth: int = 0
    score: float | None = None

    @field_validator("discovered_via", mode="before")
    @classmethod
    def validate_discovered_via(cls, value: Any) -> str:
        value = getattr(value, "value", value)
        if value not in _DISCOVERY_METHODS:
            choices = ", ".join(sorted(_DISCOVERY_METHODS))
            raise ValueError(f"discovered_via must be one of: {choices}")
        return str(value)


class WorkSummary(GraphModel):
    """Compact work representation returned by map and search reads."""

    id: str
    title: str
    year: int | None = None
    cited_by_count: int = 0
    document_sha256: str | None = None

    @field_validator("cited_by_count", mode="before")
    @classmethod
    def missing_cited_by_count_is_zero(cls, value: Any) -> int:
        return 0 if value is None else value


class WorkNeighborhood(GraphModel):
    """A work and the map relationships immediately around it."""

    work: WorkNode
    cites: list[WorkSummary] = Field(default_factory=list)
    cited_by: list[WorkSummary] = Field(default_factory=list)
    authors: list[tuple[AuthorNode, int]] = Field(default_factory=list)
    concepts: list[tuple[ConceptNode, float]] = Field(default_factory=list)


class GraphView(GraphModel):
    """Deterministically ordered nodes and edges for the project graph view."""

    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)


class ProjectStats(GraphModel):
    """Counts for the works and shared entities visible through one project."""

    works: int = 0
    citations: int = 0
    authors: int = 0
    concepts: int = 0
    documents: int = 0


__all__ = [
    "AuthorNode",
    "ConceptNode",
    "GraphView",
    "Inclusion",
    "Project",
    "ProjectStats",
    "WorkNeighborhood",
    "WorkNode",
    "WorkSummary",
]
