from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from portolan.models import (
    CitationFunction,
    Claim,
    Cluster,
    ClusterPair,
    Contribution,
    ContributionKind,
    Decision,
    DiscoveredVia,
    Evidence,
    ExtractionRun,
    FromSourceKind,
    GapHypothesis,
    GapType,
    Inclusion,
    Limitation,
    Method,
    Metric,
    MetricDirection,
    OrgKind,
    Problem,
    Result,
    Review,
    ReviewProtocol,
    Role,
    SeedKind,
    SourceTier,
    Stage,
    UserStatus,
    VenueKind,
    VerificationOutcome,
    Work,
    WorkType,
)


def _work(identifier: str = "10.1000/example") -> Work:
    return Work(
        title="A work",
        issued=date(2024, 1, 2),
        source_tier=SourceTier.PREPRINT,
        doi=identifier,
    )


def _evidence(work: Work) -> Evidence:
    return Evidence(
        quote="The reported finding.",
        from_work=work,
        from_source_kind=FromSourceKind.FULL_TEXT,
    )


def test_enumerations_match_the_contract_exactly() -> None:
    assert [value.value for value in SourceTier] == [
        "peerReviewed",
        "preprint",
        "officialBlog",
    ]
    assert [value.value for value in OrgKind] == [
        "academic",
        "industry",
        "nonprofit",
        "government",
    ]
    assert [value.value for value in VenueKind] == [
        "journal",
        "conference",
        "workshop",
        "repository",
        "blog",
    ]
    assert [value.value for value in SeedKind] == ["paper", "topic", "prompt"]
    assert [value.value for value in Decision] == ["included", "excluded"]
    assert [value.value for value in Stage] == ["search", "screening", "fulltext"]
    assert [value.value for value in DiscoveredVia] == [
        "seed",
        "search",
        "backward",
        "forward",
        "cocitation",
        "coupling",
        "semantic",
        "orkg",
    ]
    assert [value.value for value in Role] == [
        "foundational",
        "backbone",
        "bridge",
        "survey",
        "rising",
    ]
    assert [value.value for value in GapType] == [
        "matrixVoid",
        "statedUnaddressed",
        "contradiction",
        "evaluationGap",
        "bridgingGap",
        "stagnation",
    ]
    assert [value.value for value in VerificationOutcome] == [
        "notChecked",
        "noCounterEvidence",
        "partiallyAddressed",
        "refuted",
    ]
    assert [value.value for value in UserStatus] == ["proposed", "accepted", "rejected"]
    assert [value.value for value in ContributionKind] == [
        "method",
        "dataset",
        "benchmark",
        "analysis",
        "theory",
        "system",
        "survey",
        "position",
    ]
    assert [value.value for value in FromSourceKind] == ["abstract", "fullText", "blogHtml"]
    assert [value.value for value in WorkType] == [
        "journalArticle",
        "conferencePaper",
        "preprint",
        "technicalReport",
        "blogPost",
        "thesis",
        "bookChapter",
    ]
    assert [value.value for value in MetricDirection] == [
        "higherIsBetter",
        "lowerIsBetter",
    ]
    assert [value.value for value in CitationFunction] == [
        "cito:obtainsBackgroundFrom",
        "cito:usesMethodIn",
        "cito:usesDataFrom",
        "cito:extends",
        "cito:critiques",
        "cito:disputes",
        "cito:supports",
        "cito:reviews",
    ]


def test_work_requires_identifier_and_exposes_computed_iri() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        Work(title="No identifier", issued="2024", source_tier=SourceTier.PREPRINT)

    work = Work(
        title="A work",
        issued="2024",
        source_tier=SourceTier.PREPRINT,
        url="https://example.test/work",
        work_type=WorkType.PREPRINT,
    )
    assert work.iri.endswith("/work/url/https%3A%2F%2Fexample.test%2Fwork")
    assert Work.property_map()["source_tier"] == {
        "rdf": "ptl:sourceTier",
        "lpg": "sourceTier",
    }


def test_statement_evidence_must_match_the_statement_work() -> None:
    work = _work()
    other_work = _work("10.1000/other")

    with pytest.raises(ValidationError, match="from_work equals"):
        Claim(
            of_work=work,
            claim_text="A claim",
            has_evidence=[_evidence(other_work)],
        )

    claim = Claim(
        of_work=work,
        claim_text="A claim",
        has_evidence=[_evidence(work)],
        number=2,
    )
    assert "/claim/doi-10.1000%2Fexample/2" in claim.iri


def test_result_requires_method_dataset_metric_and_value() -> None:
    work = _work()
    method = Method(slug="method-a")
    with pytest.raises(ValidationError):
        Result(of_work=work, has_evidence=[_evidence(work)])

    result = Result(
        of_work=work,
        of_method=method,
        on_dataset="dataset-a",
        with_metric="accuracy",
        value=Decimal("0.91"),
        has_evidence=[_evidence(work)],
    )
    assert result.value == Decimal("0.91")


