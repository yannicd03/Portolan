"""Chat model construction for the configured LLM provider (ADR-0004, ADR-0012, ADR-0013)."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from ..settings import Settings

# Models that accept the server-side refusal fallback in its "default" form.
_FALLBACK_MODELS = frozenset(
    {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
)
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AgentUnavailable(RuntimeError):
    """A chat mode cannot run with the current configuration."""


class ChatKit(ChatOpenAI):
    """``ChatOpenAI`` for the KIT ki-toolbox gateway (ADR-0013).

    Two adjustments, both found against the live gateway:

    * A forced tool choice becomes ``auto``. LangChain forces one so that the agent ends on its
      ``AskAnswer`` tool, but with it KIT's models kept calling tools without ever answering.
    * System prompts go out as one plain string. The gateway keeps only the first block of a
      list-style system message and silently drops the rest, which is the shape Deep Agents'
      ``append_to_system_message`` helper builds.
    """

    def _get_request_payload(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        payload = super()._get_request_payload(*args, **kwargs)
        if payload.get("tool_choice") == "required":
            payload["tool_choice"] = "auto"
        for message in payload.get("messages", []):
            content = message.get("content")
            if message.get("role") in ("system", "developer") and isinstance(content, list):
                message["content"] = "\n\n".join(
                    block["text"] if isinstance(block, dict) else str(block) for block in content
                )
        return payload


def missing_key(settings: Settings) -> str | None:
    """Return the environment variable the configured provider still needs, if any."""

    if settings.chat_provider == "anthropic":
        return None if settings.anthropic_api_key else "ANTHROPIC_API_KEY"
    if settings.chat_provider == "kit":
        return None if settings.kit_api_key else "KIT_API_KEY"
    return None if settings.openrouter_api_key else "OPENROUTER_API_KEY"


def unavailable_reason(settings: Settings, mode: str) -> str | None:
    """Explain why a chat mode cannot run, or return None when it can."""

    key = missing_key(settings)
    return f"{mode.title()} mode needs {key}" if key else None


def build_chat_model(settings: Settings, mode: str) -> BaseChatModel:
    """Build the chat model for the configured provider and model id."""

    if reason := unavailable_reason(settings, mode):
        raise AgentUnavailable(reason)
    if settings.chat_provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        options: dict[str, Any] = {}
        if settings.chat_model in _FALLBACK_MODELS:
            # A refused turn is retried server-side on a substitute model.
            options["betas"] = [_FALLBACK_BETA]
            options["model_kwargs"] = {"fallbacks": "default"}
        if settings.anthropic_base_url:
            options["base_url"] = settings.anthropic_base_url
        # No temperature: current Claude models reject sampling parameters. Streaming keeps
        # long agent turns clear of the SDK's non-streaming request timeout.
        model = ChatAnthropic(
            model=settings.chat_model,
            api_key=settings.anthropic_api_key,
            max_tokens=64_000,
            streaming=True,
            thinking={"type": "adaptive"},
            effort=settings.chat_effort,
            **options,
        )
        # Current Claude models all support native structured output, but langchain-anthropic
        # 1.7.x has no (or a stale) profile for some, e.g. claude-haiku-5-5. Without the flag
        # the agent forces a tool call on every turn, which makes the model skip thinking.
        profile = {**(model.profile or {}), "structured_output": True}
        return model.model_copy(update={"profile": profile})

    if settings.chat_provider == "kit":
        # Long non-streaming responses lose the connection on this gateway.
        # No native structured output here: with response_format set, KIT's models answered
        # straight away without using a single tool.
        return ChatKit(
            model=settings.chat_model,
            base_url=settings.kit_base_url,
            api_key=settings.kit_api_key,
            temperature=0,
            streaming=True,
            timeout=600,
        )

    return ChatOpenAI(
        model=settings.chat_model,
        base_url=settings.openrouter_base_url,
        api_key=settings.openrouter_api_key,
        temperature=0,
    )
