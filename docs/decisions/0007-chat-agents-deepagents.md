# ADR-0007 — chat agents run on LangGraph Deep Agents

- **Status:** accepted (decided by the user 2026-09-26)
- **Date:** 2026-09-26
- **Decides:** the agent framework for the chat agents (Ask mode, Research mode)
- **Supersedes in part:** ADR-0002, for the chat agents; the harvest pipeline stays plain code
- **Related:** ADR-0004 (LLM provider), ADR-0006 (the graph is the agents' map)

## Context

ADR-0002 chose a plain LangGraph `StateGraph` and ruled out `deepagents`, because its filesystem
and subagent middleware cannot be removed, and a fixed pipeline with LLM calls inside nodes had
no use for either. Two things have changed since:

- The product is now chat-first: a Research-mode agent that runs the harvest with the user and
  an Ask-mode agent that answers from the project's papers.
- The amended ADR-0006 makes the graph the agents' map, not their source of facts. Agents find
  the passage that proves a claim by reading the downloaded paper with file operations (read,
  grep, open at a page), guided by graph queries.

A file-reading agent that delegates to subagents is what the Deep Agents harness provides, so
the reasons for rejecting it have become reasons to use it.

## Decision

The chat agents are built with `create_deep_agent` (`deepagents` 0.7.x; 0.7.19 was current on
2026-09-26).

- **Files:** a read-only `FilesystemBackend` over the document store (one directory per paper:
  `paper.pdf`, `paper.txt` with `=== page N ===` markers, `meta.json`). Agents use the built-in
  `read_file`, `grep` and `glob` tools; they get no write or shell access to the papers.
- **Graph tools:** thin tools over `portolan.graph.ResearchGraph`, including full-text search on
  titles and abstracts, a work's citation neighbourhood, works by author or concept, and project
  stats. These tools are how an agent decides which papers to open.
- **Subagents:** Ask mode can hand one paper to a subagent ("read this paper for X, return
  quotes with page numbers"), so the main context holds findings rather than full texts.
- **Research mode:** the M1 harvest steps (search, snowball, screen, acquire, build concepts)
  are plain Python functions exposed as tools. Confirming the research plan with the user is a
  human-in-the-loop interrupt in the chat.
- **Citations:** the agent cites a quote as (document sha256, page). The server re-checks every
  quote against `paper.txt` (`portolan.documents.text.find_passage`) before the UI shows a
  citation chip. A quote that is not found is not rendered as proof.
- **Model access:** LLM calls go through ADR-0004's OpenRouter route using a `ChatOpenAI`-style
  client.

## Consequences

- The M1 harvest pipeline and the graph and document layers stay framework-free, so they can be
  tested without an LLM and reused by both modes.
- `deepagents` requires `langchain-anthropic` and `langchain-google-genai` even though we will
  not use them, and it sets floors on `langchain-core` and pydantic. The backend has no
  conflicting pins today; if one appears, the agent moves into its own service (the pattern
  used in other projects).
- Gotchas recorded from earlier adoptions to handle up front:
  - `subagents=[]` does not remove the general-purpose subagent; a harness profile keyed on
    the resolved `provider:model` does.
  - Custom `wrap_tool_call` middleware also needs `awrap_tool_call`.
  - Derive `recursion_limit` from the compiled graph's node count.
  - The default `StateBackend` is thread-scoped; choose the backend explicitly.
- ADR-0002 still applies to anything that remains a fixed multi-step job outside the chat.
