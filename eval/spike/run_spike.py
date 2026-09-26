"""Run the M0 graph-store comparison through :class:`GraphRepository`.

The composer is deliberately kept behind a small import boundary.  The companion
``eval/golden/compose_golden.py`` module may evolve while the two store bindings are
being finished, but the spike only needs a callable named ``compose``,
``compose_graph``, or ``compose_golden`` accepting a repository and writing the
golden graph through the repository API.

Importing this module is side-effect free.  It does not construct a store, import the
composer, contact Neo4j, or write the result artifacts until :func:`run_spike` or
``main`` is called.
"""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
import os
import re
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
ONTOLOGY_ROOT = REPO_ROOT / "ontology"
COMPOSER_PATH = REPO_ROOT / "eval" / "golden" / "compose_golden.py"
SPIKE_ROOT = Path(__file__).resolve().parent
PARITY_ROOT = SPIKE_ROOT / "parity"
RESULTS_PATH = SPIKE_ROOT / "results.json"
REPORT_PATH = SPIKE_ROOT / "report.md"
EXPECTED_ANSWERS_PATH = REPO_ROOT / "eval" / "golden" / "expected_answers.json"

REVIEW_IRI = "https://w3id.org/portolan/id/review/kgqa-rag"
SEED_WORK_IRI = "https://w3id.org/portolan/id/work/arxiv/1706.03762"
DEFAULT_PROBLEM_IRI = (
    "https://w3id.org/portolan/id/concept/problem/knowledge-graph-question-answering"
)
DEFAULT_ASSERTION_IRI = "https://w3id.org/portolan/id/contribution/transformer/1"

QUESTION_NUMBERS = tuple(range(1, 15))
BACKENDS = ("oxigraph", "neo4j")
SHARED_QUERY_DEFAULTS: dict[int, dict[str, Any]] = {
    1: {"review": REVIEW_IRI},
    2: {"review": REVIEW_IRI, "seed": SEED_WORK_IRI},
    3: {"problem": DEFAULT_PROBLEM_IRI},
    4: {"review": REVIEW_IRI},
    5: {"review": REVIEW_IRI, "n": 3},
    6: {"review": REVIEW_IRI, "n": 3},
    7: {"review": REVIEW_IRI},
    8: {"review": REVIEW_IRI},
    9: {"review": REVIEW_IRI},
    10: {"review": REVIEW_IRI},
    11: {"review": REVIEW_IRI, "similarityThreshold": 0.75},
    12: {"assertion": DEFAULT_ASSERTION_IRI},
    13: {"review": REVIEW_IRI, "work": SEED_WORK_IRI},
    14: {"review": REVIEW_IRI},
}

for _import_root in (REPO_ROOT, SPIKE_ROOT):
    if str(_import_root) not in sys.path:
        sys.path.insert(0, str(_import_root))

try:
    from eval.spike.parity.normalizer import (
        expected_answer_rows,
        expected_column_mapping,
        expected_columns,
        expected_parameters,
        load_expected_answers,
        normalize_parameters,
        normalize_query_result,
        normalize_rows,
        parameters_for_backend,
        resolve_column_mapping,
        rows_equal,
    )
except ModuleNotFoundError:  # pragma: no cover - direct execution from eval/spike
    from parity.normalizer import (
        expected_answer_rows,
        expected_column_mapping,
        expected_columns,
        expected_parameters,
        load_expected_answers,
        normalize_parameters,
        normalize_query_result,
        normalize_rows,
        parameters_for_backend,
        resolve_column_mapping,
        rows_equal,
    )


class BackendUnavailable(RuntimeError):
    """Raised when a requested backend cannot be reached or constructed."""


class ComposerUnavailable(RuntimeError):
    """Raised when the golden-graph composer contract is not available."""


@dataclass(frozen=True, slots=True)
class CompetencySpec:
    """The paired query files and their measured non-comment line counts."""

    number: int
    sparql_path: Path
    cypher_path: Path

    @property
    def query_path(self) -> Path:
        """Return the query file for the backend selected by the caller."""

        raise AttributeError("use sparql_path or cypher_path for a backend-specific path")


def _ensure_backend_importable() -> None:
    """Make ``portolan`` importable when the script is invoked from ``backend`` or elsewhere."""

    backend_text = str(BACKEND_ROOT)
    if backend_text not in sys.path:
        sys.path.insert(0, backend_text)


def _json_safe(value: Any) -> Any:
    """Convert model/driver values into deterministic JSON-compatible values."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    enum_value = getattr(value, "value", None)
    if enum_value is not None and enum_value is not value:
        return _json_safe(enum_value)
    return str(value)


def _relative_path(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _non_comment_line_count(path: Path) -> int:
    """Count code-bearing lines, ignoring blank, line, and block comments."""

    if not path.is_file():
        return 0
    text = path.read_text(encoding="utf-8")
    count = 0
    block_comment = False
    for line in text.splitlines():
        remaining: list[str] = []
        index = 0
        while index < len(line):
            if block_comment:
                end = line.find("*/", index)
                if end < 0:
                    index = len(line)
                    continue
                block_comment = False
                index = end + 2
                continue
            start = line.find("/*", index)
            line_comment_positions = [
                position
                for position in (line.find("#", index), line.find("//", index))
                if position >= 0
            ]
            line_comment = min(line_comment_positions) if line_comment_positions else -1
            if line_comment >= 0 and (start < 0 or line_comment < start):
                remaining.append(line[index:line_comment])
                index = len(line)
                continue
            if start < 0:
                remaining.append(line[index:])
                index = len(line)
                continue
            remaining.append(line[index:start])
            block_comment = True
            index = start + 2
        if "".join(remaining).strip():
            count += 1
    return count


def _competency_specs() -> dict[int, CompetencySpec]:
    sparql_dir = ONTOLOGY_ROOT / "competency" / "sparql"
    cypher_dir = ONTOLOGY_ROOT / "competency" / "cypher"
    specs: dict[int, CompetencySpec] = {}
    for number in QUESTION_NUMBERS:
        number_text = f"{number:02d}"
        sparql_candidates = sorted(sparql_dir.glob(f"cq{number_text}_*.rq"))
        cypher_candidates = sorted(cypher_dir.glob(f"cq{number_text}_*.cypher"))
        sparql_path = (
            sparql_candidates[0]
            if sparql_candidates
            else sparql_dir / f"cq{number_text}_missing.rq"
        )
        cypher_path = (
            cypher_candidates[0]
            if cypher_candidates
            else cypher_dir / f"cq{number_text}_missing.cypher"
        )
        specs[number] = CompetencySpec(number, sparql_path, cypher_path)
    return specs


def _mapping_value(source: Any, name: str) -> Any:
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _composition_parameters(composition: Any, number: int) -> dict[str, Any]:
    """Read optional query parameters from the composer's public result metadata."""

    candidates: list[Mapping[str, Any]] = []
    for attribute in ("query_parameters", "cq_parameters", "parameters"):
        source = _mapping_value(composition, attribute)
        if isinstance(source, Mapping):
            candidates.append(source)

    selected: dict[str, Any] = {}
    keys = (number, str(number), f"{number:02d}", f"CQ{number:02d}", f"cq{number:02d}")
    for source in candidates:
        for key in keys:
            value = source.get(key)
            if isinstance(value, Mapping):
                selected.update(value)

        # A flat mapping is also accepted for a composer-wide query context.
        if source and all(not isinstance(value, Mapping) for value in source.values()):
            selected.update(source)

    return selected


