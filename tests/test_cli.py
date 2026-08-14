"""Tests for the Agent Workbench command-line interface."""

import hashlib
import subprocess
from pathlib import Path
from unittest.mock import call, Mock

import pytest

from agent_workbench.built_in_tools import create_built_in_tool_registry
from agent_workbench.coding_loop import (
    CodingPhase,
    CodingProgressEvent,
    CodingProgressKind,
)
from agent_workbench.cli import (
    AUTONOMOUS_MAX_TOOL_ROUNDS,
    CANCELLATION_MESSAGE,
    _display_coding_progress,
    main,
    run_cli as run_session_cli,
)
from agent_workbench.arguments import RuntimeConfiguration
from agent_workbench.context import ContextDocument
from agent_workbench.errors import CompletionError, ConfigurationError
from agent_workbench.messages import ChatRequest, ChatResponse, Message
from agent_workbench.lifecycle_store import IsolatedCommitLifecycleStore
from agent_workbench.session import AgentSession, SessionId
from agent_workbench.agents import get_agent_profile
from agent_workbench.generation import GenerationConfig
from agent_workbench.config import (
    PROJECT_CONFIG_RELATIVE_PATH,
    ProjectCodingConfiguration,
    load_project_configuration,
    render_project_configuration,
)
from agent_workbench.git_tools import register_git_tools
from agent_workbench.structured_outputs import JSONResponseFormat
from agent_workbench.symbol_tools import register_symbol_tools
from agent_workbench.tool_registry import ToolRegistry
from agent_workbench.tools import (
    ToolApprovalDecision,
    ToolDefinition,
    ToolInvocation,
)
from agent_workbench.validation_tools import register_validation_tools
from agent_workbench.workspace import Workspace
from agent_workbench.workspace_actions import register_workspace_action_tools
from agent_workbench.workspace_tools import register_workspace_tools


class FakeProvider:
    """Provide deterministic responses for CLI tests."""

    name = "Fake"
    model_name = "fake-model"

    def __init__(
        self,
        outcomes: list[str | ChatResponse | CompletionError] | None = None,
    ) -> None:
        self._outcomes = iter(outcomes or [])
        self.calls: list[list[Message]] = []
        self.requests: list[ChatRequest] = []
        self.system_prompts: list[str | None] = []
        self.context_documents: list[tuple[ContextDocument, ...]] = []
        self.generation_configs: list[GenerationConfig] = []
        self.response_formats: list[JSONResponseFormat | None] = []

    def complete(self, request: ChatRequest) -> ChatResponse:
        """Return the next configured response or error."""

        self.calls.append([message.copy() for message in request.messages])
        self.requests.append(request)
        self.system_prompts.append(request.system_prompt)
        self.context_documents.append(request.context_documents)
        self.generation_configs.append(request.generation_config)
        self.response_formats.append(request.response_format)

        outcome = next(self._outcomes)

        if isinstance(outcome, CompletionError):
            raise outcome

        if isinstance(outcome, str):
            return ChatResponse(text=outcome)

        return outcome


EXPECTED_INITIALIZED_CONFIG = """\
[coding]
provider = "ollama"
model = "qwen3-coder:30b"
agent = "developer"
enable_tools = true
enable_actions = true
max_tool_rounds = 8
temperature = 0.2
top_p = 0.9
max_output_tokens = 4096
isolated = false
"""


def run_cli(
    provider,
    system_prompt=None,
    agent_profile=None,
    context_documents=(),
    generation_config=None,
    response_format=None,
    tool_registry=None,
    max_tool_rounds=8,
    show_tool_traces=False,
) -> None:
    """Adapt legacy test inputs to the prebuilt-session presentation boundary."""

    session = AgentSession(
        id=SessionId("test-session"),
        provider=provider,
        agent_profile=agent_profile,
        system_prompt=system_prompt,
        context_documents=context_documents,
        generation_config=generation_config,
        response_format=response_format,
        tool_registry=tool_registry,
        max_tool_rounds=max_tool_rounds,
    )
    run_session_cli(
        session,
        agent_profile=agent_profile,
        show_tool_traces=show_tool_traces,
    )


def run_main_and_capture_exit_code(argv: list[str]) -> int:
    """Emulate console-script exit semantics for deterministic assertions."""

    try:
        main(argv)
    except SystemExit as exc:
        if exc.code is None:
            return 0
        if isinstance(exc.code, int):
            return exc.code
        return 1
    return 0


def create_calculator_definition() -> ToolDefinition:
    """Create a calculator definition for CLI tool-calling tests."""

    return ToolDefinition(
        name="calculator",
        description="Evaluate a mathematical expression.",
        input_schema={
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                }
            },
            "required": ["expression"],
            "additionalProperties": False,
        },
    )


def create_tool_response(
    *invocations: ToolInvocation,
    text: str = "",
) -> ChatResponse:
    """Create a response containing ordered tool invocations."""

    return ChatResponse(text=text, tool_invocations=invocations)


