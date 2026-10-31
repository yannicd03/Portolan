# ADR-0013 — KIT ki-toolbox as an opt-in chat route, Claude as the default

- **Status:** accepted
- **Date:** 2026-11-01
- **Amends:** ADR-0012 (OpenRouter as the default chat route)
- **Related:** ADR-0004 (LLM access), ADR-0007 (chat agents on Deep Agents)

## Context

A KIT ki-toolbox key gives access to an OpenAI-compatible gateway with locally hosted models and
a per-user monthly budget. It is a useful second route for the Ask and Research chat agents, but
the Claude route is the better tested of the two and should stay in charge for now.

## Decision

- **Claude is the default.** When `PORTOLAN_CHAT_PROVIDER` is unset, chat runs on the Anthropic
  route unless `OPENROUTER_API_KEY` is the only one of those two keys set. With both set, chat
  now uses Claude; before this ADR it used OpenRouter. An explicit provider still wins.
- **KIT is opt-in.** `PORTOLAN_CHAT_PROVIDER=kit` selects it, with `KIT_API_KEY`. The key alone
  never selects the route, so adding it changes nothing.
- **Client:** `ChatKit` in `agent/llm.py`, a `ChatOpenAI` subclass pointed at `KIT_BASE_URL`
  (default `https://ki-toolbox.scc.kit.edu/api/v1`). It streams, because long non-streaming
  responses lose the connection on this gateway, and uses a 600 s timeout.
- **Default model:** `kit.deepseek-v4-flash`, overridable with `PORTOLAN_CHAT_MODEL`. It was the
  only model of the four tried that finished an Ask turn.
- **Forced tool choice becomes `auto`.** LangChain forces a tool call so the agent ends on its
  `AskAnswer` tool. On the gateway that kept `kit.mistral-small-4-119b-a8b` calling tools without
  answering, and `kit.deepseek-v4-flash` stalled on its first request.
- **No native structured output.** With `response_format` set, the models answered at once
  without using a tool: `kit.glm-5.3-flash` produced placeholder citations and
  `kit.deepseek-v4-flash` returned its plan as the answer.
- **System prompts are sent as one string.** The gateway keeps only the first block of a
  list-style system message. The Ask path already sends a plain string; this guards any path
  that goes through Deep Agents' `append_to_system_message`.

## Consequences

- The route works but is less reliable than Claude. Measured on the golden project with
  `kit.deepseek-v4-flash` on 2026-11-01:
  - Ask: two of three turns finished with verified citations. The third ended in plain text
    instead of `AskAnswer` and failed with "Ask mode returned no structured answer".
  - Research: one planning turn used its tools but ended in text before calling `run_research`,
    so it never reached the approval step.
- `kit.mistral-small-4-119b-a8b`, `kit.glm-5.3-flash` and `google.claude-haiku-5.5` ended Ask
  turns in plain text in the same way, so they do not work on this route yet.
- A bad answer is not produced by these failures: the turn ends with an error. Citations still go
  through `verify_citations`.
- The gateway caches whole responses per identical request, so repeating an identical request can
  replay the same result.
- Usage counts against the KIT account's budget, shared with every other project on that account.
- Deployments with both Anthropic and OpenRouter keys and no explicit provider move from OpenRouter
  to Claude.
