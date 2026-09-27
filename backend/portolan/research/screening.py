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


def _term_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for token in _TOKEN_RE.findall(text.casefold()):
        if token in _STOP_WORDS or len(token) <= 1:
            continue
        term = _stem(token)
        counts[term] = counts.get(term, 0) + 1
    return counts


def _semantic_text(record: Mapping[str, Any]) -> str:
    return " ".join(str(record[key]) for key in ("title", "abstract") if record.get(key))


def _unit(vector: Mapping[str, float]) -> dict[str, float]:
    norm = math.sqrt(sum(value * value for value in vector.values()))
    if norm <= 0:
        return {}
    return {term: value / norm for term, value in vector.items()}


def _reference_key_sets(record: Mapping[str, Any]) -> list[frozenset[tuple[str, str]]]:
    """Return one identity set per distinct reference of ``record``."""

    values = record.get("referenced_works")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    result: dict[frozenset[tuple[str, str]], None] = {}
    for value in values:
        keys = frozenset(_keys_from_value(value))
        if keys:
            result[keys] = None
    return list(result)


def _year(record: Mapping[str, Any]) -> int | None:
    value = record.get("year")
    if value is None:
        value = record.get("publication_year")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _reference_keys(record: Mapping[str, Any]) -> set[tuple[str, str]]:
    """Every identity key of every reference of ``record``."""

    result: set[tuple[str, str]] = set()
    values = record.get("referenced_works")
    if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
        for value in values:
            result.update(_keys_from_value(value))
    return result


class _LinkSources:
    """Identity and reference keys of the seed and included works (``link``).

    Both sets only grow as works are included, so a greedy selection can keep
    one instance and add each inclusion instead of rebuilding it per score.
    """

    def __init__(self, context: Mapping[str, Any]) -> None:
        self.known: set[tuple[str, str]] = set()
        self.references: set[tuple[str, str]] = set()
        self.add_ids(context.get("seed_ids"))
        self.add_ids(context.get("included_ids"))
        for record in [
            *_records(context.get("seed_records")),
            *_records(context.get("included_records")),
        ]:
            self.add_record(record)

    def add_ids(self, value: Any) -> None:
        self.known.update(_keys_from_value(value))

    def add_record(self, record: Mapping[str, Any]) -> None:
        self.known.update(_identifier_keys(record))
        self.references.update(_reference_keys(record))

    def link(self, candidate_keys: set[tuple[str, str]], referenced: set[tuple[str, str]]) -> float:
        """1.0 if the candidate cites, or is cited by, a known work."""

        if not self.known:
            return 0.0
        if not referenced.isdisjoint(self.known):
            return 1.0
        if not self.references.isdisjoint(candidate_keys):
            return 1.0
        return 0.0


