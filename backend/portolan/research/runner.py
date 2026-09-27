"""The deterministic M1 research pipeline."""

from __future__ import annotations

import copy
import logging
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from threading import Event
from typing import Any
from urllib.parse import unquote, urlsplit

from portolan.adapters.base import OfflineCacheMissError
from portolan.concepts.merge import KeywordOccurrence, merge_keywords_with_report
from portolan.documents import DocumentStore, PdfCandidate, PdfFetcher
from portolan.graph import (
    AuthorNode,
    ConceptNode,
    Inclusion,
    ResearchGraph,
    WorkNode,
)

from .models import ResearchRequest, RunCancelled, RunProgress, RunReport
from .screening import HeuristicScreener, Screener
from .sources import ResearchSources

_LOG = logging.getLogger(__name__)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _normalise_doi(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None
    lowered = text.casefold()
    if lowered.startswith("doi:"):
        text = text[4:].strip()
    elif lowered.startswith(("https://doi.org/", "http://doi.org/", "https://dx.doi.org/")):
        text = unquote(urlsplit(text).path).lstrip("/")
    if not re.fullmatch(r"10\.\d{4,9}/\S+", text, flags=re.IGNORECASE):
        return None
    return text.rstrip(".").casefold()


def _normalise_openalex(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None
    if "openalex.org/" in text.casefold():
        text = unquote(urlsplit(text).path).rstrip("/").split("/")[-1]
    if text.casefold().startswith("openalex:"):
        text = text.split(":", 1)[1]
    return text.upper() if re.fullmatch(r"W\d+", text, flags=re.IGNORECASE) else None


def _normalise_openalex_entity(value: Any) -> str | None:
    """Normalize a work or author OpenAlex id."""

    text = _text(value)
    if text is None:
        return None
    if "openalex.org/" in text.casefold():
        text = unquote(urlsplit(text).path).rstrip("/").split("/")[-1]
    if text.casefold().startswith("openalex:"):
        text = text.split(":", 1)[1]
    return text.upper() if re.fullmatch(r"[WA]\d+", text, flags=re.IGNORECASE) else None


def _normalise_arxiv(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None
    if "arxiv.org/" in text.casefold():
        text = unquote(urlsplit(text).path).strip("/").split("/", 1)[-1]
    if text.casefold().startswith("arxiv:"):
        text = text.split(":", 1)[1]
    text = re.sub(r"\.pdf$", "", text, flags=re.IGNORECASE)
    if not re.fullmatch(
        r"(?:\d{4}\.\d{4,5}(?:v\d+)?|[a-z][a-z0-9-]+(?:\.[a-z0-9-]+)?/\S+)",
        text,
        flags=re.IGNORECASE,
    ):
        return None
    return re.sub(r"v\d+$", "", text, flags=re.IGNORECASE)


def _normalise_s2(value: Any) -> str | None:
    text = _text(value)
    return text.casefold() if text else None


def _record_identifiers(record: Mapping[str, Any]) -> set[tuple[str, str]]:
    raw_ids = record.get("identifiers")
    identifiers = raw_ids if isinstance(raw_ids, Mapping) else {}
    result: set[tuple[str, str]] = set()
    values = {
        "openalex": record.get("openalex_id") or identifiers.get("openalex"),
        "doi": record.get("doi") or identifiers.get("doi"),
        "arxiv": record.get("arxiv_id") or identifiers.get("arxiv"),
        "s2": record.get("s2_id") or record.get("paperId") or identifiers.get("s2"),
    }
    if (value := _normalise_openalex(values["openalex"])) is not None:
        result.add(("openalex", value))
    if (value := _normalise_doi(values["doi"])) is not None:
        result.add(("doi", value))
    if (value := _normalise_arxiv(values["arxiv"])) is not None:
        result.add(("arxiv", value.casefold()))
    if (value := _normalise_s2(values["s2"])) is not None:
        result.add(("s2", value))
    if not result:
        title = " ".join(str(record.get("title") or "").casefold().split())
        year = _text(record.get("year")) or ""
        if title:
            result.add(("title", f"{title}|{year}"))
    return result


def _identifier_keys_for_input(value: Any) -> set[tuple[str, str]]:
    text = _text(value)
    if text is None:
        return set()
    record = {"identifiers": {}}
    if (openalex := _normalise_openalex(text)) is not None:
        record["identifiers"]["openalex"] = openalex
    elif (doi := _normalise_doi(text)) is not None:
        record["identifiers"]["doi"] = doi
    elif (arxiv := _normalise_arxiv(text)) is not None:
        record["identifiers"]["arxiv"] = arxiv
    else:
        record["identifiers"]["s2"] = text
    return _record_identifiers(record)


def _source_identifier(record: Mapping[str, Any]) -> str | None:
    identifiers = record.get("identifiers")
    identifiers = identifiers if isinstance(identifiers, Mapping) else {}
    values = (
        _text(record.get("openalex_id") or identifiers.get("openalex")),
        _text(record.get("doi") or identifiers.get("doi")),
        _text(record.get("arxiv_id") or identifiers.get("arxiv")),
        _text(record.get("s2_id") or identifiers.get("s2")),
    )
    return next((value for value in values if value), None)


def _semantic_identifier(record: Mapping[str, Any]) -> str | None:
    """Choose an identifier accepted by Semantic Scholar's batch endpoint."""

    identifiers = record.get("identifiers")
    identifiers = identifiers if isinstance(identifiers, Mapping) else {}
    values = (
        _text(record.get("s2_id") or identifiers.get("s2")),
        _text(record.get("doi") or identifiers.get("doi")),
        _text(record.get("arxiv_id") or identifiers.get("arxiv")),
        _text(record.get("openalex_id") or identifiers.get("openalex")),
    )
    return next((value for value in values if value), None)


def _candidate_sort_id(candidate: _Candidate | Mapping[str, Any]) -> str:
    record = candidate.record if isinstance(candidate, _Candidate) else candidate
    identifiers = record.get("identifiers")
    identifiers = identifiers if isinstance(identifiers, Mapping) else {}
    for kind, value in (
        ("openalex", record.get("openalex_id") or identifiers.get("openalex")),
        ("doi", record.get("doi") or identifiers.get("doi")),
        ("arxiv", record.get("arxiv_id") or identifiers.get("arxiv")),
        ("s2", record.get("s2_id") or identifiers.get("s2")),
    ):
        if kind == "openalex":
            value = _normalise_openalex(value)
        elif kind == "doi":
            value = _normalise_doi(value)
        elif kind == "arxiv":
            value = _normalise_arxiv(value)
        else:
            value = _normalise_s2(value)
        if value:
            return f"{kind}:{value}"
    return str(record.get("title") or "").casefold()


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _merge_record(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    """Merge two source records while retaining the richest available fields."""

    merged = copy.deepcopy(dict(left))
    for key, value in right.items():
        if key == "identifiers" and isinstance(value, Mapping):
            current = merged.get("identifiers")
            current = dict(current) if isinstance(current, Mapping) else {}
            for identifier, identifier_value in value.items():
                if _is_empty(current.get(identifier)) and not _is_empty(identifier_value):
                    current[identifier] = identifier_value
            merged["identifiers"] = current
            continue
        if isinstance(value, list):
            current = merged.get(key)
            if not isinstance(current, list):
                merged[key] = copy.deepcopy(value)
                continue
            for item in value:
                if item not in current:
                    current.append(copy.deepcopy(item))
            continue
        if _is_empty(merged.get(key)) and not _is_empty(value):
            merged[key] = copy.deepcopy(value)
    return merged


def _year_allowed(record: Mapping[str, Any], request: ResearchRequest) -> bool:
    value = record.get("year")
    if value is None:
        return True
    try:
        year = int(value)
    except (TypeError, ValueError):
        return True
    if request.from_year is not None and year < request.from_year:
        return False
    return request.to_year is None or year <= request.to_year


def _record_identifier(record: Mapping[str, Any], kind: str) -> str | None:
    """Return a normalized identifier from one of the adapter record shapes."""

    identifiers = record.get("identifiers")
    identifiers = identifiers if isinstance(identifiers, Mapping) else {}
    values: list[Any] = [
        record.get(kind),
        record.get(f"{kind}_id"),
        identifiers.get(kind),
    ]
    if kind == "doi":
        external = record.get("externalIds") or record.get("external_ids")
        if isinstance(external, Mapping):
            values.extend((external.get("DOI"), external.get("doi")))
    for value in values:
        if kind == "doi":
            normalized = _normalise_doi(value)
        elif kind == "arxiv":
            normalized = _normalise_arxiv(value)
        elif kind == "openalex":
            normalized = _normalise_openalex(value)
        else:
            normalized = _normalise_s2(value)
        if normalized:
            return normalized
    return None


def _raw_record_identifier(record: Mapping[str, Any], kind: str) -> str | None:
    """Return the first source spelling of an identifier that normalizes."""

    identifiers = record.get("identifiers")
    identifiers = identifiers if isinstance(identifiers, Mapping) else {}
    values: list[Any] = [
        record.get(kind),
        record.get(f"{kind}_id"),
        identifiers.get(kind),
    ]
    if kind == "doi":
        external = record.get("externalIds") or record.get("external_ids")
        if isinstance(external, Mapping):
            values.extend((external.get("DOI"), external.get("doi")))
    for value in values:
        if not _text(value):
            continue
        normalized = _record_identifier({"identifiers": {kind: value}}, kind)
        if normalized:
            return _text(value)
    return None


def _title_parts(value: Any) -> tuple[str, frozenset[str]] | None:
    text = _text(value)
    if not text:
        return None
    folded = text.casefold()
    compact = re.sub(r"[\W_]+", "", folded, flags=re.UNICODE)
    tokens = frozenset(re.findall(r"[^\W_]+", folded, flags=re.UNICODE))
    if not compact or not tokens:
        return None
    return compact, tokens


def _titles_match(left: Any, right: Any) -> bool:
    left_parts = _title_parts(left)
    right_parts = _title_parts(right)
    if left_parts is None or right_parts is None:
        return False
    if left_parts[0] == right_parts[0]:
        return True
    union = left_parts[1] | right_parts[1]
    if not union:
        return False
    return len(left_parts[1] & right_parts[1]) / len(union) >= 0.9


def _years_match(left: Any, right: Any) -> bool:
    def year_value(value: Any) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            match = re.search(r"(?<!\d)(\d{4})(?!\d)", str(value))
            return int(match.group(1)) if match else None

    left_year = year_value(left)
    right_year = year_value(right)
    return left_year is None or right_year is None or abs(left_year - right_year) <= 1


@dataclass
class _Candidate:
    key: str
    record: dict[str, Any]
    discovered_via: str
    depth: int
    is_seed: bool = False
    frontier_seed: bool = False
    score: float | None = None
    citation_pairs: set[tuple[str, str]] = field(default_factory=set)
    work_id: str | None = None


class ResearchRunner:
    """Run resolve, snowball, screen, graph, concept, and PDF stages."""

    def __init__(
        self,
        graph: ResearchGraph,
        sources: ResearchSources,
        *,
        documents: DocumentStore | None = None,
        fetcher: PdfFetcher | None = None,
        screener: Screener | None = None,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.graph = graph
        self.sources = sources
        self.documents = documents
        self.fetcher = fetcher
        self.screener = screener or HeuristicScreener()
        self.clock = clock

    def _check_cancel(self, cancel: Event | None) -> None:
        if cancel is not None and cancel.is_set():
            raise RunCancelled()

    @staticmethod
    def _event_counts(
        candidates: Mapping[str, _Candidate], included: Sequence[_Candidate]
    ) -> dict[str, int]:
        return {
            "candidates": len(candidates),
            "included": len(included),
        }

    def _emit(
        self,
        progress: Callable[[RunProgress], None] | None,
        stage: str,
        message: str,
        counts: Mapping[str, int],
    ) -> None:
        if progress is not None:
            progress(RunProgress(stage=stage, message=message, counts=dict(counts)))

    @staticmethod
    def _remove_excluded_candidate(
        candidates: dict[str, _Candidate],
        identity_index: dict[tuple[str, str], str],
        record: Mapping[str, Any],
        excluded_identities: set[tuple[str, str]] | None,
        excluded_count: list[int] | None,
    ) -> bool:
        if not excluded_identities:
            return False
        identities = _record_identifiers(record)
        if not identities & excluded_identities:
            return False
        matches = {identity_index[item] for item in identities if item in identity_index}
        if any(candidates[key].is_seed for key in matches if key in candidates):
            raise ValueError("a seed is also excluded")
        if excluded_count is not None:
            excluded_count[0] += 1
        for key in matches:
            candidates.pop(key, None)
            for identity, mapped_key in list(identity_index.items()):
                if mapped_key == key:
                    del identity_index[identity]
        return True

    def _add_candidate(
        self,
        candidates: dict[str, _Candidate],
        identity_index: dict[tuple[str, str], str],
        record: Mapping[str, Any] | None,
        *,
        via: str,
        depth: int,
        is_seed: bool,
        cap: int,
        warnings: list[str],
        excluded_identities: set[tuple[str, str]] | None = None,
        excluded_count: list[int] | None = None,
    ) -> str | None:
        if not isinstance(record, Mapping):
            return None
        copied = copy.deepcopy(dict(record))
        identities = _record_identifiers(copied)
        matches = {identity_index[item] for item in identities if item in identity_index}
        if self._remove_excluded_candidate(
            candidates,
            identity_index,
            copied,
            excluded_identities,
            excluded_count,
        ):
            return None
        if not matches:
            if len(candidates) >= cap:
                if "candidate cap reached; stopped expanding" not in warnings:
                    warnings.append("candidate cap reached; stopped expanding")
                return None
            key = min(
                (f"{kind}:{value}" for kind, value in identities),
                default=f"candidate:{len(candidates)}",
            )
            while key in candidates:
                key = f"candidate:{len(candidates)}"
            candidates[key] = _Candidate(
                key=key,
                record=copied,
                discovered_via=via,
                depth=depth,
                is_seed=is_seed,
            )
            for identity in _record_identifiers(copied):
                identity_index[identity] = key
            return key

        key = min(matches)
        target = candidates[key]
        target.record = _merge_record(target.record, copied)
        if is_seed:
            target.is_seed = True
            if via == "seed":
                target.discovered_via = "seed"
                target.depth = 0
        elif depth < target.depth and not target.is_seed:
            target.discovered_via = via
            target.depth = depth
        for identity in _record_identifiers(target.record):
            identity_index[identity] = key

        # It is unusual for two pre-existing candidates to match one incoming
        # record, but this can happen when one source initially supplied only a
        # DOI and another supplied only an OpenAlex id.  Collapse them here.
        for duplicate_key in sorted(matches - {key}):
            duplicate = candidates.pop(duplicate_key)
            target.record = _merge_record(target.record, duplicate.record)
            target.is_seed = target.is_seed or duplicate.is_seed
            target.depth = min(target.depth, duplicate.depth)
            if target.discovered_via != "seed" and duplicate.discovered_via == "seed":
                target.discovered_via = "seed"
            for identity, mapped_key in list(identity_index.items()):
                if mapped_key == duplicate_key:
                    identity_index[identity] = key
            for item in duplicate.citation_pairs:
                target.citation_pairs.add(
                    tuple(key if endpoint == duplicate_key else endpoint for endpoint in item)
                )
        return key

    @staticmethod
    def _first_mapping(value: Any) -> Mapping[str, Any] | None:
        if isinstance(value, Mapping):
            return value
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return next((item for item in value if isinstance(item, Mapping)), None)
        return None

    def _fallback_lookup(
        self, adapter: Any, identifier: str, cancel: Event | None
    ) -> Mapping[str, Any] | None:
        """Call whichever single-record lookup surface an optional adapter exposes."""

        for method_name in ("lookup_many", "lookup_by_id"):
            method = getattr(adapter, method_name, None)
            if not callable(method):
                continue
            self._check_cancel(cancel)
            try:
                result = (
                    method([identifier]) if method_name == "lookup_many" else method(identifier)
                )
            except RunCancelled:
                raise
            except OfflineCacheMissError:
                raise
            except Exception:
                continue
            record = self._first_mapping(result)
            if record is not None:
                return record
        return None

    def _fallback_openalex_lookup(self, doi: str, cancel: Event | None) -> Mapping[str, Any] | None:
        lookup = getattr(self.sources.openalex, "lookup", None)
        if not callable(lookup):
            return None
        self._check_cancel(cancel)
        try:
            result = lookup(doi)
        except RunCancelled:
            raise
        except OfflineCacheMissError:
            raise
        except Exception:
            return None
        return result if isinstance(result, Mapping) else None

    def _fallback_title_search(
        self, title: str, year: Any, cancel: Event | None
    ) -> Mapping[str, Any] | None:
        search = getattr(self.sources.openalex, "search", None)
        if not callable(search):
            return None
        self._check_cancel(cancel)
        try:
            results = search(title, limit=5)
        except RunCancelled:
            raise
        except OfflineCacheMissError:
            raise
        except Exception:
            return None

        requested_parts = _title_parts(title)
        if requested_parts is None:
            return None
        best: Mapping[str, Any] | None = None
        best_score = -1.0
        if isinstance(results, (str, bytes)):
            return None
        try:
            result_items = list(results)
        except Exception:
            return None
        for item in result_items:
            if not isinstance(item, Mapping):
                continue
            candidate_parts = _title_parts(item.get("title"))
            if (
                candidate_parts is None
                or not _years_match(year, item.get("year"))
                or not _titles_match(title, item.get("title"))
            ):
                continue
            if candidate_parts[0] == requested_parts[0]:
                score = 1.0
            else:
                union = requested_parts[1] | candidate_parts[1]
                score = len(requested_parts[1] & candidate_parts[1]) / len(union) if union else 0.0
            if score < 0.9:
                continue
            if score > best_score:
                best = item
                best_score = score
        return best

    @staticmethod
    def _merge_seed_identifiers(
        record: Mapping[str, Any], seed: Any, *, extra_doi: str | None = None
    ) -> dict[str, Any]:
        merged = copy.deepcopy(dict(record))
        raw_identifiers = merged.get("identifiers")
        identifiers = dict(raw_identifiers) if isinstance(raw_identifiers, Mapping) else {}
        for kind, value in _identifier_keys_for_input(seed):
            if kind != "title":
                identifiers[kind] = value
        if extra_doi and not _normalise_doi(identifiers.get("doi")):
            identifiers["doi"] = extra_doi
        merged["identifiers"] = identifiers
        return merged

    def _fallback_seed(
        self, seed: Any, cancel: Event | None
    ) -> tuple[Mapping[str, Any], str, str | None] | None:
        """Resolve a seed through optional sources after OpenAlex misses it."""

        seed_text = _text(seed) or str(seed)
        title: str | None = None
        title_year: Any = None
        title_source: str | None = None

        semanticscholar = self.sources.semanticscholar
        if semanticscholar is not None:
            s2_input = seed_text
            if _normalise_doi(seed_text) is not None and not seed_text.casefold().startswith(
                "doi:"
            ):
                s2_input = f"DOI:{_normalise_doi(seed_text)}"
            s2_record = self._fallback_lookup(semanticscholar, s2_input, cancel)
            if s2_record is not None:
                title = _text(s2_record.get("title"))
                title_year = s2_record.get("year")
                if title:
                    title_source = "Semantic Scholar"
                doi = _record_identifier(s2_record, "doi")
                if doi:
                    openalex_record = self._fallback_openalex_lookup(
                        _raw_record_identifier(s2_record, "doi") or doi, cancel
                    )
                    if openalex_record is not None:
                        return openalex_record, f"via Semantic Scholar DOI {doi}", doi

        arxiv_id = _normalise_arxiv(seed_text)
        arxiv = self.sources.arxiv
        if arxiv_id is not None and arxiv is not None:
            arxiv_record = self._fallback_lookup(arxiv, arxiv_id, cancel)
            if arxiv_record is not None:
                arxiv_title = _text(arxiv_record.get("title"))
                if title is None and arxiv_title:
                    title = arxiv_title
                    title_source = "arXiv"
                if title_year is None:
                    title_year = arxiv_record.get("year")
                doi = _record_identifier(arxiv_record, "doi")
                if doi:
                    openalex_record = self._fallback_openalex_lookup(
                        _raw_record_identifier(arxiv_record, "doi") or doi, cancel
                    )
                    if openalex_record is not None:
                        return openalex_record, f"via arXiv DOI {doi}", doi

        if title:
            openalex_record = self._fallback_title_search(title, title_year, cancel)
            if openalex_record is not None:
                source = title_source or "fallback"
                return openalex_record, f"via {source} title search", None
        return None

    def _resolve_excludes(
        self,
        request: ResearchRequest,
        warnings: list[str],
        cancel: Event | None,
    ) -> set[tuple[str, str]]:
        """Resolve excluded identifiers and retain directly supplied identities."""

        if not request.exclude:
            return set()

        excluded_identities: set[tuple[str, str]] = set()
        for identifier in request.exclude:
            excluded_identities.update(_identifier_keys_for_input(identifier))

        adapter = self.sources.openalex
        records: list[Mapping[str, Any]] = []
        lookup_many = getattr(adapter, "lookup_many", None)
        if callable(lookup_many):
            try:
                result = lookup_many(request.exclude)
                records = [item for item in result if isinstance(item, Mapping)]
            except RunCancelled:
                raise
            except OfflineCacheMissError:
                raise
            except ValueError:
                # A malformed value can make a batch lookup fail.  Retry one at
                # a time so valid exclusions still get resolved.
                lookup = getattr(adapter, "lookup", None)
                if callable(lookup):
                    for identifier in request.exclude:
                        self._check_cancel(cancel)
                        try:
                            item = lookup(identifier)
                        except RunCancelled:
                            raise
                        except OfflineCacheMissError:
                            raise
                        except Exception:
                            item = None
                        if isinstance(item, Mapping):
                            records.append(item)
            except Exception:
                # Exclusions are best effort.  The raw identity remains in the
                # set above, while fallback adapters get a chance below.
                records = []
        else:
            lookup = getattr(adapter, "lookup", None)
            if callable(lookup):
                for identifier in request.exclude:
                    self._check_cancel(cancel)
                    try:
                        item = lookup(identifier)
                    except RunCancelled:
                        raise
                    except OfflineCacheMissError:
                        raise
                    except Exception:
                        item = None
                    if isinstance(item, Mapping):
                        records.append(item)

        for identifier in request.exclude:
            self._check_cancel(cancel)
            requested_keys = _identifier_keys_for_input(identifier)
            match_index = next(
                (
                    index
                    for index, record in enumerate(records)
                    if requested_keys & _record_identifiers(record)
                ),
                None,
            )
            if match_index is None and len(request.exclude) == len(records) == 1:
                match_index = 0
            if match_index is None:
                fallback = self._fallback_seed(identifier, cancel)
                if fallback is None:
                    warnings.append(f"unresolvable exclude: {identifier}")
                    continue
                fallback_record, _, extra_doi = fallback
                fallback_record = self._merge_seed_identifiers(
                    fallback_record, identifier, extra_doi=extra_doi
                )
                excluded_identities.update(_record_identifiers(fallback_record))
                continue
            excluded_identities.update(_record_identifiers(records[match_index]))
        return excluded_identities

    def _resolve_seeds(
        self,
        request: ResearchRequest,
        candidates: dict[str, _Candidate],
        identity_index: dict[tuple[str, str], str],
        warnings: list[str],
        cancel: Event | None,
        progress: Callable[[RunProgress], None] | None = None,
        excluded_identities: set[tuple[str, str]] | None = None,
        excluded_count: list[int] | None = None,
    ) -> set[str]:
        if not request.seeds:
            return set()
        adapter = self.sources.openalex
        records: list[Mapping[str, Any]] = []
        lookup_many = getattr(adapter, "lookup_many", None)
        if callable(lookup_many):
            try:
                records = [item for item in lookup_many(request.seeds) if isinstance(item, Mapping)]
            except ValueError:
                # A malformed identifier is an unresolvable seed.  Resolve the
                # remaining values one at a time so one typo does not hide valid
                # seeds in a batch adapter.
                records = []
                lookup = getattr(adapter, "lookup", None)
                if not callable(lookup):
                    raise
                for seed in request.seeds:
                    self._check_cancel(cancel)
                    try:
                        item = lookup(seed)
                    except ValueError:
                        item = None
                    if isinstance(item, Mapping):
                        records.append(item)
        else:
            lookup = getattr(adapter, "lookup", None)
            if not callable(lookup):
                raise TypeError("OpenAlex adapter must provide lookup_many or lookup")
            for seed in request.seeds:
                self._check_cancel(cancel)
                try:
                    item = lookup(seed)
                except ValueError:
                    item = None
                if isinstance(item, Mapping):
                    records.append(item)

        seed_keys: set[str] = set()
        for seed in request.seeds:
            self._check_cancel(cancel)
            requested_keys = _identifier_keys_for_input(seed)
            if excluded_identities and requested_keys & excluded_identities:
                raise ValueError(f"seed is also excluded: {seed}")
            match_index = next(
                (
                    index
                    for index, record in enumerate(records)
                    if requested_keys & _record_identifiers(record)
                ),
                None,
            )
            if match_index is None and len(request.seeds) == len(records) == 1:
                match_index = 0
            if match_index is None:
                fallback = self._fallback_seed(seed, cancel)
                if fallback is None:
                    warnings.append(f"unresolvable seed: {seed}")
                    continue
                fallback_record, route, extra_doi = fallback
                fallback_record = self._merge_seed_identifiers(
                    fallback_record, seed, extra_doi=extra_doi
                )
                if excluded_identities and (
                    _record_identifiers(fallback_record) & excluded_identities
                ):
                    raise ValueError(f"seed is also excluded: {seed}")
                key = self._add_candidate(
                    candidates,
                    identity_index,
                    fallback_record,
                    via="seed",
                    depth=0,
                    is_seed=True,
                    cap=max(1, request.max_works * 5),
                    warnings=warnings,
                    excluded_identities=excluded_identities,
                    excluded_count=excluded_count,
                )
                if key is not None:
                    seed_keys.add(key)
                    self._emit(
                        progress,
                        "resolve",
                        f"resolved {seed} {route}",
                        {"candidates": len(candidates), "included": 0},
                    )
                continue
            key = self._add_candidate(
                candidates,
                identity_index,
                records[match_index],
                via="seed",
                depth=0,
                is_seed=True,
                cap=max(1, request.max_works * 5),
                warnings=warnings,
                excluded_identities=excluded_identities,
                excluded_count=excluded_count,
            )
            if key is not None:
                seed_keys.add(key)
        return seed_keys

    def _search(
        self,
        request: ResearchRequest,
        candidates: dict[str, _Candidate],
        identity_index: dict[tuple[str, str], str],
        warnings: list[str],
        cancel: Event | None,
        seed_keys: set[str],
        excluded_identities: set[tuple[str, str]] | None = None,
        excluded_count: list[int] | None = None,
    ) -> set[str]:
        if not request.query:
            return set(seed_keys)
        search = self.sources.openalex.search
        results = search(
            request.query,
            limit=min(request.max_works, 50),
            from_year=request.from_year,
            to_year=request.to_year,
        )
        search_keys: set[str] = set()
        query_only = not seed_keys
        for item in results:
            self._check_cancel(cancel)
            if not isinstance(item, Mapping):
                continue
            if self._remove_excluded_candidate(
                candidates,
                identity_index,
                item,
                excluded_identities,
                excluded_count,
            ):
                continue
            if not _year_allowed(item, request):
                continue
            key = self._add_candidate(
                candidates,
                identity_index,
                item,
                via="search",
                depth=0,
                is_seed=False,
                cap=max(1, request.max_works * 5),
                warnings=warnings,
                excluded_identities=excluded_identities,
                excluded_count=excluded_count,
            )
            if key is not None and query_only:
                candidates[key].frontier_seed = True
                search_keys.add(key)
        return seed_keys or search_keys

    def _snowball(
        self,
        request: ResearchRequest,
        candidates: dict[str, _Candidate],
        identity_index: dict[tuple[str, str], str],
        warnings: list[str],
        cancel: Event | None,
        frontier_keys: set[str],
        progress: Callable[[RunProgress], None] | None = None,
        excluded_identities: set[tuple[str, str]] | None = None,
        excluded_count: list[int] | None = None,
    ) -> set[tuple[str, str]]:
        relation_pairs: set[tuple[str, str]] = set()
        expanded: set[str] = set()
        processed = 0
        frontier = [key for key in sorted(frontier_keys) if key in candidates]
        cap = max(1, request.max_works * 5)
        for level in range(1, request.snowball_depth + 1):
            self._check_cancel(cancel)
            if not frontier or len(candidates) >= cap:
                if frontier and len(candidates) >= cap:
                    warning = "candidate cap reached; stopped expanding"
                    if warning not in warnings:
                        warnings.append(warning)
                break
            next_frontier: set[str] = set()
            reference_ids: list[str] = []
            reference_parents: dict[str, set[str]] = {}
            for parent_key in frontier:
                self._check_cancel(cancel)
                parent = candidates.get(parent_key)
                if parent is None:
                    continue
                expanded.add(parent_key)
                refs = parent.record.get("referenced_works")
                if isinstance(refs, Sequence) and not isinstance(refs, (str, bytes)):
                    for reference in refs:
                        reference_text = _text(reference)
                        if reference_text is None:
                            continue
                        if excluded_identities and (
                            _identifier_keys_for_input(reference_text) & excluded_identities
                        ):
                            if excluded_count is not None:
                                excluded_count[0] += 1
                            continue
                        reference_ids.append(reference_text)
                        reference_parents.setdefault(reference_text, set()).add(parent_key)

            if reference_ids:
                unique_references = list(dict.fromkeys(reference_ids))
                remaining_slots = max(0, cap - len(candidates))
                if len(unique_references) > remaining_slots:
                    warning = "candidate cap reached; stopped expanding"
                    if warning not in warnings:
                        warnings.append(warning)
                unique_references = unique_references[:remaining_slots]
                lookup_many = getattr(self.sources.openalex, "lookup_many", None)
                if callable(lookup_many):
                    references = lookup_many(unique_references)
                else:
                    lookup = getattr(self.sources.openalex, "lookup", None)
                    references = (
                        [lookup(item) for item in unique_references] if callable(lookup) else []
                    )
                for item in references:
                    self._check_cancel(cancel)
                    processed += 1
                    if progress is not None and processed % 25 == 0:
                        self._emit(
                            progress,
                            "snowball",
                            f"Processed {processed} snowball results",
                            {"candidates": len(candidates), "included": 0},
                        )
                    if not isinstance(item, Mapping):
                        continue
                    if self._remove_excluded_candidate(
                        candidates,
                        identity_index,
                        item,
                        excluded_identities,
                        excluded_count,
                    ):
                        continue
                    if not _year_allowed(item, request):
                        continue
                    key = self._add_candidate(
                        candidates,
                        identity_index,
                        item,
                        via="backward",
                        depth=level,
                        is_seed=False,
                        cap=cap,
                        warnings=warnings,
                        excluded_identities=excluded_identities,
                        excluded_count=excluded_count,
                    )
                    if key is None:
                        continue
                    item_ids = _record_identifiers(item)
                    for reference_id, parents in reference_parents.items():
                        if _identifier_keys_for_input(reference_id) & item_ids:
                            for parent_key in parents:
                                relation_pairs.add((parent_key, key))
                    if key not in expanded:
                        next_frontier.add(key)

            if request.forward_per_work:
                cited_by = getattr(self.sources.openalex, "cited_by", None)
                if callable(cited_by):
                    for parent_key in frontier:
                        self._check_cancel(cancel)
                        if len(candidates) >= cap:
                            break
                        parent = candidates.get(parent_key)
                        if parent is None:
                            continue
                        identifier = _source_identifier(parent.record)
                        if identifier is None:
                            continue
                        remaining_slots = max(0, cap - len(candidates))
                        children = cited_by(
                            identifier,
                            limit=min(request.forward_per_work, remaining_slots),
                        )
                        for item in children:
                            self._check_cancel(cancel)
                            processed += 1
                            if progress is not None and processed % 25 == 0:
                                self._emit(
                                    progress,
                                    "snowball",
                                    f"Processed {processed} snowball results",
                                    {"candidates": len(candidates), "included": 0},
                                )
                            if not isinstance(item, Mapping):
                                continue
                            if self._remove_excluded_candidate(
                                candidates,
                                identity_index,
                                item,
                                excluded_identities,
                                excluded_count,
                            ):
                                continue
                            if not _year_allowed(item, request):
                                continue
                            key = self._add_candidate(
                                candidates,
                                identity_index,
                                item,
                                via="forward",
                                depth=level,
                                is_seed=False,
                                cap=cap,
                                warnings=warnings,
                                excluded_identities=excluded_identities,
                                excluded_count=excluded_count,
                            )
                            if key is not None:
                                relation_pairs.add((key, parent_key))
                                if key not in expanded:
                                    next_frontier.add(key)
            if len(candidates) >= cap:
                warning = "candidate cap reached; stopped expanding"
                if warning not in warnings:
                    warnings.append(warning)
            if progress is not None and len(expanded) and len(expanded) % 25 == 0:
                self._emit(
                    progress,
                    "snowball",
                    f"Expanded {len(expanded)} frontier works",
                    {"candidates": len(candidates), "included": 0},
                )
            frontier = sorted(next_frontier)
        return relation_pairs

    def _screen(
        self,
        request: ResearchRequest,
        candidates: dict[str, _Candidate],
        seed_keys: set[str],
        relation_pairs: set[tuple[str, str]],
        cancel: Event | None,
        progress: Callable[[RunProgress], None] | None = None,
    ) -> list[_Candidate]:
        records = [candidate.record for candidate in candidates.values()]
        counts: list[float] = []
        for record in records:
            try:
                counts.append(max(0.0, float(record.get("cited_by_count") or 0)))
            except (TypeError, ValueError):
                continue
        base_context: dict[str, Any] = {
            "query": request.query,
            "profile": request.query or "",
            "candidates": records,
            "candidate_pool": records,
            "seed_records": [
                candidates[key].record for key in sorted(seed_keys) if key in candidates
            ],
            "seed_ids": set(seed_keys),
            "included_ids": set(seed_keys),
            "max_cited_by_count": max(counts, default=0.0),
            "citation_pairs": relation_pairs,
        }
        explicit_seeds = [
            candidate for candidate in candidates.values() if candidate.key in seed_keys
        ]
        selected: list[_Candidate] = sorted(explicit_seeds, key=_candidate_sort_id)
        for candidate in selected:
            candidate.score = 1.0
        selected_ids = set(seed_keys) if selected else set()

        remaining = [
            candidate for candidate in candidates.values() if candidate.key not in seed_keys
        ]
        target_count = max(request.max_works - len(selected), 0)
        while remaining and len(selected) < len(explicit_seeds) + target_count:
            self._check_cancel(cancel)
            included_records = [candidate.record for candidate in selected]
            included_ids: set[Any] = set(selected_ids)
            for candidate in selected:
                included_ids.update(_record_identifiers(candidate.record))
            context = {
                **base_context,
                "included_records": included_records,
                "included_ids": included_ids,
            }
            for candidate in remaining:
                raw_score = self.screener.score(candidate.record, context)
                try:
                    candidate.score = max(0.0, min(1.0, float(raw_score)))
                except (TypeError, ValueError):
                    candidate.score = 0.0
            best = min(
                remaining,
                key=lambda candidate: (-(candidate.score or 0.0), _candidate_sort_id(candidate)),
            )
            selected.append(best)
            selected_ids.add(best.key)
            remaining.remove(best)
            if progress is not None and len(selected) % 25 == 0:
                self._emit(
                    progress,
                    "screen",
                    f"Screened {len(selected)} works",
                    {"candidates": len(candidates), "included": len(selected)},
                )
        return selected

    def _enrich(
        self,
        included: Sequence[_Candidate],
        warnings: list[str],
        cancel: Event | None,
    ) -> None:
        adapter = self.sources.semanticscholar
        if adapter is None:
            return
        identifiers: list[str] = []
        for candidate in included:
            self._check_cancel(cancel)
            value = _semantic_identifier(candidate.record)
            if value and value not in identifiers:
                identifiers.append(value)
        if not identifiers:
            return
        lookup_many = getattr(adapter, "lookup_many", None)
        if not callable(lookup_many):
            return
        try:
            records = lookup_many(identifiers)
        except OfflineCacheMissError:
            raise
        except Exception as exc:  # best effort enrichment
            warnings.append(f"Semantic Scholar enrichment failed: {exc}")
            return
        for record in records:
            self._check_cancel(cancel)
            if not isinstance(record, Mapping):
                continue
            keys = _record_identifiers(record)
            target = next(
                (
                    candidate
                    for candidate in included
                    if keys & _record_identifiers(candidate.record)
                ),
                None,
            )
            if target is None:
                continue
            target.record = _merge_record(target.record, record)
            identifiers_map = record.get("identifiers")
            identifiers_map = identifiers_map if isinstance(identifiers_map, Mapping) else {}
            target_ids = target.record.setdefault("identifiers", {})
            if not isinstance(target_ids, dict):
                target_ids = {}
                target.record["identifiers"] = target_ids
            for kind in ("arxiv", "doi", "s2"):
                value = record.get(f"{kind}_id") or identifiers_map.get(kind)
                if value and not target_ids.get(kind):
                    target_ids[kind] = value

    @staticmethod
    def _work_node(record: Mapping[str, Any], keyword_min_score: float) -> WorkNode:
        identifiers = record.get("identifiers")
        identifiers = identifiers if isinstance(identifiers, Mapping) else {}
        publication_types = record.get("publication_types")
        if isinstance(publication_types, Sequence) and not isinstance(
            publication_types, (str, bytes)
        ):
            work_type = next((str(value) for value in publication_types if value), None)
        else:
            work_type = _text(publication_types)
        keywords: list[str] = []
        scores: list[float] = []
        raw_keywords = record.get("keywords")
        if isinstance(raw_keywords, Sequence) and not isinstance(raw_keywords, (str, bytes)):
            for keyword in raw_keywords:
                if isinstance(keyword, Mapping):
                    term = _text(keyword.get("term") or keyword.get("display_name"))
                    score = keyword.get("score")
                else:
                    term, score = _text(keyword), None
                if not term:
                    continue
                try:
                    numeric_score = 1.0 if score is None else float(score)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(numeric_score) or numeric_score < keyword_min_score:
                    continue
                keywords.append(term)
                scores.append(numeric_score)
        cited_by = record.get("cited_by_count")
        try:
            cited_by = int(cited_by) if cited_by is not None else None
        except (TypeError, ValueError):
            cited_by = None
        year = record.get("year")
        try:
            year = int(year) if year is not None else None
        except (TypeError, ValueError):
            year = None
        return WorkNode(
            title=_text(record.get("title")) or "Untitled work",
            year=year,
            abstract=_text(record.get("abstract")),
            doi=_normalise_doi(record.get("doi") or identifiers.get("doi")),
            arxiv_id=_normalise_arxiv(record.get("arxiv_id") or identifiers.get("arxiv")),
            openalex_id=_normalise_openalex(
                record.get("openalex_id") or identifiers.get("openalex")
            ),
            s2_id=_normalise_s2(
                record.get("s2_id") or record.get("paperId") or identifiers.get("s2")
            ),
            venue=_text(record.get("venue")),
            work_type=work_type,
            cited_by_count=cited_by,
            keywords=keywords,
            keyword_scores=scores,
        )

    def _write(
        self,
        project_id: str,
        request: ResearchRequest,
        included: Sequence[_Candidate],
        relation_pairs: set[tuple[str, str]],
        warnings: list[str],
        cancel: Event | None,
        progress: Callable[[RunProgress], None] | None = None,
    ) -> tuple[int, int, set[str]]:
        self._enrich(included, warnings, cancel)
        authors: set[str] = set()
        included_ids: dict[str, str] = {}
        for index, candidate in enumerate(included, start=1):
            self._check_cancel(cancel)
            work = self.graph.upsert_work(
                self._work_node(candidate.record, request.keyword_min_score)
            )
            if work.id is None:
                continue
            candidate.work_id = work.id
            included_ids[candidate.key] = work.id
            self.graph.include_work(
                Inclusion(
                    project_id=project_id,
                    work_id=work.id,
                    discovered_via=candidate.discovered_via,
                    depth=candidate.depth,
                    score=candidate.score,
                )
            )
            author_items: list[tuple[str, int]] = []
            raw_authors = candidate.record.get("authors")
            if isinstance(raw_authors, Sequence) and not isinstance(raw_authors, (str, bytes)):
                for author_index, raw_author in enumerate(raw_authors, start=1):
                    self._check_cancel(cancel)
                    if not isinstance(raw_author, Mapping):
                        continue
                    name = _text(raw_author.get("name") or raw_author.get("display_name"))
                    if not name:
                        continue
                    try:
                        position = int(raw_author.get("position") or author_index)
                    except (TypeError, ValueError):
                        position = author_index
                    author = self.graph.upsert_author(
                        AuthorNode(
                            name=name,
                            orcid=_text(raw_author.get("orcid")),
                            openalex_id=_normalise_openalex_entity(raw_author.get("openalex_id")),
                            s2_id=_normalise_s2(
                                raw_author.get("s2_id") or raw_author.get("paperId")
                            ),
                        )
                    )
                    if author.id is not None:
                        authors.add(author.id)
                        author_items.append((author.id, position))
                if author_items:
                    self.graph.set_authors(work.id, author_items)
            if progress is not None and index % 25 == 0:
                self._emit(
                    progress,
                    "write",
                    f"Wrote {index} works",
                    {"candidates": len(included), "included": index},
                )

        records_by_key = {candidate.key: candidate.record for candidate in included}
        identities_by_key = {
            key: _record_identifiers(record) for key, record in records_by_key.items()
        }
        citations: set[tuple[str, str]] = set()
        for candidate in included:
            self._check_cancel(cancel)
            if candidate.key not in included_ids:
                continue
            citing_id = included_ids[candidate.key]
            refs = candidate.record.get("referenced_works")
            if isinstance(refs, Sequence) and not isinstance(refs, (str, bytes)):
                for reference in refs:
                    self._check_cancel(cancel)
                    reference_keys = _identifier_keys_for_input(reference)
                    target_key = next(
                        (
                            key
                            for key in sorted(included_ids)
                            if reference_keys & identities_by_key[key]
                        ),
                        None,
                    )
                    if target_key is not None and target_key != candidate.key:
                        edge = (citing_id, included_ids[target_key])
                        citations.add(edge)
                        self.graph.add_citation(*edge)
        for source_key, target_key in relation_pairs:
            self._check_cancel(cancel)
            if source_key in included_ids and target_key in included_ids:
                edge = (included_ids[source_key], included_ids[target_key])
                citations.add(edge)
                self.graph.add_citation(*edge)
        return len(citations), len(authors), authors

    def _concepts(
        self,
        project_id: str,
        warnings: list[str],
        cancel: Event | None,
        progress: Callable[[RunProgress], None] | None = None,
    ) -> int:
        from portolan.concepts import filter_concepts

        project_works = self.graph.project_works(project_id)
        occurrences: list[KeywordOccurrence] = []
        for index, work in enumerate(project_works, start=1):
            self._check_cancel(cancel)
            if work.id is None:
                continue
            keywords = list(getattr(work, "keywords", []) or [])
            scores = list(getattr(work, "keyword_scores", []) or [])
            for keyword_index, term in enumerate(keywords):
                if not _text(term):
                    continue
                score = scores[keyword_index] if keyword_index < len(scores) else None
                occurrences.append(KeywordOccurrence(work_id=work.id, term=str(term), score=score))
            if progress is not None and index % 25 == 0:
                self._emit(
                    progress,
                    "concepts",
                    f"Collected keywords from {index} works",
                    {"candidates": len(project_works), "included": len(project_works)},
                )
        clusters, _ = merge_keywords_with_report(occurrences, embedder=None)
        clusters, filter_report = filter_concepts(clusters, total_works=len(project_works))
        self._concepts_filtered = filter_report.total_dropped
        for cluster in clusters:
            self._check_cancel(cancel)
            self.graph.upsert_concept(
                ConceptNode(id=cluster.id, label=cluster.label, aliases=list(cluster.aliases))
            )
        by_work: dict[str, list[tuple[str, float]]] = {
            work.id: [] for work in project_works if work.id
        }
        for cluster in clusters:
            for work_id, score in cluster.work_scores.items():
                by_work.setdefault(work_id, []).append((cluster.id, float(score)))
        for work in project_works:
            self._check_cancel(cancel)
            if work.id is not None:
                self.graph.set_concepts(work.id, by_work.get(work.id, []))
        return len(clusters)

    @staticmethod
    def _pdf_candidates(record: Mapping[str, Any]) -> list[PdfCandidate]:
        result: list[PdfCandidate] = []
        seen: set[str] = set()

        def add(value: Mapping[str, Any] | None, *, fallback_source: str) -> None:
            if not isinstance(value, Mapping):
                return
            url = _text(value.get("url"))
            if not url or url in seen:
                return
            seen.add(url)
            try:
                result.append(
                    PdfCandidate(
                        url=url,
                        source=_text(value.get("source")) or fallback_source,
                        version=_text(value.get("version")),
                        license=_text(value.get("license")),
                        host_type=_text(value.get("host_type")),
                    )
                )
            except ValueError:
                return

        raw_candidates = record.get("pdf_candidates")
        if isinstance(raw_candidates, Sequence) and not isinstance(raw_candidates, (str, bytes)):
            for candidate in raw_candidates:
                add(
                    candidate if isinstance(candidate, Mapping) else None,
                    fallback_source="openalex",
                )
        arxiv_id = _normalise_arxiv(
            record.get("arxiv_id")
            or (
                record.get("identifiers", {}).get("arxiv")
                if isinstance(record.get("identifiers"), Mapping)
                else None
            )
        )
        if arxiv_id:
            add(
                {"url": f"https://arxiv.org/pdf/{arxiv_id}", "source": "arxiv"},
                fallback_source="arxiv",
            )
        s2_url = _text(record.get("open_access_pdf_url"))
        if s2_url:
            add({"url": s2_url, "source": "semanticscholar"}, fallback_source="semanticscholar")
        return result

    def _acquire(
        self,
        request: ResearchRequest,
        included: Sequence[_Candidate],
        warnings: list[str],
        cancel: Event | None,
        progress: Callable[[RunProgress], None] | None = None,
    ) -> tuple[int, int, int]:
        if not request.acquire_pdfs or self.fetcher is None:
            return 0, 0, 0
        acquired = failed = skipped = 0
        attempted = 0
        ordered = sorted(
            included,
            key=lambda candidate: (
                0 if candidate.is_seed else 1,
                -(candidate.score or 0.0),
                candidate.work_id or candidate.key,
            ),
        )
        for index, candidate in enumerate(ordered, start=1):
            self._check_cancel(cancel)
            if progress is not None and index % 25 == 0:
                self._emit(
                    progress,
                    "acquire",
                    f"Processed {index} PDF candidates",
                    {"candidates": len(included), "included": acquired},
                )
            if candidate.work_id is None:
                continue
            work = self.graph.get_work(candidate.work_id)
            if work is None:
                continue
            if work.document_sha256:
                skipped += 1
                continue
            if attempted >= request.max_pdfs:
                skipped += 1
                continue
            attempted += 1
            pdfs = self._pdf_candidates(candidate.record)
            raw_identifiers = candidate.record.get("identifiers")
            raw_identifiers = raw_identifiers if isinstance(raw_identifiers, Mapping) else {}
            doi = _normalise_doi(candidate.record.get("doi") or raw_identifiers.get("doi"))
            if doi and self.sources.unpaywall is not None:
                try:
                    unpaywall_record = self.sources.unpaywall.lookup(doi)
                    if isinstance(unpaywall_record, Mapping):
                        values = unpaywall_record.get("pdf_candidates")
                        if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
                            for value in values:
                                if isinstance(value, Mapping):
                                    try:
                                        item = PdfCandidate(
                                            url=str(value.get("url") or ""),
                                            source=_text(value.get("source")) or "unpaywall",
                                            version=_text(value.get("version")),
                                            license=_text(value.get("license")),
                                            host_type=_text(value.get("host_type")),
                                        )
                                    except ValueError:
                                        continue
                                    if item.url not in {candidate.url for candidate in pdfs}:
                                        pdfs.append(item)
                except Exception as exc:  # best effort acquisition enrichment
                    warnings.append(f"Unpaywall lookup failed for {doi}: {exc}")
            if not pdfs:
                failed += 1
                continue
            try:
                result = self.fetcher.fetch_first(pdfs)
            except Exception as exc:  # acquisition failures do not abort a run
                warnings.append(f"PDF acquisition failed for {candidate.work_id}: {exc}")
                failed += 1
                continue
            document = (
                result.get("document")
                if isinstance(result, Mapping)
                else getattr(result, "document", None)
            )
            sha256 = getattr(document, "sha256", None)
            source_url = getattr(document, "source_url", None)
            if isinstance(document, Mapping):
                sha256 = document.get("sha256")
                source_url = document.get("source_url")
            if not sha256:
                failed += 1
                continue
            self.graph.set_document(candidate.work_id, str(sha256), _text(source_url))
            acquired += 1
        return acquired, failed, skipped

    def run(
        self,
        project_id: str,
        request: ResearchRequest,
        *,
        progress: Callable[[RunProgress], None] | None = None,
        cancel: Event | None = None,
    ) -> RunReport:
        started_at = self.clock()
        warnings: list[str] = []
        candidates: dict[str, _Candidate] = {}
        identity_index: dict[tuple[str, str], str] = {}
        included: list[_Candidate] = []
        relations: set[tuple[str, str]] = set()
        excluded_count = [0]

        self._emit(
            progress,
            "resolve",
            "Resolving seeds",
            {"candidates": 0, "included": 0},
        )
        self._check_cancel(cancel)
        excluded_identities = self._resolve_excludes(request, warnings, cancel)
        seed_keys = self._resolve_seeds(
            request,
            candidates,
            identity_index,
            warnings,
            cancel,
            progress,
            excluded_identities,
            excluded_count,
        )
        if not seed_keys and not request.query:
            raise ValueError("none of the seeds could be resolved and no query was provided")

        self._emit(
            progress, "search", "Searching OpenAlex", {"candidates": len(candidates), "included": 0}
        )
        self._check_cancel(cancel)
        frontier_keys = self._search(
            request,
            candidates,
            identity_index,
            warnings,
            cancel,
            seed_keys,
            excluded_identities,
            excluded_count,
        )

        self._emit(
            progress,
            "snowball",
            "Expanding citation neighbourhood",
            {"candidates": len(candidates), "included": 0},
        )
        self._check_cancel(cancel)
        relations = self._snowball(
            request,
            candidates,
            identity_index,
            warnings,
            cancel,
            frontier_keys,
            progress,
            excluded_identities,
            excluded_count,
        )

        self._emit(
            progress,
            "screen",
            "Scoring candidate works",
            {"candidates": len(candidates), "included": 0},
        )
        self._check_cancel(cancel)
        included = self._screen(request, candidates, seed_keys, relations, cancel, progress)
        screened_out = len(candidates) - len(included)

        self._emit(
            progress,
            "write",
            "Writing works and citations",
            self._event_counts(candidates, included),
        )
        self._check_cancel(cancel)
        citations, authors, _ = self._write(
            project_id, request, included, relations, warnings, cancel, progress
        )

        self._emit(
            progress,
            "concepts",
            "Rebuilding project concepts",
            self._event_counts(candidates, included),
        )
        self._check_cancel(cancel)
        concepts = self._concepts(project_id, warnings, cancel, progress)

        self._emit(
            progress,
            "acquire",
            "Acquiring open access PDFs",
            self._event_counts(candidates, included),
        )
        self._check_cancel(cancel)
        pdfs_acquired, pdfs_failed, pdfs_skipped = self._acquire(
            request, included, warnings, cancel, progress
        )

        finished_at = self.clock()
        report = RunReport(
            project_id=project_id,
            candidates_found=len(candidates),
            excluded=excluded_count[0],
            screened_out=screened_out,
            included=len(included),
            citations=citations,
            authors=authors,
            concepts=concepts,
            concepts_filtered=self._concepts_filtered,
            pdfs_acquired=pdfs_acquired,
            pdfs_failed=pdfs_failed,
            pdfs_skipped=pdfs_skipped,
            warnings=warnings,
            started_at=started_at,
            finished_at=finished_at,
        )
        self._emit(
            progress,
            "done",
            "Research run complete",
            {
                "candidates": report.candidates_found,
                "included": report.included,
                "citations": report.citations,
                "pdfs_acquired": report.pdfs_acquired,
            },
        )
        _LOG.debug("research run complete", extra={"project_id": project_id})
        return report


__all__ = ["ResearchRunner"]
