# Survey recall evaluation

Generated: 2026-09-27

Settings used:
- max_works: survey value or 150
- snowball_depth: 2
- acquire_pdfs: false
- offline: false
- OPENALEX_API_KEY: unset
- SEMANTIC_SCHOLAR_API_KEY: unset

| Survey | R | Candidates | Included | Candidate recall | Included recall | Included precision |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| speculative-decoding | 46 | 660 | 150 | 0.435 | 0.239 | 0.073 |
| efficient-transformers | 94 | 736 | 146 | 0.415 | 0.319 | 0.212 |
| rag | 202 | 706 | 150 | 0.272 | 0.149 | 0.213 |

Macro averages:

- Candidate recall: 0.374
- Included recall: 0.236
- Included precision: 0.166

`R` is the survey's identity-resolved reference set. Missed reference titles are listed in each per-survey JSON file, capped at 50.
