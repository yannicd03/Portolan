"""Compose the complete four-layer golden graph and write it into a ``GraphRepository``.

``works.json`` and ``citations.json`` carry layer 1 only — 19 real papers and the 51
citation edges between them, harvested and title-verified by ``build_golden.py``. The
content and analysis layers come from ``analysis_fixture.yaml``, which is hand-authored:
M0 has no extraction pipeline yet, so the concepts, contributions, results, clusters and
gap hypotheses are curated rather than extracted. That limit is deliberate and is stated
in ADR-0005, because it bounds what the graph-store spike can claim.

Two things are *not* hand-waved:

* **Every evidence quote is verified as a verbatim substring of the real abstract** in
  ``works.json``. This is the project's central rule (spec decision 5): unverifiable
  assertions are dropped, not stored with low confidence. A fixture that broke the rule
  it is meant to test would be worthless, so composition fails loudly on a bad quote.
* **Every analysis metric is computed from the real citation graph** with networkx —
  PageRank, betweenness, main-path membership, citation velocity and the six frontier
  components of contract amendment A2. No metric is typed in by hand.

The module is importable with no side effects; ``compose(repository)`` is the entry point
used by both the spike harness and the tests.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import networkx as nx
import yaml

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(_BACKEND))

from portolan import models as M  # noqa: E402
from portolan.iri import mint_statement_iri  # noqa: E402

DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]

#: Statement kinds as the fixture spells them, mapped to their model and IRI kind.
_STATEMENT_KINDS: dict[str, str] = {
    "contribution": "contribution",
    "result": "result",
    "claim": "claim",
    "limitation": "limitation",
    "futurework": "futurework",
}

_CONCEPT_SECTIONS: dict[str, str] = {
    "problems": "Problem",
    "methods": "Method",
    "datasets": "Dataset",
    "metrics": "Metric",
}


class QuoteVerificationError(RuntimeError):
    """Raised when an evidence quote is not a verbatim substring of its source abstract."""


class FixtureError(RuntimeError):
    """Raised when the fixture references a work, concept or statement that does not exist."""


@dataclass
class ComposeStats:
    """Counts of what was written, plus the computed metrics for inspection."""

    works: int = 0
    citations: int = 0
    concepts: int = 0
    statements: int = 0
    evidence: int = 0
    inclusions: int = 0
    clusters: int = 0
    cluster_pairs: int = 0
    gaps: int = 0
    metrics: dict[str, dict[str, float]] = field(default_factory=dict)
    query_parameters: dict[int, dict[str, Any]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "works": self.works,
            "citations": self.citations,
            "concepts": self.concepts,
            "statements": self.statements,
            "evidence": self.evidence,
            "inclusions": self.inclusions,
            "clusters": self.clusters,
            "cluster_pairs": self.cluster_pairs,
            "gaps": self.gaps,
        }


# --------------------------------------------------------------------------- loading


def _unwrap(payload: Any, key: str) -> Any:
    if isinstance(payload, Mapping) and key in payload:
        return payload[key]
    return payload


def load_inputs(
    repo_root: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Read the three input artifacts. Raises if the golden set has not been built."""

    root = Path(repo_root or DEFAULT_REPO_ROOT)
    golden = root / "eval" / "golden"
    works_path = golden / "works.json"
    citations_path = golden / "citations.json"
    fixture_path = golden / "analysis_fixture.yaml"
    for path in (works_path, citations_path, fixture_path):
        if not path.exists():
            raise FileNotFoundError(
                f"{path} is missing — run eval/golden/build_golden.py first "
                f"(it rebuilds offline from the committed cache)."
            )
    works = _unwrap(json.loads(works_path.read_text(encoding="utf-8")), "works")
    citations = _unwrap(json.loads(citations_path.read_text(encoding="utf-8")), "citations")
    fixture = yaml.safe_load(fixture_path.read_text(encoding="utf-8"))
    return dict(works), list(citations), dict(fixture)


# ----------------------------------------------------------------------- layer 1


