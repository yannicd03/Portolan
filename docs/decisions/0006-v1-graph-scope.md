# ADR-0006 — v1 graph scope: works, citations, authors, merged keyword concepts

- **Status:** accepted
- **Date:** 2026-09-26
- **Decides:** how much of the v0.2 ontology (`ontology/README.md`) the product needs now
- **Related:** ADR-0005 (Neo4j)

## Context

The v0.2 ontology was designed for landscape, frontier and gap analytics: four layers
(bibliographic, extracted content with verified evidence, review/analysis, provenance) checked
by 14 competency questions. The product vision has since moved to projects with a chat agent
(Research mode builds the graph, Ask mode answers from it with a cited passage for every claim)
and a graph view of how papers relate. For that, the user needs far less of the ontology.

## Decision

The v1 graph holds only:

| Element | Neo4j shape | Notes |
|---|---|---|
| Paper | `(:Work)` | title, year, identifiers (DOI, arXiv, OpenAlex, S2), source tier, abstract |
| Citation | `(:Work)-[:CITES]->(:Work)` | one edge per referencing pair |
| Author | `(:Author)`, `(:Work)-[:AUTHORED_BY {position}]->(:Author)` | identity via ORCID / OpenAlex / S2 ids |
| Concept | `(:Concept)`, `(:Work)-[:HAS_CONCEPT]->(:Concept)` | built from keywords, see below |

**Concepts start from keywords.** Each work's keywords (author keywords and the source APIs'
keyword or topic fields) become concept candidates. Similar keywords are merged into one
`Concept` node that keeps the variants as aliases, so a concept links every paper that uses any
of its variants. The merge method (string normalisation first, then embedding similarity with a
threshold, possibly an LLM check near the threshold) is an implementation detail for M1.

Everything else in v0.2 — statements, claims and results, evidence nodes, reviews, inclusions,
clusters, frontier scores, gap hypotheses, venues and organisations — is **out of the v1 graph**.

## Consequences

- The competency questions, the SHACL shapes and the RDF binding describe the M0 spike, not
  the v1 schema. They stay in the repo, with the parity tests, until the v1 schema replaces
  them. The competency-question endpoints were removed from the app on 2026-09-26.
- Ask mode still has to prove every claim with a passage from the paper. Passages and their
  locations in the downloaded PDFs are therefore still needed, but as a document and passage
  store keyed by work and PDF hash, not as ontology statements. Its design is part of the
  Ask-mode milestone.
- The golden mini-graph remains the test fixture until a fixture with keyword concepts exists.
- "Broader concepts" may later need a hierarchy between concepts (a `BROADER` edge). v1 starts
  flat and adds it only if the graph view or Ask mode needs it.
