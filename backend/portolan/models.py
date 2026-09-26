"""Pydantic domain models for the four layers of the PTL ontology.

The models intentionally know nothing about a graph store.  They carry the contract's RDF
property name and LPG name as field metadata, while their computed ``iri`` values delegate all
minting to :mod:`portolan.iri`.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from . import iri as iri_helpers


class SourceTier(StrEnum):
    PEER_REVIEWED = "peerReviewed"
    PREPRINT = "preprint"
    OFFICIAL_BLOG = "officialBlog"

    peerReviewed = PEER_REVIEWED
    officialBlog = OFFICIAL_BLOG


class OrgKind(StrEnum):
    ACADEMIC = "academic"
    INDUSTRY = "industry"
    NONPROFIT = "nonprofit"
    GOVERNMENT = "government"


class VenueKind(StrEnum):
    JOURNAL = "journal"
    CONFERENCE = "conference"
    WORKSHOP = "workshop"
    REPOSITORY = "repository"
    BLOG = "blog"


class MetricDirection(StrEnum):
    HIGHER_IS_BETTER = "higherIsBetter"
    LOWER_IS_BETTER = "lowerIsBetter"

    higherIsBetter = HIGHER_IS_BETTER
    lowerIsBetter = LOWER_IS_BETTER


class SeedKind(StrEnum):
    PAPER = "paper"
    TOPIC = "topic"
    PROMPT = "prompt"


class Decision(StrEnum):
    INCLUDED = "included"
    EXCLUDED = "excluded"


class Stage(StrEnum):
    SEARCH = "search"
    SCREENING = "screening"
    FULLTEXT = "fulltext"


class DiscoveredVia(StrEnum):
    SEED = "seed"
    SEARCH = "search"
    BACKWARD = "backward"
    FORWARD = "forward"
    COCITATION = "cocitation"
    COUPLING = "coupling"
    SEMANTIC = "semantic"
    ORKG = "orkg"


class Role(StrEnum):
    FOUNDATIONAL = "foundational"
    BACKBONE = "backbone"
    BRIDGE = "bridge"
    SURVEY = "survey"
    RISING = "rising"


class GapType(StrEnum):
    MATRIX_VOID = "matrixVoid"
    STATED_UNADDRESSED = "statedUnaddressed"
    CONTRADICTION = "contradiction"
    EVALUATION_GAP = "evaluationGap"
    BRIDGING_GAP = "bridgingGap"
    STAGNATION = "stagnation"

    matrixVoid = MATRIX_VOID
    statedUnaddressed = STATED_UNADDRESSED
    evaluationGap = EVALUATION_GAP
    bridgingGap = BRIDGING_GAP


class VerificationOutcome(StrEnum):
    NOT_CHECKED = "notChecked"
    NO_COUNTER_EVIDENCE = "noCounterEvidence"
    PARTIALLY_ADDRESSED = "partiallyAddressed"
    REFUTED = "refuted"

    notChecked = NOT_CHECKED
    noCounterEvidence = NO_COUNTER_EVIDENCE
    partiallyAddressed = PARTIALLY_ADDRESSED


class UserStatus(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ContributionKind(StrEnum):
    METHOD = "method"
    DATASET = "dataset"
    BENCHMARK = "benchmark"
    ANALYSIS = "analysis"
    THEORY = "theory"
    SYSTEM = "system"
    SURVEY = "survey"
    POSITION = "position"


class FromSourceKind(StrEnum):
    ABSTRACT = "abstract"
    FULL_TEXT = "fullText"
    BLOG_HTML = "blogHtml"

    fullText = FULL_TEXT
    blogHtml = BLOG_HTML


class WorkType(StrEnum):
    JOURNAL_ARTICLE = "journalArticle"
    CONFERENCE_PAPER = "conferencePaper"
    PREPRINT = "preprint"
    TECHNICAL_REPORT = "technicalReport"
    BLOG_POST = "blogPost"
    THESIS = "thesis"
    BOOK_CHAPTER = "bookChapter"

    journalArticle = JOURNAL_ARTICLE
    conferencePaper = CONFERENCE_PAPER
    technicalReport = TECHNICAL_REPORT
    blogPost = BLOG_POST
    bookChapter = BOOK_CHAPTER


class CitationFunction(StrEnum):
    OBTAINS_BACKGROUND_FROM = "cito:obtainsBackgroundFrom"
    USES_METHOD_IN = "cito:usesMethodIn"
    USES_DATA_FROM = "cito:usesDataFrom"
    EXTENDS = "cito:extends"
    CRITIQUES = "cito:critiques"
    DISPUTES = "cito:disputes"
    SUPPORTS = "cito:supports"
    REVIEWS = "cito:reviews"

    obtainsBackgroundFrom = OBTAINS_BACKGROUND_FROM
    usesMethodIn = USES_METHOD_IN
    usesDataFrom = USES_DATA_FROM


# Natural aliases used by callers that spell the acronym differently.
CitoFunction = CitationFunction
CiTOFunction = CitationFunction


def _mapped_field(
    rdf: str,
    default: Any = ...,
    *,
    lpg: str | None = None,
    validation_alias: str | AliasChoices | None = None,
    **kwargs: Any,
) -> Any:
    """Create a field carrying both vocabulary and property-graph names."""

    extra = dict(kwargs.pop("json_schema_extra", {}) or {})
    extra.setdefault("rdf", rdf)
    if lpg is not None:
        extra.setdefault("lpg", lpg)
    if validation_alias is not None:
        kwargs["validation_alias"] = validation_alias
    return Field(default, json_schema_extra=extra, **kwargs)


def _as_iri(value: Any) -> str:
    if hasattr(value, "iri"):
        return str(value.iri)
    return str(value)


def _as_list(value: Any) -> Any:
    if value is None or isinstance(value, (list, tuple, set, frozenset)):
        return value
    return [value]


def _iri_equal(left: Any, right: Any) -> bool:
    return _as_iri(left) == _as_iri(right)


class DomainModel(BaseModel):
    """Store-neutral base with strict input and recoverable RDF/LPG mappings."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        validate_assignment=True,
        use_enum_values=False,
    )

    @computed_field(return_type=str)
    @property
    def iri(self) -> str:
        return self._mint_iri()

    def _mint_iri(self) -> str:
        raise NotImplementedError(f"{type(self).__name__} does not define an instance IRI")

    @classmethod
    def property_map(cls) -> dict[str, dict[str, str]]:
        """Return field metadata used by RDF and LPG serializers."""

        result: dict[str, dict[str, str]] = {}
        for base in reversed(cls.__mro__):
            for field_name, field in getattr(base, "model_fields", {}).items():
                extra = field.json_schema_extra or {}
                mapping = {
                    key: str(value)
                    for key, value in extra.items()
                    if key in {"rdf", "rdf_iri", "lpg"} and value is not None
                }
                if mapping:
                    result[field_name] = mapping
        return result

    @classmethod
    def rdf_property_map(cls) -> dict[str, dict[str, str]]:
        return cls.property_map()


