# `ptl:` property-graph binding (Neo4j 5.x)

This is the mechanical LPG binding of the vocabulary contract in
[`README.md`](README.md). It is deliberately boring: RDF local names become
camelCase property keys, RDF object properties become SCREAMING_SNAKE relationship
types, and RDF classes become PascalCase labels. `iri` is the one binding-level
property added to every persisted node. It is the exact RDF IRI and is the identity
key used by the Neo4j constraints.

The tables below describe the v0.2 binding. A concept node has the common `:Concept`
label and one or more of `:Problem`, `:Method`, `:Dataset`, and `:Metric` when its
RDF type is specific. `:Activity` is the LPG label for `prov:Activity`, used for an
extraction run. There is intentionally no `:Citation` or `:Authorship` node:
`ptl:Citation` is collapsed into `:CITES`, and the optional `ptl:Authorship` node is
explicitly out of scope in this binding.

## Scalar and partition conventions

| RDF value shape | LPG representation |
|---|---|
| IRI | `string` in `iri`, or a relationship endpoint |
| `xsd:string` / plain literal | `string` |
| `xsd:boolean` | `boolean` |
| `xsd:integer` | `integer` |
| `xsd:decimal` | Neo4j `FLOAT` (decimal-valued number) |
| `xsd:date` | Neo4j `DATE` when it is a complete date; the `issued` key may also be a four-digit `gYear` string because the contract permits both |
| `xsd:dateTime` | Neo4j `DATETIME` |
| RDF multi-valued property | `string[]` unless stated otherwise |
| closed enumeration | a bare string with the exact spelling in the contract |

`reviewId` is the bare review id (for example, `kgqa-rag`), not a generated database
id; the owning `:Review.iri` remains the full review IRI. `extractionRun` is the
full extraction-run IRI (the same value represented by `prov:wasGeneratedBy`). They
are partition keys, not replacements for `iri`.

`seedValue` is always stored as a string, including for paper seeds. The resolved
paper is linked separately through the review's seed relationship in the domain
model. Extraction-run IRIs follow `ptlr:extraction/{runId}`; the same IRI is used
for the `:Activity.iri` identity and the `extractionRun` content partition value.

## Node-label and property-key catalogue

Properties in the `Properties` column include the identity key. “None specified”
means that the contract gives the node no additional scalar property; it does not
permit an implementation to invent one.

### Bibliographic labels

| Label | Properties (key: type) |
|---|---|
| `:Work` | `iri: string`; `title: string`; `abstract: string` (optional); `issued: DATE or gYear string`; `workType: string` (closed vocabulary); `isSurvey: boolean`; `doi: string` (optional); `arxivId: string` (optional); `openAlexId: string` (optional); `s2Id: string` (optional); `url: string` (optional); `sourceTier: string` (closed vocabulary); `oaStatus: string` (optional); `fullTextAvailable: boolean` (optional); `globalCitationCount: integer` (optional); `asOf: DATE` (optional); `tldr: string` (optional); `sourceApi: string` (optional); `retrievedAt: DATETIME` (optional); `isCanonicalVersion: boolean` (optional) |
| `:Author` | `iri: string`; no additional scalar property is specified by the contract |
| `:Organization` | `iri: string`; `orgKind: string` (closed vocabulary); `domain: string` (the domain used by the blog allowlist rule) |
| `:Venue` | `iri: string`; `venueKind: string` (closed vocabulary) |
| `:Activity` | `iri: string`; `model: string`; `promptVersion: string`; `ontologyVersion: string`; `endedAtTime: DATETIME` (optional) |

`iri` on `:Activity` is required by this binding because an extraction run is a
`prov:Activity`. Under A6 the writer mints the stable
`ptlr:extraction/{runId}` IRI before writing the node and uses that same value for
the `extractionRun` content partition.

### Concept labels

