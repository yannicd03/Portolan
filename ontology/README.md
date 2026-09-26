# `ptl:` vocabulary contract (v0.2)

> **Status (2026-09-26):** this contract describes the M0 graph-store spike. The product now
> uses Neo4j (ADR-0005) with a much smaller v1 graph — works, citations, authors and merged
> keyword concepts (ADR-0006). The contract below stays until the v1 schema replaces it.

Authoritative in-repo digest of the conceptual model defined in the design note
`$PORTOLAN_NOTES_DIR/ontology.md`. **Every artifact in this repo — `portolan.ttl`,
`shapes.ttl`, the SPARQL and Cypher competency questions, the Pydantic models, and both store
backends — must use exactly the names below.** If a name is missing here, add it here first.

Store neutrality is a hard requirement: the graph store (RDF/Oxigraph vs Neo4j) is an open
decision settled by the M0 spike. Each construct therefore has both an RDF and an LPG binding.

## Namespaces

| Prefix | IRI | Use |
|---|---|---|
| `ptl:` | `https://w3id.org/portolan/ontology#` | Classes and properties |
| `ptlr:` | `https://w3id.org/portolan/id/` | Instances |
| `ptlg:` | `https://w3id.org/portolan/graph/` | Named graphs |
| `cito:` | `http://purl.org/spar/cito/` | Citation functions |
| `fabio:` | `http://purl.org/spar/fabio/` | Publication types |
| `skos:` | `http://www.w3.org/2004/02/skos/core#` | Concept layer |
| `prov:` | `http://www.w3.org/ns/prov#` | Provenance |
| `dcterms:` | `http://purl.org/dc/terms/` | Title, abstract, issued |
| `foaf:` | `http://xmlns.com/foaf/0.1/` | Person, Organization |

The `w3id.org` path is **not registered yet** — it is a placeholder chosen so the IRIs are stable
and redirectable later. Do not publish the vocabulary under it without registering first.

## Instance IRI minting

`ptlr:{type}/{scheme}/{id}` with the id percent-encoded:

| Kind | Pattern | Example |
|---|---|---|
| Work | `ptlr:work/{doi\|arxiv\|openalex\|s2\|url}/{id}` | `ptlr:work/arxiv/1706.03762` |
| Author | `ptlr:author/{orcid\|openalex\|s2}/{id}` | `ptlr:author/openalex/A5062577330` |
| Organization | `ptlr:org/{ror\|domain}/{id}` | `ptlr:org/ror/00njsd438` |
| Venue | `ptlr:venue/{openalex\|issn\|slug}/{id}` | `ptlr:venue/slug/neurips` |
| Citation (reified) | `ptlr:citation/{sha1(citing+cited)}` | |
| Concept | `ptlr:concept/{problem\|method\|dataset\|metric}/{slug}` | `ptlr:concept/method/speculative-decoding` |
| Statement | `ptlr:{contribution\|result\|claim\|limitation\|futurework}/{workSlug}/{n}` | |
| Evidence | `ptlr:evidence/{sha1(quote+workIri)}` | |
| Review layer | `ptlr:review/{id}`, `ptlr:protocol/{id}`, `ptlr:inclusion/{reviewId}/{workSlug}`, `ptlr:cluster/{reviewId}/{n}`, `ptlr:gap/{reviewId}/{n}` | |

Preferred identifier order when several exist: DOI, arXiv, OpenAlex, S2, URL.

## Named graphs / partitions

| Layer | RDF named graph | LPG binding |
|---|---|---|
| 1 Bibliographic (global) | `ptlg:biblio` | no partition property |
| 2 Content (global, per extraction run) | `ptlg:content/{runId}` | `extractionRun` property on every content node |
| 3 Analysis (review-scoped) | `ptlg:review/{reviewId}` | `reviewId` property on every analysis node |
| 4 Provenance | alongside the asserting graph | properties on the node |

**Rule:** anything relative to a corpus (PageRank, cluster membership, frontier score) lives on a
review-scoped node, never on the `Work`.

## Layer 1 — bibliographic

| Class | Aligns to | LPG label |
|---|---|---|
| `ptl:Work` | `fabio:Work` | `:Work` |
| `ptl:Author` | `foaf:Person` | `:Author` |
| `ptl:Organization` | `foaf:Organization` | `:Organization` |
| `ptl:Venue` | `fabio:Journal` / conference series | `:Venue` |
| `ptl:Citation` | `cito:Citation` | reified in RDF; **collapses into the `:CITES` relationship** in LPG |

