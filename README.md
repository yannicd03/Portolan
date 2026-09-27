# Portolan

Portolan is a research workspace for scholarly literature. For each project it harvests open
literature (OpenAlex, with Semantic Scholar, arXiv and Unpaywall as enrichers) into a Neo4j
graph of works, citations, authors and concepts, where every concept is checked against the
text of the paper. It then maps the result: clusters, central works, the main citation path, the
research frontier, and structural gaps. You can also chat with the papers. **Ask mode** answers
questions from the downloaded PDFs, and the server checks every quote before it is shown.
**Research mode** plans a harvest with you and runs it once you approve the plan.

The full product spec, source policy and design notes are private and kept outside this repo.
The docs refer to their directory as `$PORTOLAN_NOTES_DIR`.

## Features

- **Projects.** Works, authors and concepts are shared across projects. A project is the set of
  works it includes.
- **Research runs.** Resolve seeds (DOI, arXiv id or OpenAlex id), search OpenAlex, snowball
  citations backward and forward, screen candidates, write the graph, rebuild concepts, and
  download open-access PDFs. Runs are cancellable. Run history survives restarts, and each
  report shows how many works the run added.
- **Grounded concepts** (ADR-0008). An OpenAlex keyword is kept only if the work's title and
  abstract support it. Key phrases extracted from titles and abstracts are added, then merged
  and filtered for noise.
- **Map.** A Sigma.js citation map with four lenses (Landscape, Central, Frontier, Gaps), cluster
  or year colouring, a cluster legend, a timeline filter, work details, and an in-app PDF viewer
  that opens on the cited page.
- **Analytics.** Louvain clusters, PageRank and betweenness roles, main path, a frontier score
  that shows its component parts, and gap hypotheses (bridging, unexplored combinations,
  stagnation). The gap board lets you verify, accept, reject and annotate them.
- **Chat.** Deep Agents (ADR-0007) read `paper.txt` files with page markers. Every Ask-mode
  citation is re-checked against the paper text on the server.
- **Evaluation.** Recall is measured against survey reference lists, and a time-sliced backtest
  measures the frontier and gap analytics.

User guide: [docs/user-guide.md](docs/user-guide.md). Architecture:
[docs/architecture.md](docs/architecture.md).

## Quick start (Docker Compose)

```bash
cp .env.example .env      # then edit .env (see below)
docker compose up --build
```

Open <http://localhost:8080>. All ports bind to localhost only:

| Service | Default | Override |
|---|---|---|
| Web UI (nginx, proxies `/api`) | <http://localhost:8080> | `PORTOLAN_WEB_PORT` |
| API (FastAPI) | <http://localhost:8000/api/health> | `PORTOLAN_API_PORT` |
| Neo4j Browser / Bolt | <http://localhost:7474> / `7687` | `NEO4J_HTTP_PORT` / `NEO4J_BOLT_PORT` |

On first start, when the database is empty, the backend loads the golden mini-graph as a demo
project (`PORTOLAN_SEED_GOLDEN=true` is the compose default). PDFs, chats, run history, gap
decisions and the HTTP cache live in the `portolan-data` volume. The graph lives in `neo4j-data`.

### Environment variables

Compose passes the whole `.env` file to the backend. The variables that matter:

| Variable | Needed? | What it unlocks |
|---|---|---|
| `NEO4J_PASSWORD` | **required** | Initialises the Neo4j database. Any value works. Compose refuses to start without it. |
| `OPENALEX_API_KEY` | strongly recommended | OpenAlex is the resolver, search and snowball source. Without a key, requests are anonymous: they are throttled under load and have a small daily budget, which a single run can exhaust. |
| `SEMANTIC_SCHOLAR_API_KEY` | optional | Authenticated Semantic Scholar enrichment of included works (runs anonymously without it). |
| `PORTOLAN_CONTACT_EMAIL` | recommended | Enables the Unpaywall adapter (more open-access PDFs). It is also added as `mailto:` to the User-Agent of PDF downloads. |
| `OPENROUTER_API_KEY` | for chat | Enables Ask and Plan-research chat. Without it the chat shows "Ask mode is unavailable". |
| `PORTOLAN_CHAT_MODEL` | optional | OpenRouter model id for the chat agents (default `deepseek/deepseek-v4-pro`). |
| `OPENROUTER_BASE_URL` | optional | OpenAI-compatible endpoint (default `https://openrouter.ai/api/v1`). |
| `PORTOLAN_CHAT_MAX_TOOL_CALLS` | optional | Tool-call budget per Ask turn (default 40). |
| `PORTOLAN_MAX_CONCURRENT_RUNS` | optional | Research runs executed in parallel across projects (default 1; one active run per project). |
| `PORTOLAN_SEED_GOLDEN` | optional | Load the demo project into an empty database. |
| `PORTOLAN_LOG_LEVEL` | optional | Backend log level (default `INFO`). |

Outside compose you also need `NEO4J_URI` (and optionally `NEO4J_USER` and `NEO4J_DATABASE`),
`PORTOLAN_DATA_DIR` (default `<repo>/data`), and optionally `PORTOLAN_STORE=memory` for a
throwaway in-process graph. `PORTOLAN_STORE` accepts `neo4j` or `memory`.

## Local development

