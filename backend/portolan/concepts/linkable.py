"""Judge which concepts are worth linking in abstracts, with a small model (YAN-721).

Keyphrase extraction is deterministic and cannot tell "contrastive learning" from
"accurate": both are frequent in a project. A yes/no judgement on the label fixes that
for the abstract links. The verdict belongs to the label, not to a project, so it is kept
on the concept and a label is only paid for once. Concepts stay in the graph either way,
and an unjudged concept is linked as before.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from portolan.graph import ResearchGraph
    from portolan.settings import Settings

BATCH_SIZE = 40

PROMPT = """You sort terms extracted from research paper abstracts.

Keep a term if it names something specific a reader could look up: a method, algorithm, \
model, architecture, system, dataset, benchmark, metric, or a technique or phenomenon \
particular to a field.

Reject a term if it is ordinary language rather than a name: adjectives, general nouns \
and vague phrases such as "accurate", "large number", "recent work", "novel approach" or \
"experimental results".

Return the numbers of the terms to keep.

Terms:
{terms}"""


class KeptTerms(BaseModel):
    """The numbers of the terms worth linking."""

    keep: list[int] = Field(description="Numbers of the terms to keep")


class TermJudge(Protocol):
    def __call__(self, labels: Sequence[str]) -> Iterable[int]:
        """Return the 1-based positions of the labels to keep."""


@dataclass(frozen=True, slots=True)
class LinkableReport:
    judged: int
    linkable: int
    not_linkable: int
    failed: int
    skipped: int


def openrouter_judge(settings: Settings) -> TermJudge:
    """Build a judge on the configured small model."""

    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(
        model=settings.concept_model,
        base_url=settings.openrouter_base_url,
        api_key=settings.openrouter_api_key,
        temperature=0,
        timeout=120,
    ).with_structured_output(KeptTerms, method="json_schema")

    def judge(labels: Sequence[str]) -> Iterable[int]:
        terms = "\n".join(f"{index}. {label}" for index, label in enumerate(labels, start=1))
        result = model.invoke(PROMPT.format(terms=terms))
        assert isinstance(result, KeptTerms)
        return result.keep

    return judge


def classify_project_concepts(
    graph: ResearchGraph,
    project_id: str,
    settings: Settings,
    *,
    judge: TermJudge | None = None,
    redo: bool = False,
) -> LinkableReport:
    """Judge a project's concepts and store the verdicts batch by batch.

    A failed batch stays unjudged, so the concepts keep linking and a later call retries
    them. Each finished batch is stored at once and is not paid for again.
    """

    concepts = graph.project_concepts(project_id)
    pending = [concept for concept in concepts if redo or concept.linkable is None]
    judge = judge or openrouter_judge(settings)
    linkable = not_linkable = failed = 0
    for start in range(0, len(pending), BATCH_SIZE):
        batch = pending[start : start + BATCH_SIZE]
        try:
            kept = set(judge([concept.label for concept in batch]))
        except Exception:  # best effort: an unjudged concept simply keeps linking
            failed += len(batch)
            continue
        verdicts = {concept.id: position in kept for position, concept in enumerate(batch, start=1)}
        graph.set_concept_linkable(verdicts)
        linkable += sum(verdicts.values())
        not_linkable += len(verdicts) - sum(verdicts.values())
    return LinkableReport(
        judged=linkable + not_linkable,
        linkable=linkable,
        not_linkable=not_linkable,
        failed=failed,
        skipped=len(concepts) - len(pending),
    )
