# ADR-0003 — ORKG gets a thin adapter in this repo, not a shared package with an earlier KBQA project

- **Status:** accepted
- **Date:** 2026-09-22
- **Decides:** open decision 8 in the design-notes spec (`$PORTOLAN_NOTES_DIR/spec.md`)

## Context

An earlier in-house KBQA project has a mature ORKG tool surface (`FindFrequentValues`,
`AggregateComparisonValues`, `RunORKGSPARQL`) built for a question-answering agent over ORKG.
This project needs ORKG for a different job: importing pre-structured contributions for works
already in a review corpus, as an enrichment that skips LLM extraction where coverage exists.

## Decision

A thin ORKG adapter lives in `backend/portolan/adapters/orkg.py`. No shared package.

Reasons: the two jobs differ (interactive QA vs batch enrichment); a shared package would
couple two repos' release cycles and force a refactor in the earlier KBQA project before this
project can move; ORKG coverage here is small by design, so the adapter is small.

## What is reused anyway — the lessons, not the code

1. Three scope tiers from day one (single resource, explicit union, all-of-type). Retrofitting
   the third tier was expensive in the earlier KBQA project.
2. A raw-SPARQL fallback is load-bearing but only about a third of such calls were productive;
   it needs a hard "stop and synthesize" trigger after N unproductive calls.
3. ORKG's schema is irregular — the same fact is modelled several ways. Import goes through a
   mapping layer into the `ptl:` ontology. **ORKG structure never leaks into the graph.**
4. The live endpoint drifts from the SciQA snapshot, so SciQA scores measure the tool, not the
   data.

## Consequences

- The SciQA test split is still used as the regression test for the ORKG tool (M5), reusing
  the earlier project's *harness* by copying it, not by depending on it.
- If a third consumer of ORKG appears, revisit — two is not enough to justify a package.
