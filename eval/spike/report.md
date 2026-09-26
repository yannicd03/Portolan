# M0 graph-store spike

Generated: `2026-09-26T16:50:04Z`

This report records measurements from the requested stores. It does not declare a winner; the ADR decision remains a human decision from this evidence.

## Run summary

| Backend | Status | Load | CQs executed successfully | Turtle triples | Export |
|---|---|---:|---:|---:|---:|
| oxigraph | completed | 201.636 ms | 14/14 | 1886 | 91.995 ms |
| neo4j | completed | 4252.593 ms | 14/14 | 1886 | 205.897 ms |

## Competency-question measurements

Each query was submitted three times through `GraphRepository.run_competency_question`; the reported time is the best successful run. An error is retained even when another attempt succeeds.

| CQ | Oxigraph SPARQL lines | Oxigraph rows / best | Neo4j Cypher lines | Neo4j rows / best |
|---|---:|---:|---:|---:|
| CQ01 | 24 | 29 / 0.488 ms | 12 | 29 / 2.722 ms |
| CQ02 | 33 | 17 / 0.839 ms | 21 | 17 / 3.409 ms |
| CQ03 | 36 | 20 / 0.662 ms | 12 | 20 / 2.683 ms |
| CQ04 | 60 | 5 / 6.369 ms | 31 | 5 / 6.112 ms |
| CQ05 | 55 | 8 / 36.567 ms | 18 | 8 / 16.638 ms |
| CQ06 | 56 | 1 / 0.717 ms | 25 | 1 / 2.054 ms |
| CQ07 | 36 | 1 / 1.866 ms | 14 | 1 / 1.315 ms |
| CQ08 | 61 | 19 / 77.097 ms | 23 | 19 / 28.477 ms |
| CQ09 | 23 | 4 / 0.224 ms | 11 | 4 / 1.247 ms |
| CQ10 | 52 | 1 / 1.270 ms | 29 | 1 / 1.657 ms |
| CQ11 | 32 | 2 / 0.328 ms | 13 | 2 / 1.848 ms |
| CQ12 | 28 | 1 / 0.229 ms | 16 | 1 / 1.338 ms |
| CQ13 | 21 | 1 / 0.200 ms | 10 | 1 / 1.714 ms |
| CQ14 | 16 | 1 / 0.174 ms | 11 | 1 / 1.335 ms |

No competency-question execution errors were recorded.

## Neo4j native constraint evidence

The checked-in schema contained 59 statements; 35 were accepted and 24 rejected. The current ontology file declares 9 shapes (Shape 9 is the v0.2 ClusterPair amendment).

| Shape | Fully enforced natively | Accepted fragments | Rejected fragments |
|---:|---|---|---|
| 1 | False |  | work_title_exists, work_issued_exists, work_source_tier_exists |
| 2 | False |  |  |
| 3 | False |  | contribution_kind_exists, claim_text_exists, limitation_text_exists, future_work_text_exists, evidence_quote_exists, evidence_source_kind_exists |
| 4 | False |  | result_value_exists |
| 5 | False |  | inclusion_decision_exists, inclusion_stage_exists |
| 6 | False | gap_hypothesis_iri_unique, gap_review_status_index | gap_type_exists, gap_statement_exists, gap_confidence_exists, gap_verification_outcome_exists, gap_user_status_exists |
| 7 | False |  |  |
| 8 | False |  | metric_direction_exists |
| 9 | False | cluster_pair_review_index | cluster_pair_review_exists, cluster_pair_similarity_exists, cluster_pair_cross_citation_exists |

Enterprise/Community rejection candidates: `metric_direction_exists`, `work_title_exists`, `work_issued_exists`, `work_source_tier_exists`, `contribution_kind_exists`, `result_value_exists`, `claim_text_exists`, `limitation_text_exists`, `future_work_text_exists`, `evidence_quote_exists`, `evidence_source_kind_exists`, `review_seed_kind_exists`, `review_seed_value_exists`, `inclusion_decision_exists`, `inclusion_stage_exists`, `gap_type_exists`, `gap_statement_exists`, `gap_confidence_exists`, `gap_verification_outcome_exists`, `gap_user_status_exists`, `addresses_limitation_review_exists`, `cluster_pair_review_exists`, `cluster_pair_similarity_exists`, `cluster_pair_cross_citation_exists`.

## ADR criteria evidence

| Criterion | Evidence / observed difference |
|---|---|
| Query clarity | Per-CQ line counts and measured rows/times are above. The paired artifacts keep the same CQ numbers and slugs; CQ05 and CQ11 expose the main absence/pair-measure differences. |
| Validation effort | Oxigraph: conforms=True; violations=0; warnings=0. Neo4j: conforms=True; violations=0; warnings=0; the Neo4j path validates the Turtle export outside the server. |
| Setup friction | Oxigraph setup is in-memory. Neo4j setup includes the live connection and the schema acceptance results above; load times are measured in the run summary. GraphRepository has no reset operation, so a persisted Neo4j run assumes the target database is empty or isolated before loading. |
| Export fidelity | Oxigraph exported 1886 Turtle triples. Neo4j exported 1886; the machine-readable comparison records missing/extra triples when both parses succeeded. Neo4j plain Turtle cannot retain named-graph boundaries and its exporter omits reviewId/extractionRun partition properties. |
| Modelling fit | The RDF binding retains a reified ptl:Citation plus cito:cites. Neo4j stores citation facts on a CITES relationship and reconstructs the reified RDF form during export; the concrete triple-set comparison is the measured check. |
| Isolation | Oxigraph uses named graphs for bibliography, extraction runs, and reviews. Neo4j uses reviewId/extractionRun node or relationship properties; those partition properties are intentionally omitted by its plain Turtle exporter. |

## Repository-boundary issues to carry into the ADR

- `GraphRepository` exposes `write_cluster_pair`; the v0.2 binding still records the pair differently in RDF and LPG, so the two competency queries must read their binding-specific shapes.
- `GraphRepository` has no reset/clear method. The harness creates a new Oxigraph instance, but it cannot clear a persisted Neo4j database without leaving the repository boundary; repeat Neo4j runs therefore require an externally prepared empty database.
- Neo4j schema application is measured through the store's private execution hook only because the repository contract has no schema-management operation. Data writes, CQs, validation, and export remain repository calls.

The harness intentionally leaves the ADR decision open.
