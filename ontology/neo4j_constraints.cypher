// Neo4j 5.x schema for the LPG binding in lpg-binding.md.
//
// Community gap finding: none of the nine SHACL shapes is fully expressible as
// Neo4j Community constraints. Shape 1 (required metadata plus an OR over five
// identifiers/URL), Shape 2 (conditional blog allowlist), Shape 3 (same-work
// evidence cardinality), Shape 4 (required relationship targets), Shape 5
// (relationship cardinalities plus the excluded=>reason condition), Shape 6
// (relationship alternatives plus confidence range), Shape 7 (closed
// enumerations), Shape 8 (required metric direction), and Shape 9 (ClusterPair
// cardinality, ranges, and endpoint ordering) all require application-level
// validation. The property-existence
// fragments below are useful documentation and can be created on Enterprise;
// Community deployments must enforce those same cardinalities at the write
// boundary if the server rejects existence constraints.
//
// Relationship cardinalities, conditional rules, ranges, and enum membership are
// deliberately not approximated with fake properties. The write boundary is the
// authoritative validator for those parts of the contract.
// v0.2 adds metric direction, six named frontier components, the review-scoped
// ADDRESSES_LIMITATION edge, and the review-scoped CLUSTER_PAIR edge. Neo4j
// indexes the partition/filter keys below; relationship target unions, component
// ranges/co-presence, pair ordering/uniqueness, and the supportedBy target union
// remain write-boundary checks.

// Contract IRI minting rule: every Work stores its preferred RDF IRI in `iri`.
CREATE CONSTRAINT work_iri_unique IF NOT EXISTS
FOR (n:Work) REQUIRE n.iri IS UNIQUE;

// Contract IRI minting rule: every Author has one stable RDF IRI.
CREATE CONSTRAINT author_iri_unique IF NOT EXISTS
FOR (n:Author) REQUIRE n.iri IS UNIQUE;

// Contract IRI minting rule: every Organization has one stable RDF IRI.
CREATE CONSTRAINT organization_iri_unique IF NOT EXISTS
FOR (n:Organization) REQUIRE n.iri IS UNIQUE;

// Contract IRI minting rule: every Venue has one stable RDF IRI. A7's
// venueKind-dependent FaBiO journal alignment is an export rule, not a Neo4j
// constraint.
CREATE CONSTRAINT venue_iri_unique IF NOT EXISTS
FOR (n:Venue) REQUIRE n.iri IS UNIQUE;

// Contract IRI minting rule: every canonical concept has one stable RDF IRI.
CREATE CONSTRAINT concept_iri_unique IF NOT EXISTS
FOR (n:Concept) REQUIRE n.iri IS UNIQUE;

// Contract concept IRI minting rule: Problem resources are unique by RDF IRI.
CREATE CONSTRAINT problem_iri_unique IF NOT EXISTS
FOR (n:Problem) REQUIRE n.iri IS UNIQUE;

// Contract concept IRI minting rule: Method resources are unique by RDF IRI.
CREATE CONSTRAINT method_iri_unique IF NOT EXISTS
FOR (n:Method) REQUIRE n.iri IS UNIQUE;

// Contract concept IRI minting rule: Dataset resources are unique by RDF IRI.
CREATE CONSTRAINT dataset_iri_unique IF NOT EXISTS
FOR (n:Dataset) REQUIRE n.iri IS UNIQUE;

// Contract concept IRI minting rule: Metric resources are unique by RDF IRI.
CREATE CONSTRAINT metric_iri_unique IF NOT EXISTS
FOR (n:Metric) REQUIRE n.iri IS UNIQUE;

// A3 / SHACL shape 8: every Metric declares which numeric direction is better.
// The higherIsBetter/lowerIsBetter enumeration remains application-level.
CREATE CONSTRAINT metric_direction_exists IF NOT EXISTS
FOR (n:Metric) REQUIRE n.metricDirection IS NOT NULL;

// Contract IRI minting rule: every Contribution statement has one RDF IRI.
CREATE CONSTRAINT contribution_iri_unique IF NOT EXISTS
FOR (n:Contribution) REQUIRE n.iri IS UNIQUE;

// Contract IRI minting rule: every Result statement has one RDF IRI.
CREATE CONSTRAINT result_iri_unique IF NOT EXISTS
FOR (n:Result) REQUIRE n.iri IS UNIQUE;

