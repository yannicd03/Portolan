"""Project frontier and structural gap endpoints."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import asdict
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from ..analysis.frontier import frontier_concepts, frontier_scores
from ..analysis.gap_store import GapStore, verify_gap
from ..analysis.gaps import GapHypothesis, detect_gaps
from ..analysis.service import analyze_project
from ..graph.base import ResearchGraph
from ..graph.models import GraphView, WorkNode
from ..research.sources import ResearchSources
from ..settings import Settings


class GapUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["proposed", "accepted", "rejected"] | None = None
    note: str | None = None


class FrontierWorkResponse(BaseModel):
    work_id: str
    title: str
    year: int | None
    score: float
    components: dict[str, float]


class FrontierConceptResponse(BaseModel):
    concept_id: str
    label: str
    first_year: int
    adoption_by_year: dict[int, int]


class FrontierResponse(BaseModel):
    now_year: int | None
    window_years: int
    works: list[FrontierWorkResponse]
    concepts: list[FrontierConceptResponse]


def _invoke_graph(provider: Callable[..., ResearchGraph], project_id: str) -> ResearchGraph:
    """Accept graph providers taking either no arguments or a project id."""

    try:
        signature = inspect.signature(provider)
    except (TypeError, ValueError):
        return provider()
    required = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        and parameter.default is inspect.Parameter.empty
    ]
    return provider(project_id) if required else provider()


def _project_graph(graph: ResearchGraph, project_id: str) -> GraphView:
    if graph.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    return graph.project_graph(project_id, include_authors=False, include_concepts=True)


def _enrich_work_data(view: GraphView, graph: ResearchGraph, project_id: str) -> GraphView:
    """Add bibliographic fields omitted by the compact graph view."""

    works = {work.id: work for work in graph.project_works(project_id)}
    nodes: list[dict[str, Any]] = []
    for original in view.nodes:
        node = dict(original)
        work = works.get(node.get("id")) if node.get("kind") == "work" else None
        if work is not None:
            data = dict(node.get("data") or {})
            data.update(
                year=work.year,
                cited_by_count=work.cited_by_count,
                work_type=work.work_type,
                venue=work.venue,
                source_tier=work.source_tier,
                arxiv_id=work.arxiv_id,
                doi=work.doi,
            )
            node["data"] = data
        nodes.append(node)
    return GraphView(nodes=nodes, edges=view.edges)


def _project_identifiers(works: Iterable[WorkNode]) -> list[str]:
    """Bibliographic ids that mark a search hit as already inside the project."""

    return [
        identifier
        for work in works
        for identifier in (work.openalex_id, work.doi, work.arxiv_id)
        if identifier
    ]


def _default_sources(settings: Settings) -> ResearchSources:
    return ResearchSources.default(
        settings.http_cache_dir,
        openalex_api_key=settings.openalex_api_key,
        semantic_scholar_api_key=settings.semantic_scholar_api_key,
        contact_email=settings.contact_email,
    )


def build_insights_router(
    get_graph: Callable[..., ResearchGraph],
    settings: Settings,
    *,
    sources_factory: Callable[[Settings], Any] | None = None,
) -> APIRouter:
    """Build the opt-in router for project frontier and gap insights."""

    router = APIRouter()
    assert settings.data_dir is not None
    store = GapStore(settings.data_dir / "gaps")
    make_sources = sources_factory or _default_sources

    def graph_for(project_id: str) -> ResearchGraph:
        return _invoke_graph(get_graph, project_id)

    def detected_gaps(graph: ResearchGraph, project_id: str) -> list[GapHypothesis]:
        """Freshly detected gaps, before stored user fields are overlaid."""

        view = _project_graph(graph, project_id)
        return detect_gaps(view, analyze_project(graph, project_id))

    @router.get("/api/projects/{project_id}/frontier", response_model=FrontierResponse)
    def get_frontier(
        project_id: str, window_years: int = Query(2, ge=1, le=50)
    ) -> FrontierResponse:
        graph = graph_for(project_id)
        view = _project_graph(graph, project_id)
        analysis = analyze_project(graph, project_id)
        view = _enrich_work_data(view, graph, project_id)
        years = [
            node.get("data", {}).get("year") for node in view.nodes if node.get("kind") == "work"
        ]
        now_year = max((year for year in years if isinstance(year, int)), default=None)
        works = frontier_scores(view, analysis, window_years=window_years, now_year=now_year)
        concepts = frontier_concepts(view, window_years=window_years, now_year=now_year)
        return FrontierResponse(
            now_year=now_year,
            window_years=window_years,
            works=[FrontierWorkResponse(**asdict(work)) for work in works],
            concepts=[FrontierConceptResponse(**concept) for concept in concepts],
        )

    @router.get("/api/projects/{project_id}/gaps", response_model=list[GapHypothesis])
    def get_gaps(project_id: str, include_rejected: bool = False) -> list[GapHypothesis]:
        graph = graph_for(project_id)
        gaps = store.merge(project_id, detected_gaps(graph, project_id))
        return gaps if include_rejected else [gap for gap in gaps if gap.status != "rejected"]

    @router.patch("/api/projects/{project_id}/gaps/{gap_id}", response_model=GapHypothesis)
    def update_gap(project_id: str, gap_id: str, body: GapUpdateRequest) -> GapHypothesis:
        graph = graph_for(project_id)
        changes = body.model_dump(exclude_unset=True)
        if changes.get("status", "") is None:
            # An explicit null status means "leave unchanged", like an omitted one.
            changes.pop("status")
        updated = store.update(project_id, gap_id, detected_gaps(graph, project_id), **changes)
        if updated is None:
            raise HTTPException(status_code=404, detail="gap not found")
        return updated

    # A sync endpoint runs in FastAPI's threadpool, keeping the network search
    # off the event loop.
    @router.post("/api/projects/{project_id}/gaps/{gap_id}/verify", response_model=GapHypothesis)
    def verify_project_gap(project_id: str, gap_id: str) -> GapHypothesis:
        graph = graph_for(project_id)
        computed = detected_gaps(graph, project_id)
        # Stale gaps can be verified too, so look the gap up in the merged list.
        gap = next((item for item in store.merge(project_id, computed) if item.id == gap_id), None)
        if gap is None:
            raise HTTPException(status_code=404, detail="gap not found")
        works = graph.project_works(project_id)
        newest_year = max((work.year for work in works if work.year is not None), default=None)

        sources = make_sources(settings)
        try:
            verification = verify_gap(
                gap,
                sources,
                project_identifiers=_project_identifiers(works),
                newest_year=newest_year,
            )
        finally:
            close = getattr(sources, "close", None)
            if callable(close):
                with suppress(Exception):
                    close()

        updated = store.update(project_id, gap_id, computed, verification=verification)
        if updated is None:
            raise HTTPException(status_code=404, detail="gap not found")
        return updated

    return router


__all__ = [
    "FrontierResponse",
    "GapUpdateRequest",
    "build_insights_router",
]
