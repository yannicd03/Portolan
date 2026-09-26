# ADR-0001 — v1 scope is CS/AI only

- **Status:** accepted
- **Date:** 2026-09-22
- **Decides:** open decision 1 in the design-notes spec (`$PORTOLAN_NOTES_DIR/spec.md`)

## Context

Every high-value signal this project depends on is strongest in computer science: Semantic
Scholar citation intents and SPECTER embeddings, arXiv full text, CS-KG, ORKG's CS coverage,
and the official-blog allowlist (`sources/official_blogs.yaml`, all 17 entries are AI labs).
A domain-general v1 would inherit weak metadata, unusable venue heuristics, and a golden set
the user cannot personally adjudicate.

## Decision

v1 targets CS/AI. Domain-general support is a **roadmap item, not a v1 goal**.

The scope is enforced as *configuration, never as hard-coded assumptions*:

1. No domain-specific logic is compiled into the ontology. `ptl:Problem` / `ptl:Method` /
   `ptl:Dataset` / `ptl:Metric` are domain-neutral by construction.
2. Anything CS-specific lives in config: the blog allowlist, the venue heuristics, the source
   adapters enabled per run, and the screening prompt's field description.
3. Adapters that are CS-only (arXiv, CS-KG) sit behind the same adapter interface as the
   general ones (OpenAlex, Crossref), so adding PubMed/Europe PMC later is an adapter plus a
   config entry, not a refactor.

## Roadmap for widening the domain (post-v1)

| Step | What it needs |
|---|---|
| R1 | A second `sources/*.yaml` profile per domain (allowlist, preprint servers, venue rules) |
| R2 | Domain-appropriate preprint servers: bioRxiv, medRxiv, SSRN, RePEc |
| R3 | A bibliographic adapter that is not arXiv-shaped: PubMed / Europe PMC full text |
| R4 | Replacing SPECTER with a domain-appropriate embedding, or accepting the quality drop |
| R5 | Re-validating the frontier signals — citation velocity norms differ sharply by field |
| R6 | A second golden mini-graph in the new domain before any claim of support |

## Consequences

- The golden set (`eval/golden/seeds.yaml`) is a CS/AI KGQA/RAG corpus, and recall evaluation
  uses CS surveys.
- Coverage-bias risk from the spec is accepted deliberately and stated in the UI rather than
  mitigated: every view already shows source-tier composition.
- No code may branch on "is this CS?". If such a branch becomes necessary, it is a config
  lookup instead.
