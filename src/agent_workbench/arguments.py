"""Command-line arguments and runtime configuration resolution."""

from argparse import ArgumentParser, ArgumentTypeError
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import cast
from pathlib import Path

from agent_workbench.agents import (
    SUPPORTED_AGENT_NAMES,
    AgentProfile,
    get_agent_profile,
    load_agent_profile_file,
)

from agent_workbench.config import (
    SUPPORTED_PROVIDERS,
    ProviderName,
    get_model_name,
    get_provider_name,
)
from agent_workbench.context import ContextDocument, load_context_document
from agent_workbench.errors import ConfigurationError
from agent_workbench.generation import GenerationConfig


@dataclass(frozen=True, slots=True)
class CLIArguments:
    """Represent optional configuration supplied through the CLI."""

    provider_name: ProviderName | None
    model_name: str | None
    system_prompt: str | None = None
    agent_name: str | None = None
    agent_file: Path | None = None
    context_files: tuple[Path, ...] = ()
    temperature: float | None = None
    top_p: float | None = None
    max_output_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class RuntimeConfiguration:
    """Represent the resolved provider and model configuration."""

    provider_name: ProviderName
    model_name: str
    system_prompt: str | None = None
    agent_profile: AgentProfile | None = None
    context_documents: tuple[ContextDocument, ...] = ()
    generation_config: GenerationConfig = field(default_factory=GenerationConfig)


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


def _agent_profile_path(value: str) -> Path:
    """Return a normalized custom agent profile path."""

    normalized_path = value.strip()

    if not normalized_path:
        raise ArgumentTypeError("agent file path must not be blank")

    return Path(normalized_path).expanduser()


def _context_file_path(value: str) -> Path:
    """Return a normalized context file path."""

    normalized_path = value.strip()

    if not normalized_path:
        raise ArgumentTypeError("context file path must not be blank")

    return Path(normalized_path).expanduser()


def _unit_interval(
    value: str,
    *,
    argument_name: str,
) -> float:
    """Parse a numeric command-line value between zero and one."""

    try:
        parsed_value = float(value)
    except ValueError as exc:
        raise ArgumentTypeError(
            f"{argument_name} must be a number between 0.0 and 1.0"
        ) from exc

    if not 0.0 <= parsed_value <= 1.0:
        raise ArgumentTypeError(f"{argument_name} must be a number between 0.0 and 1.0")

    return parsed_value


def _temperature(value: str) -> float:
    """Parse a portable temperature value."""

    return _unit_interval(
        value,
        argument_name="temperature",
    )


def _top_p(value: str) -> float:
    """Parse a portable top-p value."""

    return _unit_interval(
        value,
        argument_name="top-p",
    )


def _positive_output_token_limit(value: str) -> int:
    """Parse a positive maximum output token count."""

    try:
        parsed_value = int(value)
    except ValueError as exc:
        raise ArgumentTypeError("max output tokens must be a positive integer") from exc

    if parsed_value <= 0:
        raise ArgumentTypeError("max output tokens must be a positive integer")

    return parsed_value


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

    parser.add_argument(
        "--agent-file",
        type=_agent_profile_path,
        help="Path to a custom TOML agent profile.",
    )

    parser.add_argument(
        "--context-file",
        action="append",
        type=_context_file_path,
        default=[],
        help="Path to a context file. May be supplied multiple times.",
    )

    parser.add_argument(
        "--temperature",
        type=_temperature,
        help="Sampling temperature between 0.0 and 1.0.",
    )

    parser.add_argument(
        "--top-p",
        type=_top_p,
        help="Nucleus sampling probability between 0.0 and 1.0.",
    )

    parser.add_argument(
        "--max-output-tokens",
        type=_positive_output_token_limit,
        help="Maximum number of tokens generated in each response.",
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
        agent_file=parsed_arguments.agent_file,
        context_files=tuple(parsed_arguments.context_file),
        temperature=parsed_arguments.temperature,
        top_p=parsed_arguments.top_p,
        max_output_tokens=parsed_arguments.max_output_tokens,
    )


def resolve_runtime_configuration(
    arguments: CLIArguments,
) -> RuntimeConfiguration:
    """Resolve CLI overrides against environment-based configuration."""

    if arguments.provider_name is not None and arguments.model_name is None:
        raise ConfigurationError("--model is required when --provider is specified.")

    if arguments.agent_name is not None and arguments.agent_file is not None:
        raise ConfigurationError("--agent cannot be combined with --agent-file.")

    if arguments.agent_name is not None and arguments.system_prompt is not None:
        raise ConfigurationError("--agent cannot be combined with --system-prompt.")

    if arguments.agent_file is not None and arguments.system_prompt is not None:
        raise ConfigurationError(
            "--agent-file cannot be combined with --system-prompt."
        )

    if arguments.agent_file is not None:
        agent_profile = load_agent_profile_file(arguments.agent_file)
    elif arguments.agent_name is not None:
        agent_profile = get_agent_profile(arguments.agent_name)
    else:
        agent_profile = None

    system_prompt = (
        agent_profile.system_prompt
        if agent_profile is not None
        else arguments.system_prompt
    )

    context_documents = tuple(
        load_context_document(path) for path in arguments.context_files
    )

    generation_config = GenerationConfig(
        temperature=arguments.temperature,
        top_p=arguments.top_p,
        max_output_tokens=arguments.max_output_tokens,
    )

    provider_name = arguments.provider_name or get_provider_name()
    model_name = arguments.model_name or get_model_name(provider_name)

    return RuntimeConfiguration(
        provider_name=provider_name,
        model_name=model_name,
        system_prompt=system_prompt,
        agent_profile=agent_profile,
        context_documents=context_documents,
        generation_config=generation_config,
    )