class Work(DomainModel):
    """Layer 1 bibliographic Work."""

    title: str = _mapped_field("dcterms:title", min_length=1, lpg="title")
    abstract: str | None = _mapped_field("dcterms:abstract", None, lpg="abstract")
    issued: date | str = _mapped_field("dcterms:issued", lpg="issued")
    doi: str | None = _mapped_field("ptl:doi", None, lpg="doi")
    arxiv_id: str | None = _mapped_field("ptl:arxivId", None, lpg="arxivId")
    openalex_id: str | None = _mapped_field("ptl:openAlexId", None, lpg="openAlexId")
    s2_id: str | None = _mapped_field("ptl:s2Id", None, lpg="s2Id")
    url: str | None = _mapped_field("ptl:url", None, lpg="url")
    source_tier: SourceTier = _mapped_field("ptl:sourceTier", lpg="sourceTier")
    oa_status: str | None = _mapped_field("ptl:oaStatus", None, lpg="oaStatus")
    full_text_available: bool | None = _mapped_field(
        "ptl:fullTextAvailable", None, lpg="fullTextAvailable"
    )
    global_citation_count: int | None = _mapped_field(
        "ptl:globalCitationCount", None, lpg="globalCitationCount"
    )
    as_of: date | None = _mapped_field("ptl:asOf", None, lpg="asOf")
    tldr: str | None = _mapped_field("ptl:tldr", None, lpg="tldr")
    source_api: str | None = _mapped_field("ptl:sourceApi", None, lpg="sourceApi")
    retrieved_at: datetime | None = _mapped_field("ptl:retrievedAt", None, lpg="retrievedAt")
    work_type: WorkType | None = _mapped_field("ptl:workType", None, lpg="workType")
    is_survey: bool | None = _mapped_field("ptl:isSurvey", None, lpg="isSurvey")
    authored_by: list[Author] = Field(
        default_factory=list,
        validation_alias=AliasChoices("authored_by", "authoredBy"),
        json_schema_extra={"rdf": "ptl:authoredBy", "lpg": "AUTHORED_BY"},
    )
    published_in: Venue | None = _mapped_field("ptl:publishedIn", None, lpg="PUBLISHED_IN")
    published_by: Organization | None = _mapped_field("ptl:publishedBy", None, lpg="PUBLISHED_BY")
    has_version: list[Work] = Field(
        default_factory=list,
        validation_alias=AliasChoices("has_version", "hasVersion"),
        json_schema_extra={"rdf": "ptl:hasVersion", "lpg": "HAS_VERSION"},
    )
    is_canonical_version: bool | None = _mapped_field(
        "ptl:isCanonicalVersion", None, lpg="isCanonicalVersion"
    )

    @field_validator("issued")
    @classmethod
    def _valid_issued(cls, value: date | str) -> date | str:
        if isinstance(value, str) and not (
            re.fullmatch(r"\d{4}", value) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
        ):
            raise ValueError("issued must be an ISO date or a four-digit gYear")
        return value

    @model_validator(mode="after")
    def _has_identifier(self) -> Work:
        if not any((self.doi, self.arxiv_id, self.openalex_id, self.s2_id, self.url)):
            raise ValueError(
                "Work requires at least one DOI, arXiv, OpenAlex, S2, or URL identifier"
            )
        return self

    def _mint_iri(self) -> str:
        return iri_helpers.mint_work_iri(
            doi=self.doi,
            arxiv_id=self.arxiv_id,
            openalex_id=self.openalex_id,
            s2_id=self.s2_id,
            url=self.url,
        )


