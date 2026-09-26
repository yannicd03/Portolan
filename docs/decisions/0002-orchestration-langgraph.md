# ADR-0002 — orchestration is a plain LangGraph `StateGraph`

- **Status:** accepted
- **Date:** 2026-09-22
- **Decides:** open decision 3 in the design-notes spec (`$PORTOLAN_NOTES_DIR/spec.md`)

## Context

The pipeline (stages 0-8 in `$PORTOLAN_NOTES_DIR/architecture.md`) is a fixed graph with checkpoints, a
resume-from-last-completed-stage requirement, and a mandatory human confirmation of the review
protocol between stage 0 and stage 1. The alternative considered was the Claude Agent SDK.

Prior art on this machine: AMA-KBQA and ORCA both run plain `StateGraph`; `deepagents` was
rejected there for non-removable middleware and dependency conflicts
(recorded in the private design notes).

## Decision

Plain LangGraph `StateGraph`. No `deepagents`, no agent framework on top.

- One node per pipeline stage; the agentic stages (0 intake, 1 screening, 3 extraction,
  4 canonicalization, 7 synthesis) call the LLM client directly from inside their node.
- State is an explicit typed object persisted per run; a checkpointer gives resume.
- The protocol confirmation is an interrupt between stage 0 and stage 1, not a tool call.
- The LLM client is provider-agnostic (see ADR-0004) so the framework choice does not drag a
  provider choice behind it.

## Consequences

- Reproducibility and cost control come from the graph being fixed; autonomy is deliberately
  low, matching spec decision 3.
- Tool-use loops that *are* genuinely open-ended (the ORKG querying step, the gap verification
  search) are implemented as bounded loops inside their own node with a hard iteration cap and
  a "stop and synthesize" trigger — the lesson carried from the AMA-KBQA SciQA work.
- LangGraph is not yet a dependency in `backend/pyproject.toml`; it is added at M1, when the
  first stage node is written. M0 needs no orchestration.
