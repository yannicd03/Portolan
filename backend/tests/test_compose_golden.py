"""Tests for the golden-graph composer.

The composer is the only thing standing between a hand-authored fixture and the spike's
conclusions, so these tests hold it to the two promises it makes: every evidence quote is
verbatim, and every analysis metric is derived rather than typed in.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSER = REPO_ROOT / "eval" / "golden" / "compose_golden.py"


def _load_composer():
    spec = importlib.util.spec_from_file_location("arg_test_compose_golden", COMPOSER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


cg = _load_composer()

pytestmark = pytest.mark.skipif(
    not (REPO_ROOT / "eval" / "golden" / "works.json").exists(),
    reason="golden set not built; run eval/golden/build_golden.py",
)


@pytest.fixture(scope="module")
def repository():
    from portolan.store.oxigraph_store import OxigraphRepository

    return OxigraphRepository()


@pytest.fixture(scope="module")
def stats(repository):
    return cg.compose(repository)


def test_every_layer_is_populated(stats):
    counts = stats.as_dict()
    # Layer 1 is the real harvest; the rest comes from the fixture. Exact numbers are
    # asserted so a silently shrinking fixture fails here rather than in the spike.
    assert counts["works"] == 19
    assert counts["citations"] == 51
    assert counts["concepts"] > 20
    assert counts["statements"] > 40
    assert counts["evidence"] == counts["statements"]
    assert counts["inclusions"] == 19
    assert counts["clusters"] >= 4
    assert counts["cluster_pairs"] >= 3
    assert counts["gaps"] >= 4


def test_every_evidence_quote_is_verbatim():
    works_json, _, fixture = cg.load_inputs()
    checked = 0
    for statement in fixture["content"]["statements"]:
        for item in statement.get("evidence") or []:
            cg.verify_quote(item["quote"], statement["work"], works_json)
            checked += 1
    assert checked > 40


def test_a_corrupted_quote_fails_loudly():
    works_json, _, _ = cg.load_inputs()
    with pytest.raises(cg.QuoteVerificationError):
        cg.verify_quote("this sentence appears in no abstract anywhere", "transformer", works_json)


def test_a_paraphrase_is_not_accepted():
    """A near-miss must fail: the rule is verbatim, not approximately verbatim."""

    works_json, _, _ = cg.load_inputs()
    abstract = works_json["transformer"]["abstract"]
    real = " ".join(abstract.split())[:60]
    paraphrase = real.replace(" is ", " was ") if " is " in real else real.replace("e", "3", 1)
    with pytest.raises(cg.QuoteVerificationError):
        cg.verify_quote(paraphrase, "transformer", works_json)


def test_metrics_are_deterministic():
    works_json, citations_json, fixture = cg.load_inputs()
    works = cg.build_works(works_json)
    first = cg.compute_metrics(works, citations_json, fixture)
    second = cg.compute_metrics(works, citations_json, fixture)
    assert first == second


def test_metrics_are_derived_from_the_real_citation_graph():
    works_json, citations_json, fixture = cg.load_inputs()
    works = cg.build_works(works_json)
    graph = cg.citation_digraph(works, citations_json)
    metrics = cg.compute_metrics(works, citations_json, fixture)

    assert graph.number_of_edges() == 51
    # PageRank is a distribution over the corpus.
    assert abs(sum(m["page_rank"] for m in metrics.values()) - 1.0) < 1e-6
    # The most-cited work in the corpus must outrank a leaf.
    most_cited = max(graph.nodes, key=lambda n: graph.in_degree(n))
    least_cited = min(graph.nodes, key=lambda n: graph.in_degree(n))
    assert metrics[most_cited]["page_rank"] > metrics[least_cited]["page_rank"]
    # Every frontier component stays inside [0, 1] (contract A2).
    for measured in metrics.values():
        for name, value in measured.items():
            if name.startswith("frontier_component") or name == "frontier_score":
                assert 0.0 <= float(value) <= 1.0


def test_main_path_is_chronological():
    works_json, citations_json, fixture = cg.load_inputs()
    works = cg.build_works(works_json)
    metrics = cg.compute_metrics(works, citations_json, fixture)
    path_years = [
        int(str(works[key].issued)[:4])
        for key, measured in sorted(metrics.items())
        if measured["on_main_path"]
    ]
    assert len(path_years) >= 3
    assert min(path_years) < max(path_years)


def test_unknown_concept_reference_is_rejected():
    with pytest.raises(cg.FixtureError):
        cg._resolve_concepts(["no-such-concept"], {})


def test_malformed_statement_reference_is_rejected():
    works_json, _, _ = cg.load_inputs()
    works = cg.build_works(works_json)
    with pytest.raises(cg.FixtureError):
        cg._statement_ref_iri("not-a-reference", works)
