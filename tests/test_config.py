"""Tests for Agent Workbench configuration."""

import os
from pathlib import Path
import pytest

from agent_workbench.config import (
    DEFAULT_MODEL_NAME,
    DEFAULT_PROVIDER_NAME,
    MODEL_ENV_VAR,
    PROVIDER_ENV_VAR,
    get_model_name,
    get_provider_name,
    load_environment,
)
from agent_workbench.errors import ConfigurationError


def test_default_model_is_used_when_variable_is_missing(monkeypatch) -> None:
    """Use the default model when no environment variable is configured."""

    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)

    assert get_model_name() == DEFAULT_MODEL_NAME


def test_model_can_be_configured_through_environment(monkeypatch) -> None:
    """Read the model name from the environment."""

    monkeypatch.setenv(MODEL_ENV_VAR, "custom-local-model")

    assert get_model_name() == "custom-local-model"


def test_blank_model_uses_default(monkeypatch) -> None:
    """Ignore an environment variable containing only whitespace."""

    monkeypatch.setenv(MODEL_ENV_VAR, "   ")

    assert get_model_name() == DEFAULT_MODEL_NAME


def test_default_provider_is_used_when_variable_is_missing(monkeypatch) -> None:
    """Use Ollama when no provider is configured."""

    monkeypatch.delenv(PROVIDER_ENV_VAR, raising=False)

    assert get_provider_name() == DEFAULT_PROVIDER_NAME


def test_provider_can_be_configured_through_environment(monkeypatch) -> None:
    """Read and normalize the provider name from the environment."""

    monkeypatch.setenv(PROVIDER_ENV_VAR, " OpenAI ")

    assert get_provider_name() == "openai"


def test_unsupported_provider_is_rejected(monkeypatch) -> None:
    """Reject provider names that the application does not support."""

    monkeypatch.setenv(PROVIDER_ENV_VAR, "unsupported")

    with pytest.raises(
        ConfigurationError,
        match="Unsupported provider 'unsupported'",
    ):
        get_provider_name()


def test_openai_requires_an_explicit_model(monkeypatch) -> None:
    """Require explicit model selection for the OpenAI provider."""

    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)

    with pytest.raises(
        ConfigurationError,
        match="AGENT_WORKBENCH_MODEL is required",
    ):
        get_model_name("openai")


def test_environment_file_is_loaded(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Load variables from a local environment file."""

    environment_file = tmp_path / ".env"
    environment_file.write_text(
        "OPENAI_API_KEY=file-api-key\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    load_environment(environment_file)

    assert os.getenv("OPENAI_API_KEY") == "file-api-key"


def test_environment_file_does_not_override_existing_variables(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Preserve variables already defined by the runtime environment."""

    environment_file = tmp_path / ".env"
    environment_file.write_text(
        "OPENAI_API_KEY=file-api-key\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("OPENAI_API_KEY", "runtime-api-key")

    load_environment(environment_file)

    assert os.getenv("OPENAI_API_KEY") == "runtime-api-key"


def test_get_provider_name_accepts_anthropic(
    monkeypatch,
) -> None:
    """Accept Anthropic as a supported provider."""

    monkeypatch.setenv(
        PROVIDER_ENV_VAR,
        " Anthropic ",
    )

    assert get_provider_name() == "anthropic"


def test_get_model_name_requires_explicit_anthropic_model(
    monkeypatch,
) -> None:
    """Require explicit model selection for Anthropic."""

    monkeypatch.delenv(
        MODEL_ENV_VAR,
        raising=False,
    )

    with pytest.raises(
        ConfigurationError,
        match=MODEL_ENV_VAR,
    ):
        get_model_name("anthropic")