class Author(DomainModel):
    orcid: str | None = _mapped_field("ptl:orcid", None, lpg="orcid")
    openalex_id: str | None = _mapped_field("ptl:openAlexId", None, lpg="openAlexId")
    s2_id: str | None = _mapped_field("ptl:s2Id", None, lpg="s2Id")
    affiliated_with: list[Organization] = Field(
        default_factory=list,
        validation_alias=AliasChoices("affiliated_with", "affiliatedWith"),
        json_schema_extra={"rdf": "ptl:affiliatedWith", "lpg": "AFFILIATED_WITH"},
    )

    @model_validator(mode="after")
    def _has_identifier(self) -> Author:
        if not any((self.orcid, self.openalex_id, self.s2_id)):
            raise ValueError("Author requires an ORCID, OpenAlex, or S2 identifier")
        return self

    def _mint_iri(self) -> str:
        return iri_helpers.mint_author_iri(
            orcid=self.orcid,
            openalex_id=self.openalex_id,
            s2_id=self.s2_id,
        )


class Organization(DomainModel):
    ror: str | None = _mapped_field("ptl:ror", None, lpg="ror")
    domain: str | None = _mapped_field("ptl:domain", None, lpg="domain")
    org_kind: OrgKind | None = _mapped_field("ptl:orgKind", None, lpg="orgKind")

    @model_validator(mode="after")
    def _has_identifier(self) -> Organization:
        if not any((self.ror, self.domain)):
            raise ValueError("Organization requires a ROR or domain identifier")
        return self

    def _mint_iri(self) -> str:
        return iri_helpers.mint_organization_iri(ror=self.ror, domain=self.domain)


class Venue(DomainModel):
    openalex_id: str | None = _mapped_field("ptl:openAlexId", None, lpg="openAlexId")
    issn: str | None = _mapped_field("ptl:issn", None, lpg="issn")
    slug: str | None = _mapped_field("ptl:slug", None, lpg="slug")
    venue_kind: VenueKind | None = _mapped_field("ptl:venueKind", None, lpg="venueKind")

    @model_validator(mode="after")
    def _has_identifier(self) -> Venue:
        if not any((self.openalex_id, self.issn, self.slug)):
            raise ValueError("Venue requires an OpenAlex ID, ISSN, or slug identifier")
        return self

    def _mint_iri(self) -> str:
        return iri_helpers.mint_venue_iri(
            openalex_id=self.openalex_id,
            issn=self.issn,
            slug=self.slug,
        )


class Citation(DomainModel):
    citing_work: Work | str = _mapped_field("ptl:citingWork", lpg="CITES")
    cited_work: Work | str = _mapped_field("ptl:citedWork", lpg="CITES")
    citation_function: CitationFunction | None = _mapped_field(
        "ptl:citationFunction", None, lpg="citationFunction"
    )
    is_influential: bool | None = _mapped_field("ptl:isInfluential", None, lpg="isInfluential")
    citation_context: str | None = _mapped_field("ptl:citationContext", None, lpg="citationContext")

    def _mint_iri(self) -> str:
        return iri_helpers.mint_citation_iri(self.citing_work, self.cited_work)


