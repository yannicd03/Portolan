# ADR-0009 — how a `Project` relates to the ontology's `Review`

- **Status:** proposed — not decided; the user decides between the options below
- **Date:** 2026-10-25
- **Decides:** whether the product's `Project` is a rename of `ptl:Review` or a wrapper that owns
  one or more reviews, and what a research run is in ontology terms
- **Related:** ADR-0004 (the budget is "per review"), ADR-0006 (reviews left the v1 graph)

## Context

The v0.2 ontology (`ontology/README.md`, layer 3) scopes all analysis to a `ptl:Review`. A
review has exactly one seed (`ptl:seedKind`, `ptl:seedValue`) and exactly one
`ptl:ReviewProtocol` (scope, inclusion and exclusion criteria, year bounds, allowed tiers,
`maxWorks`, `maxSnowballDepth`). `Inclusion`, `Cluster`, `ClusterPair` and `GapHypothesis` each
point at one review, and ADR-0004 puts the $10 ceiling on the review's protocol
(`ptl:budgetUsd`). The M0 classes are in `portolan.models` (`Review`, `ReviewProtocol`,
`Inclusion`).

ADR-0006 dropped reviews from the v1 graph, and the product built something else:

- **Project.** `portolan.graph.models.Project` is a `(:Project)` node with an id, a name, a
  description and a creation time. It holds no seed, protocol or budget. Membership is
  `(:Project)-[:INCLUDES {discovered_via, depth, score, added_at}]->(:Work)`. Works, authors and
  concepts are shared between projects.
- **Runs.** A research run is `portolan.api.models.Run`. Its `ResearchRunRequest` carries the
  seeds, exclusions, query, year range, `max_works`, `snowball_depth`, the screening threshold
  and the PDF settings, so in practice it is a review protocol. `portolan.api.runs.RunRegistry`
  stores runs as JSON files under `data/runs/<project_id>/`, not in the graph. It allows one
  active run per project and keeps the newest 50.
- **Runs add up.** Each run adds to the same project. `ResearchRunner` writes inclusions with
  `include_work`, and the Neo4j `MERGE` keeps the first run's `discovered_via` and the smallest
  depth. Nothing on the edge records which run added a work. `RunRegistry._with_increment`
  works out a run's contribution afterwards by comparing the project's works before and after
  the run (at most 200 ids are listed). Candidates removed by screening only appear as a count
  (`RunReport.screened_out`), so the ontology's CQ13 ("why was W excluded, at which stage")
  cannot be answered.
- **Analysis is per project.** Clusters and the main path
  (`portolan.analysis.service.analyze_project`), frontier scores
  (`portolan.analysis.frontier`) and gaps are computed over the union of all runs. Gap statuses
  are kept in `portolan.analysis.gap_store`, one JSON file per project.
- **No budget ledger.** `budget_usd` and `spend_usd` appear only in the M0 models. Nothing in
  the v1 code records spend per run or per project.

So the product has two levels, Project and Run. The ontology has one level, Review, and it
carries both roles: the scope that analysis belongs to and the protocol a harvest followed.

## Options

**A — `Project` is a renamed `Review`.** One review per project. The protocol becomes the
project's settings.

- *For:* the analysis layer maps directly, because clusters and gaps are already per project.
  It needs the least new vocabulary.
- *Against:* the cardinalities do not fit. A project has many runs with different seeds,
  queries and year ranges, so either only the latest run's protocol is kept or `hasProtocol`
  and `seedValue` become repeatable, which removes what made them a protocol. Per-run
  provenance has nowhere to go, and the per-review budget becomes a lifetime project budget.

**B — `Project` wraps one or more `Review`s, one per run.** A run is a review, and its
request is its protocol.

- *For:* it matches how runs are stored today and how the budget works (one run starts, spends
  and stops). Inclusions, including exclusions with a reason, can be kept per run.
- *Against:* the analysis layer does not match. The ontology makes clusters and gaps
  review-scoped, but the product computes them over the whole project, and a gap the user
  rejected must stay rejected across runs. So either analysis is moved to the `Project`
  (changing all four analysis classes), or it is recomputed per run, which is not what users
  look at. "Review" also suggests a finished systematic review, not a single snowball pass.

**C — `Project` is the `Review` as the analysis scope; each run is a separate activity with its
own protocol.** The project keeps the analysis role: inclusions, clusters and gaps stay
project-scoped as they are today. A new run class (a `prov:Activity`, for example
`ptl:ResearchRun`) carries the seed, the protocol and the spend, and links to its project.
`INCLUDES` gains the id of the run that first included the work. Screening exclusions can be
recorded per run when CQ13 is needed.

- *For:* it keeps both of today's levels and keeps each where it already lives. Per-run
  provenance and per-run budgets fit without moving the analysis layer, and existing projects
  only need an optional property added to existing edges.
- *Against:* it changes the ontology: `ptl:Review` loses its seed and protocol to the run class,
  and `Review` and `Project` become two names for one thing unless one of them is retired. ADR-0004's
  "per review" ceiling needs restating as either per run or per project.

## Recommendation

**Option C**, with `Project` as the single user-facing name (the ontology's `ptl:Review`
renamed or aliased to it), a per-run class that owns the protocol, and the budget ceiling read
as **per run** because a run is the unit that starts, spends and can be resumed. This is the
recommendation only. The user has not decided.

## Consequences (if C is accepted)

- Ontology v0.3: move `seedKind`, `seedValue`, `hasProtocol`, `budgetUsd` and `spendUsd` from
  `ptl:Review` to the run class; update the SHACL shapes and `ontology/lpg-binding.md`. This
  follows ADR-0006's rule that the M0 artefacts change only when the v1 schema replaces them.
- Graph: `include_work` sets a `run_id` on `INCLUDES` when the edge is created. Older edges keep
  no run id, so run provenance starts from the first run after the change.
- Runs stay as JSON files in `data/runs/`. Moving them into the graph is a separate decision.
- ADR-0004 gets an amendment line: the $10 ceiling applies per run. A per-project total cap,
  and the spend ledger that both need, are follow-up work.
