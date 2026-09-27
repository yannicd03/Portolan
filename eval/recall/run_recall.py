"""Run the survey-paper recall evaluation.

The command is intentionally a small orchestration layer.  Source adapters
own HTTP caching and normalization; :mod:`metrics` owns identity matching; the
runner owns candidate discovery and graph inclusion.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

# The documented invocation runs this file from ``backend/``.  Add the backend
# package explicitly because Python places the script directory, rather than
# the current directory, first on sys.path for a direct script invocation.
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

try:  # Works both as ``python -m eval.recall.run_recall`` and as a script.
    from .metrics import compute_metrics, identity_keys
except ImportError:  # pragma: no cover - exercised by the documented command.
    from metrics import compute_metrics, identity_keys

import yaml  # noqa: E402

from portolan.adapters.base import OfflineCacheMissError  # noqa: E402
from portolan.graph.memory import InMemoryResearchGraph  # noqa: E402
from portolan.research import ResearchRunner, ResearchSources  # noqa: E402
from portolan.research.models import ResearchRequest  # noqa: E402
from portolan.settings import Settings  # noqa: E402

DEFAULT_SURVEY_PATH = Path(__file__).resolve().with_name("surveys.yaml")
DEFAULT_RESULTS_DIR = Path(__file__).resolve().with_name("results")
S2_PAGE_SIZE = 100


def _as_records(value: Any) -> list[dict[str, Any]]:
    """Normalize common adapter response containers into plain records."""

    if value is None:
        return []
    if isinstance(value, Mapping):
        for key in ("data", "papers", "results", "items", "works"):
            nested = value.get(key)
            if isinstance(nested, list):
                return [dict(item) for item in nested if isinstance(item, Mapping)]
        return [dict(value)]
    if isinstance(value, (list, tuple)):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


def _call(adapter: Any, names: Sequence[str], *args: Any, **kwargs: Any) -> Any:
    """Call the first available method, with a small test-double fallback."""

    for name in names:
        method = getattr(adapter, name, None)
        if not callable(method):
            continue
        if kwargs:
            try:
                return method(*args, **kwargs)
            except TypeError:
                # Tiny fakes often implement ``references(identifier)`` only.
                return method(*args)
        return method(*args)
    raise AttributeError(f"{type(adapter).__name__} has none of: {', '.join(names)}")


def _identifier_values(record: Any, kind: str) -> list[str]:
    aliases: dict[str, tuple[str, ...]] = {
        "openalex": ("openalex_id", "openalex", "openalexId"),
        "doi": ("doi", "doi_id", "doiId"),
        "arxiv": ("arxiv_id", "arxiv", "arxivId"),
        "s2": ("s2_id", "s2", "s2Id", "paperId", "paper_id"),
    }
    values: list[str] = []
    identifiers = record.get("identifiers") if isinstance(record, Mapping) else None
    identifiers = identifiers if isinstance(identifiers, Mapping) else {}
    for name in aliases[kind]:
        value = record.get(name) if isinstance(record, Mapping) else getattr(record, name, None)
        if value:
            values.append(str(value))
        value = identifiers.get(name)
        if value:
            values.append(str(value))
    external = (
        record.get("externalIds") or record.get("external_ids")
        if isinstance(record, Mapping)
        else {}
    )
    if isinstance(external, Mapping):
        if kind == "doi":
            values.extend(
                str(value) for value in (external.get("DOI"), external.get("doi")) if value
            )
        elif kind == "arxiv":
            values.extend(
                str(value) for value in (external.get("ArXiv"), external.get("arxiv")) if value
            )
    return list(dict.fromkeys(values))


def _record_title(record: Any) -> str:
    if isinstance(record, Mapping):
        return str(record.get("title") or record.get("display_name") or "").strip()
    return str(getattr(record, "title", "") or "").strip()


def _normalise_title(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(char for char in text if char.isalnum())


def _record_year(record: Mapping[str, Any]) -> int | None:
    value = record.get("year") or record.get("publication_year")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _validate_survey_resolution(survey: Mapping[str, Any], record: Mapping[str, Any]) -> None:
    expected_title = _normalise_title(survey.get("title"))
    actual_title = _normalise_title(_record_title(record))
    if (
        not actual_title
        or not expected_title
        or SequenceMatcher(None, expected_title, actual_title).ratio() < 0.92
    ):
        raise LookupError(
            f"resolved survey title does not match {survey['survey']!r}: {_record_title(record)!r}"
        )
    actual_year = _record_year(record)
    if actual_year is not None and abs(actual_year - int(survey["to_year"])) > 1:
        raise LookupError(
            f"resolved survey year does not match {survey['survey']!r}: {actual_year}"
        )


def _openalex_reference_ids(record: Mapping[str, Any]) -> list[str]:
    references = record.get("referenced_works")
    if not isinstance(references, (list, tuple)):
        return []
    result: list[str] = []
    for reference in references:
        if isinstance(reference, Mapping):
            values = _identifier_values(reference, "openalex")
        else:
            values = [str(reference)] if reference else []
        for value in values:
            if value not in result:
                result.append(value)
    return result


def _resolve_openalex_references(
    record: Mapping[str, Any], openalex: Any, *, batch_size: int = 50
) -> list[dict[str, Any]]:
    """Resolve OpenAlex ``referenced_works`` in bounded batches."""

    reference_ids = _openalex_reference_ids(record)
    if not reference_ids:
        return []
    resolved: list[dict[str, Any]] = []
    lookup_many = getattr(openalex, "lookup_many", None)
    lookup = getattr(openalex, "lookup", None)
    for offset in range(0, len(reference_ids), max(1, batch_size)):
        chunk = reference_ids[offset : offset + max(1, batch_size)]
        if callable(lookup_many):
            values = lookup_many(chunk)
        elif callable(lookup):
            values = [lookup(identifier) for identifier in chunk]
        else:
            values = []
        resolved.extend(item for item in _as_records(values) if identity_keys(item))
    return resolved


def _relation_paper(relation: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for key in ("paper", "citedPaper", "cited_paper", "citingPaper", "citing_paper"):
        nested = relation.get(key)
        if isinstance(nested, Mapping):
            return nested
    return relation if identity_keys(relation) else None


def _s2_survey_record(record: Mapping[str, Any], adapter: Any) -> dict[str, Any] | None:
    identifiers = [*_identifier_values(record, "s2")]
    identifiers.extend(f"arxiv:{value}" for value in _identifier_values(record, "arxiv"))
    identifiers.extend(f"doi:{value}" for value in _identifier_values(record, "doi"))
    for identifier in dict.fromkeys(identifiers):
        try:
            result = _call(adapter, ("lookup_by_id", "lookup", "get_by_id"), identifier)
        except AttributeError:
            result = _call(adapter, ("lookup_many", "batch_lookup", "lookup_batch"), [identifier])
        records = _as_records(result)
        if records:
            return records[0]
    return None


def _s2_references(record: Mapping[str, Any], adapter: Any) -> list[dict[str, Any]]:
    """Fetch all available Semantic Scholar references, following pages."""

    survey_record = _s2_survey_record(record, adapter)
    source_record = survey_record or record
    identifiers = _identifier_values(source_record, "s2")
    if not identifiers:
        identifiers = [*_identifier_values(record, "arxiv"), *_identifier_values(record, "doi")]
    if not identifiers:
        return []
    identifier = identifiers[0]
    if not _identifier_values(source_record, "s2"):
        if _identifier_values(record, "arxiv"):
            identifier = f"ARXIV:{_identifier_values(record, 'arxiv')[0]}"
        elif _identifier_values(record, "doi"):
            identifier = f"DOI:{_identifier_values(record, 'doi')[0]}"
    relation_method = getattr(adapter, "references", None) or getattr(
        adapter, "get_references", None
    )
    if not callable(relation_method):
        embedded = source_record.get("references") if isinstance(source_record, Mapping) else None
        return [
            dict(paper)
            for item in (embedded or [])
            if isinstance(item, Mapping)
            and (paper := _relation_paper(item)) is not None
            and identity_keys(paper)
        ]

    resolved: list[dict[str, Any]] = []
    seen_keys: set[frozenset[tuple[str, str]]] = set()
    offset = 0
    for _ in range(1000):
        page = _call(
            adapter,
            ("references", "get_references"),
            identifier,
            limit=S2_PAGE_SIZE,
            offset=offset,
        )
        relations = _as_records(page)
        if not relations:
            break
        added = 0
        for relation in relations:
            paper = _relation_paper(relation)
            if paper is None:
                continue
            copied = dict(paper)
            keys = identity_keys(copied)
            if not keys or keys in seen_keys:
                continue
            seen_keys.add(keys)
            resolved.append(copied)
            added += 1
        if len(relations) < S2_PAGE_SIZE:
            break
        # If a fake or a cache snapshot ignores offset, avoid looping forever.
        if offset and added == 0:
            break
        offset += len(relations)
    return resolved


def resolve_survey(identifier: str, sources: ResearchSources) -> dict[str, Any]:
    """Resolve a survey identifier, preferring OpenAlex."""

    try:
        record = sources.openalex.lookup(identifier)
    except ValueError:
        # Older OpenAlex test doubles, and some adapter versions, accept only
        # bare DOI/OpenAlex identifiers.  An arXiv DOI is equivalent for the
        # OpenAlex works endpoint.
        if not any(kind == "arxiv" for kind, _ in identity_keys(identifier)):
            raise
        arxiv = identifier.strip()
        if arxiv.casefold().startswith("arxiv:"):
            arxiv = arxiv.split(":", 1)[1].strip()
        record = sources.openalex.lookup(f"10.48550/arxiv.{arxiv}")
    if isinstance(record, Mapping):
        return dict(record)

    # Resolve through arXiv when OpenAlex does not recognize the supplied
    # spelling, then retry OpenAlex with the DOI/title returned by arXiv.
    arxiv_adapter = getattr(sources, "arxiv", None)
    if arxiv_adapter is not None:
        try:
            arxiv_record = _call(arxiv_adapter, ("lookup_by_id", "lookup", "get_by_id"), identifier)
        except ValueError:
            arxiv_record = None
        if isinstance(arxiv_record, Mapping):
            for doi in _identifier_values(arxiv_record, "doi"):
                record = sources.openalex.lookup(doi)
                if isinstance(record, Mapping):
                    return dict(record)
            title = _record_title(arxiv_record)
            search = getattr(sources.openalex, "search", None)
            if title and callable(search):
                records = _as_records(search(title, limit=5))
                if records:
                    return records[0]

    adapter = sources.semanticscholar
    if adapter is not None:
        try:
            fallback = _call(adapter, ("lookup_by_id", "lookup", "get_by_id"), identifier)
        except AttributeError:
            fallback = _call(adapter, ("lookup_many", "batch_lookup", "lookup_batch"), [identifier])
        records = _as_records(fallback)
        if records:
            return records[0]
    raise LookupError(f"survey identifier did not resolve: {identifier}")


def fetch_ground_truth(
    survey_record: Mapping[str, Any], sources: ResearchSources
) -> list[dict[str, Any]]:
    """Fetch identity-bearing survey references from OpenAlex or S2 fallback."""

    references = _resolve_openalex_references(survey_record, sources.openalex)
    if references:
        return references
    if sources.semanticscholar is None:
        return []
    return _s2_references(survey_record, sources.semanticscholar)


class _CapturingResearchRunner(ResearchRunner):
    """ResearchRunner variant that exposes the candidate pool for evaluation."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.candidate_records: list[dict[str, Any]] = []
        self.included_records: list[dict[str, Any]] = []

    def _screen(self, *args: Any, **kwargs: Any) -> Any:
        candidates = args[1] if len(args) > 1 else kwargs["candidates"]
        self.candidate_records = [
            copy.deepcopy(candidate.record) for candidate in candidates.values()
        ]
        selected = super()._screen(*args, **kwargs)
        self.included_records = [copy.deepcopy(candidate.record) for candidate in selected]
        return selected


