"""Lazy Neo4j binding for the store-neutral graph repository.

Neo4j is deliberately not contacted at import time (or merely by constructing
``Neo4jStore``).  The module also contains the pure mapping functions used by
the offline spike tests, including the reverse mapping from fake Neo4j records
to RDF Turtle.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD

try:
    from neo4j import GraphDatabase
    from neo4j.graph import Node as Neo4jNode
    from neo4j.graph import Relationship as Neo4jRelationship
    from neo4j.time import Date as Neo4jDate
    from neo4j.time import DateTime as Neo4jDateTime
except ImportError:  # pragma: no cover - dependency is pinned, kept import-safe for docs builds
    GraphDatabase = None  # type: ignore[assignment,misc]
    Neo4jNode = ()  # type: ignore[assignment,misc]
    Neo4jRelationship = ()  # type: ignore[assignment,misc]
    Neo4jDate = ()  # type: ignore[assignment,misc]
    Neo4jDateTime = ()  # type: ignore[assignment,misc]

try:
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


try:
    from .oxigraph_store import (
        CITO_NS,
        DCTERMS_NS,
        FABIO_NS,
        FOAF_NS,
        PROV_NS,
        PTL_NS,
        PTLG_NS,
        PTLR_NS,
        RDFS_NS,
        SKOS_NS,
        XSD_NS,
        _as_list,
        _class_name,
        _enum_value,
        _field_values,
        _iri,
    )
except ImportError:  # pragma: no cover
    PTL_NS = "https://w3id.org/portolan/ontology#"
    PTLR_NS = "https://w3id.org/portolan/id/"
    PTLG_NS = "https://w3id.org/portolan/graph/"
    CITO_NS = "http://purl.org/spar/cito/"
    DCTERMS_NS = "http://purl.org/dc/terms/"
    FOAF_NS = "http://xmlns.com/foaf/0.1/"
    FABIO_NS = "http://purl.org/spar/fabio/"
    PROV_NS = "http://www.w3.org/ns/prov#"
    RDFS_NS = "http://www.w3.org/2000/01/rdf-schema#"
    SKOS_NS = "http://www.w3.org/2004/02/skos/core#"
    XSD_NS = "http://www.w3.org/2001/XMLSchema#"

    def _as_list(value: Any) -> list[Any]:
        if isinstance(value, (list, tuple, set, frozenset)):
            return list(value)
        return [] if value is None else [value]

    def _class_name(value: Any) -> str:
        return type(value).__name__

    def _enum_value(value: Any) -> Any:
        return getattr(value, "value", value)

    def _field_values(value: Any) -> dict[str, Any]:
        dump = getattr(value, "model_dump", None)
        if callable(dump):
            try:
                return dict(dump(mode="python", exclude_none=True))
            except TypeError:
                return dict(dump(exclude_none=True))
        return dict(vars(value)) if hasattr(value, "__dict__") else {}

    def _iri(value: Any) -> str:
        if isinstance(value, Mapping) and "iri" in value:
            return str(value["iri"])
        return str(getattr(value, "iri", value))


PTL = Namespace(PTL_NS)
PTLR = Namespace(PTLR_NS)
CITO = Namespace(CITO_NS)

RELATIONSHIP_OVERRIDES: dict[str, str] = {
    "authored_by": "AUTHORED_BY",
    "affiliated_with": "AFFILIATED_WITH",
    "published_in": "PUBLISHED_IN",
    "published_by": "PUBLISHED_BY",
    "has_version": "HAS_VERSION",
    "citing_work": "CITES",
    "cited_work": "CITES",
    "has_evidence": "HAS_EVIDENCE",
    "was_generated_by": "WAS_GENERATED_BY",
    "from_work": "FROM_WORK",
    "of_work": "OF_WORK",
    "of_method": "OF_METHOD",
    "on_dataset": "ON_DATASET",
    "with_metric": "WITH_METRIC",
    "addresses": "ADDRESSES",
    "proposes": "PROPOSES",
    "uses": "USES",
    "evaluates_on": "EVALUATES_ON",
    "reports": "REPORTS",
    "addresses_limitation": "ADDRESSES_LIMITATION",
    "supports_claim": "SUPPORTS_CLAIM",
    "contradicts_claim": "CONTRADICTS_CLAIM",
    "limitation_of": "LIMITATION_OF",
    "broader": "BROADER",
    "narrower": "NARROWER",
    "extends_method": "EXTENDS_METHOD",
    "first_seen_in": "FIRST_SEEN_IN",
    "exact_match": "EXACT_MATCH",
    "close_match": "CLOSE_MATCH",
    "of_review": "OF_REVIEW",
    "in_cluster": "IN_CLUSTER",
    "parent_cluster": "PARENT_CLUSTER",
    "top_concept": "TOP_CONCEPT",
    "supported_by": "SUPPORTED_BY",
    "has_protocol": "HAS_PROTOCOL",
}

OBJECT_FIELDS = set(RELATIONSHIP_OVERRIDES) | {
    "about",
    "review",
    "work",
    "concept",
}
PARTITION_CONTENT = {
    "Concept",
    "Contribution",
    "Result",
    "Claim",
    "Limitation",
    "FutureWork",
    "Evidence",
}
PARTITION_REVIEW = {"Review", "ReviewProtocol", "Inclusion", "Cluster", "GapHypothesis"}
LABEL_OVERRIDES = {"ExtractionRun": "Activity"}
CONCEPT_LABELS = {"Problem", "Method", "Dataset", "Metric"}
EXPORT_INHERENT_LOSSES = frozenset({"named_graph_boundaries", "xsd_decimal_to_float"})
PROPERTY_NAME_OVERRIDES = {"claims_sota": "claimsSOTA"}


def _is_neo4j_node(value: Any) -> bool:
    return isinstance(value, Neo4jNode)


def _is_neo4j_relationship(value: Any) -> bool:
    return isinstance(value, Neo4jRelationship)


def camel_case(name: str) -> str:
    """Map a snake_case domain property to the contract's camelCase key."""

    if not name or name.isupper() or (name[0].islower() and "_" not in name):
        return name
    parts = name.split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def label_for_model(model_or_name: Any) -> str:
    """Return the exact PascalCase LPG label for a domain class."""

    name = model_or_name if isinstance(model_or_name, str) else _class_name(model_or_name)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", name):
        raise ValueError(f"Invalid Neo4j label: {name!r}")
    return LABEL_OVERRIDES.get(name, name)


