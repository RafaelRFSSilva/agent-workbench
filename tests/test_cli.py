"""Tests for the Agent Workbench command-line interface."""

from pathlib import Path
from unittest.mock import Mock

from agent_workbench.cli import main, run_cli
from agent_workbench.arguments import RuntimeConfiguration
from agent_workbench.context import ContextDocument
from agent_workbench.errors import CompletionError
from agent_workbench.messages import ChatRequest, ChatResponse, Message
from agent_workbench.agents import get_agent_profile
from agent_workbench.generation import GenerationConfig
from agent_workbench.structured_outputs import JSONResponseFormat
from agent_workbench.tool_registry import ToolRegistry
from agent_workbench.tools import ToolDefinition, ToolInvocation


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


def test_cli_without_registry_uses_direct_provider_completion(
    monkeypatch,
) -> None:
    """Keep the direct provider path when no tool registry is supplied."""

    user_inputs = iter(["Respond directly.", "/exit"])
    provider = FakeProvider(["direct response"])
    loop_mock = Mock()

    monkeypatch.setattr("builtins.input", lambda _: next(user_inputs))
    monkeypatch.setattr("agent_workbench.cli.run_tool_calling_loop", loop_mock)

    run_cli(provider)

    loop_mock.assert_not_called()
    assert provider.calls == [
        [
            {
                "role": "user",
                "content": "Respond directly.",
            }
        ]
    ]


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
    provider = FakeProvider()

    setup_mock = Mock(return_value=configuration)
    create_provider_mock = Mock(return_value=provider)
    run_cli_mock = Mock()

    monkeypatch.setattr(
        "agent_workbench.cli.run_interactive_setup",
        setup_mock,
    )
    monkeypatch.setattr(
        "agent_workbench.cli.create_provider",
        create_provider_mock,
    )
    monkeypatch.setattr(
        "agent_workbench.cli.run_cli",
        run_cli_mock,
    )

    main(["--setup"])

    setup_mock.assert_called_once_with()
    create_provider_mock.assert_called_once_with(
        "ollama",
        "test-model",
    )
    run_cli_mock.assert_called_once_with(
        provider,
        system_prompt=None,
        agent_profile=None,
        context_documents=(),
        generation_config=GenerationConfig(),
        response_format=response_format,
    )