class Concept(DomainModel):
    slug: str = Field(
        min_length=1,
        json_schema_extra={"iri_component": "conceptSlug", "lpg": "slug"},
    )
    alt_label: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("alt_label", "altLabel"),
        json_schema_extra={"rdf": "skos:altLabel", "lpg": "altLabel"},
    )
    broader: list[Concept | str] = Field(
        default_factory=list,
        json_schema_extra={"rdf": "skos:broader", "lpg": "BROADER"},
    )
    narrower: list[Concept | str] = Field(
        default_factory=list,
        json_schema_extra={"rdf": "skos:narrower", "lpg": "NARROWER"},
    )
    first_seen_in: Work | str | None = Field(
        default=None,
        validation_alias=AliasChoices("first_seen_in", "firstSeenIn"),
        json_schema_extra={"rdf": "ptl:firstSeenIn", "lpg": "FIRST_SEEN_IN"},
    )
    exact_match: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("exact_match", "exactMatch"),
        json_schema_extra={"rdf": "skos:exactMatch", "lpg": "exactMatch"},
    )
    close_match: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("close_match", "closeMatch"),
        json_schema_extra={"rdf": "skos:closeMatch", "lpg": "closeMatch"},
    )
    concept_kind: ClassVar[str]

    def _mint_iri(self) -> str:
        return iri_helpers.mint_concept_iri(self.concept_kind, self.slug)


class Problem(Concept):
    concept_kind: ClassVar[str] = "problem"


class Method(Concept):
    concept_kind: ClassVar[str] = "method"
    extends_method: list[Method | str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("extends_method", "extendsMethod"),
        json_schema_extra={"rdf": "ptl:extendsMethod", "lpg": "EXTENDS_METHOD"},
    )


class Dataset(Concept):
    concept_kind: ClassVar[str] = "dataset"


class Metric(Concept):
    concept_kind: ClassVar[str] = "metric"
    metric_direction: MetricDirection = _mapped_field(
        "ptl:metricDirection",
        lpg="metricDirection",
        validation_alias=AliasChoices("metric_direction", "metricDirection"),
    )


class Evidence(DomainModel):
    quote: str = _mapped_field("ptl:quote", min_length=1, lpg="quote")
    locator: str | None = _mapped_field("ptl:locator", None, lpg="locator")
    from_work: Work | str = _mapped_field("ptl:fromWork", lpg="FROM_WORK")
    from_source_kind: FromSourceKind = _mapped_field("ptl:fromSourceKind", lpg="fromSourceKind")

    def _mint_iri(self) -> str:
        return iri_helpers.mint_evidence_iri(self.quote, self.from_work)


class Statement(DomainModel):
    of_work: Work | str = Field(
        validation_alias=AliasChoices("of_work", "ofWork"),
        json_schema_extra={"rdf": "ptl:ofWork", "lpg": "OF_WORK"},
    )
    has_evidence: list[Evidence] = Field(
        default_factory=list,
        min_length=1,
        validation_alias=AliasChoices("has_evidence", "hasEvidence", "evidence"),
        json_schema_extra={"rdf": "ptl:hasEvidence", "lpg": "HAS_EVIDENCE"},
    )
    number: int = Field(
        default=1,
        ge=1,
        exclude=True,
        validation_alias=AliasChoices("number", "n"),
        json_schema_extra={"iri_component": "statementOrdinal"},
    )
    statement_kind: ClassVar[str]

    @model_validator(mode="after")
    def _evidence_matches_work(self) -> Statement:
        if not any(_iri_equal(evidence.from_work, self.of_work) for evidence in self.has_evidence):
            raise ValueError("a statement needs Evidence whose from_work equals its of_work")
        return self

    def _mint_iri(self) -> str:
        return iri_helpers.mint_statement_iri(self.statement_kind, self.of_work, self.number)


class Contribution(Statement):
    statement_kind: ClassVar[str] = "contribution"
    addresses: list[Problem | str] = Field(
        default_factory=list, json_schema_extra={"rdf": "ptl:addresses", "lpg": "ADDRESSES"}
    )
    proposes: list[Method | str] = Field(
        default_factory=list, json_schema_extra={"rdf": "ptl:proposes", "lpg": "PROPOSES"}
    )
    uses: list[Method | str] = Field(
        default_factory=list, json_schema_extra={"rdf": "ptl:uses", "lpg": "USES"}
    )
    evaluates_on: list[Dataset | str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("evaluates_on", "evaluatesOn"),
        json_schema_extra={"rdf": "ptl:evaluatesOn", "lpg": "EVALUATES_ON"},
    )
    reports: list[Result | str] = Field(
        default_factory=list, json_schema_extra={"rdf": "ptl:reports", "lpg": "REPORTS"}
    )
    addresses_limitation: list[Limitation | str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("addresses_limitation", "addressesLimitation"),
        json_schema_extra={"rdf": "ptl:addressesLimitation", "lpg": "ADDRESSES_LIMITATION"},
    )
    contribution_kind: ContributionKind = _mapped_field(
        "ptl:contributionKind", lpg="contributionKind"
    )

    _normalize_lists = field_validator(
        "addresses",
        "proposes",
        "uses",
        "evaluates_on",
        "reports",
        "addresses_limitation",
        mode="before",
    )(_as_list)