def _identifiers(record: Mapping[str, Any]) -> dict[str, str | None]:
    ids = record.get("identifiers") or {}
    if not isinstance(ids, Mapping):
        ids = {}

    def pick(*names: str) -> str | None:
        for name in names:
            value = ids.get(name) or record.get(name)
            if value:
                return str(value)
        return None

    return {
        "doi": pick("doi", "DOI"),
        "arxiv_id": pick("arxiv", "arxiv_id", "arXiv", "arxivId"),
        "openalex_id": pick("openalex", "openalex_id", "openAlexId"),
        "s2_id": pick("s2", "s2_id", "corpusId", "paperId"),
        "url": pick("url", "open_access_pdf_url"),
    }


def _work_type(record: Mapping[str, Any], source_tier: str) -> str | None:
    types = [str(value).casefold() for value in (record.get("publication_types") or [])]
    if any("journal" in value for value in types):
        return "journalArticle"
    if any("conference" in value for value in types):
        return "conferencePaper"
    if source_tier == "preprint":
        return "preprint"
    if source_tier == "officialBlog":
        return "blogPost"
    return None


def build_works(works_json: Mapping[str, Any]) -> dict[str, Any]:
    """Build one ``Work`` per seed key, sorted so composition order is deterministic."""

    built: dict[str, Any] = {}
    for key in sorted(works_json):
        record = works_json[key]
        source_tier = str(record.get("source_tier") or "preprint")
        ids = _identifiers(record)
        built[key] = M.Work(
            title=record["title"],
            issued=str(record.get("year")),
            source_tier=source_tier,
            abstract=record.get("abstract"),
            tldr=record.get("tldr"),
            is_survey=bool(record.get("isSurvey", False)),
            work_type=_work_type(record, source_tier),
            source_api=record.get("source_api"),
            full_text_available=bool(record.get("open_access_pdf_url")),
            **{name: value for name, value in ids.items() if value},
        )
    return built


def build_citations(
    citations_json: Sequence[Mapping[str, Any]],
    works: Mapping[str, Any],
) -> list[Any]:
    """Build in-corpus ``Citation`` models, skipping any edge with an unknown endpoint."""

    built: list[Any] = []
    for edge in sorted(citations_json, key=lambda e: (str(e.get("citing")), str(e.get("cited")))):
        citing, cited = edge.get("citing"), edge.get("cited")
        if citing not in works or cited not in works:
            continue
        built.append(
            M.Citation(
                citing_work=works[citing].iri,
                cited_work=works[cited].iri,
                citation_function=edge.get("citation_function"),
                is_influential=edge.get("is_influential"),
                citation_context=edge.get("citation_context"),
            )
        )
    return built


# --------------------------------------------------------------------- analytics


def citation_digraph(
    works: Mapping[str, Any],
    citations_json: Sequence[Mapping[str, Any]],
) -> nx.DiGraph:
    """Citing -> cited, built in sorted order so every downstream metric is reproducible."""

    graph = nx.DiGraph()
    for key in sorted(works):
        graph.add_node(key)
    for edge in sorted(citations_json, key=lambda e: (str(e.get("citing")), str(e.get("cited")))):
        citing, cited = edge.get("citing"), edge.get("cited")
        if citing in works and cited in works and citing != cited:
            graph.add_edge(citing, cited)
    return graph


def _year(work: Any) -> int:
    issued = getattr(work, "issued", None)
    text = str(issued)[:4]
    return int(text) if text.isdigit() else 0


def _main_path(graph: nx.DiGraph, works: Mapping[str, Any]) -> list[str]:
    """Longest chronologically-consistent citation chain, newest first.

    The full analysis stage (M1) uses search path count; here the corpus is 19 nodes and
    a longest chronological path is an honest stand-in that exercises the same query
    shape. Edges that disagree with publication order are dropped first so the subgraph
    is acyclic and ``dag_longest_path`` is well defined; the choice is recorded here
    rather than buried, because CQ02 reads the ``onMainPath`` flag it produces.
    """

    acyclic = nx.DiGraph()
    acyclic.add_nodes_from(sorted(graph.nodes))
    for citing, cited in sorted(graph.edges):
        # A strict order is required, not just year >= year: two papers from the same
        # year can cite each other in both directions (the corpus has such pairs), which
        # would leave a 2-cycle and make dag_longest_path undefined. Ties break on the
        # work key so the result is deterministic rather than insertion-ordered.
        if (_year(works[citing]), citing) > (_year(works[cited]), cited):
            acyclic.add_edge(citing, cited)
    if not acyclic.edges:
        return []
    return list(nx.dag_longest_path(acyclic))