def _report_dict(report: Any) -> dict[str, Any]:
    if hasattr(report, "model_dump"):
        return report.model_dump(mode="json")
    if isinstance(report, Mapping):
        return dict(report)
    return {
        "included": getattr(report, "included", 0),
        "candidates_found": getattr(report, "candidates_found", 0),
    }


def run_survey(
    survey: Mapping[str, Any],
    sources: ResearchSources,
    *,
    max_works: int = 150,
    snowball_depth: int = 1,
) -> dict[str, Any]:
    """Resolve, run, and score one survey specification."""

    survey_identifier = str(survey["survey"])
    survey_record = resolve_survey(survey_identifier, sources)
    _validate_survey_resolution(survey, survey_record)
    ground_truth = fetch_ground_truth(survey_record, sources)
    graph = InMemoryResearchGraph()
    project = graph.create_project(f"Recall: {survey['key']}")
    request = ResearchRequest(
        seeds=[str(seed) for seed in survey.get("seeds", [])],
        query=str(survey["query"]),
        max_works=max_works,
        snowball_depth=snowball_depth,
        to_year=int(survey["to_year"]),
        exclude=[survey_identifier],
        acquire_pdfs=False,
    )
    runner = _CapturingResearchRunner(graph, sources)
    report = runner.run(project.id, request)
    included = graph.project_works(project.id)
    report_data = _report_dict(report)
    metrics = compute_metrics(
        ground_truth,
        runner.candidate_records,
        included,
        excluded=[survey_record],
    )
    result = {
        "key": survey["key"],
        "survey": survey_identifier,
        "title": survey["title"],
        "query": survey["query"],
        "seeds": list(survey.get("seeds", [])),
        "to_year": int(survey["to_year"]),
        "settings": {"max_works": max_works, "snowball_depth": snowball_depth},
        "survey_resolved_title": _record_title(survey_record),
        "survey_identities": sorted(
            f"{kind}:{value}" for kind, value in identity_keys(survey_record)
        ),
        "candidate_identities": sorted(
            {
                f"{kind}:{value}"
                for candidate in runner.candidate_records
                for kind, value in identity_keys(candidate)
            }
        ),
        "included_identities": sorted(
            {f"{kind}:{value}" for work in included for kind, value in identity_keys(work)}
        ),
        "ground_truth_count": metrics.ground_truth_count,
        "candidates_found": metrics.candidates_count,
        "included": metrics.included_count,
        "recall_candidates": metrics.recall_candidates,
        "recall_included": metrics.recall_included,
        "precision_included": metrics.precision_included,
        "candidates_matched": metrics.candidates_matched,
        "included_matched": metrics.included_matched,
        "missed_references": list(metrics.missed_references),
        "excluded": report_data.get("excluded", 0),
        "report": report_data,
    }
    return result


