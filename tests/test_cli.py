"""Tests for the Agent Workbench command-line interface."""

from pathlib import Path
from unittest.mock import call, Mock

from agent_workbench.built_in_tools import create_built_in_tool_registry
from agent_workbench.coding_loop import (
    CodingPhase,
    CodingProgressEvent,
    CodingProgressKind,
)
from agent_workbench.cli import (
    AUTONOMOUS_MAX_TOOL_ROUNDS,
    _display_coding_progress,
    main,
    run_cli as run_session_cli,
)
from agent_workbench.arguments import RuntimeConfiguration
from agent_workbench.context import ContextDocument
from agent_workbench.errors import CompletionError, ConfigurationError
from agent_workbench.messages import ChatRequest, ChatResponse, Message
from agent_workbench.session import AgentSession, SessionId
from agent_workbench.agents import get_agent_profile
from agent_workbench.generation import GenerationConfig
from agent_workbench.structured_outputs import JSONResponseFormat
from agent_workbench.symbol_tools import register_symbol_tools
from agent_workbench.tool_registry import ToolRegistry
from agent_workbench.tools import ToolDefinition, ToolInvocation
from agent_workbench.workspace import Workspace
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

    main(["--workspace", str(tmp_path / "missing")])

    assert (
        "Configuration error: Workspace root does not exist:" in capsys.readouterr().out
    )
    run_cli_mock.assert_not_called()


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
        "[VERIFY] 2 changed files\n"
        "[FAILED] VERIFY: unexpected changed paths before DONE: unrelated.py; "
        "repair attempts 1/2; workspace preserved for manual recovery\n"
    )


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
        "[FAILED] VALIDATE: unexpected changed paths after run_ruff_format: "
        "unrelated.py; repair attempts 1/2; "
        "workspace preserved for manual recovery\n"
    )


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
