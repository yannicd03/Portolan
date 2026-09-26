// CQ07 — Which claims are contradicted, and by whom?
// PARAMS: $review (string, bare review id)
MATCH (claimInclusion:Inclusion {reviewId: $review, decision: "included"})-[:OF_WORK]->(contradictedWork:Work)
MATCH (counterInclusion:Inclusion {reviewId: $review, decision: "included"})-[:OF_WORK]->(contradictingWork:Work)
MATCH (contradicting:Claim)-[:CONTRADICTS_CLAIM]->(contradicted:Claim)
MATCH (contradicting)-[:OF_WORK]->(contradictingWork)
MATCH (contradicted)-[:OF_WORK]->(contradictedWork)
RETURN contradicting.iri AS contradictingClaim,
       contradicting.claimText AS contradictingText,
       contradictingWork.iri AS contradictingWork,
       contradictingWork.title AS contradictingWorkTitle,
       contradicted.iri AS contradictedClaim,
       contradicted.claimText AS contradictedText,
       contradictedWork.iri AS contradictedWork,
       contradictedWork.title AS contradictedWorkTitle
ORDER BY contradictedWork, contradictingWork, contradictedClaim
// SPIKE NOTE: a single directed relationship makes the claim-to-claim contradiction chain easy to inspect in Cypher.
// DEFAULTS: {"review": "kgqa-rag"}
