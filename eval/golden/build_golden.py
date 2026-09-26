"""Build the reproducible golden literature mini-graph.

The builder deliberately keeps the adapter boundary small: adapters return plain
dictionaries, while this module verifies seed matches and adds the provenance
needed by the golden fixture.  The default command is offline and can only use
responses already present in the cache.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml

from portolan.adapters.arxiv import ArxivAdapter
from portolan.adapters.crossref import CrossrefAdapter
from portolan.adapters.semanticscholar import SemanticScholarAdapter

TITLE_MATCH_THRESHOLD = 0.92
YEAR_TOLERANCE = 1
GOLDEN_DIR = Path(__file__).resolve().parent
DEFAULT_CACHE_DIR = GOLDEN_DIR / "cache"
DEFAULT_SEED_PATH = GOLDEN_DIR / "seeds.yaml"


@dataclass
class Candidate:
    """A record returned by one source search."""

    record: dict[str, Any]
    source: str
    query: str
    fetched_at: str | None = None


@dataclass
class Resolution:
    """The verification result for one seed."""

    seed: dict[str, Any]
    candidate: Candidate | None
    score: float | None
    searched: str
    warning: str | None = None
    status: str = "FAILURE"


@dataclass
class BuildResult:
    """Files and status returned by :func:`build_golden`."""

    works: dict[str, dict[str, Any]]
    citations: list[dict[str, Any]]
    report: str
    failures: list[str]
    output_dir: Path
    request_summary: str = ""

    @property
    def ok(self) -> bool:
        return not self.failures


def _append_warning(existing: str | None, warning: str) -> str:
    """Append one audit warning without turning it into a verification failure."""

    return "; ".join(part for part in (existing, warning) if part)


def normalize_title(value: str | None) -> str:
    """Casefold a title and remove punctuation and whitespace."""

    if not value:
        return ""
    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    return "".join(
        char
        for char in normalized
        if not char.isspace() and not unicodedata.category(char).startswith("P")
    )


def title_similarity(left: str | None, right: str | None) -> float:
    """Return a normalized-title similarity in the inclusive range [0, 1]."""

    left_normalized = normalize_title(left)
    right_normalized = normalize_title(right)
    if not left_normalized or not right_normalized:
        return 0.0
    return SequenceMatcher(None, left_normalized, right_normalized).ratio()


def _as_records(value: Any) -> list[dict[str, Any]]:
    """Turn common adapter response containers into a list of records."""

    if value is None:
        return []
    if isinstance(value, Mapping):
        for key in ("data", "papers", "results", "items", "works"):
            nested = value.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, Mapping)]
        return [dict(value)]
    if isinstance(value, (list, tuple)):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


def _invoke(adapter: Any, method_names: Sequence[str], *args: Any) -> Any:
    """Call the first available adapter method.

    A few method aliases keep the builder usable with small test doubles and
    with adapters that expose a more descriptive method name.
    """

    for method_name in method_names:
        method = getattr(adapter, method_name, None)
        if method is None:
            continue
        try:
            return method(*args)
        except TypeError:
            if len(args) == 1:
                for keyword in ("title", "identifier", "identifiers", "ids"):
                    try:
                        return method(**{keyword: args[0]})
                    except TypeError:
                        continue
            raise
    raise AttributeError(f"{type(adapter).__name__} has none of: {', '.join(method_names)}")


def _record_identifiers(record: Mapping[str, Any]) -> dict[str, str]:
    identifiers: dict[str, str] = {}
    nested = record.get("identifiers")
    if isinstance(nested, Mapping):
        for key, value in nested.items():
            if value:
                normalized_key = str(key).casefold().replace("_", "")
                canonical_key = {
                    "arxiv": "arxiv",
                    "doi": "doi",
                    "s2": "s2",
                    "s2id": "s2",
                    "paperid": "s2",
                    "url": "url",
                }.get(normalized_key, str(key).lower())
                identifiers[canonical_key] = str(value)

    aliases = {
        "doi": ("doi", "doi_id", "doiId"),
        "arxiv": ("arxiv", "arxiv_id", "arxivId", "arxiv_id"),
        "s2": ("s2", "s2_id", "s2Id", "paperId", "paper_id"),
        "url": ("url", "landing_url", "landingUrl"),
    }
    for canonical, keys in aliases.items():
        if canonical in identifiers:
            continue
        for key in keys:
            value = record.get(key)
            if value:
                identifiers[canonical] = str(value)
                break

    external = record.get("externalIds") or record.get("external_ids")
    if isinstance(external, Mapping):
        for key, value in external.items():
            if not value:
                continue
            lowered = str(key).lower()
            if lowered == "arxiv":
                identifiers.setdefault("arxiv", str(value))
            elif lowered == "doi":
                identifiers.setdefault("doi", str(value))
    return identifiers


def _record_year(record: Mapping[str, Any]) -> int | None:
    value = record.get("year")
    if value is None:
        value = record.get("publication_year")
    if value is None:
        value = record.get("issued")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _record_title(record: Mapping[str, Any]) -> str:
    return str(record.get("title") or record.get("name") or "").strip()


def _fetched_at(record: Mapping[str, Any]) -> str | None:
    for key in ("fetched_at", "retrieved_at", "retrievedAt"):
        value = record.get(key)
        if value:
            return str(value)
    metadata = record.get("metadata")
    if isinstance(metadata, Mapping) and metadata.get("fetched_at"):
        return str(metadata["fetched_at"])
    return None


def _merge_records(first: Mapping[str, Any], second: Mapping[str, Any]) -> dict[str, Any]:
    """Merge duplicate source views while retaining non-empty values."""

    merged = dict(first)
    first_ids = _record_identifiers(first)
    first_ids.update(_record_identifiers(second))
    if first_ids:
        merged["identifiers"] = first_ids
    for key, value in second.items():
        current = merged.get(key)
        if current in (None, "", [], {}):
            merged[key] = value
        elif isinstance(current, list) and isinstance(value, list):
            merged[key] = list(dict.fromkeys([*current, *value]))
    return merged


def _candidate_identity(candidate: Candidate) -> tuple[str, str] | None:
    identifiers = _record_identifiers(candidate.record)
    for kind in ("doi", "arxiv", "s2", "url"):
        value = identifiers.get(kind)
        if value:
            return kind, value.casefold().strip()
    normalized = normalize_title(_record_title(candidate.record))
    year = _record_year(candidate.record)
    if normalized and year is not None:
        return "title-year", f"{normalized}:{year}"
    return None


def _deduplicate(candidates: Iterable[Candidate]) -> list[Candidate]:
    unique: list[Candidate] = []
    by_identity: dict[tuple[str, str], int] = {}
    for candidate in candidates:
        identity = _candidate_identity(candidate)
        if identity is not None and identity in by_identity:
            index = by_identity[identity]
            previous = unique[index]
            previous.record = _merge_records(previous.record, candidate.record)
            previous.source = ", ".join(
                dict.fromkeys([part for part in (*previous.source.split(", "), candidate.source)])
            )
            previous.fetched_at = previous.fetched_at or candidate.fetched_at
            continue
        if identity is not None:
            by_identity[identity] = len(unique)
        unique.append(candidate)
    return unique


def _source_tier(record: Mapping[str, Any], source: str) -> str:
    for key in ("source_tier", "sourceTier"):
        value = record.get(key)
        if value:
            return str(value)
    identifiers = _record_identifiers(record)
    publication_types = record.get("publication_types") or record.get("publicationTypes") or []
    normalized_types = {str(value).casefold() for value in publication_types}
    doi = identifiers.get("doi", "").casefold()
    if source == "arxiv" or "arxiv" in identifiers or doi.startswith("10.48550/arxiv"):
        return "preprint"
    if source == "crossref" or "doi" in identifiers:
        return "peerReviewed"
    if normalized_types & {"journalarticle", "conference", "conferencepaper", "review"}:
        return "peerReviewed"
    return "preprint"


#: Title markers for review-type works. Venue metadata rarely flags a survey outside journals,
#: so the title carries the signal: "Unifying Large Language Models and Knowledge Graphs: A
#: Roadmap" is a survey that no publicationTypes field reports as one. Deliberately narrow —
#: this is a golden-set heuristic, not the pipeline's classifier; LLM screening refines it.
_SURVEY_TITLE_RE = re.compile(
    r"\b(survey|roadmap|systematic review|literature review|an overview|a review)\b",
    re.IGNORECASE,
)


def _is_survey(record: Mapping[str, Any]) -> bool:
    for key in ("isSurvey", "is_survey"):
        value = record.get(key)
        if value is not None:
            return bool(value)
    publication_types = record.get("publication_types") or record.get("publicationTypes") or []
    if any(str(value).casefold() in {"review", "survey"} for value in publication_types):
        return True
    return bool(_SURVEY_TITLE_RE.search(_record_title(record)))


def _canonical_record(seed: Mapping[str, Any], candidate: Candidate) -> dict[str, Any]:
    record = candidate.record
    raw = record.get("raw")
    identifiers = _record_identifiers(record)
    for kind in ("doi", "arxiv"):
        if seed.get(kind) and kind not in identifiers:
            identifiers[kind] = str(seed[kind])
    fetched_at = candidate.fetched_at or _fetched_at(record)
    if fetched_at is None:
        fetched_at = datetime.now(UTC).isoformat()
    publication_types = record.get("publication_types") or record.get("publicationTypes") or []
    tldr = record.get("tldr")
    if isinstance(tldr, Mapping):
        tldr = tldr.get("text")
    references = record.get("references")
    if references is None and isinstance(raw, Mapping):
        references = raw.get("references")
    if not isinstance(references, list):
        references = []
    categories = record.get("categories") or []
    if not isinstance(categories, list):
        categories = [categories]
    fields: dict[str, Any] = {
        "key": seed["key"],
        "identifiers": identifiers,
        "title": _record_title(record),
        "year": _record_year(record),
        "abstract": record.get("abstract"),
        "venue": record.get("venue"),
        "publication_types": [str(value) for value in publication_types],
        "open_access_pdf_url": record.get("open_access_pdf_url") or record.get("openAccessPdfUrl"),
        "source_tier": _source_tier(record, candidate.source),
        "isSurvey": _is_survey(record),
        "tldr": tldr,
        "categories": [str(value) for value in categories],
        "references": [dict(value) for value in references if isinstance(value, Mapping)],
        "source_api": candidate.source,
        "retrieved_at": fetched_at,
    }
    fields["provenance"] = {
        key: {"source": candidate.source, "fetched_at": fetched_at}
        for key in fields
        if key != "key"
    }
    s2_id = identifiers.get("s2")
    if s2_id:
        fields["s2_id"] = s2_id
    return fields


def _seed_hint(seed: Mapping[str, Any]) -> tuple[str, str] | None:
    """Return the preferred Semantic Scholar batch identifier for a seed."""

    if seed.get("arxiv"):
        return "arxiv", str(seed["arxiv"])
    if seed.get("doi"):
        return "doi", str(seed["doi"])
    return None


def _normalize_hint_identifier(kind: str, value: Any) -> str:
    normalized = str(value or "").strip().casefold()
    if kind == "arxiv":
        normalized = re.sub(r"^https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/", "", normalized)
        normalized = re.sub(r"^arxiv:\s*", "", normalized)
        normalized = normalized.split("?", 1)[0].split("#", 1)[0].rstrip("/")
        normalized = re.sub(r"\.pdf$", "", normalized)
        normalized = re.sub(r"v\d+$", "", normalized)
    elif kind == "doi":
        normalized = re.sub(r"^doi:\s*", "", normalized)
        normalized = re.sub(r"^https?://doi\.org/", "", normalized)
    return normalized


def _record_matches_hint(seed: Mapping[str, Any], record: Mapping[str, Any]) -> bool:
    hint = _seed_hint(seed)
    if hint is None:
        return False
    kind, value = hint
    record_value = _record_identifiers(record).get(kind)
    normalized_record = _normalize_hint_identifier(kind, record_value)
    normalized_value = _normalize_hint_identifier(kind, value)
    return bool(record_value) and normalized_record == normalized_value


def _verify_candidates(
    seed: dict[str, Any],
    candidates: Iterable[Candidate],
    searched: str,
    warnings: Iterable[str] = (),
) -> Resolution:
    """Apply the unchanged strict title/year/expectation contract."""

    title = str(seed.get("title", ""))
    candidates = _deduplicate(candidates)
    warning_list = [warning for warning in warnings if warning]
    scored: list[tuple[Candidate, float, bool]] = []
    for candidate in candidates:
        score = title_similarity(title, _record_title(candidate.record))
        fetched_year = _record_year(candidate.record)
        year_ok = (
            fetched_year is not None and abs(fetched_year - int(seed["year"])) <= YEAR_TOLERANCE
        )
        scored.append((candidate, score, year_ok))

    title_matches = [item for item in scored if item[1] >= TITLE_MATCH_THRESHOLD]
    valid = [item for item in title_matches if item[2]]
    if len(title_matches) > 1 or len(valid) != 1:
        if not candidates:
            warning = "no candidate returned"
        elif len(title_matches) > 1:
            warning = f"ambiguous search: {len(title_matches)} candidates pass title verification"
        else:
            details = [
                f"{_record_title(candidate.record)!r} "
                f"(score={score:.3f}, year={_record_year(candidate.record)})"
                for candidate, score, _ in scored
            ]
            warning = "no candidate passed strict title/year verification: " + "; ".join(details)
        warning = "; ".join([*warning_list, warning]) if warning_list else warning
        return Resolution(
            seed=seed,
            candidate=None,
            score=max((score for _, score, _ in scored), default=None),
            searched=searched or "nothing",
            warning=warning,
        )

    candidate, score, _ = valid[0]
    canonical = _canonical_record(seed, candidate)
    expectation = seed.get("expect") or {}
    expectation_mismatches: list[str] = []
    expected_tier = expectation.get("source_tier", expectation.get("sourceTier"))
    if expected_tier is not None and canonical["source_tier"] != expected_tier:
        expectation_mismatches.append(
            f"source_tier expected {expected_tier!r}, got {canonical['source_tier']!r}"
        )
    expected_survey = expectation.get("is_survey", expectation.get("isSurvey"))
    if expected_survey is not None and canonical["isSurvey"] != bool(expected_survey):
        expectation_mismatches.append(
            f"isSurvey expected {bool(expected_survey)!r}, got {canonical['isSurvey']!r}"
        )
    if expectation_mismatches:
        warning = "; ".join([*warning_list, *expectation_mismatches])
        return Resolution(
            seed=seed,
            candidate=Candidate(canonical, candidate.source, candidate.query, candidate.fetched_at),
            score=score,
            searched=searched,
            warning=warning,
        )
    return Resolution(
        seed=seed,
        candidate=Candidate(canonical, candidate.source, candidate.query, candidate.fetched_at),
        score=score,
        searched=searched,
        warning="; ".join(warning_list) if warning_list else None,
        status="PASS",
    )


def _resolve_seed(seed: dict[str, Any], adapters: Mapping[str, Any]) -> Resolution:
    """Resolve one seed through the same batch-first source policy.

    The public helper remains useful to small callers and test doubles.  The
    normal builder calls :func:`_resolve_seeds`, which makes one batch request
    for all hinted seeds before doing any title searches.
    """

    title = str(seed.get("title", ""))
    s2_adapter = adapters["semanticscholar"]
    hint = _seed_hint(seed)
    if hint is not None:
        kind, value = hint
        identifier = f"{kind.upper()}:{value}"
        try:
            result = _invoke(
                s2_adapter,
                ("lookup_many", "batch_lookup", "batch_get", "lookup_batch"),
                [identifier],
            )
        except Exception as exc:
            return _verify_candidates(
                seed,
                [],
                f"semanticscholar:paper/batch id={identifier}",
                [f"semanticscholar lookup failed: {exc}"],
            )
        candidates = [
            Candidate(record, "semanticscholar", f"batch id={identifier}", _fetched_at(record))
            for record in _as_records(result)
            if _record_matches_hint(seed, record)
        ]
        return _verify_candidates(seed, candidates, f"semanticscholar:paper/batch id={identifier}")

    try:
        result = _invoke(s2_adapter, ("search_title", "search"), title)
    except Exception as exc:
        return _verify_candidates(
            seed,
            [],
            f"semanticscholar:title={title!r}",
            [f"semanticscholar lookup failed: {exc}"],
        )
    candidates = [
        Candidate(record, "semanticscholar", f"title={title!r}", _fetched_at(record))
        for record in _as_records(result)
    ]
    return _verify_candidates(seed, candidates, f"semanticscholar:title={title!r}")


def _resolve_seeds(seeds: Sequence[dict[str, Any]], s2_adapter: Any) -> list[Resolution]:
    """Batch-resolve every hinted seed, then title-search only unhinted seeds."""

    resolutions: dict[str, Resolution] = {}
    hinted = [(seed, _seed_hint(seed)) for seed in seeds]
    hinted = [(seed, hint) for seed, hint in hinted if hint is not None]
    if hinted:
        identifiers = [f"{kind.upper()}:{value}" for _, (kind, value) in hinted]
        try:
            batch_result = _invoke(
                s2_adapter,
                ("lookup_many", "batch_lookup", "batch_get", "lookup_batch"),
                identifiers,
            )
            batch_records = _as_records(batch_result)
            batch_error: str | None = None
        except Exception as exc:
            batch_records = []
            batch_error = f"semanticscholar batch lookup failed: {exc}"

        for seed, hint in hinted:
            assert hint is not None
            kind, value = hint
            identifier = f"{kind.upper()}:{value}"
            candidates = [
                Candidate(
                    record,
                    "semanticscholar",
                    f"batch id={identifier}",
                    _fetched_at(record),
                )
                for record in batch_records
                if _record_matches_hint(seed, record)
            ]
            warnings = [batch_error] if batch_error else []
            resolutions[str(seed.get("key", ""))] = _verify_candidates(
                seed,
                candidates,
                f"semanticscholar:paper/batch id={identifier}",
                warnings,
            )

    for seed in seeds:
        if _seed_hint(seed) is not None:
            continue
        title = str(seed.get("title", ""))
        searched = f"semanticscholar:title={title!r}"
        try:
            result = _invoke(s2_adapter, ("search_title", "search"), title)
            candidates = [
                Candidate(record, "semanticscholar", f"title={title!r}", _fetched_at(record))
                for record in _as_records(result)
            ]
            warning_list: list[str] = []
        except Exception as exc:
            candidates = []
            warning_list = [f"semanticscholar lookup failed: {exc}"]
        resolutions[str(seed.get("key", ""))] = _verify_candidates(
            seed, candidates, searched, warning_list
        )

    return [resolutions[str(seed.get("key", ""))] for seed in seeds]


def _relation_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        for key in ("data", "references", "citations", "items"):
            nested = value.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, Mapping)]
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def _relation_paper(relation: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("paper", "citedPaper", "cited_paper", "citingPaper", "citing_paper"):
        nested = relation.get(key)
        if isinstance(nested, Mapping):
            return nested
    return relation


def _relation_value(relation: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in relation and relation[key] is not None:
            return relation[key]
    paper = _relation_paper(relation)
    for key in keys:
        if key in paper and paper[key] is not None:
            return paper[key]
    return None


def _identifier_index(works: Mapping[str, dict[str, Any]]) -> dict[str, str]:
    index: dict[str, str] = {}
    for key, work in works.items():
        identifiers = _record_identifiers(work)
        for kind, value in identifiers.items():
            normalized = (
                _normalize_hint_identifier(kind, value)
                if kind in {"arxiv", "doi"}
                else str(value).casefold().strip()
            )
            index[f"{kind}:{normalized}"] = key
            index[normalized] = key
    return index


def _relation_target_key(relation: Mapping[str, Any], index: Mapping[str, str]) -> str | None:
    paper = _relation_paper(relation)
    identifiers = _record_identifiers(paper)
    for kind, value in identifiers.items():
        normalized = (
            _normalize_hint_identifier(kind, value)
            if kind in {"arxiv", "doi"}
            else str(value).casefold().strip()
        )
        key = index.get(f"{kind}:{normalized}") or index.get(normalized)
        if key:
            return key
    return None


def _s2_batch_ids(work: Mapping[str, Any]) -> str | None:
    identifiers = _record_identifiers(work)
    if identifiers.get("arxiv"):
        return f"ARXIV:{identifiers['arxiv']}"
    if identifiers.get("doi"):
        return f"DOI:{identifiers['doi']}"
    if identifiers.get("s2"):
        return str(identifiers["s2"])
    return None


def _matching_enrichment(
    work: Mapping[str, Any], records: Sequence[Mapping[str, Any]], used: set[int]
) -> tuple[int, Mapping[str, Any]] | None:
    work_ids = _record_identifiers(work)
    for index, record in enumerate(records):
        if index in used:
            continue
        record_ids = _record_identifiers(record)
        for kind in ("arxiv", "doi", "s2"):
            left = work_ids.get(kind)
            right = record_ids.get(kind)
            if left and right and str(left).casefold().strip() == str(right).casefold().strip():
                return index, record
    work_title = _record_title(work)
    work_year = _record_year(work)
    for index, record in enumerate(records):
        if index in used:
            continue
        record_year = _record_year(record)
        if work_year is None or record_year is None:
            continue
        if (
            abs(work_year - record_year) <= YEAR_TOLERANCE
            and title_similarity(work_title, _record_title(record)) >= TITLE_MATCH_THRESHOLD
        ):
            return index, record
    return None


def _enrich_with_arxiv(
    works: Mapping[str, dict[str, Any]], arxiv_adapter: Any
) -> dict[str, list[str]]:
    """Optionally fill abstract/categories; arXiv errors are seed warnings only."""

    method_names = ("lookup_many", "batch_lookup", "lookup_batch")
    arxiv_works = {
        key: work for key, work in works.items() if _record_identifiers(work).get("arxiv")
    }
    if not arxiv_works:
        return {}

    identifiers = [str(_record_identifiers(work)["arxiv"]) for work in arxiv_works.values()]
    records: list[dict[str, Any]] = []
    warnings: dict[str, list[str]] = {}
    try:
        if any(callable(getattr(arxiv_adapter, name, None)) for name in method_names):
            result = _invoke(arxiv_adapter, method_names, identifiers)
            records = _as_records(result)
        else:
            for key, work in arxiv_works.items():
                identifier = str(_record_identifiers(work)["arxiv"])
                try:
                    result = _invoke(
                        arxiv_adapter,
                        ("lookup", "lookup_by_id", "get_by_id"),
                        identifier,
                    )
                    records.extend(_as_records(result))
                except Exception as exc:
                    warnings.setdefault(key, []).append(f"arxiv enrichment warning: {exc}")
    except Exception as exc:
        warning = f"arxiv enrichment warning: {exc}"
        for key in arxiv_works:
            warnings.setdefault(key, []).append(warning)
        return warnings

    used: set[int] = set()
    fetched_at = datetime.now(UTC).isoformat()
    for key, work in arxiv_works.items():
        match = _matching_enrichment(work, records, used)
        if match is None:
            warnings.setdefault(key, []).append(
                "arxiv enrichment warning: no record matched its arXiv identifier"
            )
            continue
        index, record = match
        used.add(index)
        provenance = work.setdefault("provenance", {})
        record_fetched_at = _fetched_at(record) or fetched_at
        abstract = record.get("abstract")
        if abstract and not work.get("abstract"):
            work["abstract"] = abstract
            provenance["abstract"] = {"source": "arxiv", "fetched_at": record_fetched_at}
        categories = record.get("categories") or record.get("category")
        if categories and not work.get("categories"):
            if not isinstance(categories, list):
                categories = [categories]
            work["categories"] = [str(value) for value in categories]
            provenance["categories"] = {"source": "arxiv", "fetched_at": record_fetched_at}
    return warnings


def _semantic_scholar_has_key(adapter: Any) -> bool:
    configured = getattr(adapter, "api_key", None)
    if hasattr(adapter, "api_key"):
        return bool(configured)
    return bool(os.environ.get("SEMANTIC_SCHOLAR_API_KEY"))


def _base_citation_edges(
    works: Mapping[str, dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Build only the in-corpus graph embedded in the batch paper records."""

    index = _identifier_index(works)
    edges: dict[tuple[str, str], dict[str, Any]] = {}
    for citing_key, work in works.items():
        for relation in _relation_items(work.get("references")):
            target_key = _relation_target_key(relation, index)
            if target_key is None or target_key == citing_key:
                continue
            edge_key = (citing_key, target_key)
            edges.setdefault(
                edge_key,
                {"citing": edge_key[0], "cited": edge_key[1]},
            )
    return edges