def load_surveys(path: Path = DEFAULT_SURVEY_PATH) -> list[dict[str, Any]]:
    """Load and lightly validate the survey specification file."""

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    values = raw.get("surveys") if isinstance(raw, Mapping) else raw
    if not isinstance(values, list):
        raise ValueError(f"expected a list of surveys in {path}")
    required = {"key", "survey", "title", "query", "seeds", "to_year"}
    surveys: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, Mapping):
            raise ValueError("each survey entry must be a mapping")
        missing = required - set(value)
        if missing:
            raise ValueError(f"survey entry is missing: {', '.join(sorted(missing))}")
        surveys.append(dict(value))
    return surveys


def _format_metric(value: float) -> str:
    return f"{value:.3f}"


def print_results_table(results: Sequence[Mapping[str, Any]]) -> None:
    """Print the same compact table that is persisted in ``summary.md``."""

    print(
        "| Survey | R | Candidates | Included | Candidate recall | Included recall | "
        "Included precision |"
    )
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for result in results:
        print(
            (
                "| {key} | {ground_truth_count} | {candidates_found} | {included} | "
                "{recall_candidates} | {recall_included} | {precision_included} |"
            ).format(
                key=result["key"],
                ground_truth_count=result["ground_truth_count"],
                candidates_found=result["candidates_found"],
                included=result["included"],
                recall_candidates=_format_metric(float(result["recall_candidates"])),
                recall_included=_format_metric(float(result["recall_included"])),
                precision_included=_format_metric(float(result["precision_included"])),
            )
        )


