"""Public data models for the plain Python research run."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ResearchRequest(BaseModel):
    """Options accepted by :class:`~portolan.research.ResearchRunner`."""

    seeds: list[str] = Field(default_factory=list)
    query: str | None = None
    max_works: int = Field(100, ge=1, le=2000)
    snowball_depth: int = Field(1, ge=0, le=2)
    forward_per_work: int = Field(20, ge=0, le=200)
    from_year: int | None = None
    to_year: int | None = None
    acquire_pdfs: bool = True
    max_pdfs: int = Field(50, ge=0, le=2000)
    keyword_min_score: float = Field(0.3, ge=0, le=1)

    @model_validator(mode="after")
    def require_seed_or_query(self) -> ResearchRequest:
        if not self.seeds and not (self.query and self.query.strip()):
            raise ValueError("at least one seed or a query is required")
        return self


Stage = Literal[
    "resolve",
    "search",
    "snowball",
    "screen",
    "write",
    "concepts",
    "acquire",
    "done",
]


class RunProgress(BaseModel):
    """A progress event emitted while a run is executing."""

    stage: Stage
    message: str
    counts: dict[str, int] = Field(default_factory=dict)


class RunReport(BaseModel):
    """Summary returned after a successful research run."""

    project_id: str
    candidates_found: int
    screened_out: int
    included: int
    citations: int
    authors: int
    concepts: int
    pdfs_acquired: int
    pdfs_failed: int
    pdfs_skipped: int
    warnings: list[str] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime


class RunCancelled(Exception):
    """Raised when a caller cancels an in-progress research run."""


__all__ = [
    "ResearchRequest",
    "RunCancelled",
    "RunProgress",
    "RunReport",
    "Stage",
]
