# Golden mini-graph

This directory contains the reproducible M0 fixture: the 23 curated CS/AI papers in
`seeds.yaml`, their verified bibliographic records, and the citation edges between them.
The fixture is consumed by both graph-store implementations and by the 14 competency
questions. The generated artifacts (`works.json`, `citations.json`, and
`resolution_report.md`) are committed and rebuilt from the local source-response cache.
Of the 23 seeds, 19 resolve to verified works. The other 4 (`webqsp`, `kaping`, `hipporag`,
`graphrag_survey`) have no arXiv or DOI hint, so they resolve only through Semantic Scholar
title search, whose responses are not in the cache: keyless title searches are too
rate-limited to fetch them reliably, so they need a Semantic Scholar API key. They are
reported as `FAILURE` and the offline build exits non-zero ("4 seed(s) failed") until a
`--refresh` with `SEMANTIC_SCHOLAR_API_KEY` set caches those searches.

## Build

From the repository's `backend/` directory, the normal offline build is:

```bash
uv run python ../eval/golden/build_golden.py --cache-dir ../eval/golden/cache
```

Passing the cache directory as a relative path keeps absolute local paths out of the
committed `resolution_report.md`, whose cache-miss warnings name the directory searched.

To populate or refresh the local, gitignored response cache from the live APIs, run:

```bash
uv run python ../eval/golden/build_golden.py --refresh --cache-dir ../eval/golden/cache
```

The command writes `works.json`, `citations.json`, and `resolution_report.md` beside this
README. `--only KEY` limits a run to one seed. The default is offline and requires a
populated local cache: a missing cached response is an error, so CI never reaches an
external service. `--no-arxiv` skips the optional arXiv enrichment. `--refresh-arxiv`
permits live requests to arXiv only (one batched `id_list` request) while Semantic Scholar
and Crossref stay on the cache, so arXiv metadata can be refreshed without re-fetching
rate-limited sources. `--with-intents` enables per-paper citation-intent enrichment only
when `SEMANTIC_SCHOLAR_API_KEY` is set. `--allow-failures` still writes
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
and per-field API/timestamp provenance. Each provenance `fetched_at` (and a work's
`retrieved_at`, the fetch time of its `source_api` record) is the time the source response
was fetched, read from the cache metadata, so an offline rebuild reproduces the same
timestamps rather than stamping the build time. `citations.json` is an edge list restricted to
papers present in the set and is built from the `references` returned by the batch
response. Without a Semantic Scholar key, batch references provide no intents, contexts,
or influence flag, so `citationFunction` and `isInfluential` are absent. Those fields are
optional enrichment only: `--with-intents` fetches them from per-paper references when a
key is available, and an absent or failed intent never invalidates a seed or edge.

ArXiv is enrichment only, but it is the preferred abstract source. Its batched `id_list`
lookup replaces the Semantic Scholar abstract of every work with an arXiv id whenever arXiv
returns one (provenance `source: arxiv`), and fills categories when they are empty. Works
without an arXiv id, or whose arXiv lookup returns no abstract, keep the Semantic Scholar
abstract. Any arXiv error—including HTTP 406 throttling—is recorded as a warning on the
affected seed and cannot turn an otherwise verified seed into a failure.

## Cache and known gaps

The cache contains the raw response body and a small metadata record for every source,
endpoint, and sorted-parameter combination. It is local and gitignored. The cache makes the
M0 fixture auditable and reproducible offline, while still retaining enough metadata to
explain when and where a field was fetched. Populate or refresh it by running
`build_golden.py --refresh`; offline runs require a populated local cache. Credentials are
never stored in the cache.

OpenAlex is deliberately absent until a paid API key exists. A cold keyless refresh of
the 23-seed set makes exactly 7 source requests in the successful, no-retry path:
Semantic Scholar **6** (one batch plus five title searches), arXiv **1** batched
enrichment request, and Crossref **0**. `--no-arxiv` reduces that to 6. The CLI and
report finish with per-source request and cache-hit counts; the shared retry policy may
add wire attempts when a source returns a retryable status. A live refresh should be
treated as a reviewable data update: inspect `resolution_report.md` before using the
generated fixture.

## Data licence and attribution

The fixture files in this directory (`works.json`, `citations.json`, `analysis_fixture.yaml`,
`expected_answers.json`) and the spike exports under `eval/spike/parity/` embed third-party
bibliographic data. That data is **not** covered by any licence that applies to this
repository's code; it remains under the terms of its sources:

- **Abstracts of the 18 works with an arXiv id** come from arXiv metadata, which arXiv makes
  available under the [Creative Commons CC0 1.0 Universal Public Domain
  Declaration](https://creativecommons.org/publicdomain/zero/1.0/) (see the
  [arXiv API Terms of Use](https://info.arxiv.org/help/api/tou.html)). The per-field
  `provenance.abstract.source` value `arxiv` marks them.
- **The `sciqa` abstract** is © its authors and published under
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/): S. Auer, D. A. C. Barone,
  C. Bartz, E. G. Cortes, M. Y. Jaradeh, O. Karras, M. Koubarakis, D. Mouromtsev,
  D. Pliukhin, D. Radyush, I. Shilin, M. Stocker, and E. Tsalapati, "The SciQA Scientific
  Question Answering Benchmark for Scholarly Knowledge", *Scientific Reports* 13, 7240
  (2023), <https://doi.org/10.1038/s41598-023-33607-z>. It was retrieved through the
  Semantic Scholar API; apart from whitespace normalization it is unchanged.
- **TL;DRs and the other Semantic Scholar fields** (identifiers, titles, years, venues,
  publication types, open-access links, and the reference lists behind `citations.json`)
  contain information from the [Semantic Scholar](https://www.semanticscholar.org/)
  Academic Graph, which is made available under the
  [ODC Attribution License](https://opendatacommons.org/licenses/by/1-0/). The TL;DRs are
  AI2-generated summaries. Please cite:
  - R. Kinney et al., "The Semantic Scholar Open Data Platform", 2023,
    [arXiv:2301.10140](https://arxiv.org/abs/2301.10140).
  - I. Cachola, K. Lo, A. Cohan, and D. S. Weld, "TLDR: Extreme Summarization of Scientific
    Documents", Findings of EMNLP 2020,
    [arXiv:2004.15011](https://arxiv.org/abs/2004.15011).
- arXiv categories come from arXiv metadata (CC0 1.0, as above).