| Label | Properties (key: type) |
|---|---|
| `:Concept` | `iri: string`; `altLabel: string[]` (optional); `extractionRun: string` |
| `:Problem` | the inherited `:Concept` properties; no additional properties |
| `:Method` | the inherited `:Concept` properties; no additional properties |
| `:Dataset` | the inherited `:Concept` properties; no additional properties |
| `:Metric` | the inherited `:Concept` properties; `metricDirection: string` (closed vocabulary, required) |

The `extractionRun` partition key applies to all content-layer concept nodes.

### Statement and evidence labels

All five statement labels are content-layer nodes and therefore carry
`extractionRun: string` in addition to the properties below.

| Label | Properties (key: type) |
|---|---|
| `:Contribution` | `iri: string`; `extractionRun: string`; `contributionKind: string` (closed vocabulary) |
| `:Result` | `iri: string`; `extractionRun: string`; `value: FLOAT`; `unit: string` (optional); `setting: string` (optional); `claimsSOTA: boolean` (optional) |
| `:Claim` | `iri: string`; `extractionRun: string`; `claimText: string` |
| `:Limitation` | `iri: string`; `extractionRun: string`; `limitationText: string` |
| `:FutureWork` | `iri: string`; `extractionRun: string`; `futureWorkText: string` |
| `:Evidence` | `iri: string`; `extractionRun: string`; `quote: string`; `locator: string` (optional); `fromSourceKind: string` (closed vocabulary) |

`ptl:authorPosition` is not a property on `:AUTHORED_BY` and has no LPG binding:
the contract's optional `ptl:Authorship` node is not modelled until the spike
proves author order is needed.

### Review-scoped labels

Every label in this section carries `reviewId: string` in addition to the listed
properties. This is the LPG substitute for the RDF review named graph.

| Label | Properties (key: type) |
|---|---|
| `:Review` | `iri: string`; `reviewId: string`; `seedKind: string` (closed vocabulary); `seedValue: string`; `startedAt: DATETIME` (optional); `endedAt: DATETIME` (optional); `budgetWorks: integer` (optional); `budgetUsd: FLOAT` (optional); `spendUsd: FLOAT` (optional) |
| `:ReviewProtocol` | `iri: string`; `reviewId: string`; `scopeStatement: string` (optional); `inclusionCriterion: string[]` (optional); `exclusionCriterion: string[]` (optional); `fromYear: integer` (optional); `toYear: integer` (optional); `allowedTier: string[]` (optional); `maxWorks: integer` (optional); `maxSnowballDepth: integer` (optional) |
| `:Inclusion` | `iri: string`; `reviewId: string`; `decision: string` (closed vocabulary); `stage: string` (closed vocabulary); `reason: string` (optional); `discoveredVia: string` (closed vocabulary); `relevance: FLOAT` (optional, 0..1); `pageRank: FLOAT` (optional); `betweenness: FLOAT` (optional); `onMainPath: boolean` (optional); `citationVelocity: FLOAT` (optional); `frontierScore: FLOAT` (optional); `frontierComponentVelocity: FLOAT` (optional, 0..1); `frontierComponentMainPathLeaf: FLOAT` (optional, 0..1); `frontierComponentClusterGrowth: FLOAT` (optional, 0..1); `frontierComponentConceptNovelty: FLOAT` (optional, 0..1); `frontierComponentSotaClaim: FLOAT` (optional, 0..1); `frontierComponentNotPeerReviewed: FLOAT` (optional, 0..1); `role: string[]` (optional, closed vocabulary) |
| `:Cluster` | `iri: string`; `reviewId: string`; `level: integer` (optional); `label: string` (optional, from `rdfs:label`); `summary: string` (optional); `growthRate: FLOAT` (optional); `parentCluster` is a relationship, not a property; `topConcept` is a relationship, not a property |
| `:GapHypothesis` | `iri: string`; `reviewId: string`; `gapType: string` (closed vocabulary); `statement: string`; `confidence: FLOAT` (0..1); `verificationOutcome: string` (closed vocabulary); `userStatus: string` (closed vocabulary); `userNote: string` (optional); `about` and `supportedBy` are relationships |

When an `Inclusion` has a `frontierScore`, the write boundary requires all six
named frontier components to be present and in the inclusive range 0..1. There is
no wildcard `frontierComponent` property in v0.2.

