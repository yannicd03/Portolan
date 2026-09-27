"""Neo4j implementation of the v1 research graph.

The adapter deliberately returns plain maps from Cypher and constructs the
Pydantic models at this boundary.  Neo4j ``Record``/``Node`` objects are
mapping-like in slightly surprising ways, so none of the read path relies on
iterating those objects as dictionaries.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any, ClassVar

from neo4j import GraphDatabase

from .base import (
    ResearchGraph,
    escape_lucene_query,
    mint_author_id,
    mint_project_id,
    mint_work_id,
)
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

_WORK_FIELDS: tuple[str, ...] = (
    "id",
    "title",
    "year",
    "abstract",
    "doi",
    "arxiv_id",
    "openalex_id",
    "s2_id",
    "venue",
    "work_type",
    "source_tier",
    "cited_by_count",
    "keywords",
    "keyword_scores",
    "document_sha256",
    "document_source_url",
)
_AUTHOR_FIELDS: tuple[str, ...] = ("id", "name", "orcid", "openalex_id", "s2_id")
_CONCEPT_FIELDS: tuple[str, ...] = ("id", "label", "aliases")
_INCLUSION_METHODS = frozenset({"seed", "search", "backward", "forward", "manual"})


def _record_value(record: Any, alias: str, default: Any = None) -> Any:
    """Read one explicit alias from a Neo4j record.

    A real ``neo4j.Record`` iterates over values, and therefore must never be
    converted with ``dict(record)``.  The explicit item lookup also works with
    the small record fakes commonly used by callers.
    """

    try:
        return record[alias]
    except (KeyError, IndexError, TypeError):
        getter = getattr(record, "get", None)
        if callable(getter):
            try:
                return getter(alias, default)
            except TypeError:
                try:
                    return getter(alias)
                except (KeyError, IndexError):
                    return default
        if isinstance(record, Mapping):
            return record.get(alias, default)
        return default


def _native(value: Any) -> Any:
    """Convert Neo4j temporal values and nested maps to ordinary Python values."""

    if hasattr(value, "to_native") and callable(value.to_native):
        return _native(value.to_native())
    if isinstance(value, Mapping):
        return {key: _native(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_native(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_native(item) for item in value)
    return value


def _bolt(value: Any) -> Any:
    """Coerce values to the scalar types accepted by the Bolt protocol."""

    # Importing Decimal lazily keeps this module's import surface small while
    # making the conversion explicit for callers passing Decimal scores.
    from decimal import Decimal

    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value
    if isinstance(value, Mapping):
        return {str(key): _bolt(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_bolt(item) for item in value]
    return value


def _model_values(model: Any, fields: Sequence[str]) -> dict[str, Any]:
    """Get model values without assuming an exact Pydantic minor version."""

    dump = getattr(model, "model_dump", None)
    if callable(dump):
        try:
            values = dump(mode="python", exclude_none=False)
        except TypeError:
            values = dump(exclude_none=False)
        return {field: values.get(field) for field in fields if field in values}
    if isinstance(model, Mapping):
        return {field: model.get(field) for field in fields if field in model}
    return {field: getattr(model, field, None) for field in fields}


def _clean_props(values: Mapping[str, Any], *, omit: Iterable[str] = ()) -> dict[str, Any]:
    omitted = set(omit)
    return _bolt(
        {key: value for key, value in values.items() if key not in omitted and value is not None}
    )


def _work_model(values: Any) -> WorkNode:
    data = _native(values or {})
    return WorkNode.model_validate(data)


def _author_model(values: Any) -> AuthorNode:
    data = _native(values or {})
    return AuthorNode.model_validate(data)


def _concept_model(values: Any) -> ConceptNode:
    data = _native(values or {})
    return ConceptNode.model_validate(data)


def _project_model(values: Any) -> Project:
    data = _native(values or {})
    return Project.model_validate(data)


def _summary_from_record(record: Any, prefix: str = "") -> WorkSummary:
    if prefix:
        values = {
            field: _record_value(record, f"{prefix}{field}") for field in WorkSummary.model_fields
        }
    else:
        values = {field: _record_value(record, field) for field in WorkSummary.model_fields}
    if values.get("cited_by_count") is None:
        values["cited_by_count"] = 0
    return WorkSummary.model_validate(_native(values))


def _summary_from_map(values: Any) -> WorkSummary:
    return WorkSummary.model_validate(_native(values or {}))


class Neo4jResearchGraph(ResearchGraph):
    """Neo4j-backed implementation of the v1 research graph contract."""

    _WORK_ID_FIELDS: ClassVar[tuple[str, ...]] = ("doi", "arxiv_id", "openalex_id", "s2_id")

    def __init__(
        self,
        uri: str | None = None,
        user: str | None = None,
        password: str | None = None,
        database: str | None = None,
    ) -> None:
        if uri is None:
            uri = os.getenv("NEO4J_URI") or os.getenv("PORTOLAN_TEST_NEO4J_URI")
        if user is None:
            user = os.getenv("NEO4J_USER") or os.getenv("PORTOLAN_TEST_NEO4J_USER", "neo4j")
        if password is None:
            password = os.getenv("NEO4J_PASSWORD") or os.getenv("PORTOLAN_TEST_NEO4J_PASSWORD")
        if not uri:
            raise ValueError("Neo4j URI is required")
        if password is None:
            raise ValueError("Neo4j password is required")
        self._database = (
            database or os.getenv("NEO4J_DATABASE") or os.getenv("PORTOLAN_TEST_NEO4J_DATABASE")
        )
        self.database = self._database
        self._driver = GraphDatabase.driver(uri, auth=(user, password))

    @classmethod
    def from_driver(cls, driver: Any, database: str | None = None) -> Neo4jResearchGraph:
        instance = cls.__new__(cls)
        instance._driver = driver
        instance._database = database
        instance.database = database
        return instance

    def _run(self, cypher: str, /, **params: Any) -> list[Any]:
        """Run a query and return its records as a list.

        ``execute_query`` is preferable to manually managing sessions because
        it handles retryable transactions for writes.  The session fallback is
        useful for small driver doubles and older 5.x drivers.
        """

        driver = self._driver
        parameters = _bolt(params)
        execute_query = getattr(driver, "execute_query", None)
        if callable(execute_query):
            kwargs: dict[str, Any] = {"parameters_": parameters}
            if self._database is not None:
                kwargs["database_"] = self._database
            result = execute_query(cypher, **kwargs)
            records = getattr(result, "records", result)
            return list(records)

        session_kwargs = {"database": self._database} if self._database is not None else {}
        session = driver.session(**session_kwargs)
        try:
            result = session.run(cypher, **parameters)
            return list(result)
        finally:
            close = getattr(session, "close", None)
            if callable(close):
                close()

    def close(self) -> None:
        close = getattr(self._driver, "close", None)
        if callable(close):
            close()

    # Schema -----------------------------------------------------------------

    def ensure_schema(self) -> None:
        constraints = (
            ("project_id", "Project", "id"),
            ("work_id", "Work", "id"),
            ("author_id", "Author", "id"),
            ("concept_id", "Concept", "id"),
            ("work_doi", "Work", "doi"),
            ("work_arxiv_id", "Work", "arxiv_id"),
            ("work_openalex_id", "Work", "openalex_id"),
            ("work_s2_id", "Work", "s2_id"),
            ("author_orcid", "Author", "orcid"),
            ("author_openalex_id", "Author", "openalex_id"),
        )
        for name, label, field in constraints:
            self._run(
                f"CREATE CONSTRAINT {name}_unique IF NOT EXISTS "
                f"FOR (n:{label}) REQUIRE n.{field} IS UNIQUE"
            )
        self._run(
            "CREATE FULLTEXT INDEX work_text IF NOT EXISTS "
            "FOR (w:Work) ON EACH [w.title, w.abstract]"
        )
        self._run(
            "CREATE FULLTEXT INDEX concept_text IF NOT EXISTS FOR (c:Concept) ON EACH [c.label]"
        )

    # Projects ---------------------------------------------------------------

    def create_project(self, name: str, description: str | None = None) -> Project:
        project = Project(
            id=mint_project_id(name),
            name=name,
            description=description,
            created_at=datetime.now(UTC),
        )
        self._run(
            "CREATE (p:Project {id: $id, name: $name, description: $description, "
            "created_at: $created_at})",
            **_model_values(project, ("id", "name", "description", "created_at")),
        )
        return project

    def get_project(self, project_id: str) -> Project | None:
        records = self._run(
            "MATCH (p:Project {id: $id}) "
            "RETURN p{.id, .name, .description, .created_at} AS project",
            id=project_id,
        )
        if not records:
            return None
        return _project_model(_record_value(records[0], "project"))

    def list_projects(self) -> list[Project]:
        records = self._run(
            "MATCH (p:Project) "
            "RETURN p{.id, .name, .description, .created_at} AS project, "
            "p.created_at AS sort_created_at, p.id AS sort_id "
            "ORDER BY sort_created_at DESC, sort_id"
        )
        return [_project_model(_record_value(record, "project")) for record in records]

    def delete_project(self, project_id: str) -> bool:
        records = self._run(
            "MATCH (p:Project {id: $id}) WITH p, count(p) AS found DETACH DELETE p RETURN found",
            id=project_id,
        )
        return bool(records and (_record_value(records[0], "found") or 0))

    # Global entities --------------------------------------------------------

    def _resolve_work_id(self, values: Mapping[str, Any]) -> str | None:
        conditions = ["($id IS NOT NULL AND w.id = $id)"]
        for field in self._WORK_ID_FIELDS:
            conditions.append(f"(${field} IS NOT NULL AND w.{field} = ${field})")
        query = (
            "MATCH (w:Work) WHERE "
            + " OR ".join(conditions)
            + " RETURN w.id AS resolved_id "
            + "ORDER BY CASE WHEN $id IS NOT NULL AND w.id = $id THEN 0 ELSE 1 END, "
            + "resolved_id LIMIT 1"
        )
        records = self._run(
            query,
            **{field: values.get(field) for field in ("id", *self._WORK_ID_FIELDS)},
        )
        return _record_value(records[0], "resolved_id") if records else None

    def upsert_work(self, work: WorkNode) -> WorkNode:
        values = _model_values(work, _WORK_FIELDS)
        values["doi"] = values.get("doi").lower() if values.get("doi") else None
        existing_id = self._resolve_work_id(values)
        stored_id = existing_id or values.get("id") or mint_work_id(work)
        for field in ("keywords", "keyword_scores"):
            if not values.get(field):
                values.pop(field, None)
        props = _clean_props(values, omit=("id",))
        records = self._run(
            "MERGE (w:Work {id: $id}) SET w += $props RETURN w{.*} AS work",
            id=stored_id,
            props=props,
        )
        if not records:
            return work.model_copy(update={"id": stored_id})
        return _work_model(_record_value(records[0], "work"))

    def project_works(self, project_id: str) -> list[WorkNode]:
        records = self._run(
            "MATCH (p:Project {id: $project_id})-[:INCLUDES]->(w:Work) "
            "RETURN w{.*} AS work, w.id AS work_id ORDER BY work_id",
            project_id=project_id,
        )
        return [_work_model(_record_value(record, "work")) for record in records]

    def get_work(self, work_id: str) -> WorkNode | None:
        records = self._run("MATCH (w:Work {id: $id}) RETURN w{.*} AS work", id=work_id)
        return _work_model(_record_value(records[0], "work")) if records else None

    def include_work(self, inclusion: Inclusion) -> None:
        values = _model_values(
            inclusion,
            ("project_id", "work_id", "discovered_via", "depth", "score"),
        )
        method = values.get("discovered_via")
        if hasattr(method, "value"):
            method = method.value
        if method not in _INCLUSION_METHODS:
            raise ValueError(f"invalid discovered_via: {method!r}")
        self._run(
            "MATCH (p:Project {id: $project_id}), (w:Work {id: $work_id}) "
            "MERGE (p)-[i:INCLUDES]->(w) "
            "ON CREATE SET i.discovered_via = $discovered_via, i.depth = $depth, "
            "i.score = $score, i.added_at = $added_at "
            "ON MATCH SET i.depth = CASE WHEN i.depth IS NULL OR $depth < i.depth "
            "THEN $depth ELSE i.depth END, "
            "i.score = CASE WHEN i.score IS NULL THEN $score ELSE i.score END "
            "RETURN i",
            project_id=values["project_id"],
            work_id=values["work_id"],
            discovered_via=method,
            depth=values.get("depth", 0),
            score=values.get("score"),
            added_at=datetime.now(UTC),
        )

    def add_citation(self, citing_id: str, cited_id: str) -> None:
        if citing_id == cited_id:
            return
        self._run(
            "MATCH (citing:Work {id: $citing_id}), (cited:Work {id: $cited_id}) "
            "MERGE (citing)-[:CITES]->(cited)",
            citing_id=citing_id,
            cited_id=cited_id,
        )

    def _resolve_author_id(self, values: Mapping[str, Any]) -> str | None:
        conditions = ["($id IS NOT NULL AND a.id = $id)"]
        for field in ("orcid", "openalex_id", "s2_id"):
            conditions.append(f"(${field} IS NOT NULL AND a.{field} = ${field})")
        records = self._run(
            "MATCH (a:Author) WHERE "
            + " OR ".join(conditions)
            + " RETURN a.id AS resolved_id "
            + "ORDER BY CASE WHEN $id IS NOT NULL AND a.id = $id THEN 0 ELSE 1 END, "
            + "resolved_id LIMIT 1",
            **{field: values.get(field) for field in ("id", "orcid", "openalex_id", "s2_id")},
        )
        return _record_value(records[0], "resolved_id") if records else None

    def upsert_author(self, author: AuthorNode) -> AuthorNode:
        values = _model_values(author, _AUTHOR_FIELDS)
        existing_id = self._resolve_author_id(values)
        stored_id = existing_id or values.get("id") or mint_author_id(author)
        props = _clean_props(values, omit=("id",))
        records = self._run(
            "MERGE (a:Author {id: $id}) SET a += $props RETURN a{.*} AS author",
            id=stored_id,
            props=props,
        )
        return (
            _author_model(_record_value(records[0], "author"))
            if records
            else author.model_copy(update={"id": stored_id})
        )

    def set_authors(self, work_id: str, authors: Sequence[tuple[str, int]]) -> None:
        self._run("MATCH (w:Work {id: $work_id})-[r:AUTHORED_BY]->() DELETE r", work_id=work_id)
        rows = [{"author_id": author_id, "position": position} for author_id, position in authors]
        if rows:
            self._run(
                "MATCH (w:Work {id: $work_id}) "
                "UNWIND $authors AS item "
                "MATCH (a:Author {id: item.author_id}) "
                "MERGE (w)-[r:AUTHORED_BY]->(a) SET r.position = item.position",
                work_id=work_id,
                authors=rows,
            )

    def upsert_concept(self, concept: ConceptNode) -> ConceptNode:
        values = _model_values(concept, _CONCEPT_FIELDS)
        concept_records = self._run(
            "MATCH (c:Concept {id: $id}) RETURN c{.*} AS concept",
            id=values.get("id"),
        )
        existing_aliases: list[str] = []
        if concept_records:
            existing = _native(_record_value(concept_records[0], "concept") or {})
            existing_aliases = [str(item) for item in (existing.get("aliases") or [])]
        aliases = sorted(
            {*existing_aliases, *(str(item) for item in (values.get("aliases") or []))}
        )
        records = self._run(
            "MERGE (c:Concept {id: $id}) "
            "SET c.label = $label, c.aliases = $aliases "
            "RETURN c{.*} AS concept",
            id=values.get("id"),
            label=values.get("label"),
            aliases=aliases,
        )
        return _concept_model(_record_value(records[0], "concept")) if records else concept

    def set_concepts(self, work_id: str, concepts: Sequence[tuple[str, float]]) -> None:
        self._run("MATCH (w:Work {id: $work_id})-[r:HAS_CONCEPT]->() DELETE r", work_id=work_id)
        rows = [{"concept_id": concept_id, "score": score} for concept_id, score in concepts]
        if rows:
            self._run(
                "MATCH (w:Work {id: $work_id}) "
                "UNWIND $concepts AS item "
                "MATCH (c:Concept {id: item.concept_id}) "
                "MERGE (w)-[r:HAS_CONCEPT]->(c) SET r.score = item.score",
                work_id=work_id,
                concepts=rows,
            )

    def set_document(self, work_id: str, sha256: str, source_url: str | None) -> None:
        self._run(
            "MATCH (w:Work {id: $work_id}) "
            "SET w.document_sha256 = $sha256, w.document_source_url = $source_url",
            work_id=work_id,
            sha256=sha256,
            source_url=source_url,
        )

    # Read map ---------------------------------------------------------------

    def search_works(self, project_id: str, query: str, *, limit: int = 20) -> list[WorkSummary]:
        if limit <= 0 or not query.strip():
            return []
        escaped = escape_lucene_query(query)
        records = self._run(
            "CALL db.index.fulltext.queryNodes('work_text', $query) YIELD node AS w, score "
            "MATCH (p:Project {id: $project_id})-[:INCLUDES]->(w) "
            "RETURN w.id AS id, w.title AS title, w.year AS year, "
            "w.cited_by_count AS cited_by_count, w.document_sha256 AS document_sha256, "
            "score AS sort_score "
            "ORDER BY sort_score DESC, id LIMIT $limit",
            query=escaped,
            project_id=project_id,
            limit=max(0, limit),
        )
        return [_summary_from_record(record) for record in records]

    def work_neighborhood(self, work_id: str, project_id: str | None = None) -> WorkNeighborhood:
        work_records = self._run(
            "MATCH (w:Work {id: $work_id}) RETURN w{.*} AS work",
            work_id=work_id,
        )
        if not work_records:
            raise KeyError(f"work not found: {work_id}")
        work = _work_model(_record_value(work_records[0], "work"))
        if project_id is not None:
            membership = self._run(
                "MATCH (p:Project {id: $project_id})-[:INCLUDES]->(w:Work {id: $work_id}) "
                "RETURN count(w) AS included",
                project_id=project_id,
                work_id=work_id,
            )
            if not membership or not _record_value(membership[0], "included"):
                return WorkNeighborhood(work=work)
        project_filter = ""
        params: dict[str, Any] = {"work_id": work_id}
        if project_id is not None:
            project_filter = (
                " AND EXISTS { MATCH (p:Project {id: $project_id})-[:INCLUDES]->(other) "
                "WHERE other.id = neighbour.id }"
            )
            params["project_id"] = project_id
        cites = self._run(
            "MATCH (w:Work {id: $work_id})-[:CITES]->(neighbour:Work) "
            "WHERE true" + project_filter + " "
            "RETURN neighbour.id AS id, neighbour.title AS title, neighbour.year AS year, "
            "neighbour.cited_by_count AS cited_by_count, "
            "neighbour.document_sha256 AS document_sha256 "
            "ORDER BY id",
            **params,
        )
        cited_by = self._run(
            "MATCH (neighbour:Work)-[:CITES]->(w:Work {id: $work_id}) "
            "WHERE true" + project_filter + " "
            "RETURN neighbour.id AS id, neighbour.title AS title, neighbour.year AS year, "
            "neighbour.cited_by_count AS cited_by_count, "
            "neighbour.document_sha256 AS document_sha256 "
            "ORDER BY id",
            **params,
        )
        author_records = self._run(
            "MATCH (w:Work {id: $work_id})-[r:AUTHORED_BY]->(a:Author) "
            "RETURN a{.*} AS author, a.id AS author_id, r.position AS position "
            "ORDER BY position, author_id",
            work_id=work_id,
        )
        concept_records = self._run(
            "MATCH (w:Work {id: $work_id})-[r:HAS_CONCEPT]->(c:Concept) "
            "RETURN c{.*} AS concept, c.id AS concept_id, r.score AS score "
            "ORDER BY score DESC, concept_id",
            work_id=work_id,
        )
        return WorkNeighborhood(
            work=work,
            cites=[_summary_from_record(record) for record in cites],
            cited_by=[_summary_from_record(record) for record in cited_by],
            authors=[
                (
                    _author_model(_record_value(record, "author")),
                    int(_record_value(record, "position")),
                )
                for record in author_records
            ],
            concepts=[
                (
                    _concept_model(_record_value(record, "concept")),
                    float(_record_value(record, "score")),
                )
                for record in concept_records
            ],
        )

    def works_by_author(self, author_id: str, project_id: str) -> list[WorkSummary]:
        records = self._run(
            "MATCH (p:Project {id: $project_id})-[:INCLUDES]->"
            "(w:Work)-[:AUTHORED_BY]->(a:Author {id: $author_id}) "
            "RETURN w.id AS id, w.title AS title, w.year AS year, "
            "w.cited_by_count AS cited_by_count, "
            "w.document_sha256 AS document_sha256 ORDER BY id",
            author_id=author_id,
            project_id=project_id,
        )
        return [_summary_from_record(record) for record in records]

    def works_by_concept(self, concept_id: str, project_id: str) -> list[WorkSummary]:
        records = self._run(
            "MATCH (p:Project {id: $project_id})-[:INCLUDES]->"
            "(w:Work)-[:HAS_CONCEPT]->(c:Concept {id: $concept_id}) "
            "RETURN w.id AS id, w.title AS title, w.year AS year, "
            "w.cited_by_count AS cited_by_count, "
            "w.document_sha256 AS document_sha256 ORDER BY id",
            concept_id=concept_id,
            project_id=project_id,
        )
        return [_summary_from_record(record) for record in records]

    def project_graph(
        self, project_id: str, *, include_authors: bool = True, include_concepts: bool = True
    ) -> GraphView:
        work_records = self._run(
            "MATCH (p:Project {id: $project_id})-[:INCLUDES]->(w:Work) "
            "OPTIONAL MATCH (w)-[:CITES]->(cited:Work)<-[:INCLUDES]-(p) "
            "WITH p, w, count(DISTINCT cited) AS outgoing "
            "OPTIONAL MATCH (p)-[:INCLUDES]->(citing:Work)-[:CITES]->(w) "
            "RETURN w.id AS id, w.title AS title, w.year AS year, "
            "w.cited_by_count AS cited_by_count, w.document_sha256 IS NOT NULL AS has_document, "
            "outgoing AS outgoing, count(DISTINCT citing) AS incoming ORDER BY id",
            project_id=project_id,
        )
        nodes: list[dict[str, Any]] = [
            {
                "id": _record_value(record, "id"),
                "kind": "work",
                "label": _record_value(record, "title"),
                "data": {
                    "year": _record_value(record, "year"),
                    "cited_by_count": _record_value(record, "cited_by_count"),
                    "has_document": bool(_record_value(record, "has_document")),
                    "in_degree": int(_record_value(record, "incoming", 0) or 0),
                    "out_degree": int(_record_value(record, "outgoing", 0) or 0),
                },
            }
            for record in work_records
        ]
        edges: list[dict[str, str]] = []
        edges_records = self._run(
            "MATCH (p:Project {id: $project_id})-[:INCLUDES]->"
            "(source:Work)-[:CITES]->(target:Work) "
            "MATCH (p)-[:INCLUDES]->(target) "
            "RETURN source.id AS source_id, target.id AS target_id "
            "ORDER BY source_id, target_id",
            project_id=project_id,
        )
        edges.extend(
            {
                "source": _record_value(record, "source_id"),
                "target": _record_value(record, "target_id"),
                "kind": "cites",
            }
            for record in edges_records
        )
        if include_authors:
            author_records = self._run(
                "MATCH (p:Project {id: $project_id})-[:INCLUDES]->"
                "(w:Work)-[:AUTHORED_BY]->(a:Author) "
                "RETURN a.id AS author_id, a.name AS label, a.orcid AS orcid, "
                "a.openalex_id AS openalex_id, a.s2_id AS s2_id ORDER BY author_id",
                project_id=project_id,
            )
            seen: set[str] = set()
            for record in author_records:
                node_id = _record_value(record, "author_id")
                if node_id in seen:
                    continue
                seen.add(node_id)
                nodes.append(
                    {
                        "id": node_id,
                        "kind": "author",
                        "label": _record_value(record, "label"),
                        "data": {
                            "orcid": _record_value(record, "orcid"),
                            "openalex_id": _record_value(record, "openalex_id"),
                            "s2_id": _record_value(record, "s2_id"),
                        },
                    }
                )
            author_edges = self._run(
                "MATCH (p:Project {id: $project_id})-[:INCLUDES]->"
                "(w:Work)-[:AUTHORED_BY]->(a:Author) "
                "RETURN w.id AS source_id, a.id AS target_id "
                "ORDER BY source_id, target_id",
                project_id=project_id,
            )
            edges.extend(
                {
                    "source": _record_value(record, "source_id"),
                    "target": _record_value(record, "target_id"),
                    "kind": "authored_by",
                }
                for record in author_edges
            )
        if include_concepts:
            concept_records = self._run(
                "MATCH (p:Project {id: $project_id})-[:INCLUDES]->"
                "(w:Work)-[:HAS_CONCEPT]->(c:Concept) "
                "RETURN c.id AS concept_id, c.label AS label, c.aliases AS aliases "
                "ORDER BY concept_id",
                project_id=project_id,
            )
            seen = set()
            for record in concept_records:
                node_id = _record_value(record, "concept_id")
                if node_id in seen:
                    continue
                seen.add(node_id)
                nodes.append(
                    {
                        "id": node_id,
                        "kind": "concept",
                        "label": _record_value(record, "label"),
                        "data": {"aliases": _record_value(record, "aliases") or []},
                    }
                )
            concept_edges = self._run(
                "MATCH (p:Project {id: $project_id})-[:INCLUDES]->"
                "(w:Work)-[:HAS_CONCEPT]->(c:Concept) "
                "RETURN w.id AS source_id, c.id AS target_id "
                "ORDER BY source_id, target_id",
                project_id=project_id,
            )
            edges.extend(
                {
                    "source": _record_value(record, "source_id"),
                    "target": _record_value(record, "target_id"),
                    "kind": "has_concept",
                }
                for record in concept_edges
            )
        kind_order = {"work": 0, "author": 1, "concept": 2}
        nodes.sort(key=lambda node: (kind_order[node["kind"]], node["id"]))
        edges.sort(key=lambda edge: (edge["source"], edge["target"], edge["kind"]))
        return GraphView(nodes=nodes, edges=edges)

    def project_stats(self, project_id: str) -> ProjectStats:
        work_records = self._run(
            "MATCH (p:Project {id: $project_id})-[:INCLUDES]->(w:Work) "
            "RETURN count(DISTINCT w) AS works, "
            "count(DISTINCT CASE WHEN w.document_sha256 IS NOT NULL THEN w END) AS documents",
            project_id=project_id,
        )
        citation_records = self._run(
            "MATCH (p:Project {id: $project_id})-[:INCLUDES]->(source:Work)-[r:CITES]->"
            "(target:Work) "
            "MATCH (p)-[:INCLUDES]->(target) "
            "RETURN count(DISTINCT r) AS citations",
            project_id=project_id,
        )
        author_records = self._run(
            "MATCH (p:Project {id: $project_id})-[:INCLUDES]->(w:Work)-[:AUTHORED_BY]->"
            "(a:Author) RETURN count(DISTINCT a) AS authors",
            project_id=project_id,
        )
        concept_records = self._run(
            "MATCH (p:Project {id: $project_id})-[:INCLUDES]->(w:Work)-[:HAS_CONCEPT]->"
            "(c:Concept) RETURN count(DISTINCT c) AS concepts",
            project_id=project_id,
        )
        work_record = work_records[0] if work_records else None
        return ProjectStats(
            works=int(_record_value(work_record, "works", 0) or 0),
            citations=int(_record_value(citation_records[0], "citations", 0) or 0)
            if citation_records
            else 0,
            authors=int(_record_value(author_records[0], "authors", 0) or 0)
            if author_records
            else 0,
            concepts=int(_record_value(concept_records[0], "concepts", 0) or 0)
            if concept_records
            else 0,
            documents=int(_record_value(work_record, "documents", 0) or 0),
        )


__all__ = ["Neo4jResearchGraph"]
