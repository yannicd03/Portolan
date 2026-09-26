// CQ13 — Why was work W excluded from review R, and at which stage?
// PARAMS: $review (string, bare review id used by the LPG reviewId partition), $work (string, work iri)
MATCH (review:Review {reviewId: $review})
MATCH (inclusion:Inclusion {reviewId: $review})-[:OF_REVIEW]->(review)
MATCH (inclusion)-[:OF_WORK]->(work:Work {iri: $work})
WHERE inclusion.decision = "excluded"
RETURN work.iri AS work,
       work.title AS title,
       review.iri AS review,
       inclusion.stage AS stage,
       inclusion.reason AS reason
ORDER BY stage, work
// SPIKE NOTE: the review-scoped Inclusion stores decision, reason, and stage directly, avoiding a separate exclusion event node.
// DEFAULTS: {"review": "kgqa-rag", "work": "https://w3id.org/portolan/id/work/arxiv/1706.03762"}