def _add_intent_metadata(edge: dict[str, Any], relation: Mapping[str, Any]) -> None:
    """Attach optional relationship metadata without manufacturing empty fields."""

    intents = _relation_value(relation, "intents", "intent") or []
    if isinstance(intents, str):
        intents = [intents]
    intents = [str(intent) for intent in intents if intent]
    if intents:
        existing = edge.setdefault("intents", [])
        for intent in intents:
            if intent not in existing:
                existing.append(intent)
        edge.setdefault("citationFunction", intents[0])

    influential = _relation_value(relation, "isInfluential", "is_influential")
    if influential is not None:
        edge["isInfluential"] = influential

    contexts = _relation_value(relation, "contexts", "context", "citationContext") or []
    if isinstance(contexts, str):
        contexts = [contexts]
    contexts = [str(context) for context in contexts if context]
    if contexts:
        existing_contexts = edge.setdefault("contexts", [])
        for context in contexts:
            if context not in existing_contexts:
                existing_contexts.append(context)


def _enrich_citations_with_intents(
    works: Mapping[str, dict[str, Any]],
    edges: dict[tuple[str, str], dict[str, Any]],
    s2_adapter: Any,
) -> list[str]:
    """Fetch intent metadata only for keyed Semantic Scholar clients."""

    if not _semantic_scholar_has_key(s2_adapter):
        return []
    index = _identifier_index(works)
    warnings: list[str] = []
    for citing_key, work in works.items():
        s2_id = _record_identifiers(work).get("s2")
        if not s2_id:
            continue
        try:
            value = _invoke(s2_adapter, ("references", "get_references"), s2_id)
        except Exception as exc:
            warnings.append(f"{citing_key}: intent enrichment warning: {exc}")
            continue
        for relation in _relation_items(value):
            target_key = _relation_target_key(relation, index)
            if target_key is None:
                continue
            edge = edges.get((citing_key, target_key))
            if edge is not None:
                _add_intent_metadata(edge, relation)
    return warnings


