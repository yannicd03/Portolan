// CQ11 — Which cluster pairs are semantically close but citation-disconnected?
// PARAMS: $review (string, bare review id used by the LPG reviewId partition), $similarityThreshold (float, minimum semantic similarity)
// CONTRACT: v0.2 stores one review-scoped CLUSTER_PAIR relationship per
// unordered pair. Its scalar measures are authoritative; zero cross citations
// is the disconnection predicate.
MATCH (clusterA:Cluster {reviewId: $review})
      -[pair:CLUSTER_PAIR {reviewId: $review}]->
      (clusterB:Cluster {reviewId: $review})
WHERE clusterA.iri < clusterB.iri
  AND pair.semanticSimilarity >= $similarityThreshold
  AND pair.crossCitationCount = 0
RETURN clusterA.iri AS clusterA,
       clusterA.label AS clusterALabel,
       clusterB.iri AS clusterB,
       clusterB.label AS clusterBLabel,
       pair.semanticSimilarity AS semanticSimilarity,
       pair.crossCitationCount AS crossCitationCount
ORDER BY semanticSimilarity DESC, clusterA, clusterB
// SPIKE NOTE: the pair relationship removes the prior concept-aggregation and
// cross-cluster citation traversal while preserving deterministic pair order.
// DEFAULTS: {"review": "kgqa-rag", "similarityThreshold": 0.75}
