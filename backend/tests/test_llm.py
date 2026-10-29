"""Chat provider selection and model construction (ADR-0012)."""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage

from portolan.agent.llm import AgentUnavailable, build_chat_model, unavailable_reason
from portolan.settings import Settings

_CHAT_ENV = (
    "PORTOLAN_STORE",
    "PORTOLAN_CHAT_PROVIDER",
    "PORTOLAN_CHAT_MODEL",
    "PORTOLAN_CHAT_EFFORT",
    "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
)


@pytest.fixture
def chat_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in _CHAT_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PORTOLAN_STORE", "memory")
    return monkeypatch


def _payload(settings: Settings) -> dict:
    model = build_chat_model(settings, "ask")
    return model._get_request_payload([HumanMessage(content="hi")])  # type: ignore[attr-defined]


def test_default_model_follows_provider() -> None:
    assert Settings(store="memory").chat_model == "deepseek/deepseek-v4-pro"
    assert Settings(store="memory", chat_provider="anthropic").chat_model == "claude-haiku-5-5"
    explicit = Settings(store="memory", chat_provider="anthropic", chat_model="claude-sonnet-5-5")
    assert explicit.chat_model == "claude-sonnet-5-5"


def test_settings_reject_unknown_provider_and_effort() -> None:
    with pytest.raises(ValueError, match="chat_provider"):
        Settings(store="memory", chat_provider="openai")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="chat_effort"):
        Settings(store="memory", chat_effort="extreme")


@pytest.mark.parametrize(
    ("env", "provider"),
    [
        ({}, "openrouter"),
        ({"ANTHROPIC_API_KEY": "a"}, "anthropic"),
        ({"OPENROUTER_API_KEY": "o", "ANTHROPIC_API_KEY": "a"}, "openrouter"),
        ({"OPENROUTER_API_KEY": "o", "PORTOLAN_CHAT_PROVIDER": "Anthropic"}, "anthropic"),
    ],
)
def test_from_env_resolves_provider(
    chat_env: pytest.MonkeyPatch, env: dict[str, str], provider: str
) -> None:
    for name, value in env.items():
        chat_env.setenv(name, value)
    assert Settings.from_env().chat_provider == provider


def test_from_env_reads_anthropic_settings(chat_env: pytest.MonkeyPatch) -> None:
    chat_env.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    chat_env.setenv("ANTHROPIC_BASE_URL", "https://proxy.example/anthropic")
    chat_env.setenv("PORTOLAN_CHAT_EFFORT", "XHIGH")
    settings = Settings.from_env()
    assert settings.anthropic_base_url == "https://proxy.example/anthropic"
    assert settings.chat_effort == "xhigh"
    description = settings.describe()
    assert "chat_provider=anthropic" in description
    assert "anthropic_key=set" in description
    assert "anthropic-secret" not in description


def test_from_env_rejects_unknown_provider(chat_env: pytest.MonkeyPatch) -> None:
    chat_env.setenv("PORTOLAN_CHAT_PROVIDER", "bedrock")
    with pytest.raises(ValueError, match="PORTOLAN_CHAT_PROVIDER"):
        Settings.from_env()


def test_missing_key_names_the_provider_variable() -> None:
    openrouter = Settings(store="memory")
    anthropic = Settings(store="memory", chat_provider="anthropic", openrouter_api_key="o")
    assert unavailable_reason(openrouter, "ask") == "Ask mode needs OPENROUTER_API_KEY"
    assert unavailable_reason(anthropic, "research") == "Research mode needs ANTHROPIC_API_KEY"
    with pytest.raises(AgentUnavailable, match="ANTHROPIC_API_KEY"):
        build_chat_model(anthropic, "ask")


def test_anthropic_default_request_shape() -> None:
    payload = _payload(Settings(store="memory", chat_provider="anthropic", anthropic_api_key="a"))
    assert payload["model"] == "claude-haiku-5-5"
    assert payload["output_config"] == {"effort": "high"}
    assert payload["thinking"]["type"] == "adaptive"
    assert "temperature" not in payload
    # Haiku 5.5 has no server-side refusal fallback.
    assert "fallbacks" not in payload
    assert not payload.get("betas")


def test_anthropic_fallback_on_supported_models() -> None:
    payload = _payload(
        Settings(
            store="memory",
            chat_provider="anthropic",
            anthropic_api_key="a",
            chat_model="claude-opus-5-5",
            chat_effort="low",
        )
    )
    assert payload["model"] == "claude-opus-5-5"
    assert payload["output_config"] == {"effort": "low"}
    assert payload["fallbacks"] == "default"
    assert payload["betas"] == ["server-side-fallback-2026-07-01"]


@pytest.mark.parametrize("model_id", ["claude-haiku-5-5", "claude-opus-5-5"])
def test_anthropic_structured_output_avoids_forced_tool_choice(model_id: str) -> None:
    from langchain.agents.factory import _supports_provider_strategy

    settings = Settings(
        store="memory", chat_provider="anthropic", anthropic_api_key="a", chat_model=model_id
    )
    model = build_chat_model(settings, "ask")
    assert _supports_provider_strategy(model, tools=[{"name": "search_papers"}])


def test_openrouter_model_is_unchanged() -> None:
    from langchain_openai import ChatOpenAI

    model = build_chat_model(Settings(store="memory", openrouter_api_key="o"), "ask")
    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "deepseek/deepseek-v4-pro"
    assert model.openai_api_base == "https://openrouter.ai/api/v1"
    assert model.temperature == 0
