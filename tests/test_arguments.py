"""Tests for command-line configuration handling."""

import json
from pathlib import Path

import pytest

from agent_workbench.arguments import (
    DEFAULT_AUTONOMOUS_MAX_TOOL_ROUNDS,
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


def test_parse_cli_arguments_accepts_agent_file() -> None:
    """Accept the path of a custom agent profile."""

    arguments = parse_cli_arguments(["--agent-file", "agents/security-reviewer.toml"])

    assert arguments.agent_file == Path("agents/security-reviewer.toml")


def test_parse_cli_arguments_rejects_blank_agent_file() -> None:
    """Reject a blank custom agent profile path."""

    with pytest.raises(SystemExit) as exc_info:
        parse_cli_arguments(["--agent-file", "   "])

    assert exc_info.value.code == 2


def test_agent_file_provides_system_prompt(tmp_path) -> None:
    """Resolve a custom agent profile into the runtime configuration."""

    profile_path = tmp_path / "security-reviewer.toml"
    profile_path.write_text(
        """
name = "Security Reviewer"
description = "Reviews application security."
system_prompt = "You are a security review agent."
""",
        encoding="utf-8",
    )

    configuration = resolve_runtime_configuration(
        CLIArguments(
            provider_name="ollama",
            model_name="gpt-oss:20b",
            agent_file=profile_path,
        )
    )

    assert configuration.agent_profile is not None
    assert configuration.agent_profile.name == "Security Reviewer"
    assert configuration.system_prompt == "You are a security review agent."


def test_agent_and_agent_file_cannot_be_combined(tmp_path) -> None:
    """Reject simultaneous built-in and external agent selection."""

    profile_path = tmp_path / "custom.toml"

    with pytest.raises(
        ConfigurationError,
        match="--agent cannot be combined with --agent-file",
    ):
        resolve_runtime_configuration(
            CLIArguments(
                provider_name="ollama",
                model_name="gpt-oss:20b",
                agent_name="reviewer",
                agent_file=profile_path,
            )
        )


def test_agent_file_and_system_prompt_cannot_be_combined(
    tmp_path,
) -> None:
    """Reject ambiguous custom profile and system prompt configuration."""

    profile_path = tmp_path / "custom.toml"

    with pytest.raises(
        ConfigurationError,
        match="--agent-file cannot be combined with --system-prompt",
    ):
        resolve_runtime_configuration(
            CLIArguments(
                provider_name="ollama",
                model_name="gpt-oss:20b",
                system_prompt="Custom instructions.",
                agent_file=profile_path,
            )
        )


def test_parse_cli_arguments_accepts_repeated_context_files() -> None:
    """Parse context files while preserving their supplied order."""

    arguments = parse_cli_arguments(
        [
            "--context-file",
            "README.md",
            "--context-file",
            "pyproject.toml",
        ]
    )

    assert arguments.context_files == (
        Path("README.md"),
        Path("pyproject.toml"),
    )


def test_parse_cli_arguments_rejects_blank_context_file() -> None:
    """Reject a blank context file path."""

    with pytest.raises(SystemExit) as exc_info:
        parse_cli_arguments(
            [
                "--context-file",
                "   ",
            ]
        )

    assert exc_info.value.code == 2


def test_runtime_configuration_loads_context_documents_in_order(
    tmp_path,
) -> None:
    """Load context documents while preserving CLI order."""

    first_path = tmp_path / "first.md"
    second_path = tmp_path / "second.py"

    first_path.write_text(
        "First document.",
        encoding="utf-8",
    )
    second_path.write_text(
        "Second document.",
        encoding="utf-8",
    )

    configuration = resolve_runtime_configuration(
        CLIArguments(
            provider_name="ollama",
            model_name="gpt-oss:20b",
            context_files=(first_path, second_path),
        )
    )

    assert [document.source for document in configuration.context_documents] == [
        first_path,
        second_path,
    ]
    assert [document.content for document in configuration.context_documents] == [
        "First document.",
        "Second document.",
    ]


def test_parse_cli_arguments_accepts_response_format_file() -> None:
    """Parse a JSON response format file path."""

    arguments = parse_cli_arguments(
        [
            "--response-format-file",
            "schemas/software-review.json",
        ]
    )

    assert arguments.response_format_file == Path("schemas/software-review.json")


def test_parse_cli_arguments_rejects_blank_response_format_file() -> None:
    """Reject a blank response format file path."""

    with pytest.raises(SystemExit) as exc_info:
        parse_cli_arguments(
            [
                "--response-format-file",
                "   ",
            ]
        )

    assert exc_info.value.code == 2


def test_runtime_configuration_uses_default_generation_config(
    monkeypatch,
) -> None:
    """Use provider defaults when generation parameters are absent."""

    monkeypatch.setenv(PROVIDER_ENV_VAR, "ollama")
    monkeypatch.setenv(MODEL_ENV_VAR, "gpt-oss:20b")

    configuration = resolve_runtime_configuration(
        CLIArguments(
            provider_name=None,
            model_name=None,
        )
    )

    assert configuration.generation_config.temperature is None
    assert configuration.generation_config.top_p is None
    assert configuration.generation_config.max_output_tokens is None
    assert configuration.response_format is None


def test_parse_cli_arguments_accepts_generation_parameters() -> None:
    """Parse provider-independent generation parameters."""

    arguments = parse_cli_arguments(
        [
            "--temperature",
            "0.2",
            "--top-p",
            "0.8",
            "--max-output-tokens",
            "512",
        ]
    )

    assert arguments.temperature == 0.2
    assert arguments.top_p == 0.8
    assert arguments.max_output_tokens == 512


@pytest.mark.parametrize(
    ("argument", "value"),
    [
        ("--temperature", "-0.1"),
        ("--temperature", "1.1"),
        ("--temperature", "invalid"),
        ("--top-p", "-0.1"),
        ("--top-p", "1.1"),
        ("--top-p", "invalid"),
    ],
)
def test_parse_cli_arguments_rejects_invalid_sampling_values(
    argument,
    value,
) -> None:
    """Reject sampling values outside the portable range."""

    with pytest.raises(SystemExit) as exc_info:
        parse_cli_arguments(
            [
                argument,
                value,
            ]
        )

    assert exc_info.value.code == 2


@pytest.mark.parametrize(
    "value",
    [
        "0",
        "-1",
        "1.5",
        "invalid",
    ],
)
def test_parse_cli_arguments_rejects_invalid_output_token_limit(
    value,
) -> None:
    """Reject invalid maximum output token limits."""

    with pytest.raises(SystemExit) as exc_info:
        parse_cli_arguments(
            [
                "--max-output-tokens",
                value,
            ]
        )

    assert exc_info.value.code == 2


def test_runtime_configuration_preserves_generation_parameters() -> None:
    """Resolve CLI generation parameters into runtime configuration."""

    configuration = resolve_runtime_configuration(
        CLIArguments(
            provider_name="ollama",
            model_name="gpt-oss:20b",
            temperature=0.2,
            top_p=0.8,
            max_output_tokens=512,
        )
    )

    assert configuration.generation_config.temperature == 0.2
    assert configuration.generation_config.top_p == 0.8
    assert configuration.generation_config.max_output_tokens == 512


def test_parse_cli_arguments_accepts_interactive_setup() -> None:
    """Enable the interactive runtime setup flow."""

    arguments = parse_cli_arguments(["--setup"])

    assert arguments.setup is True
    assert arguments.provider_name is None
    assert arguments.model_name is None


def test_tools_are_disabled_by_default() -> None:
    """Keep built-in tools disabled without an explicit CLI flag."""

    arguments = parse_cli_arguments([])

    assert arguments.enable_tools is False
    assert arguments.enable_actions is False
    assert arguments.workspace_root is None
    assert arguments.show_tool_traces is False
    assert arguments.max_tool_rounds is None


def test_parse_cli_arguments_accepts_workspace_paths() -> None:
    """Preserve relative and absolute workspace paths for runtime validation."""

    relative_arguments = parse_cli_arguments(["--workspace", "project"])
    absolute_arguments = parse_cli_arguments(["--workspace", "/tmp/project"])

    assert relative_arguments.workspace_root == Path("project")
    assert absolute_arguments.workspace_root == Path("/tmp/project")


def test_parse_cli_arguments_enables_controlled_actions_with_workspace() -> None:
    """Preserve explicit action authorization for relative and absolute roots."""

    relative = parse_cli_arguments(["--workspace", "project", "--enable-actions"])
    absolute = parse_cli_arguments(["--workspace", "/tmp/project", "--enable-actions"])

    assert relative.enable_actions is True
    assert relative.workspace_root == Path("project")
    assert absolute.enable_actions is True
    assert absolute.workspace_root == Path("/tmp/project")


def test_runtime_configuration_requires_workspace_for_actions() -> None:
    """Reject effectful tools without an explicit workspace boundary."""

    with pytest.raises(ConfigurationError, match="requires --workspace"):
        resolve_runtime_configuration(
            CLIArguments(
                provider_name="ollama",
                model_name="gpt-oss:20b",
                enable_actions=True,
            )
        )


def test_runtime_configuration_preserves_action_enablement() -> None:
    """Carry action authorization without callbacks or presentation state."""

    configuration = resolve_runtime_configuration(
        CLIArguments(
            provider_name="ollama",
            model_name="gpt-oss:20b",
            workspace_root=Path("project"),
            enable_actions=True,
        )
    )

    assert configuration.enable_actions is True
    assert configuration.workspace_root == Path("project")


def test_parse_cli_arguments_enables_built_in_tools() -> None:
    """Enable built-in tools when explicitly requested."""

    arguments = parse_cli_arguments(["--enable-tools"])

    assert arguments.enable_tools is True


def test_parse_cli_arguments_enables_tool_traces() -> None:
    """Enable visible tool traces only through the explicit CLI flag."""

    arguments = parse_cli_arguments(["--show-tool-traces"])

    assert arguments.show_tool_traces is True


def test_runtime_configuration_preserves_tool_enablement() -> None:
    """Carry the explicit tool setting into runtime configuration."""

    configuration = resolve_runtime_configuration(
        CLIArguments(
            provider_name="ollama",
            model_name="gpt-oss:20b",
            enable_tools=True,
        )
    )

    assert configuration.enable_tools is True


def test_runtime_configuration_preserves_tool_trace_enablement() -> None:
    """Carry the explicit trace setting into runtime configuration."""

    configuration = resolve_runtime_configuration(
        CLIArguments(
            provider_name="ollama",
            model_name="gpt-oss:20b",
            show_tool_traces=True,
        )
    )

    assert configuration.show_tool_traces is True


def test_runtime_configuration_preserves_workspace_root() -> None:
    """Carry the optional workspace root into runtime configuration."""

    workspace_root = Path("project")
    configuration = resolve_runtime_configuration(
        CLIArguments(
            provider_name="ollama",
            model_name="gpt-oss:20b",
            workspace_root=workspace_root,
        )
    )

    assert configuration.workspace_root == workspace_root


def test_worktree_isolation_options_are_absent_by_default() -> None:
    """Preserve the existing non-isolated CLI and runtime defaults."""

    arguments = parse_cli_arguments([])
    configuration = resolve_runtime_configuration(
        CLIArguments(
            provider_name="ollama",
            model_name="test-model",
        )
    )

    assert arguments.worktree_path is None
    assert arguments.worktree_branch is None
    assert arguments.commit_message is None
    assert configuration.worktree_path is None
    assert configuration.worktree_branch is None


def test_parse_worktree_options_preserves_relative_absolute_and_exact_branch() -> None:
    """Preserve explicit target paths and never trim or infer a branch name."""

    relative = parse_cli_arguments(
        [
            "--workspace",
            ".",
            "--worktree-path",
            "../task-worktree",
            "--worktree-branch",
            "agent/task",
        ]
    )
    absolute = parse_cli_arguments(
        [
            "--workspace",
            "/tmp/source",
            "--worktree-path",
            "/tmp/task-worktree",
            "--worktree-branch",
            "agent/absolute",
        ]
    )

    assert relative.worktree_path == Path("../task-worktree")
    assert relative.worktree_branch == "agent/task"
    assert absolute.worktree_path == Path("/tmp/task-worktree")
    assert absolute.worktree_branch == "agent/absolute"


def test_parse_worktree_branch_rejects_blank_values() -> None:
    """Reject a blank worktree branch without silently normalizing it."""

    with pytest.raises(SystemExit) as raised:
        parse_cli_arguments(
            [
                "--worktree-branch",
                "   ",
            ]
        )

    assert raised.value.code == 2


@pytest.mark.parametrize(
    ("worktree_path", "worktree_branch", "workspace_root", "match"),
    [
        (Path("../target"), None, Path("."), "supplied together"),
        (None, "agent/task", Path("."), "supplied together"),
        (
            Path("../target"),
            "agent/task",
            None,
            "--enable-actions requires --workspace",
        ),
    ],
)
def test_runtime_configuration_rejects_incomplete_worktree_isolation(
    worktree_path,
    worktree_branch,
    workspace_root,
    match,
) -> None:
    """Require the exact source, target, and branch triple."""

    with pytest.raises(ConfigurationError, match=match):
        resolve_runtime_configuration(
            CLIArguments(
                provider_name="ollama",
                model_name="test-model",
                task_prompt="Correct the implementation.",
                commit_message="fix: correct implementation",
                workspace_root=workspace_root,
                enable_actions=True,
                worktree_path=worktree_path,
                worktree_branch=worktree_branch,
            )
        )


def test_runtime_configuration_preserves_complete_worktree_isolation() -> None:
    """Carry optional target and branch values without plans or handlers."""

    target = Path("../task-worktree")
    configuration = resolve_runtime_configuration(
        CLIArguments(
            provider_name="ollama",
            model_name="test-model",
            task_prompt="Correct the implementation.",
            commit_message="fix: correct implementation",
            workspace_root=Path("."),
            worktree_path=target,
            worktree_branch="agent/task",
            enable_actions=True,
        )
    )

    assert configuration.workspace_root == Path(".")
    assert configuration.worktree_path is target
    assert configuration.worktree_branch == "agent/task"
    assert configuration.enable_actions is True


def test_setup_rejects_worktree_options_as_direct_configuration() -> None:
    """Keep interactive setup on its unchanged no-worktree default."""

    with pytest.raises(SystemExit) as raised:
        parse_cli_arguments(
            [
                "--setup",
                "--worktree-path",
                "../task",
                "--worktree-branch",
                "agent/task",
            ]
        )

    assert raised.value.code == 2


def test_runtime_configuration_disables_tools_by_default() -> None:
    """Keep built-in tools disabled in resolved runtime configuration."""

    configuration = resolve_runtime_configuration(
        CLIArguments(
            provider_name="ollama",
            model_name="gpt-oss:20b",
        )
    )

    assert configuration.enable_tools is False


@pytest.mark.parametrize(
    "conflicting_arguments",
    [
        [
            "--provider",
            "ollama",
            "--model",
            "gpt-oss:20b",
        ],
        [
            "--agent",
            "reviewer",
        ],
        [
            "--context-file",
            "README.md",
        ],
        [
            "--temperature",
            "0.2",
        ],
        [
            "--response-format-file",
            "schema.json",
        ],
        [
            "--enable-tools",
        ],
        [
            "--workspace",
            "project",
        ],
        [
            "--enable-actions",
        ],
        [
            "--show-tool-traces",
        ],
        [
            "--commit-message",
            "fix: exact",
        ],
        [
            "--max-tool-rounds",
            "32",
        ],
    ],
)
def test_interactive_setup_rejects_configuration_arguments(
    conflicting_arguments,
) -> None:
    """Reject ambiguous setup and direct configuration combinations."""

    with pytest.raises(SystemExit) as exc_info:
        parse_cli_arguments(
            [
                "--setup",
                *conflicting_arguments,
            ],
        )

    assert exc_info.value.code == 2


def test_runtime_configuration_loads_response_format_file(
    tmp_path,
) -> None:
    """Load a response format into runtime configuration."""

    format_path = tmp_path / "software-review.json"
    schema = {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
            },
        },
        "required": [
            "summary",
        ],
        "additionalProperties": False,
    }

    format_path.write_text(
        json.dumps(
            {
                "name": "software_review",
                "schema": schema,
            }
        ),
        encoding="utf-8",
    )

    configuration = resolve_runtime_configuration(
        CLIArguments(
            provider_name="ollama",
            model_name="gpt-oss:20b",
            response_format_file=format_path,
        )
    )

    assert configuration.response_format is not None
    assert configuration.response_format.name == "software_review"
    assert configuration.response_format.schema == schema