def _labels_for_model(model_or_name: Any) -> tuple[str, ...]:
    """Return the binding labels, including labels shared by a model family."""

    name = model_or_name if isinstance(model_or_name, str) else _class_name(model_or_name)
    primary = label_for_model(name)
    labels = [primary]
    if name in CONCEPT_LABELS:
        labels.append("Concept")
    return tuple(dict.fromkeys(labels))


def relationship_type(field_name: str) -> str:
    """Return the exact SCREAMING_SNAKE relationship type."""

    if field_name in RELATIONSHIP_OVERRIDES:
        return RELATIONSHIP_OVERRIDES[field_name]
    if field_name.isupper():
        return field_name
    return re.sub(r"[^A-Za-z0-9]+", "_", field_name).upper()


def property_name(field_name: str) -> str:
    """Return the exact camelCase LPG property key."""

    if field_name in PROPERTY_NAME_OVERRIDES:
        return PROPERTY_NAME_OVERRIDES[field_name]
    return camel_case(field_name)


def _node_iri(node: Any) -> str:
    properties = _node_properties(node)
    if properties.get("iri") is not None:
        return str(properties["iri"])
    element_id = getattr(node, "element_id", None)
    if element_id is not None:
        return f"urn:neo4j:node:{element_id}"
    identity = getattr(node, "id", None)
    if identity is not None:
        return f"urn:neo4j:node:{identity}"
    if isinstance(node, Mapping):
        for key in ("element_id", "id"):
            if node.get(key) is not None:
                return f"urn:neo4j:node:{node[key]}"
    return _iri(node)


def _node_properties(node: Any) -> dict[str, Any]:
    if node is None:
        return {}
    if _is_neo4j_node(node):
        return dict(node.items())
    if isinstance(node, Mapping):
        if isinstance(node.get("properties"), Mapping):
            return dict(node["properties"])
        return {
            key: value
            for key, value in node.items()
            if key not in {"labels", "label", "type", "properties", "id", "element_id"}
        }
    items = getattr(node, "items", None)
    if callable(items):
        try:
            return dict(items())
        except TypeError:
            pass
    return {}


def _node_labels(node: Any) -> set[str]:
    # A driver Node is Mapping-like over its PROPERTIES, and its labels live on the
    # attribute, not in the property map — so the attribute has to be consulted first
    # or every exported node comes out untyped.
    if _is_neo4j_node(node):
        labels = node.labels
    else:
        attribute_labels = getattr(node, "labels", None)
        if attribute_labels is not None and not isinstance(attribute_labels, (str, bytes)):
            labels = attribute_labels
        elif isinstance(node, Mapping):
            labels = node.get("labels", node.get("label", []))
        else:
            labels = attribute_labels or []
    if isinstance(labels, str):
        return {labels}
    try:
        return {str(item) for item in labels}
    except TypeError:
        return set()