class Result(Statement):
    statement_kind: ClassVar[str] = "result"
    of_method: Method | str = Field(
        validation_alias=AliasChoices("of_method", "ofMethod"),
        json_schema_extra={"rdf": "ptl:ofMethod", "lpg": "OF_METHOD"},
    )
    on_dataset: Dataset | str = Field(
        validation_alias=AliasChoices("on_dataset", "onDataset"),
        json_schema_extra={"rdf": "ptl:onDataset", "lpg": "ON_DATASET"},
    )
    with_metric: Metric | str = Field(
        validation_alias=AliasChoices("with_metric", "withMetric"),
        json_schema_extra={"rdf": "ptl:withMetric", "lpg": "WITH_METRIC"},
    )
    value: Decimal = _mapped_field("ptl:value", lpg="value")
    unit: str | None = _mapped_field("ptl:unit", None, lpg="unit")
    setting: str | None = _mapped_field("ptl:setting", None, lpg="setting")
    claims_sota: bool | None = _mapped_field("ptl:claimsSOTA", None, lpg="claimsSOTA")


class Claim(Statement):
    statement_kind: ClassVar[str] = "claim"
    claim_text: str = _mapped_field("ptl:claimText", min_length=1, lpg="claimText")
    about: list[Concept | str] = Field(
        default_factory=list, json_schema_extra={"rdf": "ptl:about", "lpg": "ABOUT"}
    )
    supports_claim: list[Claim | str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("supports_claim", "supportsClaim"),
        json_schema_extra={"rdf": "ptl:supportsClaim", "lpg": "SUPPORTS_CLAIM"},
    )
    contradicts_claim: list[Claim | str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("contradicts_claim", "contradictsClaim"),
        json_schema_extra={"rdf": "ptl:contradictsClaim", "lpg": "CONTRADICTS_CLAIM"},
    )

    _normalize_lists = field_validator(
        "about", "supports_claim", "contradicts_claim", mode="before"
    )(_as_list)


class Limitation(Statement):
    statement_kind: ClassVar[str] = "limitation"
    limitation_text: str = _mapped_field("ptl:limitationText", min_length=1, lpg="limitationText")
    about: list[Concept | str] = Field(
        default_factory=list, json_schema_extra={"rdf": "ptl:about", "lpg": "ABOUT"}
    )
    limitation_of: list[Method | Dataset | Work | str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("limitation_of", "limitationOf"),
        json_schema_extra={"rdf": "ptl:limitationOf", "lpg": "LIMITATION_OF"},
    )

    _normalize_lists = field_validator("about", "limitation_of", mode="before")(_as_list)


class FutureWork(Statement):
    statement_kind: ClassVar[str] = "futurework"
    future_work_text: str = _mapped_field("ptl:futureWorkText", min_length=1, lpg="futureWorkText")
    about: list[Concept | str] = Field(
        default_factory=list, json_schema_extra={"rdf": "ptl:about", "lpg": "ABOUT"}
    )

    _normalize_lists = field_validator("about", mode="before")(_as_list)


class ExtractionRun(DomainModel):
    """The ``prov:Activity`` metadata attached to a content extraction partition."""

    run_id: str = Field(min_length=1, exclude=True, json_schema_extra={"iri_component": "runId"})
    model: str = _mapped_field("ptl:model", min_length=1, lpg="model")
    prompt_version: str = _mapped_field("ptl:promptVersion", min_length=1, lpg="promptVersion")
    ontology_version: str = _mapped_field(
        "ptl:ontologyVersion", min_length=1, lpg="ontologyVersion"
    )
    ended_at_time: datetime | None = _mapped_field("prov:endedAtTime", None, lpg="endedAtTime")

    def _mint_iri(self) -> str:
        return iri_helpers.mint_extraction_iri(self.run_id)


class Review(DomainModel):
    review_id: str = Field(min_length=1, exclude=True, json_schema_extra={"iri_component": "id"})
    seed_kind: SeedKind = _mapped_field("ptl:seedKind", lpg="seedKind")
    seed_value: str = _mapped_field("ptl:seedValue", min_length=1, lpg="seedValue")
    has_protocol: ReviewProtocol | str = Field(
        validation_alias=AliasChoices("has_protocol", "hasProtocol"),
        json_schema_extra={"rdf": "ptl:hasProtocol", "lpg": "HAS_PROTOCOL"},
    )
    started_at: datetime | None = _mapped_field("ptl:startedAt", None, lpg="startedAt")
    ended_at: datetime | None = _mapped_field("ptl:endedAt", None, lpg="endedAt")
    budget_works: int | None = _mapped_field("ptl:budgetWorks", None, lpg="budgetWorks")
    budget_usd: Decimal | None = _mapped_field("ptl:budgetUsd", None, lpg="budgetUsd")
    spend_usd: Decimal | None = _mapped_field("ptl:spendUsd", None, lpg="spendUsd")

    def _mint_iri(self) -> str:
        return iri_helpers.mint_review_iri(self.review_id)