def _composition_identifier(composition: Any, *names: str) -> str | None:
    for source_name in ("metadata", "context", "identifiers"):
        source = _mapping_value(composition, source_name)
        for name in names:
            value = _mapping_value(source, name)
            if value is not None:
                return str(getattr(value, "iri", value))
    for name in names:
        value = _mapping_value(composition, name)
        if value is not None:
            return str(getattr(value, "iri", value))
    return None


def _query_parameters(
    composition: Any,
    number: int,
    spec: CompetencySpec,
    backend: str = "neo4j",
) -> dict[str, Any]:
    """Assemble one logical parameter table, then convert it for one backend.

    Composition metadata overrides the shared fallback table.  The logical table uses
    full IRIs for review-scoped identifiers; ``parameters_for_backend`` is the single
    conversion point that sends those values to Oxigraph as IRIs and to Neo4j as bare
    review ids because the LPG binding carries the partition as a ``reviewId`` property.
    Query-file defaults remain useful for direct repository calls, but the spike harness
    does not let either binding's defaults silently choose a different question context.
    """

    logical_parameters = _logical_query_parameters(composition, number)
    return parameters_for_backend(logical_parameters, backend)


def _logical_query_parameters(composition: Any, number: int) -> dict[str, Any]:
    """Build logical parameters before the binding-specific conversion."""

    logical_parameters = dict(SHARED_QUERY_DEFAULTS.get(number, {}))
    replacements = {
        "review_iri": "review",
        "seed_work_iri": "seed",
        "problem_iri": "problem",
        "assertion_iri": "assertion",
        "excluded_work_iri": "work",
    }
    identifiers = {
        "review_iri": _composition_identifier(composition, "review_iri", "review"),
        "seed_work_iri": _composition_identifier(composition, "seed_work_iri", "seed_work"),
        "problem_iri": _composition_identifier(composition, "problem_iri", "problem"),
        "assertion_iri": _composition_identifier(composition, "assertion_iri", "assertion"),
        "excluded_work_iri": _composition_identifier(
            composition, "excluded_work_iri", "excluded_work", "cq13_work"
        ),
    }
    for source_name, parameter_name in replacements.items():
        value = identifiers[source_name]
        if value is not None:
            logical_parameters[parameter_name] = value
    # A CQ-specific query-parameter entry is the most explicit composition metadata;
    # it wins over the composer-wide identifiers above while the shared table remains
    # the fallback for anything the composer did not provide.
    logical_parameters.update(_composition_parameters(composition, number))
    return normalize_parameters(logical_parameters)


def _load_composer() -> ModuleType:
    if not COMPOSER_PATH.is_file():
        raise ComposerUnavailable(f"golden composer is missing: {COMPOSER_PATH}")
    spec = importlib.util.spec_from_file_location("arg_spike_compose_golden", COMPOSER_PATH)
    if spec is None or spec.loader is None:
        raise ComposerUnavailable(f"could not import golden composer: {COMPOSER_PATH}")
    module = importlib.util.module_from_spec(spec)
    # Register before executing: a @dataclass in the composer resolves its annotations
    # through sys.modules[cls.__module__].__dict__, which is None for an unregistered
    # module and fails with "'NoneType' object has no attribute '__dict__'".
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _call_composer(repository: Any) -> Any:
    """Call the documented composer API, with small compatibility aliases."""

    module = _load_composer()
    function = next(
        (
            getattr(module, name)
            for name in ("compose", "compose_graph", "compose_golden", "compose_into")
            if callable(getattr(module, name, None))
        ),
        None,
    )
    if function is None:
        raise ComposerUnavailable(
            "compose_golden.py must expose compose(repository, *, repo_root=...)"
        )

    try:
        signature = inspect.signature(function)
        parameters = signature.parameters
    except (TypeError, ValueError):
        parameters = {}
    keyword_arguments: dict[str, Any] = {}
    for name in ("repo_root", "root", "repository_root"):
        if name in parameters:
            keyword_arguments[name] = REPO_ROOT
    if "repository" in parameters:
        return function(repository=repository, **keyword_arguments)
    return function(repository, **keyword_arguments)


def _make_repository(backend: str) -> Any:
    _ensure_backend_importable()
    if backend == "oxigraph":
        from portolan.store.oxigraph_store import OxigraphStore

        return OxigraphStore(ontology_root=ONTOLOGY_ROOT)
    if backend == "neo4j":
        from portolan.store.neo4j_store import Neo4jStore

        uri = os.getenv("NEO4J_URI", "").strip()
        if not uri:
            raise BackendUnavailable("NEO4J_URI is not set")
        return Neo4jStore(
            uri=uri,
            user=os.getenv("NEO4J_USER", "neo4j"),
            password=os.getenv("NEO4J_PASSWORD"),
            database=os.getenv("NEO4J_DATABASE"),
            ontology_root=ONTOLOGY_ROOT,
        )
    raise ValueError(f"unknown backend: {backend}")


