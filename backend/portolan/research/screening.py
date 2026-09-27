"""Deterministic candidate screening used by the M1 research run."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "this",
        "to",
        "using",
        "with",
    }
)


class Screener(Protocol):
    """Minimal screening interface used by :class:`ResearchRunner`."""

    def _cocitation_score(self, candidate: Mapping[str, Any], context: Mapping[str, Any]) -> float:
        counts = context.get("core_reference_counts")
        if not isinstance(counts, Mapping) or not counts:
            return 0.0
        try:
            core_size = int(context.get("core_size") or 0)
        except (TypeError, ValueError):
            core_size = 0
        if core_size <= 0:
            return 0.0
        count = 0
        for kind, value in _identifier_keys(candidate):
            try:
                count = max(count, int(counts.get(f"{kind}:{value}") or 0))
            except (TypeError, ValueError):
                continue
        return max(0.0, min(1.0, count / core_size))

    def score(self, candidate: Mapping[str, Any], context: Mapping[str, Any]) -> float:
        """Return a relevance score between zero and one."""


def _value(mapping: Mapping[str, Any], key: str) -> Any:
    value = mapping.get(key)
    if value is not None:
        return value
    identifiers = mapping.get("identifiers")
    if isinstance(identifiers, Mapping):
        return identifiers.get(key)
    return None


def _normalise_identifier(kind: str, value: Any) -> tuple[str, str] | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if kind == "openalex":
        if "openalex.org/" in text.lower():
            text = unquote(urlsplit(text).path).rstrip("/").split("/")[-1]
        text = text.removeprefix("openalex:")
        if text.upper().startswith("W"):
            return "openalex", text.upper()
    elif kind == "doi":
        lowered = text.casefold()
        if lowered.startswith("doi:"):
            text = text[4:].strip()
        elif lowered.startswith(
            ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/")
        ):
            text = unquote(urlsplit(text).path).lstrip("/")
        if not re.match(r"^10\.\d{4,9}/\S+$", text, flags=re.IGNORECASE):
            return None
        return "doi", text.rstrip(".").casefold()
    elif kind == "arxiv":
        if "arxiv.org/" in text.casefold():
            text = unquote(urlsplit(text).path).strip("/").split("/", 1)[-1]
        text = text.removeprefix("arxiv:")
        if not re.match(
            r"^(?:\d{4}\.\d{4,5}(?:v\d+)?|[a-z][a-z0-9-]+(?:\.[a-z0-9-]+)?/\S+)$",
            text,
            re.I,
        ):
            return None
        text = re.sub(r"v\d+$", "", text, flags=re.IGNORECASE)
        return "arxiv", text.casefold()
    return None


def _identifier_keys(record: Mapping[str, Any]) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    identifiers = record.get("identifiers")
    identifiers = identifiers if isinstance(identifiers, Mapping) else {}
    for kind in ("openalex", "doi", "arxiv", "s2"):
        value = record.get(f"{kind}_id")
        if value is None:
            value = identifiers.get(kind)
        if kind == "s2" and value is None:
            value = record.get("paperId") or identifiers.get("paperId")
        if kind == "s2":
            if value:
                result.add((kind, str(value).strip().casefold()))
            continue
        if (key := _normalise_identifier(kind, value)) is not None:
            result.add(key)
    return result


def _keys_from_value(value: Any) -> set[tuple[str, str]]:
    if isinstance(value, Mapping):
        return _identifier_keys(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        result: set[tuple[str, str]] = set()
        for item in value:
            result.update(_keys_from_value(item))
        return result
    text = str(value).strip() if value is not None else ""
    if not text:
        return set()
    for kind in ("openalex", "doi", "arxiv"):
        if (key := _normalise_identifier(kind, text)) is not None:
            return {key}
    return {("openalex", text.upper())} if text.upper().startswith("W") else set()


def _stem(token: str) -> str:
    if len(token) > 5:
        for suffix in ("ingly", "edly", "ing", "ed", "es"):
            if token.endswith(suffix) and len(token) - len(suffix) >= 4:
                return token[: -len(suffix)]
    if len(token) > 4 and token.endswith("s"):
        return token[:-1]
    return token


def _tokens(text: str) -> set[str]:
    return {
        _stem(token)
        for token in _TOKEN_RE.findall(text.casefold())
        if token not in _STOP_WORDS and len(token) > 1
    }


def _record_text(record: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in ("title", "abstract"):
        value = record.get(key)
        if value:
            parts.append(str(value))
    keywords = record.get("keywords")
    if isinstance(keywords, Sequence) and not isinstance(keywords, (str, bytes)):
        for keyword in keywords:
            if isinstance(keyword, Mapping):
                value = keyword.get("term") or keyword.get("display_name")
            else:
                value = keyword
            if value:
                parts.append(str(value))
    return " ".join(parts)


def _records(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [item for item in value if isinstance(item, Mapping)]
    return []


class HeuristicScreener:
    """Score candidates with text, link, co-citation, and citation-count signals.

    The co-citation signal counts how many core-set works (seeds plus the top
    search hits) reference the candidate.  The runner supplies these counts in
    the screening context as ``core_reference_counts`` (``"kind:value"``
    identifier -> count) together with ``core_size``.

    Callers that pass any of the original three weights explicitly, but not
    ``cocitation_weight``, keep the original three-signal score.
    """

    def __init__(
        self,
        *,
        token_weight: float | None = None,
        link_weight: float | None = None,
        citation_weight: float | None = None,
        cocitation_weight: float | None = None,
    ) -> None:
        legacy = cocitation_weight is None and any(
            weight is not None for weight in (token_weight, link_weight, citation_weight)
        )
        # (token, link, citation count, co-citation)
        defaults = (0.6, 0.25, 0.15, 0.0) if legacy else (0.45, 0.2, 0.10, 0.25)
        weights = tuple(
            float(default if weight is None else weight)
            for weight, default in zip(
                (token_weight, link_weight, citation_weight, cocitation_weight),
                defaults,
                strict=True,
            )
        )
        if any(weight < 0 for weight in weights) or sum(weights) <= 0:
            raise ValueError("screener weights must be non-negative and have a positive sum")
        total = sum(weights)
        (
            self.token_weight,
            self.link_weight,
            self.citation_weight,
            self.cocitation_weight,
        ) = (weight / total for weight in weights)

    def _profile_tokens(self, context: Mapping[str, Any]) -> set[str]:
        parts: list[str] = []
        profile = context.get("profile")
        if profile:
            parts.append(str(profile))
        query = context.get("query")
        if query:
            parts.append(str(query))
        for record in _records(context.get("seed_records")):
            parts.append(_record_text(record))
        return _tokens(" ".join(parts))

    def _link_score(self, candidate: Mapping[str, Any], context: Mapping[str, Any]) -> float:
        known_keys: set[tuple[str, str]] = set()
        known_keys.update(_keys_from_value(context.get("seed_ids")))
        known_keys.update(_keys_from_value(context.get("included_ids")))
        source_records = [
            *_records(context.get("seed_records")),
            *_records(context.get("included_records")),
        ]
        for record in source_records:
            known_keys.update(_identifier_keys(record))
        if not known_keys:
            return 0.0

        candidate_keys = _identifier_keys(candidate)
        referenced = set()
        values = candidate.get("referenced_works")
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
            for value in values:
                referenced.update(_keys_from_value(value))
        if referenced & known_keys:
            return 1.0
        for record in source_records:
            values = record.get("referenced_works")
            if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
                continue
            source_references: set[tuple[str, str]] = set()
            for value in values:
                source_references.update(_keys_from_value(value))
            if source_references & candidate_keys:
                return 1.0
        return 0.0

    def _citation_score(self, candidate: Mapping[str, Any], context: Mapping[str, Any]) -> float:
        value = candidate.get("cited_by_count")
        try:
            count = max(0.0, float(value or 0))
        except (TypeError, ValueError):
            count = 0.0
        maximum = context.get("max_cited_by_count")
        if maximum is None:
            pool = [
                item.get("cited_by_count")
                for item in _records(context.get("candidates") or context.get("candidate_pool"))
            ]
            numbers: list[float] = []
            for item in pool:
                try:
                    numbers.append(max(0.0, float(item or 0)))
                except (TypeError, ValueError):
                    continue
            maximum = max(numbers, default=0.0)
        try:
            maximum_value = max(0.0, float(maximum or 0))
        except (TypeError, ValueError):
            maximum_value = 0.0
        if maximum_value <= 0:
            return 0.0
        return min(1.0, math.log1p(count) / math.log1p(maximum_value))

    def _cocitation_score(self, candidate: Mapping[str, Any], context: Mapping[str, Any]) -> float:
        counts = context.get("core_reference_counts")
        if not isinstance(counts, Mapping) or not counts:
            return 0.0
        try:
            core_size = int(context.get("core_size") or 0)
        except (TypeError, ValueError):
            core_size = 0
        if core_size <= 0:
            return 0.0
        count = 0
        for kind, value in _identifier_keys(candidate):
            try:
                count = max(count, int(counts.get(f"{kind}:{value}") or 0))
            except (TypeError, ValueError):
                continue
        return max(0.0, min(1.0, count / core_size))

    def score(self, candidate: Mapping[str, Any], context: Mapping[str, Any]) -> float:
        """Return a deterministic score in ``[0, 1]`` for one candidate."""

        candidate_tokens = _tokens(_record_text(candidate))
        profile_tokens = self._profile_tokens(context)
        if candidate_tokens and profile_tokens:
            token_score = len(candidate_tokens & profile_tokens) / len(
                candidate_tokens | profile_tokens
            )
        else:
            token_score = 0.0
        score = (
            self.token_weight * token_score
            + self.link_weight * self._link_score(candidate, context)
            + self.citation_weight * self._citation_score(candidate, context)
            + self.cocitation_weight * self._cocitation_score(candidate, context)
        )
        return max(0.0, min(1.0, float(score)))


__all__ = ["HeuristicScreener", "Screener"]
