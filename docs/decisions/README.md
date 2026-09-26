# Architecture decision records

One file per decision, numbered, never rewritten in place — a superseded ADR keeps its text and
gains a `Superseded by` line.

| # | Decision | Status |
|---|---|---|
| [0001](0001-domain-scope-cs-ai.md) | v1 scope is CS/AI only; domain-general is a roadmap item | accepted |
| [0002](0002-orchestration-langgraph.md) | Orchestration is a plain LangGraph `StateGraph` | accepted |
| [0003](0003-orkg-thin-adapter.md) | Thin in-repo ORKG adapter, no shared package with AMA-KBQA | accepted |
| [0004](0004-llm-provider-and-budget.md) | API-only LLM via OpenRouter, DeepSeek direct as cost fallback; $10/review ceiling | accepted |
| [0005](0005-graph-store.md) | Graph store: RDF/Oxigraph vs Neo4j | **open — settled by the M0 spike** |

Strategic and cross-project context lives in the private design notes at `$PORTOLAN_NOTES_DIR` (set in `.env`);
these ADRs are the per-repo, implementation-level record.