`ptl:ClusterPair` is not a node label in LPG. Its review-scoped RDF resource is
represented by the `:CLUSTER_PAIR` relationship in the relationship catalogue;
the relationship carries the pair measures and its `reviewId` partition key.

The `:Review` node is included in the review partition itself (`reviewId` equals
the bare id parsed from its own review IRI). `:ReviewProtocol`, `:Inclusion`,
`:Cluster`, and `:GapHypothesis` use the same value. Bibliographic `:Work` nodes
remain global; review membership is an `:Inclusion` node rather than a `reviewId`
on `:Work`.

## Relationship-type catalogue

Relationship properties are listed separately from node properties. Unless a row
says otherwise, a relationship has no properties. A union in the start or end
column is intentional: it is the same RDF predicate used by several classes.

| Type | Start label | End label | Properties |
|---|---|---|---|
| `:AUTHORED_BY` | `Work` | `Author` | none |
| `:AFFILIATED_WITH` | `Author` | `Organization` | none |
| `:PUBLISHED_IN` | `Work` | `Venue` | none |
| `:PUBLISHED_BY` | `Work` | `Organization` | none |
| `:HAS_VERSION` | `Work` | `Work` | none |
| `:CITES` | `Work` | `Work` | `citationFunction: string` (CiTO local name); `isInfluential: boolean` (optional); `citationContext: string` (optional) |
| `:BROADER` | `Concept` | `Concept` | none |
| `:NARROWER` | `Concept` | `Concept` | none |
| `:EXTENDS_METHOD` | `Method` | `Method` | none |
| `:FIRST_SEEN_IN` | `Concept` | `Work` | none |
| `:EXACT_MATCH` | `Concept` | `Concept` | none |
| `:CLOSE_MATCH` | `Concept` | `Concept` | none |
| `:OF_WORK` | `Contribution`, `Result`, `Claim`, `Limitation`, `FutureWork`, `Inclusion` | `Work` | none |
| `:ADDRESSES` | `Contribution` | `Problem` | none |
| `:PROPOSES` | `Contribution` | `Method` | none |
| `:USES` | `Contribution` | `Method` | none |
| `:EVALUATES_ON` | `Contribution` | `Dataset` | none |
| `:REPORTS` | `Contribution` | `Result` | none |
| `:ADDRESSES_LIMITATION` | `Contribution` | `Limitation` | `reviewId: string` (analysis/review partition) |
| `:OF_METHOD` | `Result` | `Method` | none |
| `:ON_DATASET` | `Result` | `Dataset` | none |
| `:WITH_METRIC` | `Result` | `Metric` | none |
| `:ABOUT` | `Claim`, `Limitation`, `FutureWork`, `GapHypothesis` | `Concept` | none |
| `:SUPPORTS_CLAIM` | `Claim` | `Claim` | none |
| `:CONTRADICTS_CLAIM` | `Claim` | `Claim` | none |
| `:LIMITATION_OF` | `Limitation` | `Method`, `Dataset`, `Work` | none |
| `:HAS_EVIDENCE` | `Contribution`, `Result`, `Claim`, `Limitation`, `FutureWork` | `Evidence` | none |
| `:FROM_WORK` | `Evidence` | `Work` | none |
| `:HAS_PROTOCOL` | `Review` | `ReviewProtocol` | none |
| `:OF_REVIEW` | `Inclusion`, `Cluster`, `GapHypothesis` | `Review` | none |
| `:IN_CLUSTER` | `Inclusion` | `Cluster` | none |
| `:PARENT_CLUSTER` | `Cluster` | `Cluster` | none |
| `:TOP_CONCEPT` | `Cluster` | `Concept` | none |
| `:CLUSTER_PAIR` | `Cluster` | `Cluster` | `reviewId: string`; `semanticSimilarity: FLOAT` (0..1); `crossCitationCount: integer`; the relationship is written once from the lexicographically smaller cluster IRI to the larger one |
| `:SUPPORTED_BY` | `GapHypothesis` | `Limitation`, `FutureWork`, `Claim`, `Result`, or `Work` | none |
| `:WAS_GENERATED_BY` | `Concept`, `Contribution`, `Result`, `Claim`, `Limitation`, `FutureWork`, `Evidence` | `Activity` | none |

