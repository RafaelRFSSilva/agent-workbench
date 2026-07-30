"""Command-line arguments and runtime configuration resolution."""

import sys
from argparse import ArgumentParser, ArgumentTypeError
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from agent_workbench.agents import (
    SUPPORTED_AGENT_NAMES,
    AgentProfile,
    get_agent_profile,
    load_agent_profile_file,
)

from agent_workbench.config import (
    SUPPORTED_PROVIDERS,
    ProjectConfiguration,
    ProviderName,
    get_model_name,
    get_provider_name,
)
from agent_workbench.context import ContextDocument, load_context_document
from agent_workbench.errors import ConfigurationError
from agent_workbench.generation import GenerationConfig

from agent_workbench.structured_outputs import (
    JSONResponseFormat,
    load_response_format_file,
)

DEFAULT_AUTONOMOUS_MAX_TOOL_ROUNDS = 16
"""Default maximum tool rounds for one autonomous CLI task."""


@dataclass(frozen=True, slots=True)
class CLIArguments:
    """Represent optional configuration supplied through the CLI."""

    provider_name: ProviderName | None
    model_name: str | None
    setup: bool = False
    task_prompt: str | None = None
    max_tool_rounds: int | None = None
    commit_message: str | None = None
    system_prompt: str | None = None
    agent_name: str | None = None
    agent_file: Path | None = None
    context_files: tuple[Path, ...] = ()
    temperature: float | None = None
    top_p: float | None = None
    max_output_tokens: int | None = None
    response_format_file: Path | None = None
    enable_tools: bool = False
    workspace_root: Path | None = None
    enable_actions: bool = False
    show_tool_traces: bool = False
    show_assistant_summary: bool = False
    worktree_path: Path | None = None
    worktree_branch: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeConfiguration:
    """Represent the resolved provider and model configuration."""

    provider_name: ProviderName
    model_name: str
    system_prompt: str | None = None
    agent_profile: AgentProfile | None = None
    context_documents: tuple[ContextDocument, ...] = ()
    generation_config: GenerationConfig = field(default_factory=GenerationConfig)
    response_format: JSONResponseFormat | None = None
    enable_tools: bool = False
    workspace_root: Path | None = None
    enable_actions: bool = False
    show_tool_traces: bool = False
    show_assistant_summary: bool = False
    max_tool_rounds: int = DEFAULT_AUTONOMOUS_MAX_TOOL_ROUNDS
    worktree_path: Path | None = None
    worktree_branch: str | None = None
    isolated: bool = False


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


def _non_empty_task_prompt(value: str) -> str:
    """Return a normalized autonomous task prompt or reject a blank value."""

    task_prompt = value.strip()

    if not task_prompt:
        raise ArgumentTypeError("task prompt must not be blank")

    return task_prompt


def _positive_tool_round_limit(value: str) -> int:
    """Parse a positive autonomous tool-round limit."""

    try:
        parsed_value = int(value)
    except ValueError as exc:
        raise ArgumentTypeError("max tool rounds must be a positive integer") from exc

    if parsed_value <= 0:
        raise ArgumentTypeError("max tool rounds must be a positive integer")

    return parsed_value


def _non_empty_commit_message(value: str) -> str:
    """Return one exact non-blank isolated commit message."""

    if not value.strip():
        raise ArgumentTypeError("commit message must not be blank")

    return value


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


def _response_format_file_path(value: str) -> Path:
    """Return a normalized response format file path."""

    normalized_path = value.strip()

    if not normalized_path:
        raise ArgumentTypeError("response format file path must not be blank")

    return Path(normalized_path).expanduser()


def _workspace_path(value: str) -> Path:
    """Return a normalized workspace root path."""

    normalized_path = value.strip()

    if not normalized_path:
        raise ArgumentTypeError("workspace path must not be blank")

    return Path(normalized_path).expanduser()


def _worktree_branch(value: str) -> str:
    """Return one exact non-blank worktree branch name."""

    if not value.strip():
        raise ArgumentTypeError("worktree branch must not be blank")

    return value


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


def _normalize_cli_argv(
    argv: Sequence[str] | None,
) -> tuple[list[str], bool]:
    """Remove the optional code command before normal argument parsing."""

    normalized_argv = list(sys.argv[1:] if argv is None else argv)
    code_command = bool(normalized_argv and normalized_argv[0] == "code")

    if code_command:
        normalized_argv = normalized_argv[1:]

    return normalized_argv, code_command


