"""Read-only graph tools used by the paper-grounded Ask agent."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool

from ..documents.store import DocumentStore
from ..graph.base import ResearchGraph


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
        citation_neighbors,
        papers_by_concept,
        papers_by_author,
        project_overview,
    ]


__all__ = ["build_graph_tools"]