def test_metric_direction_is_required_and_uses_the_closed_vocabulary() -> None:
    with pytest.raises(ValidationError):
        Metric(slug="accuracy")

    metric = Metric(slug="accuracy", metricDirection="higherIsBetter")
    assert metric.metric_direction is MetricDirection.HIGHER_IS_BETTER
    assert Metric.property_map()["metric_direction"] == {
        "rdf": "ptl:metricDirection",
        "lpg": "metricDirection",
    }

    with pytest.raises(ValidationError):
        Metric(slug="perplexity", metric_direction="unchanged")


def test_excluded_inclusion_needs_reason_and_gap_needs_support() -> None:
    work = _work()
    protocol = ReviewProtocol(protocol_id="protocol-1")
    review = Review(
        review_id="review-1",
        seed_kind=SeedKind.PAPER,
        seed_value=work.iri,
        has_protocol=protocol,
    )
    with pytest.raises(ValidationError, match="requires a reason"):
        Inclusion(
            of_review=review,
            of_work=work,
            decision=Decision.EXCLUDED,
            stage=Stage.SCREENING,
        )

    included = Inclusion(
        of_review=review,
        of_work=work,
        decision=Decision.INCLUDED,
        stage=Stage.FULLTEXT,
        discovered_via=DiscoveredVia.SEED,
        role=[Role.FOUNDATIONAL],
    )
    assert "/inclusion/review-1/doi-10.1000%2Fexample" in included.iri

    with pytest.raises(ValidationError, match="supported_by or about"):
        GapHypothesis(
            gap_number=1,
            of_review=review,
            gap_type=GapType.MATRIX_VOID,
            statement="A possible gap",
            confidence=0.5,
            verification_outcome=VerificationOutcome.NOT_CHECKED,
            user_status=UserStatus.PROPOSED,
        )

    gap = GapHypothesis(
        gap_number=1,
        of_review=review,
        gap_type=GapType.MATRIX_VOID,
        statement="A possible gap",
        about=[Problem(slug="problem-a")],
        confidence=0.5,
        verification_outcome=VerificationOutcome.NOT_CHECKED,
        user_status=UserStatus.PROPOSED,
    )
    assert gap.iri.endswith("/gap/review-1/1")

    with pytest.raises(ValidationError):
        GapHypothesis(
            gap_number=2,
            of_review=review,
            gap_type=GapType.MATRIX_VOID,
            statement="An invalid confidence",
            about=["problem-a"],
            confidence=1.1,
            verification_outcome=VerificationOutcome.NOT_CHECKED,
            user_status=UserStatus.PROPOSED,
        )


def test_v02_supported_by_and_addresses_limitation_ranges_are_enforced() -> None:
    work = _work()
    evidence = _evidence(work)
    limitation = Limitation(
        of_work=work,
        limitation_text="The evaluation is narrow.",
        has_evidence=[evidence],
    )
    protocol = ReviewProtocol(protocol_id="protocol-v02")
    review = Review(
        review_id="review-v02",
        seed_kind=SeedKind.PAPER,
        seed_value="10.1000/example",
        has_protocol=protocol,
    )

    gap = GapHypothesis(
        gap_number=1,
        of_review=review,
        gap_type=GapType.STATED_UNADDRESSED,
        statement="The narrow evaluation remains unaddressed.",
        supported_by=limitation,
        confidence=Decimal("0.5"),
        verification_outcome=VerificationOutcome.NOT_CHECKED,
        user_status=UserStatus.PROPOSED,
    )
    assert gap.supported_by == [limitation]

    with pytest.raises(ValidationError):
        GapHypothesis(
            gap_number=2,
            of_review=review,
            gap_type=GapType.STATED_UNADDRESSED,
            statement="Evidence is not a supportedBy target in v0.2.",
            supported_by=evidence,
            confidence=Decimal("0.5"),
            verification_outcome=VerificationOutcome.NOT_CHECKED,
            user_status=UserStatus.PROPOSED,
        )
    with pytest.raises(ValidationError, match="cannot target Evidence"):
        GapHypothesis(
            gap_number=3,
            of_review=review,
            gap_type=GapType.STATED_UNADDRESSED,
            statement="An Evidence IRI is also not a supportedBy target.",
            supported_by=evidence.iri,
            confidence=Decimal("0.5"),
            verification_outcome=VerificationOutcome.NOT_CHECKED,
            user_status=UserStatus.PROPOSED,
        )

    contribution = Contribution(
        of_work=work,
        has_evidence=[evidence],
        contribution_kind=ContributionKind.ANALYSIS,
        addresses_limitation=limitation,
    )
    assert contribution.addresses_limitation == [limitation]
    assert Contribution.property_map()["addresses_limitation"] == {
        "rdf": "ptl:addressesLimitation",
        "lpg": "ADDRESSES_LIMITATION",
    }


