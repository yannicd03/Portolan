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