// Contract IRI minting rule: every Claim statement has one RDF IRI.
CREATE CONSTRAINT claim_iri_unique IF NOT EXISTS
FOR (n:Claim) REQUIRE n.iri IS UNIQUE;

// Contract IRI minting rule: every Limitation statement has one RDF IRI.
CREATE CONSTRAINT limitation_iri_unique IF NOT EXISTS
FOR (n:Limitation) REQUIRE n.iri IS UNIQUE;

// Contract IRI minting rule: every FutureWork statement has one RDF IRI.
CREATE CONSTRAINT future_work_iri_unique IF NOT EXISTS
FOR (n:FutureWork) REQUIRE n.iri IS UNIQUE;

// Contract IRI minting rule: every Evidence resource has one RDF IRI.
CREATE CONSTRAINT evidence_iri_unique IF NOT EXISTS
FOR (n:Evidence) REQUIRE n.iri IS UNIQUE;

// Contract review IRI minting rule: every Review is unique by RDF IRI.
CREATE CONSTRAINT review_iri_unique IF NOT EXISTS
FOR (n:Review) REQUIRE n.iri IS UNIQUE;

// Contract protocol IRI minting rule: every ReviewProtocol is unique by RDF IRI.
CREATE CONSTRAINT review_protocol_iri_unique IF NOT EXISTS
FOR (n:ReviewProtocol) REQUIRE n.iri IS UNIQUE;

// Contract inclusion IRI minting rule: every Inclusion is unique by RDF IRI.
CREATE CONSTRAINT inclusion_iri_unique IF NOT EXISTS
FOR (n:Inclusion) REQUIRE n.iri IS UNIQUE;

// Contract cluster IRI minting rule: every Cluster is unique by RDF IRI.
CREATE CONSTRAINT cluster_iri_unique IF NOT EXISTS
FOR (n:Cluster) REQUIRE n.iri IS UNIQUE;

// Contract gap IRI minting rule: every GapHypothesis is unique by RDF IRI.
CREATE CONSTRAINT gap_hypothesis_iri_unique IF NOT EXISTS
FOR (n:GapHypothesis) REQUIRE n.iri IS UNIQUE;

// A6 contract provenance rule: an extraction run is a prov:Activity whose stable
// IRI follows ptlr:extraction/{runId}; the prefix pattern is application-level.
CREATE CONSTRAINT activity_iri_unique IF NOT EXISTS
FOR (n:Activity) REQUIRE n.iri IS UNIQUE;

// SHACL shape 1 / Work contract: every Work has a title.
// Enterprise-only property-existence constraint; enforce at the Community write boundary.
CREATE CONSTRAINT work_title_exists IF NOT EXISTS
FOR (n:Work) REQUIRE n.title IS NOT NULL;

// SHACL shape 1 / Work contract: every Work has an issued date or gYear.
// Enterprise-only property-existence constraint; the date-vs-gYear type check is application-level.
CREATE CONSTRAINT work_issued_exists IF NOT EXISTS
FOR (n:Work) REQUIRE n.issued IS NOT NULL;

// SHACL shape 1 / Work contract: every Work has a source tier.
// Enterprise-only property-existence constraint; enum membership remains application-level.
CREATE CONSTRAINT work_source_tier_exists IF NOT EXISTS
FOR (n:Work) REQUIRE n.sourceTier IS NOT NULL;

// Contract cardinality 1 / SHACL shape 3 evidence rule: contributionKind is required.
// Enterprise-only property-existence constraint; evidence and ofWork are relationship checks.
CREATE CONSTRAINT contribution_kind_exists IF NOT EXISTS
FOR (n:Contribution) REQUIRE n.contributionKind IS NOT NULL;

// SHACL shape 4 / Result contract: result value is required.
// Enterprise-only property-existence constraint; method, dataset, metric, and ofWork are relationships.
CREATE CONSTRAINT result_value_exists IF NOT EXISTS
FOR (n:Result) REQUIRE n.value IS NOT NULL;

// Contract cardinality 1 / SHACL shape 3 evidence rule: claimText is required.
// Enterprise-only property-existence constraint; evidence and ofWork are relationship checks.
CREATE CONSTRAINT claim_text_exists IF NOT EXISTS
FOR (n:Claim) REQUIRE n.claimText IS NOT NULL;

