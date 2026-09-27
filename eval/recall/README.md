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
screener combines query/seed token overlap (0.45), links to seeds or included
works (0.2), co-citation by the core set (0.25) and citation count (0.10).

`run_recall.py --snowball-depth` still defaults to 1 and passes its value
explicitly, so pass `--snowball-depth 2` to include the chase round.

Run from `backend/`:

```sh
uv run python ../eval/recall/run_recall.py
```

Useful options:

```text
--only KEY             run one survey from surveys.yaml
--max-works N          use a different pipeline cap (default 150)
--snowball-depth N     citation expansion depth (default 1)
--offline              require every HTTP response to be present in the cache
--cache-dir PATH       override Settings.http_cache_dir
```

The default run may make live requests. `--offline` passes cache-only mode to
the existing source adapters; a missing response is reported as an error. API
keys are read through `Settings.from_env()` and only their `set`/`unset` status
is written to `summary.md`.

Results are written to `eval/recall/results/` as one JSON file per survey and a
`summary.md` table with macro averages. HTTP responses belong in the configured
cache directory and are not part of this directory.
