"""Canonical row and parameter normalization for competency-question parity.

The two repository bindings intentionally expose their native result column names and
their native parameter representation.  Parity compares a third representation: a
canonical row is a JSON-safe mapping whose keys come from the explicit per-CQ column
mapping in ``expected_answers.json``.  Values are normalized without relying on row
order, driver-specific numeric classes, or the RDF/LPG review-id representation.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any

DEFAULT_REVIEW_IRI_PREFIX = "https://w3id.org/portolan/id/review/"
_DECIMAL_PLACES = Decimal("0.000001")
_DATETIME_RE = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})T"
    r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
    r"(?:\.(?P<fraction>\d+))?"
    r"(?P<zone>Z|[+-]\d{2}:\d{2}(?::\d{2})?)?$"
)
_REVIEW_FIELD_NAMES = frozenset({"review", "reviewid", "reviewiri", "reviewidentifier"})
_PARAMETER_ALIASES = {
    "review_iri": "review",
    "review_id": "review",
    "reviewId": "review",
    "seed_work_iri": "seed",
    "seedWorkIri": "seed",
    "problem_iri": "problem",
    "problemIri": "problem",
    "assertion_iri": "assertion",
    "assertionIri": "assertion",
    "excluded_work_iri": "work",
    "excludedWorkIri": "work",
    "cq13_work": "work",
    "similarity_threshold": "similarityThreshold",
}
_BACKEND_ALIASES = {
    "oxigraph": ("oxigraph", "sparql", "rdf"),
    "neo4j": ("neo4j", "cypher", "lpg"),
}


def _field_token(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").casefold())


def _is_review_field(name: str | None) -> bool:
    return _field_token(name) in _REVIEW_FIELD_NAMES


def _object_value(value: Any) -> Any:
    """Unwrap the common IRI/enum wrappers used by the repository models and drivers."""

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (str, bytes, bytearray, Mapping, Sequence)):
        return value
    iri = getattr(value, "iri", None)
    if iri is not None:
        return iri() if callable(iri) else iri
    wrapped = getattr(value, "value", None)
    if wrapped is not None and wrapped is not value:
        return wrapped
    return value


def review_iri(value: Any, *, prefix: str = DEFAULT_REVIEW_IRI_PREFIX) -> str:
    """Return a review identifier in the canonical full-IRI representation.

    ``kgqa-rag`` and ``review/kgqa-rag`` are both accepted because the Cypher binding
    and older harness metadata use the bare partition id.  Other absolute IRIs are
    left alone rather than being guessed into the review namespace.
    """

    value = _object_value(value)
    text = str(value).strip()
    if not text:
        return text
    normalized_prefix = prefix.rstrip("/") + "/"
    if text.startswith(normalized_prefix):
        return text
    if text.startswith("review/"):
        return normalized_prefix + text.removeprefix("review/")
    if text.startswith(("http://", "https://", "urn:")):
        return text
    return normalized_prefix + text.removeprefix("/")


def review_id(value: Any, *, prefix: str = DEFAULT_REVIEW_IRI_PREFIX) -> str:
    """Return a review identifier in the canonical Neo4j bare-id representation."""

    text = review_iri(value, prefix=prefix)
    normalized_prefix = prefix.rstrip("/") + "/"
    if text.startswith(normalized_prefix):
        return text.removeprefix(normalized_prefix)
    return text


def _round_number(value: int | float | Decimal) -> int | float:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return str(value)  # type: ignore[return-value]
        rounded = round(value, 6)
        return 0.0 if rounded == 0 else rounded
    try:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
        if not decimal_value.is_finite():
            return str(decimal_value)  # type: ignore[return-value]
        rounded_decimal = decimal_value.quantize(_DECIMAL_PLACES, rounding=ROUND_HALF_EVEN)
        rounded = float(rounded_decimal)
        return 0.0 if rounded == 0 else rounded
    except (InvalidOperation, ValueError, OverflowError):
        rounded = round(float(value), 6)
        return 0.0 if rounded == 0 else rounded


def _datetime_text(value: Any) -> str | None:
    """Return an ISO-like datetime spelling for native and driver datetime values."""

    if isinstance(value, datetime):
        return value.isoformat()
    value_type = type(value)
    if value_type.__module__ == "neo4j.time" and value_type.__name__ == "DateTime":
        formatter = getattr(value, "iso_format", None)
        if callable(formatter):
            return str(formatter())
    if isinstance(value, str):
        return str(value)
    return None


def _canonical_datetime(value: Any) -> str | None:
    """Canonicalize ISO, RDF dateTime, and Neo4j datetime values to UTC."""

    text = _datetime_text(value)
    if text is None:
        return None
    match = _DATETIME_RE.fullmatch(text)
    if match is None:
        return None

    fraction_text = match.group("fraction") or ""
    nanoseconds = int(fraction_text[:9].ljust(9, "0")) if fraction_text else 0
    local = datetime(
        int(match.group("year")),
        int(match.group("month")),
        int(match.group("day")),
        int(match.group("hour")),
        int(match.group("minute")),
        int(match.group("second")),
        nanoseconds // 1_000,
    )

    zone = match.group("zone")
    if zone and zone != "Z":
        offset = timedelta(
            hours=int(zone[1:3]),
            minutes=int(zone[4:6]),
            seconds=int(zone[7:9]) if len(zone) == 9 else 0,
        )
        if zone[0] == "-":
            offset = -offset
    else:
        offset = timedelta(0)
    utc = (local - offset).replace(tzinfo=UTC)

    fraction = f"{utc.microsecond * 1_000 + nanoseconds % 1_000:09d}".rstrip("0")
    fractional_suffix = f".{fraction}" if fraction else ""
    return f"{utc:%Y-%m-%dT%H:%M:%S}{fractional_suffix}Z"


def normalize_value(value: Any, *, field_name: str | None = None) -> Any:
    """Convert one result value to a deterministic JSON-safe value."""

    value = _object_value(value)
    canonical_datetime = _canonical_datetime(value)
    if canonical_datetime is not None:
        return canonical_datetime
    if _field_token(field_name) == "limitationtexts" and value is not None:
        if isinstance(value, str):
            parts = value.split(" | ")
        elif isinstance(value, (list, tuple, set, frozenset)):
            parts = [str(normalize_value(item)) for item in value]
        else:
            parts = [str(normalize_value(value))]
        return " | ".join(sorted(parts))
    if value is None or isinstance(value, (str, bool)):
        if _is_review_field(field_name) and isinstance(value, str):
            return review_iri(value)
        return value
    if isinstance(value, (int, float, Decimal)):
        return _round_number(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, Mapping):
        return {
            str(key): normalize_value(item, field_name=str(key))
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [normalize_value(item, field_name=field_name) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized = [normalize_value(item, field_name=field_name) for item in value]
        return sorted(normalized, key=_json_key)
    return str(value)


def _json_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _query_result_parts(result: Any) -> tuple[list[str], list[Any]]:
    """Extract columns and rows from QueryResult-like objects or JSON envelopes."""

    if isinstance(result, Mapping) and "rows" in result:
        columns = result.get("columns", [])
        rows = result.get("rows", [])
    else:
        columns = getattr(result, "columns", [])
        rows = getattr(result, "rows", None)
        if rows is None:
            rows = result if isinstance(result, (list, tuple)) else [result]
    if columns is None:
        columns = []
    if rows is None:
        rows = []
    return [str(column) for column in columns], list(rows)


def _mapping_for_backend(mapping: Mapping[str, Any] | None, backend: str | None) -> dict[str, str]:
    if not isinstance(mapping, Mapping):
        return {}

    selected: Mapping[str, Any] = mapping
    for wrapper in ("canonical_to_backend", "bindings", "by_backend"):
        candidate = mapping.get(wrapper)
        if isinstance(candidate, Mapping):
            selected = candidate
            break

    if backend:
        for alias in _BACKEND_ALIASES.get(backend, (backend,)):
            candidate = selected.get(alias)
            if isinstance(candidate, Mapping):
                selected = candidate
                break

    resolved: dict[str, str] = {}
    for canonical, source in selected.items():
        canonical_name = str(canonical)
        if isinstance(source, Mapping):
            chosen = None
            if backend:
                for alias in _BACKEND_ALIASES.get(backend, (backend,)):
                    if source.get(alias) is not None:
                        chosen = source[alias]
                        break
            if chosen is None:
                for name in ("column", "source", "name", "canonical"):
                    if source.get(name) is not None:
                        chosen = source[name]
                        break
            if chosen is None:
                continue
            source = chosen
        if isinstance(source, str):
            resolved[canonical_name] = source
    return resolved


def resolve_column_mapping(
    column_mapping: Mapping[str, Any] | None, backend: str | None = None
) -> dict[str, str]:
    """Resolve a canonical-to-binding column mapping for one backend.

    The preferred shape is ``{"canonicalName": {"oxigraph": "sparqlName", ...}}``.
    A backend-specific map such as ``{"oxigraph": {"canonicalName": "sparqlName"}}``
    and a direct canonical-to-source map are accepted for small test fixtures.
    """

    return _mapping_for_backend(column_mapping, backend)


def normalize_rows(
    rows_or_result: Any,
    *,
    columns: Sequence[str] | None = None,
    backend: str | None = None,
    column_mapping: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Normalize a row set, applying mappings and removing binding duplicates."""

    result_columns, raw_rows = _query_result_parts(rows_or_result)
    if columns is not None:
        result_columns = [str(column) for column in columns]
    mapping = resolve_column_mapping(column_mapping, backend)
    normalized_rows: dict[str, dict[str, Any]] = {}
    for raw_row in raw_rows:
        if isinstance(raw_row, Mapping):
            row = {str(key): value for key, value in raw_row.items()}
        elif isinstance(raw_row, Sequence) and not isinstance(raw_row, (str, bytes, bytearray)):
            row = dict(zip(result_columns, raw_row, strict=False))
        else:
            row = {"value": raw_row}

        if mapping:
            normalized = {
                canonical: normalize_value(row[source], field_name=canonical)
                for canonical, source in mapping.items()
                if source in row
            }
        else:
            normalized = {
                key: normalize_value(value, field_name=key) for key, value in sorted(row.items())
            }
        normalized_rows[_json_key(normalized)] = normalized
    return sorted(normalized_rows.values(), key=_json_key)