def write_summary(
    results: Sequence[Mapping[str, Any]],
    path: Path,
    *,
    max_works: int | str,
    snowball_depth: int,
    offline: bool,
    settings: Settings,
) -> None:
    """Write a compact markdown report without exposing configuration values."""

    metric_names = ("recall_candidates", "recall_included", "precision_included")
    macro = {
        name: sum(float(result.get(name, 0.0)) for result in results) / len(results)
        if results
        else 0.0
        for name in metric_names
    }
    lines = [
        "# Survey recall evaluation",
        "",
        f"Generated: {datetime.now(UTC).date().isoformat()}",
        "",
        "Settings used:",
        f"- max_works: {max_works}",
        f"- snowball_depth: {snowball_depth}",
        "- acquire_pdfs: false",
        f"- offline: {str(offline).lower()}",
        f"- OPENALEX_API_KEY: {'set' if settings.openalex_api_key else 'unset'}",
        f"- SEMANTIC_SCHOLAR_API_KEY: {'set' if settings.semantic_scholar_api_key else 'unset'}",
        "",
        "| Survey | R | Candidates | Included | Candidate recall | Included recall | "
        "Included precision |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for result in results:
        lines.append(
            (
                "| {key} | {ground_truth_count} | {candidates_found} | {included} | "
                "{recall_candidates} | {recall_included} | {precision_included} |"
            ).format(
                key=result["key"],
                ground_truth_count=result["ground_truth_count"],
                candidates_found=result["candidates_found"],
                included=result["included"],
                recall_candidates=_format_metric(float(result["recall_candidates"])),
                recall_included=_format_metric(float(result["recall_included"])),
                precision_included=_format_metric(float(result["precision_included"])),
            )
        )
    lines.extend(
        [
            "",
            "Macro averages:",
            "",
            f"- Candidate recall: {_format_metric(macro['recall_candidates'])}",
            f"- Included recall: {_format_metric(macro['recall_included'])}",
            f"- Included precision: {_format_metric(macro['precision_included'])}",
            "",
            "`R` is the survey's identity-resolved reference set. Missed reference titles "
            "are listed in each per-survey JSON file, capped at 50.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate research-pipeline recall against survey references."
    )
    parser.add_argument("--only", metavar="KEY", help="run one survey key")
    parser.add_argument(
        "--max-works", type=int, default=None, help="candidate/inclusion cap (default: 150)"
    )
    parser.add_argument(
        "--snowball-depth", type=int, default=1, help="citation expansion depth (default: 1)"
    )
    parser.add_argument("--offline", action="store_true", help="use only cached HTTP responses")
    parser.add_argument(
        "--cache-dir", type=Path, help="HTTP cache directory (default: settings.http_cache_dir)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.max_works is not None and args.max_works < 1:
        _parser().error("--max-works must be at least 1")
    if args.snowball_depth < 0:
        _parser().error("--snowball-depth must be non-negative")
    settings = Settings.from_env()
    surveys = load_surveys()
    if args.only:
        surveys = [survey for survey in surveys if survey["key"] == args.only]
        if not surveys:
            _parser().error(f"unknown survey key: {args.only}")
    cache_dir = args.cache_dir or settings.http_cache_dir
    sources = ResearchSources.default(
        cache_dir,
        openalex_api_key=settings.openalex_api_key,
        semantic_scholar_api_key=settings.semantic_scholar_api_key,
        contact_email=settings.contact_email,
        offline=args.offline,
    )
    effective_depth = int(args.snowball_depth)
    results_dir = DEFAULT_RESULTS_DIR
    results_dir.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    try:
        for survey in surveys:
            survey_max_works = int(
                args.max_works if args.max_works is not None else survey.get("max_works", 150)
            )
            if survey_max_works < 1:
                _parser().error(f"max_works for {survey['key']} must be at least 1")
            # One survey failing (e.g. a transient 5xx that outlasts the retries) must not
            # discard the others; rerun it with --only, the HTTP cache makes that cheap.
            try:
                result = run_survey(
                    survey,
                    sources,
                    max_works=survey_max_works,
                    snowball_depth=effective_depth,
                )
            except OfflineCacheMissError as exc:
                if args.offline:
                    raise RuntimeError(f"offline HTTP cache miss: {exc}") from exc
                failures.append(f"{survey['key']}: {exc}")
                continue
            except Exception as exc:
                if args.offline:
                    raise RuntimeError(f"offline recall evaluation failed: {exc}") from exc
                failures.append(f"{survey['key']}: {exc}")
                continue
            output = results_dir / f"{result['key']}.json"
            output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    finally:
        sources.close()

    # The summary covers every survey with a result file, so partial reruns accumulate.
    results = [
        json.loads(path.read_text(encoding="utf-8"))
        for survey in load_surveys()
        if (path := results_dir / f"{survey['key']}.json").is_file()
    ]
    print_results_table(results)
    write_summary(
        results,
        results_dir / "summary.md",
        max_works=(args.max_works if args.max_works is not None else "survey value or 150"),
        snowball_depth=effective_depth,
        offline=args.offline,
        settings=settings,
    )
    for failure in failures:
        print(f"survey failed: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"recall evaluation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
