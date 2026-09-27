"""Deterministic noise filtering for merged keyword concept clusters."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .merge import ConceptCluster
from .normalize import normalize_keyword

# These broad OpenAlex subject labels describe disciplines rather than useful map topics.
_GENERIC_TERM_LABELS = (
    "computer science",
    "library science",
    "world wide web",
    "programming language",
    "operating system",
    "mathematics",
    "physics",
    "engineering",
    "business",
    "economics",
    "psychology",
    "philosophy",
    "biology",
    "medicine",
    "geography",
    "political science",
    "sociology",
    "art",
    "history",
    "law",
)
GENERIC_TERMS = frozenset(normalize_keyword(term) for term in _GENERIC_TERM_LABELS)


@dataclass(frozen=True, slots=True)
class ConceptFilterConfig:
    """Thresholds controlling concept noise filtering."""

    max_share: float = 0.5
    min_works_for_share: int = 20
    min_support: int = 2
    min_works_for_support: int = 15


DEFAULT = ConceptFilterConfig()


@dataclass(frozen=True, slots=True)
class ConceptFilterReport:
    """Labels removed by each filter rule."""

    generic: tuple[str, ...] = ()
    too_common: tuple[str, ...] = ()
    too_rare: tuple[str, ...] = ()

    @property
    def counts(self) -> dict[str, int]:
        """Return the number of dropped clusters for each filtering reason."""

        return {
            "generic": len(self.generic),
            "too_common": len(self.too_common),
            "too_rare": len(self.too_rare),
        }

    @property
    def total_dropped(self) -> int:
        """Return the total number of clusters removed by all rules."""

        return len(self.generic) + len(self.too_common) + len(self.too_rare)

    @property
    def dropped(self) -> dict[str, tuple[str, ...]]:
        """Return dropped labels grouped by filtering reason."""

        return {
            "generic": self.generic,
            "too_common": self.too_common,
            "too_rare": self.too_rare,
        }

    @property
    def dropped_generic(self) -> tuple[str, ...]:
        """Compatibility alias for the generic labels."""

        return self.generic

    @property
    def dropped_too_common(self) -> tuple[str, ...]:
        """Compatibility alias for the too-common labels."""

        return self.too_common

    @property
    def dropped_too_rare(self) -> tuple[str, ...]:
        """Compatibility alias for the too-rare labels."""

        return self.too_rare


def _is_generic(cluster: ConceptCluster) -> bool:
    terms = (cluster.label, *cluster.aliases)
    return any(normalize_keyword(term) in GENERIC_TERMS for term in terms)


def filter_concepts(
    clusters: Sequence[ConceptCluster],
    *,
    total_works: int,
    config: ConceptFilterConfig = DEFAULT,
) -> tuple[list[ConceptCluster], ConceptFilterReport]:
    """Filter generic, overly common, and unsupported concept clusters.

    The support and document-frequency rules activate only for projects large enough for
    those statistics to be meaningful.  Generic labels are removed at every project size.
    Cluster and report order follows the supplied deterministic sequence.
    """

    generic: list[str] = []
    too_common: list[str] = []
    too_rare: list[str] = []
    kept: list[ConceptCluster] = []

    share_filter_active = total_works > 0 and total_works >= config.min_works_for_share
    support_filter_active = total_works >= config.min_works_for_support

    for cluster in clusters:
        if _is_generic(cluster):
            generic.append(cluster.label)
            continue

        support = len(set(cluster.work_ids))
        if share_filter_active and support / total_works > config.max_share:
            too_common.append(cluster.label)
            continue
        if support_filter_active and support < config.min_support:
            too_rare.append(cluster.label)
            continue
        kept.append(cluster)

    report = ConceptFilterReport(
        generic=tuple(generic),
        too_common=tuple(too_common),
        too_rare=tuple(too_rare),
    )
    return kept, report


__all__ = [
    "DEFAULT",
    "GENERIC_TERMS",
    "ConceptFilterConfig",
    "ConceptFilterReport",
    "filter_concepts",
]
