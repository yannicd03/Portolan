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
| Project | `(:Project)`, `(:Project)-[:INCLUDES {discovered_via, depth}]->(:Work)` | works, authors and concepts are shared; a project is the set of works it includes |
| Paper | `(:Work)` | title, year, identifiers (DOI, arXiv, OpenAlex, S2), source tier, **abstract** (a property with a full-text index over title and abstract, not a separate node), `document_sha256` of the local PDF |
| Citation | `(:Work)-[:CITES]->(:Work)` | one edge per referencing pair |
| Author | `(:Author)`, `(:Work)-[:AUTHORED_BY {position}]->(:Author)` | identity via ORCID / OpenAlex / S2 ids |
| Concept | `(:Concept)`, `(:Work)-[:HAS_CONCEPT]->(:Concept)` | built from keywords, see below |

**Concepts start from keywords.** Each work's keywords (author keywords and the source APIs'
keyword or topic fields) become concept candidates. Similar keywords are merged into one
`Concept` node that keeps the variants as aliases, so a concept links every paper that uses any
of its variants. The merge method (string normalisation first, then embedding similarity with a
threshold, possibly an LLM check near the threshold) is an implementation detail for M1.

Everything else in v0.2 — statements, claims and results, evidence nodes, reviews and their
inclusion records (a project's `INCLUDES` edge replaces them), clusters, frontier scores, gap
hypotheses, venues and organisations — is **out of the v1 graph**.

## Consequences

- The competency questions, the SHACL shapes and the RDF binding describe the M0 spike, not
  the v1 schema. They stay in the repo, with the parity tests, until the v1 schema replaces
  them. The competency-question endpoints were removed from the app on 2026-09-26.
- **The graph is the agents' map, not their source of facts** (amended 2026-09-26, user
  decision). Ask mode still proves every claim with a passage from the paper, but the agent
  finds that passage by reading the downloaded paper with ordinary file operations (read,
  grep, open at a page), not by retrieving passages stored in the graph or in a vector
  index. The graph tells the agent where to look: full-text search over titles and abstracts,
  citation neighbourhoods, and works that share an author or a concept. So:
  - every acquired PDF is stored content-addressed with a plain-text copy that carries one
    `=== page N ===` marker per page, so a quote can be grepped and cited by page and the UI
    can open the PDF there;
  - the planned passage store, embedding index and ontology evidence locators are dropped
    from the plan;
  - the abstract stays a `Work` property. A separate abstract node would only add a hop;
    agents read it from the node or the full-text index.
- The golden mini-graph remains the test fixture until a fixture with keyword concepts exists.
- "Broader concepts" may later need a hierarchy between concepts (a `BROADER` edge). v1 starts
  flat and adds it only if the graph view or Ask mode needs it.
