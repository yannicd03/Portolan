"""Store-neutral repository contracts and result DTOs.

The repository is an abstract base class rather than a concrete query API on
purpose.  The pipeline can depend on one stable, typed boundary while the M0
spike decides whether the persisted representation is RDF or LPG.  Backends
may override the batch hooks to expose a transaction, but callers never need
to emit SPARQL or Cypher themselves.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from portolan.models import (
        Author,
        Citation,
        Claim,
        Cluster,
        ClusterPair,
        Concept,
        Contribution,
        Evidence,
        ExtractionRun,
        FutureWork,
        GapHypothesis,
        Inclusion,
        Limitation,
        Organization,
        Result,
        Review,
        ReviewProtocol,
        UserStatus,
        Venue,
        Work,
    )

    type Statement = Contribution | Result | Claim | Limitation | FutureWork


type GraphSelection = str | Sequence[str] | None


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One result emitted by SHACL validation.

    The fields intentionally use strings for RDF terms.  That keeps the DTO
    usable by both an RDF backend and an LPG backend whose validation input is
    an RDF export.
    """

    message: str
    focus_node: str | None = None
    result_path: str | None = None
    severity: str | None = None
    source_shape: str | None = None
    source_constraint_component: str | None = None
    value: Any = None


@dataclass(slots=True)
class ValidationReport:
    """Structured output from the validation boundary.

    ``conforms`` is ``None`` when validation was skipped because the shapes
    file or the optional validator dependency was unavailable.  A ``False``
    value means pySHACL ran and found a non-conforming graph.  ``report_graph``
    is kept as an escape hatch for callers that need the original RDF report;
    normal consumers should use ``violations`` and ``warnings``.
    """

    conforms: bool | None
    violations: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)
    report_text: str = ""
    shapes_path: str | None = None
    skipped: bool = False
    error: str | None = None
    report_graph: Any | None = field(default=None, repr=False, compare=False)

    @property
    def valid(self) -> bool:
        """Return ``True`` only when validation actually established conformance."""

        return self.conforms is True

    @property
    def results_text(self) -> str:
        """Compatibility alias for pySHACL's ``results_text`` name."""

        return self.report_text

    @property
    def results_graph(self) -> Any | None:
        """Compatibility alias for pySHACL's ``results_graph`` name."""

        return self.report_graph

    @property
    def issues(self) -> list[ValidationIssue]:
        """Return errors and warnings in report order as far as available."""

        return [*self.violations, *self.warnings]

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the report."""

        return {
            "conforms": self.conforms,
            "violations": [
                {
                    "message": issue.message,
                    "focus_node": issue.focus_node,
                    "result_path": issue.result_path,
                    "severity": issue.severity,
                    "source_shape": issue.source_shape,
                    "source_constraint_component": issue.source_constraint_component,
                    "value": issue.value,
                }
                for issue in self.violations
            ],
            "warnings": [
                {
                    "message": issue.message,
                    "focus_node": issue.focus_node,
                    "result_path": issue.result_path,
                    "severity": issue.severity,
                    "source_shape": issue.source_shape,
                    "source_constraint_component": issue.source_constraint_component,
                    "value": issue.value,
                }
                for issue in self.warnings
            ],
            "report_text": self.report_text,
            "shapes_path": self.shapes_path,
            "skipped": self.skipped,
            "error": self.error,
        }


@dataclass(slots=True)
class SubgraphResult:
    """Nodes and edges returned for a frontend lens.

    Backends may return domain-model instances, dictionaries, or lightweight
    records in ``nodes`` and ``edges``.  The repository boundary deliberately
    does not force a frontend-specific serialization format.
    """

    nodes: list[Any] = field(default_factory=list)
    edges: list[Any] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return len(self.edges)


@dataclass(slots=True)
class QueryResult:
    """Store-independent result of a competency question."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def row_count(self) -> int:
        return len(self.rows)


