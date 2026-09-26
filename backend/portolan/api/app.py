"""FastAPI application: a read-only API over the configured graph store.

M0 scaffold. It serves the citation map over whatever the store holds (the golden
mini-graph when ``PORTOLAN_SEED_GOLDEN`` is set). Projects, chat and review lenses
arrive with the later milestones.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from neo4j.exceptions import ServiceUnavailable
from pydantic import BaseModel

from ..golden import seed_golden
from ..settings import Settings, make_repository
from ..store import GraphRepository
from .graph_view import citation_graph

log = logging.getLogger("portolan.api")


class HealthResponse(BaseModel):
    status: str
    store: str
    works: int


class WorkNode(BaseModel):
    id: str
    title: str
    year: int | None
    sourceTier: str | None
    isSurvey: bool
    citedBy: int


class CitationEdge(BaseModel):
    source: str
    target: str


class GraphResponse(BaseModel):
    nodes: list[WorkNode]
    edges: list[CitationEdge]


def _count_works_when_ready(repository: GraphRepository, timeout: float) -> int:
    """Wait for a store that is still starting (e.g. Neo4j in Compose), then count works."""

    deadline = time.monotonic() + timeout
    while True:
        try:
            return len(citation_graph(repository)["nodes"])
        except (ServiceUnavailable, OSError) as error:
            if time.monotonic() >= deadline:
                raise
            log.warning("graph store not reachable yet (%s); retrying", error)
            time.sleep(2)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Neo4j reports "property key does not exist" for queries against a still-empty
    # database as warnings; they are expected on first start and drown real messages.
    logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        repository = make_repository(settings)
        works = _count_works_when_ready(repository, settings.startup_timeout)
        if settings.seed_golden and works == 0:
            stats = seed_golden(repository, settings.repo_root)
            works = len(citation_graph(repository)["nodes"])
            log.info("seeded golden graph: %s", stats)
        log.info("portolan api ready: %s works=%d", settings.describe(), works)
        app.state.repository = repository
        app.state.lock = threading.Lock()
        try:
            yield
        finally:
            repository.close()
            # pyoxigraph releases its on-disk lock only when the Store is garbage-collected.
            app.state.repository = None
            del repository

    app = FastAPI(title="Portolan API", version="0.1.0", lifespan=lifespan)

    def _repo(request: Request) -> tuple[Any, threading.Lock]:
        return request.app.state.repository, request.app.state.lock

    @app.get("/api/health", response_model=HealthResponse)
    def health(request: Request) -> HealthResponse:
        repository, lock = _repo(request)
        with lock:
            works = len(citation_graph(repository)["nodes"])
        return HealthResponse(status="ok", store=settings.store, works=works)

    @app.get("/api/graph", response_model=GraphResponse)
    def graph(request: Request) -> dict[str, Any]:
        repository, lock = _repo(request)
        with lock:
            return citation_graph(repository)

    return app
