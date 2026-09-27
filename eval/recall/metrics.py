"""Pure identity matching and recall metrics for the survey evaluation.

The research sources expose several spellings for the same work.  This module
keeps the evaluation independent of either a source adapter or a graph backend
by accepting mappings, graph models, and identifier strings.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import unquote, urlsplit

_DOI_RE = re.compile(r"10\.\d{4,9}/\S+", re.IGNORECASE)
_OPENALEX_RE = re.compile(r"W\d+", re.IGNORECASE)
_ARXIV_RE = re.compile(
    r"(?:\d{4}\.\d{4,5}(?:v\d+)?|[a-z][a-z0-9-]+(?:\.[a-z0-9-]+)?/\S+)",
    re.IGNORECASE,
)


def _get(record: Any, key: str) -> Any:
    if isinstance(record, Mapping):
        return record.get(key)
    return getattr(record, key, None)


def _nested_identifiers(record: Any) -> Mapping[str, Any]:
    identifiers = _get(record, "identifiers")
    return identifiers if isinstance(identifiers, Mapping) else {}


def _values(record: Any, *names: str) -> list[Any]:
    identifiers = _nested_identifiers(record)
    values: list[Any] = []
    for name in names:
        value = _get(record, name)
        if value is not None:
            values.append(value)
        value = identifiers.get(name)
        if value is not None:
            values.append(value)
    return values


def _normalise_openalex(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if "openalex.org/" in text.casefold():
        text = unquote(urlsplit(text).path).rstrip("/").split("/")[-1]
    if text.casefold().startswith("openalex:"):
        text = text.split(":", 1)[1].strip()
    match = _OPENALEX_RE.fullmatch(text)
    return match.group(0).upper() if match else None


def _normalise_doi(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    lowered = text.casefold()
    if lowered.startswith("doi:"):
        text = text[4:].strip()
    elif lowered.startswith(
        ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/")
    ):
        text = unquote(urlsplit(text).path).lstrip("/")
    text = text.rstrip(".")
    return text.casefold() if _DOI_RE.fullmatch(text) else None


def _normalise_arxiv(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if "arxiv.org/" in text.casefold():
        path = unquote(urlsplit(text).path).strip("/")
        parts = path.split("/", 1)
        text = (
            parts[1] if len(parts) == 2 and parts[0].casefold() in {"abs", "pdf", "html"} else path
        )
    if text.casefold().startswith("arxiv:"):
        text = text.split(":", 1)[1].strip()
    text = text.split("?", 1)[0].split("#", 1)[0].removesuffix(".pdf")
    text = re.sub(r"v\d+$", "", text, flags=re.IGNORECASE)
    match = _ARXIV_RE.fullmatch(text)
    return match.group(0).casefold() if match else None


def _arxiv_from_doi(value: str | None) -> str | None:
    if value is None:
        return None
    match = re.fullmatch(r"10\.48550/arxiv\.(.+)", value, flags=re.IGNORECASE)
    return _normalise_arxiv(match.group(1)) if match else None


def identity_keys(record: Any) -> frozenset[tuple[str, str]]:
    """Return normalized OpenAlex, DOI, and arXiv identities for ``record``.

    A record can carry more than one identity.  Keeping all of them is what
    lets the evaluator match an arXiv-only candidate to a DOI-bearing ground
    truth record.
    """

    values = [record] if isinstance(record, str) else []

    keys: set[tuple[str, str]] = set()
    for value in [*_values(record, "openalex_id", "openalex", "openalexId"), *values]:
        normalized = _normalise_openalex(value)
        if normalized:
            keys.add(("openalex", normalized))
    doi_values = [
        *_values(record, "doi", "doi_id", "doiId"),
    ]
    arxiv_values = [
        *_values(record, "arxiv_id", "arxiv", "arxivId"),
    ]
    if values:
        doi_values.extend(values)
        arxiv_values.extend(values)
    for container_name in ("externalIds", "external_ids"):
        external = _get(record, container_name)
        if isinstance(external, Mapping):
            doi_values.extend(external.get(name) for name in ("DOI", "doi"))
            arxiv_values.extend(external.get(name) for name in ("ArXiv", "arxiv"))
    for value in doi_values:
        normalized = _normalise_doi(value)
        if normalized:
            keys.add(("doi", normalized))
            if (arxiv := _arxiv_from_doi(normalized)) is not None:
                keys.add(("arxiv", arxiv))
    for value in arxiv_values:
        normalized = _normalise_arxiv(value)
        if normalized:
            keys.add(("arxiv", normalized))

    # Some source records only expose their OpenAlex work URL under ``id``.
    # Restrict this fallback to an unmistakable OpenAlex or DOI/arXiv value so
    # a Semantic Scholar paperId is never mistaken for an OpenAlex id.
    raw_id = _get(record, "id")
    if raw_id is not None and (normalized := _normalise_openalex(raw_id)) is not None:
        keys.add(("openalex", normalized))

    return frozenset(keys)


def shares_identity(left: Any, right: Any) -> bool:
    """Return whether two records share any supported work identity."""

    return bool(identity_keys(left) & identity_keys(right))


def _identity_groups(records: Iterable[Any]) -> list[list[Any]]:
    """Group records connected by a shared identity, preserving input order."""

    groups: list[list[Any]] = []
    group_keys: list[set[tuple[str, str]]] = []
    for record in records:
        keys = set(identity_keys(record))
        matching = [index for index, known in enumerate(group_keys) if keys & known]
        if not matching:
            groups.append([record])
            group_keys.append(keys)
            continue
        first = matching[0]
        groups[first].append(record)
        group_keys[first].update(keys)
        # A record can bridge two previously separate identifiers.  Merge all
        # such groups to keep set cardinalities correct.
        for index in reversed(matching[1:]):
            groups[first].extend(groups.pop(index))
            group_keys[first].update(group_keys.pop(index))
    return groups


def _group_keys(groups: Sequence[Sequence[Any]]) -> list[set[tuple[str, str]]]:
    return [set().union(*(identity_keys(record) for record in group)) for group in groups]


@dataclass(frozen=True, slots=True)
class RecallMetrics:
    """Set-based recall and precision values for one survey."""

    ground_truth_count: int
    candidates_count: int
    included_count: int
    candidates_matched: int
    included_matched: int
    recall_candidates: float
    recall_included: float
    precision_included: float
    missed_references: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""

        return asdict(self)


def _title(record: Any) -> str:
    value = _get(record, "title")
    if value is None:
        value = _get(record, "display_name")
    return str(value or "").strip()


def _record_detail(record: Any) -> dict[str, Any]:
    return {
        "title": _title(record),
        "identities": [f"{kind}:{value}" for kind, value in sorted(identity_keys(record))],
    }


def compute_metrics(
    ground_truth: Iterable[Any],
    candidates: Iterable[Any],
    included: Iterable[Any],
    *,
    excluded: Iterable[Any] = (),
    missed_limit: int = 50,
) -> RecallMetrics:
    """Compute candidate/included recall and included precision.

    ``excluded`` is optional and is useful for callers that want the evaluator
    to enforce a survey exclusion independently of the runner.  All supported
    identities on an excluded record are removed from every input before the
    set operations.
    """

    excluded_keys = set().union(*(identity_keys(record) for record in excluded))

    def keep(record: Any) -> bool:
        return not (identity_keys(record) & excluded_keys)

    truth_groups = _identity_groups(record for record in ground_truth if keep(record))
    candidate_groups = _identity_groups(record for record in candidates if keep(record))
    included_groups = _identity_groups(record for record in included if keep(record))
    truth_keys = _group_keys(truth_groups)
    candidate_keys = _group_keys(candidate_groups)
    included_keys = _group_keys(included_groups)

    matched_truth_by_candidates = {
        index
        for index, keys in enumerate(truth_keys)
        if any(keys & candidate for candidate in candidate_keys)
    }
    matched_truth_by_included = {
        index
        for index, keys in enumerate(truth_keys)
        if any(keys & included_record for included_record in included_keys)
    }
    matched_candidates = {
        index
        for index, keys in enumerate(candidate_keys)
        if any(keys & truth for truth in truth_keys)
    }
    matched_included = {
        index
        for index, keys in enumerate(included_keys)
        if any(keys & truth for truth in truth_keys)
    }
    missed = [
        _record_detail(group[0])
        for index, group in enumerate(truth_groups)
        if index not in matched_truth_by_candidates
    ][: max(0, missed_limit)]

    truth_count = len(truth_groups)
    candidate_count = len(candidate_groups)
    included_count = len(included_groups)
    return RecallMetrics(
        ground_truth_count=truth_count,
        candidates_count=candidate_count,
        included_count=included_count,
        candidates_matched=len(matched_candidates),
        included_matched=len(matched_included),
        recall_candidates=(len(matched_truth_by_candidates) / truth_count if truth_count else 0.0),
        recall_included=(len(matched_truth_by_included) / truth_count if truth_count else 0.0),
        precision_included=(len(matched_included) / included_count if included_count else 0.0),
        missed_references=tuple(missed),
    )


# A descriptive alias keeps the function convenient for callers that think in
# terms of an evaluation rather than a metric bundle.
evaluate_recall = compute_metrics


__all__ = [
    "RecallMetrics",
    "compute_metrics",
    "evaluate_recall",
    "identity_keys",
    "shares_identity",
]
