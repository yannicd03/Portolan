# ADR-0005 — graph store: RDF/Oxigraph vs Neo4j

- **Status:** OPEN — spike complete (run 2, 2026-09-23); awaiting the user's choice
- **Date opened:** 2026-09-22
- **Decides:** open decision 2 in the design-notes spec (`$PORTOLAN_NOTES_DIR/spec.md`)

The user deliberately left this open on 2026-09-20. This file is the slot the spike's outcome
lands in; it must not be filled by argument.

## What keeps the decision cheap to defer

1. The ontology is conceptual first (`ontology/README.md`); Turtle+SHACL is one binding, the
   LPG catalogue in `ontology/lpg-binding.md` is the other.
2. `GraphRepository` (`backend/portolan/store/repository.py`) is the only way the pipeline and API
   touch a store. Neither ever emits SPARQL or Cypher.
3. Turtle export is mandatory in both backends (spec goal G6), so a Neo4j choice still yields
   faithful RDF.

## The spike

Load the 23-paper golden mini-graph (`eval/golden/`) into both stores and implement all 14
competency questions in SPARQL and Cypher. Compare on:

| Criterion | Evidence to collect |
|---|---|
| Query clarity | Per-CQ line count and a subjective note; CQ05 and CQ11 are the hard cases |
| Validation effort | Which of the 7 SHACL shapes each store can enforce natively |
| Setup friction | Steps from empty machine to loaded, queryable graph |
| Export fidelity | How faithful the Turtle export is from the LPG side |
| Modelling fit | Reified `ptl:Citation` vs edge properties on `:CITES` |
| Isolation | Named graphs vs a `reviewId` partition property |

## Spike run 1 — 2026-09-22

The harness (`eval/spike/run_spike.py`) loads the composed four-layer golden graph into both
stores through `GraphRepository` and runs all 14 questions three times each. Artifacts:
`eval/spike/results.json`, `eval/spike/report.md`.

**Fixture caveat.** Layer 1 is real (19 harvested papers, 51 citation edges). The content and
analysis layers are hand-authored in `eval/golden/analysis_fixture.yaml` because M0 has no
extraction pipeline. Evidence quotes are verified verbatim against the real abstracts and all
centrality numbers are computed from the real citation graph, but the concepts, contributions
and gaps are curated. This bounds what the spike can claim.

### Measured

| | Oxigraph | Neo4j 2026.09.0 Community |
|---|---:|---:|
| Load of the full graph | 0.23 s | 2.72 s |
| Questions that execute | 14/14 | 14/14 |
| Turtle export | 1879 triples, 78 ms | 1092 triples, 142 ms |
| Median query text | ~34 lines SPARQL | ~13 lines Cypher |
| Query latency | 0.1–1.8 ms | 1.4–8.3 ms (CQ02 490 ms) |

**Cypher is consistently about half the length of the equivalent SPARQL** — CQ08 is 8 lines
against 61, CQ05 14 against 55. Path and neighbourhood questions are where the gap is widest,
which is exactly what the frontend will ask.

**Neo4j Community enforces none of the 9 SHACL shapes natively.** Of 59 schema statements, 35
were accepted (uniqueness constraints and indexes) and 24 rejected as Enterprise-only — every
existence constraint. All nine shapes therefore depend on write-boundary validation in
Pydantic plus pyshacl over the Turtle export. The RDF side runs the same shapes directly, in
process, with no server.

**Turtle export is lossy from the property graph**: 1092 triples against 1879. Named-graph
boundaries cannot survive (`reviewId` / `extractionRun` degrade from graph names to
properties), and Bolt has no decimal type, so every `xsd:decimal` is narrowed to a float on the
way in. TriG would carry more than Turtle does.

### Bugs this spike found (all fixed)

Worth recording, because they are the argument for writing the queries twice:

1. **CQ01 and CQ02 were invalid Cypher** against real data: both aliased a node variable to its
   own IRI (`work.iri AS work`) and then ordered by a property of it, which Neo4j rejects once
   the rebound value is a string. Neither was caught by the offline tests.
2. `Neo4jStore.export_turtle()` returned **zero triples** against a live server. Two helpers
   mis-handled real driver objects: a `Record` satisfies `isinstance(x, Mapping)` but iterates
   its *values*, and a `Node` is a mapping over its *properties* with labels on an attribute.
   Both had passed tests built on dict-shaped fakes.
