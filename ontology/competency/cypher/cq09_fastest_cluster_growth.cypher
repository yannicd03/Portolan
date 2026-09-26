// CQ09 — Which clusters grew fastest in the last 18 months, and which concepts first appeared in them?
// PARAMS: $review (string, bare review id used by the LPG reviewId partition)
// PRECOMPUTED: Cluster.growthRate is the contract's review-scoped growth metric for the last-18-month window.
MATCH (cluster:Cluster {reviewId: $review})-[:OF_REVIEW]->(review:Review {reviewId: $review})
WHERE cluster.growthRate IS NOT NULL
MATCH (cluster)-[:TOP_CONCEPT]->(concept:Concept)
      -[:FIRST_SEEN_IN]->(firstWork:Work)
RETURN cluster.iri AS cluster,
       cluster.label AS clusterLabel,
       cluster.growthRate AS growthRate,
       concept.iri AS concept,
       firstWork.iri AS firstSeenWork,
       firstWork.issued AS firstSeenDate
ORDER BY growthRate DESC, cluster
// SPIKE NOTE: TOP_CONCEPT and FIRST_SEEN_IN are the LPG equivalents of the
// RDF links; this returns one row per cluster/concept pair.
// DEFAULTS: {"review": "kgqa-rag"}
