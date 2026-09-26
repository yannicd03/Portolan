// CQ10 — What share of frontier works is not peer-reviewed, and which organizations publish them?
// PARAMS: $review (string, bare review id used by the LPG reviewId partition)
// PRECOMPUTED: Inclusion.frontierScore identifies review-scoped frontier work;
// v0.2 requires all six named components alongside a positive composite score.
MATCH (review:Review {reviewId: $review})
MATCH (inclusion:Inclusion {reviewId: $review, decision: "included"})
      -[:OF_REVIEW]->(review)
MATCH (inclusion)-[:OF_WORK]->(work:Work)
WHERE inclusion.frontierScore > 0
  AND inclusion.frontierComponentVelocity IS NOT NULL
  AND inclusion.frontierComponentMainPathLeaf IS NOT NULL
  AND inclusion.frontierComponentClusterGrowth IS NOT NULL
  AND inclusion.frontierComponentConceptNovelty IS NOT NULL
  AND inclusion.frontierComponentSotaClaim IS NOT NULL
  AND inclusion.frontierComponentNotPeerReviewed IS NOT NULL
WITH review, collect(DISTINCT work) AS frontierWorks
WITH review, frontierWorks,
     size(frontierWorks) AS totalFrontier,
     size([frontier IN frontierWorks WHERE frontier.sourceTier <> "peerReviewed"]) AS nonPeerReviewed,
     [frontier IN frontierWorks WHERE frontier.sourceTier <> "peerReviewed"] AS nonPeerWorks
WITH review, totalFrontier, nonPeerReviewed, nonPeerWorks,
     CASE
       WHEN totalFrontier = 0 THEN 0.0
       ELSE toFloat(nonPeerReviewed) / totalFrontier
     END AS nonPeerReviewedShare
UNWIND CASE WHEN size(nonPeerWorks) = 0 THEN [null] ELSE nonPeerWorks END AS nonPeerWork
OPTIONAL MATCH (nonPeerWork)-[:PUBLISHED_BY]->(organization:Organization)
WITH review, nonPeerReviewedShare, collect(DISTINCT organization.iri) AS organizationIris
UNWIND CASE WHEN size(organizationIris) = 0 THEN [null] ELSE organizationIris END AS organizationIri
RETURN DISTINCT review.iri AS review,
                nonPeerReviewedShare,
                organizationIri AS organization
ORDER BY nonPeerReviewedShare DESC, organization
// SPIKE NOTE: the frontier is deduplicated before calculating the share; an
// absent publisher does not suppress the share row or invent an organization.
// DEFAULTS: {"review": "kgqa-rag"}
