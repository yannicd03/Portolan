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
| speculative-decoding | 46 | 714 | 150 | 0.652 | 0.304 | 0.093 |
| efficient-transformers | 94 | 736 | 148 | 0.415 | 0.223 | 0.149 |
| rag | 202 | 708 | 149 | 0.267 | 0.124 | 0.168 |

Macro averages:

- Candidate recall: 0.445
- Included recall: 0.217
- Included precision: 0.137

`R` is the survey's identity-resolved reference set. Missed reference titles are listed in each per-survey JSON file, capped at 50.
