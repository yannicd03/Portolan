from __future__ import annotations

import hashlib

import pytest

from portolan.iri import (
    BIBLIO_GRAPH,
    PTLR_NAMESPACE,
    content_graph_iri,
    mint_author_iri,
    mint_citation_iri,
    mint_cluster_iri,
    mint_concept_iri,
    mint_evidence_iri,
    mint_gap_hypothesis_iri,
    mint_inclusion_iri,
    mint_organization_iri,
    mint_protocol_iri,
    mint_review_iri,
    mint_statement_iri,
    mint_venue_iri,
    mint_work_iri,
    parse_author_iri,
    parse_citation_iri,
    parse_cluster_iri,
    parse_concept_iri,
    parse_content_graph_iri,
    parse_evidence_iri,
    parse_gap_hypothesis_iri,
    parse_graph_iri,
    parse_inclusion_iri,
    parse_organization_iri,
    parse_protocol_iri,
    parse_review_iri,
    parse_statement_iri,
    parse_venue_iri,
    parse_work_iri,
    review_graph_iri,
)


def test_work_minting_prefers_contract_identifier_order() -> None:
    work = mint_work_iri(
        doi="10.1000/example",
        arxiv_id="1706.03762",
        openalex_id="W1",
        s2_id="S1",
        url="https://example.test/paper",
    )

    assert work == f"{PTLR_NAMESPACE}work/doi/10.1000%2Fexample"
    assert parse_work_iri(work) == {"scheme": "doi", "id": "10.1000/example"}


def test_work_iri_percent_encodes_awkward_doi_and_round_trips() -> None:
    work = mint_work_iri(doi="10.5555/a DOI/with?punctuation#fragment")

    assert work.endswith("10.5555%2Fa%20DOI%2Fwith%3Fpunctuation%23fragment")
    assert parse_work_iri(work)["id"] == "10.5555/a DOI/with?punctuation#fragment"


@pytest.mark.parametrize(
    ("mint", "parse", "kwargs", "parsed"),
    [
        (
            mint_author_iri,
            parse_author_iri,
            {"orcid": "0000-0001/2"},
            {"scheme": "orcid", "id": "0000-0001/2"},
        ),
        (
            mint_organization_iri,
            parse_organization_iri,
            {"domain": "research.example/a"},
            {"scheme": "domain", "id": "research.example/a"},
        ),
        (
            mint_venue_iri,
            parse_venue_iri,
            {"issn": "1234/5678"},
            {"scheme": "issn", "id": "1234/5678"},
        ),
    ],
)
def test_identifier_iris_have_unambiguous_inverse(mint, parse, kwargs, parsed) -> None:
    assert parse(mint(**kwargs)) == parsed


def test_reified_hash_iris_and_review_layer_paths() -> None:
    citing = mint_work_iri(arxiv_id="2211.17192")
    cited = mint_work_iri(arxiv_id="1706.03762")
    citation = mint_citation_iri(citing, cited)
    expected_citation_hash = hashlib.sha1(
        (citing + cited).encode(), usedforsecurity=False
    ).hexdigest()
    assert parse_citation_iri(citation) == {"sha1": expected_citation_hash}

    evidence = mint_evidence_iri("A quoted finding", cited)
    expected_evidence_hash = hashlib.sha1(
        ("A quoted finding" + cited).encode(), usedforsecurity=False
    ).hexdigest()
    assert parse_evidence_iri(evidence) == {"sha1": expected_evidence_hash}

    assert parse_concept_iri(mint_concept_iri("method", "speculative/decoding")) == {
        "kind": "method",
        "slug": "speculative/decoding",
    }
    assert parse_statement_iri(mint_statement_iri("claim", cited, 3)) == {
        "kind": "claim",
        "work_slug": "arxiv-1706.03762",
        "number": "3",
    }

    review = mint_review_iri("review 1/α")
    protocol = mint_protocol_iri("protocol 1")
    inclusion = mint_inclusion_iri(review, cited)
    cluster = mint_cluster_iri(review, 2)
    gap = mint_gap_hypothesis_iri(review, 4)
    assert parse_review_iri(review) == {"id": "review 1/α"}
    assert parse_protocol_iri(protocol) == {"id": "protocol 1"}
    assert parse_inclusion_iri(inclusion) == {
        "review_id": "review 1/α",
        "work_slug": "arxiv-1706.03762",
    }
    assert parse_cluster_iri(cluster) == {"review_id": "review 1/α", "number": "2"}
    assert parse_gap_hypothesis_iri(gap) == {"review_id": "review 1/α", "number": "4"}


def test_named_graph_iris_round_trip() -> None:
    content = content_graph_iri("run 1/α")
    review = review_graph_iri("review/1")

    assert parse_graph_iri(BIBLIO_GRAPH) == {"kind": "biblio"}
    assert parse_content_graph_iri(content) == {"run_id": "run 1/α"}
    assert parse_graph_iri(content) == {"kind": "content", "run_id": "run 1/α"}
    assert parse_graph_iri(review) == {"kind": "review", "review_id": "review/1"}