`ptl:workType` ∈ `journalArticle`, `conferencePaper`, `preprint`, `technicalReport`, `blogPost`,
`thesis`, `bookChapter` (a property, not a subclass hierarchy, so the LPG binding stays flat).
`ptl:isSurvey` is a boolean, never a type.

`Work` properties: `dcterms:title` (1), `dcterms:abstract` (0..1), `dcterms:issued` (1, xsd:date or
gYear), `ptl:doi`, `ptl:arxivId`, `ptl:openAlexId`, `ptl:s2Id`, `ptl:url`, `ptl:sourceTier` (1),
`ptl:oaStatus`, `ptl:fullTextAvailable` (xsd:boolean), `ptl:globalCitationCount` (xsd:integer),
`ptl:asOf` (xsd:date), `ptl:tldr`, `ptl:sourceApi`, `ptl:retrievedAt` (xsd:dateTime).

Relations: `ptl:authoredBy` (Work→Author, ordered via `ptl:authorPosition` on an
`ptl:Authorship` node — **not modelled in v0.1**, author order is a `ptl:authorPosition` integer on
a `ptlr:authorship/...` node only if the spike shows it is needed; until then authors are unordered),
`ptl:affiliatedWith` (Author→Organization), `ptl:publishedIn` (Work→Venue),
`ptl:publishedBy` (Work→Organization), `ptl:hasVersion` (Work→Work),
`ptl:isCanonicalVersion` (xsd:boolean).

**Citations.** The plain shortcut is always materialized alongside the reified node:

```turtle
ptlr:work/arxiv/2211.17192 cito:cites ptlr:work/arxiv/1706.03762 .

ptlr:citation/ab12… a ptl:Citation ;
    ptl:citingWork ptlr:work/arxiv/2211.17192 ;
    ptl:citedWork  ptlr:work/arxiv/1706.03762 ;
    ptl:citationFunction cito:usesMethodIn ;
    ptl:isInfluential true ;
    ptl:citationContext "We build on the decoding scheme of [12] …" .
```

Reduced CiTO subset — the **only** allowed values of `ptl:citationFunction`:
`cito:obtainsBackgroundFrom`, `cito:usesMethodIn`, `cito:usesDataFrom`, `cito:extends`,
`cito:critiques`, `cito:disputes`, `cito:supports`, `cito:reviews`.
*Open M0 item: the exact IRIs must be checked against the published CiTO ontology before release.*

## Layer 2 — content

Concept classes, all `rdfs:subClassOf skos:Concept`, all canonicalized (one node per real-world
concept, surface forms as `skos:altLabel`): `ptl:Problem`, `ptl:Method`, `ptl:Dataset`, `ptl:Metric`.
Concept relations: `skos:broader`/`skos:narrower`, `ptl:extendsMethod` (Method→Method),
`ptl:firstSeenIn` (Concept→Work, derived), `skos:exactMatch`/`skos:closeMatch` (external vocabularies).

> Naming note: the concept-level relation is `ptl:extendsMethod`, distinct from the citation
> function `cito:extends`. Do not conflate them.

Statement classes, each attached to exactly one Work and each requiring ≥1 Evidence:

| Class | Key properties |
|---|---|
| `ptl:Contribution` | `ptl:ofWork` (1), `ptl:addresses`→Problem, `ptl:proposes`/`ptl:uses`→Method, `ptl:evaluatesOn`→Dataset, `ptl:reports`→Result, `ptl:contributionKind` (1) |
| `ptl:Result` | `ptl:ofWork` (1), `ptl:ofMethod` (1), `ptl:onDataset` (1), `ptl:withMetric` (1), `ptl:value` (1, xsd:decimal), `ptl:unit`, `ptl:setting`, `ptl:claimsSOTA` (xsd:boolean) |
| `ptl:Claim` | `ptl:ofWork` (1), `ptl:claimText` (1), `ptl:about`→Concept, `ptl:supportsClaim`/`ptl:contradictsClaim`→Claim (derived) |
| `ptl:Limitation` | `ptl:ofWork` (1), `ptl:limitationText` (1), `ptl:about`, `ptl:limitationOf`→Method/Dataset/Work |
| `ptl:FutureWork` | `ptl:ofWork` (1), `ptl:futureWorkText` (1), `ptl:about` |

