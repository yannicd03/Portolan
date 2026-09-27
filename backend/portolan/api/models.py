"""HTTP request and response models for the v1 Portolan API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..documents.models import Outline
from ..graph.models import (
    AuthorNode,
    ConceptNode,
    GraphView,
    Project,
    ProjectStats,
    WorkNode,
    WorkSummary,
)


class ApiModel(BaseModel):
    """Base configuration shared by API DTOs."""

    model_config = ConfigDict(extra="forbid")


class HealthResponse(ApiModel):
    status: Literal["ok"]
    store: str
    projects: int


class ProjectCreateRequest(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def strip_and_validate_name(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("name must be a string")
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class ProjectDetailResponse(ApiModel):
    project: Project
    stats: ProjectStats


class AnalysisCluster(ApiModel):
    id: str
    label: str
    size: int
    top_concepts: list[str]
    work_ids: list[str]


AnalysisRole = Literal["foundational", "bridge", "hub", "emerging", "peripheral"]


class AnalysisWork(ApiModel):
    cluster: str | None
    pagerank: float
    betweenness: float
    local_in: int
    local_out: int
    roles: list[AnalysisRole]


class MainPathEdge(ApiModel):
    source: str
    target: str
    spc: int


class MainPath(ApiModel):
    work_ids: list[str]
    edges: list[MainPathEdge]


class ProjectAnalysis(ApiModel):
    project_id: str
    computed_at: datetime
    clusters: list[AnalysisCluster]
    works: dict[str, AnalysisWork]
    main_path: MainPath


class AuthorMembership(ApiModel):
    author: AuthorNode
    position: int


class ConceptMembership(ApiModel):
    concept: ConceptNode
    score: float


class WorkResponse(ApiModel):
    work: WorkNode
    cites: list[WorkSummary]
    cited_by: list[WorkSummary]
    authors: list[AuthorMembership]
    concepts: list[ConceptMembership]


class LocateHit(ApiModel):
    """One occurrence returned by the document quote locator."""

    page: int
    offset: int
    snippet: str


class LocateResponse(ApiModel):
    """Quote locator result for one stored document."""

    found: bool
    hits: list[LocateHit]


class ResearchRunRequest(ApiModel):
    """Request body accepted by the research run endpoint.

    This deliberately mirrors the pipeline's request model without importing the
    pipeline package.  The conversion to that model happens in the default runner
    factory, at execution time.
    """

    seeds: list[str] = Field(default_factory=list)
    query: str | None = None
    max_works: int = Field(default=100, ge=1, le=2000)
    snowball_depth: int = Field(default=2, ge=0, le=2)
    forward_per_work: int = Field(default=20, ge=0, le=200)
    from_year: int | None = None
    to_year: int | None = None
    acquire_pdfs: bool = True
    max_pdfs: int = Field(default=50, ge=0, le=2000)
    keyword_min_score: float = Field(default=0.3, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def require_seed_or_query(self) -> ResearchRunRequest:
        if not self.seeds and not (self.query and self.query.strip()):
            raise ValueError("at least one seed or a query is required")
        return self


class RunProgress(ApiModel):
    stage: str
    message: str
    counts: dict[str, int]
    at: datetime


RunStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class Run(ApiModel):
    id: str
    project_id: str
    status: RunStatus
    request: ResearchRunRequest
    progress: list[RunProgress] = Field(default_factory=list)
    report: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


__all__ = [
    "AnalysisCluster",
    "AnalysisWork",
    "AuthorMembership",
    "ConceptMembership",
    "GraphView",
    "HealthResponse",
    "LocateHit",
    "LocateResponse",
    "MainPath",
    "MainPathEdge",
    "Outline",
    "Project",
    "ProjectAnalysis",
    "ProjectCreateRequest",
    "ProjectDetailResponse",
    "ProjectStats",
    "ResearchRunRequest",
    "Run",
    "RunProgress",
    "WorkNode",
    "WorkResponse",
    "WorkSummary",
]
