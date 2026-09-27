"""Pure Python implementation of the v1 research graph contract.

The in-memory graph mirrors the Neo4j binding closely enough for callers to use it
without a database.  Works, authors and concepts live in global dictionaries;
project membership is represented only by ``_inclusions``.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from .base import mint_author_id, mint_project_id, mint_work_id
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

_WORK_IDENTIFIERS = ("doi", "arxiv_id", "openalex_id", "s2_id")
_AUTHOR_IDENTIFIERS = ("orcid", "openalex_id", "s2_id")
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _model_data(value: Any) -> dict[str, Any]:
    """Return a model's ordinary Python fields without relying on private state."""

    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return dict(dump(mode="python"))
        except TypeError:  # pragma: no cover - compatibility with older Pydantic shims
            return dict(dump())
    return dict(vars(value))


def _model_copy(value: Any) -> Any:
    copier = getattr(value, "model_copy", None)
    if callable(copier):
        return copier(deep=True)
    return type(value)(**_model_data(value))


def _value(value: Any) -> Any:
    """Unwrap enum-like values before putting them in plain in-memory records."""

    return getattr(value, "value", value)


def _text(value: Any) -> str:
    return str(_value(value)).strip()


def _identifier_key(field: str, value: Any) -> str | None:
    if value is None:
        return None
    text = _text(value)
    if not text:
        return None
    if field == "doi":
        return text.casefold()
    if field == "arxiv_id":
        # arXiv versions identify revisions of one work.  The graph identity is
        # intentionally revision-independent, just like mint_work_id.
        text = re.sub(r"v\d+$", "", text, flags=re.IGNORECASE)
    return text.casefold()


def _tokens(value: str | None) -> list[str]:
    return _TOKEN_RE.findall((value or "").casefold())