def test_autonomous_tool_rounds_use_the_default_when_omitted() -> None:
    """Resolve the established autonomous limit without changing parse defaults."""

    arguments = parse_cli_arguments([])
    configuration = resolve_runtime_configuration(
        CLIArguments(
            provider_name="ollama",
            model_name="gpt-oss:20b",
        )
    )

    assert arguments.max_tool_rounds is None
    assert configuration.max_tool_rounds == DEFAULT_AUTONOMOUS_MAX_TOOL_ROUNDS == 16


def test_parse_and_resolve_custom_autonomous_tool_round_limit() -> None:
    """Parse and preserve one explicit positive autonomous round limit."""

    arguments = parse_cli_arguments(
        [
            "--provider",
            "ollama",
            "--model",
            "gpt-oss:20b",
            "--workspace",
            ".",
            "--enable-actions",
            "--task",
            "Correct the implementation.",
            "--max-tool-rounds",
            "32",
        ]
    )
    configuration = resolve_runtime_configuration(arguments)

    assert arguments.max_tool_rounds == 32
    assert configuration.max_tool_rounds == 32


@pytest.mark.parametrize(
    "value",
    [
        "0",
        "-1",
        "1.5",
        "invalid",
    ],
)
def test_parse_cli_arguments_rejects_invalid_tool_round_limits(
    value: str,
) -> None:
    """Reject non-positive and non-integer autonomous round limits."""

    with pytest.raises(SystemExit) as exc_info:
        parse_cli_arguments(["--max-tool-rounds", value])

    assert exc_info.value.code == 2


