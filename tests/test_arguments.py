"""Tests for command-line configuration handling."""

import pytest

from agent_workbench.arguments import (
    CLIArguments,
    parse_cli_arguments,
    resolve_runtime_configuration,
)
from agent_workbench.config import MODEL_ENV_VAR, PROVIDER_ENV_VAR
from agent_workbench.errors import ConfigurationError


def test_parse_cli_arguments_accepts_provider_and_model() -> None:
    """Parse explicit provider and model arguments."""

    arguments = parse_cli_arguments(
        [
            "--provider",
            "ollama",
            "--model",
            "gpt-oss:20b",
        ]
    )

    assert arguments.provider_name == "ollama"
    assert arguments.model_name == "gpt-oss:20b"
    assert arguments.system_prompt is None


def test_parse_cli_arguments_rejects_unsupported_provider() -> None:
    """Reject providers outside the supported provider set."""

    with pytest.raises(SystemExit) as exc_info:
        parse_cli_arguments(
            [
                "--provider",
                "unsupported",
                "--model",
                "test-model",
            ]
        )

    assert exc_info.value.code == 2


def test_cli_configuration_overrides_environment(
    monkeypatch,
) -> None:
    """Give explicit CLI configuration priority over the environment."""

    monkeypatch.setenv(PROVIDER_ENV_VAR, "anthropic")
    monkeypatch.setenv(
        MODEL_ENV_VAR,
        "claude-haiku-4-5-20251001",
    )

    configuration = resolve_runtime_configuration(
        CLIArguments(
            provider_name="ollama",
            model_name="gpt-oss:20b",
        )
    )

    assert configuration.provider_name == "ollama"
    assert configuration.model_name == "gpt-oss:20b"


def test_provider_argument_requires_model_argument() -> None:
    """Prevent a provider override from reusing another provider's model."""

    with pytest.raises(
        ConfigurationError,
        match="--model is required",
    ):
        resolve_runtime_configuration(
            CLIArguments(
                provider_name="ollama",
                model_name=None,
            )
        )


def test_model_argument_uses_environment_provider(
    monkeypatch,
) -> None:
    """Allow a model override for the configured provider."""

    monkeypatch.setenv(PROVIDER_ENV_VAR, "openai")

    configuration = resolve_runtime_configuration(
        CLIArguments(
            provider_name=None,
            model_name="openai-test-model",
        )
    )

    assert configuration.provider_name == "openai"
    assert configuration.model_name == "openai-test-model"


def test_environment_is_used_without_cli_arguments(
    monkeypatch,
) -> None:
    """Use environment configuration when no CLI overrides are supplied."""

    monkeypatch.setenv(PROVIDER_ENV_VAR, "anthropic")
    monkeypatch.setenv(
        MODEL_ENV_VAR,
        "claude-test-model",
    )

    configuration = resolve_runtime_configuration(
        CLIArguments(
            provider_name=None,
            model_name=None,
        )
    )

    assert configuration.provider_name == "anthropic"
    assert configuration.model_name == "claude-test-model"


def test_parse_cli_arguments_accepts_system_prompt() -> None:
    """Parse and normalize an explicit system prompt."""

    arguments = parse_cli_arguments(
        [
            "--provider",
            "ollama",
            "--model",
            "gpt-oss:20b",
            "--system-prompt",
            "  You are a strict software reviewer.  ",
        ]
    )

    assert arguments.system_prompt == "You are a strict software reviewer."


def test_parse_cli_arguments_rejects_blank_system_prompt() -> None:
    """Reject system prompts containing only whitespace."""

    with pytest.raises(SystemExit) as exc_info:
        parse_cli_arguments(
            [
                "--system-prompt",
                "   ",
            ]
        )

    assert exc_info.value.code == 2


def test_parse_cli_arguments_accepts_agent_profile() -> None:
    """Parse a reusable agent profile."""

    arguments = parse_cli_arguments(
        [
            "--agent",
            "reviewer",
        ]
    )

    assert arguments.agent_name == "reviewer"


def test_parse_cli_arguments_rejects_unknown_agent_profile() -> None:
    """Reject agent profiles outside the registered collection."""

    with pytest.raises(SystemExit) as exc_info:
        parse_cli_arguments(
            [
                "--agent",
                "unknown",
            ]
        )

    assert exc_info.value.code == 2


def test_agent_profile_provides_system_prompt(
    monkeypatch,
) -> None:
    """Resolve the selected profile into its system instructions."""

    monkeypatch.setenv(PROVIDER_ENV_VAR, "ollama")
    monkeypatch.setenv(MODEL_ENV_VAR, "gpt-oss:20b")

    configuration = resolve_runtime_configuration(
        CLIArguments(
            provider_name=None,
            model_name=None,
            agent_name="reviewer",
        )
    )

    assert configuration.agent_profile is not None
    assert configuration.agent_profile.name == "Reviewer"
    assert configuration.system_prompt == (configuration.agent_profile.system_prompt)


def test_agent_and_system_prompt_cannot_be_combined() -> None:
    """Reject ambiguous simultaneous agent and prompt configuration."""

    with pytest.raises(
        ConfigurationError,
        match="--agent cannot be combined",
    ):
        resolve_runtime_configuration(
            CLIArguments(
                provider_name="ollama",
                model_name="gpt-oss:20b",
                system_prompt="Custom instructions.",
                agent_name="reviewer",
            )
        )