class ReviewProtocol(DomainModel):
    protocol_id: str = Field(min_length=1, exclude=True, json_schema_extra={"iri_component": "id"})
    scope_statement: str | None = _mapped_field("ptl:scopeStatement", None, lpg="scopeStatement")
    inclusion_criterion: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("inclusion_criterion", "inclusionCriterion"),
        json_schema_extra={"rdf": "ptl:inclusionCriterion", "lpg": "inclusionCriterion"},
    )
    exclusion_criterion: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("exclusion_criterion", "exclusionCriterion"),
        json_schema_extra={"rdf": "ptl:exclusionCriterion", "lpg": "exclusionCriterion"},
    )
    from_year: int | None = _mapped_field("ptl:fromYear", None, lpg="fromYear")
    to_year: int | None = _mapped_field("ptl:toYear", None, lpg="toYear")
    allowed_tier: list[SourceTier] = Field(
        default_factory=list,
        validation_alias=AliasChoices("allowed_tier", "allowedTier"),
        json_schema_extra={"rdf": "ptl:allowedTier", "lpg": "allowedTier"},
    )
    max_works: int | None = _mapped_field("ptl:maxWorks", None, lpg="maxWorks")
    max_snowball_depth: int | None = _mapped_field(
        "ptl:maxSnowballDepth", None, lpg="maxSnowballDepth"
    )

    def _mint_iri(self) -> str:
        return iri_helpers.mint_protocol_iri(self.protocol_id)


class Inclusion(DomainModel):
    of_review: Review | str = Field(
        validation_alias=AliasChoices("of_review", "ofReview"),
        json_schema_extra={"rdf": "ptl:ofReview", "lpg": "OF_REVIEW"},
    )
    of_work: Work | str = Field(
        validation_alias=AliasChoices("of_work", "ofWork"),
        json_schema_extra={"rdf": "ptl:ofWork", "lpg": "OF_WORK"},
    )
    decision: Decision = _mapped_field("ptl:decision", lpg="decision")
    stage: Stage = _mapped_field("ptl:stage", lpg="stage")
    reason: str | None = _mapped_field("ptl:reason", None, lpg="reason")
    discovered_via: DiscoveredVia | None = _mapped_field(
        "ptl:discoveredVia", None, lpg="discoveredVia"
    )
    relevance: float | None = _mapped_field("ptl:relevance", None, lpg="relevance", ge=0, le=1)
    page_rank: float | None = _mapped_field("ptl:pageRank", None, lpg="pageRank")
    betweenness: float | None = _mapped_field("ptl:betweenness", None, lpg="betweenness")
    on_main_path: bool | None = _mapped_field("ptl:onMainPath", None, lpg="onMainPath")
    citation_velocity: float | None = _mapped_field(
        "ptl:citationVelocity", None, lpg="citationVelocity"
    )
    frontier_score: float | None = _mapped_field(
        "ptl:frontierScore",
        None,
        lpg="frontierScore",
        validation_alias=AliasChoices("frontier_score", "frontierScore"),
    )
    frontier_component_velocity: Decimal | None = _mapped_field(
        "ptl:frontierComponentVelocity",
        None,
        lpg="frontierComponentVelocity",
        validation_alias=AliasChoices("frontier_component_velocity", "frontierComponentVelocity"),
        ge=Decimal("0"),
        le=Decimal("1"),
    )
    frontier_component_main_path_leaf: Decimal | None = _mapped_field(
        "ptl:frontierComponentMainPathLeaf",
        None,
        lpg="frontierComponentMainPathLeaf",
        validation_alias=AliasChoices(
            "frontier_component_main_path_leaf", "frontierComponentMainPathLeaf"
        ),
        ge=Decimal("0"),
        le=Decimal("1"),
    )
    frontier_component_cluster_growth: Decimal | None = _mapped_field(
        "ptl:frontierComponentClusterGrowth",
        None,
        lpg="frontierComponentClusterGrowth",
        validation_alias=AliasChoices(
            "frontier_component_cluster_growth", "frontierComponentClusterGrowth"
        ),
        ge=Decimal("0"),
        le=Decimal("1"),
    )
    frontier_component_concept_novelty: Decimal | None = _mapped_field(
        "ptl:frontierComponentConceptNovelty",
        None,
        lpg="frontierComponentConceptNovelty",
        validation_alias=AliasChoices(
            "frontier_component_concept_novelty", "frontierComponentConceptNovelty"
        ),
        ge=Decimal("0"),
        le=Decimal("1"),
    )
    frontier_component_sota_claim: Decimal | None = _mapped_field(
        "ptl:frontierComponentSotaClaim",
        None,
        lpg="frontierComponentSotaClaim",
        validation_alias=AliasChoices(
            "frontier_component_sota_claim", "frontierComponentSotaClaim"
        ),
        ge=Decimal("0"),
        le=Decimal("1"),
    )
    frontier_component_not_peer_reviewed: Decimal | None = _mapped_field(
        "ptl:frontierComponentNotPeerReviewed",
        None,
        lpg="frontierComponentNotPeerReviewed",
        validation_alias=AliasChoices(
            "frontier_component_not_peer_reviewed", "frontierComponentNotPeerReviewed"
        ),
        ge=Decimal("0"),
        le=Decimal("1"),
    )
    role: list[Role] = Field(
        default_factory=list,
        validation_alias=AliasChoices("role", "roles"),
        json_schema_extra={"rdf": "ptl:role", "lpg": "role"},
    )
    in_cluster: Cluster | str | None = Field(
        default=None,
        validation_alias=AliasChoices("in_cluster", "inCluster"),
        json_schema_extra={"rdf": "ptl:inCluster", "lpg": "IN_CLUSTER"},
    )

    _normalize_roles = field_validator("role", mode="before")(_as_list)

    @model_validator(mode="after")
    def _excluded_needs_reason(self) -> Inclusion:
        if self.decision is Decision.EXCLUDED and not (self.reason and self.reason.strip()):
            raise ValueError("an excluded Inclusion requires a reason")
        return self

    @model_validator(mode="after")
    def _frontier_score_needs_all_components(self) -> Inclusion:
        components = (
            self.frontier_component_velocity,
            self.frontier_component_main_path_leaf,
            self.frontier_component_cluster_growth,
            self.frontier_component_concept_novelty,
            self.frontier_component_sota_claim,
            self.frontier_component_not_peer_reviewed,
        )
        if self.frontier_score is not None and any(component is None for component in components):
            raise ValueError("frontier_score requires all six frontier components")
        return self

    def _mint_iri(self) -> str:
        return iri_helpers.mint_inclusion_iri(self.of_review, self.of_work)


