"""FastAPI application for the v1 research graph and document store."""

from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse
from neo4j.exceptions import ServiceUnavailable

from ..documents import DocumentStore, PdfFetcher
from ..documents.models import Outline
from ..documents.text import find_passage
from ..graph import GraphView, ResearchGraph
from ..graph.models import Project, WorkSummary
from ..settings import Settings
from .models import (
    HealthResponse,
    LocateResponse,
    ProjectCreateRequest,
    ProjectDetailResponse,
    ResearchRunRequest,
    Run,
    WorkResponse,
)
from .runs import ActiveRunError, RunnerHandle, RunRegistry

log = logging.getLogger("portolan.api")


class _LockedGraph:
    """Serialize graph calls made by request workers and research workers.

    Neo4j itself is safe for concurrent calls.  The in-memory implementation is
    deliberately kept behind this small proxy as the API may run research work in
    parallel with request handlers.
    """

    def __init__(self, graph: ResearchGraph, lock: threading.RLock) -> None:
        self._graph = graph
        self._lock = lock

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._graph, name)
        if not callable(attribute):
            return attribute

        def call(*args: Any, **kwargs: Any) -> Any:
            with self._lock:
                return attribute(*args, **kwargs)

        return call


def _wait_for_store(graph: ResearchGraph, timeout: float) -> None:
    """Run schema setup, retrying while a containerised Neo4j is starting."""

    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        try:
            graph.ensure_schema()
            return
        except (ServiceUnavailable, OSError) as error:
            if time.monotonic() >= deadline:
                raise
            log.warning("graph store not reachable yet (%s); retrying", error)
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


def _default_runner_factory(
    graph: ResearchGraph, settings: Settings, request: ResearchRunRequest
) -> RunnerHandle:
    """Build one pipeline runner and its per-run resources on demand."""

    # The research package is developed independently from the API.  Keeping this
    # import inside the factory lets the app and its API tests run before that package
    # is installed or importable.
    from portolan.research import ResearchRequest, ResearchRunner, ResearchSources

    pipeline_request = ResearchRequest.model_validate(request.model_dump())
    sources = ResearchSources.default(
        settings.http_cache_dir,
        openalex_api_key=settings.openalex_api_key,
        semantic_scholar_api_key=settings.semantic_scholar_api_key,
        contact_email=settings.contact_email,
    )
    documents = DocumentStore(settings.documents_dir)
    fetcher: PdfFetcher | None = None
    try:
        fetcher = PdfFetcher(documents, contact_email=settings.contact_email)
        runner = ResearchRunner(graph, sources, documents=documents, fetcher=fetcher)
    except Exception:
        if fetcher is not None:
            fetcher.close()
        sources.close()
        raise

    def close() -> None:
        try:
            if fetcher is not None:
                fetcher.close()
        finally:
            sources.close()

    return RunnerHandle(runner=runner, request=pipeline_request, close=close)


def _graph_from_settings(settings: Settings) -> ResearchGraph:
    # Import lazily so importing ``portolan.api`` does not construct a Neo4j driver.
    from ..settings import make_graph

    return make_graph(settings)


def _project_or_404(graph: ResearchGraph, project_id: str) -> Project:
    project = graph.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


def _document_or_error(store: DocumentStore, sha256: str) -> Any:
    try:
        record = store.get(sha256)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if record is None:
        raise HTTPException(status_code=404, detail="document not found")
    return record


def _read_document_text(record: Any) -> str:
    """Read the extracted text for an already validated document record."""

    if record.text_path is None or not record.text_path.is_file():
        raise HTTPException(status_code=404, detail="document text not found")
    try:
        return record.text_path.read_text(encoding="utf-8")
    except OSError as error:
        raise HTTPException(status_code=404, detail="document text not found") from error


def _fold_for_snippet(value: str) -> tuple[str, list[int]]:
    """Fold text like :func:`find_passage` while retaining source offsets."""

    folded: list[str] = []
    offsets: list[int] = []
    for index, character in enumerate(value):
        if character.isspace():
            continue
        normalized = unicodedata.normalize("NFKC", character).casefold()
        folded.append(normalized)
        offsets.extend([index] * len(normalized))
    return "".join(folded), offsets


def _snippet(text: str, quote: str, offset: int, *, context: int = 100) -> str:
    """Return a bounded context preview with the matched source span marked."""

    folded_text, offsets = _fold_for_snippet(text)
    folded_quote, _ = _fold_for_snippet(quote)
    match_end = offset + len(quote)
    if folded_quote:
        for folded_offset, source_offset in enumerate(offsets):
            if source_offset != offset:
                continue
            if not folded_text.startswith(folded_quote, folded_offset):
                continue
            last_folded_offset = folded_offset + len(folded_quote) - 1
            if last_folded_offset < len(offsets):
                match_end = offsets[last_folded_offset] + 1
            break

    match_end = min(len(text), max(offset, match_end))
    start = max(0, offset - context)
    end = min(len(text), match_end + context)
    return f"{text[start:offset]}«{text[offset:match_end]}»{text[match_end:end]}"


