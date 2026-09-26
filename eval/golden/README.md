# Golden mini-graph

This directory contains the reproducible M0 fixture: the 23 curated CS/AI papers in
`seeds.yaml`, their verified bibliographic records, and the citation edges between them.
The fixture is consumed by both graph-store implementations and by the 14 competency
questions. The generated JSON artifacts are intentionally not part of this change; the
orchestrator creates them from the committed source-response cache.

## Build

From the repository's `backend/` directory, the normal offline build is:

```bash
uv run python ../eval/golden/build_golden.py
```

To populate or refresh the committed response cache from the live APIs, run:

```bash
uv run python ../eval/golden/build_golden.py --refresh --cache-dir ../eval/golden/cache
```

The command writes `works.json`, `citations.json`, and `resolution_report.md` beside this
README. `--only KEY` limits a run to one seed. The default is offline: a missing cached
response is an error, so CI never reaches an external service. `--no-arxiv` skips the
optional arXiv enrichment, and `--with-intents` enables per-paper citation-intent
enrichment only when `SEMANTIC_SCHOLAR_API_KEY` is set. `--allow-failures` still writes
the artifacts and report for inspection, but the process exits non-zero.

## Verification contract

The `arxiv` and `doi` values in `seeds.yaml` are resolution hints, not permission to
substitute a different work. All hinted seeds are resolved by one Semantic Scholar
`paper/batch` request (chunked at the API limit); only seeds with no hint use one title
search request each. Each fetched candidate must have a title whose normalized
case-folded form (punctuation and whitespace removed) clears the strict similarity
threshold, and its year must be within one year of the seed. A title-search result is
also rejected when more than one candidate passes those checks. Every `expect` assertion
(`source_tier` and `is_survey`) is checked against the normalized result. Any mismatch,
ambiguity, year failure, cache miss, or violated expectation is recorded as `FAILURE` in
the audit and makes the command exit non-zero.

`works.json` is keyed by seed key and contains the resolved identifiers, title, year,
abstract, venue, publication types, open-access PDF URL, source tier, survey flag, TL;DR,
and per-field API/timestamp provenance. `citations.json` is an edge list restricted to
papers present in the set and is built from the `references` returned by the batch
response. Without a Semantic Scholar key, batch references provide no intents, contexts,
or influence flag, so `citationFunction` and `isInfluential` are absent. Those fields are
optional enrichment only: `--with-intents` fetches them from per-paper references when a
key is available, and an absent or failed intent never invalidates a seed or edge.

ArXiv is enrichment only. Its batched `id_list` lookup can fill an abstract or categories,
but any error—including HTTP 406 throttling—is recorded as a warning on the affected seed
and cannot turn an otherwise verified seed into a failure.

## Cache and known gaps

The cache contains the raw response body and a small metadata record for every source,
endpoint, and sorted-parameter combination. It is committed because it makes the M0
fixture auditable and reproducible offline, while still retaining enough metadata to
explain when and where a field was fetched. Credentials are never stored in the cache.

OpenAlex is deliberately absent until a paid API key exists. A cold keyless refresh of
the 23-seed set makes exactly 7 source requests in the successful, no-retry path:
Semantic Scholar **6** (one batch plus five title searches), arXiv **1** batched
enrichment request, and Crossref **0**. `--no-arxiv` reduces that to 6. The CLI and
report finish with per-source request and cache-hit counts; the shared retry policy may
add wire attempts when a source returns a retryable status. A live refresh should be
treated as a reviewable data update: inspect `resolution_report.md` before using the
generated fixture.
