"""Command-line arguments and runtime configuration resolution."""

from argparse import ArgumentParser, ArgumentTypeError
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from agent_workbench.agents import (
    SUPPORTED_AGENT_NAMES,
    AgentProfile,
    get_agent_profile,
)

from agent_workbench.config import (
    SUPPORTED_PROVIDERS,
    ProviderName,
    get_model_name,
    get_provider_name,
)
from agent_workbench.errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class CLIArguments:
    """Represent optional configuration supplied through the CLI."""

    provider_name: ProviderName | None
    model_name: str | None
    system_prompt: str | None = None
    agent_name: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeConfiguration:
    """Represent the resolved provider and model configuration."""

    provider_name: ProviderName
    model_name: str
    system_prompt: str | None = None
    agent_profile: AgentProfile | None = None


def _non_empty_model_name(value: str) -> str:
    """Return a normalized model name or reject a blank value."""

    model_name = value.strip()

    if not model_name:
        raise ArgumentTypeError("model name must not be blank")

    return model_name


def _non_empty_system_prompt(value: str) -> str:
    """Return a normalized system prompt or reject a blank value."""

    system_prompt = value.strip()

    if not system_prompt:
        raise ArgumentTypeError("system prompt must not be blank")

    return system_prompt


def parse_cli_arguments(
    argv: Sequence[str] | None = None,
) -> CLIArguments:
    """Parse optional provider and model command-line arguments."""

    parser = ArgumentParser(
        prog="agent-workbench",
        description=(
            "Start an interactive conversation using a configured "
            "language model provider."
        ),
    )
    parser.add_argument(
        "--provider",
        choices=sorted(SUPPORTED_PROVIDERS),
        help="Language model provider to use.",
    )
    parser.add_argument(
        "--model",
        type=_non_empty_model_name,
        help="Provider-specific model name.",
    )

    parser.add_argument(
        "--system-prompt",
        type=_non_empty_system_prompt,
        help="Instructions that define the assistant's role and behavior.",
    )

    parser.add_argument(
        "--agent",
        choices=sorted(SUPPORTED_AGENT_NAMES),
        help="Reusable agent profile to activate.",
    )
    parsed_arguments = parser.parse_args(argv)

    provider_name = (
        cast(ProviderName, parsed_arguments.provider)
        if parsed_arguments.provider is not None
        else None
    )

    return CLIArguments(
        provider_name=provider_name,
        model_name=parsed_arguments.model,
        system_prompt=parsed_arguments.system_prompt,
        agent_name=parsed_arguments.agent,
    )


def resolve_runtime_configuration(
    arguments: CLIArguments,
) -> RuntimeConfiguration:
    """Resolve CLI overrides against environment-based configuration."""

    if arguments.provider_name is not None and arguments.model_name is None:
        raise ConfigurationError("--model is required when --provider is specified.")

    if arguments.agent_name is not None and arguments.system_prompt is not None:
        raise ConfigurationError("--agent cannot be combined with --system-prompt.")

    agent_profile = (
        get_agent_profile(arguments.agent_name)
        if arguments.agent_name is not None
        else None
    )

    system_prompt = (
        agent_profile.system_prompt
        if agent_profile is not None
        else arguments.system_prompt
    )

    provider_name = arguments.provider_name or get_provider_name()
    model_name = arguments.model_name or get_model_name(provider_name)

    return RuntimeConfiguration(
        provider_name=provider_name,
        model_name=model_name,
        system_prompt=system_prompt,
        agent_profile=agent_profile,
    )