// Contract cardinality 1 / SHACL shape 3 evidence rule: limitationText is required.
// Enterprise-only property-existence constraint; evidence and ofWork are relationship checks.
CREATE CONSTRAINT limitation_text_exists IF NOT EXISTS
FOR (n:Limitation) REQUIRE n.limitationText IS NOT NULL;

// Contract cardinality 1 / SHACL shape 3 evidence rule: futureWorkText is required.
// Enterprise-only property-existence constraint; evidence and ofWork are relationship checks.
CREATE CONSTRAINT future_work_text_exists IF NOT EXISTS
FOR (n:FutureWork) REQUIRE n.futureWorkText IS NOT NULL;

// SHACL shape 3 / Evidence contract: quote is required.
// Enterprise-only property-existence constraint; fromWork is a relationship check.
CREATE CONSTRAINT evidence_quote_exists IF NOT EXISTS
FOR (n:Evidence) REQUIRE n.quote IS NOT NULL;

// SHACL shape 3 / Evidence contract: source kind is required.
// Enterprise-only property-existence constraint; enum membership remains application-level.
CREATE CONSTRAINT evidence_source_kind_exists IF NOT EXISTS
FOR (n:Evidence) REQUIRE n.fromSourceKind IS NOT NULL;

// Review contract cardinality 1: seedKind is required.
// Enterprise-only property-existence constraint; enum membership remains application-level.
CREATE CONSTRAINT review_seed_kind_exists IF NOT EXISTS
FOR (n:Review) REQUIRE n.seedKind IS NOT NULL;

// A7 / Review contract cardinality 1: seedValue is required and is always an
// xsd:string at the binding boundary; Neo4j cannot enforce the scalar type.
// Enterprise-only property-existence constraint.
CREATE CONSTRAINT review_seed_value_exists IF NOT EXISTS
FOR (n:Review) REQUIRE n.seedValue IS NOT NULL;

// SHACL shape 5 / Inclusion contract cardinality 1: decision is required.
// Enterprise-only property-existence constraint; exactly-one review/work are relationships.
CREATE CONSTRAINT inclusion_decision_exists IF NOT EXISTS
FOR (n:Inclusion) REQUIRE n.decision IS NOT NULL;

// Inclusion contract cardinality 1: stage is required.
// Enterprise-only property-existence constraint; enum membership remains application-level.
CREATE CONSTRAINT inclusion_stage_exists IF NOT EXISTS
FOR (n:Inclusion) REQUIRE n.stage IS NOT NULL;

// SHACL shape 6 / GapHypothesis contract cardinality 1: gapType is required.
// Enterprise-only property-existence constraint; enum membership remains application-level.
CREATE CONSTRAINT gap_type_exists IF NOT EXISTS
FOR (n:GapHypothesis) REQUIRE n.gapType IS NOT NULL;

// SHACL shape 6 / GapHypothesis contract cardinality 1: statement is required.
// Enterprise-only property-existence constraint.
CREATE CONSTRAINT gap_statement_exists IF NOT EXISTS
FOR (n:GapHypothesis) REQUIRE n.statement IS NOT NULL;

// SHACL shape 6 / GapHypothesis contract cardinality 1: confidence is required.
// Enterprise-only property-existence constraint; the 0..1 range is application-level.
CREATE CONSTRAINT gap_confidence_exists IF NOT EXISTS
FOR (n:GapHypothesis) REQUIRE n.confidence IS NOT NULL;

// SHACL shape 6 / GapHypothesis contract cardinality 1: verificationOutcome is required.
// Enterprise-only property-existence constraint; enum membership remains application-level.
CREATE CONSTRAINT gap_verification_outcome_exists IF NOT EXISTS
FOR (n:GapHypothesis) REQUIRE n.verificationOutcome IS NOT NULL;

// SHACL shape 6 / GapHypothesis contract cardinality 1: userStatus is required.
// Enterprise-only property-existence constraint; enum membership remains application-level.
CREATE CONSTRAINT gap_user_status_exists IF NOT EXISTS
FOR (n:GapHypothesis) REQUIRE n.userStatus IS NOT NULL;

