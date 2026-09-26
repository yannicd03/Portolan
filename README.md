# Portolan

Given a seed paper, a research question, or a topic, an agent runs a bounded, reproducible
literature-research pass over openly available scholarly sources, compiles the result into a
typed knowledge graph with provenance, and serves an interactive map answering four questions:
**what does the landscape look like, which papers are central, where is the frontier, where are the gaps.**

The full product spec, ontology, source policy, and architecture are private design notes kept
outside this repo (`spec.md`, `ontology.md`, `data-sources.md`, `architecture.md`). Docs refer to
their directory as `$PORTOLAN_NOTES_DIR`; set it in your local `.env` (see `.env.example`).
`ontology/README.md` in this repo is the authoritative *in-repo* digest of the vocabulary contract.

## Status

**M0 — decisions and ontology v0.1.** See `docs/decisions/` for ADRs and the milestone table in
`$PORTOLAN_NOTES_DIR/architecture.md`.

Settled decisions (2026-09-22):

| # | Decision | Choice |
|---|---|---|
| D1 | Domain scope for v1 | CS/AI only; domain-general expansion is a roadmap item, not a v1 goal |
| D2 | Orchestration | Plain LangGraph `StateGraph` |
| D3 | ORKG tool surface | Thin adapter in this repo; no shared package with AMA-KBQA |
| D4 | LLM provider | API only, via OpenRouter or DeepSeek; no local `llama-server` |
| D5 | Graph store | **Open** — settled by the M0 spike (Oxigraph/SPARQL vs Neo4j/Cypher), not by discussion |

## Layout

```
ontology/     portolan.ttl, shapes.ttl, competency/{sparql,cypher}/ — the vocabulary contract
sources/      official_blogs.yaml — source allowlist
backend/      uv project (package `portolan`)
  portolan/adapters/  openalex, semanticscholar, arxiv, crossref, unpaywall, orkg, blogs
  portolan/pipeline/  one module per stage + the StateGraph
  portolan/extract/   schemas, versioned prompts, quote verification
  portolan/resolve/   work dedupe, concept canonicalization
  portolan/analyze/   clustering, centrality, main path, frontier, gaps
  portolan/store/     GraphRepository interface + backends, validation, sqlite state, cache
  portolan/api/       FastAPI
eval/golden/  golden mini-graph (~20 CS/AI papers) used to test the ontology and the store spike
docs/decisions/ ADRs
```

## Development

```bash
cd backend
uv sync                # create .venv and install deps
uv run pytest          # tests
uv run ruff check .    # lint
```
