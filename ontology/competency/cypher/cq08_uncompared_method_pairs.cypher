// CQ08 — Which methods were never compared on a shared dataset?
// PARAMS: $review (string, bare review id used by the LPG reviewId partition)
MATCH (review:Review {reviewId: $review})
MATCH (inclusionA:Inclusion {reviewId: $review, decision: "included"})
      -[:OF_REVIEW]->(review)
MATCH (inclusionA)-[:OF_WORK]->(workA:Work)<-[:OF_WORK]
      -(resultA:Result)-[:OF_METHOD]->(methodA:Method)
MATCH (inclusionB:Inclusion {reviewId: $review, decision: "included"})
      -[:OF_REVIEW]->(review)
MATCH (inclusionB)-[:OF_WORK]->(workB:Work)<-[:OF_WORK]
      -(resultB:Result)-[:OF_METHOD]->(methodB:Method)
WHERE methodA.iri < methodB.iri
  AND NOT EXISTS {
    MATCH (leftInclusion:Inclusion {reviewId: $review, decision: "included"})
          -[:OF_WORK]->(leftWork:Work)<-[:OF_WORK]-
          (leftResult:Result)-[:OF_METHOD]->(methodA)
    MATCH (leftResult)-[:ON_DATASET]->(sharedDataset:Dataset)
    MATCH (rightInclusion:Inclusion {reviewId: $review, decision: "included"})
          -[:OF_WORK]->(rightWork:Work)<-[:OF_WORK]-
          (rightResult:Result)-[:OF_METHOD]->(methodB)
    MATCH (rightResult)-[:ON_DATASET]->(sharedDataset)
  }
RETURN DISTINCT methodA.iri AS methodA,
                methodB.iri AS methodB
ORDER BY methodA, methodB
// SPIKE NOTE: the candidate methods are derived from included review works; the
// NOT EXISTS subquery then tests only included results on a shared dataset.
// DEFAULTS: {"review": "kgqa-rag"}