def test_max_tool_rounds_requires_autonomous_task() -> None:
    """Reject explicit autonomous limits outside task mode."""

    with pytest.raises(
        ConfigurationError,
        match="--max-tool-rounds requires --task",
    ):
        resolve_runtime_configuration(
            CLIArguments(
                provider_name="ollama",
                model_name="gpt-oss:20b",
                max_tool_rounds=32,
            )
        )


def test_parse_cli_arguments_accepts_autonomous_task() -> None:
    """Parse and normalize one autonomous coding task."""

    arguments = parse_cli_arguments(
        [
            "--task",
            "  Correct the failing implementation.  ",
        ]
    )

    assert arguments.task_prompt == "Correct the failing implementation."


def test_parse_cli_arguments_rejects_blank_autonomous_task() -> None:
    """Reject autonomous task prompts containing only whitespace."""

    with pytest.raises(SystemExit) as exc_info:
        parse_cli_arguments(["--task", "   "])

    assert exc_info.value.code == 2


def test_parse_cli_arguments_preserves_exact_commit_message() -> None:
    """Preserve the exact non-blank isolated commit message."""

    arguments = parse_cli_arguments(
        [
            "--commit-message",
            "  fix: preserve exact spacing  ",
        ]
    )

    assert arguments.commit_message == "  fix: preserve exact spacing  "