class _PoolState:
    """Per-context values that do not change while one pool is screened.

    The runner scores every remaining candidate once per greedy step, each time
    with a fresh context dict built from the same base context.  The objects in
    that base context (candidate pool, seed and core records, core counts) are
    shared between the steps, so they identify the pool.  The state holds
    references to them, which keeps the identity comparison sound.
    """

    def __init__(self, context: Mapping[str, Any]) -> None:
        self.pool = context.get("candidates") or context.get("candidate_pool")
        self.seed_records = context.get("seed_records")
        self.core_records = context.get("core_records")
        self.counts = context.get("core_reference_counts")
        self.profile = context.get("profile")
        self.query = context.get("query")
        self._idf: dict[str, float] | None = None
        self._pool_size = 0
        self._profile_vector: dict[str, float] | None = None
        self._vectors: dict[int, tuple[Mapping[str, Any], dict[str, float]]] = {}
        self._references: dict[
            int, tuple[Mapping[str, Any], list[frozenset[tuple[str, str]]], bool]
        ] = {}
        self._cocitation_max: int | None = None
        self._core_counts: dict[tuple[str, str], int] | None = None
        self._core_keys: set[tuple[str, str]] = set()

    def matches(self, context: Mapping[str, Any]) -> bool:
        return (
            (context.get("candidates") or context.get("candidate_pool")) is self.pool
            and context.get("seed_records") is self.seed_records
            and context.get("core_records") is self.core_records
            and context.get("core_reference_counts") is self.counts
            and context.get("profile") == self.profile
            and context.get("query") == self.query
        )

    def _literature(self) -> list[Mapping[str, Any]]:
        records: dict[int, Mapping[str, Any]] = {}
        for record in [*_records(self.seed_records), *_records(self.core_records)]:
            records.setdefault(id(record), record)
        return list(records.values())

    def idf(self) -> dict[str, float]:
        if self._idf is None:
            frequencies: dict[str, int] = {}
            pool = _records(self.pool)
            for record in pool:
                for term in _term_counts(_semantic_text(record)):
                    frequencies[term] = frequencies.get(term, 0) + 1
            self._pool_size = len(pool)
            self._idf = {
                term: math.log((1 + self._pool_size) / (1 + count)) + 1.0
                for term, count in frequencies.items()
            }
        return self._idf

    def vector(self, text: str) -> dict[str, float]:
        idf = self.idf()
        unseen = math.log(1 + self._pool_size) + 1.0
        return _unit(
            {
                term: (1.0 + math.log(count)) * idf.get(term, unseen)
                for term, count in _term_counts(text).items()
            }
        )

    def record_vector(self, record: Mapping[str, Any]) -> dict[str, float]:
        cached = self._vectors.get(id(record))
        if cached is not None and cached[0] is record:
            return cached[1]
        vector = self.vector(_semantic_text(record))
        self._vectors[id(record)] = (record, vector)
        return vector

    def coupling_inputs(
        self, record: Mapping[str, Any]
    ) -> tuple[list[frozenset[tuple[str, str]]], bool]:
        """The record's reference identity sets and whether it is a core work."""

        cached = self._references.get(id(record))
        if cached is not None and cached[0] is record:
            return cached[1], cached[2]
        references = _reference_key_sets(record)
        is_core = bool(references) and bool(_identifier_keys(record) & self.core_keys())
        self._references[id(record)] = (record, references, is_core)
        return references, is_core

    def profile_vector(self) -> dict[str, float]:
        """Unit profile: half query, half the centroid of seed and core texts."""

        if self._profile_vector is None:
            texts = [str(text) for text in dict.fromkeys((self.profile, self.query)) if text]
            query_vector = self.vector(" ".join(texts))
            centroid: dict[str, float] = {}
            for record in self._literature():
                for term, value in self.record_vector(record).items():
                    centroid[term] = centroid.get(term, 0.0) + value
            centroid = _unit(centroid)
            combined = dict(query_vector)
            for term, value in centroid.items():
                combined[term] = combined.get(term, 0.0) + value
            self._profile_vector = _unit(combined)
        return self._profile_vector

    def cocitation_max(self) -> int:
        """Largest co-citation count among non-seed identities in the pool."""

        if self._cocitation_max is None:
            counts = self.counts if isinstance(self.counts, Mapping) else {}
            seed_keys = {
                f"{kind}:{value}"
                for record in _records(self.seed_records)
                for kind, value in _identifier_keys(record)
            }
            numbers: list[int] = []
            fallback: list[int] = []
            for key, value in counts.items():
                try:
                    number = int(value or 0)
                except (TypeError, ValueError):
                    continue
                fallback.append(number)
                if str(key) not in seed_keys:
                    numbers.append(number)
            self._cocitation_max = max(numbers or fallback, default=0)
        return self._cocitation_max

    def core_reference_counts(self) -> dict[tuple[str, str], int]:
        """Per reference identity, the number of core works that cite it."""

        if self._core_counts is None:
            counts: dict[tuple[str, str], int] = {}
            for record in self._literature():
                self._core_keys.update(_identifier_keys(record))
                keys: set[tuple[str, str]] = set()
                for reference in _reference_key_sets(record):
                    keys.update(reference)
                for key in keys:
                    counts[key] = counts.get(key, 0) + 1
            self._core_counts = counts
        return self._core_counts

    def core_keys(self) -> set[tuple[str, str]]:
        self.core_reference_counts()
        return self._core_keys


COMPONENTS = ("token", "semantic", "link", "citation", "cocitation", "coupling")
"""Additive score components, in the order their weights are normalised."""

DEFAULT_WEIGHTS: dict[str, float] = {
    "token": 0.0,
    "semantic": 0.4,
    "link": 0.15,
    "citation": 0.05,
    "cocitation": 0.25,
    "coupling": 0.15,
}
"""Default component weights (before normalisation); see eval/recall/README.md."""

_LEGACY_WEIGHTS = {"token": 0.45, "link": 0.2, "citation": 0.10, "cocitation": 0.25}
_LEGACY_THREE_WEIGHTS = {"token": 0.6, "link": 0.25, "citation": 0.15, "cocitation": 0.0}
_COCITATION_NORMS = ("pool_max", "core_size")


