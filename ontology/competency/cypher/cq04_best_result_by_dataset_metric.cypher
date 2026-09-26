// CQ04 — What is the best reported result per dataset and metric, by which method, from which source tier?
// PARAMS: $review (string, bare review id)
// CONTRACT: Metric.metricDirection selects max for higherIsBetter and min for
// lowerIsBetter; tied best results are returned.
MATCH (inclusion:Inclusion {reviewId: $review, decision: "included"})-[:OF_WORK]->(work:Work)
MATCH (result:Result)-[:OF_WORK]->(work)
MATCH (result)-[:ON_DATASET]->(dataset:Dataset)
MATCH (result)-[:WITH_METRIC]->(metric:Metric)
MATCH (result)-[:OF_METHOD]->(method:Method)
WHERE metric.metricDirection IN ["higherIsBetter", "lowerIsBetter"]
WITH dataset,
     metric,
     metric.metricDirection AS metricDirection,
     CASE
       WHEN metric.metricDirection = "higherIsBetter" THEN max(result.value)
       ELSE min(result.value)
     END AS bestValue
MATCH (best:Result)-[:ON_DATASET]->(dataset)
MATCH (best)-[:WITH_METRIC]->(metric)
MATCH (best)-[:OF_METHOD]->(bestMethod:Method)
MATCH (best)-[:OF_WORK]->(bestWork:Work)
MATCH (bestInclusion:Inclusion {reviewId: $review, decision: "included"})-[:OF_WORK]->(bestWork)
WHERE best.value = bestValue
RETURN dataset.iri AS dataset,
       metric.iri AS metric,
       metricDirection,
       best.value AS value,
       bestMethod.iri AS method,
       bestWork.iri AS work,
       bestWork.sourceTier AS sourceTier
ORDER BY dataset,
         metric,
         CASE WHEN metricDirection = "higherIsBetter" THEN 0 - value ELSE value END,
         method,
         work
// SPIKE NOTE: direction-aware aggregate followed by a re-match returns all tied best results.
// DEFAULTS: {"review": "kgqa-rag"}