`ptl:contributionKind` ∈ `method`, `dataset`, `benchmark`, `analysis`, `theory`, `system`,
`survey`, `position`.

Simple edges (e.g. *Contribution uses Method*) are **not** individually reified; their evidence
hangs on the owning statement node. No RDF-star anywhere.

## Layer 3 — analysis (review-scoped)

| Class | Key properties |
|---|---|
| `ptl:Review` | `ptl:seedKind` (1), `ptl:seedValue` (1), `ptl:hasProtocol` (1), `ptl:startedAt`, `ptl:endedAt`, `ptl:budgetWorks`, `ptl:budgetUsd`, `ptl:spendUsd` |
| `ptl:ReviewProtocol` | `ptl:scopeStatement`, `ptl:inclusionCriterion` (n), `ptl:exclusionCriterion` (n), `ptl:fromYear`, `ptl:toYear`, `ptl:allowedTier` (n), `ptl:maxWorks`, `ptl:maxSnowballDepth` |
| `ptl:Inclusion` | `ptl:ofReview` (1), `ptl:ofWork` (1), `ptl:decision` (1), `ptl:stage` (1), `ptl:reason`, `ptl:discoveredVia`, `ptl:relevance` (xsd:decimal 0..1), `ptl:pageRank`, `ptl:betweenness`, `ptl:onMainPath` (xsd:boolean), `ptl:citationVelocity`, `ptl:frontierScore`, `ptl:frontierComponent…`, `ptl:role` (n), `ptl:inCluster` |
| `ptl:Cluster` | `ptl:ofReview` (1), `ptl:level`, `ptl:parentCluster`, `rdfs:label`, `ptl:summary`, `ptl:growthRate`, `ptl:topConcept` |
| `ptl:GapHypothesis` | `ptl:ofReview` (1), `ptl:gapType` (1), `ptl:statement` (1), `ptl:about`, `ptl:supportedBy`, `ptl:confidence` (1, 0..1), `ptl:verificationOutcome` (1), `ptl:userStatus` (1), `ptl:userNote` |

Enumerations (closed — SHACL `sh:in`, LPG write-boundary validation):

| Property | Values |
|---|---|
| `ptl:sourceTier` | `peerReviewed`, `preprint`, `officialBlog` |
| `ptl:orgKind` | `academic`, `industry`, `nonprofit`, `government` |
| `ptl:venueKind` | `journal`, `conference`, `workshop`, `repository`, `blog` |
| `ptl:seedKind` | `paper`, `topic`, `prompt` |
| `ptl:decision` | `included`, `excluded` |
| `ptl:stage` | `search`, `screening`, `fulltext` |
| `ptl:discoveredVia` | `seed`, `search`, `backward`, `forward`, `cocitation`, `coupling`, `semantic`, `orkg` |
| `ptl:role` | `foundational`, `backbone`, `bridge`, `survey`, `rising` |
| `ptl:gapType` | `matrixVoid`, `statedUnaddressed`, `contradiction`, `evaluationGap`, `bridgingGap`, `stagnation` |
| `ptl:verificationOutcome` | `notChecked`, `noCounterEvidence`, `partiallyAddressed`, `refuted` |
| `ptl:userStatus` | `proposed`, `accepted`, `rejected` |
| `ptl:contributionKind` | `method`, `dataset`, `benchmark`, `analysis`, `theory`, `system`, `survey`, `position` |
| `ptl:fromSourceKind` | `abstract`, `fullText`, `blogHtml` |

## Layer 4 — provenance

`ptl:Evidence`: `ptl:quote` (1, verbatim, string-verified), `ptl:locator`, `ptl:fromWork` (1),
`ptl:fromSourceKind` (1). Statements link via `ptl:hasEvidence`.

Each extraction run is a `prov:Activity` with `ptl:model`, `ptl:promptVersion`,
`ptl:ontologyVersion`, `prov:endedAtTime`; content entities carry `prov:wasGeneratedBy`.

## SHACL shapes (first set — all must be implemented)

1. Every `Work` has a title, an issued date, a `sourceTier`, and ≥1 identifier or URL.
2. Every `Work` with `ptl:workType blogPost` has `ptl:publishedBy` an `Organization` whose domain is on the allowlist.
3. Every `Contribution`, `Result`, `Claim`, `Limitation`, `FutureWork` has ≥1 `Evidence` whose `ptl:fromWork` equals the statement's own `ptl:ofWork`.
4. Every `Result` has method, dataset, metric, and value.
5. Every `Inclusion` has exactly one Work, one Review, one decision; `excluded` ones have a reason.
6. Every `GapHypothesis` has a type, a statement, ≥1 `ptl:supportedBy` or `ptl:about`, a confidence in [0,1], and a user status.
7. Every enumerated property only takes values from its vocabulary above.