def _build_citations(
    works: Mapping[str, dict[str, Any]],
    s2_adapter: Any | None = None,
    *,
    with_intents: bool = False,
    warning_sink: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return batch-derived edges, optionally enriching them with keyed intents."""

    edges = _base_citation_edges(works)
    if with_intents and s2_adapter is not None:
        warnings = _enrich_citations_with_intents(works, edges, s2_adapter)
        if warning_sink is not None:
            warning_sink.extend(warnings)
    return [edges[key] for key in sorted(edges)]


def _adapter_metric(adapter: Any, names: Sequence[str]) -> int | None:
    for name in names:
        value = getattr(adapter, name, None)
        if isinstance(value, int):
            return value
    return None


def _request_summary(adapters: Mapping[str, Any], fallback: Mapping[str, int]) -> str:
    """Format source request/cache metrics as one stable CLI line."""

    request_counts: dict[str, int] = {}
    cache_counts: dict[str, int] = {}
    for source in ("semanticscholar", "arxiv", "crossref"):
        adapter = adapters.get(source)
        request_counts[source] = _adapter_metric(
            adapter,
            ("http_requests", "request_count", "requests_made"),
        )
        if request_counts[source] is None:
            request_counts[source] = int(fallback.get(source, 0))
        cache_counts[source] = _adapter_metric(
            adapter,
            ("cache_hits", "cache_hit_count"),
        )
        if cache_counts[source] is None:
            cache_counts[source] = 0
    total = sum(request_counts.values())
    request_text = ", ".join(f"{source}={request_counts[source]}" for source in request_counts)
    cache_text = ", ".join(f"{source}={cache_counts[source]}" for source in cache_counts)
    return f"HTTP requests: {request_text} (total={total}); cache hits: {cache_text}."


def create_adapters(cache_dir: Path, offline: bool) -> dict[str, Any]:
    """Construct source adapters without shared global state."""

    return {
        "arxiv": ArxivAdapter(cache_dir=cache_dir, offline=offline, refresh=not offline),
        "semanticscholar": SemanticScholarAdapter(
            cache_dir=cache_dir, offline=offline, refresh=not offline
        ),
        "crossref": CrossrefAdapter(cache_dir=cache_dir, offline=offline, refresh=not offline),
    }


def _load_seeds(seed_path: Path) -> list[dict[str, Any]]:
    payload = yaml.safe_load(seed_path.read_text(encoding="utf-8")) or {}
    papers = payload.get("papers") if isinstance(payload, Mapping) else None
    if not isinstance(papers, list):
        raise ValueError(f"{seed_path} does not contain a papers list")
    return [dict(paper) for paper in papers if isinstance(paper, Mapping)]


def _render_report(
    resolutions: Sequence[Resolution],
    failures: Sequence[str],
    global_warnings: Sequence[str] = (),
    request_summary: str | None = None,
) -> str:
    lines = [
        "# Golden resolution report",
        "",
        f"Title threshold: {TITLE_MATCH_THRESHOLD:.2f}; year tolerance: +/-{YEAR_TOLERANCE}.",
        "",
    ]
    for resolution in resolutions:
        seed = resolution.seed
        similarity_line = (
            f"- normalized-title similarity: {resolution.score:.3f}"
            if resolution.score is not None
            else "- normalized-title similarity: —"
        )
        lines.extend(
            [
                f"## {seed.get('key', '<missing-key>')} — {resolution.status}",
                "",
                f"- searched: {resolution.searched}",
                f"- requested title: {seed.get('title', '')}",
                f"- matched title: "
                f"{resolution.candidate.record.get('title', '') if resolution.candidate else '—'}",
                similarity_line,
            ]
        )
        if resolution.warning:
            lines.append(f"- warning: {resolution.warning}")
        else:
            lines.append("- warning: none")
        lines.append("")
    if failures:
        lines.extend(["## Failures", "", *[f"- {failure}" for failure in failures], ""])
    if global_warnings:
        lines.extend(["## Warnings", "", *[f"- {warning}" for warning in global_warnings], ""])
    if request_summary:
        lines.extend(["## Request summary", "", f"- {request_summary}", ""])
    return "\n".join(lines)


def build_golden(
    seed_path: Path,
    output_dir: Path,
    cache_dir: Path,
    *,
    refresh: bool = False,
    only: str | None = None,
    allow_failures: bool = False,
    allow_regression: bool = False,
    no_arxiv: bool = False,
    with_intents: bool = False,
    adapters: Mapping[str, Any] | None = None,
) -> BuildResult:
    """Resolve seeds, verify them, and write the three golden-set artifacts."""

    seeds = _load_seeds(seed_path)
    if only is not None:
        seeds = [seed for seed in seeds if seed.get("key") == only]
        if not seeds:
            seeds = [{"key": only, "title": "", "year": 0, "expect": {}}]
    adapter_bundle = dict(adapters or create_adapters(cache_dir, offline=not refresh))
    resolutions = _resolve_seeds(seeds, adapter_bundle["semanticscholar"])
    works: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    for resolution in resolutions:
        key = str(resolution.seed.get("key", ""))
        if resolution.status == "PASS" and resolution.candidate is not None:
            works[key] = resolution.candidate.record
        else:
            failures.append(f"{key}: {resolution.warning or 'verification failed'}")

    global_warnings: list[str] = []
    arxiv_warnings: dict[str, list[str]] = {}
    if not no_arxiv:
        arxiv_warnings = _enrich_with_arxiv(works, adapter_bundle["arxiv"])
        for resolution in resolutions:
            key = str(resolution.seed.get("key", ""))
            for warning in arxiv_warnings.get(key, []):
                resolution.warning = _append_warning(resolution.warning, warning)

    intent_warnings: list[str] = []
    citations = _build_citations(
        works,
        adapter_bundle["semanticscholar"],
        with_intents=with_intents,
        warning_sink=intent_warnings,
    )
    global_warnings.extend(intent_warnings)
    if with_intents and not _semantic_scholar_has_key(adapter_bundle["semanticscholar"]):
        global_warnings.append(
            "Semantic Scholar intent enrichment skipped: SEMANTIC_SCHOLAR_API_KEY is not set"
        )

    hinted_count = sum(1 for seed in seeds if _seed_hint(seed) is not None)
    unhinted_count = len(seeds) - hinted_count
    intent_count = (
        sum(1 for work in works.values() if _record_identifiers(work).get("s2"))
        if with_intents and _semantic_scholar_has_key(adapter_bundle["semanticscholar"])
        else 0
    )
    arxiv_work_count = sum(1 for work in works.values() if _record_identifiers(work).get("arxiv"))
    fallback_counts = {
        "semanticscholar": (1 if hinted_count else 0) + unhinted_count + intent_count,
        "arxiv": 1 if arxiv_work_count and not no_arxiv else 0,
        "crossref": 0,
    }
    request_summary = _request_summary(adapter_bundle, fallback_counts)
    report = _render_report(resolutions, failures, global_warnings, request_summary)
    output_dir.mkdir(parents=True, exist_ok=True)
    _guard_against_regression(output_dir, works, allow_regression=allow_regression)
    (output_dir / "works.json").write_text(
        json.dumps(works, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_dir / "citations.json").write_text(
        json.dumps(citations, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_dir / "resolution_report.md").write_text(report, encoding="utf-8")
    return BuildResult(works, citations, report, failures, output_dir, request_summary)


class RegressionError(RuntimeError):
    """Raised when a rebuild would replace the fixture with a strictly worse one."""


def _guard_against_regression(
    output_dir: Path,
    works: Mapping[str, Any],
    *,
    allow_regression: bool = False,
) -> None:
    """Refuse to overwrite a committed fixture with a strictly worse one.

    A live ``--refresh`` run depends on flaky third-party rate limits: an anonymous
    Semantic Scholar title search that 429s resolves fewer seeds than the run before it.
    Measured 2026-09-22: consecutive refresh runs produced 19 and then 18 works. The
    golden set is a test fixture, so silently degrading it is worse than failing loudly.
    Pass ``--allow-regression`` when the drop is intended (a seed genuinely retracted, or
    seeds.yaml shrank).
    """
    existing_path = output_dir / "works.json"
    if allow_regression or not existing_path.exists():
        return
    try:
        previous = json.loads(existing_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    previous_works = previous.get("works", previous) if isinstance(previous, dict) else previous
    previous_count = len(previous_works)
    if len(works) < previous_count:
        raise RegressionError(
            f"refusing to overwrite {existing_path}: this run resolved {len(works)} works, "
            f"the existing fixture has {previous_count}. Re-run (the cache makes a good run "
            f"reproducible) or pass --allow-regression if the drop is intended."
        )


app = typer.Typer(add_completion=False, no_args_is_help=False)


@app.command()
def main(
    refresh: Annotated[bool, typer.Option("--refresh", help="Permit live API requests.")] = False,
    cache_dir: Annotated[
        Path, typer.Option("--cache-dir", help="Response-cache directory.")
    ] = DEFAULT_CACHE_DIR,
    only: Annotated[str | None, typer.Option("--only", help="Build one seed key.")] = None,
    allow_failures: Annotated[
        bool,
        typer.Option(
            "--allow-failures", help="Write artifacts but still exit non-zero on failures."
        ),
    ] = False,
    allow_regression: Annotated[
        bool,
        typer.Option(
            "--allow-regression",
            help="Permit overwriting the fixture with fewer resolved works than it has now.",
        ),
    ] = False,
    no_arxiv: Annotated[
        bool, typer.Option("--no-arxiv", help="Skip optional arXiv enrichment.")
    ] = False,
    with_intents: Annotated[
        bool,
        typer.Option(
            "--with-intents",
            help="Fetch citation intents/context metadata when a Semantic Scholar key is set.",
        ),
    ] = False,
    seed_path: Annotated[Path, typer.Option("--seeds", help="Seed YAML path.")] = DEFAULT_SEED_PATH,
    output_dir: Annotated[
        Path, typer.Option("--output-dir", help="Artifact output directory.")
    ] = GOLDEN_DIR,
) -> None:
    """Resolve the curated seed set into works, citations, and an audit report."""

    result = build_golden(
        seed_path,
        output_dir,
        cache_dir,
        refresh=refresh,
        only=only,
        allow_failures=allow_failures,
        allow_regression=allow_regression,
        no_arxiv=no_arxiv,
        with_intents=with_intents,
    )
    if result.failures:
        typer.echo(
            f"{len(result.failures)} seed(s) failed verification; see "
            f"{result.output_dir / 'resolution_report.md'}",
            err=True,
        )
        typer.echo(result.request_summary)
        raise typer.Exit(code=1)
    typer.echo(
        f"Built {len(result.works)} verified works and {len(result.citations)} citation edges."
    )
    typer.echo(result.request_summary)


if __name__ == "__main__":
    app()
