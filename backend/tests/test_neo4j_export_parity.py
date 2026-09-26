"""Live parity check for the Neo4j-to-Turtle export boundary."""

from __future__ import annotations

import importlib.util
import os
import sys
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pytest
from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import XSD

from portolan.store.neo4j_store import EXPORT_INHERENT_LOSSES, Neo4jStore
from portolan.store.oxigraph_store import OxigraphStore

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
COMPOSER_PATH = REPOSITORY_ROOT / "eval" / "golden" / "compose_golden.py"


def _load_composer() -> Any:
    spec = importlib.util.spec_from_file_location("arg_export_parity_compose_golden", COMPOSER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


composer = _load_composer()


def _term_key(term: Any) -> tuple[Any, ...]:
    if isinstance(term, URIRef):
        return ("iri", str(term))
    if isinstance(term, BNode):
        return ("bnode", str(term))
    if isinstance(term, Literal):
        datatype = str(term.datatype) if term.datatype is not None else None
        if datatype == str(XSD.decimal):
            try:
                return ("literal", "decimal", Decimal(str(term)))
            except (InvalidOperation, ValueError):
                pass
        return ("literal", str(term), datatype, term.language)
    return ("term", str(term))


def _triple_key(triple: tuple[Any, Any, Any]) -> tuple[Any, ...]:
    subject, predicate, object_ = triple
    return _term_key(subject), _term_key(predicate), _term_key(object_)


def _normalized_turtle(text: str) -> set[tuple[Any, ...]]:
    graph = Graph()
    graph.parse(data=text, format="turtle")
    # Plain Turtle has no graph/context slot.  This intentionally compares the
    # union view; the named-graph loss is listed in EXPORT_INHERENT_LOSSES.
    return {_triple_key(triple) for triple in graph}


def _format_diff(triples: set[tuple[Any, ...]]) -> str:
    by_predicate: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
    for subject, predicate, object_ in triples:
        by_predicate[str(predicate[1])].append((subject, predicate, object_))
    lines: list[str] = []
    for predicate in sorted(by_predicate):
        values = sorted(by_predicate[predicate], key=repr)
        lines.append(f"  {predicate} ({len(values)})")
        lines.extend(f"    {value!r}" for value in values[:5])
        if len(values) > 5:
            lines.append(f"    ... {len(values) - 5} more")
    return "\n".join(lines) or "  (none)"


@pytest.mark.neo4j
def test_neo4j_export_matches_oxigraph_golden_graph() -> None:
    if not os.getenv("NEO4J_URI"):
        pytest.skip("NEO4J_URI is not set")
    assert frozenset({"named_graph_boundaries", "xsd_decimal_to_float"}) == EXPORT_INHERENT_LOSSES

    oxigraph = OxigraphStore()
    neo4j = Neo4jStore()
    try:
        composer.compose(oxigraph, repo_root=REPOSITORY_ROOT)
        composer.compose(neo4j, repo_root=REPOSITORY_ROOT)
        expected = _normalized_turtle(oxigraph.export_turtle())
        actual = _normalized_turtle(neo4j.export_turtle())
    finally:
        oxigraph.close()
        neo4j.close()

    missing = expected - actual
    extra = actual - expected
    if missing or extra:
        pytest.fail(
            "Neo4j Turtle export differs from Oxigraph after documented-loss normalization\n"
            f"Expected triple count: {len(expected)}; actual: {len(actual)}\n"
            f"Missing triples by predicate:\n{_format_diff(missing)}\n"
            f"Extra triples by predicate:\n{_format_diff(extra)}"
        )