class HeuristicScreener:
    """Score candidates with text, link, co-citation, coupling and citation signals.

    Components, each in ``[0, 1]`` and weighted by a non-negative weight (the
    weights of the additive components are normalised to sum to one):

    - ``semantic``: TF-IDF cosine (sublinear tf, smoothed idf over the candidate
      pool) between the candidate's title and abstract and a profile made of the
      query plus the seed and core-set titles and abstracts;
    - ``token``: Jaccard token overlap with the query and seed texts (the
      original text signal, off by default);
    - ``link``: the candidate cites, or is cited by, a seed or included work;
    - ``citation``: log-scaled citation count relative to the pool maximum;
    - ``cocitation``: how many core-set works (seeds plus top search hits)
      reference the candidate, from ``core_reference_counts``, divided by the
      largest non-seed count in the pool (``cocitation_norm="pool_max"``) or by
      ``core_size`` (``"core_size"``, the original normalisation);
    - ``coupling``: the share of the candidate's references that at least two
      core works also reference (bibliographic coupling).

    ``recency_weight`` is a penalty rather than a component: it is subtracted,
    unnormalised, from the score of works newer than the context's ``to_year``.

    Callers that pass only the original weights (``token``, ``link``,
    ``citation``, ``cocitation``) keep the original score: the unspecified
    original weights take their original defaults, the new components are off
    and co-citation is normalised by the core size.  Passing only the first
    three keeps the original three-signal score.
    """

    def __init__(
        self,
        *,
        token_weight: float | None = None,
        link_weight: float | None = None,
        citation_weight: float | None = None,
        cocitation_weight: float | None = None,
        semantic_weight: float | None = None,
        coupling_weight: float | None = None,
        recency_weight: float | None = None,
        cocitation_norm: str | None = None,
    ) -> None:
        given = {
            "token": token_weight,
            "link": link_weight,
            "citation": citation_weight,
            "cocitation": cocitation_weight,
            "semantic": semantic_weight,
            "coupling": coupling_weight,
        }
        legacy = (
            semantic_weight is None
            and coupling_weight is None
            and recency_weight is None
            and any(value is not None for value in given.values())
        )
        if legacy:
            original = _LEGACY_THREE_WEIGHTS if cocitation_weight is None else _LEGACY_WEIGHTS
            defaults = {name: original.get(name, 0.0) for name in COMPONENTS}
        else:
            defaults = dict(DEFAULT_WEIGHTS)
        weights: dict[str, float] = {}
        for name in COMPONENTS:
            value = given[name]
            weights[name] = float(defaults[name] if value is None else value)
        if any(weight < 0 for weight in weights.values()) or sum(weights.values()) <= 0:
            raise ValueError("screener weights must be non-negative and have a positive sum")
        recency = float(recency_weight or 0.0)
        if not 0.0 <= recency <= 1.0:
            raise ValueError("recency_weight must be between zero and one")
        norm = cocitation_norm or ("core_size" if legacy else "pool_max")
        if norm not in _COCITATION_NORMS:
            raise ValueError(f"cocitation_norm must be one of {', '.join(_COCITATION_NORMS)}")
        total = sum(weights.values())
        self.token_weight = weights["token"] / total
        self.semantic_weight = weights["semantic"] / total
        self.link_weight = weights["link"] / total
        self.citation_weight = weights["citation"] / total
        self.cocitation_weight = weights["cocitation"] / total
        self.coupling_weight = weights["coupling"] / total
        self.recency_weight = recency
        self.cocitation_norm = norm
        self._state: _PoolState | None = None

    @property
    def weights(self) -> dict[str, float]:
        """Normalised component weights plus the recency penalty."""

        return {
            **{name: getattr(self, f"{name}_weight") for name in COMPONENTS},
            "recency": self.recency_weight,
        }

    def _pool_state(self, context: Mapping[str, Any]) -> _PoolState:
        state = self._state
        if state is None or not state.matches(context):
            state = _PoolState(context)
            self._state = state
        return state

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

    def _token_score(self, candidate: Mapping[str, Any], context: Mapping[str, Any]) -> float:
        candidate_tokens = _tokens(_record_text(candidate))
        profile_tokens = self._profile_tokens(context)
        if candidate_tokens and profile_tokens:
            return len(candidate_tokens & profile_tokens) / len(candidate_tokens | profile_tokens)
        return 0.0

    def _semantic_score(self, candidate: Mapping[str, Any], context: Mapping[str, Any]) -> float:
        state = self._pool_state(context)
        profile = state.profile_vector()
        if not profile:
            return 0.0
        vector = state.record_vector(candidate)
        similarity = sum(value * profile.get(term, 0.0) for term, value in vector.items())
        return max(0.0, min(1.0, similarity))

    def _link_score(self, candidate: Mapping[str, Any], context: Mapping[str, Any]) -> float:
        return _LinkSources(context).link(_identifier_keys(candidate), _reference_keys(candidate))

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
        if self.cocitation_norm == "core_size":
            try:
                denominator = int(context.get("core_size") or 0)
            except (TypeError, ValueError):
                denominator = 0
        else:
            denominator = self._pool_state(context).cocitation_max()
        if denominator <= 0:
            return 0.0
        count = 0
        for kind, value in _identifier_keys(candidate):
            try:
                count = max(count, int(counts.get(f"{kind}:{value}") or 0))
            except (TypeError, ValueError):
                continue
        return max(0.0, min(1.0, count / denominator))

    def _coupling_score(self, candidate: Mapping[str, Any], context: Mapping[str, Any]) -> float:
        state = self._pool_state(context)
        references, is_core = state.coupling_inputs(candidate)
        if not references:
            return 0.0
        counts = state.core_reference_counts()
        if not counts:
            return 0.0
        # A core work's own references are in the counts: require two other core works.
        threshold = 3 if is_core else 2
        shared = sum(
            1 for keys in references if max(counts.get(key, 0) for key in keys) >= threshold
        )
        return shared / len(references)

    def _recency_penalty(self, candidate: Mapping[str, Any], context: Mapping[str, Any]) -> float:
        try:
            to_year = int(context["to_year"]) if context.get("to_year") is not None else None
        except (TypeError, ValueError):
            to_year = None
        year = _year(candidate)
        return 1.0 if to_year is not None and year is not None and year > to_year else 0.0

    def _component(
        self, name: str, candidate: Mapping[str, Any], context: Mapping[str, Any]
    ) -> float:
        method = getattr(self, "_recency_penalty" if name == "recency" else f"_{name}_score")
        return float(method(candidate, context))

    def selection(self, context: Mapping[str, Any]) -> ScreeningSelection:
        """Return an incremental scorer for one greedy selection from ``context``."""

        return ScreeningSelection(self, context)

    def components(
        self, candidate: Mapping[str, Any], context: Mapping[str, Any]
    ) -> dict[str, float]:
        """Return every component value (and the recency penalty) for one candidate."""

        return {
            name: self._component(name, candidate, context) for name in (*COMPONENTS, "recency")
        }

    def combine(self, components: Mapping[str, float]) -> float:
        """Combine component values into a score in ``[0, 1]``."""

        def value(name: str) -> float:
            return float(components.get(name, 0.0))

        score = (
            self.token_weight * value("token")
            + self.link_weight * value("link")
            + self.citation_weight * value("citation")
            + self.cocitation_weight * value("cocitation")
            + self.semantic_weight * value("semantic")
            + self.coupling_weight * value("coupling")
            - self.recency_weight * value("recency")
        )
        return max(0.0, min(1.0, float(score)))

    def score(self, candidate: Mapping[str, Any], context: Mapping[str, Any]) -> float:
        """Return a deterministic score in ``[0, 1]`` for one candidate."""

        weights = self.weights
        return self.combine(
            {
                name: self._component(name, candidate, context)
                for name in (*COMPONENTS, "recency")
                if weights[name] > 0
            }
        )