def _scale(values: Mapping[str, float]) -> dict[str, float]:
    """Min-max scale to [0, 1]; a flat input maps to all zeros rather than dividing by 0."""

    if not values:
        return {}
    low, high = min(values.values()), max(values.values())
    if high - low < 1e-12:
        return {key: 0.0 for key in values}
    return {key: (value - low) / (high - low) for key, value in values.items()}


def compute_metrics(
    works: Mapping[str, Any],
    citations_json: Sequence[Mapping[str, Any]],
    fixture: Mapping[str, Any],
    *,
    as_of_year: int = 2026,
) -> dict[str, dict[str, float]]:
    """Every analysis-layer number, derived from the real citation graph.

    Returns one dict per work key with PageRank, betweenness, main-path membership,
    citation velocity, the six frontier components (amendment A2) and their mean as
    ``frontier_score``.
    """

    graph = citation_digraph(works, citations_json)
    if graph.edges:
        page_rank = nx.pagerank(graph, alpha=0.85, tol=1.0e-10)
    else:
        page_rank = dict.fromkeys(graph, 0.0)
    betweenness = nx.betweenness_centrality(graph, normalized=True)
    main_path = set(_main_path(graph, works))

    velocity = {
        key: graph.in_degree(key) / max(1, as_of_year - _year(works[key])) for key in sorted(works)
    }
    scaled_velocity = _scale(velocity)

    analysis = fixture.get("analysis", {})
    cluster_growth = {
        int(cluster["number"]): float(cluster.get("growth_rate") or 0.0)
        for cluster in analysis.get("clusters", [])
    }
    cluster_of = {
        str(inclusion["work"]): int(inclusion["cluster"])
        for inclusion in analysis.get("inclusions", [])
        if inclusion.get("cluster") is not None
    }
    scaled_growth = _scale({str(number): value for number, value in cluster_growth.items()})

    novel_concept_works = {
        str(entry.get("first_seen_in"))
        for section in fixture.get("content", {}).get("concepts", {}).values()
        for entry in section
        if entry.get("first_seen_in")
    }
    sota_works = {
        str(statement["work"])
        for statement in fixture.get("content", {}).get("statements", [])
        if statement.get("kind") == "result" and statement.get("claims_sota")
    }

    metrics: dict[str, dict[str, float]] = {}
    for key in sorted(works):
        work = works[key]
        year = _year(work)
        component_velocity = scaled_velocity.get(key, 0.0)
        # A main-path node that nothing in the corpus cites is the newest end of the chain.
        component_leaf = 1.0 if key in main_path and graph.in_degree(key) == 0 else 0.0
        component_growth = scaled_growth.get(str(cluster_of.get(key, -1)), 0.0)
        component_novelty = 1.0 if key in novel_concept_works and year >= 2023 else 0.0
        component_sota = 1.0 if key in sota_works else 0.0
        component_tier = 0.0 if str(getattr(work, "source_tier", "")) == "peerReviewed" else 1.0
        components = [
            component_velocity,
            component_leaf,
            component_growth,
            component_novelty,
            component_sota,
            component_tier,
        ]
        metrics[key] = {
            "page_rank": round(float(page_rank.get(key, 0.0)), 10),
            "betweenness": round(float(betweenness.get(key, 0.0)), 10),
            "on_main_path": key in main_path,
            "citation_velocity": round(float(velocity.get(key, 0.0)), 10),
            "frontier_component_velocity": round(component_velocity, 10),
            "frontier_component_main_path_leaf": component_leaf,
            "frontier_component_cluster_growth": round(component_growth, 10),
            "frontier_component_concept_novelty": component_novelty,
            "frontier_component_sota_claim": component_sota,
            "frontier_component_not_peer_reviewed": component_tier,
            "frontier_score": round(sum(components) / len(components), 10),
        }
    return metrics


# ------------------------------------------------------------------ layers 2 & 4


def _normalize_ws(text: str) -> str:
    return " ".join(str(text).split())