def test_parse_cli_arguments_rejects_blank_commit_message() -> None:
    """Reject isolated commit messages containing only whitespace."""

    with pytest.raises(SystemExit) as exc_info:
        parse_cli_arguments(["--commit-message", "   "])

    assert exc_info.value.code == 2


def test_autonomous_task_requires_actions() -> None:
    """Require explicit action enablement for autonomous coding."""

    with pytest.raises(
        ConfigurationError,
        match="--task requires --enable-actions",
    ):
        resolve_runtime_configuration(
            CLIArguments(
                provider_name="ollama",
                model_name="gpt-oss:20b",
                task_prompt="Correct the implementation.",
                workspace_root=Path("."),
            )
        )


def test_autonomous_task_actions_require_workspace() -> None:
    """Require an authorized workspace for autonomous coding actions."""

    with pytest.raises(
        ConfigurationError,
        match="--enable-actions requires --workspace",
    ):
        resolve_runtime_configuration(
            CLIArguments(
                provider_name="ollama",
                model_name="gpt-oss:20b",
                task_prompt="Correct the implementation.",
                enable_actions=True,
            )
        )


def test_worktree_isolation_requires_autonomous_task() -> None:
    """Reject the removed interactive worktree mode."""

    with pytest.raises(
        ConfigurationError,
        match="Worktree isolation options require --task",
    ):
        resolve_runtime_configuration(
            CLIArguments(
                provider_name="ollama",
                model_name="gpt-oss:20b",
                commit_message="fix: exact",
                workspace_root=Path("."),
                enable_actions=True,
                worktree_path=Path("../task-worktree"),
                worktree_branch="agent/task",
            )
        )


def test_worktree_isolation_requires_commit_message() -> None:
    """Require the final exact commit message before isolated execution."""

    with pytest.raises(
        ConfigurationError,
        match="Worktree isolation options require --commit-message",
    ):
        resolve_runtime_configuration(
            CLIArguments(
                provider_name="ollama",
                model_name="gpt-oss:20b",
                task_prompt="Correct the implementation.",
                workspace_root=Path("."),
                enable_actions=True,
                worktree_path=Path("../task-worktree"),
                worktree_branch="agent/task",
            )
        )


def test_commit_message_requires_worktree_isolation() -> None:
    """Reject a commit message outside the isolated autonomous workflow."""

    with pytest.raises(
        ConfigurationError,
        match="--commit-message requires --worktree-path and --worktree-branch",
    ):
        resolve_runtime_configuration(
            CLIArguments(
                provider_name="ollama",
                model_name="gpt-oss:20b",
                task_prompt="Correct the implementation.",
                commit_message="fix: exact",
                workspace_root=Path("."),
                enable_actions=True,
            )
        )