def test_inclusion_frontier_score_requires_all_six_bounded_components() -> None:
    work = _work()
    review = Review(
        review_id="review-frontier",
        seed_kind=SeedKind.TOPIC,
        seed_value="bounded frontier",
        has_protocol=ReviewProtocol(protocol_id="protocol-frontier"),
    )
    components = {
        "frontier_component_velocity": Decimal("0.10"),
        "frontier_component_main_path_leaf": Decimal("0.20"),
        "frontier_component_cluster_growth": Decimal("0.30"),
        "frontier_component_concept_novelty": Decimal("0.40"),
        "frontier_component_sota_claim": Decimal("0.50"),
        "frontier_component_not_peer_reviewed": Decimal("1.00"),
    }
    inclusion = Inclusion(
        of_review=review,
        of_work=work,
        decision=Decision.INCLUDED,
        stage=Stage.FULLTEXT,
        frontier_score=0.75,
        **components,
    )
    assert inclusion.frontier_component_velocity == Decimal("0.10")
    assert inclusion.frontier_component_not_peer_reviewed == Decimal("1.00")
    assert "frontier_component" not in Inclusion.model_fields
    assert {
        name for name in Inclusion.property_map() if name.startswith("frontier_component_")
    } == set(components)

    with pytest.raises(ValidationError, match="all six frontier components"):
        Inclusion(
            of_review=review,
            of_work=work,
            decision=Decision.INCLUDED,
            stage=Stage.FULLTEXT,
            frontier_score=0.75,
            frontier_component_velocity=Decimal("0.1"),
        )

    with pytest.raises(ValidationError):
        Inclusion(
            of_review=review,
            of_work=work,
            decision=Decision.INCLUDED,
            stage=Stage.FULLTEXT,
            **components | {"frontier_component_velocity": Decimal("1.01")},
        )


def test_cluster_pair_is_ordered_and_has_bounded_measures() -> None:
    review = Review(
        review_id="review-pairs",
        seed_kind=SeedKind.PROMPT,
        seed_value="compare clusters",
        has_protocol=ReviewProtocol(protocol_id="protocol-pairs"),
    )
    cluster_a = Cluster(cluster_number=1, of_review=review)
    cluster_b = Cluster(cluster_number=2, of_review=review)
    pair = ClusterPair(
        of_review=review,
        cluster_a=cluster_a,
        cluster_b=cluster_b,
        semantic_similarity=Decimal("0.80"),
        cross_citation_count=0,
    )
    assert pair.cluster_a is cluster_a
    assert pair.cluster_b is cluster_b
    assert pair.iri.startswith("https://w3id.org/portolan/id/cluster-pair/review-pairs/")
    assert ClusterPair.property_map()["cluster_a"] == {
        "rdf": "ptl:clusterA",
        "lpg": "CLUSTER_PAIR",
    }

    with pytest.raises(ValidationError, match="lexicographically smaller"):
        ClusterPair(
            of_review=review,
            cluster_a=cluster_b,
            cluster_b=cluster_a,
            semantic_similarity=Decimal("0.80"),
            cross_citation_count=0,
        )
    with pytest.raises(ValidationError):
        ClusterPair(
            of_review=review,
            cluster_a=cluster_a,
            cluster_b=cluster_b,
            semantic_similarity=Decimal("1.01"),
            cross_citation_count=0,
        )
    with pytest.raises(ValidationError):
        ClusterPair(
            of_review=review,
            cluster_a=cluster_a,
            cluster_b=cluster_b,
            semantic_similarity=Decimal("0.80"),
            cross_citation_count=-1,
        )


def test_extraction_run_iri_and_seed_value_follow_v02_clarifications() -> None:
    run = ExtractionRun(
        run_id="run/1",
        model="test-model",
        prompt_version="prompt-v2",
        ontology_version="v0.2",
    )
    assert run.iri == "https://w3id.org/portolan/id/extraction/run%2F1"

    review = Review(
        review_id="review-seed",
        seed_kind=SeedKind.PAPER,
        seed_value="10.1000/example",
        has_protocol=ReviewProtocol(protocol_id="protocol-seed"),
    )
    assert review.seed_value == "10.1000/example"
    with pytest.raises(ValidationError):
        Review(
            review_id="review-seed-invalid",
            seed_kind=SeedKind.PAPER,
            seed_value=123,
            has_protocol=ReviewProtocol(protocol_id="protocol-seed-invalid"),
        )
