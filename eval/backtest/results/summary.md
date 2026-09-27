# Time-sliced backtest

Generated: 2026-09-27

Frontier precision counts a top-ranked work as a hit when its citations from works published in (T, T+2] are in the top quartile of the frontier window. The baseline ranks by in-slice local in-degree; the leak-free columns re-rank without `cited_by_count`. Gap rates are checked against the same future works; the random baseline draws equally many random clusters or cluster/concept pairs. See README.md for details.

| Project | T | Slice works | Future works | Frontier P@5 | P@10 | Spearman | Baseline P@5 | P@10 | Spearman | Leak-free P@5 | P@10 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| golden | 2022 | 9 | 10 | 0.400 | 0.333 | 0.246 | 0.400 | 0.333 | 0.188 | 0.400 | 0.333 |

| Project | Gap type | Hypotheses | Anticipated/confirmed | Rate | Random baseline |
| --- | --- | ---: | ---: | ---: | ---: |
| golden | bridging | 0 | 0 | n/a | n/a |
| golden | matrix_void | 0 | 0 | n/a | n/a |
| golden | stagnation | 0 | 0 | n/a | n/a |
