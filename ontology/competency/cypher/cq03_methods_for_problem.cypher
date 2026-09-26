// CQ03 — Which methods address problem P, and on which datasets and metrics were they evaluated?
// PARAMS: $problem (string, problem concept iri)
MATCH (problem:Problem {iri: $problem})<-[:ADDRESSES]-(contribution:Contribution)
MATCH (contribution)-[:OF_WORK]->(work:Work)
MATCH (contribution)-[:PROPOSES|USES]->(method:Method)
OPTIONAL MATCH (contribution)-[:REPORTS]->(result:Result)-[:OF_METHOD]->(method)
OPTIONAL MATCH (result)-[:ON_DATASET]->(dataset:Dataset)
OPTIONAL MATCH (result)-[:WITH_METRIC]->(metric:Metric)
RETURN DISTINCT method.iri AS method,
       work.iri AS work,
       work.title AS title,
       dataset.iri AS dataset,
       metric.iri AS metric
ORDER BY method, work, dataset, metric
// SPIKE NOTE: OPTIONAL MATCH preserves a method even when its contribution has no reported result, which is useful for sparse evidence.
// DEFAULTS: {"problem": "https://w3id.org/portolan/id/concept/problem/knowledge-graph-question-answering"}