## Competency questions

Numbered CQ01–CQ14; file names `competency/sparql/cq01_*.rq` and `competency/cypher/cq01_*.cypher`
must share the number and slug so they can be paired by the spike harness.

| # | Question |
|---|---|
| CQ01 | Which works are foundational / bridge / backbone / rising in review R, per cluster? |
| CQ02 | What is the main path from the seed's ancestors to the newest works? |
| CQ03 | Which methods address problem P, and on which datasets and metrics were they evaluated? |
| CQ04 | What is the best reported result per dataset and metric, by which method, from which source tier? |
| CQ05 | Which problem × method pairs have no contribution while both the row and the column have at least *n*? |
| CQ06 | Which limitations are stated by ≥3 works and addressed by no later work in the corpus? |
| CQ07 | Which claims are contradicted, and by whom? |
| CQ08 | Which methods were never compared on a shared dataset? |
| CQ09 | Which clusters grew fastest in the last 18 months, and which concepts first appeared in them? |
| CQ10 | What share of frontier works is not peer-reviewed, and which organizations publish them? |
| CQ11 | Which cluster pairs are semantically close but citation-disconnected? |
| CQ12 | For any assertion shown in the UI: which quote, from which work, extracted by which model and prompt version, supports it? |
| CQ13 | Why was work W excluded from review R, and at which stage? |
| CQ14 | Which gap hypotheses did the user reject, so a re-run does not resurface them? |

## Open points (carried from the design note, to close during M0)

- Exact CiTO and FaBiO IRIs; `rdfs:subClassOf` vs `skos:closeMatch` for alignment.
- Whether Claim→Claim support/contradiction is worth v1 or starts as `Result`-only contradiction detection.
- Granularity of `Problem` vs `Method` for things that are both ("retrieval-augmented generation").
- Whether to mint concept IRIs from external vocabularies on exact match, or always mint local IRIs and link.

## LPG naming rules (binding for the Neo4j side of the spike)

Mechanical, so the RDF and Cypher artifacts stay comparable:

| RDF construct | LPG form | Example |
|---|---|---|
| Class | PascalCase label | `ptl:GapHypothesis` → `:GapHypothesis` |
| Object property | SCREAMING_SNAKE relationship type | `ptl:authoredBy` → `:AUTHORED_BY`, `ptl:inCluster` → `:IN_CLUSTER` |
| Datatype property | camelCase property key | `ptl:frontierScore` → `frontierScore` |
| `cito:cites` | `:CITES` relationship carrying `citationFunction`, `isInfluential`, `citationContext` | the reified `ptl:Citation` node does **not** exist in the LPG binding |
| `ptl:hasEvidence` | `:HAS_EVIDENCE` → `:Evidence` node | evidence stays a node in both bindings |
| Enumerated value | the bare string, identical spelling to the RDF binding | `"matrixVoid"` |
| Named graph | partition property (`reviewId`, `extractionRun`) | see the table above |

`ptl:citationFunction` values are stored in the LPG as the CiTO local name only
(`usesMethodIn`, not the full IRI), and re-expanded on Turtle export.

## v0.2 amendments (2026-09-22)

Six gaps were found while implementing the two bindings and the 28 competency queries. Each was
reported rather than filled by assumption, and is resolved here. **v0.2 is the binding version;
`portolan.ttl`, `shapes.ttl`, `lpg-binding.md`, the models and the affected queries must match it.**

### A1. `ptl:supportedBy` gets a range

`ptl:supportedBy` (on `ptl:GapHypothesis`) ranges over `ptl:Limitation`, `ptl:FutureWork`,
`ptl:Claim`, `ptl:Result`, `ptl:Work`. SHACL: `sh:or` over those five classes. A gap supported by
nothing else must still carry `ptl:about`, as shape 6 already requires.

### A2. `ptl:frontierComponent…` is replaced by six named properties

The ellipsis in v0.1 was an unfinished thought, not a wildcard. The frontier score's components
are fixed, each `xsd:decimal` in [0,1] on `ptl:Inclusion`, and the UI shows all six:

