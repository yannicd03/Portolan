# Time-sliced backtest

This evaluation checks whether the M6 frontier scores and gap hypotheses
(`backend/portolan/analysis/{frontier,gaps}.py`) anticipate what a field did next.
The project graph is cut back to a cutoff year `T`, analysed exactly as the
application analyses a project, and the results are compared with the works the
same project contains for the years `T+1 .. T+2`.

## Method

1. **Slice** (`slice.py`). `slice_view(view, T)` keeps the works with a year
   `<= T` (works without a year are dropped), the citation edges among them, and
   the concept (and author) nodes and edges attached to them. The per-work
   `in_degree`/`out_degree` fields are recounted on the slice. The input view is
   not modified.
2. **Analyse the slice** (`backtest.py`). `cluster_works`, `analyze_centrality`
   and `find_main_path` are composed into a `ProjectAnalysis` the same way
   `analyze_project` does it (without its cache), then `frontier_scores(...,
   now_year=T)` and `detect_gaps` run on the slice. Nothing in `backend/` is
   changed.
3. **Future works.** `F` is the set of works with a year in `(T, T+2]`. Their
   citations and concepts come from the full project view.

The default cutoff is the newest work year minus two, so the full horizon is
observed.

## Metrics

### Frontier

For every work `w` scored by the frontier (the frontier window: years
`T-2 .. T`), the future uptake `u(w)` is the number of `F`-works citing `w`. A
work is *relevant* when `u(w)` is in the top quartile of `u` over the window
(ties included) and non-zero; the threshold is the `ceil(n/4)`-th largest value.

- **Precision@k** (k = 5, 10): the share of the top-k frontier works (by score)
  that are relevant. With fewer than `k` windowed works the denominator is the
  number of works available. `base_rate` is the share of relevant works in the
  window, i.e. what a random ranking would expect.
- **Spearman**: rank correlation (average ranks for ties) between frontier
  score and `u(w)` over the window; `n/a` when fewer than two works or when
  either side is constant.
- **Baseline**: the same metrics for a ranking by the work's *in-slice* local
  in-degree. `cited_by_count` is not a usable baseline: it is today's total and
  therefore already contains the citations the backtest is trying to predict.
- **Leak-free frontier**: the frontier's velocity component also uses
  `cited_by_count`, which leaks the future in the same way. The result
  therefore also reports the frontier re-ranked with `cited_by_count` removed
  from the slice (`frontier_leak_free`). The gap between the two rows is the
  share of the frontier's apparent skill that comes from look-ahead.

### Gaps

Each hypothesis from the slice is checked against `F`:

| Type | Hit when |
| --- | --- |
| bridging (clusters A, B) | *anticipated*: some `F`-work cites a work in A and a work in B |
| matrix void (concepts X, Y) | *anticipated*: some `F`-work carries both X and Y |
| stagnation (cluster C) | *confirmed*: at most one `F`-work cites into C |

Clusters are the slice's clusters; concepts are per work from the full view.
Per type the result reports the number of hypotheses, hits and rate, and a
**random baseline**: the mean hit rate of equally many random picks (without
replacement) over 20 draws with a fixed seed (`--seed`, default 13). The random
populations mirror what each detector could propose: all pairs of slice
clusters (bridging); pairs of concepts with at least three slice works that
never co-occur in the slice (matrix void); slice clusters with at least
`STAGNATION_MIN_CLUSTER_SIZE` works (stagnation). A type with no hypotheses
has no rate and no baseline.

## Running

From `backend/`:

```sh
uv run python ../eval/backtest/run_backtest.py --project ID [--cutoff T]
uv run python ../eval/backtest/run_backtest.py --golden
```

`--project` loads the project from the configured store (`Settings.from_env()`
and `make_graph`: Neo4j or memory). `--golden` seeds the golden demo project into
an in-memory graph, so it needs no database. Further options: `--horizon N`
(default 2), `--seed`, `--draws` (default 20).

Each run writes `results/<project_id>-T<cutoff>.json` (`golden-T<cutoff>.json` for
`--golden`, whose in-memory project id is random) and rewrites
`results/summary.md` from every result file in the directory, so runs for
several projects accumulate. Outputs contain project ids, names, work ids and
titles, and no configuration values or local paths.

## Limits

- **Small projects.** A project of a few dozen works gives a frontier window of
  a handful of works and few or no gap hypotheses; precision moves in steps of
  `1/k` and the rates are anecdotes, not estimates. The golden fixture has no
  concepts, so it produces no matrix voids and weak clusters.
- **Only the project's own future.** `F` is what the research pipeline
  collected, not the field's full output. A project harvested around a narrow
  query under-represents later work that moved elsewhere, and later works are
  biased towards ones citing the seeds.
- **Citation lag.** Two years is short: many influential works collect most of
  their citations later, so low precision at a short horizon is partly lag.
  Stagnation is favoured by the same lag (few future citers in general).
- **Leakage beyond `cited_by_count`.** Concepts, titles and cluster labels are
  assigned today, and concept assignment may use knowledge of later
  terminology. Only citation edges and years are genuinely time-sliced.
- **Concept noise.** Matrix voids and their anticipation depend on concept
  tagging; a noisy or synonym-split concept vocabulary produces spurious voids
  and misses real fills.
- **Clusters are recomputed on the slice.** Louvain communities on the slice do
  not correspond to clusters on the full graph, so the backtest says nothing
  about the stability of today's cluster labels.
