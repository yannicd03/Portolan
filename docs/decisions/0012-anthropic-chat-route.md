# ADR-0012 — direct Anthropic route for the chat agents

- **Status:** accepted
- **Date:** 2026-10-28
- **Amends:** ADR-0004 (OpenRouter as the only gateway)
- **Related:** ADR-0007 (chat agents on Deep Agents), Linear YAN-325

## Context

ADR-0004 routes every LLM call through OpenRouter, and the Ask and Research chat agents
(ADR-0007) built a `ChatOpenAI` client against it in two places. Running the agents on Claude
through OpenRouter loses Anthropic-native features that the agents benefit from: adaptive
thinking with an explicit effort level, prompt caching (Deep Agents adds
`AnthropicPromptCachingMiddleware` only for a `ChatAnthropic` model), native structured output
for `AskAnswer`, and the server-side refusal fallback. An Anthropic key is also a second way to
unblock chat when no OpenRouter key is available.

## Decision

- **Two chat routes, chosen by config.** `PORTOLAN_CHAT_PROVIDER` is `openrouter` (default,
  unchanged behaviour) or `anthropic`. When it is unset and `ANTHROPIC_API_KEY` is the only chat
  key present, the Anthropic route is used. `agent/llm.py` is the one place that builds the chat
  model; both agents and the chat API's availability checks go through it.
- **Anthropic client:** `langchain-anthropic`'s `ChatAnthropic` (the official `anthropic` SDK
  underneath), default model `claude-haiku-5-5`, overridable with `PORTOLAN_CHAT_MODEL`.
  Haiku is the low-cost default; `claude-sonnet-5-5` or `claude-opus-5-5` are one variable away.
  - Adaptive thinking with `output_config.effort` from `PORTOLAN_CHAT_EFFORT` (default `high`;
    Haiku 5.5's own default is `medium`, and grounded, cited answers are intelligence-sensitive).
  - No `temperature`: current Claude models reject sampling parameters.
  - Streaming with `max_tokens` 64,000, so long agent turns stay clear of the SDK's
    non-streaming timeout.
  - `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`) on the models that accept it
    (Fable 5.1, Opus 5.5, Opus 5, Sonnet 5.5), so a safety-classifier refusal is retried on a
    substitute model instead of ending the turn without an answer. Haiku 5.5 has no server-side
    fallback; a refused turn ends with an error.
  - Structured output goes through the provider strategy (`output_config.format`), never a
    forced `tool_choice`: Opus 5.5 and Fable 5.1 reject a forced call, and Haiku 5.5 skips
    thinking on one. langchain-anthropic 1.7.4 has no profile for `claude-haiku-5-5`, so
    `agent/llm.py` sets the model profile's `structured_output` flag itself; without it
    LangChain would force a tool call on every agent turn.
- **OpenRouter stays the default** and the route for ADR-0004's cost tiers. Nothing in the
  harvest pipeline changes.

## Consequences

- `GET /api/chat/status` returns `provider` alongside `model`, and the "unavailable" reason names
  the key the configured provider needs (`OPENROUTER_API_KEY` or `ANTHROPIC_API_KEY`).
- The recorded answer `model` is the configured model id. A refusal served by a fallback model is
  not distinguished yet.
- Cost: Haiku 5.5 is $0.10 / $0.50 per million tokens (input / output) for prompts up to 100K
  tokens and $0.50 / $2.50 above that. A long Ask turn that has read several papers can cross
  100K. Opus 5.5 would be $4 / $20. Chat turns are not on ADR-0004's per-review budget ledger, which is itself not
  enforced in code yet.
- Ask history is replayed as plain text (no thinking blocks), so the preserved-thinking
  history check has nothing to reject across turns. Within a turn the agent loop is append-only.
