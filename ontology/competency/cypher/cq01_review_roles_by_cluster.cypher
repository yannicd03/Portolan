// CQ01 — Which works are foundational / bridge / backbone / rising in review R, per cluster?
// PARAMS: $review (string, bare review id)
// Review isolation is carried by Inclusion.reviewId and Cluster.reviewId.
MATCH (inclusion:Inclusion {reviewId: $review})-[:OF_WORK]->(work:Work)
MATCH (inclusion)-[:IN_CLUSTER]->(cluster:Cluster {reviewId: $review})
UNWIND inclusion.role AS role
WITH cluster, role, work
WHERE role IN ["foundational", "bridge", "backbone", "rising"]
RETURN cluster.iri AS cluster,
       cluster.label AS clusterLabel,
       role,
       work.iri AS workIri,
       work.title AS title,
       work.issued AS issued
// Sort keys are returned and ordered by alias: aliasing work.iri AS work would rebind
// `work` to a string, and ORDER BY work.issued would then read a property off a string
// ("expected MAP but was STRING"). Cypher resolves ORDER BY against the RETURN scope.
ORDER BY cluster, role, issued, workIri
// SPIKE NOTE: the review-scoped Inclusion node makes this grouping a direct property-and-edge traversal.
// DEFAULTS: {"review": "kgqa-rag"}