class Cluster(DomainModel):
    cluster_number: int = Field(
        ge=1,
        validation_alias=AliasChoices("cluster_number", "number", "n"),
        json_schema_extra={"iri_component": "number"},
    )
    of_review: Review | str = Field(
        validation_alias=AliasChoices("of_review", "ofReview"),
        json_schema_extra={"rdf": "ptl:ofReview", "lpg": "OF_REVIEW"},
    )
    level: int | None = _mapped_field("ptl:level", None, lpg="level")
    parent_cluster: Cluster | str | None = Field(
        default=None,
        validation_alias=AliasChoices("parent_cluster", "parentCluster"),
        json_schema_extra={"rdf": "ptl:parentCluster", "lpg": "PARENT_CLUSTER"},
    )
    label: str | None = _mapped_field("rdfs:label", None, lpg="label")
    summary: str | None = _mapped_field("ptl:summary", None, lpg="summary")
    growth_rate: float | None = _mapped_field("ptl:growthRate", None, lpg="growthRate")
    top_concept: Concept | str | None = Field(
        default=None,
        validation_alias=AliasChoices("top_concept", "topConcept"),
        json_schema_extra={"rdf": "ptl:topConcept", "lpg": "TOP_CONCEPT"},
    )

    def _mint_iri(self) -> str:
        return iri_helpers.mint_cluster_iri(self.of_review, self.cluster_number)


class ClusterPair(DomainModel):
    """Review-scoped RDF node for one ordered representation of an unordered cluster pair."""

    of_review: Review | str = Field(
        validation_alias=AliasChoices("of_review", "ofReview"),
        json_schema_extra={"rdf": "ptl:ofReview", "lpg": "OF_REVIEW"},
    )
    cluster_a: Cluster | str = Field(
        validation_alias=AliasChoices("cluster_a", "clusterA"),
        json_schema_extra={"rdf": "ptl:clusterA", "lpg": "CLUSTER_PAIR"},
    )
    cluster_b: Cluster | str = Field(
        validation_alias=AliasChoices("cluster_b", "clusterB"),
        json_schema_extra={"rdf": "ptl:clusterB", "lpg": "CLUSTER_PAIR"},
    )
    semantic_similarity: Decimal = _mapped_field(
        "ptl:semanticSimilarity",
        lpg="semanticSimilarity",
        validation_alias=AliasChoices("semantic_similarity", "semanticSimilarity"),
        ge=Decimal("0"),
        le=Decimal("1"),
    )
    cross_citation_count: int = _mapped_field(
        "ptl:crossCitationCount",
        lpg="crossCitationCount",
        validation_alias=AliasChoices("cross_citation_count", "crossCitationCount"),
        ge=0,
    )

    @model_validator(mode="after")
    def _cluster_endpoints_are_ordered(self) -> ClusterPair:
        cluster_a = _as_iri(self.cluster_a)
        cluster_b = _as_iri(self.cluster_b)
        if cluster_a >= cluster_b:
            raise ValueError("cluster_a must be lexicographically smaller than cluster_b")
        return self

    def _mint_iri(self) -> str:
        # v0.2 specifies the pair's RDF fields and uniqueness rule, but not a separate
        # instance-IRI row.  Keep the node addressable with a stable, fully encoded pair key.
        review = _as_iri(self.of_review)
        review_prefix = f"{iri_helpers.PTLR_NAMESPACE}review/"
        if review.startswith(review_prefix):
            review = iri_helpers.parse_review_iri(review)["id"]
        return (
            f"{iri_helpers.PTLR_NAMESPACE}cluster-pair/"
            f"{iri_helpers.percent_encode(review)}/"
            f"{iri_helpers.percent_encode(_as_iri(self.cluster_a))}/"
            f"{iri_helpers.percent_encode(_as_iri(self.cluster_b))}"
        )