def _probe_neo4j(repository: Any) -> None:
    """Probe Neo4j through the repository read boundary before mutating schema."""

    repository.fetch_work("urn:ptl:m0-spike-connectivity-probe")


def _split_cypher_statements(text: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    quote: str | None = None
    block_comment = False
    index = 0
    while index < len(text):
        character = text[index]
        next_character = text[index + 1] if index + 1 < len(text) else ""
        if block_comment:
            if character == "*" and next_character == "/":
                block_comment = False
                index += 2
                continue
            if character == "\n":
                current.append(character)
            index += 1
            continue
        if quote is None and character == "/" and next_character == "/":
            index += 2
            while index < len(text) and text[index] != "\n":
                index += 1
            continue
        if quote is None and character == "/" and next_character == "*":
            block_comment = True
            index += 2
            continue
        if quote is None and character in {"'", '"', "`"}:
            quote = character
            current.append(character)
            index += 1
            continue
        if quote is not None:
            current.append(character)
            if character == "\\" and index + 1 < len(text):
                current.append(text[index + 1])
                index += 2
                continue
            if character == quote:
                quote = None
            index += 1
            continue
        if character == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
        else:
            current.append(character)
        index += 1
    statement = "".join(current).strip()
    if statement:
        statements.append(statement)
    return statements


def _constraint_name(statement: str) -> str | None:
    match = re.search(r"CREATE\s+(?:CONSTRAINT|INDEX)\s+([A-Za-z0-9_]+)", statement, re.IGNORECASE)
    return match.group(1) if match else None


def _shape_for_constraint(name: str | None) -> int | None:
    if name is None:
        return None
    if name in {"work_title_exists", "work_issued_exists", "work_source_tier_exists"}:
        return 1
    if name in {
        "contribution_kind_exists",
        "claim_text_exists",
        "limitation_text_exists",
        "future_work_text_exists",
        "evidence_quote_exists",
        "evidence_source_kind_exists",
    }:
        return 3
    if name == "result_value_exists":
        return 4
    if name in {"inclusion_decision_exists", "inclusion_stage_exists"}:
        return 5
    if name.startswith("gap_"):
        return 6
    if name == "metric_direction_exists":
        return 8
    if name.startswith("cluster_pair_"):
        return 9
    return None


def _is_enterprise_only_error(error: str) -> bool:
    text = error.lower()
    return any(
        phrase in text
        for phrase in (
            "enterprise",
            "community",
            "property existence",
            "relationship property existence",
            "not supported",
        )
    )


def _apply_neo4j_constraints(repository: Any) -> dict[str, Any]:
    """Apply and record the checked-in Neo4j schema statements.

    ``GraphRepository`` intentionally has no schema-management method.  This is the one
    explicit spike-only exception to the repository boundary: the requested measurement
    is whether the server accepts the checked-in schema, so the Neo4j store's existing
    private execution hook is used only for those schema statements, never for data or
    competency-question queries.
    """

    path = ONTOLOGY_ROOT / "neo4j_constraints.cypher"
    if not path.is_file():
        return {"executed": False, "error": f"missing constraints file: {path}", "statements": []}
    runner = getattr(repository, "_run", None)
    if not callable(runner):
        return {
            "executed": False,
            "error": "Neo4j repository has no schema execution hook",
            "statements": [],
        }
    records: list[dict[str, Any]] = []
    for statement in _split_cypher_statements(path.read_text(encoding="utf-8")):
        name = _constraint_name(statement)
        started = time.perf_counter()
        try:
            runner(statement)
        except Exception as exc:  # a rejected schema item is evidence, not a harness crash
            error = str(exc)
            records.append(
                {
                    "name": name,
                    "kind": "constraint" if "CONSTRAINT" in statement.upper() else "index",
                    "shape": _shape_for_constraint(name),
                    "accepted": False,
                    "enterprise_only_error": _is_enterprise_only_error(error),
                    "error": error,
                    "wall_time_seconds": time.perf_counter() - started,
                }
            )
        else:
            records.append(
                {
                    "name": name,
                    "kind": "constraint" if "CONSTRAINT" in statement.upper() else "index",
                    "shape": _shape_for_constraint(name),
                    "accepted": True,
                    "enterprise_only_error": False,
                    "error": None,
                    "wall_time_seconds": time.perf_counter() - started,
                }
            )

    shapes: dict[str, dict[str, Any]] = {}
    for shape_number in range(1, 10):
        shape_records = [record for record in records if record["shape"] == shape_number]
        accepted = [record["name"] for record in shape_records if record["accepted"]]
        rejected = [record["name"] for record in shape_records if not record["accepted"]]
        shapes[str(shape_number)] = {
            "shape": shape_number,
            "fully_enforced_natively": False,
            "accepted_constraint_fragments": accepted,
            "rejected_constraint_fragments": rejected,
            "note": (
                "Property fragments were measured, but the shape also contains relationship, "
                "conditional, range, or closed-enumeration rules."
                if shape_records
                else "No checked-in Neo4j constraint claims this shape."
            ),
        }
    return {
        "executed": True,
        "shape_count_in_ontology": 9,
        "statements": records,
        "accepted_count": sum(bool(record["accepted"]) for record in records),
        "rejected_count": sum(not record["accepted"] for record in records),
        "shapes": shapes,
        "enterprise_only_rejections": [
            record["name"]
            for record in records
            if not record["accepted"] and record["enterprise_only_error"]
        ],
    }


def _query_result_row_count(result: Any) -> int:
    row_count = getattr(result, "row_count", None)
    if isinstance(row_count, int):
        return row_count
    rows = getattr(result, "rows", None)
    if rows is not None:
        return len(rows)
    if isinstance(result, (list, tuple, set, frozenset)):
        return len(result)
    return 1 if result is not None else 0


def _run_question(
    repository: Any, spec: CompetencySpec, composition: Any, backend: str
) -> dict[str, Any]:
    query_path = spec.sparql_path if backend == "oxigraph" else spec.cypher_path
    parameters = _query_parameters(composition, spec.number, spec, backend)
    attempts: list[dict[str, Any]] = []
    successes: list[tuple[float, int]] = []
    for attempt_number in range(1, 4):
        started = time.perf_counter()
        try:
            result = repository.run_competency_question(spec.number, parameters)
        except Exception as exc:  # keep going: an execution failure is a spike finding
            attempts.append(
                {
                    "attempt": attempt_number,
                    "executed": False,
                    "row_count": None,
                    "wall_time_seconds": time.perf_counter() - started,
                    "error": str(exc),
                }
            )
        else:
            elapsed = time.perf_counter() - started
            row_count = _query_result_row_count(result)
            successes.append((elapsed, row_count))
            attempts.append(
                {
                    "attempt": attempt_number,
                    "executed": True,
                    "row_count": row_count,
                    "wall_time_seconds": elapsed,
                    "error": None,
                }
            )

    errors = [attempt["error"] for attempt in attempts if attempt["error"]]
    row_counts = sorted({row_count for _, row_count in successes})
    return {
        "number": f"CQ{spec.number:02d}",
        "query_file": _relative_path(query_path),
        "query_non_comment_line_count": _non_comment_line_count(query_path),
        "parameters": _json_safe(parameters),
        "executed": bool(successes),
        "row_count": successes[0][1] if successes else None,
        "row_counts_across_runs": row_counts,
        "best_wall_time_seconds": min((elapsed for elapsed, _ in successes), default=None),
        "runs": attempts,
        "error": " | ".join(f"run {index + 1}: {error}" for index, error in enumerate(errors))
        or None,
    }


def _failed_questions(error: str, backend: str) -> list[dict[str, Any]]:
    specs = _competency_specs()
    return [
        {
            "number": f"CQ{number:02d}",
            "query_file": _relative_path(
                spec.sparql_path if backend == "oxigraph" else spec.cypher_path
            ),
            "query_non_comment_line_count": _non_comment_line_count(
                spec.sparql_path if backend == "oxigraph" else spec.cypher_path
            ),
            "parameters": {},
            "executed": False,
            "row_count": None,
            "row_counts_across_runs": [],
            "best_wall_time_seconds": None,
            "runs": [],
            "error": error,
        }
        for number, spec in specs.items()
    ]


def _repo_relative(path: Any) -> Any:
    """Keep machine-specific absolute paths out of committed result artifacts."""

    if not isinstance(path, str) or not Path(path).is_absolute():
        return path
    try:
        return Path(path).resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path


def _validation_dict(report: Any) -> dict[str, Any]:
    result = _validation_fields(report)
    if "shapes_path" in result:
        result["shapes_path"] = _repo_relative(result["shapes_path"])
    return result


def _validation_fields(report: Any) -> dict[str, Any]:
    if isinstance(report, Mapping):
        return _json_safe(dict(report))
    as_dict = getattr(report, "as_dict", None)
    if callable(as_dict):
        return _json_safe(as_dict())
    fields = (
        "conforms",
        "valid",
        "skipped",
        "error",
        "shapes_path",
        "report_text",
    )
    result = {field: getattr(report, field, None) for field in fields}
    for field in ("violations", "warnings", "issues"):
        value = getattr(report, field, None)
        if value is not None:
            result[field] = value
    return _json_safe(result)


def _parse_turtle(
    turtle: str | None,
) -> tuple[int | None, set[tuple[str, str, str]] | None, str | None]:
    if turtle is None:
        return None, None, None
    try:
        from rdflib import Graph

        graph = Graph()
        graph.parse(data=turtle, format="turtle")
        triples = {
            (subject.n3(), predicate.n3(), object_.n3()) for subject, predicate, object_ in graph
        }
        return len(graph), triples, None
    except Exception as exc:
        return None, None, str(exc)


def _run_backend(
    backend: str, skip_neo4j_if_unavailable: bool
) -> tuple[dict[str, Any], str | None]:
    result: dict[str, Any] = {
        "backend": backend,
        "status": "started",
        "load": {},
        "competency_questions": [],
        "validation": {},
        "export": {},
    }
    repository: Any | None = None
    composition: Any = {}
    turtle: str | None = None
    try:
        repository = _make_repository(backend)
        if backend == "neo4j":
            try:
                _probe_neo4j(repository)
            except Exception as exc:
                raise BackendUnavailable(f"Neo4j connectivity probe failed: {exc}") from exc
            result["native_constraints"] = _apply_neo4j_constraints(repository)

        load_started = time.perf_counter()
        try:
            composition = _call_composer(repository)
        except Exception as exc:
            result["load"] = {
                "executed": False,
                "elapsed_seconds": time.perf_counter() - load_started,
                "error": str(exc),
            }
        else:
            result["load"] = {
                "executed": True,
                "elapsed_seconds": time.perf_counter() - load_started,
                "error": None,
            }

        specs = _competency_specs()
        if result["load"].get("executed"):
            result["competency_questions"] = [
                _run_question(repository, specs[number], composition, backend)
                for number in QUESTION_NUMBERS
            ]
        else:
            result["competency_questions"] = _failed_questions(
                f"golden graph load failed: {result['load'].get('error')}", backend
            )

        validation_started = time.perf_counter()
        try:
            report = repository.validate()
        except Exception as exc:
            result["validation"] = {
                "executed": False,
                "elapsed_seconds": time.perf_counter() - validation_started,
                "error": str(exc),
            }
        else:
            result["validation"] = {
                "executed": True,
                "elapsed_seconds": time.perf_counter() - validation_started,
                "path": (
                    "Neo4j export_turtle() -> validation.validate()"
                    if backend == "neo4j"
                    else "Oxigraph export_turtle() -> validation.validate()"
                ),
                "report": _validation_dict(report),
                "error": None,
            }

        export_started = time.perf_counter()
        try:
            turtle = repository.export_turtle()
        except Exception as exc:
            result["export"] = {
                "executed": False,
                "elapsed_seconds": time.perf_counter() - export_started,
                "triple_count": None,
                "error": str(exc),
            }
        else:
            triple_count, _, parse_error = _parse_turtle(turtle)
            result["export"] = {
                "executed": True,
                "elapsed_seconds": time.perf_counter() - export_started,
                "turtle_bytes": len(turtle.encode("utf-8")),
                "triple_count": triple_count,
                "parse_error": parse_error,
                "error": None,
                "known_partition_metadata_omissions": (
                    ["reviewId", "extractionRun"] if backend == "neo4j" else []
                ),
            }
        result["status"] = "completed"
    except BackendUnavailable as exc:
        result["status"] = (
            "skipped" if skip_neo4j_if_unavailable and backend == "neo4j" else "unavailable"
        )
        result["availability_error"] = str(exc)
        result["competency_questions"] = _failed_questions(str(exc), backend)
    except Exception as exc:
        result["status"] = "failed"
        result["fatal_error"] = str(exc)
        result["competency_questions"] = _failed_questions(str(exc), backend)
    finally:
        close = getattr(repository, "close", None)
        if callable(close):
            close()
    return _json_safe(result), turtle


def _compare_exports(results: dict[str, dict[str, Any]], turtles: Mapping[str, str | None]) -> None:
    oxigraph_count, oxigraph_triples, _ = _parse_turtle(turtles.get("oxigraph"))
    neo4j_count, neo4j_triples, _ = _parse_turtle(turtles.get("neo4j"))
    if "oxigraph" in results and oxigraph_count is not None:
        results["oxigraph"]["export"]["parsed_triple_count"] = oxigraph_count
    if "neo4j" not in results:
        return
    export = results["neo4j"].setdefault("export", {})
    comparison: dict[str, Any] = {"available": False}
    if oxigraph_triples is not None and neo4j_triples is not None:
        missing = sorted(oxigraph_triples - neo4j_triples)
        extra = sorted(neo4j_triples - oxigraph_triples)
        comparison = {
            "available": True,
            "reference": "oxigraph Turtle triple set",
            "missing_from_neo4j_count": len(missing),
            "extra_in_neo4j_count": len(extra),
            "missing_from_neo4j_examples": [list(item) for item in missing[:10]],
            "extra_in_neo4j_examples": [list(item) for item in extra[:10]],
        }
    export["comparison_to_oxigraph"] = comparison
    export["observed_fidelity_notes"] = [
        "Plain Turtle has no named-graph syntax, so partition boundaries cannot be retained.",
        (
            "Neo4jStore.records_to_turtle omits the LPG partition properties reviewId "
            "and extractionRun."
        ),
        (
            "Neo4j stores cito:cites as a CITES relationship; the exporter reconstructs "
            "the RDF citation node and shortcut."
        ),
    ]
    if neo4j_count is not None:
        export["parsed_triple_count"] = neo4j_count


def _fmt_seconds(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "—"
    return f"{value * 1000:.3f} ms"


def _fmt_value(value: Any) -> str:
    if value is None:
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _question_by_number(result: Mapping[str, Any], number: int) -> Mapping[str, Any] | None:
    expected = f"CQ{number:02d}"
    return next(
        (item for item in result.get("competency_questions", []) if item.get("number") == expected),
        None,
    )


def _validation_summary(result: Mapping[str, Any]) -> str:
    validation = result.get("validation", {})
    if not validation.get("executed"):
        return f"not executed: {_fmt_value(validation.get('error'))}"
    report = validation.get("report", {})
    if not isinstance(report, Mapping):
        return "executed"
    conforms = report.get("conforms")
    violations = len(report.get("violations", []) or [])
    warnings = len(report.get("warnings", []) or [])
    return f"conforms={conforms}; violations={violations}; warnings={warnings}"


def _success_count(result: Mapping[str, Any]) -> int:
    return sum(bool(item.get("executed")) for item in result.get("competency_questions", []))


def _render_report(payload: Mapping[str, Any]) -> str:
    backends = payload.get("backends", {})
    oxigraph = backends.get("oxigraph", {})
    neo4j = backends.get("neo4j", {})
    lines = [
        "# M0 graph-store spike",
        "",
        f"Generated: `{payload.get('generated_at_utc', 'unknown')}`",
        "",
        (
            "This report records measurements from the requested stores. It does not "
            "declare a winner; the ADR decision remains a human decision from this evidence."
        ),
        "",
        "## Run summary",
        "",
        "| Backend | Status | Load | CQs executed successfully | Turtle triples | Export |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for name in BACKENDS:
        result = backends.get(name)
        if not result:
            lines.append(f"| {name} | not requested | — | — | — | — |")
            continue
        load = result.get("load", {})
        export = result.get("export", {})
        lines.append(
            f"| {name} | {_fmt_value(result.get('status'))} | "
            f"{_fmt_seconds(load.get('elapsed_seconds'))} | "
            f"{_success_count(result)}/14 | {_fmt_value(export.get('triple_count'))} | "
            f"{_fmt_seconds(export.get('elapsed_seconds'))} |"
        )

    lines.extend(
        [
            "",
            "## Competency-question measurements",
            "",
            (
                "Each query was submitted three times through "
                "`GraphRepository.run_competency_question`; the reported time is the "
                "best successful run. An error is retained even when another attempt "
                "succeeds."
            ),
            "",
            (
                "| CQ | Oxigraph SPARQL lines | Oxigraph rows / best | "
                "Neo4j Cypher lines | Neo4j rows / best |"
            ),
            "|---|---:|---:|---:|---:|",
        ]
    )
    for number in QUESTION_NUMBERS:
        ox = _question_by_number(oxigraph, number)
        neo = _question_by_number(neo4j, number)
        ox_rows = (
            f"{ox.get('row_count')} / {_fmt_seconds(ox.get('best_wall_time_seconds'))}"
            if ox
            else "—"
        )
        neo_rows = (
            f"{neo.get('row_count')} / {_fmt_seconds(neo.get('best_wall_time_seconds'))}"
            if neo
            else "—"
        )
        ox_line_count = ox.get("query_non_comment_line_count") if ox else None
        neo_line_count = neo.get("query_non_comment_line_count") if neo else None
        lines.append(
            f"| CQ{number:02d} | {_fmt_value(ox_line_count)} | "
            f"{_fmt_value(ox_rows)} | {_fmt_value(neo_line_count)} | "
            f"{_fmt_value(neo_rows)} |"
        )
    failures = []
    for name, result in backends.items():
        for question in result.get("competency_questions", []):
            if question.get("error"):
                failures.append((name, question))
    if failures:
        lines.extend(["", "### CQ errors", ""])
        for name, question in failures:
            lines.append(f"- `{name} {question['number']}`: {question['error']}")
    else:
        lines.extend(["", "No competency-question execution errors were recorded."])

    lines.extend(["", "## Neo4j native constraint evidence", ""])
    native = neo4j.get("native_constraints")
    if not native:
        lines.append("Neo4j was not run, so schema acceptance was not measured.")
    else:
        lines.append(
            f"The checked-in schema contained {len(native.get('statements', []))} statements; "
            f"{native.get('accepted_count', 0)} were accepted and "
            f"{native.get('rejected_count', 0)} rejected. "
            f"The current ontology file declares "
            f"{native.get('shape_count_in_ontology', 'unknown')} shapes "
            "(Shape 9 is the v0.2 ClusterPair amendment)."
        )
        lines.extend(
            [
                "",
                "| Shape | Fully enforced natively | Accepted fragments | Rejected fragments |",
                "|---:|---|---|---|",
            ]
        )
        for shape_number in range(1, 10):
            shape = native.get("shapes", {}).get(str(shape_number), {})
            lines.append(
                f"| {shape_number} | {_fmt_value(shape.get('fully_enforced_natively'))} | "
                f"{_fmt_value(', '.join(shape.get('accepted_constraint_fragments', [])))} | "
                f"{_fmt_value(', '.join(shape.get('rejected_constraint_fragments', [])))} |"
            )
        rejected = native.get("enterprise_only_rejections", [])
        if rejected:
            lines.extend(
                [
                    "",
                    "Enterprise/Community rejection candidates: "
                    + ", ".join(f"`{name}`" for name in rejected)
                    + ".",
                ]
            )

    lines.extend(["", "## ADR criteria evidence", ""])
    criteria = [
        (
            "Query clarity",
            (
                "Per-CQ line counts and measured rows/times are above. The paired "
                "artifacts keep the same CQ numbers and slugs; CQ05 and CQ11 expose "
                "the main absence/pair-measure differences."
            ),
        ),
        (
            "Validation effort",
            (
                f"Oxigraph: {_validation_summary(oxigraph)}. "
                f"Neo4j: {_validation_summary(neo4j)}; the Neo4j path validates the "
                "Turtle export outside the server."
            ),
        ),
        (
            "Setup friction",
            (
                "Oxigraph setup is in-memory. Neo4j setup includes the live connection "
                "and the schema acceptance results above; load times are measured in "
                "the run summary. GraphRepository has no reset operation, so a "
                "persisted Neo4j run assumes the target database is empty or isolated "
                "before loading."
            ),
        ),
        (
            "Export fidelity",
            (
                f"Oxigraph exported {_fmt_value(oxigraph.get('export', {}).get('triple_count'))} "
                "Turtle triples. "
                f"Neo4j exported {_fmt_value(neo4j.get('export', {}).get('triple_count'))}; "
                "the machine-readable comparison records missing/extra triples when "
                "both parses succeeded. Neo4j plain Turtle cannot retain named-graph "
                "boundaries and its exporter omits reviewId/extractionRun partition "
                "properties."
            ),
        ),
        (
            "Modelling fit",
            (
                "The RDF binding retains a reified ptl:Citation plus cito:cites. Neo4j "
                "stores citation facts on a CITES relationship and reconstructs the "
                "reified RDF form during export; the concrete triple-set comparison "
                "is the measured check."
            ),
        ),
        (
            "Isolation",
            (
                "Oxigraph uses named graphs for bibliography, extraction runs, and "
                "reviews. Neo4j uses reviewId/extractionRun node or relationship "
                "properties; those partition properties are intentionally omitted "
                "by its plain Turtle exporter."
            ),
        ),
    ]
    lines.extend(["| Criterion | Evidence / observed difference |", "|---|---|"])
    lines.extend(f"| {name} | {note} |" for name, note in criteria)

    lines.extend(
        [
            "",
            "## Repository-boundary issues to carry into the ADR",
            "",
            (
                "- `GraphRepository` exposes `write_cluster_pair`; the v0.2 binding "
                "still records the pair differently in RDF and LPG, so the two "
                "competency queries must read their binding-specific shapes."
            ),
            (
                "- `GraphRepository` has no reset/clear method. The harness creates a "
                "new Oxigraph instance, but it cannot clear a persisted Neo4j database "
                "without leaving the repository boundary; repeat Neo4j runs therefore "
                "require an externally prepared empty database."
            ),
            (
                "- Neo4j schema application is measured through the store's private "
                "execution hook only because the repository contract has no "
                "schema-management operation. Data writes, CQs, validation, and "
                "export remain repository calls."
            ),
            "",
            "The harness intentionally leaves the ADR decision open.",
            "",
        ]
    )
    return "\n".join(lines)


def _expected_logical_parameters(
    expected_entry: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Read optional logical parameters from the oracle for a consistency check."""

    if expected_entry is None:
        return None
    parameters = expected_parameters(expected_entry)
    if parameters is None:
        return None
    logical = parameters.get("logical")
    if isinstance(logical, Mapping):
        parameters = logical
    elif all(isinstance(parameters.get(name), Mapping) for name in BACKENDS) and any(
        name in parameters for name in BACKENDS
    ):
        parameters = parameters.get("oxigraph", {})
    return normalize_parameters(parameters)


def _parity_expected_columns(
    expected_entry: Mapping[str, Any] | None,
    backend: str,
) -> list[str]:
    if expected_entry is None:
        return []
    explicit = expected_columns(expected_entry)
    if explicit:
        return explicit
    mapping = resolve_column_mapping(expected_column_mapping(expected_entry), backend)
    if mapping:
        return list(mapping)
    rows = normalize_rows(expected_answer_rows(expected_entry))
    return sorted({key for row in rows for key in row})


def _parity_failure_records(
    error: str,
    expected_answers: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    questions: list[dict[str, Any]] = []
    rows: dict[str, dict[str, Any]] = {}
    for number in QUESTION_NUMBERS:
        key = f"CQ{number:02d}"
        record = {
            "number": key,
            "executed": False,
            "match": False,
            "parameters": {},
            "source_columns": [],
            "columns": [],
            "actual_row_count": None,
            "expected_row_count": len(normalize_rows(expected_answer_rows(expected_answers[key])))
            if key in expected_answers
            else None,
            "rows": [],
            "mismatches": [error],
            "error": error,
        }
        questions.append(record)
        rows[key] = {
            "parameters": {},
            "columns": [],
            "rows": [],
            "executed": False,
            "error": error,
        }
    return questions, rows


def _run_parity_backend(
    backend: str,
    expected_answers: Mapping[str, Mapping[str, Any]],
    skip_neo4j_if_unavailable: bool,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Compose one backend, run all CQs once, and compare normalized rows."""

    result: dict[str, Any] = {
        "backend": backend,
        "status": "started",
        "competency_questions": [],
        "mismatches": [],
    }
    rows_artifact: dict[str, dict[str, Any]] = {}
    repository: Any | None = None
    try:
        repository = _make_repository(backend)
        if backend == "neo4j":
            try:
                _probe_neo4j(repository)
            except Exception as exc:
                raise BackendUnavailable(f"Neo4j connectivity probe failed: {exc}") from exc

        composition = _call_composer(repository)
        specs = _competency_specs()
        for number in QUESTION_NUMBERS:
            key = f"CQ{number:02d}"
            spec = specs[number]
            expected_entry = expected_answers.get(key)
            # Use the same logical-source resolver as regular spike runs. It performs
            # the binding conversion exactly once, so the oracle records/checks what
            # was actually asked rather than silently choosing the query context.
            parameters = _query_parameters(composition, number, spec, backend)
            logical_parameters = normalize_parameters(parameters)
            mapping = expected_column_mapping(expected_entry) if expected_entry else {}
            expected_rows = normalize_rows(
                expected_answer_rows(expected_entry) if expected_entry else []
            )
            question: dict[str, Any] = {
                "number": key,
                "executed": False,
                "match": False,
                "parameters": _json_safe(parameters),
                "logical_parameters": _json_safe(logical_parameters),
                "source_columns": [],
                "columns": [],
                "actual_row_count": None,
                "expected_row_count": len(expected_rows),
                "rows": [],
                "mismatches": [],
                "error": None,
            }
            if expected_entry is None:
                question["mismatches"].append("missing expected answer entry")
            expected_logical_parameters = _expected_logical_parameters(expected_entry)
            if (
                expected_logical_parameters is not None
                and expected_logical_parameters != logical_parameters
            ):
                question["mismatches"].append(
                    "logical parameters differ: "
                    f"actual={logical_parameters!r}, expected={expected_logical_parameters!r}"
                )

            try:
                query_result = repository.run_competency_question(number, parameters)
            except Exception as exc:
                question["error"] = str(exc)
                question["mismatches"].append(f"query failed: {exc}")
            else:
                raw_columns = getattr(query_result, "columns", None)
                if isinstance(query_result, Mapping):
                    raw_columns = query_result.get("columns", raw_columns)
                source_columns = [str(column) for column in (raw_columns or [])]
                question["executed"] = True
                question["source_columns"] = source_columns
                normalized = normalize_query_result(
                    query_result,
                    backend=backend,
                    column_mapping=mapping,
                )
                actual_rows = normalized["rows"]
                question["columns"] = normalized["columns"]
                question["actual_row_count"] = len(actual_rows)
                # Normalization projects to the compared columns and dedupes. If that
                # collapses rows, the query fans out on an uncompared column (CQ03/CQ04
                # once returned one row per rdfs:label|skos:altLabel) and the set
                # comparison below would hide it.
                raw_row_count = len(getattr(query_result, "rows", None) or [])
                question["raw_row_count"] = raw_row_count
                if raw_row_count > len(actual_rows):
                    question["mismatches"].append(
                        f"{raw_row_count} raw rows collapse to {len(actual_rows)} "
                        "after normalization: fan-out on an uncompared column"
                    )
                question["rows"] = actual_rows
                missing_columns = normalized.get("missing_columns", [])
                if missing_columns:
                    question["mismatches"].append(
                        "missing mapped source columns: " + ", ".join(missing_columns)
                    )
                compared_columns = _parity_expected_columns(expected_entry, backend)
                if compared_columns and set(normalized["columns"]) != set(compared_columns):
                    question["mismatches"].append(
                        "compared columns differ: "
                        f"actual={normalized['columns']!r}, expected={compared_columns!r}"
                    )
                if not rows_equal(actual_rows, expected_rows):
                    question["mismatches"].append("normalized row set differs")

            question["match"] = bool(question["executed"] and not question["mismatches"])
            result["competency_questions"].append(question)
            if question["mismatches"]:
                result["mismatches"].append(
                    {
                        "number": key,
                        "messages": list(question["mismatches"]),
                    }
                )
            rows_artifact[key] = {
                "parameters": question["parameters"],
                "columns": question["columns"],
                "source_columns": question["source_columns"],
                "rows": question["rows"],
                "executed": question["executed"],
                "error": question["error"],
            }
        result["status"] = "completed"
    except BackendUnavailable as exc:
        result["status"] = (
            "skipped" if skip_neo4j_if_unavailable and backend == "neo4j" else "unavailable"
        )
        result["availability_error"] = str(exc)
        questions, rows_artifact = _parity_failure_records(str(exc), expected_answers)
        result["competency_questions"] = questions
        if result["status"] != "skipped":
            result["mismatches"] = [
                {"number": question["number"], "messages": question["mismatches"]}
                for question in questions
            ]
    except Exception as exc:
        result["status"] = "failed"
        result["fatal_error"] = str(exc)
        questions, rows_artifact = _parity_failure_records(str(exc), expected_answers)
        result["competency_questions"] = questions
        result["mismatches"] = [
            {"number": question["number"], "messages": question["mismatches"]}
            for question in questions
        ]
    finally:
        close = getattr(repository, "close", None)
        if callable(close):
            close()
    result["passed"] = result["status"] == "completed" and not result["mismatches"]
    return _json_safe(result), _json_safe(rows_artifact)


def _render_parity_report(payload: Mapping[str, Any]) -> str:
    """Render a compact, deterministic report for the parity run."""

    lines = [
        "# Competency-question parity",
        "",
        f"Expected answers: `{payload.get('expected_answers', 'unknown')}`",
        f"Overall result: **{'PASS' if payload.get('passed') else 'FAIL'}**",
        "",
        "| Backend | Status | Matching CQs | Mismatching CQs |",
        "|---|---|---:|---:|",
    ]
    backends = payload.get("backends", {})
    for backend in BACKENDS:
        result = backends.get(backend)
        if not isinstance(result, Mapping):
            lines.append(f"| {backend} | not requested | — | — |")
            continue
        questions = result.get("competency_questions", [])
        matching = sum(bool(question.get("match")) for question in questions)
        mismatching = sum(not bool(question.get("match")) for question in questions)
        if result.get("status") == "skipped":
            mismatching = 0
        lines.append(
            f"| {backend} | {result.get('status', 'unknown')} | {matching} | {mismatching} |"
        )

    lines.extend(["", "## Per-question results", ""])
    lines.extend(
        [
            "| CQ | Backend | Result | Actual rows | Expected rows | Details |",
            "|---|---|---|---:|---:|---|",
        ]
    )
    for backend in BACKENDS:
        result = backends.get(backend)
        if not isinstance(result, Mapping):
            continue
        for question in result.get("competency_questions", []):
            details = "; ".join(str(item) for item in question.get("mismatches", []))
            details = details.replace("|", "\\|").replace("\n", " ") or "—"
            status = "PASS" if question.get("match") else "FAIL"
            if result.get("status") == "skipped":
                status = "SKIP"
            lines.append(
                f"| {question.get('number')} | {backend} | {status} | "
                f"{question.get('actual_row_count', '—')} | "
                f"{question.get('expected_row_count', '—')} | {details} |"
            )
    lines.append("")
    return "\n".join(lines)


def run_parity(
    backend: str = "both",
    *,
    output_dir: str | Path | None = None,
    expected_answers_path: str | Path | None = None,
    skip_neo4j_if_unavailable: bool = False,
) -> dict[str, Any]:
    """Run all CQs against the oracle and write normalized parity artifacts."""

    if backend not in {"oxigraph", "neo4j", "both"}:
        raise ValueError("backend must be one of: oxigraph, neo4j, both")
    expected_path = Path(expected_answers_path) if expected_answers_path else EXPECTED_ANSWERS_PATH
    expected_answers = load_expected_answers(expected_path)
    requested = list(BACKENDS if backend == "both" else (backend,))
    target_dir = Path(output_dir) if output_dir is not None else PARITY_ROOT
    target_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, dict[str, Any]] = {}
    for name in requested:
        result, rows = _run_parity_backend(
            name,
            expected_answers,
            skip_neo4j_if_unavailable,
        )
        results[name] = result
        (target_dir / f"rows_{name}.json").write_text(
            json.dumps(rows, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    passed = all(
        result.get("passed") or (result.get("status") == "skipped" and skip_neo4j_if_unavailable)
        for result in results.values()
    )
    payload: dict[str, Any] = {
        "schema_version": "m0-parity-v1",
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "requested_backend": backend,
        "expected_answers": _relative_path(expected_path),
        "skip_neo4j_if_unavailable": skip_neo4j_if_unavailable,
        "passed": passed,
        "backends": results,
    }
    payload = _json_safe(payload)
    (target_dir / "parity_report.md").write_text(
        _render_parity_report(payload),
        encoding="utf-8",
    )
    return payload


def run_spike(
    backend: str = "both",
    *,
    output_dir: str | Path | None = None,
    skip_neo4j_if_unavailable: bool = False,
) -> dict[str, Any]:
    """Run one or both stores and write ``results.json`` and ``report.md``.

    The composer is called once per backend so every backend is loaded into a fresh
    repository instance.  The return value is the same JSON-compatible payload written
    to disk, which keeps tests independent of implementation details of the composer.
    """

    if backend not in {"oxigraph", "neo4j", "both"}:
        raise ValueError("backend must be one of: oxigraph, neo4j, both")
    requested = list(BACKENDS if backend == "both" else (backend,))
    results: dict[str, dict[str, Any]] = {}
    turtles: dict[str, str | None] = {}
    for name in requested:
        result, turtle = _run_backend(name, skip_neo4j_if_unavailable)
        results[name] = result
        turtles[name] = turtle

    _compare_exports(results, turtles)
    payload: dict[str, Any] = {
        "schema_version": "m0-spike-v1",
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "requested_backend": backend,
        "skip_neo4j_if_unavailable": skip_neo4j_if_unavailable,
        "backends": results,
    }
    payload = _json_safe(payload)
    target_dir = Path(output_dir) if output_dir is not None else SPIKE_ROOT
    target_dir.mkdir(parents=True, exist_ok=True)
    results_path = target_dir / "results.json"
    report_path = target_dir / "report.md"
    results_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(payload), encoding="utf-8")
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("oxigraph", "neo4j", "both"), default="both")
    parser.add_argument(
        "--parity",
        action="store_true",
        help="run all competency questions against eval/golden/expected_answers.json",
    )
    parser.add_argument(
        "--expected-answers",
        type=Path,
        help="override the expected-answer oracle path (primarily useful for tests)",
    )
    parser.add_argument(
        "--skip-neo4j-if-unavailable",
        action="store_true",
        help="record Neo4j as skipped when NEO4J_URI or connectivity is unavailable",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.parity:
        try:
            payload = run_parity(
                args.backend,
                expected_answers_path=args.expected_answers,
                skip_neo4j_if_unavailable=args.skip_neo4j_if_unavailable,
            )
        except (FileNotFoundError, ValueError) as exc:
            print(f"parity failed: {exc}", file=sys.stderr)
            return 2
        print(f"wrote {(PARITY_ROOT / 'parity_report.md')}")
        for name in BACKENDS:
            if name == args.backend or args.backend == "both":
                print(f"wrote {PARITY_ROOT / f'rows_{name}.json'}")
        return 0 if payload.get("passed") else 1
    payload = run_spike(
        args.backend,
        skip_neo4j_if_unavailable=args.skip_neo4j_if_unavailable,
    )
    print(f"wrote {SPIKE_ROOT / 'results.json'}")
    print(f"wrote {SPIKE_ROOT / 'report.md'}")
    required = payload["backends"]
    if any(result.get("status") == "unavailable" for result in required.values()):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