def create_cli_coding_repository(root: Path) -> Path:
    """Create one committed failing project for full CLI coding regressions."""

    root.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "CLI Test User"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "cli-test@example.invalid"],
        cwd=root,
        check=True,
    )
    (root / "module.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left - right\n",
        encoding="utf-8",
    )
    (root / "test_module.py").write_text(
        "from module import add\n\n\n"
        "def test_add() -> None:\n"
        "    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", "module.py", "test_module.py"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return root


def create_cli_coding_session(
    repository: Path,
    provider: FakeProvider,
) -> AgentSession:
    """Create one real action-enabled session for CLI progress regressions."""

    registry = ToolRegistry()
    workspace = Workspace(repository)
    register_workspace_tools(registry, workspace)
    register_symbol_tools(registry, workspace)
    register_git_tools(registry, workspace)
    register_workspace_action_tools(registry, workspace)
    register_validation_tools(registry, workspace)
    return AgentSession(
        id=SessionId("cli-session"),
        provider=provider,
        tool_registry=registry,
        max_tool_rounds=AUTONOMOUS_MAX_TOOL_ROUNDS,
    )


def coding_replacement_response(
    invocation_id: str,
    *,
    expected_content: str,
    expected_text: str,
    replacement_text: str,
) -> ChatResponse:
    """Create one SHA-guarded literal replacement provider response."""

    return create_tool_response(
        ToolInvocation(
            id=invocation_id,
            tool_name="apply_text_replacement",
            arguments={
                "path": "module.py",
                "expected_text": expected_text,
                "replacement_text": replacement_text,
                "expected_file_sha256": hashlib.sha256(
                    expected_content.encode("utf-8")
                ).hexdigest(),
            },
        )
    )


def test_exit_command_does_not_call_provider(monkeypatch, capsys) -> None:
    """Exit immediately without contacting the provider."""

    user_inputs = iter(["/exit"])
    provider = FakeProvider()

    monkeypatch.setattr("builtins.input", lambda _: next(user_inputs))

    run_cli(provider)

    captured = capsys.readouterr()

    assert provider.calls == []
    assert "Session ended." in captured.out


def test_empty_input_is_ignored(monkeypatch) -> None:
    """Ignore empty input without contacting the provider."""

    user_inputs = iter(["", "/quit"])
    provider = FakeProvider()

    monkeypatch.setattr("builtins.input", lambda _: next(user_inputs))

    run_cli(provider)

    assert provider.calls == []


def test_conversation_history_is_preserved(monkeypatch, capsys) -> None:
    """Send previous user and assistant messages with each new request."""

    user_inputs = iter(
        [
            "Remember the code word cobalt.",
            "What is the code word?",
            "/exit",
        ]
    )
    provider = FakeProvider(["acknowledged", "cobalt"])

    monkeypatch.setattr("builtins.input", lambda _: next(user_inputs))

    run_cli(provider)

    captured = capsys.readouterr()

    assert provider.calls == [
        [
            {
                "role": "user",
                "content": "Remember the code word cobalt.",
            }
        ],
        [
            {
                "role": "user",
                "content": "Remember the code word cobalt.",
            },
            {
                "role": "assistant",
                "content": "acknowledged",
            },
            {
                "role": "user",
                "content": "What is the code word?",
            },
        ],
    ]
    assert "Assistant: acknowledged" in captured.out
    assert "Assistant: cobalt" in captured.out


def test_cli_recovers_after_provider_error(monkeypatch, capsys) -> None:
    """Continue the session without preserving a failed request."""

    user_inputs = iter(
        [
            "First request",
            "Second request",
            "/exit",
        ]
    )
    provider = FakeProvider(
        [
            CompletionError("Temporary provider failure."),
            "recovered",
        ]
    )

    monkeypatch.setattr("builtins.input", lambda _: next(user_inputs))

    run_cli(provider)

    captured = capsys.readouterr()

    assert provider.calls == [
        [
            {
                "role": "user",
                "content": "First request",
            }
        ],
        [
            {
                "role": "user",
                "content": "Second request",
            }
        ],
    ]
    assert "Error: Temporary provider failure." in captured.out
    assert "Assistant: recovered" in captured.out


def test_system_prompt_is_forwarded_without_entering_history(
    monkeypatch,
) -> None:
    """Forward the system prompt separately from conversation history."""

    user_inputs = iter(
        [
            "First request",
            "Second request",
            "/exit",
        ]
    )
    provider = FakeProvider(
        [
            "first response",
            "second response",
        ]
    )

    monkeypatch.setattr(
        "builtins.input",
        lambda _: next(user_inputs),
    )

    run_cli(
        provider,
        system_prompt="You are a strict software reviewer.",
    )

    assert provider.system_prompts == [
        "You are a strict software reviewer.",
        "You are a strict software reviewer.",
    ]

    assert all(
        message["role"] != "system"
        for request_messages in provider.calls
        for message in request_messages
    )


def test_agent_profile_is_displayed(
    monkeypatch,
    capsys,
) -> None:
    """Display the active agent identity when the session starts."""

    user_inputs = iter(["/exit"])
    provider = FakeProvider()
    agent_profile = get_agent_profile("reviewer")

    monkeypatch.setattr(
        "builtins.input",
        lambda _: next(user_inputs),
    )

    run_cli(
        provider,
        system_prompt=agent_profile.system_prompt,
        agent_profile=agent_profile,
    )

    captured = capsys.readouterr()

    assert "Agent: Reviewer" in captured.out
    assert agent_profile.description in captured.out


def test_context_documents_are_forwarded_without_entering_history(
    monkeypatch,
) -> None:
    """Forward context documents separately from conversation messages."""

    user_inputs = iter(
        [
            "Review the supplied project files.",
            "/exit",
        ]
    )
    provider = FakeProvider(["review complete"])
    context_documents = (
        ContextDocument(
            source=Path("README.md"),
            content="Project documentation.",
        ),
        ContextDocument(
            source=Path("pyproject.toml"),
            content='name = "agent-workbench"',
        ),
    )

    monkeypatch.setattr(
        "builtins.input",
        lambda _: next(user_inputs),
    )

    run_cli(
        provider,
        context_documents=context_documents,
    )

    assert provider.context_documents == [context_documents]
    assert provider.calls == [
        [
            {
                "role": "user",
                "content": "Review the supplied project files.",
            }
        ]
    ]


def test_generation_config_is_forwarded_without_entering_history(
    monkeypatch,
) -> None:
    """Forward generation settings separately from conversation messages."""

    user_inputs = iter(
        [
            "Generate a concise response.",
            "/exit",
        ]
    )
    provider = FakeProvider(["configured response"])
    generation_config = GenerationConfig(
        temperature=0.2,
        top_p=0.8,
        max_output_tokens=256,
    )

    monkeypatch.setattr(
        "builtins.input",
        lambda _: next(user_inputs),
    )

    run_cli(
        provider,
        generation_config=generation_config,
    )

    assert provider.generation_configs == [generation_config]
    assert provider.calls == [
        [
            {
                "role": "user",
                "content": "Generate a concise response.",
            }
        ]
    ]


def test_response_format_is_forwarded_without_entering_history(
    monkeypatch,
) -> None:
    """Forward structured output configuration separately from messages."""

    user_inputs = iter(
        [
            "Review this implementation.",
            "/exit",
        ]
    )
    provider = FakeProvider(['{"summary":"No critical issues."}'])
    response_format = JSONResponseFormat(
        name="software_review",
        schema={
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
        },
    )

    monkeypatch.setattr(
        "builtins.input",
        lambda _: next(user_inputs),
    )

    run_cli(
        provider,
        response_format=response_format,
    )

    assert provider.response_formats == [response_format]
    assert provider.calls == [
        [
            {
                "role": "user",
                "content": "Review this implementation.",
            }
        ]
    ]


def test_cli_without_registry_completes_without_tool_registry(
    monkeypatch,
) -> None:
    """Keep successful no-tool behavior when no registry is supplied."""

    user_inputs = iter(["Respond directly.", "/exit"])
    provider = FakeProvider(["direct response"])

    monkeypatch.setattr("builtins.input", lambda _: next(user_inputs))

    run_cli(provider)

    assert provider.calls == [
        [
            {
                "role": "user",
                "content": "Respond directly.",
            }
        ]
    ]


def test_cli_constructs_and_reuses_one_agent_session(
    monkeypatch,
    capsys,
) -> None:
    """Delegate every non-exit turn to one configured AgentSession."""

    session = Mock()
    session.provider_name = "Fake"
    session.model_name = "fake-model"
    session.tool_registry = None
    session.send.side_effect = [
        ChatResponse(text="First response."),
        ChatResponse(text="Second response."),
    ]
    user_inputs = iter(["First request.", "Second request.", "/exit"])

    monkeypatch.setattr("builtins.input", lambda _: next(user_inputs))

    run_session_cli(session)

    assert session.send.call_args_list == [
        call("First request."),
        call("Second request."),
    ]
    output = capsys.readouterr().out
    assert "Assistant: First response." in output
    assert "Assistant: Second response." in output


def test_cli_forwards_registry_definitions_and_request_configuration(
    monkeypatch,
) -> None:
    """Forward ordered registry definitions with the complete request configuration."""

    calculator = create_calculator_definition()
    project_information = ToolDefinition(
        name="project_information",
        description="Return project information.",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    )
    registry = ToolRegistry()
    registry.register(calculator, lambda arguments: {"value": 4})
    registry.register(project_information, lambda arguments: {"name": "Workbench"})
    context_documents = (
        ContextDocument(
            source=Path("README.md"),
            content="Project documentation.",
        ),
    )
    generation_config = GenerationConfig(
        temperature=0.2,
        top_p=0.8,
        max_output_tokens=128,
    )
    response_format = JSONResponseFormat(
        name="result",
        schema={
            "type": "object",
            "properties": {
                "value": {
                    "type": "integer",
                }
            },
            "additionalProperties": False,
        },
    )
    provider = FakeProvider(
        [
            create_tool_response(
                ToolInvocation(
                    id="call-1",
                    tool_name="calculator",
                    arguments={"expression": "2 + 2"},
                )
            ),
            "4",
        ]
    )
    user_inputs = iter(["Calculate two plus two.", "/exit"])

    monkeypatch.setattr("builtins.input", lambda _: next(user_inputs))

    run_cli(
        provider,
        system_prompt="Be concise.",
        context_documents=context_documents,
        generation_config=generation_config,
        response_format=response_format,
        tool_registry=registry,
        max_tool_rounds=1,
    )

    assert provider.requests[0].tools == (
        calculator,
        project_information,
    )
    assert provider.requests[0].system_prompt == "Be concise."
    assert provider.requests[0].context_documents == context_documents
    assert provider.requests[0].generation_config == generation_config
    assert provider.requests[0].response_format == response_format
    assert provider.requests[1].tools == provider.requests[0].tools


def test_cli_executes_one_tool_and_displays_only_the_final_response(
    monkeypatch,
    capsys,
) -> None:
    """Execute a requested tool without displaying internal tool details."""

    calculator = create_calculator_definition()
    registry = ToolRegistry()
    registry.register(calculator, lambda arguments: {"value": 4})
    provider = FakeProvider(
        [
            create_tool_response(
                ToolInvocation(
                    id="call-1",
                    tool_name="calculator",
                    arguments={"expression": "2 + 2"},
                ),
                text="I will calculate the answer.",
            ),
            "The answer is 4.",
        ]
    )
    user_inputs = iter(["Calculate two plus two.", "/exit"])

    monkeypatch.setattr("builtins.input", lambda _: next(user_inputs))

    run_cli(provider, tool_registry=registry, max_tool_rounds=1)

    captured = capsys.readouterr()

    assert "Assistant: The answer is 4." in captured.out
    assert "I will calculate the answer." not in captured.out
    assert "ToolInvocation" not in captured.out
    assert "ToolResult" not in captured.out


def test_cli_displays_opt_in_compact_tool_traces_before_final_response(
    monkeypatch,
    capsys,
) -> None:
    """Display ordered JSON-safe traces only when explicitly enabled."""

    calculator = create_calculator_definition()
    registry = ToolRegistry()
    registry.register(calculator, lambda arguments: {"value": 4})
    provider = FakeProvider(
        [
            create_tool_response(
                ToolInvocation(
                    id="call-1",
                    tool_name="calculator",
                    arguments={"expression": "2 + 2"},
                )
            ),
            "The answer is 4.",
        ]
    )
    user_inputs = iter(["Calculate.", "/exit"])

    monkeypatch.setattr("builtins.input", lambda _: next(user_inputs))

    run_cli(
        provider,
        tool_registry=registry,
        max_tool_rounds=1,
        show_tool_traces=True,
    )

    captured = capsys.readouterr()

    assert "Tool trace: calculator (call-1)" in captured.out
    assert 'arguments={"expression":"2 + 2"}' in captured.out
    assert 'result={"output":{"value":4},"status":"success"}' in captured.out
    assert captured.out.index("Tool trace:") < captured.out.index(
        "Assistant: The answer is 4."
    )
    assert "ToolInvocation(" not in captured.out
    assert "ToolResult(" not in captured.out


def test_cli_traces_multiple_rounds_and_keeps_trace_text_out_of_history(
    monkeypatch,
    capsys,
) -> None:
    """Trace ordered successes and errors without changing message history."""

    calculator = create_calculator_definition()
    registry = ToolRegistry()
    registry.register(calculator, lambda arguments: {"value": 4})
    provider = FakeProvider(
        [
            create_tool_response(
                ToolInvocation(
                    id="call-1",
                    tool_name="calculator",
                    arguments={"expression": "2 + 2"},
                )
            ),
            create_tool_response(
                ToolInvocation(
                    id="call-2",
                    tool_name="unknown",
                    arguments={"path": "/host/.env"},
                )
            ),
            "Completed.",
            "Acknowledged.",
        ]
    )
    user_inputs = iter(["Use tools.", "Continue.", "/exit"])

    monkeypatch.setattr("builtins.input", lambda _: next(user_inputs))

    run_cli(
        provider,
        tool_registry=registry,
        max_tool_rounds=2,
        show_tool_traces=True,
    )

    captured = capsys.readouterr()

    assert captured.out.index("Tool trace: calculator (call-1)") < captured.out.index(
        "Tool trace: unknown (call-2)"
    )
    assert 'result={"error":"Unknown tool \'unknown\'.","status":"error"}' in (
        captured.out
    )
    assert "[redacted absolute path]" in captured.out
    assert "/host/.env" not in captured.out
    assert "ToolInvocation(" not in captured.out
    assert "ToolResult(" not in captured.out
    assert "Tool trace:" not in str(provider.calls[3])


def test_cli_executes_multiple_invocations_in_provider_order(
    monkeypatch,
) -> None:
    """Execute all requested tools in their provider response order."""

    execution_order = []
    calculator = create_calculator_definition()
    project_information = ToolDefinition(
        name="project_information",
        description="Return project information.",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    )
    registry = ToolRegistry()
    registry.register(
        calculator,
        lambda arguments: execution_order.append("calculator") or {"value": 4},
    )
    registry.register(
        project_information,
        lambda arguments: (
            execution_order.append("project_information") or {"name": "Workbench"}
        ),
    )
    provider = FakeProvider(
        [
            create_tool_response(
                ToolInvocation(
                    id="call-1",
                    tool_name="calculator",
                    arguments={},
                ),
                ToolInvocation(
                    id="call-2",
                    tool_name="project_information",
                    arguments={},
                ),
            ),
            "Completed.",
        ]
    )
    user_inputs = iter(["Use both tools.", "/exit"])

    monkeypatch.setattr("builtins.input", lambda _: next(user_inputs))

    run_cli(provider, tool_registry=registry, max_tool_rounds=1)

    assert execution_order == ["calculator", "project_information"]


def test_cli_returns_tool_errors_to_the_provider_and_continues(
    monkeypatch,
) -> None:
    """Continue to the final provider response after a tool error result."""

    provider = FakeProvider(
        [
            create_tool_response(
                ToolInvocation(
                    id="unknown-call",
                    tool_name="unknown",
                    arguments={},
                )
            ),
            "The requested tool is unavailable.",
        ]
    )
    user_inputs = iter(["Use the unknown tool.", "/exit"])

    monkeypatch.setattr("builtins.input", lambda _: next(user_inputs))

    run_cli(provider, tool_registry=ToolRegistry(), max_tool_rounds=1)

    result = provider.requests[1].tool_interactions[0].results[0]

    assert result.status == "error"
    assert result.error == "Unknown tool 'unknown'."


def test_cli_reports_maximum_tool_round_error_without_preserving_request(
    monkeypatch,
    capsys,
) -> None:
    """Recover from an exhausted tool-round limit without retaining the request."""

    calculator = create_calculator_definition()
    registry = ToolRegistry()
    registry.register(calculator, lambda arguments: {"value": 4})
    provider = FakeProvider(
        [
            create_tool_response(
                ToolInvocation(
                    id="call-1",
                    tool_name="calculator",
                    arguments={},
                )
            ),
            create_tool_response(
                ToolInvocation(
                    id="call-2",
                    tool_name="calculator",
                    arguments={},
                )
            ),
            "Recovered.",
        ]
    )
    user_inputs = iter(["First request", "Second request", "/exit"])

    monkeypatch.setattr("builtins.input", lambda _: next(user_inputs))

    run_cli(provider, tool_registry=registry, max_tool_rounds=1)

    captured = capsys.readouterr()

    assert "Error: The maximum number of tool execution rounds was exceeded." in (
        captured.out
    )
    assert provider.calls[2] == [
        {
            "role": "user",
            "content": "Second request",
        }
    ]


def test_cli_history_contains_only_user_and_final_assistant_messages(
    monkeypatch,
) -> None:
    """Keep internal tool interactions out of normal CLI conversation history."""

    calculator = create_calculator_definition()
    registry = ToolRegistry()
    registry.register(calculator, lambda arguments: {"value": 4})
    provider = FakeProvider(
        [
            create_tool_response(
                ToolInvocation(
                    id="call-1",
                    tool_name="calculator",
                    arguments={},
                ),
                text="Calculating.",
            ),
            "Four.",
            "Acknowledged.",
        ]
    )
    user_inputs = iter(["Calculate.", "Continue.", "/exit"])

    monkeypatch.setattr("builtins.input", lambda _: next(user_inputs))

    run_cli(provider, tool_registry=registry, max_tool_rounds=1)

    assert provider.calls[2] == [
        {
            "role": "user",
            "content": "Calculate.",
        },
        {
            "role": "assistant",
            "content": "Four.",
        },
        {
            "role": "user",
            "content": "Continue.",
        },
    ]


def test_cli_executes_the_built_in_calculator_and_displays_final_text(
    monkeypatch,
    capsys,
) -> None:
    """Complete a CLI tool-calling flow through the built-in registry."""

    provider = FakeProvider(
        [
            create_tool_response(
                ToolInvocation(
                    id="calculator-call",
                    tool_name="calculator",
                    arguments={"expression": "2 + 2"},
                )
            ),
            "The answer is 4.",
        ]
    )
    user_inputs = iter(["Calculate two plus two.", "/exit"])

    monkeypatch.setattr("builtins.input", lambda _: next(user_inputs))

    run_cli(
        provider,
        tool_registry=create_built_in_tool_registry(),
        max_tool_rounds=1,
    )

    captured = capsys.readouterr()

    assert provider.requests[1].tool_interactions[0].results[0].output == {
        "expression": "2 + 2",
        "result": 4,
    }
    assert "Assistant: The answer is 4." in captured.out


def test_cli_executes_workspace_list_and_read_tools(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    """Complete a list-and-read workspace flow through the CLI loop."""

    (tmp_path / "README.md").write_text("WORKSPACE-731", encoding="utf-8")
    registry = ToolRegistry()
    register_workspace_tools(registry, Workspace(tmp_path))
    provider = FakeProvider(
        [
            create_tool_response(
                ToolInvocation(
                    id="list-call",
                    tool_name="list_files",
                    arguments={"path": "."},
                )
            ),
            create_tool_response(
                ToolInvocation(
                    id="read-call",
                    tool_name="read_file",
                    arguments={"path": "README.md"},
                )
            ),
            "The code word is WORKSPACE-731.",
        ]
    )
    user_inputs = iter(["Inspect the workspace.", "/exit"])

    monkeypatch.setattr("builtins.input", lambda _: next(user_inputs))

    run_cli(provider, tool_registry=registry, max_tool_rounds=2)

    captured = capsys.readouterr()

    assert provider.requests[1].tool_interactions[0].results[0].output == {
        "path": ".",
        "entries": [
            {
                "name": "README.md",
                "path": "README.md",
                "type": "file",
            }
        ],
    }
    assert provider.requests[2].tool_interactions[1].results[0].output == {
        "path": "README.md",
        "content": "WORKSPACE-731",
        "size_bytes": 13,
        "sha256": ("288f952d38f1323582a4296251db4c2714ea7923e0c627c56a9cb9f27fcf0f77"),
    }
    assert "Assistant: The code word is WORKSPACE-731." in captured.out


def test_cli_executes_symbol_search_with_safe_opt_in_trace(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    """Trace symbol search without leaking paths or changing later history."""

    source_path = tmp_path / "src" / "models.py"
    source_path.parent.mkdir()
    source_path.write_text(
        "class WorkspaceModel:\n    async def inspect_workspace(self):\n        pass\n",
        encoding="utf-8",
    )
    registry = ToolRegistry()
    register_symbol_tools(registry, Workspace(tmp_path))
    provider = FakeProvider(
        [
            create_tool_response(
                ToolInvocation(
                    id="symbol-call",
                    tool_name="search_symbols",
                    arguments={"query": "WorkspaceModel"},
                )
            ),
            "Found WorkspaceModel in src/models.py.",
            "History remains clean.",
        ]
    )
    user_inputs = iter(["Find the model.", "Continue.", "/exit"])

    monkeypatch.setattr("builtins.input", lambda _: next(user_inputs))

    run_cli(
        provider,
        tool_registry=registry,
        max_tool_rounds=1,
        show_tool_traces=True,
    )

    captured = capsys.readouterr()
    trace_position = captured.out.index("Tool trace: search_symbols (symbol-call)")
    response_position = captured.out.index(
        "Assistant: Found WorkspaceModel in src/models.py."
    )
    result = provider.requests[1].tool_interactions[0].results[0]

    assert trace_position < response_position
    assert result.status == "success"
    assert result.output["matches"][0]["qualified_name"] == "WorkspaceModel"
    assert '"path":"src/models.py"' in captured.out
    assert str(tmp_path) not in captured.out
    assert provider.calls[2] == [
        {
            "role": "user",
            "content": "Find the model.",
        },
        {
            "role": "assistant",
            "content": "Found WorkspaceModel in src/models.py.",
        },
        {
            "role": "user",
            "content": "Continue.",
        },
    ]
    assert "Tool trace:" not in str(provider.calls[2])


def test_main_does_not_inject_tools_by_default(monkeypatch) -> None:
    """Construct one CLI session through the resolved runtime factory."""

    configuration = RuntimeConfiguration(
        provider_name="ollama",
        model_name="test-model",
    )
    session = Mock()
    resolve_mock = Mock(return_value=configuration)
    create_session_mock = Mock(return_value=session)
    run_cli_mock = Mock()

    monkeypatch.setattr("agent_workbench.cli.load_environment", Mock())
    monkeypatch.setattr(
        "agent_workbench.cli.resolve_runtime_configuration",
        resolve_mock,
    )
    monkeypatch.setattr(
        "agent_workbench.cli.create_agent_session",
        create_session_mock,
    )
    monkeypatch.setattr("agent_workbench.cli.run_cli", run_cli_mock)

    main([])

    create_session_mock.assert_called_once_with(
        SessionId("cli-session"),
        configuration,
    )
    run_cli_mock.assert_called_once_with(
        session,
        agent_profile=None,
    )


def test_main_injects_the_built_in_registry_when_tools_are_enabled(
    monkeypatch,
) -> None:
    """Pass the built-in registry to the CLI for an enabled configuration."""

    configuration = RuntimeConfiguration(
        provider_name="ollama",
        model_name="test-model",
        enable_tools=True,
    )
    session = Mock()
    session.tool_registry = Mock()
    create_session_mock = Mock(return_value=session)
    run_cli_mock = Mock()

    monkeypatch.setattr("agent_workbench.cli.load_environment", Mock())
    monkeypatch.setattr(
        "agent_workbench.cli.resolve_runtime_configuration",
        Mock(return_value=configuration),
    )
    monkeypatch.setattr(
        "agent_workbench.cli.create_agent_session",
        create_session_mock,
    )
    monkeypatch.setattr("agent_workbench.cli.run_cli", run_cli_mock)

    main(["--enable-tools"])

    create_session_mock.assert_called_once_with(
        SessionId("cli-session"),
        configuration,
    )
    run_cli_mock.assert_called_once_with(session, agent_profile=None)


def test_main_injects_workspace_tools_when_workspace_is_enabled(
    monkeypatch,
    tmp_path,
) -> None:
    """Pass only workspace tools to the CLI when a workspace is supplied."""

    configuration = RuntimeConfiguration(
        provider_name="ollama",
        model_name="test-model",
        workspace_root=tmp_path,
    )
    session = Mock()
    session.tool_registry = Mock()
    create_session_mock = Mock(return_value=session)
    run_cli_mock = Mock()

    monkeypatch.setattr("agent_workbench.cli.load_environment", Mock())
    monkeypatch.setattr(
        "agent_workbench.cli.resolve_runtime_configuration",
        Mock(return_value=configuration),
    )
    monkeypatch.setattr(
        "agent_workbench.cli.create_agent_session",
        create_session_mock,
    )
    monkeypatch.setattr("agent_workbench.cli.run_cli", run_cli_mock)

    main(["--workspace", str(tmp_path)])

    create_session_mock.assert_called_once_with(
        SessionId("cli-session"),
        configuration,
    )
    run_cli_mock.assert_called_once_with(session, agent_profile=None)


def test_main_combines_calculator_and_workspace_tools_in_order(
    monkeypatch,
    tmp_path,
) -> None:
    """Register all enabled tools in their documented deterministic order."""

    configuration = RuntimeConfiguration(
        provider_name="ollama",
        model_name="test-model",
        enable_tools=True,
        workspace_root=tmp_path,
    )
    session = Mock()
    session.tool_registry = Mock()
    create_session_mock = Mock(return_value=session)
    run_cli_mock = Mock()

    monkeypatch.setattr("agent_workbench.cli.load_environment", Mock())
    monkeypatch.setattr(
        "agent_workbench.cli.resolve_runtime_configuration",
        Mock(return_value=configuration),
    )
    monkeypatch.setattr(
        "agent_workbench.cli.create_agent_session",
        create_session_mock,
    )
    monkeypatch.setattr("agent_workbench.cli.run_cli", run_cli_mock)

    main(["--enable-tools", "--workspace", str(tmp_path)])

    create_session_mock.assert_called_once_with(
        SessionId("cli-session"),
        configuration,
    )
    run_cli_mock.assert_called_once_with(session, agent_profile=None)


def test_main_forwards_opt_in_tool_trace_configuration(monkeypatch) -> None:
    """Forward trace enablement only for a configured tool registry."""

    configuration = RuntimeConfiguration(
        provider_name="ollama",
        model_name="test-model",
        enable_tools=True,
        show_tool_traces=True,
    )
    session = Mock()
    session.tool_registry = Mock()
    create_session_mock = Mock(return_value=session)
    run_cli_mock = Mock()

    monkeypatch.setattr("agent_workbench.cli.load_environment", Mock())
    monkeypatch.setattr(
        "agent_workbench.cli.resolve_runtime_configuration",
        Mock(return_value=configuration),
    )
    monkeypatch.setattr(
        "agent_workbench.cli.create_agent_session",
        create_session_mock,
    )
    monkeypatch.setattr("agent_workbench.cli.run_cli", run_cli_mock)

    main(["--enable-tools", "--show-tool-traces"])

    create_session_mock.assert_called_once_with(
        SessionId("cli-session"),
        configuration,
    )
    run_cli_mock.assert_called_once_with(
        session,
        agent_profile=None,
        show_tool_traces=True,
    )


def test_main_forwards_tool_traces_to_autonomous_task(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    """Forward the trace observer to one autonomous coding task."""

    configuration = RuntimeConfiguration(
        provider_name="ollama",
        model_name="test-model",
        workspace_root=tmp_path,
        enable_actions=True,
        show_tool_traces=True,
    )
    session = Mock()
    result = Mock(
        assistant_summary="Task complete.",
        final_phase=CodingPhase.DONE,
        tool_round_count=1,
        workspace_change_applied=True,
        repair_attempt_count=1,
        completion_continuation_count=2,
        validation_succeeded=True,
        inspected_git_status=True,
        inspected_git_diff=True,
        validation_runs=(),
    )
    runner_mock = Mock(return_value=result)
    trace_mock = Mock()
    create_session_mock = Mock(return_value=session)

    monkeypatch.setattr("agent_workbench.cli.load_environment", Mock())
    monkeypatch.setattr(
        "agent_workbench.cli.resolve_runtime_configuration",
        Mock(return_value=configuration),
    )
    monkeypatch.setattr(
        "agent_workbench.cli.create_agent_session",
        create_session_mock,
    )
    monkeypatch.setattr(
        "agent_workbench.cli.run_autonomous_coding_task",
        runner_mock,
    )
    monkeypatch.setattr(
        "agent_workbench.cli._display_tool_round",
        trace_mock,
    )

    main(
        [
            "--workspace",
            str(tmp_path),
            "--enable-actions",
            "--show-tool-traces",
            "--task",
            "Fix the defect.",
        ]
    )

    create_session_mock.assert_called_once_with(
        SessionId("cli-session"),
        configuration,
        max_tool_rounds=AUTONOMOUS_MAX_TOOL_ROUNDS,
    )
    assert runner_mock.call_args.args == (
        session,
        "Fix the defect.",
    )
    assert callable(runner_mock.call_args.kwargs["tool_approval_handler"])
    assert runner_mock.call_args.kwargs["tool_round_observer"] is trace_mock
    assert callable(runner_mock.call_args.kwargs["progress_event_observer"])
    assert capsys.readouterr().out == ""


def test_main_forwards_custom_tool_round_limit_to_autonomous_session(
    monkeypatch,
    tmp_path,
) -> None:
    """Forward one resolved custom limit only to an autonomous session."""

    configuration = RuntimeConfiguration(
        provider_name="ollama",
        model_name="test-model",
        workspace_root=tmp_path,
        enable_actions=True,
        max_tool_rounds=32,
    )
    session = Mock()
    create_session_mock = Mock(return_value=session)
    run_configured_mock = Mock()

    monkeypatch.setattr("agent_workbench.cli.load_environment", Mock())
    monkeypatch.setattr(
        "agent_workbench.cli.resolve_runtime_configuration",
        Mock(return_value=configuration),
    )
    monkeypatch.setattr(
        "agent_workbench.cli.create_agent_session",
        create_session_mock,
    )
    monkeypatch.setattr(
        "agent_workbench.cli._run_configured_cli",
        run_configured_mock,
    )

    main(
        [
            "--workspace",
            str(tmp_path),
            "--enable-actions",
            "--task",
            "Fix the defect.",
            "--max-tool-rounds",
            "32",
        ]
    )

    create_session_mock.assert_called_once_with(
        SessionId("cli-session"),
        configuration,
        max_tool_rounds=32,
    )
    run_configured_mock.assert_called_once_with(
        session,
        configuration,
        task_prompt="Fix the defect.",
    )


def test_main_reports_invalid_workspace_configuration(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    """Use normal configuration-error handling for invalid workspace roots."""

    configuration = RuntimeConfiguration(
        provider_name="ollama",
        model_name="test-model",
        workspace_root=tmp_path / "missing",
    )
    run_cli_mock = Mock()

    monkeypatch.setattr("agent_workbench.cli.load_environment", Mock())
    monkeypatch.setattr(
        "agent_workbench.cli.resolve_runtime_configuration",
        Mock(return_value=configuration),
    )
    failure = ConfigurationError(
        f"Workspace root does not exist: {tmp_path / 'missing'}"
    )
    monkeypatch.setattr(
        "agent_workbench.cli.create_agent_session",
        Mock(side_effect=failure),
    )
    monkeypatch.setattr("agent_workbench.cli.run_cli", run_cli_mock)

    with pytest.raises(SystemExit) as raised:
        main(["--workspace", str(tmp_path / "missing")])

    assert raised.value.code == 1

    captured = capsys.readouterr()
    assert "Configuration error: Workspace root does not exist:" in captured.out
    assert captured.err == ""
    run_cli_mock.assert_not_called()


def test_main_discovers_explicit_workspace_configuration_before_provider(
    monkeypatch,
    tmp_path,
) -> None:
    """Start project configuration discovery at an explicit workspace."""

    configuration = RuntimeConfiguration(
        provider_name="ollama",
        model_name="project-model",
    )
    discovered = Mock()
    discover_mock = Mock(return_value=discovered)
    resolve_mock = Mock(return_value=configuration)
    create_mock = Mock(return_value=Mock(tool_registry=None))

    monkeypatch.setattr("agent_workbench.cli.load_environment", Mock())
    monkeypatch.setattr(
        "agent_workbench.cli.discover_project_configuration",
        discover_mock,
    )
    monkeypatch.setattr(
        "agent_workbench.cli.resolve_runtime_configuration",
        resolve_mock,
    )
    monkeypatch.setattr("agent_workbench.cli.create_agent_session", create_mock)
    monkeypatch.setattr("agent_workbench.cli.run_cli", Mock())

    main(["--workspace", str(tmp_path)])

    discover_mock.assert_called_once_with(
        tmp_path,
        include_project_instructions=False,
    )
    assert resolve_mock.call_args.kwargs["project_configuration"] is discovered


def test_non_coding_cli_ignores_invalid_project_instructions(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    """Do not inspect or compose coding-only instructions for interactive use."""

    configuration_path = tmp_path / PROJECT_CONFIG_RELATIVE_PATH
    configuration_path.parent.mkdir()
    configuration_path.write_text(EXPECTED_INITIALIZED_CONFIG, encoding="utf-8")
    (configuration_path.parent / "instructions.md").write_bytes(b"invalid\xff")
    monkeypatch.chdir(tmp_path)
    provider = FakeProvider()
    monkeypatch.setattr(
        "agent_workbench.session_factory.create_provider",
        lambda _provider_name, _model_name: provider,
    )
    run_cli_mock = Mock()
    monkeypatch.setattr("agent_workbench.cli.run_cli", run_cli_mock)

    main([])

    session = run_cli_mock.call_args.args[0]
    assert session.system_prompt == get_agent_profile("developer").system_prompt
    assert "<project_instructions>" not in session.system_prompt
    assert "Configuration error:" not in capsys.readouterr().out


def test_init_creates_deterministic_loadable_project_configuration(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    """Create the fixed UTF-8 project file with useful local coding defaults."""

    create_session_mock = Mock()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "agent_workbench.cli.create_agent_session",
        create_session_mock,
    )

    main(
        [
            "init",
            "--provider",
            "ollama",
            "--model",
            "qwen3-coder:30b",
        ]
    )

    configuration_path = tmp_path / PROJECT_CONFIG_RELATIVE_PATH
    contents = configuration_path.read_text(encoding="utf-8")
    assert contents == EXPECTED_INITIALIZED_CONFIG
    assert "api_key" not in contents
    assert "access_token" not in contents
    assert "password" not in contents
    assert str(Path.home()) not in contents
    loaded = load_project_configuration(configuration_path)
    assert loaded.provider == "ollama"
    assert loaded.model == "qwen3-coder:30b"
    assert loaded.enable_tools is True
    assert loaded.enable_actions is True
    assert loaded.isolated is False
    assert not (configuration_path.parent / "instructions.md").exists()
    assert capsys.readouterr().out == ("Created .agent-workbench/config.toml\n")
    create_session_mock.assert_not_called()


def test_init_dry_run_prints_exact_canonical_configuration_without_files(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    """Print only canonical TOML without calling the filesystem initializer."""

    create_configuration_mock = Mock()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "agent_workbench.cli.create_project_configuration",
        create_configuration_mock,
    )

    main(
        [
            "init",
            "--dry-run",
            "--provider",
            "ollama",
            "--model",
            "qwen3-coder:30b",
        ]
    )

    expected = render_project_configuration(
        ProjectCodingConfiguration(
            provider="ollama",
            model="qwen3-coder:30b",
            agent="developer",
            enable_tools=True,
            enable_actions=True,
            max_tool_rounds=8,
            temperature=0.2,
            top_p=0.9,
            max_output_tokens=4096,
            isolated=False,
        )
    )
    captured = capsys.readouterr()
    assert captured.out.encode() == expected.encode()
    assert captured.err == ""
    assert "Created .agent-workbench/config.toml" not in captured.out
    assert not (tmp_path / ".agent-workbench").exists()
    assert not (tmp_path / "instructions.md").exists()
    assert not any(tmp_path.iterdir())
    create_configuration_mock.assert_not_called()


def test_init_dry_run_output_exactly_matches_normal_initialization(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    """Keep preview bytes identical to the file written by normal init."""

    preview_root = tmp_path / "preview"
    normal_root = tmp_path / "normal"
    preview_root.mkdir()
    normal_root.mkdir()
    options = [
        "--provider",
        "ollama",
        "--model",
        "qwen3-coder:30b",
        "--no-enable-tools",
        "--isolated",
    ]

    monkeypatch.chdir(preview_root)
    main(["init", "--dry-run", *options])
    preview_output = capsys.readouterr().out.encode()

    monkeypatch.chdir(normal_root)
    main(["init", *options])
    capsys.readouterr()

    assert preview_output == (normal_root / PROJECT_CONFIG_RELATIVE_PATH).read_bytes()
    assert not (preview_root / ".agent-workbench").exists()


def test_init_dry_run_does_not_inspect_existing_invalid_configuration(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    """Ignore and preserve existing configuration without opening it."""

    configuration_path = tmp_path / PROJECT_CONFIG_RELATIVE_PATH
    configuration_path.parent.mkdir()
    original = b"invalid = [toml\n"
    configuration_path.write_bytes(original)
    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        if path == configuration_path:
            raise AssertionError("dry-run inspected the existing configuration")
        return original_open(path, *args, **kwargs)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "open", guarded_open)

    main(["init", "--dry-run"])

    captured = capsys.readouterr()
    assert captured.out == render_project_configuration(
        ProjectCodingConfiguration(
            provider="ollama",
            model="gpt-oss:20b",
            agent="developer",
            enable_tools=True,
            enable_actions=True,
            max_tool_rounds=8,
            temperature=0.2,
            top_p=0.9,
            max_output_tokens=4096,
            isolated=False,
        )
    )
    assert captured.err == ""
    with original_open(configuration_path, "rb") as configuration_file:
        assert configuration_file.read() == original
    assert list(configuration_path.parent.iterdir()) == [configuration_path]


def test_invalid_init_dry_run_options_create_no_files(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    """Report parser validation errors without initialization side effects."""

    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as raised:
        main(["init", "--dry-run", "--max-tool-rounds", "0"])

    assert raised.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "max tool rounds must be a positive integer" in captured.err
    assert "Traceback" not in captured.err
    assert not (tmp_path / ".agent-workbench").exists()


def test_init_dry_run_rendering_error_is_concise_and_creates_no_files(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    """Normalize canonical-renderer validation errors without write attempts."""

    create_configuration_mock = Mock()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "agent_workbench.cli.render_project_configuration",
        Mock(side_effect=ConfigurationError("simulated rendering failure")),
    )
    monkeypatch.setattr(
        "agent_workbench.cli.create_project_configuration",
        create_configuration_mock,
    )

    with pytest.raises(SystemExit) as raised:
        main(["init", "--dry-run"])

    assert raised.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == "Configuration error: simulated rendering failure\n"
    assert captured.err == ""
    assert "Traceback" not in captured.out
    assert not (tmp_path / ".agent-workbench").exists()
    create_configuration_mock.assert_not_called()


def test_init_creates_configuration_in_the_current_nested_directory(
    monkeypatch,
    tmp_path,
) -> None:
    """Treat the current nested directory as the initialized project root."""

    nested = tmp_path / "packages" / "example"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    main(
        [
            "init",
            "--provider",
            "ollama",
            "--model",
            "qwen3-coder:30b",
        ]
    )

    assert (nested / PROJECT_CONFIG_RELATIVE_PATH).is_file()
    assert not (tmp_path / PROJECT_CONFIG_RELATIVE_PATH).exists()


def test_init_explicit_disable_options_generate_false_booleans(
    monkeypatch,
    tmp_path,
) -> None:
    """Persist explicit tool and action disabling in generated TOML."""

    monkeypatch.chdir(tmp_path)

    main(
        [
            "init",
            "--provider",
            "ollama",
            "--model",
            "qwen3-coder:30b",
            "--no-enable-tools",
            "--no-enable-actions",
        ]
    )

    configuration_path = tmp_path / PROJECT_CONFIG_RELATIVE_PATH
    contents = configuration_path.read_text(encoding="utf-8")
    loaded = load_project_configuration(configuration_path)
    assert "enable_tools = false\n" in contents
    assert "enable_actions = false\n" in contents
    assert loaded.enable_tools is False
    assert loaded.enable_actions is False


def test_init_refuses_an_existing_file_without_modifying_it(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    """Never overwrite, truncate, or silently back up project configuration."""

    configuration_path = tmp_path / PROJECT_CONFIG_RELATIVE_PATH
    configuration_path.parent.mkdir()
    original = '[coding]\nmodel = "keep-me"\n'
    configuration_path.write_text(original, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as raised:
        main(["init"])

    assert raised.value.code == 1
    assert configuration_path.read_text(encoding="utf-8") == original
    assert list(configuration_path.parent.iterdir()) == [configuration_path]
    captured = capsys.readouterr()
    assert "Configuration error:" in captured.out
    assert ".agent-workbench/config.toml already exists" in captured.out
    assert "Traceback" not in captured.out
    assert captured.err == ""


def test_init_failure_leaves_no_partial_config_or_gitignore_change(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    """Clean only a newly-created partial target when writing fails."""

    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(".venv/\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    original_open = Path.open

    class FailingWriter:
        def __init__(self, file_object) -> None:
            self._file_object = file_object

        def __enter__(self):
            self._file_object.__enter__()
            return self

        def __exit__(self, *args):
            return self._file_object.__exit__(*args)

        def write(self, value):
            self._file_object.write(value[:12])
            self._file_object.flush()
            raise OSError("simulated write failure")

    def failing_open(path, *args, **kwargs):
        file_object = original_open(path, *args, **kwargs)
        if path.name == "config.toml" and args and args[0] == "x":
            return FailingWriter(file_object)
        return file_object

    monkeypatch.setattr(Path, "open", failing_open)

    with pytest.raises(SystemExit) as raised:
        main(["init"])

    assert raised.value.code == 1
    assert not (tmp_path / PROJECT_CONFIG_RELATIVE_PATH).exists()
    assert gitignore.read_text(encoding="utf-8") == ".venv/\n"
    captured = capsys.readouterr()
    assert "Configuration error:" in captured.out
    assert "Traceback" not in captured.out
    assert captured.err == ""


def test_short_code_command_uses_detected_project_root_and_configuration(
    monkeypatch,
    tmp_path,
) -> None:
    """Use root coding defaults when invoked from a nested source directory."""

    configuration_path = tmp_path / PROJECT_CONFIG_RELATIVE_PATH
    configuration_path.parent.mkdir()
    configuration_path.write_text(EXPECTED_INITIALIZED_CONFIG, encoding="utf-8")
    nested = tmp_path / "src" / "package"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    session = Mock()
    create_session_mock = Mock(return_value=session)
    run_mock = Mock()
    monkeypatch.setattr(
        "agent_workbench.cli.create_agent_session",
        create_session_mock,
    )
    monkeypatch.setattr(
        "agent_workbench.cli._run_configured_cli",
        run_mock,
    )

    main(["code", "Fix the failing tests."])

    runtime_configuration = create_session_mock.call_args.args[1]
    assert runtime_configuration.provider_name == "ollama"
    assert runtime_configuration.model_name == "qwen3-coder:30b"
    assert runtime_configuration.agent_profile.name == "Developer"
    assert runtime_configuration.workspace_root == tmp_path.resolve()
    assert runtime_configuration.enable_tools is True
    assert runtime_configuration.enable_actions is True
    assert runtime_configuration.max_tool_rounds == 8
    create_session_mock.assert_called_once_with(
        SessionId("cli-session"),
        runtime_configuration,
        max_tool_rounds=8,
    )
    run_mock.assert_called_once_with(
        session,
        runtime_configuration,
        task_prompt="Fix the failing tests.",
    )


def test_nested_direct_coding_sends_only_exact_root_instructions_as_system_context(
    monkeypatch,
    tmp_path,
) -> None:
    """Cover discovery through the provider request without instruction leakage."""

    outer_configuration = tmp_path / PROJECT_CONFIG_RELATIVE_PATH
    outer_configuration.parent.mkdir()
    outer_configuration.write_text(EXPECTED_INITIALIZED_CONFIG, encoding="utf-8")
    (outer_configuration.parent / "instructions.md").write_text(
        "Outer instructions must not load.",
        encoding="utf-8",
    )
    project = tmp_path / "workspace" / "project"
    configuration_path = project / PROJECT_CONFIG_RELATIVE_PATH
    configuration_path.parent.mkdir(parents=True)
    configuration_path.write_text(EXPECTED_INITIALIZED_CONFIG, encoding="utf-8")
    project_instructions = "# Project\n\n- Use the exact configured root.\n"
    (configuration_path.parent / "instructions.md").write_text(
        project_instructions,
        encoding="utf-8",
    )
    (project / "AGENTS.md").write_text(
        "AGENTS content must not load.",
        encoding="utf-8",
    )
    sibling = tmp_path / "workspace" / "sibling" / ".agent-workbench"
    sibling.mkdir(parents=True)
    (sibling / "instructions.md").write_text(
        "Sibling instructions must not load.",
        encoding="utf-8",
    )
    nested = project / "src" / "package"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    provider = FakeProvider(["Captured."])
    monkeypatch.setattr(
        "agent_workbench.session_factory.create_provider",
        lambda _provider_name, _model_name: provider,
    )

    task = "Fix the failing tests."

    def run_task(session, prompt, **_kwargs):
        assert prompt == task
        response = session.send(prompt, allowed_tool_names=())
        return Mock(assistant_summary=response.text)

    monkeypatch.setattr("agent_workbench.cli.run_autonomous_coding_task", run_task)

    main(
        [
            "code",
            "--system-prompt",
            "Existing system instructions.",
            task,
        ]
    )

    assert provider.requests[0].system_prompt == (
        "Existing system instructions.\n\n"
        "<project_instructions>\n"
        f"{project_instructions}\n"
        "</project_instructions>"
    )
    assert provider.requests[0].system_prompt.count(project_instructions) == 1
    assert provider.requests[0].messages == [{"role": "user", "content": task}]
    assert "Outer instructions" not in provider.requests[0].system_prompt
    assert "Sibling instructions" not in provider.requests[0].system_prompt
    assert "AGENTS content" not in provider.requests[0].system_prompt


def test_invalid_project_configuration_prevents_provider_construction(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    """Fail safely before constructing any provider-backed session."""

    failure = ConfigurationError(
        "project configuration .agent-workbench/config.toml: "
        "unknown key [coding].api_key"
    )
    create_mock = Mock()

    monkeypatch.setattr("agent_workbench.cli.load_environment", Mock())
    monkeypatch.setattr(
        "agent_workbench.cli.discover_project_configuration",
        Mock(side_effect=failure),
    )
    monkeypatch.setattr("agent_workbench.cli.create_agent_session", create_mock)

    with pytest.raises(SystemExit) as raised:
        main(["code", "--workspace", str(tmp_path), "--task", "Fix it."])

    assert raised.value.code == 1

    create_mock.assert_not_called()
    output = capsys.readouterr().out
    assert "Configuration error:" in output
    assert "[coding].api_key" in output
    assert str(tmp_path) not in output


def test_invalid_project_instructions_prevent_provider_construction(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    """Reject invalid project instructions before a session/provider is built."""

    configuration_path = tmp_path / PROJECT_CONFIG_RELATIVE_PATH
    configuration_path.parent.mkdir()
    configuration_path.write_text(EXPECTED_INITIALIZED_CONFIG, encoding="utf-8")
    (configuration_path.parent / "instructions.md").write_bytes(b"invalid\xff")
    create_mock = Mock()
    monkeypatch.setattr("agent_workbench.cli.load_environment", Mock())
    monkeypatch.setattr("agent_workbench.cli.create_agent_session", create_mock)

    with pytest.raises(SystemExit) as raised:
        main(["code", "--workspace", str(tmp_path), "Fix it."])

    assert raised.value.code == 1

    create_mock.assert_not_called()
    output = capsys.readouterr().out
    assert "Configuration error:" in output
    assert "not valid UTF-8" in output
    assert str(tmp_path) not in output


@pytest.mark.parametrize(
    "interrupt_source",
    [
        "workspace discovery",
        "provider completion",
        "model tool-calling",
        "approval input",
        "validation",
        "direct coding",
    ],
)
def test_direct_coding_interrupts_exit_cleanly_with_preserved_workspace(
    monkeypatch,
    tmp_path,
    capsys,
    interrupt_source,
) -> None:
    """Normalize direct-workflow interrupts at one outer CLI boundary."""

    changed_path = tmp_path / "approved.py"
    session = Mock()

    if interrupt_source == "workspace discovery":
        monkeypatch.setattr(
            "agent_workbench.cli.discover_project_configuration",
            Mock(side_effect=KeyboardInterrupt),
        )
    else:
        monkeypatch.setattr(
            "agent_workbench.cli.create_agent_session",
            Mock(return_value=session),
        )

        def interrupt_task(_session, _prompt, **kwargs):
            if interrupt_source == "approval input":
                request = Mock(
                    invocation=Mock(tool_name="run_pytest"),
                    preview={},
                )
                monkeypatch.setattr(
                    "builtins.input",
                    Mock(side_effect=KeyboardInterrupt),
                )
                kwargs["tool_approval_handler"](request)
            if interrupt_source in {
                "validation",
                "direct coding",
            }:
                changed_path.write_text("approved = True\n", encoding="utf-8")
            raise KeyboardInterrupt

        monkeypatch.setattr(
            "agent_workbench.cli.run_autonomous_coding_task",
            interrupt_task,
        )

    with pytest.raises(SystemExit) as raised:
        main(
            [
                "code",
                "--provider",
                "ollama",
                "--model",
                "test-model",
                "--workspace",
                str(tmp_path),
                "--enable-actions",
                "--task",
                "Fix it.",
            ]
        )

    assert raised.value.code == 130
    output = capsys.readouterr()
    assert output.out.count(CANCELLATION_MESSAGE) == 1
    assert output.err == ""
    assert "Traceback" not in output.out
    assert "Workspace preserved." in output.out
    if interrupt_source in {"validation", "direct coding"}:
        assert changed_path.read_text(encoding="utf-8") == "approved = True\n"


def test_isolated_coding_interrupt_exits_cleanly_without_cleanup(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    """Propagate isolated cancellation without cleanup or failure conversion."""

    target = tmp_path / "isolated"
    sentinel = target / "preserved.txt"

    def interrupt_after_isolated_change(*_args, **_kwargs):
        target.mkdir()
        sentinel.write_text("approved isolated change\n", encoding="utf-8")
        raise KeyboardInterrupt

    runner = Mock(side_effect=interrupt_after_isolated_change)
    monkeypatch.setattr(
        "agent_workbench.cli.run_isolated_autonomous_workflow",
        runner,
    )

    with pytest.raises(SystemExit) as raised:
        main(
            [
                "code",
                "--provider",
                "ollama",
                "--model",
                "test-model",
                "--workspace",
                str(tmp_path),
                "--enable-actions",
                "--task",
                "Fix it.",
                "--worktree-path",
                str(target),
                "--worktree-branch",
                "agent/cancel",
                "--commit-message",
                "fix: cancelled task",
            ]
        )

    assert raised.value.code == 130
    captured = capsys.readouterr()
    assert captured.out.count(CANCELLATION_MESSAGE) == 1
    assert captured.out == f"{CANCELLATION_MESSAGE}\n"
    assert captured.err == ""
    assert "Traceback" not in captured.out
    assert target.is_dir()
    assert sentinel.is_file()
    assert sentinel.read_text(encoding="utf-8") == "approved isolated change\n"


def test_isolated_preflight_identity_failure_exits_with_status_1(
    tmp_path: Path,
    capsys,
) -> None:
    """Return exit status 1 before any worktree when local identity is absent."""

    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=source, check=True, capture_output=True
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Temp",
            "-c",
            "user.email=t@example.invalid",
            "commit",
            "--allow-empty",
            "-m",
            "initial",
        ],
        check=True,
        capture_output=True,
    )
    target = tmp_path / "isolated"

    with pytest.raises(SystemExit) as raised:
        main(
            [
                "code",
                "--provider",
                "ollama",
                "--model",
                "test-model",
                "--workspace",
                str(source),
                "--enable-actions",
                "--task",
                "Fix it.",
                "--worktree-path",
                str(target),
                "--worktree-branch",
                "agent/preflight-cli",
                "--commit-message",
                "fix: preflight",
            ]
        )

    assert raised.value.code == 1
    captured = capsys.readouterr()
    assert "config --local" in captured.out
    assert not target.exists()
    result = subprocess.run(
        ["git", "-C", str(source), "branch", "--list", "agent/preflight-cli"],
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == ""


def test_main_uses_interactive_runtime_setup(
    monkeypatch,
) -> None:
    """Use the setup result before constructing the provider."""

    response_format = JSONResponseFormat(
        name="result",
        schema={
            "type": "object",
            "properties": {
                "answer": {
                    "type": "string",
                },
            },
            "required": [
                "answer",
            ],
            "additionalProperties": False,
        },
    )

    configuration = RuntimeConfiguration(
        provider_name="ollama",
        model_name="test-model",
        response_format=response_format,
    )
    session = Mock()
    session.tool_registry = None

    setup_mock = Mock(return_value=configuration)
    create_session_mock = Mock(return_value=session)
    run_cli_mock = Mock()

    monkeypatch.setattr(
        "agent_workbench.cli.run_interactive_setup",
        setup_mock,
    )
    monkeypatch.setattr(
        "agent_workbench.cli.create_agent_session",
        create_session_mock,
    )
    monkeypatch.setattr(
        "agent_workbench.cli.run_cli",
        run_cli_mock,
    )

    main(["--setup"])

    setup_mock.assert_called_once_with()
    create_session_mock.assert_called_once_with(
        SessionId("cli-session"),
        configuration,
    )
    run_cli_mock.assert_called_once_with(
        session,
        agent_profile=None,
    )


def test_main_routes_code_command_to_existing_autonomous_workflow(
    monkeypatch,
    tmp_path,
) -> None:
    """Route the explicit code command through the existing task workflow."""

    configuration = RuntimeConfiguration(
        provider_name="ollama",
        model_name="test-model",
        workspace_root=tmp_path,
        enable_actions=True,
    )
    session = Mock()
    result = Mock(
        assistant_summary="Task complete.",
        final_phase=CodingPhase.DONE,
        tool_round_count=1,
        workspace_change_applied=True,
        repair_attempt_count=0,
        completion_continuation_count=0,
        validation_succeeded=True,
        inspected_git_status=True,
        inspected_git_diff=True,
        validation_runs=(),
    )
    resolve_configuration_mock = Mock(return_value=configuration)
    create_session_mock = Mock(return_value=session)
    runner_mock = Mock(return_value=result)

    monkeypatch.setattr("agent_workbench.cli.load_environment", Mock())
    monkeypatch.setattr(
        "agent_workbench.cli.resolve_runtime_configuration",
        resolve_configuration_mock,
    )
    monkeypatch.setattr(
        "agent_workbench.cli.create_agent_session",
        create_session_mock,
    )
    monkeypatch.setattr(
        "agent_workbench.cli.run_autonomous_coding_task",
        runner_mock,
    )

    main(
        [
            "code",
            "--workspace",
            str(tmp_path),
            "--enable-actions",
            "--task",
            "Fix the defect.",
        ]
    )

    parsed_arguments = resolve_configuration_mock.call_args.args[0]
    assert parsed_arguments.workspace_root == tmp_path
    assert parsed_arguments.enable_actions is True
    assert parsed_arguments.task_prompt == "Fix the defect."
    create_session_mock.assert_called_once_with(
        SessionId("cli-session"),
        configuration,
        max_tool_rounds=AUTONOMOUS_MAX_TOOL_ROUNDS,
    )
    assert runner_mock.call_args.args == (
        session,
        "Fix the defect.",
    )
    assert callable(runner_mock.call_args.kwargs["tool_approval_handler"])
    assert runner_mock.call_args.kwargs["tool_round_observer"] is None
    assert callable(runner_mock.call_args.kwargs["progress_event_observer"])


def test_default_autonomous_output_is_concise_stable_and_hides_model_prose(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    """Render controller evidence without raw internals or assistant prose."""

    configuration = RuntimeConfiguration(
        provider_name="ollama",
        model_name="test-model",
        workspace_root=tmp_path,
        enable_actions=True,
    )
    session = Mock()

    def run_task(_session, _prompt, **kwargs):
        observer = kwargs["progress_event_observer"]
        for event in (
            CodingProgressEvent(
                phase=CodingPhase.DISCOVER,
                kind=CodingProgressKind.PHASE_STARTED,
            ),
            CodingProgressEvent(
                phase=CodingPhase.DISCOVER,
                kind=CodingProgressKind.PHASE_COMPLETED,
            ),
            CodingProgressEvent(
                phase=CodingPhase.EDIT,
                kind=CodingProgressKind.PHASE_STARTED,
            ),
            CodingProgressEvent(
                phase=CodingPhase.EDIT,
                kind=CodingProgressKind.WORKSPACE_CHANGED,
                path="src/calculator.py",
            ),
            CodingProgressEvent(
                phase=CodingPhase.VALIDATE,
                kind=CodingProgressKind.PHASE_STARTED,
            ),
            CodingProgressEvent(
                phase=CodingPhase.VALIDATE,
                kind=CodingProgressKind.VALIDATION_RESULT,
                tool_name="run_ruff_format",
                result_status="success",
                exit_code=0,
            ),
            CodingProgressEvent(
                phase=CodingPhase.VALIDATE,
                kind=CodingProgressKind.VALIDATION_RESULT,
                tool_name="run_ruff_check",
                result_status="success",
                exit_code=0,
            ),
            CodingProgressEvent(
                phase=CodingPhase.VALIDATE,
                kind=CodingProgressKind.VALIDATION_RESULT,
                tool_name="run_pytest",
                result_status="success",
                exit_code=0,
                validation_summary="5 passed",
            ),
            CodingProgressEvent(
                phase=CodingPhase.VERIFY,
                kind=CodingProgressKind.PHASE_STARTED,
            ),
            CodingProgressEvent(
                phase=CodingPhase.VERIFY,
                kind=CodingProgressKind.CHANGED_PATH_COUNT,
                changed_path_count=1,
            ),
            CodingProgressEvent(
                phase=CodingPhase.DONE,
                kind=CodingProgressKind.DONE,
            ),
        ):
            observer(event)
        return Mock(
            assistant_summary=(
                "Long provider completion with SHA "
                "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef "
                'and {"tool_call":"private"}'
            )
        )

    monkeypatch.setattr("agent_workbench.cli.load_environment", Mock())
    monkeypatch.setattr(
        "agent_workbench.cli.resolve_runtime_configuration",
        Mock(return_value=configuration),
    )
    monkeypatch.setattr(
        "agent_workbench.cli.create_agent_session",
        Mock(return_value=session),
    )
    monkeypatch.setattr("agent_workbench.cli.run_autonomous_coding_task", run_task)

    main(
        [
            "code",
            "--workspace",
            str(tmp_path),
            "--enable-actions",
            "--task",
            "Fix the defect.",
        ]
    )

    assert capsys.readouterr().out == (
        "[DISCOVER] Inspecting workspace\n"
        "[DISCOVER] Inspection complete\n"
        "[EDIT] Applying controlled workspace changes\n"
        "[EDIT] Changed src/calculator.py\n"
        "[VALIDATE] Running controller-owned validation\n"
        "[VALIDATE] Ruff format passed\n"
        "[VALIDATE] Ruff check passed\n"
        "[VALIDATE] Pytest passed: 5 passed\n"
        "[VERIFY] Inspecting final workspace changes\n"
        "[VERIFY] 1 changed file\n"
        "[DONE] Task completed successfully\n"
    )


def test_progress_renderer_covers_repair_skip_plural_and_terminal_failure(
    capsys,
) -> None:
    """Keep exceptional progress concise, stable, and recovery-oriented."""

    for event in (
        CodingProgressEvent(
            phase=CodingPhase.VALIDATE,
            kind=CodingProgressKind.VALIDATION_RESULT,
            tool_name="run_ruff_format",
            result_status="success",
            skipped=True,
        ),
        CodingProgressEvent(
            phase=CodingPhase.REPAIR,
            kind=CodingProgressKind.REPAIR_STARTED,
            repair_attempt=1,
            max_repair_attempts=2,
        ),
        CodingProgressEvent(
            phase=CodingPhase.REPAIR,
            kind=CodingProgressKind.WORKSPACE_CHANGED,
            path="module.py",
            repair_attempt=1,
            max_repair_attempts=2,
        ),
        CodingProgressEvent(
            phase=CodingPhase.EDIT,
            kind=CodingProgressKind.ACTION_ARGUMENTS_REJECTED,
            path="module.py",
            reason="apply_file_patch requires corrected structured arguments",
        ),
        CodingProgressEvent(
            phase=CodingPhase.VERIFY,
            kind=CodingProgressKind.CHANGED_PATH_COUNT,
            changed_path_count=2,
        ),
        CodingProgressEvent(
            phase=CodingPhase.VERIFY,
            kind=CodingProgressKind.TERMINAL_FAILURE,
            reason="unexpected changed paths before DONE: unrelated.py",
            repair_attempt=1,
            max_repair_attempts=2,
            workspace_preserved=True,
        ),
    ):
        _display_coding_progress(event)

    assert capsys.readouterr().out == (
        "[VALIDATE] Ruff format skipped: no approved changed Python files\n"
        "[REPAIR 1/2] Resolving validation failures\n"
        "[REPAIR 1/2] Changed module.py\n"
        "[EDIT] Controlled action arguments rejected for module.py: "
        "apply_file_patch requires corrected structured arguments\n"
        "[VERIFY] 2 changed files\n"
        "[FAILED] VERIFY: unexpected changed paths before DONE: unrelated.py; "
        "validation repair attempts 1/2; "
        "workspace preserved for manual recovery\n"
    )


def test_cli_progress_omits_unsupported_control_character_field_names(
    capsys,
) -> None:
    """Render registry guidance without terminal-control or private key names."""

    calculator = create_calculator_definition()
    registry = ToolRegistry()
    registry.register(
        calculator,
        lambda arguments: arguments,
        requires_approval=True,
    )
    unsupported_name = (
        "/home/operator/private.env\nPRIVATE_TOKEN=secret-value\rreturn\ttab\x1b[31mred"
    )
    error = registry.argument_validation_error(
        ToolInvocation(
            id="invalid-calculator",
            tool_name="calculator",
            arguments={
                "expression": "2 + 2",
                unsupported_name: "untrusted value",
            },
        )
    )
    assert error is not None

    _display_coding_progress(
        CodingProgressEvent(
            phase=CodingPhase.EDIT,
            kind=CodingProgressKind.ACTION_ARGUMENTS_REJECTED,
            reason=error,
        )
    )

    output = capsys.readouterr().out
    assert (
        "arguments contain 1 unsupported field; additional fields are not allowed"
        in output
    )
    assert unsupported_name not in output
    assert "/home/operator" not in output
    assert "PRIVATE_TOKEN" not in output
    assert "secret-value" not in output
    assert "\x1b[31m" not in output


def test_complete_assistant_summary_is_explicitly_opt_in(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    """Display complete model prose only when the dedicated flag is enabled."""

    configuration = RuntimeConfiguration(
        provider_name="ollama",
        model_name="test-model",
        workspace_root=tmp_path,
        enable_actions=True,
        show_assistant_summary=True,
    )
    session = Mock()

    def run_task(_session, _prompt, **kwargs):
        kwargs["progress_event_observer"](
            CodingProgressEvent(
                phase=CodingPhase.DONE,
                kind=CodingProgressKind.DONE,
            )
        )
        return Mock(assistant_summary="Complete provider summary.")

    monkeypatch.setattr("agent_workbench.cli.load_environment", Mock())
    monkeypatch.setattr(
        "agent_workbench.cli.resolve_runtime_configuration",
        Mock(return_value=configuration),
    )
    monkeypatch.setattr(
        "agent_workbench.cli.create_agent_session",
        Mock(return_value=session),
    )
    monkeypatch.setattr("agent_workbench.cli.run_autonomous_coding_task", run_task)

    main(
        [
            "code",
            "--workspace",
            str(tmp_path),
            "--enable-actions",
            "--show-assistant-summary",
            "--task",
            "Fix the defect.",
        ]
    )

    assert capsys.readouterr().out == (
        "[DONE] Task completed successfully\n"
        "\nAssistant summary:\n"
        "Complete provider summary.\n"
    )


def test_successful_coding_workflow_exits_with_zero(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    """Keep successful autonomous coding exit status unchanged at zero."""

    configuration = RuntimeConfiguration(
        provider_name="ollama",
        model_name="test-model",
        workspace_root=tmp_path,
        enable_actions=True,
    )

    def run_task(_session, _prompt, **kwargs):
        kwargs["progress_event_observer"](
            CodingProgressEvent(
                phase=CodingPhase.DONE,
                kind=CodingProgressKind.DONE,
            )
        )
        return Mock(assistant_summary="Done")

    monkeypatch.setattr("agent_workbench.cli.load_environment", Mock())
    monkeypatch.setattr(
        "agent_workbench.cli.resolve_runtime_configuration",
        Mock(return_value=configuration),
    )
    monkeypatch.setattr(
        "agent_workbench.cli.create_agent_session",
        Mock(return_value=Mock()),
    )
    monkeypatch.setattr("agent_workbench.cli.run_autonomous_coding_task", run_task)

    exit_code = run_main_and_capture_exit_code(
        [
            "code",
            "--workspace",
            str(tmp_path),
            "--enable-actions",
            "--task",
            "Fix the defect.",
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out == "[DONE] Task completed successfully\n"


def test_terminal_failure_progress_is_not_duplicated_by_cli_error(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    """Print one clear terminal failure with attempts and preservation state."""

    configuration = RuntimeConfiguration(
        provider_name="ollama",
        model_name="test-model",
        workspace_root=tmp_path,
        enable_actions=True,
    )

    def fail_task(_session, _prompt, **kwargs):
        kwargs["progress_event_observer"](
            CodingProgressEvent(
                phase=CodingPhase.VALIDATE,
                kind=CodingProgressKind.TERMINAL_FAILURE,
                reason="unexpected changed paths after run_ruff_format: unrelated.py",
                repair_attempt=1,
                max_repair_attempts=2,
                workspace_preserved=True,
            )
        )
        raise CompletionError("Internal duplicate must stay hidden.")

    monkeypatch.setattr("agent_workbench.cli.load_environment", Mock())
    monkeypatch.setattr(
        "agent_workbench.cli.resolve_runtime_configuration",
        Mock(return_value=configuration),
    )
    monkeypatch.setattr(
        "agent_workbench.cli.create_agent_session",
        Mock(return_value=Mock()),
    )
    monkeypatch.setattr("agent_workbench.cli.run_autonomous_coding_task", fail_task)

    exit_code = run_main_and_capture_exit_code(
        [
            "code",
            "--workspace",
            str(tmp_path),
            "--enable-actions",
            "--task",
            "Fix the defect.",
        ]
    )

    assert exit_code == 1
    assert capsys.readouterr().out == (
        "[FAILED] VALIDATE: unexpected changed paths after run_ruff_format: "
        "unrelated.py; validation repair attempts 1/2; "
        "workspace preserved for manual recovery\n"
    )


def test_setup_command_exit_behavior_is_unchanged(
    monkeypatch,
) -> None:
    """Keep unrelated non-coding setup flow returning a successful exit code."""

    configuration = RuntimeConfiguration(
        provider_name="ollama",
        model_name="test-model",
    )
    monkeypatch.setattr(
        "agent_workbench.cli.run_interactive_setup",
        Mock(return_value=configuration),
    )
    monkeypatch.setattr(
        "agent_workbench.cli.create_agent_session",
        Mock(return_value=Mock(tool_registry=None)),
    )
    monkeypatch.setattr("agent_workbench.cli.run_cli", Mock())

    exit_code = run_main_and_capture_exit_code(["--setup"])

    assert exit_code == 0


def test_isolated_autonomous_output_preserves_progress_order_without_hashes(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    """Use the same coding progress before concise isolated commit evidence."""

    target = tmp_path / "isolated"
    configuration = RuntimeConfiguration(
        provider_name="ollama",
        model_name="test-model",
        workspace_root=tmp_path,
        enable_actions=True,
        worktree_path=target,
        worktree_branch="agent/fix",
    )

    def run_isolated(*_args, **kwargs):
        observer = kwargs["progress_event_observer"]
        observer(
            CodingProgressEvent(
                phase=CodingPhase.DISCOVER,
                kind=CodingProgressKind.PHASE_STARTED,
            )
        )
        observer(
            CodingProgressEvent(
                phase=CodingPhase.DONE,
                kind=CodingProgressKind.DONE,
            )
        )
        return Mock(
            coding_result=Mock(assistant_summary="Hidden long completion."),
            commit_result=Mock(
                branch_name="agent/fix",
                new_head="a" * 40,
                operation_count=1,
            ),
            final_worktree_state=Mock(clean=True),
        )

    monkeypatch.setattr("agent_workbench.cli.load_environment", Mock())
    monkeypatch.setattr(
        "agent_workbench.cli.resolve_runtime_configuration",
        Mock(return_value=configuration),
    )
    monkeypatch.setattr(
        "agent_workbench.cli.run_isolated_autonomous_workflow",
        run_isolated,
    )

    main(
        [
            "code",
            "--workspace",
            str(tmp_path),
            "--worktree-path",
            str(target),
            "--worktree-branch",
            "agent/fix",
            "--enable-actions",
            "--task",
            "Fix the defect.",
            "--commit-message",
            "fix: defect",
        ]
    )

    assert capsys.readouterr().out == (
        "[DISCOVER] Inspecting workspace\n"
        "[DONE] Task completed successfully\n"
        "[ISOLATED] Created local commit on agent/fix with 1 changed file\n"
        "[ISOLATED] Worktree clean; primary workspace unchanged; "
        "worktree and local branch preserved\n"
    )


def test_scripted_cli_progress_orders_failure_repair_success_verify_and_done(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    """Prove the complete normal operator-visible coding progress sequence."""

    repository = create_cli_coding_repository(tmp_path / "project")
    original = (repository / "module.py").read_text(encoding="utf-8")
    multiplied = original.replace("left - right", "left * right")
    provider = FakeProvider(
        [
            ChatResponse(text="Discovery complete."),
            coding_replacement_response(
                "bad-edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left * right",
            ),
            ChatResponse(
                text="Provider completion after first edit with private prose."
            ),
            ChatResponse(
                text="Provider completion after first edit with private prose."
            ),
            coding_replacement_response(
                "repair",
                expected_content=multiplied,
                expected_text="return left * right",
                replacement_text="return left + right",
            ),
            ChatResponse(
                text=(
                    "Provider long completion must remain hidden. "
                    "replacement_content and tool-call JSON stay private."
                )
            ),
            ChatResponse(
                text=(
                    "Provider long completion must remain hidden. "
                    "replacement_content and tool-call JSON stay private."
                )
            ),
        ]
    )
    session = create_cli_coding_session(repository, provider)
    configuration = RuntimeConfiguration(
        provider_name="ollama",
        model_name="scripted",
        workspace_root=repository,
        enable_actions=True,
    )

    monkeypatch.setattr("agent_workbench.cli.load_environment", Mock())
    monkeypatch.setattr(
        "agent_workbench.cli.resolve_runtime_configuration",
        Mock(return_value=configuration),
    )
    monkeypatch.setattr(
        "agent_workbench.cli.create_agent_session",
        Mock(return_value=session),
    )
    monkeypatch.setattr(
        "agent_workbench.cli._prompt_for_tool_approval",
        Mock(return_value=ToolApprovalDecision.APPROVE),
    )

    main(
        [
            "code",
            "--provider",
            "ollama",
            "--model",
            "scripted",
            "--workspace",
            str(repository),
            "--enable-tools",
            "--enable-actions",
            "--agent",
            "developer",
            "--task",
            "Fix the failing tests.",
        ]
    )

    output = capsys.readouterr().out
    assert output.splitlines() == [
        "[DISCOVER] Inspecting workspace",
        "[DISCOVER] Inspection complete",
        "[EDIT] Applying controlled workspace changes",
        "[EDIT] Changed module.py",
        "[VALIDATE] Running controller-owned validation",
        "[VALIDATE] Ruff format passed",
        "[VALIDATE] Ruff check passed",
        "[VALIDATE] Pytest failed: 1 failed",
        "[REPAIR 1/2] Resolving validation failures",
        "[REPAIR 1/2] Changed module.py",
        "[VALIDATE] Running controller-owned validation",
        "[VALIDATE] Ruff format passed",
        "[VALIDATE] Ruff check passed",
        "[VALIDATE] Pytest passed: 1 passed",
        "[VERIFY] Inspecting final workspace changes",
        "[VERIFY] 1 changed file",
        "[DONE] Task completed successfully",
    ]
    assert '"tool_call"' not in output
    assert '"tool_name"' not in output
    assert "replacement_content" not in output
    assert "return left * right" not in output
    assert hashlib.sha256(original.encode("utf-8")).hexdigest() not in output
    assert "Provider long completion" not in output


def test_scripted_cli_separates_successful_patch_from_later_stale_repeat(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    """Report a preserved successful change before a later rejected action."""

    repository = create_cli_coding_repository(tmp_path / "project")
    original = (repository / "module.py").read_text(encoding="utf-8")
    corrected = original.replace("left - right", "left + right")
    arguments = {
        "path": "module.py",
        "expected_content": original,
        "replacement_content": corrected,
    }
    repeated_patch = create_tool_response(
        ToolInvocation(
            id="ollama-tool-call-1",
            tool_name="apply_file_patch",
            arguments=arguments,
        )
    )
    provider = FakeProvider(
        [
            ChatResponse(text="Discovery complete."),
            repeated_patch,
            repeated_patch,
            ChatResponse(text="Edit complete."),
            ChatResponse(text="Edit complete."),
        ]
    )
    session = create_cli_coding_session(repository, provider)
    configuration = RuntimeConfiguration(
        provider_name="ollama",
        model_name="scripted",
        workspace_root=repository,
        enable_actions=True,
    )
    approval = Mock(return_value=ToolApprovalDecision.APPROVE)
    monkeypatch.setattr("agent_workbench.cli.load_environment", Mock())
    monkeypatch.setattr(
        "agent_workbench.cli.resolve_runtime_configuration",
        Mock(return_value=configuration),
    )
    monkeypatch.setattr(
        "agent_workbench.cli.create_agent_session",
        Mock(return_value=session),
    )
    monkeypatch.setattr("agent_workbench.cli._prompt_for_tool_approval", approval)

    main(
        [
            "code",
            "--workspace",
            str(repository),
            "--enable-actions",
            "--task",
            "Fix the failing tests.",
        ]
    )

    output = capsys.readouterr().out
    assert output.splitlines() == [
        "[DISCOVER] Inspecting workspace",
        "[DISCOVER] Inspection complete",
        "[EDIT] Applying controlled workspace changes",
        "[EDIT] Changed module.py",
        (
            "[EDIT] Later controlled action rejected for module.py: "
            "Approval preview failed for apply_file_patch: "
            "apply_file_patch expected content does not match."
        ),
        "[VALIDATE] Running controller-owned validation",
        "[VALIDATE] Ruff format passed",
        "[VALIDATE] Ruff check passed",
        "[VALIDATE] Pytest passed: 1 passed",
        "[VERIFY] Inspecting final workspace changes",
        "[VERIFY] 1 changed file",
        "[DONE] Task completed successfully",
    ]
    patch_approval_calls = [
        approval_call
        for approval_call in approval.call_args_list
        if approval_call.args[0].invocation.tool_name == "apply_file_patch"
    ]
    assert len(patch_approval_calls) == 1
    assert (repository / "module.py").read_text(encoding="utf-8") == corrected


def test_scripted_cli_unexpected_formatter_path_fails_without_done(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    """Prove unexpected formatter mutations produce one terminal failure."""

    repository = create_cli_coding_repository(tmp_path / "project")
    original = (repository / "module.py").read_text(encoding="utf-8")
    provider = FakeProvider(
        [
            ChatResponse(text="Discovery complete."),
            coding_replacement_response(
                "edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="Hidden provider completion."),
            ChatResponse(text="Hidden provider completion."),
        ]
    )
    session = create_cli_coding_session(repository, provider)
    configuration = RuntimeConfiguration(
        provider_name="ollama",
        model_name="scripted",
        workspace_root=repository,
        enable_actions=True,
    )
    from agent_workbench import validation_tools

    original_run_validation = validation_tools.run_validation

    def intrusive_validation(workspace, tool_name, arguments):
        result = original_run_validation(workspace, tool_name, arguments)
        if tool_name == "run_ruff_format":
            (repository / "unexpected.txt").write_text(
                "preserve me\n",
                encoding="utf-8",
            )
        return result

    monkeypatch.setattr(validation_tools, "run_validation", intrusive_validation)
    monkeypatch.setattr("agent_workbench.cli.load_environment", Mock())
    monkeypatch.setattr(
        "agent_workbench.cli.resolve_runtime_configuration",
        Mock(return_value=configuration),
    )
    monkeypatch.setattr(
        "agent_workbench.cli.create_agent_session",
        Mock(return_value=session),
    )
    monkeypatch.setattr(
        "agent_workbench.cli._prompt_for_tool_approval",
        Mock(return_value=ToolApprovalDecision.APPROVE),
    )

    exit_code = run_main_and_capture_exit_code(
        [
            "code",
            "--workspace",
            str(repository),
            "--enable-actions",
            "--task",
            "Fix the failing tests.",
        ]
    )

    assert exit_code == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[-1] == (
        "[FAILED] VALIDATE: unexpected changed paths after run_ruff_format: "
        "unexpected.txt; validation repair attempts 0/2; "
        "workspace preserved for manual recovery"
    )
    assert "[DONE] Task completed successfully" not in lines
    assert (repository / "unexpected.txt").read_text(encoding="utf-8") == (
        "preserve me\n"
    )


def test_recover_command_routes_before_environment_and_session_construction(
    monkeypatch,
) -> None:
    """Handle recover through the read-only path before provider runtime setup."""

    load_environment_mock = Mock()
    run_recovery_mock = Mock()
    create_session_mock = Mock()

    monkeypatch.setattr("agent_workbench.cli.load_environment", load_environment_mock)
    monkeypatch.setattr(
        "agent_workbench.cli._run_recovery_inspection",
        run_recovery_mock,
    )
    monkeypatch.setattr(
        "agent_workbench.cli.create_agent_session",
        create_session_mock,
    )

    main(
        [
            "recover",
            "--workspace",
            ".",
            "--lifecycle-store",
            "./store",
            "--session-id",
            "task-001",
        ]
    )

    load_environment_mock.assert_not_called()
    create_session_mock.assert_not_called()
    run_recovery_mock.assert_called_once()


def test_isolated_cli_forwards_explicit_lifecycle_store_and_session_id(
    monkeypatch,
    tmp_path,
) -> None:
    """Forward the exact lifecycle store and SessionId into isolated workflow."""

    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / "isolated"
    lifecycle_store_path = tmp_path / "lifecycle-store"
    lifecycle_store_path.mkdir()
    configuration = RuntimeConfiguration(
        provider_name="ollama",
        model_name="test-model",
        workspace_root=source,
        enable_actions=True,
        worktree_path=target,
        worktree_branch="agent/task",
    )
    runner = Mock(
        return_value=Mock(
            coding_result=Mock(assistant_summary="Hidden."),
            commit_result=Mock(branch_name="agent/task", operation_count=1),
            final_worktree_state=Mock(clean=True),
        )
    )

    monkeypatch.setattr("agent_workbench.cli.load_environment", Mock())
    monkeypatch.setattr(
        "agent_workbench.cli.resolve_runtime_configuration",
        Mock(return_value=configuration),
    )
    monkeypatch.setattr(
        "agent_workbench.cli.run_isolated_autonomous_workflow",
        runner,
    )

    main(
        [
            "code",
            "--workspace",
            str(source),
            "--enable-actions",
            "--task",
            "Fix it.",
            "--worktree-path",
            str(target),
            "--worktree-branch",
            "agent/task",
            "--commit-message",
            "fix: exact",
            "--lifecycle-store",
            str(lifecycle_store_path),
            "--session-id",
            "task-001",
        ]
    )

    assert runner.call_args.args[0] == SessionId("task-001")
    assert isinstance(
        runner.call_args.kwargs["lifecycle_store"],
        IsolatedCommitLifecycleStore,
    )
    assert runner.call_args.kwargs["max_tool_rounds"] == configuration.max_tool_rounds


def test_isolated_cli_does_not_construct_lifecycle_store_when_absent(
    monkeypatch,
    tmp_path,
) -> None:
    """Keep existing isolated behavior when lifecycle options are omitted."""

    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / "isolated"
    configuration = RuntimeConfiguration(
        provider_name="ollama",
        model_name="test-model",
        workspace_root=source,
        enable_actions=True,
        worktree_path=target,
        worktree_branch="agent/task",
    )
    runner = Mock(
        return_value=Mock(
            coding_result=Mock(assistant_summary="Hidden."),
            commit_result=Mock(branch_name="agent/task", operation_count=1),
            final_worktree_state=Mock(clean=True),
        )
    )
    constructor_mock = Mock(side_effect=AssertionError("must not be constructed"))

    monkeypatch.setattr("agent_workbench.cli.load_environment", Mock())
    monkeypatch.setattr(
        "agent_workbench.cli.resolve_runtime_configuration",
        Mock(return_value=configuration),
    )
    monkeypatch.setattr(
        "agent_workbench.cli.run_isolated_autonomous_workflow",
        runner,
    )
    monkeypatch.setattr(
        "agent_workbench.cli.IsolatedCommitLifecycleStore",
        constructor_mock,
    )

    main(
        [
            "code",
            "--workspace",
            str(source),
            "--enable-actions",
            "--task",
            "Fix it.",
            "--worktree-path",
            str(target),
            "--worktree-branch",
            "agent/task",
            "--commit-message",
            "fix: exact",
        ]
    )

    constructor_mock.assert_not_called()
    assert runner.call_args.args[0] == SessionId("cli-session")
    assert runner.call_args.kwargs["lifecycle_store"] is None


def test_isolated_cli_invalid_lifecycle_store_fails_before_workflow_starts(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    """Fail isolated invocation early when lifecycle-store validation fails."""

    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / "isolated"
    missing_store = tmp_path / "missing-store"
    configuration = RuntimeConfiguration(
        provider_name="ollama",
        model_name="test-model",
        workspace_root=source,
        enable_actions=True,
        worktree_path=target,
        worktree_branch="agent/task",
    )
    runner = Mock()

    monkeypatch.setattr("agent_workbench.cli.load_environment", Mock())
    monkeypatch.setattr(
        "agent_workbench.cli.resolve_runtime_configuration",
        Mock(return_value=configuration),
    )
    monkeypatch.setattr(
        "agent_workbench.cli.run_isolated_autonomous_workflow",
        runner,
    )

    exit_code = run_main_and_capture_exit_code(
        [
            "code",
            "--workspace",
            str(source),
            "--enable-actions",
            "--task",
            "Fix it.",
            "--worktree-path",
            str(target),
            "--worktree-branch",
            "agent/task",
            "--commit-message",
            "fix: exact",
            "--lifecycle-store",
            str(missing_store),
            "--session-id",
            "task-001",
        ]
    )

    assert exit_code == 1
    runner.assert_not_called()
    assert "lifecycle store directory does not exist" in capsys.readouterr().out


def test_lifecycle_runtime_configuration_error_exits_1_without_traceback(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    """Return status 1 for lifecycle persistence without worktree isolation."""

    lifecycle_store = tmp_path / "lifecycle-store"
    lifecycle_store.mkdir()

    monkeypatch.setattr("agent_workbench.cli.load_environment", Mock())

    with pytest.raises(SystemExit) as raised:
        main(
            [
                "code",
                "--workspace",
                str(tmp_path),
                "--provider",
                "ollama",
                "--model",
                "test-model",
                "--agent",
                "developer",
                "--enable-actions",
                "--task",
                "Fix it.",
                "--lifecycle-store",
                str(lifecycle_store),
                "--session-id",
                "task-001",
            ]
        )

    assert raised.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == (
        "Configuration error: Lifecycle persistence options require "
        "--worktree-path and --worktree-branch.\n"
    )
    assert captured.err == ""


def test_incomplete_isolated_autonomous_workflow_configuration_exits_1_without_traceback(
    tmp_path,
    capsys,
) -> None:
    """Return status 1 for the defensive incomplete isolated-workflow guard."""

    from agent_workbench.cli import _run_isolated_autonomous_task

    configuration = RuntimeConfiguration(
        provider_name="ollama",
        model_name="test-model",
        workspace_root=tmp_path,
        enable_actions=True,
        worktree_path=tmp_path / "isolated",
        worktree_branch="agent/test",
    )

    with pytest.raises(SystemExit) as raised:
        _run_isolated_autonomous_task(
            configuration,
            task_prompt=None,
            commit_message="fix: test",
            lifecycle_store_directory=None,
            lifecycle_session_id=None,
        )

    assert raised.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == (
        "Configuration error: Isolated autonomous workflow "
        "configuration is incomplete.\n"
    )
    assert captured.err == ""
