"""Read-only graph tools used by the paper-grounded Ask agent."""

from __future__ import annotations

import inspect
import time
from collections import Counter
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from threading import Event
from typing import Any

from langchain_core.tools import BaseTool, tool

from ..analysis.service import analyze_project
from ..documents.models import Outline
from ..documents.outline import outline_to_text
from ..documents.store import DocumentStore
from ..graph.base import ResearchGraph

_OUTLINE_LINE_LIMIT = 60


def _field(value: object | None, name: str, default: Any = None) -> Any:
    """Read a field from a graph DTO or a plain mapping."""

    if value is None:
        return default
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _string(value: object | None, default: str = "") -> str:
    """Return a trimmed string for tolerant formatting of graph values."""

    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _year(value: object | None) -> str:
    """Format an optional publication year without inventing one."""

    return _string(value, "unknown")


def _short_text(value: object | None, limit: int = 1600) -> str:
    """Keep long abstracts from consuming the agent context."""

    text = " ".join(_string(value).split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _document_details(work: object, documents: DocumentStore) -> tuple[str | None, int | None]:
    """Return a virtual text path and page count for a locally stored work."""

    sha256 = _string(_field(work, "document_sha256"))
    if not sha256:
        return None, None
    try:
        record = documents.get(sha256)
    except (OSError, TypeError, ValueError):
        return None, None
    if record is None:
        return None, None

    pages = _field(record, "pages")
    stored_text_path = _field(record, "text_path")
    if stored_text_path is None:
        return None, pages
    try:
        if not Path(stored_text_path).is_file():
            return None, pages
    except (OSError, TypeError, ValueError):
        return None, pages

    # The virtual filesystem is rooted at ``documents.root``.  Keep the path
    # independent of the host filesystem so the model can pass it to read_file.
    text_path = f"/{sha256[:2]}/{sha256}/paper.txt"
    return text_path, pages


def _work_line(work: object, documents: DocumentStore) -> str:
    """Render one work in the compact format shared by all graph tools."""

    work_id = _string(_field(work, "id"), "unknown")
    title = _short_text(_field(work, "title"), 240) or "untitled"
    line = f"work_id={work_id} | year={_year(_field(work, 'year'))} | title={title}"
    text_path, _ = _document_details(work, documents)
    if text_path is not None:
        line += f" | text_path={text_path}"
    return line


def _summary_line(summary: object, documents: DocumentStore) -> str:
    """Render a WorkSummary while retaining its document pointer."""

    return _work_line(summary, documents)


def _project_works(graph: ResearchGraph, project_id: str) -> list[object]:
    """Load project works while tolerating a missing project in read tools."""

    try:
        return list(graph.project_works(project_id))
    except (KeyError, OSError, TypeError, ValueError):
        return []


def _find_project_work(graph: ResearchGraph, project_id: str, work_id: str) -> object | None:
    """Resolve a work only when it is included in the requested project."""

    requested = _string(work_id)
    if not requested:
        return None
    for work in _project_works(graph, project_id):
        if _string(_field(work, "id")) == requested:
            return work
    return None


def _graph_nodes(graph: ResearchGraph, project_id: str) -> list[object]:
    """Return project graph nodes as a list for DTO and mapping implementations."""

    try:
        view = graph.project_graph(project_id)
    except (KeyError, OSError, TypeError, ValueError):
        return []
    return list(_field(view, "nodes", []))


def _node_data(node: object) -> Mapping[str, Any]:
    """Return a graph node's nested data mapping."""

    data = _field(node, "data", {})
    return data if isinstance(data, Mapping) else {}


def _resolve_nodes(graph: ResearchGraph, project_id: str, query: str, kind: str) -> list[object]:
    """Resolve an entity id or case-insensitive label/name fragment."""

    wanted = _string(query).casefold()
    if not wanted:
        return []
    nodes = [node for node in _graph_nodes(graph, project_id) if _field(node, "kind") == kind]

    # An exact id is unambiguous and should win over a coincidental label match.
    exact = [node for node in nodes if _string(_field(node, "id")).casefold() == wanted]
    if exact:
        return exact

    matches: list[object] = []
    for node in nodes:
        candidates = [_string(_field(node, "label"))]
        if kind == "concept":
            candidates.extend(str(alias) for alias in _node_data(node).get("aliases", []) or [])
        if any(wanted in candidate.casefold() for candidate in candidates if candidate):
            matches.append(node)
    return matches


def _unique_summaries(summaries: list[object]) -> list[object]:
    """Deduplicate summaries from multiple matching author/concept nodes."""

    unique: dict[str, object] = {}
    for summary in summaries:
        work_id = _string(_field(summary, "id"))
        if work_id and work_id not in unique:
            unique[work_id] = summary
    return [unique[key] for key in sorted(unique)]


def _no_papers(message: str = "No papers found in this project.") -> str:
    """Use one consistent empty result that the model can act on."""

    return message


def build_graph_tools(
    graph: ResearchGraph,
    project_id: str,
    documents: DocumentStore,
) -> list[BaseTool]:
    """Build read-only LangChain tools scoped to one research project."""

    @tool
    def search_papers(query: str, limit: int = 10) -> str:
        """Search this project's paper titles and abstracts; return ids and text paths."""

        query = _string(query)
        if not query:
            return "Enter a search query."
        if limit <= 0:
            return _no_papers()
        try:
            results = graph.search_works(project_id, query, limit=limit)
        except (KeyError, OSError, TypeError, ValueError):
            results = []
        if not results:
            return _no_papers(f"No papers matched {query!r} in this project.")
        lines = [f"Papers matching {query!r}:"]
        lines.extend(f"- {_summary_line(summary, documents)}" for summary in results)
        return "\n".join(lines)

    @tool
    def paper_info(work_id: str) -> str:
        """Show metadata, authors, concepts, abstract, and local text path for one paper."""

        work = _find_project_work(graph, project_id, work_id)
        if work is None:
            return f"No paper with work_id={_string(work_id, 'unknown')} is in this project."

        try:
            neighborhood = graph.work_neighborhood(
                _string(_field(work, "id")), project_id=project_id
            )
        except (KeyError, OSError, TypeError, ValueError):
            neighborhood = None
        authors = []
        concepts = []
        if neighborhood is not None:
            authors = [
                _string(_field(author, "name"), "unknown")
                for author, _position in (_field(neighborhood, "authors", []) or [])
            ]
            concepts = [
                _string(_field(concept, "label"), "unknown")
                for concept, _score in (_field(neighborhood, "concepts", []) or [])
            ]

        text_path, pages = _document_details(work, documents)
        lines = [_work_line(work, documents)]
        lines.append(f"venue={_string(_field(work, 'venue'), 'unknown')}")
        lines.append(f"authors={', '.join(authors) if authors else 'unknown'}")
        lines.append(f"concepts={', '.join(concepts) if concepts else 'unknown'}")
        lines.append(f"abstract={_short_text(_field(work, 'abstract')) or 'none'}")
        lines.append(f"doi={_string(_field(work, 'doi'), 'none')}")
        lines.append(f"arxiv={_string(_field(work, 'arxiv_id'), 'none')}")
        if text_path is None:
            if _string(_field(work, "document_sha256")):
                lines.append("document=local PDF present but extracted text unavailable")
            else:
                lines.append("document=no local PDF")
        else:
            page_text = "unknown" if pages is None else str(pages)
            lines.append(f"document={text_path} | pages={page_text}")
        return "\n".join(lines)

    @tool
    def paper_outline(work_id: str) -> str:
        """Show one paper's section outline with page numbers, to navigate before reading."""

        work = _find_project_work(graph, project_id, work_id)
        if work is None:
            return f"No paper with work_id={_string(work_id, 'unknown')} is in this project."

        title = _short_text(_field(work, "title"), 240) or "untitled"
        sha256 = _string(_field(work, "document_sha256"))
        if not sha256:
            return f"{title}\nNo local PDF for this paper; use its abstract from paper_info."

        text_path, pages = _document_details(work, documents)
        page_text = "unknown" if pages is None else str(pages)
        lines = [title, f"document={text_path or 'text unavailable'} | pages={page_text}"]
        try:
            outline = documents.get_outline(sha256)
        except (OSError, TypeError, ValueError):
            outline = None
        sections = list(outline.sections) if outline is not None else []
        if not sections:
            lines.append("No outline; grep the text for section headings instead.")
            return "\n".join(lines)

        shown = Outline(sections=sections[:_OUTLINE_LINE_LIMIT], source=outline.source)
        lines.append(f"Outline (source={outline.source}):")
        lines.extend(outline_to_text(shown).splitlines())
        if len(sections) > _OUTLINE_LINE_LIMIT:
            lines.append(f"… {len(sections) - _OUTLINE_LINE_LIMIT} more sections not shown")
        return "\n".join(lines)

    @tool
    def citation_neighbors(work_id: str) -> str:
        """List papers this work cites and papers in this project that cite it."""

        work = _find_project_work(graph, project_id, work_id)
        if work is None:
            return f"No paper with work_id={_string(work_id, 'unknown')} is in this project."
        try:
            neighborhood = graph.work_neighborhood(
                _string(_field(work, "id")), project_id=project_id
            )
        except (KeyError, OSError, TypeError, ValueError):
            return f"Could not load citation neighbors for work_id={_string(work_id, 'unknown')}."

        cites = _field(neighborhood, "cites", []) or []
        cited_by = _field(neighborhood, "cited_by", []) or []
        lines = [f"Citation neighbors for work_id={_string(_field(work, 'id'))}:", "Cites:"]
        lines.extend(f"- {_summary_line(item, documents)}" for item in cites)
        if not cites:
            lines.append("- none")
        lines.append("Cited by:")
        lines.extend(f"- {_summary_line(item, documents)}" for item in cited_by)
        if not cited_by:
            lines.append("- none")
        return "\n".join(lines)

    @tool
    def papers_by_concept(concept: str) -> str:
        """Find project papers by a concept id, label, or alias fragment."""

        matches = _resolve_nodes(graph, project_id, concept, "concept")
        if not matches:
            return f"No concept matching {_string(concept, 'unknown')!r} is in this project."
        summaries: list[object] = []
        labels: list[str] = []
        for node in matches:
            concept_id = _string(_field(node, "id"))
            labels.append(f"{_string(_field(node, 'label'), concept_id)} ({concept_id})")
            try:
                summaries.extend(graph.works_by_concept(concept_id, project_id))
            except (KeyError, OSError, TypeError, ValueError):
                continue
        unique = _unique_summaries(summaries)
        if not unique:
            return f"No papers use concept {', '.join(labels)} in this project."
        lines = [f"Papers by concept: {', '.join(labels)}"]
        lines.extend(f"- {_summary_line(summary, documents)}" for summary in unique)
        return "\n".join(lines)

    @tool
    def papers_by_author(author: str) -> str:
        """Find project papers by an author id or case-insensitive name fragment."""

        matches = _resolve_nodes(graph, project_id, author, "author")
        if not matches:
            return f"No author matching {_string(author, 'unknown')!r} is in this project."
        summaries: list[object] = []
        labels: list[str] = []
        for node in matches:
            author_id = _string(_field(node, "id"))
            labels.append(f"{_string(_field(node, 'label'), author_id)} ({author_id})")
            try:
                summaries.extend(graph.works_by_author(author_id, project_id))
            except (KeyError, OSError, TypeError, ValueError):
                continue
        unique = _unique_summaries(summaries)
        if not unique:
            return f"No papers by {', '.join(labels)} are in this project."
        lines = [f"Papers by author: {', '.join(labels)}"]
        lines.extend(f"- {_summary_line(summary, documents)}" for summary in unique)
        return "\n".join(lines)

    @tool
    def project_overview() -> str:
        """Summarize project stats, frequent concepts, and the ten most-cited works."""

        try:
            stats = graph.project_stats(project_id)
        except (KeyError, OSError, TypeError, ValueError):
            stats = None
        stats_values = {
            name: _field(stats, name, 0)
            for name in ("works", "citations", "authors", "concepts", "documents")
        }
        lines = [
            f"Project overview for project_id={project_id}:",
            "stats: " + ", ".join(f"{name}={stats_values[name]}" for name in stats_values),
            "Top concepts by number of works:",
        ]

        nodes = _graph_nodes(graph, project_id)
        concept_nodes = {
            _string(_field(node, "id")): node
            for node in nodes
            if _field(node, "kind") == "concept" and _string(_field(node, "id"))
        }
        counts: Counter[str] = Counter()
        try:
            view = graph.project_graph(project_id)
            for edge in _field(view, "edges", []) or []:
                if _field(edge, "kind") == "has_concept":
                    target = _string(_field(edge, "target"))
                    if target in concept_nodes:
                        counts[target] += 1
        except (KeyError, OSError, TypeError, ValueError):
            pass
        if not counts and concept_nodes:
            for concept_id in concept_nodes:
                try:
                    counts[concept_id] = len(graph.works_by_concept(concept_id, project_id))
                except (KeyError, OSError, TypeError, ValueError):
                    counts[concept_id] = 0
        top_concepts = sorted(
            counts.items(),
            key=lambda item: (-item[1], _string(_field(concept_nodes[item[0]], "label")), item[0]),
        )[:10]
        if top_concepts:
            lines.extend(
                f"- concept_id={concept_id} | "
                f"label={_string(_field(concept_nodes[concept_id], 'label'), concept_id)} | "
                f"works={count}"
                for concept_id, count in top_concepts
            )
        else:
            lines.append("- none")

        lines.append("Most-cited works:")
        works = _project_works(graph, project_id)
        works.sort(
            key=lambda work: (
                -int(_field(work, "cited_by_count") or 0),
                _string(_field(work, "id")),
            )
        )
        if works:
            lines.extend(f"- {_work_line(work, documents)}" for work in works[:10])
        else:
            lines.append("- none")
        return "\n".join(lines)

    return [
        search_papers,
        paper_info,
        paper_outline,
        citation_neighbors,
        papers_by_concept,
        papers_by_author,
        project_overview,
    ]


def _record_identifier(record: object, name: str) -> str | None:
    """Return one identifier from either a source record or its ``identifiers`` map."""

    value = _field(record, name)
    if value is None:
        value = _field(record, f"{name}_id")
    identifiers = _field(record, "identifiers", {})
    if value is None and isinstance(identifiers, Mapping):
        value = identifiers.get(name)
    text = _string(value)
    return text or None


def _record_identifiers(record: object) -> list[tuple[str, str]]:
    """Return the useful source identifiers in stable order."""

    values: list[tuple[str, str]] = []
    for name in ("openalex", "doi", "arxiv", "s2", "pmid", "pmcid"):
        value = _record_identifier(record, name)
        if value is not None:
            values.append((name, value))
    return values


def _source_adapter(sources: object) -> object:
    """Use the OpenAlex adapter exposed by a source facade or a test fake."""

    if isinstance(sources, Mapping):
        return sources.get("openalex", sources)
    adapter = getattr(sources, "openalex", None)
    return sources if adapter is None else adapter


def _invoke_factory(
    factory: Callable[..., Any],
    *,
    graph: ResearchGraph,
    project_id: str,
    settings: Any,
    sources: Any,
    documents: DocumentStore,
    request: Any = None,
) -> Any:
    """Call injected factories using parameter names while supporting small fakes.

    The API runner factory uses ``(graph, settings, request)`` while tests and
    command-line callers commonly use ``(graph, sources)`` or no arguments.  A
    name-aware call keeps all of those forms deterministic without catching a
    ``TypeError`` raised from inside the factory itself.
    """

    try:
        parameters = list(inspect.signature(factory).parameters.values())
    except (TypeError, ValueError):
        return factory(graph, settings, request)

    positional = [
        parameter
        for parameter in parameters
        if parameter.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    if any(parameter.kind == inspect.Parameter.VAR_POSITIONAL for parameter in parameters):
        return factory(graph, settings, request)

    context = {
        "graph": graph,
        "project_id": project_id,
        "settings": settings,
        "sources": sources,
        "documents": documents,
        "request": request,
    }
    aliases = {
        "g": "graph",
        "req": "request",
        "r": "request",
        "config": "settings",
        "store": "documents",
        "docs": "documents",
    }
    fallback = [graph, settings, request]
    args: list[Any] = []
    for index, parameter in enumerate(positional):
        name = parameter.name.casefold()
        key = aliases.get(name)
        if key is None:
            if "project" in name:
                key = "project_id"
            elif "source" in name:
                key = "sources"
            elif "document" in name or name in {"doc", "docs", "store"}:
                key = "documents"
            elif "setting" in name or name in {"config", "configuration"}:
                key = "settings"
            elif "request" in name or name in {"req", "r"}:
                key = "request"
            elif name in {"graph", "g"}:
                key = "graph"
        if key is None:
            value = fallback[index] if index < len(fallback) else None
        else:
            value = context[key]
        if value is None and parameter.default is not inspect.Parameter.empty:
            break
        args.append(value)
    return factory(*args)


def _source_factory_default(settings: Any) -> Any:
    """Build the same source facade and cache location used by the runner."""

    from ..research.sources import ResearchSources

    if settings is None:
        from ..settings import Settings

        settings = Settings.from_env()
    return ResearchSources.default(
        settings.http_cache_dir,
        openalex_api_key=getattr(settings, "openalex_api_key", None),
        semantic_scholar_api_key=getattr(settings, "semantic_scholar_api_key", None),
        contact_email=getattr(settings, "contact_email", None),
    )


def _as_text(value: object | None) -> str:
    """Render source values without leaking Python ``None`` into tool output."""

    if value is None:
        return "unknown"
    text = str(value).strip()
    return text or "unknown"


def _format_source_record(record: object, *, include_citations: bool = True) -> str:
    """Render the bounded metadata shown while planning a research harvest."""

    title = _short_text(_field(record, "title"), 240) or _short_text(
        _field(record, "display_name"), 240
    )
    parts = [f"title={title or 'untitled'}", f"year={_as_text(_field(record, 'year'))}"]
    if include_citations:
        parts.append(f"cited_by_count={_as_text(_field(record, 'cited_by_count'))}")
    identifiers = _record_identifiers(record)
    if identifiers:
        parts.append("ids=" + ", ".join(f"{name}:{value}" for name, value in identifiers))
    for name, value in identifiers:
        parts.append(f"{name}_id={value}" if name not in {"doi", "arxiv"} else f"{name}={value}")
    return " | ".join(parts)


def _report_value(report: object, name: str, default: Any = None) -> Any:
    """Read report fields from Pydantic, dataclass, or plain fake reports."""

    return _field(report, name, default)


def _compact_report(report: object, *, run_id: str | None = None) -> str:
    """Return the small result consumed by the research agent after a harvest."""

    prefix = "Research run"
    if run_id:
        prefix += f" {run_id}"
    prefix += " complete."
    fields = (
        ("candidates", "candidates_found"),
        ("excluded", "excluded"),
        ("screened_out", "screened_out"),
        ("included", "included"),
        ("citations", "citations"),
        ("authors", "authors"),
        ("concepts", "concepts"),
        ("pdfs_acquired", "pdfs_acquired"),
        ("pdfs_failed", "pdfs_failed"),
        ("pdfs_skipped", "pdfs_skipped"),
    )
    values = [f"{label}={_report_value(report, name, 0)}" for label, name in fields]
    warnings = _report_value(report, "warnings", []) or []
    values.append(f"warnings={len(warnings) if isinstance(warnings, (list, tuple, set)) else 0}")
    return prefix + " " + ", ".join(values)


def _event_value(event: object, name: str, default: Any = None) -> Any:
    return _field(event, name, default)


def _cancel_is_set(cancel: Event | Callable[[], Event | None] | None) -> bool:
    event = cancel() if callable(cancel) and not hasattr(cancel, "is_set") else cancel
    return bool(event is not None and event.is_set())


def _cancel_run(cancel: Event | Callable[[], Event | None] | None) -> Event | None:
    event = cancel() if callable(cancel) and not hasattr(cancel, "is_set") else cancel
    return event


def _run_request(
    *,
    query: str | None,
    seeds: list[str],
    from_year: int | None,
    to_year: int | None,
    max_works: int,
    snowball_depth: int,
    acquire_pdfs: bool,
    registry: bool,
) -> Any:
    """Construct the API or pipeline request without importing research eagerly."""

    if registry:
        from ..api.models import ResearchRunRequest

        return ResearchRunRequest(
            query=query,
            seeds=seeds,
            from_year=from_year,
            to_year=to_year,
            max_works=max_works,
            snowball_depth=snowball_depth,
            acquire_pdfs=acquire_pdfs,
        )
    from ..research.models import ResearchRequest

    return ResearchRequest(
        query=query,
        seeds=seeds,
        from_year=from_year,
        to_year=to_year,
        max_works=max_works,
        snowball_depth=snowball_depth,
        acquire_pdfs=acquire_pdfs,
    )


def build_research_tools(
    graph: ResearchGraph,
    project_id: str,
    sources_factory: Callable[..., Any] | Any | None = None,
    runs: Any | None = None,
    documents: DocumentStore | None = None,
    *,
    settings: Any | None = None,
    runner_factory: Callable[..., Any] | Any | None = None,
    on_event: Callable[[str], None] | None = None,
    cancel: Event | Callable[[], Event | None] | None = None,
    on_run_submitted: Callable[[str], None] | None = None,
) -> list[BaseTool]:
    """Build planning, harvest, map, and read-only project tools.

    ``sources_factory`` is called once and its facade is reused for all planning
    calls in one agent.  ``cancel`` may be a callback so an agent can replace its
    per-request event while retaining the same interruptible tool set.
    """

    if documents is None:
        if settings is not None and getattr(settings, "documents_dir", None) is not None:
            documents = DocumentStore(settings.documents_dir)
        else:
            documents = DocumentStore(Path("."))

    source_holder: list[Any] = []

    def get_sources() -> Any:
        if not source_holder:
            if sources_factory is None:
                source_holder.append(_source_factory_default(settings))
            elif hasattr(sources_factory, "openalex"):
                source_holder.append(sources_factory)
            else:
                source_holder.append(
                    _invoke_factory(
                        sources_factory,
                        graph=graph,
                        project_id=project_id,
                        settings=settings,
                        sources=None,
                        documents=documents,
                    )
                )
        return source_holder[0]

    @tool
    def preview_search(
        query: str,
        from_year: int | None = None,
        to_year: int | None = None,
        limit: int = 10,
    ) -> str:
        """Preview OpenAlex results without changing the project graph."""

        query = _string(query)
        if not query:
            return "Enter a search query."
        if limit <= 0:
            return "No preview results requested."
        try:
            adapter = _source_adapter(get_sources())
            search = getattr(adapter, "search", None)
            if not callable(search):
                return "OpenAlex search is unavailable."
            records = search(
                query,
                limit=max(0, min(int(limit), 100)),
                from_year=from_year,
                to_year=to_year,
            )
        except Exception as error:
            return f"OpenAlex preview failed: {error}"
        records = [record for record in records or [] if record is not None]
        if not records:
            return f"No OpenAlex papers matched {query!r}."
        lines = [f"OpenAlex preview for {query!r}:"]
        lines.extend(f"- {_format_source_record(record)}" for record in records)
        return "\n".join(lines)

    @tool
    def lookup_paper(identifier: str) -> str:
        """Resolve a DOI, arXiv id, or OpenAlex id to seed metadata."""

        identifier = _string(identifier)
        if not identifier:
            return "Enter a DOI, arXiv id, or OpenAlex id."
        try:
            adapter = _source_adapter(get_sources())
            record = None
            lookup = getattr(adapter, "lookup", None)
            if callable(lookup):
                record = lookup(identifier)
            if record is None:
                lookup_many = getattr(adapter, "lookup_many", None)
                if callable(lookup_many):
                    records = lookup_many([identifier])
                    record = records[0] if records else None
        except Exception as error:
            return f"Paper lookup failed for {identifier!r}: {error}"
        if record is None:
            return f"No OpenAlex paper found for {identifier!r}."
        return f"OpenAlex paper for {identifier!r}: {_format_source_record(record)}"

    def emit_progress(event: object, state: dict[str, Any]) -> None:
        if on_event is None:
            return
        stage = _as_text(_event_value(event, "stage", "research"))
        message = _as_text(_event_value(event, "message", "working"))
        now = time.monotonic()
        previous_stage = state.get("stage")
        previous_at = float(state.get("at", 0.0))
        if stage == previous_stage and now - previous_at < 5.0:
            return
        state["stage"] = stage
        state["at"] = now
        on_event(f"Harvest: {stage} — {message}")

    def wait_for_registry_run(run_id: str, state: dict[str, Any]) -> str:
        progress_index = 0
        while True:
            if _cancel_is_set(cancel):
                with suppress(OSError, RuntimeError, TypeError, ValueError):
                    runs.cancel(run_id)
                return f"Research run {run_id} cancelled."
            current = runs.get(run_id)
            if current is None:
                return f"Research run {run_id} disappeared before completion."
            progress = _field(current, "progress", []) or []
            for event in progress[progress_index:]:
                emit_progress(event, state)
            progress_index = len(progress)
            status = _string(_field(current, "status"), "unknown")
            if status == "succeeded":
                return _compact_report(_field(current, "report", {}) or {}, run_id=run_id)
            if status == "failed":
                return f"Research run {run_id} failed: {_as_text(_field(current, 'error'))}"
            if status == "cancelled":
                return f"Research run {run_id} cancelled."
            time.sleep(1.0)

    @tool
    def run_research(
        query: str | None,
        seeds: list[str],
        from_year: int | None = None,
        to_year: int | None = None,
        max_works: int = 100,
        snowball_depth: int = 2,
        acquire_pdfs: bool = True,
        rationale: str = "",
    ) -> str:
        """Run the approved literature harvest and wait for its compact report."""

        del rationale  # The rationale is for the approval UI, not the pipeline request.
        clean_query = _string(query) or None
        clean_seeds = []
        for seed in seeds or []:
            value = _string(seed)
            if value and value not in clean_seeds:
                clean_seeds.append(value)
        if not clean_query and not clean_seeds:
            return "Provide a query or at least one seed paper."
        if _cancel_is_set(cancel):
            return "Research run cancelled before submission."

        state: dict[str, Any] = {}
        try:
            if runs is not None:
                request = _run_request(
                    query=clean_query,
                    seeds=clean_seeds,
                    from_year=from_year,
                    to_year=to_year,
                    max_works=max_works,
                    snowball_depth=snowball_depth,
                    acquire_pdfs=acquire_pdfs,
                    registry=True,
                )
                has_active = getattr(runs, "has_active", None)
                if callable(has_active) and has_active(project_id):
                    return "A research run is already active for this project."
                try:
                    submitted = runs.submit(project_id, request)
                except Exception as error:
                    if (
                        "active" in str(error).casefold()
                        or type(error).__name__ == "ActiveRunError"
                    ):
                        return "A research run is already active for this project."
                    raise
                run_id = _string(_field(submitted, "id"))
                if not run_id:
                    return "Research run submission did not return a run id."
                if on_run_submitted is not None:
                    on_run_submitted(run_id)
                return wait_for_registry_run(run_id, state)

            from ..research.runner import ResearchRunner

            sources = get_sources()
            request = _run_request(
                query=clean_query,
                seeds=clean_seeds,
                from_year=from_year,
                to_year=to_year,
                max_works=max_works,
                snowball_depth=snowball_depth,
                acquire_pdfs=acquire_pdfs,
                registry=False,
            )
            if runner_factory is None:
                runner = ResearchRunner(graph, sources, documents=documents)
            elif hasattr(runner_factory, "run"):
                runner = runner_factory
            else:
                runner = _invoke_factory(
                    runner_factory,
                    graph=graph,
                    project_id=project_id,
                    settings=settings,
                    sources=sources,
                    documents=documents,
                    request=request,
                )
            report = runner.run(
                project_id,
                request,
                progress=lambda event: emit_progress(event, state),
                cancel=_cancel_run(cancel),
            )
            return _compact_report(report)
        except Exception as error:
            if _cancel_is_set(cancel) or type(error).__name__ == "RunCancelled":
                return "Research run cancelled."
            if type(error).__name__ in {"ValidationError", "ValueError"}:
                return f"Research run request is invalid: {error}"
            return f"Research run failed: {type(error).__name__}: {error}"

    @tool
    def map_summary() -> str:
        """Summarize clusters, structural roles, and the project's main path."""

        try:
            analysis = analyze_project(graph, project_id)
            works = {
                _string(_field(work, "id")): work
                for work in _project_works(graph, project_id)
                if _string(_field(work, "id"))
            }
            lines = [f"Research map for project_id={project_id}:", "Clusters:"]
            clusters = _field(analysis, "clusters", []) or []
            for cluster in clusters:
                work_ids = [str(work_id) for work_id in (_field(cluster, "work_ids", []) or [])]
                ranked_ids = sorted(
                    work_ids,
                    key=lambda work_id: (
                        -float(
                            _field(
                                _field(analysis, "works", {}).get(work_id),
                                "pagerank",
                                0.0,
                            )
                            or 0.0
                        ),
                        work_id,
                    ),
                )
                top_titles = [
                    _short_text(_field(works[work_id], "title"), 120) or work_id
                    for work_id in ranked_ids[:3]
                    if work_id in works
                ]
                label = _string(_field(cluster, "label"), "unlabelled")
                top = ", ".join(top_titles) if top_titles else "none"
                lines.append(
                    f"- {label} | size={_field(cluster, 'size', len(work_ids))} | top works={top}"
                )
            if not clusters:
                lines.append("- none")

            roles = _field(analysis, "works", {}) or {}
            for role in ("foundational", "bridge", "emerging"):
                selected = []
                for work_id, details in roles.items():
                    if role in (_field(details, "roles", []) or []):
                        title = _short_text(_field(works.get(work_id), "title"), 140) or str(
                            work_id
                        )
                        selected.append((title.casefold(), title))
                selected.sort()
                lines.append(
                    f"{role.capitalize()} works: "
                    + (", ".join(title for _, title in selected) or "none")
                )

            path = _field(_field(analysis, "main_path"), "work_ids", []) or []
            path_titles = [
                _short_text(_field(works.get(work_id), "title"), 140) or str(work_id)
                for work_id in path
            ]
            lines.append("Main path: " + (" → ".join(path_titles) if path_titles else "none"))
            return "\n".join(lines)
        except (KeyError, OSError, TypeError, ValueError, RuntimeError) as error:
            return f"Could not analyze the project map: {error}"

    graph_tools = build_graph_tools(graph, project_id, documents)
    return [*graph_tools, preview_search, lookup_paper, run_research, map_summary]


__all__ = ["build_graph_tools", "build_research_tools"]