class InMemoryResearchGraph:
    """Dictionary-backed implementation of :class:`ResearchGraph`."""

    def __init__(self) -> None:
        self._projects: dict[str, Project] = {}
        self._works: dict[str, WorkNode] = {}
        self._authors: dict[str, AuthorNode] = {}
        self._concepts: dict[str, ConceptNode] = {}
        self._inclusions: dict[tuple[str, str], Inclusion] = {}
        self._citations: set[tuple[str, str]] = set()
        self._authors_by_work: dict[str, dict[str, int]] = defaultdict(dict)
        self._concepts_by_work: dict[str, dict[str, float]] = defaultdict(dict)

    def ensure_schema(self) -> None:
        """Keep API parity with Neo4j; dictionaries need no schema setup."""

    def close(self) -> None:
        """Keep API parity with Neo4j; there are no external resources to close."""

    def create_project(self, name: str, description: str | None = None) -> Project:
        project = Project(
            id=mint_project_id(name),
            name=name,
            description=description,
            created_at=datetime.now(UTC),
        )
        # A random id collision is extraordinarily unlikely, but a retry keeps
        # the in-memory implementation's behaviour well-defined under a patched
        # token generator in tests.
        while project.id in self._projects:
            project = Project(
                id=mint_project_id(name),
                name=name,
                description=description,
                created_at=datetime.now(UTC),
            )
        self._projects[project.id] = project
        return _model_copy(project)

    def get_project(self, id: str) -> Project | None:
        project = self._projects.get(id)
        return None if project is None else _model_copy(project)

    def list_projects(self) -> list[Project]:
        projects = sorted(
            self._projects.values(),
            key=lambda project: (project.created_at, project.id),
            reverse=True,
        )
        return [_model_copy(project) for project in projects]

    def delete_project(self, id: str) -> bool:
        if id not in self._projects:
            return False
        del self._projects[id]
        for key in [key for key in self._inclusions if key[0] == id]:
            del self._inclusions[key]
        return True

    def _find_work(self, work: WorkNode) -> str | None:
        requested_id = getattr(work, "id", None)
        if requested_id is not None and _text(requested_id) in self._works:
            return _text(requested_id)

        candidates: set[str] = set()
        for field in _WORK_IDENTIFIERS:
            incoming = _identifier_key(field, getattr(work, field, None))
            if incoming is None:
                continue
            for work_id, stored in self._works.items():
                if _identifier_key(field, getattr(stored, field, None)) == incoming:
                    candidates.add(work_id)
        return min(candidates) if candidates else None

    def upsert_work(self, work: WorkNode) -> WorkNode:
        work_id = self._find_work(work)
        if work_id is None:
            # mint_work_id is also the deterministic fallback identity for a
            # title/year-only work.  Recheck the result so that repeated fallback
            # inputs merge instead of replacing an existing dictionary entry.
            work_id = mint_work_id(work)
            existing = self._works.get(work_id)
        else:
            existing = self._works[work_id]

        if existing is None:
            data = _model_data(work)
            data["id"] = work_id
            stored = WorkNode.model_validate(data)
        else:
            data = _model_data(existing)
            for field, value in _model_data(work).items():
                if (
                    field != "id"
                    and value is not None
                    and (field not in {"keywords", "keyword_scores"} or value)
                ):
                    data[field] = value
            data["id"] = work_id
            stored = WorkNode.model_validate(data)
        self._works[work_id] = stored
        return _model_copy(stored)

    def project_works(self, project_id: str) -> list[WorkNode]:
        work_ids = sorted(self._included_work_ids(project_id))
        return [_model_copy(self._works[work_id]) for work_id in work_ids]

    def get_work(self, id: str) -> WorkNode | None:
        work = self._works.get(id)
        return None if work is None else _model_copy(work)

    def include_work(self, inclusion: Inclusion) -> None:
        project_id = _text(inclusion.project_id)
        work_id = _text(inclusion.work_id)
        if project_id not in self._projects or work_id not in self._works:
            return
        key = (project_id, work_id)
        current = self._inclusions.get(key)
        if current is None:
            self._inclusions[key] = _model_copy(inclusion)
            return

        # Repeated discovery keeps the first route and the smallest depth.  A
        # later score fills an absent score, while an existing score remains the
        # first observed score just as discovered_via does.
        score = current.score if current.score is not None else inclusion.score
        self._inclusions[key] = current.model_copy(
            update={
                "depth": min(current.depth, inclusion.depth),
                "score": score,
            }
        )

    def add_citation(self, citing_id: str, cited_id: str) -> None:
        if citing_id == cited_id:
            return
        if citing_id not in self._works or cited_id not in self._works:
            return
        self._citations.add((citing_id, cited_id))

    def _find_author(self, author: AuthorNode) -> str | None:
        requested_id = getattr(author, "id", None)
        if requested_id is not None and _text(requested_id) in self._authors:
            return _text(requested_id)
        candidates: set[str] = set()
        for field in _AUTHOR_IDENTIFIERS:
            incoming = _identifier_key(field, getattr(author, field, None))
            if incoming is None:
                continue
            for author_id, stored in self._authors.items():
                if _identifier_key(field, getattr(stored, field, None)) == incoming:
                    candidates.add(author_id)
        return min(candidates) if candidates else None

    def upsert_author(self, author: AuthorNode) -> AuthorNode:
        author_id = self._find_author(author)
        if author_id is None:
            author_id = mint_author_id(author)
            existing = self._authors.get(author_id)
        else:
            existing = self._authors[author_id]

        if existing is None:
            data = _model_data(author)
            data["id"] = author_id
            stored = AuthorNode.model_validate(data)
        else:
            data = _model_data(existing)
            for field, value in _model_data(author).items():
                if field != "id" and value is not None:
                    data[field] = value
            data["id"] = author_id
            stored = AuthorNode.model_validate(data)
        self._authors[author_id] = stored
        return _model_copy(stored)

    def set_authors(self, work_id: str, authors: Sequence[tuple[str, int]]) -> None:
        if work_id not in self._works:
            return
        self._authors_by_work[work_id] = {
            author_id: position for author_id, position in authors if author_id in self._authors
        }

    def upsert_concept(self, concept: ConceptNode) -> ConceptNode:
        concept_id = _text(concept.id)
        existing = self._concepts.get(concept_id)
        if existing is None:
            data = _model_data(concept)
            data["id"] = concept_id
            data["aliases"] = sorted({str(alias) for alias in (data.get("aliases") or [])})
            stored = ConceptNode.model_validate(data)
        else:
            data = _model_data(existing)
            incoming = _model_data(concept)
            if incoming.get("label") is not None:
                data["label"] = incoming["label"]
            aliases = set(data.get("aliases") or []) | set(incoming.get("aliases") or [])
            data["aliases"] = sorted(str(alias) for alias in aliases)
            data["id"] = concept_id
            stored = ConceptNode.model_validate(data)
        self._concepts[concept_id] = stored
        return _model_copy(stored)

    def set_concepts(self, work_id: str, concepts: Sequence[tuple[str, float]]) -> None:
        if work_id not in self._works:
            return
        self._concepts_by_work[work_id] = {
            concept_id: float(score)
            for concept_id, score in concepts
            if concept_id in self._concepts
        }

    def set_document(self, work_id: str, sha256: str, source_url: str | None) -> None:
        work = self._works.get(work_id)
        if work is None:
            return
        data = _model_data(work)
        data["document_sha256"] = sha256
        data["document_source_url"] = source_url
        self._works[work_id] = WorkNode.model_validate(data)

    def _included_work_ids(self, project_id: str) -> set[str]:
        return {
            work_id
            for (included_project, work_id) in self._inclusions
            if included_project == project_id and work_id in self._works
        }

    def _summary(self, work_id: str) -> WorkSummary:
        work = self._works[work_id]
        return WorkSummary(
            id=work.id,
            title=work.title,
            year=work.year,
            cited_by_count=work.cited_by_count,
            document_sha256=work.document_sha256,
        )

    def search_works(self, project_id: str, query: str, *, limit: int = 20) -> list[WorkSummary]:
        if limit <= 0:
            return []
        query_tokens = set(_tokens(query))
        if not query_tokens:
            return []
        ranked: list[tuple[int, str, WorkSummary]] = []
        for work_id in self._included_work_ids(project_id):
            work = self._works[work_id]
            document_tokens = _tokens(work.title) + _tokens(work.abstract)
            score = sum(document_tokens.count(token) for token in query_tokens)
            if score:
                ranked.append((score, work_id, self._summary(work_id)))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [summary for _, _, summary in ranked[:limit]]

    def work_neighborhood(self, work_id: str, project_id: str | None = None) -> WorkNeighborhood:
        work = self._works.get(work_id)
        if work is None:
            raise KeyError(work_id)
        allowed = None if project_id is None else self._included_work_ids(project_id)
        if allowed is not None and work_id not in allowed:
            return WorkNeighborhood(
                work=_model_copy(work), cites=[], cited_by=[], authors=[], concepts=[]
            )

        cites = sorted(
            (
                target
                for source, target in self._citations
                if source == work_id and (allowed is None or target in allowed)
            ),
            key=str,
        )
        cited_by = sorted(
            (
                source
                for source, target in self._citations
                if target == work_id and (allowed is None or source in allowed)
            ),
            key=str,
        )
        author_items = [
            (_model_copy(self._authors[author_id]), position)
            for author_id, position in self._authors_by_work.get(work_id, {}).items()
            if author_id in self._authors
        ]
        author_items.sort(key=lambda item: (item[1], item[0].id))
        concept_items = [
            (_model_copy(self._concepts[concept_id]), score)
            for concept_id, score in self._concepts_by_work.get(work_id, {}).items()
            if concept_id in self._concepts
        ]
        concept_items.sort(key=lambda item: (-item[1], item[0].id))
        return WorkNeighborhood(
            work=_model_copy(work),
            cites=[self._summary(item) for item in cites],
            cited_by=[self._summary(item) for item in cited_by],
            authors=author_items,
            concepts=concept_items,
        )

    def works_by_author(self, author_id: str, project_id: str) -> list[WorkSummary]:
        included = self._included_work_ids(project_id)
        work_ids = sorted(
            work_id for work_id in included if author_id in self._authors_by_work.get(work_id, {})
        )
        return [self._summary(work_id) for work_id in work_ids]

    def works_by_concept(self, concept_id: str, project_id: str) -> list[WorkSummary]:
        included = self._included_work_ids(project_id)
        work_ids = sorted(
            work_id for work_id in included if concept_id in self._concepts_by_work.get(work_id, {})
        )
        return [self._summary(work_id) for work_id in work_ids]

    def project_graph(
        self,
        project_id: str,
        *,
        include_authors: bool = True,
        include_concepts: bool = True,
    ) -> GraphView:
        included = self._included_work_ids(project_id)
        work_ids = sorted(included)
        author_ids = sorted(
            {
                author_id
                for work_id in work_ids
                for author_id in self._authors_by_work.get(work_id, {})
                if author_id in self._authors
            }
        )
        concept_ids = sorted(
            {
                concept_id
                for work_id in work_ids
                for concept_id in self._concepts_by_work.get(work_id, {})
                if concept_id in self._concepts
            }
        )

        nodes: list[dict[str, Any]] = []
        for work_id in work_ids:
            work = self._works[work_id]
            outgoing = sum(
                source == work_id and target in included for source, target in self._citations
            )
            incoming = sum(
                target == work_id and source in included for source, target in self._citations
            )
            nodes.append(
                {
                    "id": work.id,
                    "kind": "work",
                    "label": work.title,
                    "data": {
                        "year": work.year,
                        "cited_by_count": work.cited_by_count,
                        "has_document": work.document_sha256 is not None,
                        "in_degree": incoming,
                        "out_degree": outgoing,
                    },
                }
            )
        if include_authors:
            for author_id in author_ids:
                author = self._authors[author_id]
                nodes.append(
                    {
                        "id": author.id,
                        "kind": "author",
                        "label": author.name,
                        "data": {
                            "orcid": author.orcid,
                            "openalex_id": author.openalex_id,
                            "s2_id": author.s2_id,
                        },
                    }
                )
        if include_concepts:
            for concept_id in concept_ids:
                concept = self._concepts[concept_id]
                nodes.append(
                    {
                        "id": concept.id,
                        "kind": "concept",
                        "label": concept.label,
                        "data": {"aliases": list(concept.aliases)},
                    }
                )

        edges: list[dict[str, str]] = []
        for source, target in sorted(self._citations):
            if source in included and target in included:
                edges.append({"source": source, "target": target, "kind": "cites"})
        if include_authors:
            for work_id in work_ids:
                for author_id in sorted(self._authors_by_work.get(work_id, {})):
                    if author_id in author_ids:
                        edges.append(
                            {"source": work_id, "target": author_id, "kind": "authored_by"}
                        )
        if include_concepts:
            for work_id in work_ids:
                for concept_id in sorted(self._concepts_by_work.get(work_id, {})):
                    if concept_id in concept_ids:
                        edges.append(
                            {"source": work_id, "target": concept_id, "kind": "has_concept"}
                        )
        kind_order = {"work": 0, "author": 1, "concept": 2}
        nodes.sort(key=lambda node: (kind_order[node["kind"]], node["id"]))
        edges.sort(key=lambda edge: (edge["source"], edge["target"], edge["kind"]))
        return GraphView(nodes=nodes, edges=edges)

    def project_stats(self, project_id: str) -> ProjectStats:
        included = self._included_work_ids(project_id)
        citations = {
            (source, target)
            for source, target in self._citations
            if source in included and target in included
        }
        authors = {
            author_id
            for work_id in included
            for author_id in self._authors_by_work.get(work_id, {})
            if author_id in self._authors
        }
        concepts = {
            concept_id
            for work_id in included
            for concept_id in self._concepts_by_work.get(work_id, {})
            if concept_id in self._concepts
        }
        documents = sum(self._works[work_id].document_sha256 is not None for work_id in included)
        return ProjectStats(
            works=len(included),
            citations=len(citations),
            authors=len(authors),
            concepts=len(concepts),
            documents=documents,
        )
