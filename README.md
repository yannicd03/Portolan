# Portolan

A research workspace. You create a project on a topic, and an agent researches it for you:
in **Research mode** it searches openly available scholarly sources, downloads the papers and
builds a local knowledge graph; in **Ask mode** it answers your questions from that graph and
backs every claim with a citation to the passage in the paper it came from. A graph view shows
how the papers relate: who cites whom, shared authors, and shared concepts.

The full product spec, source policy, and architecture are private design notes kept outside
this repo (`spec.md`, `ontology.md`, `data-sources.md`, `architecture.md`). Docs refer to their
directory as `$PORTOLAN_NOTES_DIR`; set it in your local `.env` (see `.env.example`).

## Status

**M0 is done; the chat and research pipeline are not built yet.** What runs today is a scaffold:
a FastAPI backend and a React frontend showing the citation map of the golden mini-graph, backed
by Neo4j. See `docs/decisions/` for the ADRs.

| # | Decision | Choice |
|---|---|---|
| [0001](docs/decisions/0001-domain-scope-cs-ai.md) | Domain scope for v1 | CS/AI only |
| [0002](docs/decisions/0002-orchestration-langgraph.md) | Orchestration | Plain LangGraph `StateGraph` (chat agents: see 0007) |
| [0003](docs/decisions/0003-orkg-thin-adapter.md) | ORKG tool surface | Thin adapter in this repo |
| [0004](docs/decisions/0004-llm-provider-and-budget.md) | LLM provider | API only, via OpenRouter or DeepSeek |
| [0005](docs/decisions/0005-graph-store.md) | Graph store | Neo4j |
| [0006](docs/decisions/0006-v1-graph-scope.md) | v1 graph | Works, citations, authors, merged keyword concepts; the graph is the agents' map |
| [0007](docs/decisions/0007-chat-agents-deepagents.md) | Chat agents | LangGraph Deep Agents, reading papers as files |

## Run it

```bash
cp .env.example .env     # then set NEO4J_PASSWORD (any value; it initialises the database)
docker compose up --build
```

Open <http://localhost:8080>. On first start the backend loads the golden mini-graph into the
empty database (`PORTOLAN_SEED_GOLDEN=true`, the compose default). The API is also reachable
directly on <http://localhost:8000/api/health>, and Neo4j Browser on <http://localhost:7474>.
All ports bind to localhost only.

## Layout

```
compose.yaml  frontend (nginx) -> backend (FastAPI) -> neo4j
frontend/     React + Vite + Sigma.js; nginx serves the build and proxies /api
backend/      uv project (package `portolan`), Dockerfile
  portolan/api/       FastAPI app
  portolan/store/     GraphRepository interface; Neo4j backend (+ Oxigraph, kept from the spike)
  portolan/adapters/  arxiv, crossref, semanticscholar
  portolan/cli.py     `portolan serve`, `portolan seed-golden`
ontology/     M0 vocabulary contract and competency questions (spike artifacts, see ADR-0006)
eval/golden/  golden mini-graph (~20 CS/AI papers): test fixture and demo data
eval/spike/   the ADR-0005 graph-store spike harness and results
sources/      official_blogs.yaml — source allowlist
docs/decisions/ ADRs
```

## Development

```bash
cd backend
uv sync                # create .venv and install deps
uv run pytest          # tests (Neo4j tests run when NEO4J_URI is set)
uv run ruff check .    # lint
uv run portolan serve  # API on :8000; needs NEO4J_URI/NEO4J_PASSWORD, or PORTOLAN_STORE=oxigraph

cd frontend
npm install
npm run dev            # Vite dev server; proxies /api to 127.0.0.1:8000
```
