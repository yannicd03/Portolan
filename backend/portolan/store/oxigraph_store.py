"""Oxigraph implementation of the store-neutral graph repository.

The repository deliberately keeps the RDF binding here instead of exposing
SPARQL to callers.  Models are converted to RDF by their field names and the
vocabulary contract; this also makes the backend tolerant of small changes in
the Pydantic implementation while the ontology work is in progress.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from pyoxigraph import (
    BlankNode,
    Literal,
    NamedNode,
    Quad,
    RdfFormat,
    Store,
)

try:  # The interface is supplied by the store-neutral layer.
    from .repository import GraphRepository, QueryResult, SubgraphResult
except ImportError:  # pragma: no cover - useful while the parallel layer is bootstrapped
    from dataclasses import dataclass

    class GraphRepository:  # type: ignore[no-redef]
        """Temporary import-time fallback; the real interface lives in repository.py."""

    @dataclass
    class SubgraphResult:  # type: ignore[no-redef]
        nodes: list[dict[str, Any]]
        edges: list[dict[str, Any]]

    @dataclass
    class QueryResult:  # type: ignore[no-redef]
        columns: list[str]
        rows: list[dict[str, Any]]


try:
    from .validation import validate_graph
except ImportError:  # pragma: no cover - validation.py is written by the repository layer

    def validate_graph(*_: Any, **__: Any) -> Any:
        raise RuntimeError("portolan.store.validation is not available")


PTL_NS = "https://w3id.org/portolan/ontology#"
PTLR_NS = "https://w3id.org/portolan/id/"
PTLG_NS = "https://w3id.org/portolan/graph/"
CITO_NS = "http://purl.org/spar/cito/"
FABIO_NS = "http://purl.org/spar/fabio/"
SKOS_NS = "http://www.w3.org/2004/02/skos/core#"
PROV_NS = "http://www.w3.org/ns/prov#"
DCTERMS_NS = "http://purl.org/dc/terms/"
FOAF_NS = "http://xmlns.com/foaf/0.1/"
RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS_NS = "http://www.w3.org/2000/01/rdf-schema#"
XSD_NS = "http://www.w3.org/2001/XMLSchema#"

BIBLIO_GRAPH = NamedNode(f"{PTLG_NS}biblio")
DEFAULT_CONTENT_RUN = "default"


def _iri(value: Any) -> str:
    """Return an IRI from a model, RDF term, or already-minted string."""

    if isinstance(value, NamedNode):
        return value.value
    if isinstance(value, (BlankNode, Literal)):
        return str(value.value)
    candidate = getattr(value, "iri", None)
    if candidate is not None:
        candidate = candidate() if callable(candidate) else candidate
        return str(candidate)
    if isinstance(value, Mapping) and "iri" in value:
        return str(value["iri"])
    return str(value)


def _graph_name(value: Any) -> NamedNode:
    if isinstance(value, NamedNode):
        return value
    text = str(value)
    if not text.startswith(("http://", "https://", "urn:")):
        text = f"{PTLG_NS}{text.lstrip('/')}"
    return NamedNode(text)


def content_graph(run_id: Any = DEFAULT_CONTENT_RUN) -> NamedNode:
    """Return ``ptlg:content/{runId}``, percent-encoding the partition id."""

    return NamedNode(f"{PTLG_NS}content/{quote(str(run_id), safe='')}")


def review_graph(review_id: Any) -> NamedNode:
    """Return ``ptlg:review/{reviewId}``, percent-encoding the partition id."""

    return NamedNode(f"{PTLG_NS}review/{quote(str(review_id), safe='')}")


def _class_name(model: Any) -> str:
    return type(model).__name__


def _field_values(model: Any) -> dict[str, Any]:
    if isinstance(model, Mapping):
        return dict(model)
    model_fields = getattr(type(model), "model_fields", None)
    if model_fields:
        values: dict[str, Any] = {}
        for field_name in model_fields:
            value = getattr(model, field_name, None)
            if value is not None:
                values[field_name] = value
        with suppress(AttributeError, TypeError, ValueError):
            values["iri"] = model.iri
        return values
    dump = getattr(model, "model_dump", None)
    if callable(dump):
        try:
            return dict(dump(mode="python", exclude_none=True))
        except TypeError:
            return dict(dump(exclude_none=True))
    if hasattr(model, "__dict__"):
        return {
            key: value
            for key, value in vars(model).items()
            if not key.startswith("_") and value is not None
        }
    return {}


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        return list(value)
    return [value]


def _camel(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


PREDICATE_OVERRIDES: dict[str, str] = {
    "title": f"{DCTERMS_NS}title",
    "abstract": f"{DCTERMS_NS}abstract",
    "issued": f"{DCTERMS_NS}issued",
    "label": f"{RDFS_NS}label",
    "alt_label": f"{SKOS_NS}altLabel",
    "alt_labels": f"{SKOS_NS}altLabel",
    "pref_label": f"{SKOS_NS}prefLabel",
    "work_type": f"{PTL_NS}workType",
    "is_survey": f"{PTL_NS}isSurvey",
    "source_tier": f"{PTL_NS}sourceTier",
    "org_kind": f"{PTL_NS}orgKind",
    "venue_kind": f"{PTL_NS}venueKind",
    "seed_kind": f"{PTL_NS}seedKind",
    "discovered_via": f"{PTL_NS}discoveredVia",
    "gap_type": f"{PTL_NS}gapType",
    "verification_outcome": f"{PTL_NS}verificationOutcome",
    "user_status": f"{PTL_NS}userStatus",
    "contribution_kind": f"{PTL_NS}contributionKind",
    "from_source_kind": f"{PTL_NS}fromSourceKind",
    "citation_function": f"{PTL_NS}citationFunction",
    "claims_sota": f"{PTL_NS}claimsSOTA",
    "of_work": f"{PTL_NS}ofWork",
    "of_method": f"{PTL_NS}ofMethod",
    "on_dataset": f"{PTL_NS}onDataset",
    "with_metric": f"{PTL_NS}withMetric",
    "from_work": f"{PTL_NS}fromWork",
    "has_evidence": f"{PTL_NS}hasEvidence",
    "was_generated_by": f"{PROV_NS}wasGeneratedBy",
    "has_protocol": f"{PTL_NS}hasProtocol",
    "of_review": f"{PTL_NS}ofReview",
    "in_cluster": f"{PTL_NS}inCluster",
    "supported_by": f"{PTL_NS}supportedBy",
    "about": f"{PTL_NS}about",
    "published_in": f"{PTL_NS}publishedIn",
    "published_by": f"{PTL_NS}publishedBy",
    "authored_by": f"{PTL_NS}authoredBy",
    "affiliated_with": f"{PTL_NS}affiliatedWith",
    "has_version": f"{PTL_NS}hasVersion",
    "is_canonical_version": f"{PTL_NS}isCanonicalVersion",
    "citing_work": f"{PTL_NS}citingWork",
    "cited_work": f"{PTL_NS}citedWork",
    "limitation_of": f"{PTL_NS}limitationOf",
    "extends_method": f"{PTL_NS}extendsMethod",
    "first_seen_in": f"{PTL_NS}firstSeenIn",
    "exact_match": f"{SKOS_NS}exactMatch",
    "close_match": f"{SKOS_NS}closeMatch",
    "parent_cluster": f"{PTL_NS}parentCluster",
    "top_concept": f"{PTL_NS}topConcept",
}

OBJECT_FIELDS = {
    "addresses",
    "addresses_limitation",
    "about",
    "proposes",
    "uses",
    "evaluates_on",
    "reports",
    "of_method",
    "on_dataset",
    "with_metric",
    "supports_claim",
    "contradicts_claim",
    "limitation_of",
    "of_work",
    "from_work",
    "authored_by",
    "affiliated_with",
    "published_in",
    "published_by",
    "has_version",
    "citing_work",
    "cited_work",
    "broader",
    "narrower",
    "extends_method",
    "first_seen_in",
    "exact_match",
    "close_match",
    "has_evidence",
    "was_generated_by",
    "of_review",
    "in_cluster",
    "parent_cluster",
    "top_concept",
    "cluster_a",
    "cluster_b",
    "supported_by",
    "has_protocol",
}


def predicate_for_field(field_name: str) -> NamedNode:
    if field_name in PREDICATE_OVERRIDES:
        return NamedNode(PREDICATE_OVERRIDES[field_name])
    return NamedNode(f"{PTL_NS}{_camel(field_name)}")


def _is_iri_field(field_name: str, value: Any) -> bool:
    if field_name in OBJECT_FIELDS:
        return True
    if field_name in {"exact_match", "close_match"}:
        return True
    return hasattr(value, "iri") or (isinstance(value, Mapping) and "iri" in value)


def _literal(value: Any, predicate: str | None = None) -> Literal:
    value = _enum_value(value)
    if isinstance(value, Literal):
        return value
    if isinstance(value, bool):
        return Literal("true" if value else "false", datatype=NamedNode(f"{XSD_NS}boolean"))
    if isinstance(value, int) and not isinstance(value, bool):
        return Literal(str(value), datatype=NamedNode(f"{XSD_NS}integer"))
    if isinstance(value, Decimal):
        return Literal(str(value), datatype=NamedNode(f"{XSD_NS}decimal"))
    if isinstance(value, float):
        return Literal(str(value), datatype=NamedNode(f"{XSD_NS}decimal"))
    if isinstance(value, datetime):
        return Literal(value.isoformat(), datatype=NamedNode(f"{XSD_NS}dateTime"))
    if isinstance(value, date):
        return Literal(value.isoformat(), datatype=NamedNode(f"{XSD_NS}date"))
    if isinstance(value, str) and re.fullmatch(r"\d{4}", value):
        return Literal(value, datatype=NamedNode(f"{XSD_NS}gYear"))
    if isinstance(value, (dict, list, tuple, set, frozenset)):
        return Literal(json.dumps(value, default=str, sort_keys=True))
    return Literal(str(value))


def _rdf_type_for(model: Any) -> NamedNode:
    name = _class_name(model)
    return NamedNode(f"{PTL_NS}{name}")


def _alignment_type(model: Any) -> NamedNode | None:
    name = _class_name(model)
    return {
        "Work": NamedNode(f"{FABIO_NS}Work"),
        "Author": NamedNode(f"{FOAF_NS}Person"),
        "Organization": NamedNode(f"{FOAF_NS}Organization"),
        "Venue": NamedNode(f"{FABIO_NS}Journal"),
        "Citation": NamedNode(f"{CITO_NS}Citation"),
        "Evidence": NamedNode(f"{PROV_NS}Entity"),
        "ExtractionRun": NamedNode(f"{PROV_NS}Activity"),
    }.get(name)


def _object_term(value: Any) -> NamedNode | BlankNode | Literal:
    if isinstance(value, (NamedNode, BlankNode)):
        return value
    if isinstance(value, Mapping) and "iri" not in value:
        return BlankNode()
    text = _iri(value)
    try:
        return NamedNode(text)
    except ValueError:
        # A model may carry an unresolved surface identifier (for example a
        # dataset slug); keep the write lossless and let SHACL flag the bad
        # object kind instead of failing the entire batch at the RDF API.
        return Literal(text)


def _expand_vocab_iri(value: str) -> str:
    if ":" not in value or value.startswith(("http://", "https://", "urn:")):
        return value
    prefix, local = value.split(":", 1)
    namespaces = {
        "ptl": PTL_NS,
        "ptlg": PTLG_NS,
        "ptlr": PTLR_NS,
        "cito": CITO_NS,
        "dcterms": DCTERMS_NS,
        "fabio": FABIO_NS,
        "foaf": FOAF_NS,
        "prov": PROV_NS,
        "rdf": RDF_NS,
        "rdfs": RDFS_NS,
        "skos": SKOS_NS,
        "xsd": XSD_NS,
    }
    return f"{namespaces[prefix]}{local}" if prefix in namespaces else value


def model_quads(model: Any, graph: NamedNode) -> list[Quad]:
    """Convert one domain model to quads in ``graph``.

    This function is intentionally public: it is useful to adapters and makes
    the RDF representation independently testable without a repository.
    """

    subject = NamedNode(_iri(model))
    quads = [Quad(subject, NamedNode(f"{RDF_NS}type"), _rdf_type_for(model), graph)]
    aligned = _alignment_type(model)
    if aligned is not None:
        quads.append(Quad(subject, NamedNode(f"{RDF_NS}type"), aligned, graph))
    if _class_name(model) in {"Problem", "Method", "Dataset", "Metric"}:
        quads.append(
            Quad(
                NamedNode(f"{PTL_NS}{_class_name(model)}"),
                NamedNode(f"{RDFS_NS}subClassOf"),
                NamedNode(f"{SKOS_NS}Concept"),
                graph,
            )
        )

    field_map: dict[str, dict[str, str]] = {}
    property_map = getattr(type(model), "property_map", None)
    if callable(property_map):
        with suppress(Exception):
            field_map = property_map()
    identity_fields = {
        "iri",
        "id",
        "model_config",
        "run_id",
        "review_id",
        "protocol_id",
        "number",
        "cluster_number",
        "gap_number",
        "slug",
    }
    for field_name, raw_value in _field_values(model).items():
        if field_name in identity_fields or raw_value is None:
            continue
        if field_name.startswith("_"):
            continue
        mapped = field_map.get(field_name, {}).get("rdf")
        predicate = (
            NamedNode(_expand_vocab_iri(mapped)) if mapped else predicate_for_field(field_name)
        )
        for value in _as_list(raw_value):
            value = _enum_value(value)
            if field_name == "citation_function":
                function = str(value)
                object_term = NamedNode(
                    function
                    if function.startswith(("http://", "https://"))
                    else f"{CITO_NS}{function.removeprefix('cito:')}"
                )
                quads.append(Quad(subject, predicate, object_term, graph))
            elif _is_iri_field(field_name, value):
                try:
                    object_term = _object_term(value)
                except (TypeError, ValueError):
                    object_term = _literal(value)
                quads.append(Quad(subject, predicate, object_term, graph))
            elif isinstance(value, Mapping):
                quads.append(Quad(subject, predicate, _literal(value), graph))
            else:
                quads.append(Quad(subject, predicate, _literal(value, str(predicate)), graph))
    return quads


def _value_from_term(term: Any) -> Any:
    if isinstance(term, NamedNode):
        return term.value
    if isinstance(term, BlankNode):
        return f"_:{term.value}"
    if isinstance(term, Literal):
        text = term.value
        datatype = term.datatype.value if term.datatype else ""
        if datatype == f"{XSD_NS}boolean":
            return text.lower() == "true"
        if datatype == f"{XSD_NS}integer":
            try:
                return int(text)
            except ValueError:
                return text
        if datatype == f"{XSD_NS}decimal":
            try:
                return Decimal(text)
            except Exception:
                return text
        return text
    return term


def _make_dto(cls: Any, **values: Any) -> Any:
    if cls is None:
        return values
    try:
        return cls(**values)
    except Exception:
        # The DTOs are intentionally small, but accepting a slightly different
        # constructor keeps this adapter compatible with dataclass/Pydantic
        # implementations of the same repository contract.
        try:
            instance = cls()  # type: ignore[call-arg]
            for key, value in values.items():
                with suppress(Exception):
                    setattr(instance, key, value)
            return instance
        except Exception:
            return values


def _query_rows(result: Any) -> tuple[list[str], list[dict[str, Any]]]:
    if hasattr(result, "variables"):
        columns = [str(variable.value) for variable in result.variables]
        rows: list[dict[str, Any]] = []
        for solution in result:
            rows.append({column: _value_from_term(solution[column]) for column in columns})
        return columns, rows
    if isinstance(result, bool):
        return ["answer"], [{"answer": result}]
    rows = []
    try:
        for item in result:
            rows.append({"value": _value_from_term(item)})
    except TypeError:
        rows.append({"value": _value_from_term(result)})
    return ["value"], rows


def _normalise_identifier(value: Any) -> str:
    return _iri(value)


class OxigraphStore(GraphRepository):
    """In-memory or on-disk Oxigraph implementation of ``GraphRepository``."""

    def __init__(
        self,
        store: Store | None = None,
        *,
        path: str | Path | None = None,
        ontology_root: str | Path | None = None,
    ) -> None:
        self.store = store if store is not None else Store(str(path) if path is not None else None)
        self.ontology_root = self._resolve_ontology_root(ontology_root)
        self._batch_depth = 0

    @staticmethod
    def _resolve_ontology_root(value: str | Path | None) -> Path:
        if value is not None:
            root = Path(value)
            return root if root.name == "ontology" else root / "ontology"
        return Path(__file__).resolve().parents[3] / "ontology"

    @staticmethod
    def graph_for_content(run_id: Any = DEFAULT_CONTENT_RUN) -> NamedNode:
        return content_graph(run_id)

    @staticmethod
    def graph_for_review(review_id: Any) -> NamedNode:
        return review_graph(review_id)

    def _add(self, model: Any, graph: NamedNode, *, generated_by: Any | None = None) -> str:
        iri = _iri(model)
        quads = model_quads(model, graph)
        if generated_by is not None:
            quads.append(
                Quad(
                    NamedNode(iri),
                    NamedNode(f"{PROV_NS}wasGeneratedBy"),
                    NamedNode(_iri(generated_by)),
                    graph,
                )
            )
        for quad in quads:
            self.store.add(quad)
        return iri

    def _remove_predicate(self, subject: str, predicate: str, graph: NamedNode) -> None:
        matches = list(
            self.store.quads_for_pattern(NamedNode(subject), NamedNode(predicate), None, graph)
        )
        for quad in matches:
            self.store.remove(quad)

    def _model_partition(self, model: Any, field: str, default: Any) -> Any:
        values = _field_values(model)
        for key in (field, field.replace("Id", "_id"), field.replace("_", "")):
            if key in values and values[key] is not None:
                return values[key]
        return getattr(model, field, default)

    def _content_run_id(self, model: Any, explicit: Any | None = None) -> Any:
        if explicit is not None:
            model = explicit
        values = _field_values(model)
        for key in ("extraction_run", "extraction_run_id", "run_id", "extractionRun"):
            if values.get(key) is not None:
                candidate = values[key]
                raw_id = getattr(candidate, "run_id", None)
                if raw_id is not None:
                    return str(raw_id)
                if isinstance(candidate, Mapping) and candidate.get("run_id") is not None:
                    return str(candidate["run_id"])
                text = _iri(candidate)
                prefix = f"{PTLR_NS}extraction/"
                if text.startswith(prefix):
                    return unquote(text.removeprefix(prefix))
                return text
        raw_id = getattr(model, "run_id", None)
        if raw_id is not None:
            return str(raw_id)
        if isinstance(model, Mapping) and model.get("run_id") is not None:
            return str(model["run_id"])
        text = _iri(model)
        prefix = f"{PTLR_NS}extraction/"
        if text.startswith(prefix):
            return unquote(text.removeprefix(prefix))
        if explicit is not None:
            return text
        return DEFAULT_CONTENT_RUN

    def _review_id(self, model: Any, explicit: Any | None = None) -> str:
        if explicit is not None:
            model = explicit
        values = _field_values(model)
        for key in ("review_id", "review", "of_review"):
            if values.get(key) is not None:
                candidate = values[key]
                raw_id = getattr(candidate, "review_id", None)
                if raw_id is not None:
                    return str(raw_id)
                if isinstance(candidate, Mapping) and candidate.get("review_id") is not None:
                    return str(candidate["review_id"])
                text = _iri(candidate)
                prefix = f"{PTLR_NS}review/"
                if text.startswith(prefix):
                    return unquote(text.removeprefix(prefix))
                return text
        raw_id = getattr(model, "review_id", None)
        if raw_id is not None:
            return str(raw_id)
        if isinstance(model, Mapping) and model.get("review_id") is not None:
            return str(model["review_id"])
        text = _iri(model)
        prefix = f"{PTLR_NS}review/"
        if text.startswith(prefix):
            return unquote(text.removeprefix(prefix))
        if explicit is not None:
            return text
        raise ValueError(f"{_class_name(model)} requires a review_id for review-scoped storage")

    # Layer 1: bibliography -------------------------------------------------

    def upsert_work(self, work: Any) -> Any:
        """Write a Work and its bibliographic fields into ``ptlg:biblio``."""

        self._add(work, BIBLIO_GRAPH)
        return work

    def upsert_author(self, author: Any) -> Any:
        """Write an Author into the global bibliography graph."""

        self._add(author, BIBLIO_GRAPH)
        return author

    def upsert_organization(self, organization: Any) -> Any:
        """Write an Organization into the global bibliography graph."""

        self._add(organization, BIBLIO_GRAPH)
        return organization

    def upsert_venue(self, venue: Any) -> Any:
        """Write a Venue into the global bibliography graph."""

        self._add(venue, BIBLIO_GRAPH)
        return venue

    def add_citation(self, citation: Any) -> Any:
        """Write a reified Citation and the mandatory ``cito:cites`` shortcut."""

        graph = BIBLIO_GRAPH
        self._add(citation, graph)
        values = _field_values(citation)
        citing = values.get("citing_work") or values.get("citing")
        cited = values.get("cited_work") or values.get("cited")
        if citing is None or cited is None:
            raise ValueError("Citation requires citing_work and cited_work")
        self.store.add(
            Quad(
                NamedNode(_iri(citing)),
                NamedNode(f"{CITO_NS}cites"),
                NamedNode(_iri(cited)),
                graph,
            )
        )
        return citation

    def upsert_concept(self, concept: Any, run_id: Any | None = None, **kwargs: Any) -> Any:
        """Write a content-layer Concept into ``ptlg:content/{runId}``."""

        run_id = kwargs.get("extraction_run", kwargs.get("extraction_run_id", run_id))
        self._add(concept, content_graph(self._content_run_id(concept, run_id)))
        return concept

    # Layer 2: content and provenance --------------------------------------

    @staticmethod
    def _parse_extraction_args(
        args: Sequence[Any], kwargs: Mapping[str, Any]
    ) -> tuple[Any, list[Any], list[Any], Any | None]:
        """Accept the contract order and the original spike's positional order."""

        work = kwargs.get("work", kwargs.get("of_work"))
        run = kwargs.get("run_id", kwargs.get("extraction_run"))
        statements = kwargs.get("statements")
        evidence = kwargs.get("evidence", kwargs.get("evidences"))

        def is_run(value: Any) -> bool:
            return (
                type(value).__name__ == "ExtractionRun"
                or hasattr(value, "run_id")
                or (isinstance(value, Mapping) and "run_id" in value)
                or (
                    isinstance(value, (str, int))
                    and len(args) > 1
                    and isinstance(args[1], Sequence)
                    and not isinstance(args[1], (str, bytes))
                )
            )

        positional = list(args)
        if positional and is_run(positional[0]):
            # Legacy spike order: (run, statements, evidence[, work]).
            run = positional[0] if run is None else run
            if statements is None and len(positional) > 1:
                statements = positional[1]
            if evidence is None and len(positional) > 2:
                evidence = positional[2]
            if work is None and len(positional) > 3:
                work = positional[3]
        else:
            # Repository contract order: (work, run, statements, evidence).
            if work is None and positional:
                work = positional[0]
            if run is None and len(positional) > 1:
                run = positional[1]
            if statements is None and len(positional) > 2:
                statements = positional[2]
            if evidence is None and len(positional) > 3:
                evidence = positional[3]

        if run is None:
            raise TypeError("write_extraction requires an extraction run")
        return run, list(statements or []), list(evidence or []), work

    @staticmethod
    def _validate_extraction_batch(
        work: Any | None, statements: Sequence[Any], evidence: Sequence[Any]
    ) -> None:
        for statement in statements:
            values = _field_values(statement)
            statement_work = values.get("of_work") or values.get("ofWork")
            if (
                work is not None
                and statement_work is not None
                and _iri(statement_work) != _iri(work)
            ):
                raise ValueError("every extraction statement must belong to the supplied work")
            own_evidence = values.get("has_evidence") or values.get("evidence")
            if (
                own_evidence
                and not any(
                    _iri(item) == _iri(candidate)
                    for item in _as_list(own_evidence)
                    for candidate in evidence
                )
                and evidence
            ):
                raise ValueError(
                    "the extraction evidence batch does not contain statement evidence"
                )
        if work is not None:
            for item in evidence:
                from_work = _field_values(item).get("from_work")
                if from_work is not None and _iri(from_work) != _iri(work):
                    raise ValueError("every extraction Evidence must come from the supplied work")

    @staticmethod
    def _evidence_pairs(
        statements: Sequence[Any], evidence: Sequence[Any]
    ) -> list[tuple[Any, Any]]:
        pairs: list[tuple[Any, Any]] = []
        remaining = list(evidence)
        for statement in statements:
            values = _field_values(statement)
            own = values.get("evidence") or values.get("has_evidence") or values.get("evidences")
            if own:
                own_iris = {_iri(item) for item in _as_list(own)}
                for item in evidence:
                    if _iri(item) in own_iris:
                        pairs.append((statement, item))
                        if item in remaining:
                            remaining.remove(item)
        if len(statements) == 1:
            pairs.extend((statements[0], item) for item in remaining)
        else:
            for item in evidence:
                values = _field_values(item)
                target = (
                    values.get("statement")
                    or values.get("of_statement")
                    or values.get("statement_iri")
                )
                if target is not None:
                    for statement in statements:
                        if _iri(target) == _iri(statement):
                            pairs.append((statement, item))
        return pairs

    def write_extraction(self, *args: Any, **kwargs: Any) -> Any:
        """Write content statements/evidence and provenance into one run graph."""

        run, statements, evidence, work = self._parse_extraction_args(args, kwargs)
        known_evidence = {_iri(item) for item in evidence}
        for statement in statements:
            own = _field_values(statement).get("has_evidence") or _field_values(statement).get(
                "evidence"
            )
            for item in _as_list(own):
                if _iri(item) not in known_evidence:
                    evidence.append(item)
                    known_evidence.add(_iri(item))
        self._validate_extraction_batch(work, statements, evidence)
        run_id = self._content_run_id(run)
        graph = content_graph(run_id)
        if work is not None:
            self._add(work, BIBLIO_GRAPH)
        if hasattr(run, "iri") or isinstance(run, Mapping):
            self._add(run, graph)
            run_iri = _iri(run)
        else:
            run_iri = f"{PTLR_NS}extraction/{quote(str(run_id), safe='')}"
        for statement in statements:
            self._add(statement, graph, generated_by=run_iri)
        for item in evidence:
            self._add(item, graph, generated_by=run_iri)
        for statement, item in self._evidence_pairs(statements, evidence):
            self.store.add(
                Quad(
                    NamedNode(_iri(statement)),
                    NamedNode(f"{PTL_NS}hasEvidence"),
                    NamedNode(_iri(item)),
                    graph,
                )
            )
        if work is not None:
            for statement in statements:
                self.store.add(
                    Quad(
                        NamedNode(_iri(statement)),
                        NamedNode(f"{PTL_NS}ofWork"),
                        NamedNode(_iri(work)),
                        graph,
                    )
                )
        return run

    # Layer 3: review analysis ---------------------------------------------

    def write_review(self, review: Any, protocol: Any | None = None, **kwargs: Any) -> Any:
        """Write a Review and optional protocol into ``ptlg:review/{reviewId}``."""

        review_id = kwargs.get("review_id", self._review_id(review, kwargs.get("review")))
        graph = review_graph(review_id)
        self._add(review, graph)
        if protocol is not None:
            self.write_protocol(protocol, review_id=review_id)
        return review

    def write_protocol(self, protocol: Any, review_id: Any | None = None, **kwargs: Any) -> Any:
        """Write a ReviewProtocol in its review partition."""

        review_id = kwargs.get("review_id", review_id)
        graph = review_graph(self._review_id(protocol, review_id))
        self._add(protocol, graph)
        return protocol

    def write_inclusion(self, inclusion: Any, review_id: Any | None = None, **kwargs: Any) -> Any:
        """Write an Inclusion in its review partition."""

        graph = review_graph(self._review_id(inclusion, kwargs.get("review_id", review_id)))
        self._add(inclusion, graph)
        return inclusion

    def write_cluster(self, cluster: Any, review_id: Any | None = None, **kwargs: Any) -> Any:
        """Write a Cluster in its review partition."""

        graph = review_graph(self._review_id(cluster, kwargs.get("review_id", review_id)))
        self._add(cluster, graph)
        return cluster

    def write_cluster_pair(self, pair: Any, review_id: Any | None = None, **kwargs: Any) -> Any:
        """Write a ClusterPair node in its review partition."""

        graph = review_graph(self._review_id(pair, kwargs.get("review_id", review_id)))
        self._add(pair, graph)
        return pair

    def write_gap_hypothesis(self, gap: Any, review_id: Any | None = None, **kwargs: Any) -> Any:
        """Write a GapHypothesis in its review partition."""

        graph = review_graph(self._review_id(gap, kwargs.get("review_id", review_id)))
        self._add(gap, graph)
        return gap

    def update_gap_status(
        self,
        gap: Any,
        status: Any | None = None,
        note: str | None = None,
        review_id: Any | None = None,
        **kwargs: Any,
    ) -> Any:
        """Replace a gap's user status/note in its review graph."""

        if status is None:
            status = kwargs.get("user_status")
        if note is None:
            note = kwargs.get("user_note")
        values = _field_values(gap) if not isinstance(gap, str) else {}
        gap_iri = _iri(gap)
        review_id = (
            review_id
            or kwargs.get("review_id")
            or values.get("review_id")
            or values.get("of_review")
        )
        graph = review_graph(self._review_id(gap, review_id))
        self._remove_predicate(gap_iri, f"{PTL_NS}userStatus", graph)
        self._remove_predicate(gap_iri, f"{PTL_NS}userNote", graph)
        if status is not None:
            self.store.add(
                Quad(
                    NamedNode(gap_iri),
                    NamedNode(f"{PTL_NS}userStatus"),
                    _literal(status),
                    graph,
                )
            )
        if note is not None:
            self.store.add(
                Quad(
                    NamedNode(gap_iri),
                    NamedNode(f"{PTL_NS}userNote"),
                    _literal(note),
                    graph,
                )
            )
        return gap

    # Queries and exports ---------------------------------------------------

    def fetch_work(self, work: Any) -> Any | None:
        """Fetch all quads for a Work; return a model when it can be rebuilt."""

        work_iri = _iri(work)
        quads = []
        for graph in self.store.named_graphs():
            quads.extend(self.store.quads_for_pattern(NamedNode(work_iri), None, None, graph))
        if not quads:
            return None
        data: dict[str, Any] = {"iri": work_iri}
        for quad in quads:
            predicate = quad.predicate.value
            if predicate == f"{RDF_NS}type":
                continue
            field = _field_from_predicate(predicate)
            value = _value_from_term(quad.object)
            if field in data:
                data[field] = _as_list(data[field]) + [value]
            else:
                data[field] = value
        try:
            from ..models import Work

            data.pop("iri", None)
            return Work.model_validate(data)
        except Exception:
            return data

    def fetch_lens_subgraph(
        self,
        review_id: Any,
        lens_name: str,
        filters: Mapping[str, Any] | None = None,
    ) -> Any:
        """Return review nodes/edges for the frontend lens, filtered in Python."""

        graph = review_graph(review_id)
        filters = filters or {}
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        for quad in self.store.quads_for_pattern(None, None, None, graph):
            subject = _value_from_term(quad.subject)
            predicate = quad.predicate.value
            obj = _value_from_term(quad.object)
            if isinstance(quad.object, (NamedNode, BlankNode)):
                object_iri = str(obj)
                edges.append({"source": subject, "target": object_iri, "type": predicate})
                nodes.setdefault(object_iri, {"iri": object_iri})
            else:
                nodes.setdefault(subject, {"iri": subject}).setdefault("properties", {})[
                    _field_from_predicate(predicate)
                ] = obj
            nodes.setdefault(subject, {"iri": subject})
        selected = []
        for node in nodes.values():
            properties = node.get("properties", {})
            if all(properties.get(key, node.get(key)) == value for key, value in filters.items()):
                node["lens"] = lens_name
                selected.append(node)
        selected_ids = {node["iri"] for node in selected}
        edges = [
            edge
            for edge in edges
            if edge["source"] in selected_ids or edge["target"] in selected_ids
        ]
        return _make_dto(SubgraphResult, nodes=selected, edges=edges)

    def _competency_file(self, number: int | str) -> Path:
        directory = self.ontology_root / "competency" / "sparql"
        if not directory.is_dir():
            raise FileNotFoundError(f"SPARQL competency-question directory is absent: {directory}")
        number_text = str(number).lower().replace("cq", "")
        try:
            number_text = f"{int(number_text):02d}"
        except ValueError:
            number_text = str(number).lower()
        candidates = sorted(directory.glob(f"cq{number_text}_*.rq"))
        if not candidates:
            candidates = sorted(directory.glob(f"cq{number_text}*.rq"))
        if not candidates:
            raise FileNotFoundError(
                f"No SPARQL competency question found for CQ{number_text}: {directory}"
            )
        return candidates[0]

    @staticmethod
    def _sparql_term(value: Any) -> str:
        value = _enum_value(value)
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float, Decimal)):
            return str(value)
        if isinstance(value, (list, tuple, set, frozenset)):
            return " ".join(OxigraphStore._sparql_term(item) for item in value)
        text = str(value)
        if text.startswith(("http://", "https://", "urn:")):
            return f"<{text}>"
        escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'

    @classmethod
    def _rewrite_param_block(cls, query: str, parameters: Mapping[str, Any]) -> str:
        blocks = list(
            re.finditer(
                r"# PARAM-BLOCK(?P<body>.*?)# /PARAM-BLOCK",
                query,
                flags=re.DOTALL,
            )
        )
        if not blocks:
            return query

        replacements: list[tuple[int, int, str]] = []
        for block in blocks:
            body = block.group("body")
            # Some CQs need parameters at two graph scopes.  Restrict each
            # replacement to the variables declared by that block so a
            # review partition remains outside GRAPH while a graph-local
            # threshold is bound where Oxigraph can compare it.
            declared = {
                variable.removeprefix("?")
                for values_clause in re.findall(r"VALUES\s+([^\{]+)\{", body)
                for variable in re.findall(r"\?[A-Za-z_][A-Za-z0-9_]*", values_clause)
            }
            selected = (
                parameters.items()
                if not declared
                else (
                    (key, value)
                    for key, value in parameters.items()
                    if re.sub(r"[^A-Za-z0-9_]", "_", str(key)) in declared
                )
            )
            lines = ["# PARAM-BLOCK"]
            for key, value in selected:
                values = _as_list(value)
                rendered = " ".join(cls._sparql_term(item) for item in values)
                variable = re.sub(r"[^A-Za-z0-9_]", "_", str(key))
                lines.append(f"VALUES ?{variable} {{ {rendered} }}")
            lines.append("# /PARAM-BLOCK")
            replacements.append((block.start(), block.end(), "\n".join(lines)))

        rewritten = query
        for start, end, replacement in reversed(replacements):
            rewritten = rewritten[:start] + replacement + rewritten[end:]
        return rewritten

    def run_competency_question(
        self, number: int | str, parameters: Mapping[str, Any] | None = None
    ) -> Any:
        """Run an ontology competency query after replacing its parameter block."""

        path = self._competency_file(number)
        query = self._rewrite_param_block(path.read_text(encoding="utf-8"), parameters or {})
        result = self.store.query(query)
        columns, rows = _query_rows(result)
        return _make_dto(QueryResult, columns=columns, rows=rows)

    def export_turtle(self, graphs: Sequence[Any] | Any | None = None) -> str:
        """Serialize selected graphs as valid Turtle.

        Turtle has no named-graph syntax, so multiple selected partitions are
        exported as their RDF union.  A single graph is delegated to Oxigraph's
        serializer; callers that need partition preservation should request one
        graph at a time (or use the store's native quad API).
        """

        if graphs is None:
            graph_names = list(self.store.named_graphs())
        elif isinstance(graphs, (str, NamedNode)):
            graph_names = [_graph_name(graphs)]
        else:
            graph_names = [_graph_name(item) for item in graphs]
        if len(graph_names) == 1:
            from io import BytesIO

            output = BytesIO()
            self.store.dump(output, RdfFormat.TURTLE, from_graph=graph_names[0])
            return output.getvalue().decode("utf-8")

        # Use rdflib only for the union case; Oxigraph remains the graph store.
        from rdflib import Graph, URIRef

        graph = Graph()
        for graph_name in graph_names:
            for quad in self.store.quads_for_pattern(None, None, None, graph_name):
                subject = (
                    URIRef(quad.subject.value) if isinstance(quad.subject, NamedNode) else None
                )
                if subject is None:
                    continue
                predicate = URIRef(quad.predicate.value)
                if isinstance(quad.object, NamedNode):
                    object_value: Any = URIRef(quad.object.value)
                elif isinstance(quad.object, BlankNode):
                    from rdflib import BNode

                    object_value = BNode(quad.object.value)
                else:
                    object_value = _rdflib_literal(quad.object)
                graph.add((subject, predicate, object_value))
        return graph.serialize(format="turtle")

    def validate(self, graphs: Sequence[Any] | Any | None = None) -> Any:
        """Validate an RDF export against ``ontology/shapes.ttl``."""

        data = self.export_turtle(graphs)
        shapes = self.ontology_root / "shapes.ttl"
        try:
            return validate_graph(data, shapes_path=shapes)
        except TypeError:
            return validate_graph(data, shapes)

    # Batched writes --------------------------------------------------------

    @contextmanager
    def batch(self) -> Iterator[OxigraphStore]:
        """Context manager for a group of idempotent writes.

        Oxigraph's set semantics already make individual writes atomic at the
        quad level; the context is a stable repository API and a hook for a
        future transaction implementation.
        """

        self._batch_depth += 1
        try:
            yield self
        finally:
            self._batch_depth -= 1
            if self._batch_depth == 0:
                self.store.flush()

    def batched_writes(self) -> Any:
        """Expose the backend batch transaction through the shared API."""

        return self.batch()

    transaction = batch

    def close(self) -> None:
        self.store.flush()