def parse_cli_arguments(
    argv: Sequence[str] | None = None,
) -> CLIArguments:
    """Parse optional provider and model command-line arguments."""

    normalized_argv, code_command = _normalize_cli_argv(argv)
    parser = ArgumentParser(
        prog="agent-workbench code" if code_command else "agent-workbench",
        description=(
            "Run one supervised autonomous coding task."
            if code_command
            else (
                "Start an interactive conversation or run one supervised "
                "autonomous coding task."
            )
        ),
    )

    parser.add_argument(
        "--setup",
        action="store_true",
        help="Configure the session through an interactive setup flow.",
    )
    parser.add_argument(
        "--task",
        type=_non_empty_task_prompt,
        help="Run one supervised autonomous coding task and exit.",
    )
    parser.add_argument(
        "--max-tool-rounds",
        type=_positive_tool_round_limit,
        help=(
            "Maximum tool execution rounds for one autonomous task; "
            f"defaults to {DEFAULT_AUTONOMOUS_MAX_TOOL_ROUNDS}."
        ),
    )
    parser.add_argument(
        "--commit-message",
        type=_non_empty_commit_message,
        help="Exact local commit message for isolated autonomous coding.",
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
        "--response-format-file",
        type=_response_format_file_path,
        help="Path to a JSON response format definition.",
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

    parser.add_argument(
        "--enable-tools",
        action="store_true",
        help="Enable the available built-in tools.",
    )

    parser.add_argument(
        "--workspace",
        type=_workspace_path,
        help="Authorized workspace root for controlled workspace tools.",
    )

    parser.add_argument(
        "--enable-actions",
        action="store_true",
        help=(
            "Enable approved file changes and fixed validation commands; "
            "requires --workspace."
        ),
    )

    parser.add_argument(
        "--show-tool-traces",
        action="store_true",
        help="Display completed tool calls and results during the session.",
    )
    parser.add_argument(
        "--show-assistant-summary",
        action="store_true",
        help="Display the model's complete final autonomous coding summary.",
    )

    parser.add_argument(
        "--worktree-path",
        type=_workspace_path,
        help="New isolated Git worktree target path; requires --worktree-branch.",
    )

    parser.add_argument(
        "--worktree-branch",
        type=_worktree_branch,
        help="New local branch for an isolated worktree; requires --worktree-path.",
    )

    parsed_arguments = parser.parse_args(normalized_argv)

    setup_conflicts = (
        parsed_arguments.provider is not None
        or parsed_arguments.model is not None
        or parsed_arguments.system_prompt is not None
        or parsed_arguments.agent is not None
        or parsed_arguments.agent_file is not None
        or bool(parsed_arguments.context_file)
        or parsed_arguments.temperature is not None
        or parsed_arguments.top_p is not None
        or parsed_arguments.max_output_tokens is not None
        or parsed_arguments.response_format_file is not None
        or parsed_arguments.enable_tools
        or parsed_arguments.workspace is not None
        or parsed_arguments.enable_actions
        or parsed_arguments.show_tool_traces
        or parsed_arguments.show_assistant_summary
        or parsed_arguments.worktree_path is not None
        or parsed_arguments.worktree_branch is not None
        or parsed_arguments.task is not None
        or parsed_arguments.max_tool_rounds is not None
        or parsed_arguments.commit_message is not None
    )

    if parsed_arguments.setup and setup_conflicts:
        parser.error("--setup cannot be combined with other configuration arguments.")

    provider_name = (
        cast(ProviderName, parsed_arguments.provider)
        if parsed_arguments.provider is not None
        else None
    )

    return CLIArguments(
        provider_name=provider_name,
        model_name=parsed_arguments.model,
        setup=parsed_arguments.setup,
        system_prompt=parsed_arguments.system_prompt,
        agent_name=parsed_arguments.agent,
        agent_file=parsed_arguments.agent_file,
        context_files=tuple(parsed_arguments.context_file),
        temperature=parsed_arguments.temperature,
        top_p=parsed_arguments.top_p,
        max_output_tokens=parsed_arguments.max_output_tokens,
        response_format_file=parsed_arguments.response_format_file,
        enable_tools=parsed_arguments.enable_tools,
        workspace_root=parsed_arguments.workspace,
        enable_actions=parsed_arguments.enable_actions,
        show_tool_traces=parsed_arguments.show_tool_traces,
        show_assistant_summary=parsed_arguments.show_assistant_summary,
        worktree_path=parsed_arguments.worktree_path,
        worktree_branch=parsed_arguments.worktree_branch,
        task_prompt=parsed_arguments.task,
        max_tool_rounds=parsed_arguments.max_tool_rounds,
        commit_message=parsed_arguments.commit_message,
    )


def resolve_runtime_configuration(
    arguments: CLIArguments,
    *,
    project_configuration: ProjectConfiguration | None = None,
) -> RuntimeConfiguration:
    """Resolve CLI overrides against project and application configuration."""

    project = (
        project_configuration.configuration
        if project_configuration is not None
        else None
    )

    if arguments.provider_name is not None and arguments.model_name is None:
        raise ConfigurationError("--model is required when --provider is specified.")

    enable_tools = arguments.enable_tools or bool(
        project is not None and project.enable_tools
    )
    enable_actions = arguments.enable_actions or bool(
        project is not None and project.enable_actions
    )
    workspace_root = (
        arguments.workspace_root
        if arguments.workspace_root is not None
        else (
            project_configuration.project_root
            if project_configuration is not None
            else None
        )
    )

    if arguments.task_prompt is not None and not enable_actions:
        raise ConfigurationError(
            "--task requires --enable-actions or [coding].enable_actions=true."
        )

    if arguments.max_tool_rounds is not None and arguments.task_prompt is None:
        raise ConfigurationError("--max-tool-rounds requires --task.")

    if enable_actions and workspace_root is None:
        raise ConfigurationError("--enable-actions requires --workspace.")

    if (arguments.worktree_path is None) != (arguments.worktree_branch is None):
        raise ConfigurationError(
            "--worktree-path and --worktree-branch must be supplied together."
        )

    if arguments.worktree_path is not None and workspace_root is None:
        raise ConfigurationError("Worktree isolation options require --workspace.")

    if arguments.worktree_path is not None and arguments.task_prompt is None:
        raise ConfigurationError("Worktree isolation options require --task.")

    if arguments.worktree_path is not None and arguments.commit_message is None:
        raise ConfigurationError("Worktree isolation options require --commit-message.")

    if arguments.commit_message is not None and arguments.worktree_path is None:
        raise ConfigurationError(
            "--commit-message requires --worktree-path and --worktree-branch."
        )

    if arguments.agent_name is not None and arguments.agent_file is not None:
        raise ConfigurationError("--agent cannot be combined with --agent-file.")

    if arguments.agent_name is not None and arguments.system_prompt is not None:
        raise ConfigurationError("--agent cannot be combined with --system-prompt.")

    if arguments.agent_file is not None and arguments.system_prompt is not None:
        raise ConfigurationError(
            "--agent-file cannot be combined with --system-prompt."
        )

    project_agent_name = project.agent if project is not None else None
    if arguments.agent_file is not None:
        agent_profile = load_agent_profile_file(arguments.agent_file)
    elif arguments.agent_name is not None:
        agent_profile = get_agent_profile(arguments.agent_name)
    elif arguments.system_prompt is None and project_agent_name is not None:
        agent_profile = get_agent_profile(project_agent_name)
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
        temperature=(
            arguments.temperature
            if arguments.temperature is not None
            else project.temperature
            if project is not None
            else None
        ),
        top_p=(
            arguments.top_p
            if arguments.top_p is not None
            else project.top_p
            if project is not None
            else None
        ),
        max_output_tokens=(
            arguments.max_output_tokens
            if arguments.max_output_tokens is not None
            else project.max_output_tokens
            if project is not None
            else None
        ),
    )

    response_format = (
        load_response_format_file(arguments.response_format_file)
        if arguments.response_format_file is not None
        else None
    )

    provider_name = (
        arguments.provider_name
        or (project.provider if project is not None else None)
        or get_provider_name()
    )
    model_name = (
        arguments.model_name
        or (project.model if project is not None else None)
        or get_model_name(provider_name)
    )

    return RuntimeConfiguration(
        provider_name=provider_name,
        model_name=model_name,
        system_prompt=system_prompt,
        agent_profile=agent_profile,
        context_documents=context_documents,
        generation_config=generation_config,
        response_format=response_format,
        enable_tools=enable_tools,
        workspace_root=workspace_root,
        enable_actions=enable_actions,
        show_tool_traces=arguments.show_tool_traces,
        show_assistant_summary=arguments.show_assistant_summary,
        max_tool_rounds=(
            arguments.max_tool_rounds
            if arguments.max_tool_rounds is not None
            else (
                project.max_tool_rounds
                if project is not None and project.max_tool_rounds is not None
                else DEFAULT_AUTONOMOUS_MAX_TOOL_ROUNDS
            )
        ),
        worktree_path=arguments.worktree_path,
        worktree_branch=arguments.worktree_branch,
        isolated=bool(project is not None and project.isolated),
    )
