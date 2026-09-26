"""Unit tests for the shared CQ parity normalizer and harness mode."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import UTC
from decimal import Decimal
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from neo4j.time import DateTime
from rdflib import Literal
from rdflib.namespace import XSD

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS_PATH = REPO_ROOT / "eval" / "spike" / "run_spike.py"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from eval.spike.parity.normalizer import (  # noqa: E402
    expected_answer_rows,
    expected_column_mapping,
    expected_columns,
    expected_parameters,
    load_expected_answers,
    normalize_parameters,
    normalize_query_result,
    normalize_rows,
    normalize_value,
    parameters_for_backend,
    review_iri,
    rows_equal,
)


def _load_harness() -> ModuleType:
    spec = importlib.util.spec_from_file_location("arg_spike_parity_test", HARNESS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_normalizer_maps_columns_rounds_numbers_and_ignores_row_order() -> None:
    result = SimpleNamespace(
        columns=["reviewId", "score", "extra"],
        rows=[
            {
                "reviewId": "kgqa-rag",
                "score": Decimal("0.1234567"),
                "extra": "not compared",
            },
            {
                "reviewId": "https://w3id.org/portolan/id/review/kgqa-rag",
                "score": 0.5000000004,
                "extra": "also not compared",
            },
        ],
    )
    mapping = {
        "review": {"neo4j": "reviewId"},
        "score": {"neo4j": "score"},
    }

    normalized = normalize_rows(result, backend="neo4j", column_mapping=mapping)

    assert normalized == [
        {
            "review": review_iri("kgqa-rag"),
            "score": 0.123457,
        },
        {
            "review": review_iri("kgqa-rag"),
            "score": 0.5,
        },
    ]
    assert rows_equal(normalized, list(reversed(normalized)))


def test_normalizer_accepts_binding_specific_column_map_and_is_json_safe() -> None:
    result = {
        "columns": ["workIri", "review"],
        "rows": [{"workIri": "urn:work:1", "review": "kgqa-rag"}],
    }
    mapping = {
        "oxigraph": {"work": "work", "review": "review"},
        "neo4j": {"work": "workIri", "review": "review"},
    }

    normalized = normalize_query_result(
        result,
        backend="neo4j",
        column_mapping=mapping,
    )

    assert normalized["columns"] == ["work", "review"]
    assert normalized["rows"] == [{"review": review_iri("kgqa-rag"), "work": "urn:work:1"}]
    json.dumps(normalized, allow_nan=False)


def test_normalizer_canonicalizes_list_and_group_concat_limitation_texts() -> None:
    assert normalize_rows(
        [{"limitationTexts": ["z", "a"]}],
        column_mapping={"limitationTexts": "limitationTexts"},
    ) == [{"limitationTexts": "a | z"}]


@pytest.mark.parametrize(
    "value",
    [
        DateTime(2026, 9, 22, 0, 0, 0, 0, tzinfo=UTC),
        "2026-09-22T00:00:00.000000000+00:00",
        Literal("2026-09-22T00:00:00.000000000+00:00", datatype=XSD.dateTime),
    ],
)
def test_normalizer_canonicalizes_datetime_values(value: object) -> None:
    assert normalize_value(value) == "2026-09-22T00:00:00Z"


def test_normalizer_keeps_nonzero_datetime_fraction() -> None:
    value = DateTime(2026, 9, 22, 0, 0, 0, 123_400_000, tzinfo=UTC)

    assert normalize_value(value) == "2026-09-22T00:00:00.1234Z"
    assert normalize_rows(
        [{"limitationTexts": "z | a"}],
        column_mapping={"limitationTexts": "limitationTexts"},
    ) == [{"limitationTexts": "a | z"}]


def test_parameter_conversion_has_one_iri_bare_id_boundary() -> None:
    logical = {"review": "kgqa-rag", "seed_work_iri": "urn:seed:1"}

    assert parameters_for_backend(logical, "oxigraph") == {
        "review": review_iri("kgqa-rag"),
        "seed": "urn:seed:1",
    }
    assert parameters_for_backend(logical, "neo4j") == {
        "review": "kgqa-rag",
        "seed": "urn:seed:1",
    }


def test_regular_query_parameters_share_logical_source_and_convert_once() -> None:
    harness = _load_harness()
    spec = harness._competency_specs()[2]
    calls: list[tuple[dict[str, object], str]] = []
    original_converter = harness.parameters_for_backend

    def spy_converter(parameters: dict[str, object], backend: str) -> dict[str, object]:
        calls.append((parameters, backend))
        return original_converter(parameters, backend)

    harness.parameters_for_backend = spy_converter
    composition = {"query_parameters": {2: {"review": "https://w3id.org/portolan/id/review/other"}}}

    oxigraph = harness._query_parameters(composition, 2, spec, "oxigraph")
    neo4j = harness._query_parameters(composition, 2, spec, "neo4j")

    assert oxigraph == {
        "review": "https://w3id.org/portolan/id/review/other",
        "seed": harness.SEED_WORK_IRI,
    }
    assert neo4j == {"review": "other", "seed": harness.SEED_WORK_IRI}
    assert [backend for _, backend in calls] == ["oxigraph", "neo4j"]


def test_parity_harness_writes_rows_and_report_for_all_questions(tmp_path: Path) -> None:
    harness = _load_harness()

    class FakeRepository:
        def run_competency_question(self, number: int, parameters: dict[str, object]) -> object:
            return SimpleNamespace(columns=["native"], rows=[{"native": number}])

        def close(self) -> None:
            return None

    monkeypatch = SimpleNamespace(
        make=lambda backend: FakeRepository(),
        compose=lambda repository: {},
    )
    original_make = harness._make_repository
    original_compose = harness._call_composer
    harness._make_repository = monkeypatch.make
    harness._call_composer = monkeypatch.compose
    try:
        oracle = {
            f"CQ{number:02d}": {
                "parameters": harness._logical_query_parameters({}, number),
                "column_mapping": {"answer": {"oxigraph": "native"}},
                "rows": [{"answer": number}],
            }
            for number in range(1, 15)
        }
        oracle_path = tmp_path / "expected_answers.json"
        oracle_path.write_text(json.dumps(oracle), encoding="utf-8")

        payload = harness.run_parity(
            "oxigraph",
            output_dir=tmp_path,
            expected_answers_path=oracle_path,
        )
    finally:
        harness._make_repository = original_make
        harness._call_composer = original_compose

    assert payload["passed"] is True
    assert payload["backends"]["oxigraph"]["status"] == "completed"
    questions = payload["backends"]["oxigraph"]["competency_questions"]
    assert all(question["match"] for question in questions)
    rows = json.loads((tmp_path / "rows_oxigraph.json").read_text(encoding="utf-8"))
    assert rows["CQ01"]["rows"] == [{"answer": 1}]
    assert "Overall result: **PASS**" in (tmp_path / "parity_report.md").read_text(encoding="utf-8")


def test_parity_harness_rejects_missing_expected_rows(tmp_path: Path) -> None:
    harness = _load_harness()

    class FakeRepository:
        def run_competency_question(self, number: int, parameters: dict[str, object]) -> object:
            return SimpleNamespace(columns=["native"], rows=[{"native": number}])

        def close(self) -> None:
            return None

    original_make = harness._make_repository
    original_compose = harness._call_composer
    harness._make_repository = lambda backend: FakeRepository()
    harness._call_composer = lambda repository: {}
    try:
        oracle = {
            f"CQ{number:02d}": {
                "parameters": harness._logical_query_parameters({}, number),
                "column_mapping": {"answer": {"oxigraph": "native"}},
                "rows": [] if number == 1 else [{"answer": number}],
            }
            for number in range(1, 15)
        }
        oracle_path = tmp_path / "expected_answers.json"
        oracle_path.write_text(json.dumps(oracle), encoding="utf-8")
        payload = harness.run_parity(
            "oxigraph",
            output_dir=tmp_path,
            expected_answers_path=oracle_path,
        )
    finally:
        harness._make_repository = original_make
        harness._call_composer = original_compose

    question = payload["backends"]["oxigraph"]["competency_questions"][0]
    assert payload["passed"] is False
    assert question["match"] is False
    assert "normalized row set differs" in question["mismatches"]


def _assert_composed_backend_matches_oracle(harness: ModuleType, backend: str) -> None:
    repository = harness._make_repository(backend)
    expected = load_expected_answers(REPO_ROOT / "eval" / "golden" / "expected_answers.json")
    try:
        composition = harness._call_composer(repository)
        for number in range(1, 15):
            key = f"CQ{number:02d}"
            entry = expected[key]
            spec = harness._competency_specs()[number]
            logical = harness._logical_query_parameters(composition, number)
            assert logical == normalize_parameters(expected_parameters(entry))
            parameters = harness._query_parameters(composition, number, spec, backend)
            actual = repository.run_competency_question(number, parameters)
            normalized = normalize_query_result(
                actual,
                backend=backend,
                column_mapping=expected_column_mapping(entry),
            )
            assert not normalized["missing_columns"]
            assert set(normalized["columns"]) == set(expected_columns(entry))
            assert rows_equal(
                normalized["rows"],
                normalize_rows(expected_answer_rows(entry)),
            )
            assert normalized["rows"], f"{key} must exercise a non-empty golden answer"
    finally:
        repository.close()


def test_composed_golden_oxigraph_matches_expected_answers() -> None:
    _assert_composed_backend_matches_oracle(_load_harness(), "oxigraph")


@pytest.mark.neo4j
def test_composed_golden_neo4j_matches_expected_answers() -> None:
    if not os.getenv("NEO4J_URI"):
        pytest.skip("NEO4J_URI is not set")
    _assert_composed_backend_matches_oracle(_load_harness(), "neo4j")