class GraphRepository(ABC):
    """Typed, store-neutral write and read boundary for the ontology.

    Every concrete backend must implement these operations.  The layer and
    partition written by each operation are called out in its docstring so a
    backend cannot silently place corpus-scoped values on global ``Work``
    nodes.  Upserts and stable-IRI writes are idempotent by contract.  The
    default batch context manager is deliberately no-op; a backend that
    supports transactions overrides ``_begin_batch``, ``_commit_batch``, and
    ``_rollback_batch`` without changing callers.
    """

    @abstractmethod
    def upsert_work(self, work: Work) -> Work:
        """Write Layer 1 bibliographic data to the global ``ptlg:biblio`` partition."""

    @abstractmethod
    def upsert_author(self, author: Author) -> Author:
        """Write a Layer 1 ``Author`` to the global ``ptlg:biblio`` partition."""

    @abstractmethod
    def upsert_organization(self, organization: Organization) -> Organization:
        """Write a Layer 1 ``Organization`` to the global ``ptlg:biblio`` partition."""

    @abstractmethod
    def upsert_venue(self, venue: Venue) -> Venue:
        """Write a Layer 1 ``Venue`` to the global ``ptlg:biblio`` partition."""

    @abstractmethod
    def add_citation(self, citation: Citation) -> Citation:
        """Write Layer 1 citation data to ``ptlg:biblio``.

        Implementations must preserve both the reified ``ptl:Citation`` node
        and the plain ``cito:cites`` shortcut.
        """

    @abstractmethod
    def upsert_concept(self, concept: Concept) -> Concept:
        """Write a Layer 2 concept to the content partition selected by its run."""

    @abstractmethod
    def write_extraction(
        self,
        work: Work,
        extraction_run: ExtractionRun,
        statements: Sequence[Statement],
        evidence: Sequence[Evidence],
        *,
        review_id: str | None = None,
    ) -> None:
        """Write Layer 2 statements and evidence to ``ptlg:content/{runId}``.

        The batch belongs to exactly one work and extraction run.  The write
        boundary is responsible for enforcing the evidence-to-``ofWork``
        invariant shared by all five statement classes.  When supplied, the
        review id scopes analysis-stage relationships such as
        ``ADDRESSES_LIMITATION`` in the LPG binding; it does not move the
        content nodes out of their extraction partition.
        """

    @abstractmethod
    def write_review(self, review: Review) -> Review:
        """Write a Layer 3 ``Review`` to ``ptlg:review/{reviewId}``."""

    @abstractmethod
    def write_protocol(self, protocol: ReviewProtocol) -> ReviewProtocol:
        """Write a Layer 3 ``ReviewProtocol`` to its review partition."""

    @abstractmethod
    def write_inclusion(self, inclusion: Inclusion) -> Inclusion:
        """Write a Layer 3 ``Inclusion`` to ``ptlg:review/{reviewId}``."""

    @abstractmethod
    def write_cluster(self, cluster: Cluster) -> Cluster:
        """Write a Layer 3 ``Cluster`` and membership data to its review partition."""

    @abstractmethod
    def write_cluster_pair(self, pair: ClusterPair) -> ClusterPair:
        """Write a review-scoped ClusterPair (contract v0.2, amendment A5).

        The pair is unordered and written once, with ``cluster_a`` the
        lexicographically smaller IRI.  The two bindings diverge here: RDF needs
        an intermediate node to carry the per-pair scalars, while a property
        graph puts them on a single ``:CLUSTER_PAIR`` relationship.  CQ11 reads
        whichever shape its own binding produced.
        """

    @abstractmethod
    def write_gap_hypothesis(self, gap: GapHypothesis) -> GapHypothesis:
        """Write a Layer 3 ``GapHypothesis`` to ``ptlg:review/{reviewId}``."""

    @abstractmethod
    def update_gap_status(
        self,
        gap: GapHypothesis,
        user_status: UserStatus,
        user_note: str | None = None,
    ) -> GapHypothesis:
        """Update Layer 3 gap status in the gap's review partition."""

    @abstractmethod
    def fetch_work(self, work_iri: str) -> Work | None:
        """Read a Layer 1 work from the global ``ptlg:biblio`` partition."""

    @abstractmethod
    def fetch_lens_subgraph(
        self,
        review_id: str,
        lens_name: str,
        filters: Mapping[str, Any] | None = None,
    ) -> SubgraphResult:
        """Read a review-scoped Layer 3 lens subgraph for the frontend."""

    @abstractmethod
    def run_competency_question(
        self,
        cq_number: str | int,
        parameters: Mapping[str, Any] | None = None,
    ) -> QueryResult:
        """Read a numbered competency question across its graph partitions."""

    @abstractmethod
    def validate(self, graphs: GraphSelection = None) -> ValidationReport:
        """Read selected asserting graphs and validate all chosen partitions."""

    @abstractmethod
    def export_turtle(self, graphs: GraphSelection = None) -> str:
        """Read selected partitions and export them as faithful Turtle RDF."""

    def _begin_batch(self) -> None:
        """Begin a backend transaction; the default implementation is a no-op."""

        return None

    def _commit_batch(self) -> None:
        """Commit a backend transaction; the default implementation is a no-op."""

        return None

    def _rollback_batch(self) -> None:
        """Roll back a backend transaction; the default implementation is a no-op."""

        return None

    @contextmanager
    def batched_writes(self) -> Iterator[GraphRepository]:
        """Group writes in one backend transaction when the backend supports it."""

        self._begin_batch()
        try:
            yield self
        except BaseException:
            self._rollback_batch()
            raise
        else:
            self._commit_batch()

    def batch_writes(self) -> AbstractContextManager[GraphRepository]:
        """Return the named batch context manager used by pipeline callers."""

        return self.batched_writes()

    def batch(self) -> AbstractContextManager[GraphRepository]:
        """Short alias for :meth:`batched_writes`."""

        return self.batched_writes()


__all__ = [
    "GraphRepository",
    "GraphSelection",
    "QueryResult",
    "SubgraphResult",
    "ValidationIssue",
    "ValidationReport",
]
