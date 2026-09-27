# Survey recall evaluation

This evaluation measures how much of a survey paper's reference list the
research pipeline can discover. For each survey, OpenAlex references are
resolved into the ground truth set `R`; Semantic Scholar references are used
when OpenAlex has no reference list. The survey itself is excluded from the
pipeline run.

The metrics match works by shared OpenAlex ID, DOI, or arXiv ID. `recall_candidates`
measures the fraction of `R` present in the candidate pool,
`recall_included` measures the fraction that survives screening, and
`precision_included` measures the fraction of included works that are in `R`.
Reference titles missed by the candidate pool are included in each result JSON,
up to 50 titles.

## Pipeline defaults

`ResearchRequest` fields that are not set by `run_recall.py` use the pipeline
defaults:

```text
snowball_depth    2     depth 1 expands the core set; depth 2 is the chase round
core_search_hits  10    top search hits that join the seeds in the core set
chase_top         20    top screened non-seed candidates whose references are chased
min_score         0.15  candidates below this score are not included (seeds always are)
forward_per_work  20    forward citations fetched per core-set work
```

The OpenAlex search asks for `min(200, max(50, 2 * max_works))` hits. The core
set (seeds plus the top `core_search_hits` hits) is expanded backward and
forward; without seeds every search hit is expanded backward. At depth 2 the
pool is screened once and the backward references of the `chase_top` best
candidates are added. The candidate pool is capped at `5 * max_works`. The
screener combines TF-IDF similarity to the query, seed and core texts
(`semantic`), links to seeds or included works (`link`), co-citation by the core
set (`cocitation`), bibliographic coupling with the core set (`coupling`) and
citation count (`citation`); see "Screening" below for the weights. Before W25
it combined token overlap (0.45), link (0.2), co-citation (0.25) and citation
count (0.10).

`run_recall.py --snowball-depth` defaults to 2, so the chase round is included;
pass `--snowball-depth 1` to measure without it.

Run from `backend/`:

```sh
uv run python ../eval/recall/run_recall.py
```

Useful options:

```text
--only KEY             run one survey from surveys.yaml
--max-works N          use a different pipeline cap (default 150)
--snowball-depth N     citation expansion depth (default 2)
--offline              require every HTTP response to be present in the cache
--cache-dir PATH       override Settings.http_cache_dir
--dump-pools [DIR]     also write each screened candidate pool (default DIR: eval/recall/pools)
```

The default run may make live requests. `--offline` passes cache-only mode to
the existing source adapters; a missing response is reported as an error. API
keys are read through `Settings.from_env()` and only their `set`/`unset` status
is written to `summary.md`.

Results are written to `eval/recall/results/` as one JSON file per survey and a
`summary.md` table with macro averages. HTTP responses belong in the configured
cache directory and are not part of this directory.

## Screening

Discovery finds far more of `R` than screening keeps (live run, max_works 150,
depth 2: candidate recall 0.445, included recall 0.217 macro). Screening is
therefore evaluated offline on frozen candidate pools instead of re-running the
harvest for every change.

### Candidate pools

`run_recall.py --dump-pools [DIR]` writes `DIR/<key>.pool.json` per survey: the
pool exactly as the runner screened it (every candidate's record, how it was
found: seed, search rank, snowball depth, core membership), the seed and core
keys, the core reference counts, the request, the survey's identities and the
identity-only ground-truth records. `eval/recall/pools/` is gitignored because
the records carry third-party abstracts. A pool reflects the screener in use
when it was dumped: the depth-2 chase round expands the references of the best
screened candidates, so a different screener could have produced a slightly
different pool.

### Offline evaluation

Run from `backend/`:

```sh
uv run python ../eval/recall/screen_eval.py                      # current defaults
uv run python ../eval/recall/screen_eval.py --screener legacy    # pre-W25 weights
uv run python ../eval/recall/screen_eval.py --weight coupling=0 --weight semantic=0.5
uv run python ../eval/recall/screen_eval.py --grid               # weight search, leave-one-out
```

The evaluation rebuilds the runner's screening context from each pool and
selects with the runner's own `select_candidates` (the greedy loop behind
`ResearchRunner._screen`), then reports included recall and precision per survey
and macro. `--max-works` and `--min-score` override the request values; `--fast`
(and `--grid`, always) use an incremental selection that is tested to pick the
same works but avoids rescoring the whole pool at every step. No network access.

Screener components (`backend/portolan/research/screening.py`), each with its own
weight; the additive weights are normalised to sum to one:

```text
semantic    TF-IDF cosine (sublinear tf, smoothed idf over the pool) between title+abstract
            and a profile: half the query, half the centroid of seed and core-set texts
token       Jaccard token overlap with query and seed texts (the pre-W25 text signal; off)
link        cites, or is cited by, a seed or an already included work
cocitation  core works referencing the candidate / the largest non-seed count in the pool
coupling    share of the candidate's references that at least two core works also cite
citation    log citation count relative to the pool maximum
recency     penalty (not normalised) for works newer than the request's to_year; default 0
```

Callers that pass only the pre-W25 weights (`token`, `link`, `citation`,
`cocitation`) keep the pre-W25 score, including co-citation normalised by core
size.

### Weight search

`--grid` evaluates every non-zero combination of the values 0, 0.1, 0.2, 0.3,
0.4 for `semantic`, `cocitation`, `coupling`, `link` and `citation` (normalised,
duplicates removed: 2851 vectors). For each survey in turn, the weights with the
best mean included recall on the other two surveys are chosen and reported on
the held-out survey. The average and (if any) majority of the three fold choices
are the robust candidates for the defaults; the best in-sample point is not
used.

Results: pending. The pools of the three surveys have not been dumped yet
(the local HTTP cache does not cover the live harvest), so the current defaults
(`semantic 0.40, cocitation 0.25, link 0.15, coupling 0.15, citation 0.05`) are
provisional and not yet backed by the held-out table below.

| Held out | Weights chosen on the other two | Held-out recall | Held-out precision | Default recall | Legacy recall |
| --- | --- | ---: | ---: | ---: | ---: |
| speculative-decoding | pending | | | | |
| efficient-transformers | pending | | | | |
| rag | pending | | | | |
