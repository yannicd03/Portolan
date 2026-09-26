// CQ02 — What is the main path from the seed's ancestors to the newest works?
// PARAMS: $review (string, bare review id), $seed (string, seed work iri)
// PRECOMPUTED: Inclusion.onMainPath identifies review-scoped main-path works.
MATCH (seed:Work {iri: $seed})
MATCH (fromInclusion:Inclusion {reviewId: $review, onMainPath: true})-[:OF_WORK]->(ancestor:Work)
MATCH (toInclusion:Inclusion {reviewId: $review, onMainPath: true})-[:OF_WORK]->(newest:Work)
MATCH (ancestor)<-[:CITES]-(newest)
WHERE (
    ancestor = seed
    OR newest = seed
    OR EXISTS {
      MATCH (seed)-[:CITES*1..]->(ancestor)
    }
    OR EXISTS {
      MATCH (newest)-[:CITES*1..]->(seed)
    }
  )
RETURN ancestor.iri AS ancestorIri,
       ancestor.title AS ancestorTitle,
       ancestor.issued AS ancestorIssued,
       newest.iri AS newestIri,
       newest.title AS newestTitle,
       newest.issued AS newestIssued
ORDER BY ancestorIssued ASC, newestIssued ASC, ancestorIri, newestIri
// SPIKE NOTE: the precomputed endpoint flag avoids recomputing main-path centrality; a direct CITES edge mirrors the SPARQL binding.
// DEFAULTS: {"review": "kgqa-rag", "seed": "https://w3id.org/portolan/id/work/arxiv/1706.03762"}
