// CQ05 — Which problem × method pairs have no contribution while both the row and the column have at least n?
// PARAMS: $review (string, bare review id), $n (integer, minimum distinct-work support for each row and column)
// DENSITY: a problem row or method column is dense when it has at least n distinct included works with contributions.
MATCH (rowInclusion:Inclusion {reviewId: $review, decision: "included"})-[:OF_WORK]->(rowWork:Work)
MATCH (problem:Problem)<-[:ADDRESSES]-(rowContribution:Contribution)-[:OF_WORK]->(rowWork)
WITH problem, count(DISTINCT rowWork) AS rowSupport
WHERE rowSupport >= $n
MATCH (columnInclusion:Inclusion {reviewId: $review, decision: "included"})-[:OF_WORK]->(columnWork:Work)
MATCH (method:Method)<-[:PROPOSES|USES]-(columnContribution:Contribution)-[:OF_WORK]->(columnWork)
WITH problem, rowSupport, method, count(DISTINCT columnWork) AS columnSupport
WHERE columnSupport >= $n
  AND NOT EXISTS {
    MATCH (cellInclusion:Inclusion {reviewId: $review, decision: "included"})-[:OF_WORK]->(cellWork:Work)
    MATCH (problem)<-[:ADDRESSES]-(existing:Contribution)-[:OF_WORK]->(cellWork)
    MATCH (existing)-[:PROPOSES|USES]->(method)
  }
RETURN problem.iri AS problem,
       method.iri AS method,
       rowSupport,
       columnSupport
ORDER BY rowSupport DESC, columnSupport DESC, problem, method
// SPIKE NOTE: Cypher expresses the dense-row/dense-column aggregation and the empty-cell negation in one correlated query.
// DEFAULTS: {"review": "kgqa-rag", "n": 3}