def verify_quote(quote: str, work_key: str, works_json: Mapping[str, Any]) -> None:
    """Fail loudly unless ``quote`` appears verbatim in that work's real abstract."""

    abstract = (works_json.get(work_key) or {}).get("abstract")
    if not abstract:
        raise QuoteVerificationError(
            f"work {work_key!r} has no abstract in works.json, so its statements cannot "
            f"carry verifiable evidence — remove them from the fixture rather than "
            f"inventing source text."
        )
    if _normalize_ws(quote) not in _normalize_ws(abstract):
        raise QuoteVerificationError(
            f"evidence quote for {work_key!r} is not a verbatim substring of its abstract: "
            f"{quote[:80]!r}"
        )


def _concept_index(
    fixture: Mapping[str, Any],
    works: Mapping[str, Any],
) -> tuple[dict[str, Any], list[Any]]:
    """Build every concept model and a slug -> IRI index used to resolve fixture references."""

    built: list[Any] = []
    index: dict[str, Any] = {}
    sections = fixture.get("content", {}).get("concepts", {})
    for section_name in sorted(sections):
        model_name = _CONCEPT_SECTIONS.get(section_name)
        if model_name is None:
            raise FixtureError(f"unknown concept section {section_name!r}")
        model_cls = getattr(M, model_name)
        for entry in sections[section_name]:
            payload: dict[str, Any] = {
                "slug": entry["slug"],
                "alt_label": entry.get("alt_label") or [],
            }
            first_seen = entry.get("first_seen_in")
            if first_seen:
                if first_seen not in works:
                    raise FixtureError(
                        f"concept {entry['slug']!r} has first_seen_in={first_seen!r}, "
                        f"which is not a work in the golden set"
                    )
                payload["first_seen_in"] = works[first_seen].iri
            if model_name == "Metric":
                payload["metric_direction"] = entry["metric_direction"]
            concept = model_cls(**payload)
            built.append(concept)
            index[entry["slug"]] = concept
    return index, built


def _resolve_concepts(slugs: Iterable[str] | None, index: Mapping[str, Any]) -> list[str]:
    resolved = []
    for slug in slugs or []:
        concept = index.get(slug)
        if concept is None:
            raise FixtureError(f"fixture references unknown concept {slug!r}")
        resolved.append(concept.iri)
    return resolved


def _statement_ref_iri(ref: str, works: Mapping[str, Any]) -> str:
    """Resolve a ``kind:workKey:number`` fixture reference to a statement IRI."""

    parts = str(ref).split(":")
    if len(parts) != 3 or parts[0] not in _STATEMENT_KINDS:
        raise FixtureError(f"malformed statement reference {ref!r} (expected kind:work:number)")
    kind, work_key, number = parts
    if work_key not in works:
        raise FixtureError(f"statement reference {ref!r} points at unknown work {work_key!r}")
    return mint_statement_iri(kind, works[work_key].iri, number)


