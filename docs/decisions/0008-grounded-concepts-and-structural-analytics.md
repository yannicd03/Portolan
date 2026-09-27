# ADR-0008 — grounded concepts, and structural frontier and gap analytics

- **Status:** accepted
- **Date:** 2026-09-27
- **Amends:** ADR-0006 (where concepts come from)
- **Related:** ADR-0007 (the chat agents read papers), the design notes' frontier and gap
  definitions (`$PORTOLAN_NOTES_DIR/spec.md`)

## Context

ADR-0006 took concepts from keywords: author keywords and source-API keyword fields,
merged into `Concept` nodes with aliases. On a live 25-paper speculative-decoding project,
OpenAlex's keywords turned out to be systematically wrong for recent CS/AI papers. The
concept layer held "Enhanced Data Rates for GSM Evolution" (for *edge*), "Security token"
(for *token*), "Speech Recognition and Synthesis" and "Ferroelectric and Negative
Capacitance Devices", and none of the field's own terms ("speculative decoding", "draft
model", "KV cache"). A frequency filter alone did not help, because the misclassifications
recur across papers. Clusters, their labels and the gap hypotheses are all computed from
concepts, so this was the largest quality problem in the map.

The design notes define six gap types and a multi-signal frontier score. Several of them
assume claims, limitations and results extracted from full text. ADR-0006 keeps those out
of the graph.

## Decision

**Concepts have two sources, and both are tied to the paper's own text.**

- An OpenAlex keyword is kept only if the work's title and abstract support it. Every
  content word of the term must appear in them, and so must at least one word of a
  parenthetical disambiguator. Alternatively, the term's acronym must appear in
  uppercase as a standalone word. Works without an abstract keep unsupported keywords at
  half score.
- Deterministic keyphrases (1–3 words) are extracted from titles and abstracts. They are
  scored by title bonus × term frequency × idf across the project, and kept when at least
  two works use them.
- Both sources then go through the existing merge (normalisation, acronym linking) and
  the noise filter (generic umbrella terms, terms on more than half of the works,
  single-work terms). `portolan project rebuild-concepts` re-derives an existing
  project's concepts.

No LLM is involved, so concept building stays offline, cheap and reproducible. An LLM
canonicalisation pass can be layered on later without changing the graph model.

**Frontier and gap analytics are structural, and every score shows its parts.**

- The frontier score of a recent work (window relative to the project's newest year,
  not the wall clock) is a weighted mean of visible components: citation velocity,
  in-project uptake, main-path leaf position, cluster growth, a new concept, and preprint
  status.
- The three gap types computable from the graph are detected automatically:
  - *bridging*: concept-similar clusters that rarely cite each other;
  - *matrix void*: well-studied concepts that share neighbours but never co-occur;
  - *stagnation*: a sizeable cluster with little recent work.
- Each hypothesis carries evidence, metrics, a confidence, a verification search outside
  the project, and a user status that persists. Rejections are kept, so a re-run does not
  bring a rejected gap back.
- The gap types that need reading (stated-but-unaddressed, contradiction, evaluation gap)
  belong to the chat agents, which read `paper.txt` with verified quotes (ADR-0007). They
  are not graph analytics.

## Consequences

- Concept quality now depends on abstracts being present. Records without one (often
  arXiv items that OpenAlex under-serves) get fewer, weaker concepts.
- The keyphrase heuristic has no part-of-speech tagging, so some generic single words
  and verbs get through. The noise filter and the matrix-void candidate rules limit how
  much damage they do. An LLM pass is the principled fix once an OpenRouter key is
  configured.
- The frontier and gap analytics are measured by the time-sliced backtest in
  `eval/backtest/`, not asserted.