def _record_get(record: Any, key: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(key, default)
    getter = getattr(record, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            try:
                return getter(key)
            except (KeyError, IndexError):
                return default
    try:
        return record[key]
    except (KeyError, IndexError, TypeError):
        return default


def _record_keys(record: Any) -> list[str]:
    # Order matters: a real neo4j Record satisfies isinstance(x, Mapping) but ITERATES
    # ITS VALUES, not its field names, so the Mapping branch silently returned Node
    # objects as if they were keys and every lookup missed. Ask for keys() first and
    # fall back to Mapping iteration only for the plain dicts used in offline tests.
    keys = getattr(record, "keys", None)
    if callable(keys):
        return [str(key) for key in keys()]
    if isinstance(record, Mapping):
        return list(record)
    return []


def _relationship_type(rel: Any) -> str:
    if _is_neo4j_relationship(rel):
        return str(rel.type)
    if isinstance(rel, Mapping):
        return str(rel.get("type", rel.get("rel_type", "RELATES_TO")))
    value = getattr(rel, "type", None)
    return str(value() if callable(value) else value or "RELATES_TO")


def _relationship_properties(rel: Any) -> dict[str, Any]:
    if rel is None:
        return {}
    if _is_neo4j_relationship(rel):
        return dict(rel.items())
    if isinstance(rel, Mapping):
        if isinstance(rel.get("properties"), Mapping):
            return dict(rel["properties"])
        return {
            key: value
            for key, value in rel.items()
            if key not in {"type", "rel_type", "start", "end", "start_node", "end_node", "nodes"}
        }
    items = getattr(rel, "items", None)
    if callable(items):
        try:
            return dict(items())
        except TypeError:
            pass
    return {}


def _relationship_endpoints(rel: Any) -> tuple[Any, Any]:
    if _is_neo4j_relationship(rel):
        return rel.start_node, rel.end_node
    if isinstance(rel, Mapping):
        start = rel.get("start_node", rel.get("start"))
        end = rel.get("end_node", rel.get("end"))
        if start is None and isinstance(rel.get("nodes"), Sequence):
            start, end = rel["nodes"][:2]
        return start, end
    start = getattr(rel, "start_node", None)
    end = getattr(rel, "end_node", None)
    if start is None or end is None:
        nodes = getattr(rel, "nodes", None)
        if nodes is not None:
            start, end = nodes
    return start, end


def build_node_upsert(label: str, *, iri_parameter: str = "iri") -> str:
    """Build a parameterized, injection-safe node upsert statement."""

    labels = _labels_for_model(label)
    label_set = ", ".join(f"n:{item}" for item in labels)
    return (
        f"MERGE (n {{{iri_parameter}: ${iri_parameter}}})\n"
        f"SET {label_set}, n.iri = ${iri_parameter}, n += $props\n"
        "RETURN n"
    )


def build_relationship_merge(rel_type: str) -> str:
    """Build a parameterized relationship merge statement."""

    rel_type = relationship_type(rel_type)
    return (
        "MERGE (a {iri: $from_iri})\n"
        "MERGE (b {iri: $to_iri})\n"
        f"MERGE (a)-[r:{rel_type}]->(b)\n"
        "SET r += $rel_props\n"
        "RETURN r"
    )


def _literal(value: Any, key: str | None = None) -> Literal:
    value = _enum_value(value)
    if isinstance(value, Literal):
        return value
    if isinstance(value, Neo4jDateTime):
        return Literal(value.isoformat(), datatype=XSD.dateTime)
    if isinstance(value, Neo4jDate):
        return Literal(value.isoformat(), datatype=XSD.date)
    if isinstance(value, bool):
        return Literal(value, datatype=XSD.boolean)
    if isinstance(value, int) and not isinstance(value, bool):
        return Literal(value, datatype=XSD.integer)
    if isinstance(value, (float, Decimal)):
        return Literal(str(value), datatype=XSD.decimal)
    if isinstance(value, datetime):
        return Literal(value.isoformat(), datatype=XSD.dateTime)
    if isinstance(value, date):
        return Literal(value.isoformat(), datatype=XSD.date)
    if isinstance(value, str) and re.fullmatch(r"\d{4}", value):
        return Literal(value, datatype=XSD.gYear)
    return Literal(str(value))


def _predicate_for_property(key: str) -> URIRef:
    special = {
        "title": DCTERMS_NS + "title",
        "abstract": DCTERMS_NS + "abstract",
        "issued": DCTERMS_NS + "issued",
        "label": RDFS_NS + "label",
        "altLabel": SKOS_NS + "altLabel",
        "prefLabel": SKOS_NS + "prefLabel",
        # ``claimsSota`` was written by an earlier binding version.  Read it as
        # the ontology's exact acronym spelling while new writes use claimsSOTA.
        "claimsSota": PTL_NS + "claimsSOTA",
        "claimsSOTA": PTL_NS + "claimsSOTA",
        "endedAtTime": PROV_NS + "endedAtTime",
    }
    return URIRef(special.get(key, PTL_NS + key))


def _property_object(key: str, value: Any) -> URIRef | BNode | Literal:
    if key in {"exactMatch", "closeMatch"} and isinstance(value, str):
        return URIRef(value)
    if (
        key
        in {
            "firstSeenIn",
            "ofWork",
            "ofMethod",
            "onDataset",
            "withMetric",
            "about",
            "topConcept",
            "ofReview",
            "inCluster",
            "parentCluster",
            "hasProtocol",
            "supportedBy",
            "hasEvidence",
            "wasGeneratedBy",
            "citingWork",
            "citedWork",
            "publishedIn",
            "publishedBy",
        }
        and isinstance(value, str)
        and value.startswith(("http://", "https://", "urn:"))
    ):
        return URIRef(value)
    return _literal(value, key)


def _node_to_graph(graph: Graph, node: Any) -> str:
    iri = _node_iri(node)
    subject = URIRef(iri)
    labels = _node_labels(node)
    alignments = {
        "Work": FABIO_NS + "Work",
        "Author": FOAF_NS + "Person",
        "Organization": FOAF_NS + "Organization",
        "Venue": FABIO_NS + "Journal",
        "Evidence": PROV_NS + "Entity",
        "ExtractionRun": PROV_NS + "Activity",
        "Activity": PROV_NS + "Activity",
    }
    specific_concept_labels = labels & CONCEPT_LABELS
    for label in labels:
        if label == "Concept" and specific_concept_labels:
            # :Concept is a shared LPG query label.  Specific concept labels
            # carry the direct RDF type; the Oxigraph binding does not emit an
            # additional skos:Concept instance triple for that shared label.
            continue
        type_iri = {
            "Concept": SKOS_NS + "Concept",
            "Activity": PROV_NS + "Activity",
            "ExtractionRun": PTL_NS + "ExtractionRun",
        }.get(label, f"{PTL_NS}{label}")
        graph.add((subject, RDF.type, URIRef(type_iri)))
        if label in alignments:
            graph.add((subject, RDF.type, URIRef(alignments[label])))
        if label in {"Problem", "Method", "Dataset", "Metric"}:
            graph.add(
                (
                    URIRef(f"{PTL_NS}{label}"),
                    URIRef(RDFS_NS + "subClassOf"),
                    URIRef(SKOS_NS + "Concept"),
                )
            )
    if "Activity" in labels:
        # ExtractionRun is the domain type; Activity is its LPG/PROV alignment.
        graph.add((subject, RDF.type, URIRef(f"{PTL_NS}ExtractionRun")))
    for key, raw_value in _node_properties(node).items():
        if key in {
            "iri",
            "reviewId",
            "extractionRun",
            "slug",
            "number",
            "clusterNumber",
            "gapNumber",
        }:
            continue
        predicate = _predicate_for_property(key)
        for value in _as_list(raw_value):
            graph.add((subject, predicate, _property_object(key, value)))
    return iri


def _citation_iri(citing: str, cited: str) -> URIRef:
    digest = hashlib.sha1((citing + cited).encode("utf-8"), usedforsecurity=False).hexdigest()
    return PTLR[f"citation/{digest}"]


def _review_partition_key(value: Any) -> str:
    """Return the bare review id used by the LPG partition properties."""

    text = str(_enum_value(value))
    prefix = f"{PTLR_NS}review/"
    if text.startswith(prefix):
        return unquote(text.removeprefix(prefix))
    return text


def _review_partition_iri(value: Any) -> str:
    """Expand a bare LPG review partition key to the review resource IRI."""

    return f"{PTLR_NS}review/{quote(_review_partition_key(value), safe='')}"


def _cluster_pair_iri(review_id: Any, cluster_a: str, cluster_b: str) -> URIRef:
    review_text = str(_enum_value(review_id))
    review_prefix = f"{PTLR_NS}review/"
    if review_text.startswith(review_prefix):
        review_text = unquote(review_text.removeprefix(review_prefix))
    return URIRef(
        f"{PTLR_NS}cluster-pair/"
        f"{quote(review_text, safe='')}/"
        f"{quote(cluster_a, safe='')}/"
        f"{quote(cluster_b, safe='')}"
    )


def _relationship_to_graph(graph: Graph, rel: Any) -> None:
    start, end = _relationship_endpoints(rel)
    if start is None or end is None:
        return
    citing = _node_iri(start)
    cited = _node_iri(end)
    subject = URIRef(citing)
    object_iri = URIRef(cited)
    rel_type = _relationship_type(rel)
    properties = _relationship_properties(rel)
    if rel_type == "CITES":
        graph.add((subject, CITO.cites, object_iri))
        citation = _citation_iri(citing, cited)
        graph.add((citation, RDF.type, PTL.Citation))
        graph.add((citation, RDF.type, CITO.Citation))
        graph.add((citation, PTL.citingWork, subject))
        graph.add((citation, PTL.citedWork, object_iri))
        for key, value in properties.items():
            if key == "citationFunction":
                function = str(_enum_value(value))
                function_iri = (
                    function
                    if function.startswith(("http://", "https://"))
                    else CITO_NS + function.removeprefix("cito:")
                )
                graph.add((citation, PTL.citationFunction, URIRef(function_iri)))
            elif key == "isInfluential":
                graph.add((citation, PTL.isInfluential, _literal(value)))
            elif key == "citationContext":
                graph.add((citation, PTL.citationContext, _literal(value)))
        return
    if rel_type == "CLUSTER_PAIR":
        review_id = properties.get("reviewId")
        if review_id is None:
            return
        pair = _cluster_pair_iri(review_id, citing, cited)
        graph.add((pair, RDF.type, PTL.ClusterPair))
        graph.add((pair, PTL.ofReview, URIRef(_review_partition_iri(review_id))))
        graph.add((pair, PTL.clusterA, subject))
        graph.add((pair, PTL.clusterB, object_iri))
        if properties.get("semanticSimilarity") is not None:
            graph.add((pair, PTL.semanticSimilarity, _literal(properties["semanticSimilarity"])))
        if properties.get("crossCitationCount") is not None:
            graph.add((pair, PTL.crossCitationCount, _literal(properties["crossCitationCount"])))
        return
    predicate_local = re.sub(r"_+", "_", rel_type).lower()
    predicate_local = re.sub(r"_([a-z])", lambda match: match.group(1).upper(), predicate_local)
    namespace = (
        SKOS_NS
        if rel_type in {"BROADER", "NARROWER", "EXACT_MATCH", "CLOSE_MATCH"}
        else PROV_NS
        if rel_type == "WAS_GENERATED_BY"
        else PTL_NS
    )
    graph.add((subject, URIRef(namespace + predicate_local), object_iri))


def records_to_turtle(records: Iterable[Any]) -> str:
    """Map fake or real Neo4j node/relationship records back to Turtle."""

    graph = Graph()
    graph.bind("ptl", PTL)
    graph.bind("ptlr", PTLR)
    graph.bind("ptlg", Namespace(PTLG_NS))
    graph.bind("cito", CITO)
    graph.bind("dcterms", Namespace(DCTERMS_NS))
    graph.bind("skos", Namespace(SKOS_NS))
    graph.bind("prov", Namespace(PROV_NS))
    seen_nodes: set[str] = set()
    relationships: list[Any] = []
    for record in records:
        values = {key: _record_get(record, key) for key in _record_keys(record)}
        rel = values.get("r", values.get("relationship"))
        if (
            rel is None
            and "type" in values
            and (values.get("start") is not None or values.get("start_node") is not None)
        ):
            rel = record
        if rel is not None:
            relationships.append(rel)
            for endpoint_key in ("a", "b", "start", "end", "start_node", "end_node"):
                endpoint = values.get(endpoint_key)
                if endpoint is not None:
                    iri = _node_to_graph(graph, endpoint)
                    seen_nodes.add(iri)
            continue
        node = values.get("n", values.get("node"))
        if node is None and values and not any(key in values for key in ("r", "relationship")):
            # A bare Node-like object may be wrapped as a record under its first key.
            if (
                (hasattr(record, "labels") and hasattr(record, "items"))
                or "iri" in values
                and ("labels" in values or "properties" in values)
            ):
                node = record
            else:
                node = next(iter(values.values())) if len(values) == 1 else None
        if node is not None:
            iri = _node_to_graph(graph, node)
            seen_nodes.add(iri)
    for rel in relationships:
        _relationship_to_graph(graph, rel)
    return graph.serialize(format="turtle")


def _bolt_value(value: Any) -> Any:
    """Narrow a model value to something the Bolt protocol can carry.

    Bolt has no decimal type, so every ``xsd:decimal`` in the contract (confidence,
    relevance, similarity, the six frontier components) has to be narrowed to a float on
    the way into Neo4j.  The RDF binding stores these losslessly; the property-graph
    binding cannot.  At the magnitudes used here the difference is invisible, but it is a
    real fidelity asymmetry between the two stores and is recorded as such in ADR-0005.
    """

    if isinstance(value, Decimal):
        return float(value)
    return value


def _extraction_partition_iri(value: Any) -> str:
    """Return the canonical extraction Activity IRI used as a partition value."""

    text = str(_enum_value(value))
    prefix = f"{PTLR_NS}extraction/"
    if text.startswith(prefix):
        return text
    return f"{prefix}{quote(text, safe='')}"


def _model_properties(
    model: Any,
    partition: Mapping[str, Any] | None = None,
) -> tuple[str, dict[str, Any], list[tuple[str, Any]]]:
    iri = _iri(model)
    values = _field_values(model)
    props: dict[str, Any] = {}
    relationships: list[tuple[str, Any]] = []
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
    for field_name, raw_value in values.items():
        if field_name in identity_fields or raw_value is None or field_name.startswith("_"):
            continue
        raw_values = _as_list(raw_value)
        for value in raw_values:
            value = _enum_value(value)
            if (
                field_name in OBJECT_FIELDS
                or hasattr(value, "iri")
                or (isinstance(value, Mapping) and "iri" in value)
            ):
                relationships.append((field_name, value))
            elif field_name == "citation_function":
                # Citation functions are relationship properties only for CITES;
                # retaining a node property is still useful for direct model upserts.
                props[property_name(field_name)] = (
                    str(value).removeprefix(CITO_NS).removeprefix("cito:")
                )
            elif isinstance(raw_value, (list, tuple, set, frozenset)):
                converted = [_bolt_value(_enum_value(item)) for item in raw_values]
                props[property_name(field_name)] = converted
                break
            elif isinstance(value, dict):
                props[property_name(field_name)] = json.dumps(value, default=str, sort_keys=True)
            else:
                props[property_name(field_name)] = _bolt_value(value)
    if partition:
        props.update({key: _bolt_value(item) for key, item in partition.items()})
    return iri, props, relationships


def _field_from_lpg_property(name: str) -> str:
    explicit = {
        "claimsSOTA": "claims_sota",
        "claimsSota": "claims_sota",
        "openAlexId": "openalex_id",
        "s2Id": "s2_id",
        "fullTextAvailable": "full_text_available",
        "globalCitationCount": "global_citation_count",
        "retrievedAt": "retrieved_at",
    }
    if name in explicit:
        return explicit[name]
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _looks_like_records(value: Any) -> bool:
    if isinstance(value, (str, bytes, Path, Mapping)):
        return isinstance(value, Mapping) and any(
            key in value for key in ("n", "r", "node", "relationship")
        )
    try:
        first = next(iter(value))
    except (TypeError, StopIteration):
        return False
    return bool(_record_keys(first))


def _partition_selector(graphs: Any) -> tuple[str, dict[str, list[str]]] | None:
    """Build a safe Cypher partition predicate for requested RDF graph IRIs."""

    if graphs is None:
        return None
    if hasattr(graphs, "value"):
        values = [graphs.value]
    else:
        values = [graphs] if isinstance(graphs, (str, bytes)) else list(graphs)
    global_graph = False
    content_runs: list[str] = []
    review_ids: list[str] = []
    for value in values:
        text = str(getattr(value, "value", value))
        if text in {"biblio", f"{PTLG_NS}biblio"}:
            global_graph = True
        elif text.startswith(f"{PTLG_NS}content/"):
            content_runs.append(
                _extraction_partition_iri(unquote(text.removeprefix(f"{PTLG_NS}content/")))
            )
        elif text.startswith(f"{PTLG_NS}review/"):
            review_ids.append(unquote(text.removeprefix(f"{PTLG_NS}review/")))
        else:
            raise ValueError(f"unknown PTL graph partition: {text!r}")
    conditions: list[str] = []
    if global_graph:
        conditions.append("({alias}.reviewId IS NULL AND {alias}.extractionRun IS NULL)")
    if content_runs:
        conditions.append("{alias}.extractionRun IN $contentRuns")
    if review_ids:
        conditions.append("{alias}.reviewId IN $reviewIds")
    return " OR ".join(conditions) or "false", {
        "contentRuns": content_runs,
        "reviewIds": review_ids,
    }


class Neo4jStore(GraphRepository):
    """Lazy Neo4j implementation of ``GraphRepository``."""

    def __init__(
        self,
        uri: str | None = None,
        user: str | None = None,
        password: str | None = None,
        *,
        driver: Any | None = None,
        database: str | None = None,
        auth: tuple[str, str] | None = None,
        ontology_root: str | Path | None = None,
    ) -> None:
        self.uri = uri or os.getenv("NEO4J_URI")
        self.user = user or os.getenv("NEO4J_USER", "neo4j")
        self.password = password or os.getenv("NEO4J_PASSWORD")
        self.auth = auth or ((self.user, self.password) if self.password is not None else None)
        self.database = database or os.getenv("NEO4J_DATABASE")
        self._driver = driver
        self._batch_session: Any | None = None
        self._batch_tx: Any | None = None
        root = (
            Path(ontology_root)
            if ontology_root is not None
            else Path(__file__).resolve().parents[3]
        )
        self.ontology_root = root if root.name == "ontology" else root / "ontology"

    def _get_driver(self) -> Any:
        if self._driver is not None:
            return self._driver
        if not self.uri:
            raise RuntimeError("Neo4j is not configured; pass uri or set NEO4J_URI")
        if GraphDatabase is None:
            raise RuntimeError("neo4j driver is not installed")
        kwargs: dict[str, Any] = {}
        if self.auth is not None:
            kwargs["auth"] = self.auth
        self._driver = GraphDatabase.driver(self.uri, **kwargs)
        return self._driver

    def _session(self) -> Any:
        driver = self._get_driver()
        if self.database:
            try:
                return driver.session(database=self.database)
            except TypeError:
                return driver.session()
        return driver.session()

    def _run(self, query: str, **params: Any) -> list[Any]:
        if self._batch_tx is not None:
            result = self._batch_tx.run(query, **params)
            return list(result) if hasattr(result, "__iter__") else []
        session = self._session()
        try:
            result = session.run(query, **params)
            return list(result) if hasattr(result, "__iter__") else []
        finally:
            close = getattr(session, "close", None)
            if callable(close):
                close()

    def _write_model(
        self,
        model: Any,
        partition: Mapping[str, Any] | None = None,
        relationship_properties: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> Any:
        iri, props, relations = _model_properties(model, partition)
        label = label_for_model(model)
        self._run(build_node_upsert(label), iri=iri, props=props)
        for field_name, value in relations:
            rel_props = dict((relationship_properties or {}).get(field_name, {}))
            if field_name == "addresses_limitation" and partition:
                review_id = partition.get("reviewId")
                if review_id is not None:
                    rel_props["reviewId"] = _review_partition_key(review_id)
            for target in _as_list(value):
                target_iri = _iri(target)
                if not target_iri:
                    continue
                self._run(
                    build_relationship_merge(relationship_type(field_name)),
                    from_iri=iri,
                    to_iri=target_iri,
                    rel_props=rel_props,
                )
        return model

    def _write_relationship(
        self,
        source: Any,
        target: Any,
        rel_type: str,
        rel_props: Mapping[str, Any] | None = None,
    ) -> None:
        self._run(
            build_relationship_merge(rel_type),
            from_iri=_iri(source),
            to_iri=_iri(target),
            rel_props={key: _bolt_value(value) for key, value in (rel_props or {}).items()},
        )

    @staticmethod
    def _content_run_id(value: Any, explicit: Any | None = None) -> str:
        if explicit is not None:
            value = explicit
        values = _field_values(value)
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
        raw_id = getattr(value, "run_id", None)
        if raw_id is not None:
            return str(raw_id)
        if isinstance(value, Mapping) and value.get("run_id") is not None:
            return str(value["run_id"])
        text = _iri(value)
        prefix = f"{PTLR_NS}extraction/"
        if text.startswith(prefix):
            return unquote(text.removeprefix(prefix))
        if explicit is not None:
            return text
        return "default"

    @staticmethod
    def _review_id(value: Any, explicit: Any | None = None) -> str:
        if explicit is not None:
            value = explicit
        values = _field_values(value)
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
        raw_id = getattr(value, "review_id", None)
        if raw_id is not None:
            return str(raw_id)
        if isinstance(value, Mapping) and value.get("review_id") is not None:
            return str(value["review_id"])
        text = _iri(value)
        prefix = f"{PTLR_NS}review/"
        if text.startswith(prefix):
            return unquote(text.removeprefix(prefix))
        if explicit is not None:
            return text
        raise ValueError(f"{_class_name(value)} requires a review_id for review-scoped storage")

    # Layer 1 ---------------------------------------------------------------

    def upsert_work(self, work: Any) -> Any:
        """Write a global Work node."""

        return self._write_model(work)

    def upsert_author(self, author: Any) -> Any:
        """Write a global Author node."""

        return self._write_model(author)

    def upsert_organization(self, organization: Any) -> Any:
        """Write a global Organization node."""

        return self._write_model(organization)

    def upsert_venue(self, venue: Any) -> Any:
        """Write a global Venue node."""

        return self._write_model(venue)

    def add_citation(self, citation: Any) -> Any:
        """Collapse a Citation into a ``:CITES`` relationship with its properties."""

        values = _field_values(citation)
        citing = values.get("citing_work") or values.get("citing")
        cited = values.get("cited_work") or values.get("cited")
        if citing is None or cited is None:
            raise ValueError("Citation requires citing_work and cited_work")
        rel_props: dict[str, Any] = {}
        for source, target in (
            ("citation_function", "citationFunction"),
            ("is_influential", "isInfluential"),
            ("citation_context", "citationContext"),
        ):
            if values.get(source) is not None:
                value = _enum_value(values[source])
                rel_props[target] = (
                    str(value).removeprefix(CITO_NS).removeprefix("cito:")
                    if source == "citation_function"
                    else value
                )
        self._write_relationship(citing, cited, "CITES", rel_props)
        return citation

    def upsert_concept(self, concept: Any, run_id: Any | None = None, **kwargs: Any) -> Any:
        """Write a content Concept with its extraction partition."""

        run_id = kwargs.get("extraction_run", kwargs.get("extraction_run_id", run_id))
        return self._write_model(
            concept,
            {"extractionRun": _extraction_partition_iri(self._content_run_id(concept, run_id))},
        )

    # Layer 2 ---------------------------------------------------------------

    @staticmethod
    def _parse_extraction_args(
        args: Sequence[Any],
        kwargs: Mapping[str, Any],
    ) -> tuple[Any, list[Any], list[Any], Any | None]:
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
            run = positional[0] if run is None else run
            if statements is None and len(positional) > 1:
                statements = positional[1]
            if evidence is None and len(positional) > 2:
                evidence = positional[2]
            if work is None and len(positional) > 3:
                work = positional[3]
        else:
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
                and evidence
                and not any(
                    _iri(item) == _iri(candidate)
                    for item in _as_list(own_evidence)
                    for candidate in evidence
                )
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
        for statement in statements:
            values = _field_values(statement)
            own = values.get("evidence") or values.get("has_evidence") or values.get("evidences")
            if own:
                own_iris = {_iri(item) for item in _as_list(own)}
                pairs.extend((statement, item) for item in evidence if _iri(item) in own_iris)
        if len(statements) == 1 and not pairs:
            pairs.extend((statements[0], item) for item in evidence)
        return pairs

    def write_extraction(self, *args: Any, **kwargs: Any) -> Any:
        """Write content entities, Evidence, and extraction provenance."""

        run, statements, evidence, work = self._parse_extraction_args(args, kwargs)
        analysis_review_id = kwargs.get("review_id", kwargs.get("review"))
        relationship_properties = (
            {"addresses_limitation": {"reviewId": _review_partition_key(analysis_review_id)}}
            if analysis_review_id is not None
            else None
        )
        known_evidence = {_iri(item) for item in evidence}
        for statement in statements:
            values = _field_values(statement)
            own = values.get("has_evidence") or values.get("evidence")
            for item in _as_list(own):
                if _iri(item) not in known_evidence:
                    evidence.append(item)
                    known_evidence.add(_iri(item))
        self._validate_extraction_batch(work, statements, evidence)
        run_id = self._content_run_id(run)
        run_iri = (
            _iri(run)
            if hasattr(run, "iri") or (isinstance(run, Mapping) and "iri" in run)
            else _extraction_partition_iri(run_id)
        )
        if work is not None:
            self.upsert_work(work)
        if hasattr(run, "iri") or isinstance(run, Mapping):
            self._write_model(run)
        else:
            self._run(
                build_node_upsert("ExtractionRun"),
                iri=run_iri,
                props={},
            )
        for statement in statements:
            self._write_model(
                statement,
                {"extractionRun": _extraction_partition_iri(run_iri)},
                relationship_properties,
            )
            self._write_relationship(statement, run_iri, "WAS_GENERATED_BY")
        for item in evidence:
            self._write_model(
                item,
                {"extractionRun": _extraction_partition_iri(run_iri)},
            )
            self._write_relationship(item, run_iri, "WAS_GENERATED_BY")
        for statement, item in self._evidence_pairs(statements, evidence):
            self._write_relationship(statement, item, "HAS_EVIDENCE")
        if work is not None:
            for statement in statements:
                self._write_relationship(statement, work, "OF_WORK")
        return run

    # Layer 3 ---------------------------------------------------------------

    def write_review(self, review: Any, protocol: Any | None = None, **kwargs: Any) -> Any:
        """Write a Review and optional ReviewProtocol with ``reviewId``."""

        review_id = kwargs.get("review_id", self._review_id(review, kwargs.get("review")))
        protocol_to_write = protocol
        if protocol_to_write is None:
            candidate = _field_values(review).get("has_protocol")
            if candidate is not None and not isinstance(candidate, (str, Mapping)):
                protocol_to_write = candidate
        if protocol_to_write is not None:
            self.write_protocol(protocol_to_write, review_id=review_id)
        self._write_model(review, {"reviewId": review_id})
        return review

    def write_protocol(self, protocol: Any, review_id: Any | None = None, **kwargs: Any) -> Any:
        """Write a ReviewProtocol in a review partition."""

        rid = self._review_id(protocol, kwargs.get("review_id", review_id))
        return self._write_model(protocol, {"reviewId": rid})

    def write_inclusion(self, inclusion: Any, review_id: Any | None = None, **kwargs: Any) -> Any:
        """Write an Inclusion in a review partition."""

        rid = self._review_id(inclusion, kwargs.get("review_id", review_id))
        return self._write_model(inclusion, {"reviewId": rid})

    def write_cluster(self, cluster: Any, review_id: Any | None = None, **kwargs: Any) -> Any:
        """Write a Cluster in a review partition."""

        rid = self._review_id(cluster, kwargs.get("review_id", review_id))
        return self._write_model(cluster, {"reviewId": rid})

    def write_cluster_pair(self, pair: Any, review_id: Any | None = None, **kwargs: Any) -> Any:
        """Write a ClusterPair as a ``:CLUSTER_PAIR`` relationship.

        The LPG binding (``ontology/lpg-binding.md``) collapses the RDF
        intermediate node onto the relationship, so the scalars travel as
        relationship properties and CQ11's Cypher matches the pattern directly.
        """

        rid = self._review_id(pair, kwargs.get("review_id", review_id))
        values = _field_values(pair)
        self._write_relationship(
            values.get("cluster_a"),
            values.get("cluster_b"),
            "CLUSTER_PAIR",
            {
                "reviewId": rid,
                "semanticSimilarity": float(values.get("semantic_similarity")),
                "crossCitationCount": int(values.get("cross_citation_count")),
            },
        )
        return pair

    def write_gap_hypothesis(self, gap: Any, review_id: Any | None = None, **kwargs: Any) -> Any:
        """Write a GapHypothesis in a review partition."""

        rid = self._review_id(gap, kwargs.get("review_id", review_id))
        return self._write_model(gap, {"reviewId": rid})

    def update_gap_status(
        self,
        gap: Any,
        status: Any | None = None,
        note: str | None = None,
        review_id: Any | None = None,
        **kwargs: Any,
    ) -> Any:
        """Update only user-controlled GapHypothesis status fields."""

        values = _field_values(gap) if not isinstance(gap, str) else {}
        rid = (
            review_id
            or kwargs.get("review_id")
            or values.get("review_id")
            or values.get("of_review")
        )
        rid = self._review_id(gap, rid)
        status = status if status is not None else kwargs.get("user_status")
        note = note if note is not None else kwargs.get("user_note")
        set_parts: list[str] = []
        params: dict[str, Any] = {"iri": _iri(gap), "reviewId": rid}
        if status is not None:
            set_parts.append("n.userStatus = $userStatus")
            params["userStatus"] = str(_enum_value(status))
        if note is not None:
            set_parts.append("n.userNote = $userNote")
            params["userNote"] = note
        if not set_parts:
            return gap
        self._run(
            "MATCH (n:GapHypothesis {iri: $iri, reviewId: $reviewId})\nSET "
            + ", ".join(set_parts)
            + "\nRETURN n",
            **params,
        )
        return gap

    # Queries and export ----------------------------------------------------

    def fetch_work(self, work: Any) -> Any | None:
        """Fetch a Work node and reconstruct a Work model when possible."""

        records = self._run("MATCH (n:Work {iri: $iri}) RETURN n", iri=_iri(work))
        if not records:
            return None
        node = _record_get(records[0], "n")
        data = {
            _field_from_lpg_property(key): value
            for key, value in _node_properties(node).items()
            if key not in {"iri", "reviewId", "extractionRun"}
        }
        node_iri = _node_iri(node)
        try:
            from ..models import Work

            return Work.model_validate(data)
        except Exception:
            data["iri"] = node_iri
            return data

    def fetch_lens_subgraph(
        self,
        review_id: Any,
        lens_name: str,
        filters: Mapping[str, Any] | None = None,
    ) -> Any:
        """Fetch a review-partitioned lens subgraph for the frontend."""

        query = (
            "MATCH (n)-[r]-(m)\n"
            "WHERE n.reviewId = $reviewId AND ($lensName = '' OR $lensName IN labels(n))\n"
            "AND all(k IN keys($filters) WHERE n[k] = $filters[k])\n"
            "RETURN n, r, m"
        )
        records = self._run(
            query,
            reviewId=_review_partition_key(review_id),
            lensName=lens_name,
            filters=dict(filters or {}),
        )
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        for record in records:
            start = _record_get(record, "n")
            end = _record_get(record, "m")
            rel = _record_get(record, "r")
            for node in (start, end):
                if node is None:
                    continue
                iri = _node_iri(node)
                nodes[iri] = {
                    "iri": iri,
                    "labels": sorted(_node_labels(node)),
                    "properties": _node_properties(node),
                }
            if rel is not None and start is not None and end is not None:
                edges.append(
                    {
                        "source": _node_iri(start),
                        "target": _node_iri(end),
                        "type": _relationship_type(rel),
                    }
                )
        return _make_subgraph(nodes=list(nodes.values()), edges=edges)

    def _competency_file(self, number: int | str) -> Path:
        directory = self.ontology_root / "competency" / "cypher"
        if not directory.is_dir():
            raise FileNotFoundError(f"Cypher competency-question directory is absent: {directory}")
        number_text = str(number).lower().replace("cq", "")
        try:
            number_text = f"{int(number_text):02d}"
        except ValueError:
            number_text = str(number).lower()
        candidates = sorted(directory.glob(f"cq{number_text}_*.cypher"))
        if not candidates:
            candidates = sorted(directory.glob(f"cq{number_text}*.cypher"))
        if not candidates:
            raise FileNotFoundError(
                f"No Cypher competency question found for CQ{number_text}: {directory}"
            )
        return candidates[0]

    @staticmethod
    def _cypher_defaults(text: str) -> dict[str, Any]:
        match = re.search(r"^\s*//\s*DEFAULTS:\s*(\{.*\})\s*$", text, re.MULTILINE)
        if not match:
            return {}
        try:
            return dict(json.loads(match.group(1)))
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid JSON in Cypher competency-question DEFAULTS line") from exc

    def run_competency_question(
        self, number: int | str, parameters: Mapping[str, Any] | None = None
    ) -> Any:
        """Load a Cypher CQ, merge its ``// DEFAULTS:`` parameters, and run it."""

        path = self._competency_file(number)
        text = path.read_text(encoding="utf-8")
        query_lines = [
            line for line in text.splitlines() if not line.lstrip().startswith("// DEFAULTS:")
        ]
        query = "\n".join(query_lines).strip()
        params = self._cypher_defaults(text)
        params.update(parameters or {})
        records = self._run(query, **params)
        columns: list[str] = []
        rows: list[dict[str, Any]] = []
        for record in records:
            keys = _record_keys(record)
            if not keys and isinstance(record, Mapping):
                keys = list(record)
            for key in keys:
                if key not in columns:
                    columns.append(key)
            rows.append({key: _record_get(record, key) for key in keys})
        return _make_query_result(columns=columns, rows=rows)

    def export_turtle(
        self,
        graphs: Any | None = None,
        *,
        records: Iterable[Any] | None = None,
    ) -> str:
        """Export all Neo4j nodes/relationships by mapping LPG back to RDF.

        Neo4j has no RDF named graphs in this binding.  ``reviewId`` and
        ``extractionRun`` are intentionally omitted from the RDF data because
        they are partition metadata, while every domain property and edge is
        mapped back to its ``ptl:``/``cito:`` predicate.
        """

        if records is None and graphs is not None and _looks_like_records(graphs):
            records = graphs
        if records is not None:
            return records_to_turtle(records)
        selection = _partition_selector(graphs)
        if selection is None:
            node_query = "MATCH (n) RETURN n"
            relationship_query = "MATCH (a)-[r]->(b) RETURN a, r, b"
            params: dict[str, Any] = {}
        else:
            condition, params = selection
            node_query = f"MATCH (n) WHERE {condition.format(alias='n')} RETURN n"
            relationship_query = (
                "MATCH (a)-[r]->(b) WHERE "
                f"{condition.format(alias='a')} OR {condition.format(alias='b')} "
                "RETURN a, r, b"
            )
        node_records = self._run(node_query, **params)
        relationship_records = self._run(relationship_query, **params)
        return records_to_turtle([*node_records, *relationship_records])

    def validate(self, graphs: Any | None = None) -> Any:
        """Validate the Turtle export; this is the Neo4j→RDF spike boundary."""

        shapes = self.ontology_root / "shapes.ttl"
        if not shapes.is_file():
            return validate_graph("", shapes_path=shapes)
        data = self.export_turtle(graphs)
        try:
            return validate_graph(data, shapes_path=shapes)
        except TypeError:
            return validate_graph(data, shapes)

    @contextmanager
    def batch(self) -> Iterator[Neo4jStore]:
        """Use one Neo4j transaction for a group of repository writes."""

        if self._batch_tx is not None:
            yield self
            return
        session = self._session()
        tx = session.begin_transaction()
        self._batch_session = session
        self._batch_tx = tx
        try:
            yield self
            tx.commit()
        except Exception:
            rollback = getattr(tx, "rollback", None)
            if callable(rollback):
                rollback()
            raise
        finally:
            self._batch_tx = None
            self._batch_session = None
            close = getattr(session, "close", None)
            if callable(close):
                close()

    def batched_writes(self) -> Any:
        """Expose the Neo4j transaction through the shared API."""

        return self.batch()

    transaction = batch

    def close(self) -> None:
        if self._driver is not None:
            close = getattr(self._driver, "close", None)
            if callable(close):
                close()
            self._driver = None


def _make_subgraph(**values: Any) -> Any:
    try:
        return SubgraphResult(**values)
    except Exception:
        return values


def _make_query_result(**values: Any) -> Any:
    try:
        return QueryResult(**values)
    except Exception:
        return values


Neo4jRepository = Neo4jStore

__all__ = [
    "EXPORT_INHERENT_LOSSES",
    "Neo4jRepository",
    "Neo4jStore",
    "RELATIONSHIP_OVERRIDES",
    "build_node_upsert",
    "build_relationship_merge",
    "camel_case",
    "label_for_model",
    "property_name",
    "records_to_turtle",
    "relationship_type",
]