`SUPPORTED_BY` has the closed v0.2 target range shown above. Neo4j cannot enforce
that relationship target union with a native Community constraint, so the writer
must validate it at the write boundary. `ADDRESSES_LIMITATION` is an analysis
edge: its `reviewId` keeps the review-scoped fact separate, and extracted support
is carried by the owning `Contribution`'s `:HAS_EVIDENCE` edges. `CLUSTER_PAIR`
is likewise review-scoped; endpoint ordering and one-pair-per-review uniqueness
are write-boundary invariants.

## Closed enumerations and write-boundary validation

Neo4j stores the bare strings. The following keys are checked against the exact
value sets in the contract before a write: `workType`, `sourceTier`, `orgKind`, `venueKind`,
`seedKind`, `decision`, `stage`, `discoveredVia`, `role`, `gapType`,
`verificationOutcome`, `userStatus`, `contributionKind`, `fromSourceKind`,
`metricDirection`, and
the `CITES.citationFunction` relationship property.
The allowed values are respectively:

| Key | Allowed values |
|---|---|
| `workType` | `journalArticle`, `conferencePaper`, `preprint`, `technicalReport`, `blogPost`, `thesis`, `bookChapter` |
| `sourceTier` | `peerReviewed`, `preprint`, `officialBlog` |
| `orgKind` | `academic`, `industry`, `nonprofit`, `government` |
| `venueKind` | `journal`, `conference`, `workshop`, `repository`, `blog` |
| `seedKind` | `paper`, `topic`, `prompt` |
| `decision` | `included`, `excluded` |
| `stage` | `search`, `screening`, `fulltext` |
| `discoveredVia` | `seed`, `search`, `backward`, `forward`, `cocitation`, `coupling`, `semantic`, `orkg` |
| `role` | `foundational`, `backbone`, `bridge`, `survey`, `rising` |
| `gapType` | `matrixVoid`, `statedUnaddressed`, `contradiction`, `evaluationGap`, `bridgingGap`, `stagnation` |
| `verificationOutcome` | `notChecked`, `noCounterEvidence`, `partiallyAddressed`, `refuted` |
| `userStatus` | `proposed`, `accepted`, `rejected` |
| `contributionKind` | `method`, `dataset`, `benchmark`, `analysis`, `theory`, `system`, `survey`, `position` |
| `fromSourceKind` | `abstract`, `fullText`, `blogHtml` |
| `metricDirection` | `higherIsBetter`, `lowerIsBetter` |
| `CITES.citationFunction` | `obtainsBackgroundFrom`, `usesMethodIn`, `usesDataFrom`, `extends`, `critiques`, `disputes`, `supports`, `reviews` |

## Where the LPG is simpler

1. The reified `ptl:Citation` is one `:CITES` relationship. Its
   `citationFunction`, `isInfluential`, and `citationContext` sit directly on the
   relationship, so the common “who cites whom?” traversal is one hop instead of
   two hops through a reified citation node. The citation IRI is not retained in
   Neo4j; export must deterministically mint it again.
2. Evidence for a statement-owned simple edge is reached by one
   `(:Statement)-[:HAS_EVIDENCE]->(:Evidence)` traversal. The binding has no
   RDF-star edge annotations and no per-edge blank/reification node for
   “Contribution uses Method”; the owning statement carries the evidence link.
3. A review-scoped `ptl:ClusterPair` is one `:CLUSTER_PAIR` relationship with
   scalar similarity and cross-citation properties. The endpoint ordering makes
   the unordered RDF pair deterministic without an intermediate LPG node.

## Where the LPG is worse

