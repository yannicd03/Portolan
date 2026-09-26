"""Regression tests for backend RDF exports at the SHACL boundary."""

from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from rdflib import Dataset, Graph, URIRef

from portolan.store.neo4j_store import Neo4jStore, records_to_turtle
from portolan.store.oxigraph_store import PTL_NS, OxigraphStore
from portolan.store.validation import validate_graph

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
COMPOSER_PATH = REPOSITORY_ROOT / "eval" / "golden" / "compose_golden.py"
SHAPES_PATH = REPOSITORY_ROOT / "ontology" / "shapes.ttl"
INVALID_PATH = REPOSITORY_ROOT / "ontology" / "examples" / "minimal_invalid.ttl"


def _load_composer():
    spec = importlib.util.spec_from_file_location("arg_shacl_test_compose_golden", COMPOSER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


composer = _load_composer()
GOLDEN_AVAILABLE = (REPOSITORY_ROOT / "eval" / "golden" / "works.json").exists()


def _union_trig(path: Path) -> Graph:
    dataset = Dataset()
    dataset.parse(path, format="trig")
    graph = Graph()
    for subject, predicate, object_, _context in dataset.quads((None, None, None, None)):
        graph.add((subject, predicate, object_))
    return graph


def _assert_conforming(report: object) -> None:
    assert getattr(report, "conforms", None) is True, getattr(report, "report_text", report)
    assert getattr(report, "violations", []) == []


def test_golden_graph_conforms_in_oxigraph() -> None:
    if not GOLDEN_AVAILABLE:
        pytest.skip("golden set not built; run eval/golden/build_golden.py")
    repository = OxigraphStore()
    try:
        composer.compose(repository)
        _assert_conforming(repository.validate())
    finally:
        repository.close()


@pytest.mark.neo4j
def test_golden_graph_conforms_in_neo4j_when_configured() -> None:
    if not os.getenv("NEO4J_URI"):
        pytest.skip("NEO4J_URI is not set")
    if not GOLDEN_AVAILABLE:
        pytest.skip("golden set not built; run eval/golden/build_golden.py")
    repository = Neo4jStore()
    try:
        composer.compose(repository)
        _assert_conforming(repository.validate())
    finally:
        repository.close()


def test_minimal_invalid_fixture_still_fails_validation() -> None:
    report = validate_graph(_union_trig(INVALID_PATH), shapes_path=SHAPES_PATH)
    assert report.conforms is False
    assert report.violations


def test_oxigraph_and_neo4j_enum_exports_use_plain_literals() -> None:
    predicate = URIRef(f"{PTL_NS}sourceTier")
    work_iri = "https://w3id.org/portolan/id/work/test"

    oxigraph = OxigraphStore()
    try:
        # A mapping is sufficient for the store's model-to-RDF adapter and keeps
        # this test focused on the serialized term shape.
        @dataclass
        class Work:
            iri: str
            title: str
            issued: str
            source_tier: str

        oxigraph.upsert_work(Work(work_iri, "Test work", "2024", "preprint"))
        oxigraph_graph = Graph()
        oxigraph_graph.parse(data=oxigraph.export_turtle(), format="turtle")
    finally:
        oxigraph.close()

    neo4j_graph = Graph()
    neo4j_graph.parse(
        data=records_to_turtle(
            [
                {
                    "n": {
                        "iri": work_iri,
                        "labels": ["Work"],
                        "properties": {"title": "Test work", "sourceTier": "preprint"},
                    }
                }
            ]
        ),
        format="turtle",
    )

    oxigraph_value = next(oxigraph_graph.objects(URIRef(work_iri), predicate))
    neo4j_value = next(neo4j_graph.objects(URIRef(work_iri), predicate))
    assert oxigraph_value.datatype is None
    assert neo4j_value.datatype is None