| Property | Component |
|---|---|
| `ptl:frontierComponentVelocity` | age-normalized citation velocity |
| `ptl:frontierComponentMainPathLeaf` | position at a leaf of the main path |
| `ptl:frontierComponentClusterGrowth` | membership in a fast-growing cluster |
| `ptl:frontierComponentConceptNovelty` | introduces a recently-first-seen concept with growing adoption |
| `ptl:frontierComponentSotaClaim` | SOTA claims on tracked benchmarks |
| `ptl:frontierComponentNotPeerReviewed` | preprint or official-blog tier |

`ptl:frontierScore` remains the composite; it is never shown without its components.

### A3. `ptl:metricDirection` on `ptl:Metric`

CQ04 ("best reported result per dataset and metric") is unanswerable without knowing whether
high or low is better — exact-match accuracy and perplexity disagree. `ptl:metricDirection` ∈
`higherIsBetter`, `lowerIsBetter` (closed enumeration, required on every `ptl:Metric`; SHACL
shape 8). Queries order by value using this property, never by a hard-coded assumption.

### A4. `ptl:addressesLimitation`

CQ06 ("limitations stated by >= 3 works and addressed by no later work") had no edge to negate
over. `ptl:addressesLimitation` (`ptl:Contribution` -> `ptl:Limitation`) is written by the
analysis stage, not by extraction, and like every analysis-layer fact it is review-scoped. It
carries `ptl:hasEvidence` when derived from an extracted statement.

### A5. `ptl:ClusterPair` for cluster-to-cluster measures

CQ11 ("semantically close but citation-disconnected cluster pairs") needs a scalar per *pair*,
which neither a property on `Cluster` nor `skos:closeMatch` can carry. Review-scoped class
`ptl:ClusterPair`: `ptl:ofReview` (1), `ptl:clusterA` (1), `ptl:clusterB` (1),
`ptl:semanticSimilarity` (1, xsd:decimal 0..1), `ptl:crossCitationCount` (1, xsd:integer).
Pairs are unordered — write each pair once with `ptl:clusterA` the lexicographically smaller
IRI, so the shape can enforce uniqueness. LPG binding: a `:CLUSTER_PAIR` relationship carrying
both properties, since a property graph needs no intermediate node here. This is the third
place where the LPG binding is simpler than RDF; record it in `lpg-binding.md`.

### A6. ExtractionRun IRI pattern

Missing from the minting table. Ratified as used by the store layer: `ptlr:extraction/{runId}`,
with the run's named graph `ptlg:content/{runId}` and LPG partition property `extractionRun`.

### A7. Minor clarifications

- `ptl:seedValue` is `xsd:string` in every seed mode; a paper seed stores the identifier as given
  and the resolved `ptl:Work` is linked separately.
- Venue alignment: `ptl:Venue` is `skos:closeMatch fabio:Journal` **only** when `ptl:venueKind`
  is `journal`. Conference series, workshops, repositories and blogs get no FaBiO alignment
  rather than a wrong one.
- Turtle export from the LPG binding cannot preserve named-graph boundaries: `reviewId` and
  `extractionRun` survive as properties, not as graph names. Plain Turtle is therefore a lossy
  export from Neo4j and TriG is the faithful one. This is spike evidence, recorded in ADR-0005.

### A8. Three residual points, closed (2026-09-22)

Reported during the v0.2 propagation pass rather than guessed at. Closing them here so the spike
measures the real model and not a placeholder.

- **`ptl:ClusterPair` IRI pattern:** `ptlr:clusterpair/{reviewId}/{sha1(clusterA + clusterB)}`,
  with the two cluster IRIs concatenated in the stored order (clusterA lexicographically smaller),
  so the IRI is a pure function of the pair and re-running analysis is idempotent.
- **Paper-seed to Work:** `ptl:seedWork` (`ptl:Review` -> `ptl:Work`, 0..1), written once the seed
  identifier resolves. `ptl:seedValue` keeps the identifier exactly as the user typed it; the two
  are never conflated, so a seed that fails to resolve is still visible in the run view.
- **CQ11's "low" cross-citation threshold** is a query parameter, not a constant. Defaults:
  `semanticSimilarity >= 0.60` and `crossCitationCount <= 2`. Zero is the wrong default — a single
  stray citation between two otherwise disconnected clusters does not make a bridging gap
  spurious, and Swanson-style undiscovered public knowledge tolerates a thin link. Both defaults
  are recorded in the query headers and revisited once the frontier/gap backtest (M6) can score them.
