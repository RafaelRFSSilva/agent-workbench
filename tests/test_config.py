"""Tests for Agent Workbench configuration."""

from agent_workbench.config import (
    DEFAULT_MODEL_NAME,
    MODEL_ENV_VAR,
    get_model_name,
)


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
