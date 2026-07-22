"""Tests for provider construction."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_workbench.errors import ConfigurationError
from agent_workbench.providers.factory import create_provider
from agent_workbench.providers.ollama import OllamaProvider
from agent_workbench.providers.openai import OpenAIProvider
from agent_workbench.providers.anthropic import AnthropicProvider


def test_create_ollama_provider() -> None:
    """Create an Ollama provider with the configured model."""

    provider = create_provider(
        provider_name="ollama",
        model_name="local-test-model",
    )

    assert isinstance(provider, OllamaProvider)
    assert provider.model_name == "local-test-model"


def test_openai_provider_requires_api_key(monkeypatch) -> None:
    """Reject OpenAI configuration without an API key."""

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(
        ConfigurationError,
        match="OPENAI_API_KEY is required",
    ):
        create_provider(
            provider_name="openai",
            model_name="cloud-test-model",
        )


def test_create_openai_provider(monkeypatch) -> None:
    """Create an OpenAI provider using the SDK client."""

    fake_client = SimpleNamespace()
    client_factory = Mock(return_value=fake_client)

    monkeypatch.setenv("OPENAI_API_KEY", "test-api-key")
    monkeypatch.setattr(
        "agent_workbench.providers.factory.OpenAI",
        client_factory,
    )

    provider = create_provider(
        provider_name="openai",
        model_name="cloud-test-model",
    )

    assert isinstance(provider, OpenAIProvider)
    assert provider.model_name == "cloud-test-model"
    assert provider.client is fake_client
    client_factory.assert_called_once_with()


def test_create_anthropic_provider_requires_api_key(
    monkeypatch,
) -> None:
    """Reject Anthropic configuration without an API key."""

    monkeypatch.delenv(
        "ANTHROPIC_API_KEY",
        raising=False,
    )

    with pytest.raises(
        ConfigurationError,
        match="ANTHROPIC_API_KEY",
    ):
        create_provider(
            "anthropic",
            "claude-test",
        )


def test_create_anthropic_provider(
    monkeypatch,
) -> None:
    """Create Anthropic with the configured SDK client."""

    client = SimpleNamespace()
    client_factory = Mock(return_value=client)

    monkeypatch.setenv(
        "ANTHROPIC_API_KEY",
        "test-api-key",
    )
    monkeypatch.setattr(
        "agent_workbench.providers.factory.Anthropic",
        client_factory,
    )

    provider = create_provider(
        "anthropic",
        "claude-test",
    )

    assert isinstance(provider, AnthropicProvider)
    assert provider.model_name == "claude-test"
    assert provider.client is client
    client_factory.assert_called_once_with()