Neo4j Community Edition has no named graphs. Review isolation is therefore a
manually maintained `reviewId` property on every analysis node, and content
isolation is an `extractionRun` property on every content node. Every query and
writer must remember those predicates; the database cannot provide RDF graph
scope for free. Provenance is also hand-modelled as `:Activity` plus
`:WAS_GENERATED_BY` and duplicated partition keys, rather than being a native
graph/context facility. Relationship cardinalities, conditional rules, and the
closed enumerations are application checks; the schema file documents the
Enterprise-only property-existence fragments but Community still needs the same
write-boundary validation. That includes the `ADDRESSES_LIMITATION` and
`CLUSTER_PAIR` relationship ranges/partitions, frontier-component range and
co-presence, and the metric-direction enum. This is the main operational
friction of the LPG choice.

## Turtle export mapping

Export is a required operation, not a best-effort debug dump. The following
mapping is normative for reconstructing the RDF view.

### Labels and RDF types

| LPG label | Turtle construct |
|---|---|
| `:Work` | `iri a ptl:Work, fabio:Work` |
| `:Author` | `iri a ptl:Author, foaf:Person` |
| `:Organization` | `iri a ptl:Organization, foaf:Organization` |
| `:Venue` | `iri a ptl:Venue`; emit `skos:closeMatch fabio:Journal` only when `venueKind` is `journal`; do not emit a FaBiO alignment for conference series, workshops, repositories, or blogs |
| `:Concept` | `iri a skos:Concept` when no more-specific concept label is present; the shared LPG label is query metadata when combined with `:Problem`, `:Method`, `:Dataset`, or `:Metric` |
| `:Problem` | `iri a ptl:Problem` |
| `:Method` | `iri a ptl:Method` |
| `:Dataset` | `iri a ptl:Dataset` |
| `:Metric` | `iri a ptl:Metric` |
| `:Contribution` | `iri a ptl:Contribution` |
| `:Result` | `iri a ptl:Result` |
| `:Claim` | `iri a ptl:Claim` |
| `:Limitation` | `iri a ptl:Limitation` |
| `:FutureWork` | `iri a ptl:FutureWork` |
| `:Evidence` | `iri a ptl:Evidence, prov:Entity` |
| `:Review` | `iri a ptl:Review` |
| `:ReviewProtocol` | `iri a ptl:ReviewProtocol` |
| `:Inclusion` | `iri a ptl:Inclusion` |
| `:Cluster` | `iri a ptl:Cluster` |
| `:GapHypothesis` | `iri a ptl:GapHypothesis` |
| `:Activity` | `iri a ptl:ExtractionRun, prov:Activity` |

### Relationship types

For all rows below, the relationship endpoints' `iri` values are the Turtle
subjects and objects, and the SCREAMING_SNAKE name is converted back to the RDF
local name shown.