class GapHypothesis(DomainModel):
    gap_number: int = Field(
        ge=1,
        validation_alias=AliasChoices("gap_number", "number", "n"),
        json_schema_extra={"iri_component": "number"},
    )
    of_review: Review | str = Field(
        validation_alias=AliasChoices("of_review", "ofReview"),
        json_schema_extra={"rdf": "ptl:ofReview", "lpg": "OF_REVIEW"},
    )
    gap_type: GapType = _mapped_field("ptl:gapType", lpg="gapType")
    statement: str = _mapped_field("ptl:statement", min_length=1, lpg="statement")
    about: list[Concept | str] = Field(
        default_factory=list, json_schema_extra={"rdf": "ptl:about", "lpg": "ABOUT"}
    )
    supported_by: list[Limitation | FutureWork | Claim | Result | Work | str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("supported_by", "supportedBy"),
        json_schema_extra={"rdf": "ptl:supportedBy", "lpg": "SUPPORTED_BY"},
    )
    confidence: float = _mapped_field("ptl:confidence", ge=0, le=1, lpg="confidence")
    verification_outcome: VerificationOutcome = _mapped_field(
        "ptl:verificationOutcome", lpg="verificationOutcome"
    )
    user_status: UserStatus = _mapped_field("ptl:userStatus", lpg="userStatus")
    user_note: str | None = _mapped_field("ptl:userNote", None, lpg="userNote")

    _normalize_support = field_validator("about", "supported_by", mode="before")(_as_list)

    @model_validator(mode="after")
    def _has_support_or_subject(self) -> GapHypothesis:
        if not self.supported_by and not self.about:
            raise ValueError("a GapHypothesis needs at least one supported_by or about value")
        evidence_prefixes = (
            f"{iri_helpers.PTLR_NAMESPACE}evidence/",
            "ptlr:evidence/",
        )
        if any(
            isinstance(target, str) and target.startswith(evidence_prefix)
            for target in self.supported_by
            for evidence_prefix in evidence_prefixes
        ):
            raise ValueError("supported_by cannot target Evidence in v0.2")
        return self

    def _mint_iri(self) -> str:
        return iri_helpers.mint_gap_hypothesis_iri(self.of_review, self.gap_number)


_MODELS = (
    Work,
    Author,
    Organization,
    Venue,
    Citation,
    Concept,
    Problem,
    Method,
    Dataset,
    Metric,
    Evidence,
    Statement,
    Contribution,
    Result,
    Claim,
    Limitation,
    FutureWork,
    ExtractionRun,
    Review,
    ReviewProtocol,
    Inclusion,
    Cluster,
    ClusterPair,
    GapHypothesis,
)

for _model in _MODELS:
    _model.model_rebuild()


__all__ = [
    "Author",
    "Citation",
    "CitationFunction",
    "CiTOFunction",
    "Claim",
    "Cluster",
    "ClusterPair",
    "Concept",
    "Contribution",
    "ContributionKind",
    "Dataset",
    "Decision",
    "DiscoveredVia",
    "DomainModel",
    "Evidence",
    "ExtractionRun",
    "FromSourceKind",
    "FutureWork",
    "GapHypothesis",
    "GapType",
    "Inclusion",
    "Limitation",
    "Metric",
    "MetricDirection",
    "Method",
    "OrgKind",
    "Organization",
    "Problem",
    "Result",
    "Review",
    "ReviewProtocol",
    "Role",
    "SeedKind",
    "SourceTier",
    "Stage",
    "Statement",
    "UserStatus",
    "Venue",
    "VenueKind",
    "VerificationOutcome",
    "Work",
    "WorkType",
    "CitoFunction",
]