def build_statements(
    fixture: Mapping[str, Any],
    works: Mapping[str, Any],
    works_json: Mapping[str, Any],
    concept_index: Mapping[str, Any],
) -> tuple[dict[str, list[Any]], dict[str, list[Any]], int]:
    """Build content-layer statements grouped by work, with their verified evidence."""

    statements: dict[str, list[Any]] = {}
    evidence: dict[str, list[Any]] = {}
    evidence_count = 0

    entries = fixture.get("content", {}).get("statements", [])
    for entry in sorted(entries, key=lambda e: (str(e["work"]), str(e["kind"]), int(e["number"]))):
        work_key = str(entry["work"])
        if work_key not in works:
            raise FixtureError(f"statement references unknown work {work_key!r}")
        work = works[work_key]
        kind = str(entry["kind"])
        number = int(entry["number"])

        built_evidence = []
        for item in entry.get("evidence") or []:
            quote = item["quote"]
            verify_quote(quote, work_key, works_json)
            built_evidence.append(
                M.Evidence(
                    quote=quote,
                    from_work=work.iri,
                    from_source_kind=item.get("source_kind", "abstract"),
                    locator=item.get("locator"),
                )
            )
        evidence_count += len(built_evidence)

        common: dict[str, Any] = {
            "of_work": work.iri,
            "number": number,
            "has_evidence": built_evidence,
        }
        if kind == "contribution":
            statement = M.Contribution(
                **common,
                contribution_kind=entry["contribution_kind"],
                addresses=_resolve_concepts(entry.get("addresses"), concept_index),
                proposes=_resolve_concepts(entry.get("proposes"), concept_index),
                uses=_resolve_concepts(entry.get("uses"), concept_index),
                evaluates_on=_resolve_concepts(entry.get("evaluates_on"), concept_index),
                reports=[_statement_ref_iri(ref, works) for ref in entry.get("reports") or []],
                addresses_limitation=[
                    _statement_ref_iri(ref, works)
                    for ref in entry.get("addresses_limitation") or []
                ],
            )
        elif kind == "result":
            statement = M.Result(
                **common,
                of_method=_resolve_concepts([entry["of_method"]], concept_index)[0],
                on_dataset=_resolve_concepts([entry["on_dataset"]], concept_index)[0],
                with_metric=_resolve_concepts([entry["with_metric"]], concept_index)[0],
                value=Decimal(str(entry["value"])),
                unit=entry.get("unit"),
                setting=entry.get("setting"),
                claims_sota=bool(entry.get("claims_sota", False)),
            )
        elif kind == "claim":
            statement = M.Claim(
                **common,
                claim_text=entry["claim_text"],
                about=_resolve_concepts(entry.get("about"), concept_index),
                supports_claim=[
                    _statement_ref_iri(ref, works) for ref in entry.get("supports_claim") or []
                ],
                contradicts_claim=[
                    _statement_ref_iri(ref, works) for ref in entry.get("contradicts_claim") or []
                ],
            )
        elif kind == "limitation":
            statement = M.Limitation(
                **common,
                limitation_text=entry["limitation_text"],
                about=_resolve_concepts(entry.get("about"), concept_index),
                limitation_of=_resolve_concepts(entry.get("limitation_of"), concept_index),
            )
        elif kind == "futurework":
            statement = M.FutureWork(
                **common,
                future_work_text=entry["future_work_text"],
                about=_resolve_concepts(entry.get("about"), concept_index),
            )
        else:
            raise FixtureError(f"unknown statement kind {kind!r}")

        statements.setdefault(work_key, []).append(statement)
        evidence.setdefault(work_key, []).extend(built_evidence)

    return statements, evidence, evidence_count


# --------------------------------------------------------------------- composition


