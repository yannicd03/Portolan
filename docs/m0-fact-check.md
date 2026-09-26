# M0 fact-check — volatile external facts

Every fact the design leans on that can change under us, with the date it was checked and how.
Re-check before M1 harvesting and before any cost claim is repeated.

## Checked 2026-09-22

| Fact | Result | How |
|---|---|---|
| CiTO namespace and the 8 citation functions | **Confirmed.** Namespace `http://purl.org/spar/cito/`; all eight of `obtainsBackgroundFrom`, `usesMethodIn`, `usesDataFrom`, `extends`, `critiques`, `disputes`, `supports`, `reviews` exist with those exact local names | Fetched the published CiTO documentation at sparontologies.github.io/cito |
| Papers with Code as a leaderboard source | **Dead.** Sunset by Meta on 2025-07-24; redirects to Hugging Face trending papers; the last snapshot is archived under the `pwc-archive` org on Hugging Face | Web search, multiple independent reports |
| Semantic Scholar SPECTER v2 field name | **Unresolved.** Secondary sources say `embedding.specterv2`; the official API docs page and the s2-folks FAQ did not confirm it (the FAQ still shows `specter@v0.1.1`) | Fetched api.semanticscholar.org api-docs and the s2-folks FAQ |
| DeepSeek model names | `deepseek-chat` and `deepseek-reasoner` were **retired 2026-07-24**. Current: `deepseek-flash`, `deepseek-v4-pro` | api-docs.deepseek.com pricing page |
| DeepSeek direct pricing (USD / 1M tokens) | `deepseek-flash` 0.15 in / 0.60 out off-peak, 0.30 / 1.20 peak. `deepseek-v4-pro` 0.66 / 1.98 off-peak, 1.32 / 3.96 peak. Cache hits 0.003-0.044. Peak = 01:00-04:00 and 06:00-10:00 UTC, Mon-Fri | api-docs.deepseek.com pricing page |
| OpenRouter model prices used in ADR-0004 | Read live from the OpenRouter models API; 444 models listed | `GET https://openrouter.ai/api/v1/models` |
| Official research blog URLs | 20 of 22 candidate paths returned 200. `openai.com` returns **403 to automated clients**; `ai.meta.com/research/publications/` returned 500 | `curl -sIL` per URL, recorded in `sources/official_blogs.yaml` |

| Neo4j Community current line | **2026.09.0** (image `neo4j:2026.09.0-community-trixie`, pinned with its OS variant). Cypher 5/25, Community edition confirmed from `dbms.components()` on a running container | Docker Hub tag list + live container |
| neosemantics (n10s) currency | **Lagging.** Latest release is `2025.06.1`, published 2026-06-16 — it targets the Neo4j 2025.06 line, roughly 15 months behind the current 2026.09 line | GitHub releases API for neo4j-labs/neosemantics |

### Why the n10s lag matters

n10s is the *only* route to RDF import/export and SHACL validation inside Neo4j. If it is not
kept current with the Neo4j release line, then choosing Neo4j means either pinning Neo4j to an
older line to keep n10s, or doing RDF export and SHACL validation in Python outside the store.
This project already does the latter (`portolan/store/neo4j_store.py` exports Turtle via rdflib,
`portolan/store/validation.py` runs pyshacl over that export), so the lag is survivable — but it
removes the "n10s gives you RDF interop for free" argument from the Neo4j column. Recorded as
spike evidence in ADR-0005.

## Consequences already applied

- `sources/official_blogs.yaml` v1 written with 17 organizations, per-entry verification status
  and date. The OpenAI entry is `enabled: false` pending a decision on browser-like fetching.
- ADR-0004 pins model names and records measured prices with the date.
- Papers with Code is confirmed out; benchmark/SOTA signals must come from extracted `ptl:Result`
  nodes, not from an external leaderboard.

## Still open

- **SPECTER v2 field name.** Needed at M1 when work embeddings are fetched. Resolve empirically:
  request `fields=embedding.specter_v2` and `fields=embedding.specterv2` against the live API
  once a Semantic Scholar key exists and keep whichever returns a vector. Do not hard-code
  either spelling until that test has run.
- **ROR IDs** for the allowlisted organizations — deliberately left null rather than guessed;
  they arrive free with the OpenAlex adapter at M1.
- **OpenAlex** remains unusable until a key exists (mandatory and usage-billed since 2026-02-13).
  Nothing in M0 depends on it; the golden set is built from arXiv, Semantic Scholar and Crossref.