3. The Neo4j write path could not store `Decimal` at all — Bolt rejects it outright.
4. `ClusterPair` (amendment A5) had a model but **no repository method and no backend**, so
   CQ11 had no data path in either store.
5. The harness fed the Cypher `// DEFAULTS:` to both backends, but a review is addressed by
   full IRI in SPARQL and by bare id in Cypher, so every review-scoped SPARQL query returned
   zero rows while reporting success.

## Spike run 2 — 2026-09-23 (parity)

Run 1 left the bindings disagreeing on 11 of 14 questions. Run 2 fixed every disagreement at its
root and pinned the answers: `eval/golden/expected_answers.json` holds the expected rows for
each CQ, derived from the fixture (not copied from either store), with a per-CQ justification
in `eval/spike/parity/triage.md`. `run_spike.py --parity` checks each backend against it and
also fails when normalization collapses rows (hidden fan-out on an uncompared column).

Verified live on a freshly started Neo4j 2026.09.0 Community and in-memory Oxigraph:

| | Oxigraph | Neo4j 2026.09.0 Community |
|---|---:|---:|
| CQs matching the expected answers | 14/14 | 14/14 |
| CQs returning ≥ 1 row | 14/14 | 14/14 |
| Load of the full graph | 0.20 s | 4.4 s |
| Turtle export | 1886 triples | 1886 triples, identical set after the two inherent losses |
| SHACL on the golden graph | conforms, 0 violations | conforms, 0 violations (pyshacl over the export) |
| Schema enforcement | no write-time enforcement; pyshacl runs all 9 shapes over a Turtle export | 35 of 59 native statements accepted (uniqueness, indexes); every existence constraint is Enterprise-only; shapes run via pyshacl over an export |
| Median query text | ~35 lines SPARQL | ~15 lines Cypher |
| Query latency (best of 3) | 0.2–73 ms | 0.9–34 ms |

Tests: 98 pass with `NEO4J_URI` set, 92 pass + 6 skipped without.

**Run 1's export-fidelity finding was mostly a bug, not a property of Neo4j.** The 1092-triple
export was not "lossy LPG": a real `neo4j.graph.Relationship` is a `Mapping` over its
properties, so the exporter read every edge's type as `RELATES_TO` with no endpoints and dropped
all of them — the same trap run 1 found for `Node`, missed again because the tests used
dict-shaped fakes (they now use genuine driver objects). With that fixed, the only remaining
losses are the two documented in `neo4j_store.EXPORT_INHERENT_LOSSES`: named-graph boundaries
(`reviewId` / `extractionRun` become properties) and `xsd:decimal` narrowed to float by Bolt.

Other defects run 2 found and fixed: the composer dropped `supports_claim`, `contradicts_claim`
and `limitation_of` from the fixture (CQ06/CQ07 were empty on both sides, so they only seemed to agree); four
relationship types queried by Cypher were never written; the harness still asked the two
backends about different parameters; the Oxigraph export typed enum values as `xsd:string`
while the shapes' `sh:in` lists are plain literals (all 220 Oxigraph violations); CQ02's Cypher
kept only edges straddling the seed's date (3 rows instead of 17); CQ08 had a pattern that only
a live parser rejects (now guarded by a static test); CQ03/CQ04's SPARQL fanned out once per
`skos:altLabel`.

## Decision

*Not yet taken.* The blocker recorded after run 1 — the bindings disagreeing — is resolved, so
the evidence above now measures the stores rather than the queries. The choice between them is
the user's.

## What run 2 changes about the trade-off

- Correctness no longer separates the stores: identical answers, identical RDF, both conform.
- Validation: neither store enforces the shapes at write time; both rely on Pydantic at the
  write boundary, and both currently run pyshacl over a Turtle export (about 1.5 s each on the
  golden graph). The difference is headroom: the RDF side could validate its store's native
  graph without exporting, and on Neo4j the export is the only route.
- Export: Neo4j's Turtle is faithful except named graphs and decimals; TriG-level partition
  fidelity would still need the Oxigraph side or an extra mapping.
- Query clarity: Cypher remains roughly half the length of SPARQL, widest on path and
  neighbourhood questions (CQ08: 23 vs 61 lines, CQ05: 18 vs 55).
- Operations: Oxigraph is an in-process library; Neo4j is a server (Docker), with GDS available
  in Community for later centrality work.