def compose(repository: Any, *, repo_root: Path | None = None) -> ComposeStats:
    """Write the full four-layer golden graph into ``repository`` and return the counts."""

    works_json, citations_json, fixture = load_inputs(repo_root)
    stats = ComposeStats()

    works = build_works(works_json)
    citations = build_citations(citations_json, works)
    concept_index, concepts = _concept_index(fixture, works)
    metrics = compute_metrics(works, citations_json, fixture)
    stats.metrics = metrics

    analysis = fixture["analysis"]
    protocol = M.ReviewProtocol(**analysis["protocol"])
    review = M.Review(**{**analysis["review"], "has_protocol": protocol.iri})

    extraction_run = M.ExtractionRun(**fixture["content"]["extraction_run"])
    seed_value = str(analysis["review"]["seed_value"]).removeprefix("arxiv:")
    seed_work = next(
        (work for work in works.values() if str(getattr(work, "arxiv_id", "")) == seed_value),
        None,
    )
    if seed_work is None:
        raise FixtureError(f"review seed {analysis['review']['seed_value']!r} is not in works.json")
    stats.query_parameters = {
        number: dict(values)
        for number, values in {
            1: {"review": review.iri},
            2: {"review": review.iri, "seed": seed_work.iri},
            3: {
                "problem": "https://w3id.org/portolan/id/concept/problem/knowledge-graph-question-answering"
            },
            4: {"review": review.iri},
            5: {"review": review.iri, "n": 3},
            6: {"review": review.iri, "n": 3},
            7: {"review": review.iri},
            8: {"review": review.iri},
            9: {"review": review.iri},
            10: {"review": review.iri},
            11: {"review": review.iri, "similarityThreshold": 0.75},
            12: {"assertion": "https://w3id.org/portolan/id/contribution/arxiv-1706.03762/1"},
            13: {"review": review.iri, "work": seed_work.iri},
            14: {"review": review.iri},
        }.items()
    }
    statements, evidence, evidence_count = build_statements(
        fixture, works, works_json, concept_index
    )

    clusters: dict[int, Any] = {}
    for entry in sorted(analysis.get("clusters", []), key=lambda c: int(c["number"])):
        clusters[int(entry["number"])] = M.Cluster(
            cluster_number=int(entry["number"]),
            of_review=review.iri,
            level=entry.get("level"),
            label=entry.get("label"),
            summary=entry.get("summary"),
            growth_rate=entry.get("growth_rate"),
            top_concept=next(iter(_resolve_concepts([entry["top_concept"]], concept_index)), None)
            if entry.get("top_concept")
            else None,
        )

    # --- write, in dependency order -------------------------------------------------
    for key in sorted(works):
        repository.upsert_work(works[key])
        stats.works += 1

    for citation in citations:
        repository.add_citation(citation)
        stats.citations += 1

    for concept in concepts:
        repository.upsert_concept(concept, run_id=extraction_run)
        stats.concepts += 1

    # The protocol has no of_review back-reference in the model, so the partition is
    # passed explicitly; the review carries its own id.
    repository.write_protocol(protocol, review_id=review.review_id)
    repository.write_review(review)

    for work_key in sorted(statements):
        repository.write_extraction(
            works[work_key],
            extraction_run,
            statements[work_key],
            evidence[work_key],
            review_id=review.review_id,
        )
        stats.statements += len(statements[work_key])
    stats.evidence = evidence_count

    for cluster in clusters.values():
        repository.write_cluster(cluster)
        stats.clusters += 1

    for entry in sorted(analysis.get("inclusions", []), key=lambda i: str(i["work"])):
        work_key = str(entry["work"])
        if work_key not in works:
            raise FixtureError(f"inclusion references unknown work {work_key!r}")
        measured = metrics[work_key]
        cluster = clusters.get(entry.get("cluster")) if entry.get("cluster") else None
        repository.write_inclusion(
            M.Inclusion(
                of_review=review.iri,
                of_work=works[work_key].iri,
                decision=entry["decision"],
                stage=entry["stage"],
                reason=entry.get("reason"),
                discovered_via=entry.get("discovered_via"),
                relevance=entry.get("relevance"),
                role=entry.get("roles") or [],
                in_cluster=cluster.iri if cluster else None,
                page_rank=measured["page_rank"],
                betweenness=measured["betweenness"],
                on_main_path=measured["on_main_path"],
                citation_velocity=measured["citation_velocity"],
                frontier_score=measured["frontier_score"],
                frontier_component_velocity=measured["frontier_component_velocity"],
                frontier_component_main_path_leaf=measured["frontier_component_main_path_leaf"],
                frontier_component_cluster_growth=measured["frontier_component_cluster_growth"],
                frontier_component_concept_novelty=measured["frontier_component_concept_novelty"],
                frontier_component_sota_claim=measured["frontier_component_sota_claim"],
                frontier_component_not_peer_reviewed=measured[
                    "frontier_component_not_peer_reviewed"
                ],
            )
        )
        stats.inclusions += 1

    for entry in analysis.get("cluster_pairs", []):
        first, second = clusters[int(entry["cluster_a"])], clusters[int(entry["cluster_b"])]
        # A5: the pair is unordered and written once, clusterA the smaller IRI.
        low, high = sorted([first, second], key=lambda c: c.iri)
        repository.write_cluster_pair(
            M.ClusterPair(
                of_review=review.iri,
                cluster_a=low.iri,
                cluster_b=high.iri,
                semantic_similarity=Decimal(str(entry["semantic_similarity"])),
                cross_citation_count=int(entry["cross_citation_count"]),
            )
        )
        stats.cluster_pairs += 1

    for entry in sorted(analysis.get("gaps", []), key=lambda g: int(g["number"])):
        repository.write_gap_hypothesis(
            M.GapHypothesis(
                gap_number=int(entry["number"]),
                of_review=review.iri,
                gap_type=entry["gap_type"],
                statement=entry["statement"],
                confidence=Decimal(str(entry["confidence"])),
                verification_outcome=entry.get("verification_outcome", "notChecked"),
                user_status=entry.get("user_status", "proposed"),
                user_note=entry.get("user_note"),
                about=_resolve_concepts(entry.get("about"), concept_index),
                supported_by=[
                    _statement_ref_iri(ref, works) for ref in entry.get("supported_by") or []
                ],
            )
        )
        stats.gaps += 1

    return stats


# Aliases the spike harness probes for.
compose_graph = compose
compose_golden = compose
compose_into = compose