| LPG relationship | Turtle export |
|---|---|
| `AUTHORED_BY`, `AFFILIATED_WITH`, `PUBLISHED_IN`, `PUBLISHED_BY`, `HAS_VERSION` | `ptl:authoredBy`, `ptl:affiliatedWith`, `ptl:publishedIn`, `ptl:publishedBy`, `ptl:hasVersion` |
| `BROADER`, `NARROWER`, `EXTENDS_METHOD`, `FIRST_SEEN_IN`, `EXACT_MATCH`, `CLOSE_MATCH` | `skos:broader`, `skos:narrower`, `ptl:extendsMethod`, `ptl:firstSeenIn`, `skos:exactMatch`, `skos:closeMatch` |
| `OF_WORK`, `ADDRESSES`, `PROPOSES`, `USES`, `EVALUATES_ON`, `REPORTS`, `OF_METHOD`, `ON_DATASET`, `WITH_METRIC` | `ptl:ofWork`, `ptl:addresses`, `ptl:proposes`, `ptl:uses`, `ptl:evaluatesOn`, `ptl:reports`, `ptl:ofMethod`, `ptl:onDataset`, `ptl:withMetric` |
| `ABOUT`, `SUPPORTS_CLAIM`, `CONTRADICTS_CLAIM`, `LIMITATION_OF`, `HAS_EVIDENCE`, `FROM_WORK` | `ptl:about`, `ptl:supportsClaim`, `ptl:contradictsClaim`, `ptl:limitationOf`, `ptl:hasEvidence`, `ptl:fromWork` |
| `ADDRESSES_LIMITATION` | `ptl:addressesLimitation`; preserve the relationship `reviewId` as partition metadata and validate its target as a `Limitation` |
| `HAS_PROTOCOL`, `OF_REVIEW`, `IN_CLUSTER`, `PARENT_CLUSTER`, `TOP_CONCEPT`, `SUPPORTED_BY` | `ptl:hasProtocol`, `ptl:ofReview`, `ptl:inCluster`, `ptl:parentCluster`, `ptl:topConcept`, `ptl:supportedBy` |
| `CLUSTER_PAIR` | Materialize a deterministic `ptl:ClusterPair` resource from the ordered endpoint IRIs and `reviewId`; emit `ptl:ofReview`, `ptl:clusterA`, `ptl:clusterB`, `ptl:semanticSimilarity`, and `ptl:crossCitationCount` in the review graph |
| `WAS_GENERATED_BY` | `prov:wasGeneratedBy` |
| `CITES` | Emit the direct `citingIri cito:cites citedIri` shortcut and a deterministic `ptlr:citation/{sha1(citingIri+citedIri)}` node of type `ptl:Citation`, with `ptl:citingWork`, `ptl:citedWork`, and the relationship properties expanded to `ptl:citationFunction`, `ptl:isInfluential`, and `ptl:citationContext`. Expand the stored CiTO local name (for example `usesMethodIn`) back to its configured CiTO IRI. |

Scalar keys use the namespace of the contract property: `title`, `abstract`, and
`issued` export as `dcterms:title`, `dcterms:abstract`, and `dcterms:issued`;
`label` as `rdfs:label`; `altLabel` as `skos:altLabel`; `metricDirection` as
`ptl:metricDirection`; `endedAtTime` and
`WAS_GENERATED_BY` as `prov:*`; and the remaining `ptl` keys as `ptl:*`.
The result property is spelled `claimsSOTA` exactly as in `ontology/portolan.ttl`; the
exporter also reads the legacy `claimsSota` key produced by earlier writer versions.
`reviewId` and `extractionRun` are not ontology predicates: they select the
`ptlg:review/{reviewId}` and `ptlg:content/{runId}` named graphs during export.
The graph chosen for provenance is the asserting graph, as required by the
contract.

Because Neo4j has no named graphs, a plain Turtle export cannot preserve those
graph boundaries: `reviewId` and `extractionRun` survive as partition properties,
not as graph names. Plain Turtle is therefore a lossy export from the LPG binding;
TriG is the faithful export format when the named-graph boundaries must survive.
This is the A7 export limitation to carry into the spike evidence.

## Binding decisions and friction to carry into the spike ADR

- Keep the RDF IRI in `iri` even when a Work also has DOI, arXiv, OpenAlex, S2,
  and URL identifiers. The preferred identity order is DOI, arXiv, OpenAlex,
  S2, URL; those source identifiers remain separate properties for filtering and
  export.
- Keep `:CITES` relationship properties even though export has to recreate the
  reified citation node. This preserves the cheap LPG query path while making
  the information-loss boundary explicit.
- Do not put `reviewId` on `Work`: doing so would duplicate a global
  bibliographic resource for every review. `:Inclusion` is the review-scoped
  join and is the only safe place for review-local metrics such as `onMainPath`
  and `frontierScore`.
- v0.2 fixes `supportedBy` to five target classes, replaces the frontier
  wildcard with six named components, and makes metric direction explicit for
  result ranking. Relationship target/range checks and conditional component
  co-presence remain write-boundary validation in Neo4j.
- `addressesLimitation` and `ClusterPair` are review-scoped analysis facts. The
  former is a relationship to a `Limitation`; the latter is a relationship
  carrying `semanticSimilarity`, `crossCitationCount`, and `reviewId`.
- Extraction runs use the stable `ptlr:extraction/{runId}` IRI pattern, and
  venue export applies the FaBiO journal alignment only for journal venues.
