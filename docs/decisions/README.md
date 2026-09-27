# Architecture decision records

One file per decision, numbered, never rewritten in place — a superseded ADR keeps its text and
gains a `Superseded by` line.

| # | Decision | Status |
|---|---|---|
| [0001](0001-domain-scope-cs-ai.md) | v1 scope is CS/AI only; domain-general is a roadmap item | accepted |
| [0002](0002-orchestration-langgraph.md) | Orchestration is a plain LangGraph `StateGraph` | superseded in part by 0007 (chat agents) |
| [0003](0003-orkg-thin-adapter.md) | Thin in-repo ORKG adapter, no shared package with AMA-KBQA | accepted |
| [0004](0004-llm-provider-and-budget.md) | API-only LLM via OpenRouter, DeepSeek direct as cost fallback; $10/review ceiling | accepted |
| [0005](0005-graph-store.md) | Graph store is Neo4j (M0 spike: both stores at parity) | accepted |
| [0006](0006-v1-graph-scope.md) | v1 graph: works, citations, authors, merged keyword concepts; the graph is the agents' map | accepted (amended 2026-09-26) |
| [0007](0007-chat-agents-deepagents.md) | Chat agents run on LangGraph Deep Agents; they read papers as files | accepted |
| [0008](0008-grounded-concepts-and-structural-analytics.md) | Concepts are grounded in the paper's text (checked keywords + keyphrases); frontier and gaps are structural and transparent | accepted |

Strategic and cross-project context lives in the private design notes at `$PORTOLAN_NOTES_DIR` (set in `.env`);
these ADRs are the per-repo, implementation-level record.