_PAGE_MARKER = re.compile(r"^=== page (\d+) ===\r?$", re.MULTILINE)


def _page_blocks(text: str) -> dict[int, str]:
    """Split marker-formatted extracted text into page-numbered blocks."""

    markers = list(_PAGE_MARKER.finditer(text))
    return {
        int(marker.group(1)): text[marker.start() : markers[index + 1].start()]
        if index + 1 < len(markers)
        else text[marker.start() :]
        for index, marker in enumerate(markers)
    }


def create_app(
    settings: Settings | None = None,
    *,
    graph: ResearchGraph | None = None,
    runner_factory: Callable[..., Any] | None = None,
) -> FastAPI:
    """Create the API application, optionally using injected test doubles."""

    settings = settings or Settings.from_env()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        graph_instance = graph if graph is not None else _graph_from_settings(settings)
        graph_lock = threading.RLock()
        app.state.graph = graph_instance
        app.state.lock = graph_lock
        app.state.graph_lock = graph_lock
        app.state.document_store = DocumentStore(settings.documents_dir)
        registry: RunRegistry | None = None
        try:
            with graph_lock:
                _wait_for_store(graph_instance, settings.startup_timeout)
                projects = graph_instance.list_projects()
                if settings.seed_golden and not projects:
                    from ..golden import seed_demo_project

                    seed_demo_project(graph_instance, settings.repo_root)
                    projects = graph_instance.list_projects()
            synchronized_graph = _LockedGraph(graph_instance, graph_lock)
            registry = RunRegistry(
                synchronized_graph,
                settings,
                runner_factory or _default_runner_factory,
            )
            app.state.runs = registry
            log.info("portolan api ready: %s projects=%d", settings.describe(), len(projects))
            yield
        finally:
            if registry is not None:
                registry.shutdown()
            close = getattr(graph_instance, "close", None)
            if callable(close):
                close()
            app.state.graph = None
            app.state.runs = None

    app = FastAPI(title="Portolan API", version="0.1.0", lifespan=lifespan)

    def _state(request: Request) -> tuple[ResearchGraph, threading.RLock, RunRegistry]:
        return request.app.state.graph, request.app.state.lock, request.app.state.runs

    @app.get("/api/health", response_model=HealthResponse)
    def health(request: Request) -> HealthResponse:
        graph_instance, lock, _ = _state(request)
        with lock:
            count = len(graph_instance.list_projects())
        return HealthResponse(status="ok", store=settings.store, projects=count)

    @app.get("/api/projects", response_model=list[Project])
    def list_projects(request: Request) -> list[Project]:
        graph_instance, lock, _ = _state(request)
        with lock:
            return graph_instance.list_projects()

    @app.post("/api/projects", response_model=Project, status_code=201)
    def create_project(body: ProjectCreateRequest, request: Request) -> Project:
        graph_instance, lock, _ = _state(request)
        with lock:
            return graph_instance.create_project(body.name, body.description)

    @app.get("/api/projects/{project_id}", response_model=ProjectDetailResponse)
    def get_project(project_id: str, request: Request) -> ProjectDetailResponse:
        graph_instance, lock, _ = _state(request)
        with lock:
            project = _project_or_404(graph_instance, project_id)
            stats = graph_instance.project_stats(project_id)
        return ProjectDetailResponse(project=project, stats=stats)

    @app.delete("/api/projects/{project_id}", response_model=None, status_code=204)
    def delete_project(project_id: str, request: Request) -> None:
        graph_instance, lock, registry = _state(request)
        with lock:
            if registry.has_active(project_id):
                raise HTTPException(status_code=409, detail="project has an active run")
            _project_or_404(graph_instance, project_id)
            if registry.has_active(project_id):
                raise HTTPException(status_code=409, detail="project has an active run")
            if not graph_instance.delete_project(project_id):
                raise HTTPException(status_code=404, detail="project not found")

    @app.get("/api/projects/{project_id}/graph", response_model=GraphView)
    def project_graph(
        project_id: str,
        request: Request,
        authors: bool = Query(True),
        concepts: bool = Query(True),
    ) -> GraphView:
        graph_instance, lock, _ = _state(request)
        with lock:
            _project_or_404(graph_instance, project_id)
            return graph_instance.project_graph(
                project_id,
                include_authors=authors,
                include_concepts=concepts,
            )

    @app.get("/api/projects/{project_id}/search", response_model=list[WorkSummary])
    def search_project(
        project_id: str,
        request: Request,
        q: str = Query(...),
        limit: int = Query(20, ge=1, le=100),
    ) -> list[WorkSummary]:
        graph_instance, lock, _ = _state(request)
        with lock:
            _project_or_404(graph_instance, project_id)
            return graph_instance.search_works(project_id, q, limit=limit)

    @app.get("/api/works/{work_id:path}", response_model=WorkResponse)
    def get_work(work_id: str, request: Request, project: str | None = None) -> WorkResponse:
        graph_instance, lock, _ = _state(request)
        with lock:
            if project is not None:
                _project_or_404(graph_instance, project)
            try:
                neighborhood = graph_instance.work_neighborhood(work_id, project)
            except KeyError as error:
                raise HTTPException(status_code=404, detail="work not found") from error
        return WorkResponse(
            work=neighborhood.work,
            cites=neighborhood.cites,
            cited_by=neighborhood.cited_by,
            authors=[
                {"author": author, "position": position}
                for author, position in neighborhood.authors
            ],
            concepts=[
                {"concept": concept, "score": score} for concept, score in neighborhood.concepts
            ],
        )

    @app.post("/api/projects/{project_id}/runs", response_model=Run, status_code=202)
    def create_run(
        project_id: str,
        body: ResearchRunRequest,
        request: Request,
    ) -> Run:
        graph_instance, lock, registry = _state(request)
        with lock:
            _project_or_404(graph_instance, project_id)
            try:
                return registry.submit(project_id, body)
            except ActiveRunError as error:
                raise HTTPException(
                    status_code=409, detail="project already has an active run"
                ) from error

    @app.get("/api/projects/{project_id}/runs", response_model=list[Run])
    def list_runs(project_id: str, request: Request) -> list[Run]:
        graph_instance, lock, registry = _state(request)
        with lock:
            _project_or_404(graph_instance, project_id)
        return registry.list_for_project(project_id)

    @app.get("/api/runs/{run_id}", response_model=Run)
    def get_run(run_id: str, request: Request) -> Run:
        registry = request.app.state.runs
        run = registry.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return run

    @app.post("/api/runs/{run_id}/cancel", response_model=Run, status_code=202)
    def cancel_run(run_id: str, request: Request) -> Run:
        registry = request.app.state.runs
        run = registry.cancel(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return run

    @app.get("/api/documents/{sha256}/pdf", response_model=None)
    def get_document_pdf(sha256: str, request: Request) -> FileResponse:
        record = _document_or_error(request.app.state.document_store, sha256)
        return FileResponse(
            record.pdf_path,
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{sha256}.pdf"'},
        )

    @app.get("/api/documents/{sha256}/text", response_model=None)
    def get_document_text(sha256: str, request: Request) -> PlainTextResponse:
        record = _document_or_error(request.app.state.document_store, sha256)
        if record.text_path is None or not record.text_path.is_file():
            raise HTTPException(status_code=404, detail="document text not found")
        try:
            text = record.text_path.read_text(encoding="utf-8")
        except OSError as error:
            raise HTTPException(status_code=404, detail="document text not found") from error
        return PlainTextResponse(text, media_type="text/plain")

    @app.get("/api/documents/{sha256}/outline", response_model=Outline)
    def get_document_outline(sha256: str, request: Request) -> Outline:
        store: DocumentStore = request.app.state.document_store
        _document_or_error(store, sha256)
        try:
            outline = store.get_outline(sha256)
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if outline is None:
            raise HTTPException(status_code=404, detail="document outline not found")
        return outline

    @app.get("/api/documents/{sha256}/locate", response_model=LocateResponse)
    def locate_document_quote(
        sha256: str,
        request: Request,
        q: str = Query(..., min_length=3, max_length=2000),
        page: int | None = Query(None, ge=1),
    ) -> LocateResponse:
        record = _document_or_error(request.app.state.document_store, sha256)
        text = _read_document_text(record)
        matches = find_passage(text, q)
        if page is not None:
            matches.sort(key=lambda hit: hit[0] != page)
        hits = [
            {"page": match_page, "offset": offset, "snippet": _snippet(text, q, offset)}
            for match_page, offset in matches[:20]
        ]
        return LocateResponse(found=bool(hits), hits=hits)

    @app.get("/api/documents/{sha256}/pages", response_model=None)
    def get_document_pages(
        sha256: str,
        request: Request,
        start: int = Query(..., ge=1),
        end: int = Query(..., ge=1),
    ) -> PlainTextResponse:
        record = _document_or_error(request.app.state.document_store, sha256)
        if start > end:
            raise HTTPException(status_code=422, detail="start must be less than or equal to end")
        if end - start + 1 > 10:
            raise HTTPException(status_code=422, detail="at most 10 pages may be requested")

        text = _read_document_text(record)
        blocks = _page_blocks(text)
        if any(number not in blocks for number in range(start, end + 1)):
            raise HTTPException(status_code=422, detail="requested page is out of range")
        return PlainTextResponse(
            "".join(blocks[number] for number in range(start, end + 1)),
            media_type="text/plain",
        )

    return app


__all__ = ["create_app"]
