"""Load the committed golden mini-graph into a v1 research graph."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .graph.base import ResearchGraph
from .graph.models import Inclusion, Project, WorkNode

_PROJECT_NAME = "Golden mini-graph (demo)"
_PROJECT_DESCRIPTION = "The ~20 CS/AI papers used as the test fixture."


def _identifier(record: Mapping[str, Any], *names: str) -> str | None:
    identifiers = record.get("identifiers")
    if not isinstance(identifiers, Mapping):
        identifiers = {}
    for name in names:
        value = identifiers.get(name) or record.get(name)
        if value:
            return str(value)
    return None


def _work_type(record: Mapping[str, Any]) -> str | None:
    publication_types = [str(value).casefold() for value in (record.get("publication_types") or [])]
    if any("journal" in value for value in publication_types):
        return "journalArticle"
    if any("conference" in value for value in publication_types):
        return "conferencePaper"
    if str(record.get("source_tier") or "").casefold() == "preprint":
        return "preprint"
    return None


def _load_fixture(repo_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    golden_root = repo_root / "eval" / "golden"
    works_path = golden_root / "works.json"
    citations_path = golden_root / "citations.json"
    if not works_path.is_file():
        raise FileNotFoundError(f"golden works fixture not found at {works_path}")
    if not citations_path.is_file():
        raise FileNotFoundError(f"golden citations fixture not found at {citations_path}")

    works_data = json.loads(works_path.read_text(encoding="utf-8"))
    citations_data = json.loads(citations_path.read_text(encoding="utf-8"))
    if isinstance(works_data, Mapping) and isinstance(works_data.get("works"), Mapping):
        works_data = works_data["works"]
    if isinstance(citations_data, Mapping) and isinstance(citations_data.get("citations"), list):
        citations_data = citations_data["citations"]
    if not isinstance(works_data, Mapping) or not isinstance(citations_data, list):
        raise ValueError("golden works.json/citations.json have unexpected shapes")
    return dict(works_data), [dict(edge) for edge in citations_data]


def seed_demo_project(graph: ResearchGraph, repo_root: Path) -> Project | None:
    """Create and populate the demo project when the graph has no projects.

    The operation is idempotent at the project level: an existing project means the
    graph is considered already seeded and the function returns ``None``.  Works are
    upserted before the project is created, so a failure part-way (for example a
    uniqueness conflict with pre-existing nodes) leaves no half-seeded demo project and
    the next start retries.
    """

    if graph.list_projects():
        return None

    works_data, citations_data = _load_fixture(repo_root)
    work_ids: dict[str, str] = {}

    for slug in sorted(works_data):
        record = works_data[slug]
        if not isinstance(record, Mapping):
            continue
        year = record.get("year")
        if year is not None:
            year = int(year)
        work = WorkNode(
            title=str(record["title"]),
            year=year,
            abstract=record.get("abstract"),
            doi=_identifier(record, "doi", "DOI"),
            arxiv_id=_identifier(record, "arxiv", "arxiv_id", "arXiv", "arxivId"),
            openalex_id=_identifier(record, "openalex", "openalex_id", "openAlexId"),
            s2_id=_identifier(record, "s2", "s2_id", "corpusId", "paperId"),
            venue=record.get("venue"),
            work_type=_work_type(record),
            source_tier=record.get("source_tier"),
        )
        stored = graph.upsert_work(work)
        if stored.id is None:
            raise ValueError(f"graph did not assign an id to golden work {slug!r}")
        work_ids[slug] = stored.id

    project = graph.create_project(_PROJECT_NAME, description=_PROJECT_DESCRIPTION)
    for work_id in work_ids.values():
        graph.include_work(
            Inclusion(project_id=project.id, work_id=work_id, discovered_via="seed", depth=0)
        )

    for edge in citations_data:
        citing = work_ids.get(str(edge.get("citing")))
        cited = work_ids.get(str(edge.get("cited")))
        if citing is not None and cited is not None:
            graph.add_citation(citing, cited)

    return project