Backend ([uv](https://docs.astral.sh/uv/) project, Python ≥ 3.12):

```bash
cd backend
uv sync                          # create .venv and install dependencies
uv run pytest                    # offline test suite
uv run ruff check .              # lint
NEO4J_URI=bolt://localhost:7687 NEO4J_PASSWORD=... uv run portolan serve   # API on 127.0.0.1:8000
```

`docker compose up neo4j` gives you a local database for this. The live Neo4j contract tests in
`tests/test_research_graph.py` run only when `PORTOLAN_TEST_NEO4J_URI` is set, together with
`PORTOLAN_TEST_NEO4J_PASSWORD` (and optionally `PORTOLAN_TEST_NEO4J_USER`). **They delete every
node in that database**, so point them at a disposable instance only. The older M0 spike and
parity tests run when `NEO4J_URI` is set.

Frontend (React 19 + Vite + Sigma.js + pdf.js):

```bash
cd frontend
npm install
npm run dev      # Vite dev server; proxies /api to PORTOLAN_API_URL or http://127.0.0.1:8000
npm run build    # type-check and build
npm run lint     # oxlint
```

## CLI

The backend installs a `portolan` command. It reads the same environment variables as the API.
In Docker, run it with `docker compose exec backend portolan ...`.

| Command | What it does |
|---|---|
| `portolan serve [--host 127.0.0.1] [--port 8000]` | Run the HTTP API. |
| `portolan seed-golden` | Seed the golden demo project into an empty graph. |
| `portolan project create NAME [-d DESCRIPTION]` | Create a project and print it as JSON. |
| `portolan project list` | List projects as JSON. |
| `portolan project delete PROJECT_ID` | Delete a project. |
| `portolan project rebuild-concepts PROJECT_ID` | Re-derive a project's concepts with the grounded pipeline. Prints `kept= filtered= ungrounded= text_phrases=`. Use this on projects harvested before ADR-0008. |
| `portolan research PROJECT_ID [--seed ID]... [--query TEXT] [--max-works 100] [--depth 2] [--no-pdfs] [--max-pdfs 50]` | Run the research pipeline synchronously and print the report. The run is not added to the UI's run history. |
| `portolan documents backfill-outlines [--rebuild]` | Build outline sidecars for stored PDFs that do not have one yet. |

## Evaluation

Run both from `backend/`:

- **Recall against surveys** ([eval/recall/README.md](eval/recall/README.md)): for each survey in
  `eval/recall/surveys.yaml`, measures how much of its reference list the pipeline finds
  (candidate pool and included set), while excluding the survey itself.
  `uv run python ../eval/recall/run_recall.py [--only KEY] [--max-works N] [--snowball-depth N] [--offline]`
- **Time-sliced backtest** ([eval/backtest/README.md](eval/backtest/README.md)): cuts a project
  back to a cutoff year, runs the frontier and gap analytics on the slice, and scores them
  against what the project contains for the next two years, compared with baselines.
  `uv run python ../eval/backtest/run_backtest.py --project ID [--cutoff T]` or `--golden`
  (no database needed).

The golden mini-graph ([eval/golden/README.md](eval/golden/README.md)) is the offline test
fixture and demo project.

## Architecture decisions

| # | Decision | Status |
|---|---|---|
| [0001](docs/decisions/0001-domain-scope-cs-ai.md) | v1 scope is CS/AI only | accepted |
| [0002](docs/decisions/0002-orchestration-langgraph.md) | Orchestration is a plain LangGraph `StateGraph` | superseded in part by 0007 |
| [0003](docs/decisions/0003-orkg-thin-adapter.md) | Thin in-repo ORKG adapter | accepted |
| [0004](docs/decisions/0004-llm-provider-and-budget.md) | API-only LLM via OpenRouter, DeepSeek direct as cost fallback | accepted |
| [0005](docs/decisions/0005-graph-store.md) | Graph store is Neo4j | accepted |
| [0006](docs/decisions/0006-v1-graph-scope.md) | v1 graph: works, citations, authors, concepts; the graph is the agents' map | accepted, amended by 0008 |
| [0007](docs/decisions/0007-chat-agents-deepagents.md) | Chat agents run on LangGraph Deep Agents and read papers as files | accepted |
| [0008](docs/decisions/0008-grounded-concepts-and-structural-analytics.md) | Concepts are grounded in the paper's text; frontier and gaps are structural and transparent | accepted |

## Repository layout

```
compose.yaml        frontend (nginx) -> backend (FastAPI) -> neo4j
backend/            uv project, package `portolan`, Dockerfile, tests/
  portolan/api/        FastAPI app and routers (runs, chat, insights)
  portolan/research/   the harvest pipeline (runner, screening, source facade)
  portolan/adapters/   OpenAlex, Semantic Scholar, arXiv, Unpaywall, Crossref clients
  portolan/graph/      ResearchGraph interface; Neo4j and in-memory implementations
  portolan/concepts/   keyword grounding, keyphrases, merging, noise filter
  portolan/documents/  content-addressed PDF store, text extraction, outlines, PDF fetcher
  portolan/analysis/   clusters, centrality, main path, frontier, gaps, gap store
  portolan/agent/      Ask and Research agents, graph tools, citation verification, chat store
  portolan/cli.py      the `portolan` command
  portolan/store/      M0 spike repositories (Neo4j + Oxigraph), kept with the parity tests
frontend/           React + Vite app; nginx serves the build and proxies /api
eval/recall/        survey recall evaluation
eval/backtest/      time-sliced backtest of frontier and gaps
eval/golden/        golden mini-graph (test fixture and demo data)
eval/spike/         the ADR-0005 graph-store spike harness and results
ontology/           M0 vocabulary contract and competency questions (spike artifacts)
sources/            official_blogs.yaml, the source allowlist
docs/               user guide, architecture, ADRs
```

## Licence

The repository does not include a licence file yet, so it is not yet licensed for reuse.