class ScreeningSelection:
    """Incremental scorer for one greedy selection over a fixed candidate pool.

    ``score(record)`` equals ``screener.score(record, step_context)`` where
    ``step_context`` is the starting context with every work passed to
    :meth:`include` added to ``included_records`` and its identifiers to
    ``included_ids``.  Only ``link`` depends on the included works; every
    other component is computed once per candidate, and the link sources grow
    with each inclusion instead of being rebuilt per score.
    """

    def __init__(self, screener: HeuristicScreener, context: Mapping[str, Any]) -> None:
        self._screener = screener
        self._context = context
        self._links = _LinkSources(context)
        weights = screener.weights
        self._static_names = tuple(
            name for name in (*COMPONENTS, "recency") if name != "link" and weights[name] > 0
        )
        self._use_link = weights["link"] > 0
        self._static: dict[int, tuple[Mapping[str, Any], dict[str, float]]] = {}
        self._keys: dict[
            int, tuple[Mapping[str, Any], set[tuple[str, str]], set[tuple[str, str]]]
        ] = {}

    def include(self, record: Mapping[str, Any], ids: Any = None) -> None:
        """Add one included work and its extra identifiers (``included_ids``)."""

        self._links.add_ids(ids)
        self._links.add_record(record)

    def _static_components(self, record: Mapping[str, Any]) -> dict[str, float]:
        cached = self._static.get(id(record))
        if cached is not None and cached[0] is record:
            return cached[1]
        values = {
            name: self._screener._component(name, record, self._context)
            for name in self._static_names
        }
        self._static[id(record)] = (record, values)
        return values

    def _link_keys(
        self, record: Mapping[str, Any]
    ) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
        cached = self._keys.get(id(record))
        if cached is not None and cached[0] is record:
            return cached[1], cached[2]
        keys, references = _identifier_keys(record), _reference_keys(record)
        self._keys[id(record)] = (record, keys, references)
        return keys, references

    def score(self, record: Mapping[str, Any]) -> float:
        """Return the candidate's score against the works included so far."""

        components = dict(self._static_components(record))
        if self._use_link:
            components["link"] = self._links.link(*self._link_keys(record))
        return self._screener.combine(components)


__all__ = [
    "COMPONENTS",
    "DEFAULT_WEIGHTS",
    "HeuristicScreener",
    "Screener",
    "ScreeningSelection",
]