def _field_from_predicate(predicate: str) -> str:
    explicit = {
        f"{PTL_NS}openAlexId": "openalex_id",
        f"{PTL_NS}s2Id": "s2_id",
        f"{PTL_NS}fullTextAvailable": "full_text_available",
        f"{PTL_NS}globalCitationCount": "global_citation_count",
        f"{PTL_NS}retrievedAt": "retrieved_at",
    }
    if predicate in explicit:
        return explicit[predicate]
    reverse = {value: key for key, value in PREDICATE_OVERRIDES.items()}
    if predicate in reverse:
        return reverse[predicate]
    if predicate.startswith(PTL_NS):
        local = predicate[len(PTL_NS) :]
        return re.sub(r"(?<!^)(?=[A-Z])", "_", local).lower()
    return predicate.rsplit("/", 1)[-1].rsplit("#", 1)[-1]


def _rdflib_literal(term: Literal) -> Any:
    from rdflib import Literal as RDFLiteral

    datatype = term.datatype.value if term.datatype else None
    # Oxigraph stores RDF 1.1 strings with the xsd:string datatype, while the
    # ontology's closed-vocabulary sh:in lists use simple Turtle string
    # literals.  RDFLib keeps those spellings distinct for SHACL membership
    # checks, so preserve the plain-literal form at the export boundary.  The
    # Neo4j exporter already uses RDFLib's plain string constructor.
    if datatype == f"{XSD_NS}string":
        return RDFLiteral(str(term.value))
    return RDFLiteral(term.value, datatype=datatype)


OxigraphRepository = OxigraphStore

__all__ = [
    "PTL_NS",
    "PTLR_NS",
    "PTLG_NS",
    "BIBLIO_GRAPH",
    "CITO_NS",
    "DEFAULT_CONTENT_RUN",
    "OxigraphRepository",
    "OxigraphStore",
    "content_graph",
    "model_quads",
    "predicate_for_field",
    "review_graph",
]
