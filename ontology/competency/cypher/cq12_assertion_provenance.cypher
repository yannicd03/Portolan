// CQ12 — For any assertion shown in the UI: which quote, from which work, extracted by which model and prompt version, supports it?
// PARAMS: $assertion (string, assertion iri)
MATCH (assertion {iri: $assertion})-[:HAS_EVIDENCE]->(evidence:Evidence)
MATCH (assertion)-[:OF_WORK]->(assertionWork:Work)
MATCH (evidence)-[:FROM_WORK]->(sourceWork:Work)
MATCH (evidence)-[:WAS_GENERATED_BY]->(run:Activity)
WHERE sourceWork = assertionWork
RETURN assertion.iri AS assertion,
       evidence.iri AS evidence,
       evidence.quote AS quote,
       sourceWork.iri AS work,
       sourceWork.title AS workTitle,
       evidence.fromSourceKind AS fromSourceKind,
       run.iri AS activity,
       run.model AS model,
       run.promptVersion AS promptVersion,
       run.endedAtTime AS endedAt
ORDER BY evidence, endedAt
// SPIKE NOTE: explicit evidence and provenance hops are easy to follow; the LPG binding requires the provenance join to be wired by hand.
// DEFAULTS: {"assertion": "https://w3id.org/portolan/id/contribution/arxiv-1706.03762/1"}