def normalize_query_result(
    result: Any,
    *,
    backend: str | None = None,
    column_mapping: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize a QueryResult and retain enough shape information for parity reports."""

    columns, _ = _query_result_parts(result)
    mapping = resolve_column_mapping(column_mapping, backend)
    normalized_rows = normalize_rows(
        result,
        backend=backend,
        column_mapping=column_mapping,
    )
    if mapping:
        compared_columns = list(mapping)
        missing_columns = sorted(set(mapping.values()) - set(columns)) if columns else []
    else:
        compared_columns = columns or sorted({key for row in normalized_rows for key in row})
        missing_columns = []
    return {
        "columns": compared_columns,
        "rows": normalized_rows,
        "missing_columns": missing_columns,
    }


def _canonical_parameter_name(name: str) -> str:
    return _PARAMETER_ALIASES.get(name, name)


def normalize_parameters(parameters: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize logical query parameters, expanding aliases and review ids."""

    if not isinstance(parameters, Mapping):
        return {}
    normalized = normalize_value(dict(parameters))
    if not isinstance(normalized, Mapping):
        return {}
    return {_canonical_parameter_name(str(name)): value for name, value in normalized.items()}


def _convert_review_parameter(value: Any, backend: str) -> Any:
    if isinstance(value, list):
        return [_convert_review_parameter(item, backend) for item in value]
    if isinstance(value, tuple):
        return [_convert_review_parameter(item, backend) for item in value]
    if backend == "neo4j":
        return review_id(value)
    return review_iri(value)


def parameters_for_backend(
    parameters: Mapping[str, Any] | None,
    backend: str,
) -> dict[str, Any]:
    """Convert one logical parameter table to the requested binding representation."""

    if backend not in {"oxigraph", "neo4j"}:
        raise ValueError("backend must be 'oxigraph' or 'neo4j'")
    logical = normalize_parameters(parameters)
    converted: dict[str, Any] = {}
    for name, value in logical.items():
        canonical_name = _canonical_parameter_name(str(name))
        if _is_review_field(canonical_name):
            value = _convert_review_parameter(value, backend)
        converted[canonical_name] = value
    return converted


# A short alias makes the one conversion point explicit at call sites and keeps the
# terminology readable for code that treats parameters as binding-specific values.
binding_parameters = parameters_for_backend


def _cq_key(value: Any) -> str:
    text = str(value)
    suffix = text[2:] if text.casefold().startswith("cq") else text
    try:
        return f"CQ{int(suffix):02d}"
    except ValueError:
        return text


def load_expected_answers(path: str | Path) -> dict[str, dict[str, Any]]:
    """Load the oracle and normalize its top-level CQ key shape.

    The committed oracle is a mapping of ``CQ01`` ... ``CQ14``.  ``questions`` and
    ``answers`` wrappers, or a list of entries carrying a ``number`` field, are accepted
    so the helper remains usable while the oracle is authored.
    """

    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if isinstance(payload, Mapping):
        for wrapper in ("questions", "answers", "expected_answers", "competency_questions"):
            candidate = payload.get(wrapper)
            if isinstance(candidate, (Mapping, list)):
                payload = candidate
                break

    entries: dict[str, dict[str, Any]] = {}
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            if not isinstance(value, Mapping):
                continue
            normalized_key = _cq_key(key)
            if normalized_key.casefold().startswith("cq"):
                entries[normalized_key] = dict(value)
    elif isinstance(payload, list):
        for value in payload:
            if not isinstance(value, Mapping):
                continue
            number = value.get("number", value.get("cq", value.get("id")))
            if number is not None:
                entries[_cq_key(number)] = dict(value)
    if not entries:
        raise ValueError(f"expected answers file has no CQ entries: {source}")
    return dict(sorted(entries.items()))


def _entry_payload(entry: Mapping[str, Any]) -> Mapping[str, Any]:
    for wrapper in ("expected", "expected_result", "result", "answer"):
        candidate = entry.get(wrapper)
        if isinstance(candidate, Mapping):
            return candidate
    return entry


def expected_answer_rows(entry: Mapping[str, Any]) -> list[Any]:
    """Return the expected row list from one oracle entry."""

    payload = _entry_payload(entry)
    for source in (entry, payload):
        for key in ("rows", "expected_rows", "result_rows", "expected"):
            rows = source.get(key)
            if isinstance(rows, list):
                return rows
    return []


def expected_column_mapping(entry: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the explicit per-CQ column mapping from one oracle entry."""

    payload = _entry_payload(entry)
    for source in (entry, payload):
        for key in ("column_mapping", "column_mappings", "columns"):
            mapping = source.get(key)
            if isinstance(mapping, Mapping):
                return mapping
    return {}


def expected_parameters(entry: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Return logical parameters from one oracle entry, or ``None`` if absent."""

    payload = _entry_payload(entry)
    for source in (entry, payload):
        for key in ("parameters", "params"):
            parameters = source.get(key)
            if isinstance(parameters, Mapping):
                return parameters
    return None


def expected_columns(entry: Mapping[str, Any]) -> list[str]:
    """Return explicitly compared canonical columns when the oracle supplies them."""

    payload = _entry_payload(entry)
    for source in (entry, payload):
        columns = source.get("compared_columns", source.get("columns_compared"))
        if isinstance(columns, list):
            return [str(column) for column in columns]
    for source in (entry, payload):
        columns = source.get("columns")
        if isinstance(columns, list):
            return [str(column) for column in columns]
    return []


def rows_equal(left: Sequence[Mapping[str, Any]], right: Sequence[Mapping[str, Any]]) -> bool:
    """Compare already-normalized row sets without depending on source order."""

    return {_json_key(dict(row)) for row in left} == {_json_key(dict(row)) for row in right}


__all__ = [
    "DEFAULT_REVIEW_IRI_PREFIX",
    "binding_parameters",
    "expected_answer_rows",
    "expected_column_mapping",
    "expected_columns",
    "expected_parameters",
    "load_expected_answers",
    "normalize_parameters",
    "normalize_query_result",
    "normalize_rows",
    "normalize_value",
    "parameters_for_backend",
    "resolve_column_mapping",
    "review_id",
    "review_iri",
    "rows_equal",
]
