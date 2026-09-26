// CQ06 — Which limitations are stated by ≥3 works and addressed by no later work in the corpus?
// PARAMS: $review (string, bare review id), $n (integer, minimum number of works)
// CONTRACT: addressesLimitation is an analysis-stage relationship scoped by
// reviewId; recurring limitations are grouped by their ptl:about concept.
MATCH (statingInclusion:Inclusion {reviewId: $review, decision: "included"})
      -[:OF_WORK]->(statingWork:Work)
MATCH (limitation:Limitation)-[:OF_WORK]->(statingWork)
MATCH (limitation)-[:ABOUT]->(about:Concept)
WITH about,
     collect(DISTINCT limitation.limitationText) AS limitationTexts,
     collect(DISTINCT statingWork) AS statingWorks,
     min(statingWork.issued) AS oldestStatementDate,
     max(statingWork.issued) AS latestStatementDate
WHERE size(statingWorks) >= $n
  AND NOT EXISTS {
    MATCH (laterInclusion:Inclusion {reviewId: $review, decision: "included"})
          -[:OF_WORK]->(laterWork:Work)
    MATCH (laterContribution:Contribution)-[address:ADDRESSES_LIMITATION]->
          (addressedLimitation:Limitation)
    MATCH (laterContribution)-[:OF_WORK]->(laterWork)
    MATCH (addressedLimitation)-[:ABOUT]->(about)
    WHERE address.reviewId = $review
      AND laterWork.issued > latestStatementDate
  }
RETURN about.iri AS about,
       limitationTexts,
       size(statingWorks) AS statingWorkCount,
       oldestStatementDate
ORDER BY statingWorkCount DESC, about
// SPIKE NOTE: the correlated NOT EXISTS now negates the explicit review-scoped
// limitation-addressing relationship rather than proxying through target edges.
// DEFAULTS: {"review": "kgqa-rag", "n": 3}
