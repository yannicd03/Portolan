// CQ14 — Which gap hypotheses did the user reject, so a re-run does not resurface them?
// PARAMS: $review (string, bare review id used by the LPG reviewId partition)
MATCH (review:Review {reviewId: $review})
MATCH (gap:GapHypothesis {reviewId: $review})-[:OF_REVIEW]->(review)
WHERE gap.userStatus = "rejected"
OPTIONAL MATCH (gap)-[:ABOUT]->(about)
RETURN gap.iri AS gap,
       gap.statement AS statement,
       about.iri AS about,
       gap.confidence AS confidence,
       gap.verificationOutcome AS verificationOutcome,
       gap.userNote AS userNote
ORDER BY gap
// SPIKE NOTE: userStatus is a review-partitioned scalar, so rerun suppression is a simple indexed predicate in Cypher.
// DEFAULTS: {"review": "kgqa-rag"}
