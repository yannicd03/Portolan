"""SHACL validation at the repository boundary.

The shapes file is resolved at runtime from ``ontology/shapes.ttl`` relative
to the repository/package, so importing :mod:`portolan.store` never requires the
ontology artifacts to have been generated yet.  A Neo4j backend cannot hand
pySHACL a native property graph; the M0 spike therefore validates the backend
by exporting its selected subgraph to Turtle and passing that RDF export to
this module.  This is an intentional export indirection, not a second set of
Neo4j validation rules.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .repository import ValidationIssue, ValidationReport

type PathLike = str | Path


_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SHAPES_PATH = _REPO_ROOT / "ontology" / "shapes.ttl"


def resolve_shapes_path(shapes_path: PathLike | None = None) -> Path:
    """Resolve the SHACL file without requiring it to exist at import time.

    An explicit path wins.  Otherwise the repository-relative location is
    preferred, followed by the current directory and its immediate parent;
    the latter makes an editable ``cd backend`` checkout work as expected.
    The first candidate is returned even when no candidate exists so callers
    can report a useful path in a structured validation result.
    """

    if shapes_path is not None:
        return Path(shapes_path).expanduser().resolve()

    candidates = (
        DEFAULT_SHAPES_PATH,
        Path.cwd() / "ontology" / "shapes.ttl",
        Path.cwd().parent / "ontology" / "shapes.ttl",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return DEFAULT_SHAPES_PATH


def _skipped_report(path: Path, message: str, *, error: str | None = None) -> ValidationReport:
    return ValidationReport(
        conforms=None,
        shapes_path=str(path),
        skipped=True,
        error=error,
        report_text=message,
    )


def _term_text(term: Any) -> str | None:
    if term is None:
        return None
    return str(term)


def _literal_value(term: Any) -> Any:
    if term is None:
        return None
    to_python = getattr(term, "toPython", None)
    if callable(to_python):
        try:
            return to_python()
        except Exception:  # pragma: no cover - defensive for custom RDF terms
            pass
    return str(term)


def _issue_from_result(result_graph: Any, result: Any) -> ValidationIssue:
    from rdflib.namespace import SH

    def first(predicate: Any) -> Any:
        return next(iter(result_graph.objects(result, predicate)), None)

    return ValidationIssue(
        message=str(first(SH.resultMessage) or "SHACL validation result"),
        focus_node=_term_text(first(SH.focusNode)),
        result_path=_term_text(first(SH.resultPath)),
        severity=_term_text(first(SH.resultSeverity)),
        source_shape=_term_text(first(SH.sourceShape)),
        source_constraint_component=_term_text(first(SH.sourceConstraintComponent)),
        value=_literal_value(first(SH.value)),
    )


def _issues_from_report(result_graph: Any) -> tuple[list[ValidationIssue], list[ValidationIssue]]:
    from rdflib import RDF
    from rdflib.namespace import SH

    violations: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    for result in result_graph.subjects(RDF.type, SH.ValidationResult):
        issue = _issue_from_result(result_graph, result)
        severity = issue.severity or ""
        if severity.endswith("Warning") or severity.endswith("Info"):
            warnings.append(issue)
        else:
            violations.append(issue)
    return violations, warnings


def _as_rdf_source(data_graph: Any, data_graph_format: str) -> Any:
    """Coerce common in-memory graph forms to a pySHACL-compatible source."""

    from rdflib import ConjunctiveGraph, Graph

    if isinstance(data_graph, (Graph, ConjunctiveGraph)):
        return data_graph
    if isinstance(data_graph, Path):
        return str(data_graph)
    if isinstance(data_graph, str):
        candidate = Path(data_graph)
        try:
            is_file = candidate.is_file()
        except OSError:
            is_file = False
        if is_file:
            return str(candidate)
        graph = ConjunctiveGraph()
        graph.parse(data=data_graph, format=data_graph_format)
        return graph
    if isinstance(data_graph, (bytes, bytearray)):
        graph = ConjunctiveGraph()
        graph.parse(data=bytes(data_graph), format=data_graph_format)
        return graph

    # pyoxigraph's Store exposes ``dump`` in current releases.  Keeping this
    # duck-typed avoids importing pyoxigraph from a store-neutral module.
    for method_name in ("dump", "serialize"):
        method = getattr(data_graph, method_name, None)
        if not callable(method):
            continue
        try:
            serialized = method(format="text/turtle")
        except TypeError:
            serialized = method()
        if isinstance(serialized, bytes):
            serialized = serialized.decode("utf-8")
        graph = ConjunctiveGraph()
        graph.parse(data=str(serialized), format="turtle")
        return graph

    raise TypeError(
        "data_graph must be an RDFLib graph, a path, Turtle text/bytes, "
        "or an object exposing dump()/serialize()"
    )


def validate_graph(
    data_graph: Any,
    *,
    shapes_path: PathLike | None = None,
    data_graph_format: str = "turtle",
    advanced: bool = True,
    meta_shacl: bool = False,
) -> ValidationReport:
    """Validate an RDF graph or Turtle export against ``ontology/shapes.ttl``.

    Missing ontology artifacts and an unavailable optional validator are
    represented as ``skipped=True`` reports.  A pySHACL execution or parse
    error is returned as a non-conforming report with ``error`` populated, so
    callers receive a structured result rather than an import-time failure.
    """

    resolved_shapes = resolve_shapes_path(shapes_path)
    if not resolved_shapes.is_file():
        return _skipped_report(
            resolved_shapes,
            f"SHACL shapes file is not available: {resolved_shapes}",
        )

    try:
        from pyshacl import validate as pyshacl_validate
    except ImportError as exc:  # pragma: no cover - dependency is pinned in backend
        return _skipped_report(
            resolved_shapes,
            f"pyshacl is not installed; validation skipped: {exc}",
            error=str(exc),
        )

    try:
        source = _as_rdf_source(data_graph, data_graph_format)
        conforms, result_graph, result_text = pyshacl_validate(
            source,
            shacl_graph=str(resolved_shapes),
            data_graph_format=data_graph_format,
            advanced=advanced,
            meta_shacl=meta_shacl,
            abort_on_first=False,
            allow_infos=True,
            allow_warnings=True,
        )
        violations, warnings = _issues_from_report(result_graph)
    except Exception as exc:  # pySHACL can raise on malformed RDF/shapes
        return ValidationReport(
            conforms=False,
            shapes_path=str(resolved_shapes),
            skipped=False,
            error=str(exc),
            report_text=f"SHACL validation failed: {exc}",
        )

    return ValidationReport(
        conforms=bool(conforms),
        violations=violations,
        warnings=warnings,
        report_text=str(result_text),
        shapes_path=str(resolved_shapes),
        report_graph=result_graph,
    )


def validate(
    data_graph: Any,
    *,
    shapes_path: PathLike | None = None,
    data_graph_format: str = "turtle",
    advanced: bool = True,
    meta_shacl: bool = False,
) -> ValidationReport:
    """Short alias for :func:`validate_graph` used by backend implementations."""

    return validate_graph(
        data_graph,
        shapes_path=shapes_path,
        data_graph_format=data_graph_format,
        advanced=advanced,
        meta_shacl=meta_shacl,
    )


__all__ = [
    "DEFAULT_SHAPES_PATH",
    "PathLike",
    "ValidationIssue",
    "ValidationReport",
    "resolve_shapes_path",
    "validate",
    "validate_graph",
]
