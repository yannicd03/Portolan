# ADR-0004 — API-only LLM access via OpenRouter (DeepSeek direct as the cost fallback)

- **Status:** accepted
- **Date:** 2026-09-22
- **Decides:** open decision 4 in the design-notes spec (`$PORTOLAN_NOTES_DIR/spec.md`)
- **Supersedes:** the `architecture.md` suggestion of local `llama-server` screening

## Context

The design-notes architecture (`$PORTOLAN_NOTES_DIR/architecture.md`) left the local-vs-API split open and floated `llama-server` on the local
RTX 5070 Ti for bulk screening. The user chose **API only, via OpenRouter or DeepSeek**. No
local inference in the pipeline.

## Decision

- **Gateway: OpenRouter.** One key, one client, model-agnostic; structured outputs are a
  filterable capability, so the extraction schema contract holds across model swaps.
- **Cost fallback: DeepSeek direct.** DeepSeek's own API halves prices off-peak
  (01:00-04:00 and 06:00-10:00 UTC Mon-Fri are *peak*; everything else is half price) and
  prices cache hits at roughly 2-3% of cache misses. A batch re-extraction run scheduled
  off-peak goes through DeepSeek direct; interactive runs go through OpenRouter.
- **No local inference.** The 5070 Ti stays out of the pipeline; a local model returns only if
  a cost or privacy constraint appears that the API cannot meet.
- `ptl:model` and `ptl:promptVersion` are recorded on every extraction run regardless of route,
  so a model swap is visible in provenance and re-extraction can be scoped to one run.

## Model tiers and measured prices

Prices in USD per million tokens, read from the OpenRouter models API on **2026-09-22**, and
from `api-docs.deepseek.com` for the direct route. They move; re-check before trusting the
totals below.

| Tier | Model | in | out | context | Used for |
|---|---|---|---|---|---|
| Screening | `deepseek/deepseek-v4-flash` | 0.089 | 0.177 | 1048k | title/abstract include-exclude + reason |
| Extraction (abstract) | `deepseek/deepseek-v4-flash` | 0.089 | 0.177 | 1048k | content layer from abstracts, all included works |
| Extraction (full text) | `deepseek/deepseek-v4-pro` | 0.955 | 1.911 | 1048k | top-N works by relevance and centrality |
| Adjudication | `deepseek/deepseek-v4-pro` | 0.955 | 1.911 | 1048k | concept canonicalization decisions |
| Synthesis | `anthropic/claude-sonnet-5` | 2.000 | 10.000 | 1000k | cluster labels/summaries, gap statements |

Direct DeepSeek (off-peak / peak, cache-miss input → output):
`deepseek-flash` 0.15/0.60 off-peak, 0.30/1.20 peak; `deepseek-v4-pro` 0.66/1.98 off-peak,
1.32/3.96 peak; cache hits 0.003-0.044.

## Cost target for a 1,000-work review

Arithmetic, not a guess — token estimates are the assumption, prices are measured:

| Step | Volume | Tokens (in/out) | Cost |
|---|---|---|---|
| Screening | 1,000 abstracts | 0.60M / 0.08M | $0.07 |
| Abstract extraction | 1,000 works | 0.90M / 0.60M | $0.19 |
| Full-text extraction | top 150 works | 2.70M / 0.38M | $3.30 |
| Concept adjudication | ~300 decisions | 0.60M / 0.09M | $0.74 |
| Synthesis | ~80 calls | 0.64M / 0.10M | $2.24 |
| **Total** | | | **≈ $6.55** |

**Budget ceiling: $10 per review by default**, carried on the `ReviewProtocol`
(`ptl:budgetUsd`), warning at 70%, hard stop at 100% with the run resumable after the user
raises it. A re-run of the same review should approach $0 on the API side: every raw payload
and every LLM response is cached, and the cost ledger is per run.

Levers if the ceiling binds: abstract-first triage (already the design), smaller N for full
text, prompt caching on the DeepSeek route, and batch endpoints.

## Consequences

- Two client routes to keep working; the provider-agnostic client makes this a config switch,
  and route selection is per-stage config, not per-call logic.
- Scholarly papers are public, so routing them through a third-party gateway raises no
  confidentiality issue. A future private-corpus feature would force this ADR open again.
- Model names above are pinned in config, never inferred at runtime. DeepSeek retired
  `deepseek-chat` and `deepseek-reasoner` on 2026-07-24 — exactly the drift that pinning and a
  recorded `ptl:model` are meant to survive.
