"""Durable user state and external verification for structural gap hypotheses.

Gap detection is deliberately recomputable.  The JSON files managed here keep
the small amount of state that belongs to the user (status, note, and the most
recent verification) together with a snapshot of each computed gap.  Keeping a
snapshot lets a gap remain visible as ``stale`` when a later graph analysis no
longer emits it.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from collections.abc import Iterable, Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

GapStatus = Literal["proposed", "accepted", "rejected"]

# ``None`` is a valid PATCH value for note and verification.  A separate
# sentinel is therefore needed to tell an omitted value from an explicit null.
UNSET = object()

_PROJECT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_GAP_STATUSES = frozenset({"proposed", "accepted", "rejected"})


def _now() -> datetime:
    return datetime.now(UTC)


def _validate_project_id(project_id: str) -> str:
    """Validate ids before using them as a file name.

    Project ids are graph slugs.  This mirrors the validation used by the chat
    store and prevents a project id from escaping the gaps directory.
    """

    if not isinstance(project_id, str) or _PROJECT_ID.fullmatch(project_id) is None:
        raise ValueError("project_id must be a path-safe identifier")
    return project_id


def _gap_model() -> type[Any]:
    """Import the gap model lazily to keep this module importable in isolation."""

    # ``gaps.py`` owns the model because detection and persistence share its
    # public shape.  A lazy import also avoids an import cycle if the analysis
    # package is imported while modules are being collected by pytest.
    from .gaps import GapHypothesis

    return GapHypothesis


def _model_dump(value: Any) -> dict[str, Any]:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            result = dump(mode="json")
        except TypeError:  # pragma: no cover - compatibility with older Pydantic shims
            result = dump()
        if isinstance(result, dict):
            return dict(result)
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError("gap must be a Pydantic model or mapping")


def _validate_gap(payload: Any) -> Any:
    model = _gap_model()
    validator = getattr(model, "model_validate", None)
    if callable(validator):
        return validator(payload)
    return model(**payload)


def _gap_id(gap: Any) -> str:
    value = getattr(gap, "id", None)
    if value is None and isinstance(gap, Mapping):
        value = gap.get("id")
    if not isinstance(value, str) or not value:
        raise ValueError("a gap must have a non-empty id")
    return value


def _type_value(value: Any) -> str:
    value = getattr(value, "value", value)
    return str(value).casefold()


def _clean_terms(gap: Any) -> list[str]:
    raw = getattr(gap, "search_terms", None)
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raw = []
    terms: list[str] = []
    seen: set[str] = set()
    for value in raw:
        text = str(value).strip()
        if not text:
            continue
        key = text.casefold()
        if key not in seen:
            terms.append(text)
            seen.add(key)
    if terms:
        return terms

    # This fallback keeps verification useful for hand-built hypotheses from
    # callers that do not populate the convenience field.  Detectors populate
    # search_terms explicitly, so normal API calls retain their intended terms.
    statement = getattr(gap, "statement", "")
    return [str(statement).strip()] if str(statement).strip() else []


def _field(gap: Any, name: str, default: Any = None) -> Any:
    value = getattr(gap, name, default)
    if value is not default:
        return value
    if isinstance(gap, Mapping):
        return gap.get(name, default)
    return default


def _record_value(record: Any, *names: str) -> Any:
    for name in names:
        value = record.get(name) if isinstance(record, Mapping) else getattr(record, name, None)
        if value is not None:
            return value
    return None


def _record_identifiers(record: Any) -> list[str]:
    """Return every identifier of a search record, OpenAlex id first."""

    values: list[Any] = [_record_value(record, "openalex_id", "id", "work_id", "doi")]
    identifiers = _record_value(record, "identifiers")
    if isinstance(identifiers, Mapping):
        values.extend(identifiers.get(key) for key in ("openalex", "doi", "arxiv"))
    values.extend(_record_value(record, name) for name in ("doi", "arxiv_id"))
    result: list[str] = []
    for value in values:
        text = str(value).strip() if value is not None else ""
        if text and text not in result:
            result.append(text)
    return result


def _identifier_variants(value: Any) -> set[str]:
    """Return comparable spellings of an OpenAlex id, DOI, or arXiv id."""

    if value is None:
        return set()
    text = str(value).strip().casefold().rstrip("/")
    if not text:
        return set()
    variants = {text}
    for prefix in (
        "https://openalex.org/",
        "http://openalex.org/",
        "openalex:",
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "doi:",
        "arxiv:",
    ):
        if text.startswith(prefix):
            variants.add(text[len(prefix) :])
    return variants


def _as_year(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _search_results(value: Any) -> list[Any]:
    if isinstance(value, Mapping):
        results = value.get("results")
        return list(results) if isinstance(results, list) else []
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        return list(value)
    return []


def _query_for_gap(gap: Any) -> str:
    return " ".join(_clean_terms(gap))


def _verification_result(
    *,
    query: str,
    checked_at: str,
    sampled: int,
    outside_hits: list[dict[str, Any]],
    verdict: Literal["likely_filled", "possibly_open", "unknown"],
    error: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "query": query,
        "checked_at": checked_at,
        "total_hits_sampled": sampled,
        "outside_hits": outside_hits,
        "verdict": verdict,
    }
    if error:
        result["error"] = error
    return result


def verify_gap(
    gap: Any,
    sources: Any,
    *,
    limit: int = 5,
    project_identifiers: Iterable[str] = (),
    newest_year: int | None = None,
) -> dict[str, Any]:
    """Search OpenAlex for works outside the project that may fill ``gap``.

    ``project_identifiers`` are the OpenAlex ids, DOIs, and arXiv ids of the
    project's works; hits matching any of them are not "outside".  The verdict
    is ``likely_filled`` when at least three outside hits are newer than
    ``newest_year - 1`` (the project's newest year).

    Verification is intentionally best effort.  Adapter, transport, and fake
    source failures are converted into an ``unknown`` result so the API can
    persist the attempted check without turning a network problem into a 500.
    """

    query = _query_for_gap(gap)
    checked_at = _now().isoformat()
    sample_limit = max(0, int(limit))
    if not query:
        return _verification_result(
            query=query,
            checked_at=checked_at,
            sampled=0,
            outside_hits=[],
            verdict="unknown",
            error="gap has no search terms",
        )

    adapter = getattr(sources, "openalex", None)
    search = getattr(adapter, "search", None)
    if not callable(search):
        return _verification_result(
            query=query,
            checked_at=checked_at,
            sampled=0,
            outside_hits=[],
            verdict="unknown",
            error="sources object has no OpenAlex search method",
        )

    search_kwargs: dict[str, Any] = {"limit": sample_limit}
    if _type_value(_field(gap, "type", "")) == "stagnation" and newest_year is not None:
        # "The last two years" include the project's newest year; later works
        # are exactly the evidence that the topic is still active elsewhere.
        search_kwargs["from_year"] = newest_year - 1

    try:
        results = _search_results(search(query, **search_kwargs))[:sample_limit]
    except Exception as exc:  # network failures must never escape this helper
        message = str(exc).strip() or type(exc).__name__
        return _verification_result(
            query=query,
            checked_at=checked_at,
            sampled=0,
            outside_hits=[],
            verdict="unknown",
            error=message,
        )

    inside: set[str] = set()
    for identifier in project_identifiers:
        inside.update(_identifier_variants(identifier))

    outside_hits: list[dict[str, Any]] = []
    for record in results:
        identifiers = _record_identifiers(record)
        if any(inside & _identifier_variants(identifier) for identifier in identifiers):
            continue
        outside_hits.append(
            {
                "id": identifiers[0] if identifiers else "",
                "title": str(_record_value(record, "title", "display_name") or ""),
                "year": _as_year(_record_value(record, "year", "publication_year")),
            }
        )

    recent_outside = 0
    if newest_year is not None:
        recent_outside = sum(
            1 for hit in outside_hits if hit["year"] is not None and hit["year"] > newest_year - 1
        )
    verdict: Literal["likely_filled", "possibly_open"] = (
        "likely_filled" if recent_outside >= 3 else "possibly_open"
    )
    return _verification_result(
        query=query,
        checked_at=checked_at,
        sampled=len(results),
        outside_hits=outside_hits,
        verdict=verdict,
    )


class GapStore:
    """Thread-safe, atomic JSON persistence for computed gap hypotheses."""

    def __init__(self, root: Path | str) -> None:
        # Callers pass ``settings.data_dir / "gaps"``; one JSON file per project.
        self.root = Path(root)
        self._lock = threading.RLock()

    def _path(self, project_id: str) -> Path:
        return self.root / f"{_validate_project_id(project_id)}.json"

    @staticmethod
    def _write_atomic(path: Path, records: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(records, indent=2, ensure_ascii=False, sort_keys=True).encode("utf-8")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            with suppress(FileNotFoundError):
                temporary.unlink()

    def _load(self, project_id: str) -> dict[str, dict[str, Any]]:
        path = self._path(project_id)
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
            return {}
        if isinstance(payload, Mapping) and isinstance(payload.get("gaps"), Mapping):
            payload = payload["gaps"]
        if not isinstance(payload, Mapping):
            return {}
        records: dict[str, dict[str, Any]] = {}
        for key, value in payload.items():
            if not isinstance(key, str) or not isinstance(value, Mapping):
                continue
            records[key] = dict(value)
        return records

    @staticmethod
    def _snapshot(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
        for key in ("snapshot", "computed", "gap"):
            value = record.get(key)
            if isinstance(value, Mapping):
                return value
        # Accept a directly serialized gap as a small compatibility aid for
        # hand-edited files and early development snapshots.
        if "id" in record and "statement" in record:
            return record
        return None

    @staticmethod
    def _stored_fields(record: Mapping[str, Any]) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        status = record.get("status")
        if isinstance(status, str) and status in _GAP_STATUSES:
            fields["status"] = status
        note = record.get("note")
        if "note" in record and (note is None or isinstance(note, str)):
            fields["note"] = note
        verification = record.get("verification")
        if "verification" in record and (verification is None or isinstance(verification, Mapping)):
            fields["verification"] = verification
        return fields

    def _record_for_gap(self, gap: Any, *, stale: bool, record: Mapping[str, Any]) -> Any:
        payload = dict(_model_dump(gap))
        payload.update(self._stored_fields(record))
        # GapHypothesis in the analysis module includes stale.  Keeping this
        # conditional makes old snapshots readable while that model evolves.
        model = _gap_model()
        fields = getattr(model, "model_fields", {})
        if "stale" in fields or "stale" in payload:
            payload["stale"] = stale
        return _validate_gap(payload)

    @staticmethod
    def _record_from_gap(gap: Any, *, previous: Mapping[str, Any] | None = None) -> dict[str, Any]:
        previous = previous or {}
        stored = GapStore._stored_fields(previous)
        record: dict[str, Any] = {
            "snapshot": _model_dump(gap),
        }
        for name in ("status", "note", "verification"):
            if name in stored:
                record[name] = stored[name]
            elif name in record["snapshot"]:
                # User fields are persisted separately so future recomputations
                # can overlay them even when the detector changes other fields.
                record[name] = record["snapshot"][name]
        if isinstance(previous.get("updated_at"), str):
            record["updated_at"] = previous["updated_at"]
        return record

    def _merge_locked(self, project_id: str, computed: Iterable[Any]) -> list[Any]:
        incoming: list[Any] = list(computed)
        records = self._load(project_id)
        computed_by_id: dict[str, Any] = {}
        computed_order: list[str] = []
        for gap in incoming:
            gap_id = _gap_id(gap)
            if gap_id not in computed_by_id:
                computed_order.append(gap_id)
            computed_by_id[gap_id] = gap

        merged: list[Any] = []
        next_records: dict[str, dict[str, Any]] = {}
        for gap_id in computed_order:
            gap = computed_by_id[gap_id]
            previous = records.get(gap_id, {})
            merged_gap = self._record_for_gap(gap, stale=False, record=previous)
            merged.append(merged_gap)
            next_records[gap_id] = self._record_from_gap(gap, previous=previous)

        # Keep snapshots that disappeared from the latest analysis.  They are
        # appended after current results and sorted by id for reproducibility.
        for gap_id in sorted(set(records) - set(computed_by_id)):
            previous = records[gap_id]
            snapshot = self._snapshot(previous)
            if snapshot is None:
                continue
            try:
                stale_gap = _validate_gap(dict(snapshot))
            except (TypeError, ValueError):
                continue
            stale_gap = self._record_for_gap(stale_gap, stale=True, record=previous)
            merged.append(stale_gap)
            next_records[gap_id] = dict(previous)

        if next_records or records:
            self._write_atomic(self._path(project_id), next_records)
        return merged

    def merge(self, project_id: str, computed: Iterable[Any]) -> list[Any]:
        """Overlay persisted user fields onto freshly computed hypotheses."""

        project_id = _validate_project_id(project_id)
        with self._lock:
            return self._merge_locked(project_id, computed)

    def update(
        self,
        project_id: str,
        gap_id: str,
        computed: Iterable[Any],
        *,
        status: GapStatus | None = None,
        note: str | None | object = UNSET,
        verification: Mapping[str, Any] | None | object = UNSET,
    ) -> Any | None:
        """Update user-owned fields for one current or stale gap.

        ``None`` clears ``note`` or ``verification``.  Omitting a keyword keeps
        the stored value, which is why the latter two parameters use ``UNSET``.
        """

        project_id = _validate_project_id(project_id)
        if not isinstance(gap_id, str) or not gap_id:
            return None
        if status is not None:
            status_value = str(getattr(status, "value", status))
            if status_value not in _GAP_STATUSES:
                raise ValueError("status must be one of: proposed, accepted, rejected")
        if note is not UNSET and note is not None and not isinstance(note, str):
            raise ValueError("note must be a string or null")
        if (
            verification is not UNSET
            and verification is not None
            and not isinstance(verification, Mapping)
        ):
            raise ValueError("verification must be an object or null")

        with self._lock:
            merged = self._merge_locked(project_id, computed)
            selected = next((gap for gap in merged if _gap_id(gap) == gap_id), None)
            if selected is None:
                return None
            records = self._load(project_id)
            record = dict(records.get(gap_id, {}))
            if status is not None:
                record["status"] = str(getattr(status, "value", status))
            if note is not UNSET:
                record["note"] = note
            if verification is not UNSET:
                # Round-trip through JSON so the in-memory result and file have
                # the same ordinary Python shape and no mutable alias escapes.
                try:
                    record["verification"] = json.loads(json.dumps(verification))
                except (TypeError, ValueError) as exc:
                    raise ValueError("verification must be JSON serializable") from exc
            if status is not None or note is not UNSET or verification is not UNSET:
                record["updated_at"] = _now().isoformat()
            records[gap_id] = record
            self._write_atomic(self._path(project_id), records)
            return self._record_for_gap(
                selected,
                stale=bool(getattr(selected, "stale", False)),
                record=record,
            )


__all__ = ["GapStatus", "GapStore", "UNSET", "verify_gap"]