// CQ04: best result grouping orders by result.value within dataset/metric groups.
CREATE INDEX result_value_index IF NOT EXISTS
FOR (n:Result) ON (n.value);

// CQ02, CQ04, CQ09: chronology and newest-work ordering.
CREATE INDEX work_issued_index IF NOT EXISTS
FOR (n:Work) ON (n.issued);

// CQ04 and CQ10: source-tier filtering and grouping.
CREATE INDEX work_source_tier_index IF NOT EXISTS
FOR (n:Work) ON (n.sourceTier);

// CQ01, CQ02, CQ09, CQ10, CQ13, CQ14: review partition and precomputed analysis fields.
CREATE INDEX inclusion_review_main_path_index IF NOT EXISTS
FOR (n:Inclusion) ON (n.reviewId, n.onMainPath);

CREATE INDEX inclusion_review_decision_index IF NOT EXISTS
FOR (n:Inclusion) ON (n.reviewId, n.decision);

CREATE INDEX inclusion_frontier_score_index IF NOT EXISTS
FOR (n:Inclusion) ON (n.frontierScore);

// A2: the six named components are indexed together. Their 0..1 range and the
// conditional invariant that all six accompany a frontierScore are application-level.
CREATE INDEX inclusion_frontier_components_index IF NOT EXISTS
FOR (n:Inclusion) ON (
  n.frontierComponentVelocity,
  n.frontierComponentMainPathLeaf,
  n.frontierComponentClusterGrowth,
  n.frontierComponentConceptNovelty,
  n.frontierComponentSotaClaim,
  n.frontierComponentNotPeerReviewed
);

// A3: metric-direction filtering is used when selecting CQ04 winners.
CREATE INDEX metric_direction_index IF NOT EXISTS
FOR (n:Metric) ON (n.metricDirection);

// A4: addressesLimitation is review-scoped on the relationship itself.
CREATE INDEX addresses_limitation_review_index IF NOT EXISTS
FOR ()-[r:ADDRESSES_LIMITATION]-() ON (r.reviewId);

// A4: analysis relationships carry their review partition explicitly.
CREATE CONSTRAINT addresses_limitation_review_exists IF NOT EXISTS
FOR ()-[r:ADDRESSES_LIMITATION]-() REQUIRE r.reviewId IS NOT NULL;

// A5: ClusterPair is a review-scoped relationship; crossCitationCount is the
// CQ11 disconnection predicate and semanticSimilarity is the result ordering key.
CREATE INDEX cluster_pair_review_index IF NOT EXISTS
FOR ()-[r:CLUSTER_PAIR]-() ON (r.reviewId, r.crossCitationCount, r.semanticSimilarity);

// A5: relationship measures are required; endpoint ordering, non-negative
// counts, and the [0,1] similarity range remain write-boundary checks.
CREATE CONSTRAINT cluster_pair_review_exists IF NOT EXISTS
FOR ()-[r:CLUSTER_PAIR]-() REQUIRE r.reviewId IS NOT NULL;

CREATE CONSTRAINT cluster_pair_similarity_exists IF NOT EXISTS
FOR ()-[r:CLUSTER_PAIR]-() REQUIRE r.semanticSimilarity IS NOT NULL;

CREATE CONSTRAINT cluster_pair_cross_citation_exists IF NOT EXISTS
FOR ()-[r:CLUSTER_PAIR]-() REQUIRE r.crossCitationCount IS NOT NULL;

// CQ09 and CQ11: review-scoped cluster lookup and precomputed growth ordering.
CREATE INDEX cluster_review_growth_index IF NOT EXISTS
FOR (n:Cluster) ON (n.reviewId, n.growthRate);

// CQ14: rejected hypotheses are selected by review and user status.
CREATE INDEX gap_review_status_index IF NOT EXISTS
FOR (n:GapHypothesis) ON (n.reviewId, n.userStatus);

// CQ03 and CQ11: concept identity lookup is already guaranteed by the Concept constraint;
// this secondary index helps specific-label scans in Community deployments.
CREATE INDEX problem_iri_lookup_index IF NOT EXISTS
FOR (n:Problem) ON (n.iri);

CREATE INDEX method_iri_lookup_index IF NOT EXISTS
FOR (n:Method) ON (n.iri);
