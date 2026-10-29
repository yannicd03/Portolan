# ADR-0011 — PDF storage and licence policy

- **Status:** proposed — not decided; the user decides between the options below
- **Date:** 2026-10-25
- **Decides:** where downloaded papers may live, what licence and open-access metadata is kept
  for each, which PDFs are fetched and kept, and what project data may be exported or shared
- **Related:** ADR-0006 (every acquired PDF is stored content-addressed with a text copy),
  ADR-0007 (agents read the stored papers)

## Context

The repository is public and has no licence file yet (README, "Licence"). The papers it
downloads belong to third parties. Current state:

- **Storage.** `portolan.documents.store.DocumentStore` keeps one directory per PDF under
  `documents/<sha[:2]>/<sha>/`: `paper.pdf`, `paper.txt` (page markers), `outline.json` and
  `meta.json`. The root is `Settings.documents_dir`, which is `data_dir/documents`, and
  `data_dir` defaults to `data/` in the repository (`PORTOLAN_DATA_DIR` changes it). In compose it is
  the `portolan-data` volume. `.gitignore` excludes `data/`, `cache/` and
  `eval/recall/pools/` (frozen pools, "contain third-party abstracts"). Nothing is excluded by
  file type, so a data directory set elsewhere inside the checkout is not covered. No PDF or
  `paper.txt` is tracked today.
- **Which PDFs are tried.** `ResearchRunner._pdf_candidates` and `_acquire` collect:
  - OpenAlex `best_oa_location` plus the other locations that are `is_oa`
    (`OpenAlexAdapter`);
  - arXiv, built from the arXiv id with only a URL and a source, so no licence;
  - Semantic Scholar's `open_access_pdf_url`, with no licence;
  - Unpaywall's `best_oa_location` and `oa_locations` (`UnpaywallAdapter`, only when
    `PORTOLAN_CONTACT_EMAIL` is set).

  `rank_candidates` tries arXiv first, then published, then accepted versions, and
  `content.openalex.org` URLs (paid downloads) last. Every source is open-access by
  construction, but none of them checks the licence. A PDF with no licence is fetched and kept.
- **What is kept.** `meta.json` and `DocumentRecord` hold `source`, `source_url`, `version`
  and `license` for the candidate that succeeded. `host_type` is collected on `PdfCandidate`
  and then dropped. `is_oa` and the OA status are not stored on the document or the `Work`.
  The full Unpaywall response is only in the HTTP cache. arXiv comes first and has no licence,
  so many stored papers likely have `license: null`.
- **Sharing.** All ports in `compose.yaml` bind to `127.0.0.1`, and
  `/api/documents/{sha256}/pdf` and `/text` have no authentication. There is no export
  endpoint or command. Deleting a project removes its node, runs, chats and gap statuses. The
  documents stay, because they are shared between projects. Chat answers keep verbatim quotes
  of one to three sentences.

## Options

**1. Keeping papers out of git.**
- *1a — as today:* rely on `data/` being ignored.
- *1b — also exclude by file type:* add `*.pdf`, `paper.txt` and `outline.json` to
  `.gitignore`, and have a test or check fail if a tracked file is a PDF or sits under a
  document-store path. It costs little and covers a mistyped `PORTOLAN_DATA_DIR`.

**2. Licence metadata per document.**
- *2a — as today:* one `license` string, often missing.
- *2b — a licence record in `meta.json`:* `license`, where the licence came from (OpenAlex,
  Unpaywall, arXiv), `host_type`, `version`, `is_oa` and the OA status, taken from data the
  adapters already fetch. For arXiv, take the licence from arXiv's own metadata where it is
  available. Missing values stay `null` and are never guessed.
- *2c — 2b plus licence fields on the `Work` node,* so the graph and UI can filter by licence.
  This puts acquisition details on a shared node, which ADR-0006 kept small.

**3. Which PDFs are fetched and kept.**
- *3a — any open-access location, any licence (today):* the most full text for Ask mode. The
  local copy is for personal reading and checking quotes.
- *3b — only explicitly open licences (CC, public domain):* the cleanest legal position, but it
  would exclude arXiv's default licence and every unlabelled copy. That is most CS/AI full
  text (ADR-0001), so most Ask answers would fall back to abstracts.
- *3c — fetch as in 3a, keep locally, and check the licence only when something leaves the
  machine:* storage stays private. Export and sharing check the licence record.
- In every option, paywalled sources and login-only URLs are not tried. `content.openalex.org`
  stays last, or is dropped if its cost is not wanted.

**4. Export and sharing of project data.**
- *4a — no export (today).*
- *4b — metadata-only export:* the project's works (identifiers, title, year, abstract), the
  citation, author and concept edges, run requests, gap statuses, and chats with citations as
  work id, page and short quote. No PDFs or extracted text. A recipient can fetch the papers
  again from their identifiers.
- *4c — 4b plus PDFs and text where the licence record allows redistribution.* This needs 2b
  and 3c first.

## Recommendation

**1b, 2b, 3c, 4b**, with 4c left for later. Papers never go into git. Each stored document
records where its licence came from. Acquisition stays as broad as open access allows, but the
copies stay local. Anything shared is metadata and short quotes until the licence records are
good enough for 4c. This is the recommendation only. The user has not decided.

## Consequences (if accepted)

- `DocumentRecord` and `meta.json` get optional fields, and older records read as `null`.
  Filling them in for stored PDFs needs a backfill command like
  `portolan documents backfill-outlines` that uses the cached adapter responses.
- The README states the policy: PDFs and extracted text are local copies for personal use, kept
  out of the repository and out of exports.
- An export format (likely JSON, alongside the Turtle export that ADR-0005 makes mandatory) needs its own small
  design. If any port stops binding to `127.0.0.1`, the document endpoints need authentication
  first.
- The repository licence (README, "Licence") is separate: it covers the code, not the papers.
