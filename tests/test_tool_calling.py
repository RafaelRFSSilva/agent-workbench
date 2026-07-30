"""Tests for the provider-independent tool-calling execution loop."""

from pathlib import Path

import pytest

from agent_workbench.context import ContextDocument
from agent_workbench.errors import CompletionError, ConfigurationError
from agent_workbench.generation import GenerationConfig
from agent_workbench.messages import (
    ChatRequest,
    ChatResponse,
    ToolInteractionRound,
)
from agent_workbench.structured_outputs import JSONResponseFormat
from agent_workbench.tool_calling import (
    MAX_TOOL_INVOCATIONS_PER_RESPONSE,
    run_tool_calling_loop,
)
from agent_workbench.tool_registry import ToolRegistry
from agent_workbench.tools import (
    ToolApprovalDecision,
    ToolDefinition,
    ToolInvocation,
    ToolResult,
)


class FakeProvider:
    """Return configured outcomes and retain each provider request."""

    name = "Fake"
    model_name = "fake-model"

    def __init__(self, outcomes: list[ChatResponse | CompletionError]) -> None:
        """Store the configured provider outcomes."""

        self._outcomes = outcomes
        self.requests: list[ChatRequest] = []

    def complete(self, request: ChatRequest) -> ChatResponse:
        """Return the next configured result for a provider request."""

        self.requests.append(request)
        outcome = self._outcomes.pop(0)

        if isinstance(outcome, CompletionError):
            raise outcome

        return outcome


def create_calculator_definition() -> ToolDefinition:
    """Create a calculator tool definition for loop tests."""

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
            "required": [
                "expression",
            ],
            "additionalProperties": False,
        },
    )


def create_read_file_definition() -> ToolDefinition:
    """Create a read-only file inspection definition for loop tests."""

    return ToolDefinition(
        name="read_file",
        description="Read one file.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                }
            },
            "required": [
                "path",
            ],
            "additionalProperties": False,
        },
    )


def create_tool_response(
    *invocations: ToolInvocation,
    text: str = "",
) -> ChatResponse:
    """Create a provider response that requests one or more tools."""

    return ChatResponse(
        text=text,
        tool_invocations=invocations,
    )


def test_returns_immediate_text_response_unchanged() -> None:
    """Return a text-only provider response without executing tools."""

    response = ChatResponse(text="Completed without tools.")
    provider = FakeProvider([response])
    request = ChatRequest(messages=[])

    result = run_tool_calling_loop(
        provider,
        request,
        ToolRegistry(),
        max_tool_rounds=1,
    )

    assert result is response
    assert provider.requests == [request]


def test_executes_one_tool_round_and_returns_final_response() -> None:
    """Execute requested tools and return the following final response."""

    received_arguments = []

    def calculate(arguments: dict[str, object]) -> dict[str, object]:
        received_arguments.append(arguments)
        return {
            "value": 4,
        }

    calculator = create_calculator_definition()
    registry = ToolRegistry()
    registry.register(calculator, calculate)
    requested_response = create_tool_response(
        ToolInvocation(
            id="call-1",
            tool_name="calculator",
            arguments={
                "expression": "2 + 2",
            },
        ),
        text="I will calculate the result.",
    )
    final_response = ChatResponse(text="The answer is 4.")
    provider = FakeProvider([requested_response, final_response])
    request = ChatRequest(messages=[])

    result = run_tool_calling_loop(
        provider,
        request,
        registry,
        max_tool_rounds=1,
    )

    assert result is final_response
    assert received_arguments == [
        {
            "expression": "2 + 2",
        }
    ]
    assert provider.requests[0] is request
    assert provider.requests[1].tool_interactions == (
        ToolInteractionRound(
            response=requested_response,
            results=(
                ToolResult(
                    invocation_id="call-1",
                    status="success",
                    output={
                        "value": 4,
                    },
                ),
            ),
        ),
    )


def test_reports_completed_rounds_to_an_optional_observer() -> None:
    """Expose completed provider-independent rounds without changing requests."""

    calculator = create_calculator_definition()
    registry = ToolRegistry()
    registry.register(calculator, lambda arguments: {"value": 4})
    requested_response = create_tool_response(
        ToolInvocation(
            id="call-1",
            tool_name="calculator",
            arguments={"expression": "2 + 2"},
        )
    )
    observed_rounds: list[ToolInteractionRound] = []
    provider = FakeProvider([requested_response, ChatResponse(text="4")])

    run_tool_calling_loop(
        provider,
        ChatRequest(messages=[]),
        registry,
        max_tool_rounds=1,
        tool_round_observer=observed_rounds.append,
    )

    assert observed_rounds == [
        ToolInteractionRound(
            response=requested_response,
            results=(
                ToolResult(
                    invocation_id="call-1",
                    status="success",
                    output={"value": 4},
                ),
            ),
        )
    ]


def test_executes_multiple_invocations_in_provider_order() -> None:
    """Execute every requested invocation in the provider response order."""

    execution_order = []
    registry = ToolRegistry()
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
    registry.register(
        calculator,
        lambda arguments: execution_order.append("calculator") or {"value": 4},
    )
    registry.register(
        project_information,
        lambda arguments: (
            execution_order.append("project_information") or {"name": "Agent Workbench"}
        ),
    )
    requested_response = create_tool_response(
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
    )
    provider = FakeProvider(
        [
            requested_response,
            ChatResponse(text="Completed."),
        ]
    )

    run_tool_calling_loop(
        provider,
        ChatRequest(messages=[]),
        registry,
        max_tool_rounds=1,
    )

    assert execution_order == [
        "calculator",
        "project_information",
    ]
    assert tuple(
        result.invocation_id
        for result in provider.requests[1].tool_interactions[0].results
    ) == (
        "call-1",
        "call-2",
    )


def test_recovers_one_oversized_tool_batch_without_executing_it() -> None:
    """Retry one oversized batch without executing or exposing rejected calls."""

    executions: list[dict[str, object]] = []
    observed_rounds: list[ToolInteractionRound] = []
    calculator = create_calculator_definition()
    registry = ToolRegistry()
    registry.register(
        calculator,
        lambda arguments: executions.append(arguments) or {"value": 4},
    )
    rejected_response = create_tool_response(
        *(
            ToolInvocation(
                id=f"rejected-{index}",
                tool_name="calculator",
                arguments={"expression": f"secret-{index}"},
            )
            for index in range(MAX_TOOL_INVOCATIONS_PER_RESPONSE + 1)
        )
    )
    accepted_response = create_tool_response(
        ToolInvocation(
            id="accepted",
            tool_name="calculator",
            arguments={"expression": "2 + 2"},
        )
    )
    final_response = ChatResponse(text="Recovered.")
    provider = FakeProvider(
        [
            rejected_response,
            accepted_response,
            final_response,
        ]
    )
    request = ChatRequest(
        messages=[{"role": "user", "content": "Calculate."}],
        system_prompt="Base instructions.",
        tools=(calculator,),
    )

    result = run_tool_calling_loop(
        provider,
        request,
        registry,
        max_tool_rounds=1,
        tool_round_observer=observed_rounds.append,
    )

    assert result is final_response
    assert executions == [{"expression": "2 + 2"}]
    assert len(observed_rounds) == 1
    assert observed_rounds[0].response is accepted_response
    assert len(provider.requests) == 3
    assert provider.requests[0] is request

    recovery_request = provider.requests[1]
    assert recovery_request.messages is request.messages
    assert recovery_request.tools is request.tools
    assert recovery_request.tool_interactions == ()
    assert recovery_request.system_prompt is not None
    assert recovery_request.system_prompt.startswith("Base instructions.\n\n")
    assert (
        f"at most {MAX_TOOL_INVOCATIONS_PER_RESPONSE} necessary tool calls"
        in recovery_request.system_prompt
    )
    assert "secret-0" not in recovery_request.system_prompt

    continued_request = provider.requests[2]
    assert continued_request.system_prompt == request.system_prompt
    assert continued_request.tool_interactions == tuple(observed_rounds)


def test_recovers_duplicate_tool_batch_without_executing_it() -> None:
    """Reject duplicate tool requests and allow one clean text retry."""

    executions: list[dict[str, object]] = []
    calculator = create_calculator_definition()
    registry = ToolRegistry()
    registry.register(
        calculator,
        lambda arguments: executions.append(arguments) or {"value": 4},
    )
    duplicate_response = create_tool_response(
        ToolInvocation(
            id="duplicate-1",
            tool_name="calculator",
            arguments={"expression": "2 + 2"},
        ),
        ToolInvocation(
            id="duplicate-2",
            tool_name="calculator",
            arguments={"expression": "2 + 2"},
        ),
    )
    final_response = ChatResponse(text="No tool call is needed.")
    provider = FakeProvider([duplicate_response, final_response])

    result = run_tool_calling_loop(
        provider,
        ChatRequest(messages=[]),
        registry,
        max_tool_rounds=1,
    )

    assert result is final_response
    assert executions == []
    assert len(provider.requests) == 2
    assert provider.requests[1].tool_interactions == ()


def test_repeated_unsafe_tool_batches_fail_after_one_recovery() -> None:
    """Stop safely when the single corrective retry is also unsafe."""

    executions: list[dict[str, object]] = []
    observed_rounds: list[ToolInteractionRound] = []
    calculator = create_calculator_definition()
    registry = ToolRegistry()
    registry.register(
        calculator,
        lambda arguments: executions.append(arguments) or {"value": 4},
    )

    def oversized_response(prefix: str) -> ChatResponse:
        return create_tool_response(
            *(
                ToolInvocation(
                    id=f"{prefix}-{index}",
                    tool_name="calculator",
                    arguments={"expression": f"{prefix}-secret-{index}"},
                )
                for index in range(MAX_TOOL_INVOCATIONS_PER_RESPONSE + 1)
            )
        )

    provider = FakeProvider(
        [
            oversized_response("first"),
            oversized_response("second"),
        ]
    )

    with pytest.raises(
        CompletionError,
        match="repeatedly requested an unsafe tool-call batch",
    ) as raised_error:
        run_tool_calling_loop(
            provider,
            ChatRequest(messages=[]),
            registry,
            max_tool_rounds=1,
            tool_round_observer=observed_rounds.append,
        )

    assert len(provider.requests) == 2
    assert executions == []
    assert observed_rounds == []
    assert "secret" not in str(raised_error.value)


def test_recovers_one_repeated_inspection_batch_without_reexecuting_it() -> None:
    """Withhold inspection tools for one recovery without duplicate execution."""

    inspection_executions: list[dict[str, object]] = []
    calculator_executions: list[dict[str, object]] = []
    observed_rounds: list[ToolInteractionRound] = []
    read_file = create_read_file_definition()
    calculator = create_calculator_definition()
    registry = ToolRegistry()
    registry.register(
        read_file,
        lambda arguments: (
            inspection_executions.append(arguments) or {"content": "value"}
        ),
    )
    registry.register(
        calculator,
        lambda arguments: calculator_executions.append(arguments) or {"value": 4},
    )
    first_response = create_tool_response(
        ToolInvocation(
            id="first",
            tool_name="read_file",
            arguments={"path": "secret-repeat.py"},
        )
    )
    repeated_response = create_tool_response(
        ToolInvocation(
            id="repeated-with-new-id",
            tool_name="read_file",
            arguments={"path": "secret-repeat.py"},
        )
    )
    recovery_response = create_tool_response(
        ToolInvocation(
            id="calculate",
            tool_name="calculator",
            arguments={"expression": "2 + 2"},
        )
    )
    final_response = ChatResponse(text="Recovered.")
    provider = FakeProvider(
        [
            first_response,
            repeated_response,
            recovery_response,
            final_response,
        ]
    )
    request = ChatRequest(
        messages=[{"role": "user", "content": "Inspect files."}],
        system_prompt="Base instructions.",
        tools=(read_file, calculator),
    )

    result = run_tool_calling_loop(
        provider,
        request,
        registry,
        max_tool_rounds=2,
        tool_round_observer=observed_rounds.append,
    )

    assert result is final_response
    assert inspection_executions == [{"path": "secret-repeat.py"}]
    assert calculator_executions == [{"expression": "2 + 2"}]
    assert tuple(round_.response for round_ in observed_rounds) == (
        first_response,
        recovery_response,
    )
    assert len(provider.requests) == 4

    recovery_request = provider.requests[2]
    assert recovery_request.messages is request.messages
    assert recovery_request.tools == (calculator,)
    assert request.tools == (read_file, calculator)
    assert recovery_request.tool_interactions == (observed_rounds[0],)
    assert recovery_request.system_prompt is not None
    assert recovery_request.system_prompt.startswith("Base instructions.\n\n")
    assert "immediately preceding completed round" in recovery_request.system_prompt
    assert "secret-repeat.py" not in recovery_request.system_prompt

    continued_request = provider.requests[3]
    assert continued_request.system_prompt == request.system_prompt
    assert continued_request.tools is request.tools
    assert continued_request.tool_interactions == tuple(observed_rounds)


def test_repeated_inspection_recovery_withholds_every_inspection_definition() -> None:
    """Temporarily remove every read-only inspection definition from recovery."""

    read_file = create_read_file_definition()
    calculator = create_calculator_definition()
    inspection_definitions = tuple(
        ToolDefinition(
            name=name,
            description=f"Inspect with {name}.",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        )
        for name in (
            "inspect_git_diff",
            "inspect_git_status",
            "list_files",
            "search_symbols",
            "search_text",
        )
    )
    registry = ToolRegistry()
    registry.register(read_file, lambda arguments: {"content": "value"})
    first_response = create_tool_response(
        ToolInvocation(
            id="first",
            tool_name="read_file",
            arguments={"path": "module.py"},
        )
    )
    repeated_response = create_tool_response(
        ToolInvocation(
            id="second",
            tool_name="read_file",
            arguments={"path": "module.py"},
        )
    )
    provider = FakeProvider(
        [
            first_response,
            repeated_response,
            ChatResponse(text="Use the existing inspection result."),
        ]
    )
    request = ChatRequest(
        messages=[],
        tools=(read_file, *inspection_definitions, calculator),
    )

    result = run_tool_calling_loop(
        provider,
        request,
        registry,
        max_tool_rounds=1,
    )

    assert result.text == "Use the existing inspection result."
    assert provider.requests[2].tools == (calculator,)
    assert request.tools == (read_file, *inspection_definitions, calculator)


def test_repeated_inspection_batch_fails_after_one_recovery() -> None:
    """Stop when one corrective retry repeats the same inspection again."""

    executions: list[dict[str, object]] = []
    observed_rounds: list[ToolInteractionRound] = []
    read_file = create_read_file_definition()
    registry = ToolRegistry()
    registry.register(
        read_file,
        lambda arguments: executions.append(arguments) or {"content": "value"},
    )

    def repeated_response(identifier: str) -> ChatResponse:
        return create_tool_response(
            ToolInvocation(
                id=identifier,
                tool_name="read_file",
                arguments={"path": "secret-repeat.py"},
            )
        )

    provider = FakeProvider(
        [
            repeated_response("first"),
            repeated_response("second"),
            repeated_response("third"),
        ]
    )

    with pytest.raises(
        CompletionError,
        match="repeatedly requested the same read-only inspection tool-call batch",
    ) as raised_error:
        run_tool_calling_loop(
            provider,
            ChatRequest(messages=[]),
            registry,
            max_tool_rounds=3,
            tool_round_observer=observed_rounds.append,
        )

    assert len(provider.requests) == 3
    assert executions == [{"path": "secret-repeat.py"}]
    assert len(observed_rounds) == 1
    assert "secret-repeat.py" not in str(raised_error.value)


def test_allows_inspection_with_different_arguments_in_consecutive_rounds() -> None:
    """Allow consecutive inspections that request distinct information."""

    executions: list[dict[str, object]] = []
    read_file = create_read_file_definition()
    registry = ToolRegistry()
    registry.register(
        read_file,
        lambda arguments: executions.append(arguments) or {"content": "value"},
    )
    provider = FakeProvider(
        [
            create_tool_response(
                ToolInvocation(
                    id="first",
                    tool_name="read_file",
                    arguments={"path": "first.py"},
                )
            ),
            create_tool_response(
                ToolInvocation(
                    id="second",
                    tool_name="read_file",
                    arguments={"path": "second.py"},
                )
            ),
            ChatResponse(text="Completed."),
        ]
    )

    result = run_tool_calling_loop(
        provider,
        ChatRequest(messages=[]),
        registry,
        max_tool_rounds=2,
    )

    assert result.text == "Completed."
    assert executions == [
        {"path": "first.py"},
        {"path": "second.py"},
    ]


def test_allows_repeated_inspection_after_an_error_result() -> None:
    """Allow retrying identical inspection arguments after execution failure."""

    attempts = 0

    def inspect(arguments: dict[str, object]) -> dict[str, object]:
        nonlocal attempts
        attempts += 1

        if attempts == 1:
            raise ValueError("temporary failure")

        return {"content": arguments["path"]}

    read_file = create_read_file_definition()
    registry = ToolRegistry()
    registry.register(read_file, inspect)
    requested_response = create_tool_response(
        ToolInvocation(
            id="first",
            tool_name="read_file",
            arguments={"path": "module.py"},
        )
    )
    repeated_response = create_tool_response(
        ToolInvocation(
            id="second",
            tool_name="read_file",
            arguments={"path": "module.py"},
        )
    )
    provider = FakeProvider(
        [
            requested_response,
            repeated_response,
            ChatResponse(text="Recovered after retry."),
        ]
    )

    result = run_tool_calling_loop(
        provider,
        ChatRequest(messages=[]),
        registry,
        max_tool_rounds=2,
    )

    assert result.text == "Recovered after retry."
    assert attempts == 2
    assert len(provider.requests) == 3
    assert tuple(
        round_.results[0].status for round_ in provider.requests[2].tool_interactions
    ) == ("error", "success")


def test_allows_nonconsecutive_reuse_of_an_inspection_batch() -> None:
    """Compare inspections only with the immediately preceding completed round."""

    executions: list[dict[str, object]] = []
    read_file = create_read_file_definition()
    registry = ToolRegistry()
    registry.register(
        read_file,
        lambda arguments: executions.append(arguments) or {"content": "value"},
    )

    def response(identifier: str, path: str) -> ChatResponse:
        return create_tool_response(
            ToolInvocation(
                id=identifier,
                tool_name="read_file",
                arguments={"path": path},
            )
        )

    provider = FakeProvider(
        [
            response("first", "first.py"),
            response("second", "second.py"),
            response("third", "first.py"),
            ChatResponse(text="Completed."),
        ]
    )

    result = run_tool_calling_loop(
        provider,
        ChatRequest(messages=[]),
        registry,
        max_tool_rounds=3,
    )

    assert result.text == "Completed."
    assert executions == [
        {"path": "first.py"},
        {"path": "second.py"},
        {"path": "first.py"},
    ]


def test_recovers_repeated_inspection_from_preexisting_tool_history() -> None:
    """Protect resumed requests from repeating their latest inspection batch."""

    executions: list[dict[str, object]] = []
    read_file = create_read_file_definition()
    previous_response = create_tool_response(
        ToolInvocation(
            id="previous",
            tool_name="read_file",
            arguments={"path": "secret-repeat.py"},
        )
    )
    previous_round = ToolInteractionRound(
        response=previous_response,
        results=(
            ToolResult(
                invocation_id="previous",
                status="success",
                output={"content": "value"},
            ),
        ),
    )
    registry = ToolRegistry()
    registry.register(
        read_file,
        lambda arguments: executions.append(arguments) or {"content": "changed"},
    )
    provider = FakeProvider(
        [
            create_tool_response(
                ToolInvocation(
                    id="repeated",
                    tool_name="read_file",
                    arguments={"path": "secret-repeat.py"},
                )
            ),
            ChatResponse(text="Use the previous result."),
        ]
    )
    request = ChatRequest(
        messages=[],
        tools=(read_file,),
        tool_interactions=(previous_round,),
    )

    result = run_tool_calling_loop(
        provider,
        request,
        registry,
        max_tool_rounds=1,
    )

    assert result.text == "Use the previous result."
    assert executions == []
    assert len(provider.requests) == 2
    assert provider.requests[1].tool_interactions == (previous_round,)
    assert provider.requests[1].tools == ()
    assert request.tools == (read_file,)
    assert provider.requests[1].system_prompt is not None
    assert "secret-repeat.py" not in provider.requests[1].system_prompt


def test_executes_the_maximum_safe_tool_batch() -> None:
    """Execute a distinct batch exactly at the fixed safety boundary."""

    executions: list[dict[str, object]] = []
    calculator = create_calculator_definition()
    registry = ToolRegistry()
    registry.register(
        calculator,
        lambda arguments: executions.append(arguments) or {"value": 4},
    )
    safe_response = create_tool_response(
        *(
            ToolInvocation(
                id=f"call-{index}",
                tool_name="calculator",
                arguments={"expression": f"{index} + 1"},
            )
            for index in range(MAX_TOOL_INVOCATIONS_PER_RESPONSE)
        )
    )
    provider = FakeProvider(
        [
            safe_response,
            ChatResponse(text="Completed."),
        ]
    )

    result = run_tool_calling_loop(
        provider,
        ChatRequest(messages=[]),
        registry,
        max_tool_rounds=1,
    )

    assert result.text == "Completed."
    assert executions == [
        {"expression": f"{index} + 1"}
        for index in range(MAX_TOOL_INVOCATIONS_PER_RESPONSE)
    ]


def test_appends_multiple_tool_rounds_before_final_response() -> None:
    """Append one completed interaction round after each tool response."""

    registry = ToolRegistry()
    registry.register(
        create_calculator_definition(),
        lambda arguments: {
            "value": 4,
        },
    )
    first_response = create_tool_response(
        ToolInvocation(
            id="call-1",
            tool_name="calculator",
            arguments={},
        ),
    )
    second_response = create_tool_response(
        ToolInvocation(
            id="call-2",
            tool_name="calculator",
            arguments={},
        ),
    )
    final_response = ChatResponse(text="Completed after two rounds.")
    provider = FakeProvider(
        [
            first_response,
            second_response,
            final_response,
        ]
    )

    result = run_tool_calling_loop(
        provider,
        ChatRequest(messages=[]),
        registry,
        max_tool_rounds=2,
    )

    assert result is final_response
    assert provider.requests[1].tool_interactions[0].response is first_response
    assert provider.requests[2].tool_interactions == (
        provider.requests[1].tool_interactions[0],
        ToolInteractionRound(
            response=second_response,
            results=(
                ToolResult(
                    invocation_id="call-2",
                    status="success",
                    output={
                        "value": 4,
                    },
                ),
            ),
        ),
    )


def test_preserves_request_tools_and_configuration() -> None:
    """Retain every original request field when appending tool history."""

    calculator = create_calculator_definition()
    response_format = JSONResponseFormat(
        name="tool_result",
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
    request = ChatRequest(
        messages=[
            {
                "role": "user",
                "content": "Calculate two plus two.",
            }
        ],
        system_prompt="Be concise.",
        context_documents=(
            ContextDocument(
                source=Path("README.md"),
                content="Agent Workbench",
            ),
        ),
        generation_config=GenerationConfig(
            temperature=0.2,
            top_p=0.8,
            max_output_tokens=128,
        ),
        response_format=response_format,
        tools=(calculator,),
    )
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
            ),
            ChatResponse(text="4"),
        ]
    )

    run_tool_calling_loop(
        provider,
        request,
        registry,
        max_tool_rounds=1,
    )

    continued_request = provider.requests[1]

    assert continued_request.messages is request.messages
    assert continued_request.system_prompt == request.system_prompt
    assert continued_request.context_documents == request.context_documents
    assert continued_request.generation_config == request.generation_config
    assert continued_request.response_format == request.response_format
    assert continued_request.tools is request.tools


def test_preserves_preexisting_history_without_reexecution() -> None:
    """Forward pre-existing rounds and execute only newly requested tools."""

    executed_arguments = []
    calculator = create_calculator_definition()
    preexisting_response = create_tool_response(
        ToolInvocation(
            id="previous-call",
            tool_name="calculator",
            arguments={
                "expression": "1 + 1",
            },
        ),
    )
    preexisting_round = ToolInteractionRound(
        response=preexisting_response,
        results=(
            ToolResult(
                invocation_id="previous-call",
                status="success",
                output={
                    "value": 2,
                },
            ),
        ),
    )
    registry = ToolRegistry()
    registry.register(
        calculator,
        lambda arguments: executed_arguments.append(arguments) or {"value": 4},
    )
    new_response = create_tool_response(
        ToolInvocation(
            id="new-call",
            tool_name="calculator",
            arguments={
                "expression": "2 + 2",
            },
        ),
    )
    provider = FakeProvider(
        [
            new_response,
            ChatResponse(text="4"),
        ]
    )
    request = ChatRequest(
        messages=[],
        tool_interactions=(preexisting_round,),
    )

    run_tool_calling_loop(
        provider,
        request,
        registry,
        max_tool_rounds=1,
    )

    assert executed_arguments == [
        {
            "expression": "2 + 2",
        }
    ]
    assert provider.requests[0] is request
    assert provider.requests[1].tool_interactions[0] is preexisting_round
    assert provider.requests[1].tool_interactions[1].response is new_response


def test_continues_after_tool_execution_error_result() -> None:
    """Send ToolRegistry error results back to the provider."""

    requested_response = create_tool_response(
        ToolInvocation(
            id="call-unknown",
            tool_name="unknown",
            arguments={},
        ),
    )
    final_response = ChatResponse(text="The requested tool is unavailable.")
    provider = FakeProvider([requested_response, final_response])

    result = run_tool_calling_loop(
        provider,
        ChatRequest(messages=[]),
        ToolRegistry(),
        max_tool_rounds=1,
    )

    assert result is final_response
    assert provider.requests[1].tool_interactions[0].results == (
        ToolResult(
            invocation_id="call-unknown",
            status="error",
            error="Unknown tool 'unknown'.",
        ),
    )


def test_propagates_provider_completion_errors() -> None:
    """Allow provider CompletionError values to propagate unchanged."""

    error = CompletionError("Provider failed.")
    provider = FakeProvider([error])

    with pytest.raises(CompletionError) as raised_error:
        run_tool_calling_loop(
            provider,
            ChatRequest(messages=[]),
            ToolRegistry(),
            max_tool_rounds=1,
        )

    assert raised_error.value is error


def test_rejects_tool_requests_beyond_the_maximum_rounds() -> None:
    """Stop before executing a tool request that exceeds the configured limit."""

    executions = []
    calculator = create_calculator_definition()
    registry = ToolRegistry()
    registry.register(
        calculator,
        lambda arguments: executions.append(arguments) or {"value": 4},
    )
    provider = FakeProvider(
        [
            create_tool_response(
                ToolInvocation(
                    id="call-1",
                    tool_name="calculator",
                    arguments={
                        "value": 1,
                    },
                ),
            ),
            create_tool_response(
                ToolInvocation(
                    id="call-2",
                    tool_name="calculator",
                    arguments={
                        "value": 2,
                    },
                ),
            ),
        ]
    )

    with pytest.raises(
        CompletionError,
        match="maximum number of tool execution rounds",
    ):
        run_tool_calling_loop(
            provider,
            ChatRequest(messages=[]),
            registry,
            max_tool_rounds=1,
        )

    assert executions == [
        {
            "value": 1,
        }
    ]
    assert len(provider.requests) == 2


@pytest.mark.parametrize(
    "max_tool_rounds",
    [
        0,
        -1,
        True,
        False,
        1.5,
        "1",
        None,
    ],
)
def test_rejects_invalid_maximum_tool_rounds(
    max_tool_rounds: object,
) -> None:
    """Require a positive integer maximum for newly executed rounds."""

    provider = FakeProvider([ChatResponse(text="Not called.")])

    with pytest.raises(
        ConfigurationError,
        match="maximum tool rounds must be a positive integer",
    ):
        run_tool_calling_loop(
            provider,
            ChatRequest(messages=[]),
            ToolRegistry(),
            max_tool_rounds=max_tool_rounds,
        )

    assert provider.requests == []


def test_does_not_mutate_the_original_request_or_tool_data() -> None:
    """Keep the original request and shared tool models unchanged."""

    calculator = create_calculator_definition()
    invocation = ToolInvocation(
        id="call-1",
        tool_name="calculator",
        arguments={
            "values": [
                2,
                3,
            ],
        },
    )
    registry = ToolRegistry()

    def calculate(arguments: dict[str, object]) -> dict[str, object]:
        arguments["values"] = [99]
        return {
            "values": [
                4,
            ],
        }

    registry.register(calculator, calculate)
    messages = [
        {
            "role": "user",
            "content": "Calculate values.",
        }
    ]
    request = ChatRequest(
        messages=messages,
        tools=(calculator,),
    )
    provider = FakeProvider(
        [
            create_tool_response(invocation),
            ChatResponse(text="Completed."),
        ]
    )

    run_tool_calling_loop(
        provider,
        request,
        registry,
        max_tool_rounds=1,
    )

    assert request.messages == messages
    assert request.tools == (calculator,)
    assert request.tool_interactions == ()
    assert invocation.arguments == {
        "values": [
            2,
            3,
        ],
    }


def test_approval_preview_failure_remains_fatal_by_default() -> None:
    """Preserve the existing fatal behavior unless recovery is enabled."""

    registry = ToolRegistry()
    executions = []
    approvals = []

    def fail_preview(_arguments):
        raise CompletionError("invalid target")

    def approve(request):
        approvals.append(request)
        return ToolApprovalDecision.APPROVE

    registry.register(
        create_calculator_definition(),
        lambda arguments: executions.append(arguments),
        requires_approval=True,
        approval_preview=fail_preview,
    )
    requested_response = create_tool_response(
        ToolInvocation(
            id="invalid-call",
            tool_name="calculator",
            arguments={"expression": "2 + 2"},
        )
    )
    provider = FakeProvider([requested_response])

    with pytest.raises(
        CompletionError,
        match="Approval preview failed for calculator: invalid target",
    ):
        run_tool_calling_loop(
            provider,
            ChatRequest(messages=[]),
            registry,
            max_tool_rounds=1,
            tool_approval_handler=approve,
        )

    assert executions == []
    assert approvals == []


def test_recovers_approval_preview_failure_as_tool_error_when_enabled() -> None:
    """Return a failed preview to the provider without approval or execution."""

    registry = ToolRegistry()
    executions = []
    approvals = []
    observed_rounds = []

    def fail_preview(_arguments):
        raise CompletionError("invalid target")

    def approve(request):
        approvals.append(request)
        return ToolApprovalDecision.APPROVE

    registry.register(
        create_calculator_definition(),
        lambda arguments: executions.append(arguments),
        requires_approval=True,
        approval_preview=fail_preview,
    )
    requested_response = create_tool_response(
        ToolInvocation(
            id="invalid-call",
            tool_name="calculator",
            arguments={"expression": "2 + 2"},
        )
    )
    final_response = ChatResponse(text="Recovered.")
    provider = FakeProvider([requested_response, final_response])

    result = run_tool_calling_loop(
        provider,
        ChatRequest(messages=[]),
        registry,
        max_tool_rounds=1,
        tool_round_observer=observed_rounds.append,
        tool_approval_handler=approve,
        recover_approval_preview_errors=True,
    )

    expected_round = ToolInteractionRound(
        response=requested_response,
        results=(
            ToolResult(
                invocation_id="invalid-call",
                status="error",
                error=("Approval preview failed for calculator: invalid target"),
            ),
        ),
    )

    assert result is final_response
    assert executions == []
    assert approvals == []
    assert observed_rounds == [expected_round]
    assert provider.requests[1].tool_interactions == (expected_round,)


def test_keeps_inspection_tools_available_before_sixteen_round_limit() -> None:
    """Keep the full tool set through fifteen successful inspection rounds."""

    read_file = create_read_file_definition()
    calculator = create_calculator_definition()
    registry = ToolRegistry()
    registry.register(read_file, lambda arguments: {"content": "value"})
    registry.register(calculator, lambda arguments: {"value": 4})
    inspection_responses = [
        create_tool_response(
            ToolInvocation(
                id=f"read-{index}",
                tool_name="read_file",
                arguments={"path": f"module-{index}.py"},
            )
        )
        for index in range(15)
    ]
    final_response = ChatResponse(text="Enough context collected.")
    provider = FakeProvider([*inspection_responses, final_response])
    request = ChatRequest(messages=[], tools=(read_file, calculator))

    result = run_tool_calling_loop(
        provider,
        request,
        registry,
        max_tool_rounds=15,
    )

    assert result is final_response
    assert all(
        provider_request.tools is request.tools
        for provider_request in provider.requests
    )


def test_withholds_inspection_tools_after_sixteen_inspection_only_rounds() -> None:
    """Require progress after a bounded successful inspection-only streak."""

    read_executions: list[dict[str, object]] = []
    calculator_executions: list[dict[str, object]] = []
    read_file = create_read_file_definition()
    calculator = create_calculator_definition()
    registry = ToolRegistry()
    registry.register(
        read_file,
        lambda arguments: read_executions.append(arguments) or {"content": "value"},
    )
    registry.register(
        calculator,
        lambda arguments: calculator_executions.append(arguments) or {"value": 4},
    )
    inspection_responses = [
        create_tool_response(
            ToolInvocation(
                id=f"read-{index}",
                tool_name="read_file",
                arguments={"path": f"module-{index}.py"},
            )
        )
        for index in range(16)
    ]
    calculator_response = create_tool_response(
        ToolInvocation(
            id="calculate",
            tool_name="calculator",
            arguments={"expression": "2 + 2"},
        )
    )
    final_response = ChatResponse(text="Completed after making progress.")
    provider = FakeProvider(
        [*inspection_responses, calculator_response, final_response]
    )
    request = ChatRequest(messages=[], tools=(read_file, calculator))

    result = run_tool_calling_loop(
        provider,
        request,
        registry,
        max_tool_rounds=17,
    )

    assert result is final_response
    assert len(read_executions) == 16
    assert calculator_executions == [{"expression": "2 + 2"}]
    assert provider.requests[16].tools == (calculator,)
    assert provider.requests[16].system_prompt is not None
    assert "module-0.py" not in provider.requests[16].system_prompt
    assert provider.requests[17].tools is request.tools
    assert request.tools == (read_file, calculator)


def test_recovers_once_from_inspection_requested_while_tools_are_withheld() -> None:
    """Give one corrective retry after a provider ignores withheld tools."""

    read_executions: list[dict[str, object]] = []
    calculator_executions: list[dict[str, object]] = []
    read_file = create_read_file_definition()
    calculator = create_calculator_definition()
    registry = ToolRegistry()
    registry.register(
        read_file,
        lambda arguments: read_executions.append(arguments) or {"content": "value"},
    )
    registry.register(
        calculator,
        lambda arguments: calculator_executions.append(arguments) or {"value": 4},
    )
    inspection_responses = [
        create_tool_response(
            ToolInvocation(
                id=f"read-{index}",
                tool_name="read_file",
                arguments={"path": f"module-{index}.py"},
            )
        )
        for index in range(16)
    ]
    withheld_inspection = create_tool_response(
        ToolInvocation(
            id="ignored-tools",
            tool_name="read_file",
            arguments={"path": "forbidden.py"},
        )
    )
    calculator_response = create_tool_response(
        ToolInvocation(
            id="calculate",
            tool_name="calculator",
            arguments={"expression": "2 + 2"},
        )
    )
    final_response = ChatResponse(text="Completed after bounded recovery.")
    provider = FakeProvider(
        [
            *inspection_responses,
            withheld_inspection,
            calculator_response,
            final_response,
        ]
    )
    request = ChatRequest(messages=[], tools=(read_file, calculator))

    result = run_tool_calling_loop(
        provider,
        request,
        registry,
        max_tool_rounds=17,
    )

    assert result is final_response
    assert len(read_executions) == 16
    assert calculator_executions == [{"expression": "2 + 2"}]
    assert provider.requests[16].tools == (calculator,)
    assert provider.requests[17].tools == (calculator,)
    assert provider.requests[17].system_prompt is not None
    assert "forbidden.py" not in provider.requests[17].system_prompt
    assert provider.requests[18].tools is request.tools


def test_rejects_second_inspection_requested_while_tools_are_withheld() -> None:
    """Fail safely after one ignored-withholding recovery attempt."""

    executions: list[dict[str, object]] = []
    read_file = create_read_file_definition()
    registry = ToolRegistry()
    registry.register(
        read_file,
        lambda arguments: executions.append(arguments) or {"content": "value"},
    )
    inspection_responses = [
        create_tool_response(
            ToolInvocation(
                id=f"read-{index}",
                tool_name="read_file",
                arguments={"path": f"module-{index}.py"},
            )
        )
        for index in range(16)
    ]
    first_ignored = create_tool_response(
        ToolInvocation(
            id="ignored-first",
            tool_name="read_file",
            arguments={"path": "first-forbidden.py"},
        )
    )
    second_ignored = create_tool_response(
        ToolInvocation(
            id="ignored-second",
            tool_name="read_file",
            arguments={"path": "second-forbidden.py"},
        )
    )
    provider = FakeProvider([*inspection_responses, first_ignored, second_ignored])

    with pytest.raises(
        CompletionError,
        match="repeatedly requested a read-only inspection tool",
    ):
        run_tool_calling_loop(
            provider,
            ChatRequest(messages=[], tools=(read_file,)),
            registry,
            max_tool_rounds=17,
        )

    assert len(provider.requests) == 18
    assert len(executions) == 16
    assert provider.requests[16].tools == ()
    assert provider.requests[17].tools == ()
    assert provider.requests[17].system_prompt is not None
    assert "first-forbidden.py" not in provider.requests[17].system_prompt


def test_repeated_batch_recovery_rejects_different_withheld_inspection() -> None:
    """Enforce withheld tools even when the provider changes inspection args."""

    read_executions: list[dict[str, object]] = []
    calculator_executions: list[dict[str, object]] = []
    read_file = create_read_file_definition()
    calculator = create_calculator_definition()
    registry = ToolRegistry()
    registry.register(
        read_file,
        lambda arguments: read_executions.append(arguments) or {"content": "value"},
    )
    registry.register(
        calculator,
        lambda arguments: calculator_executions.append(arguments) or {"value": 4},
    )
    first_inspection = create_tool_response(
        ToolInvocation(
            id="read-first",
            tool_name="read_file",
            arguments={"path": "first.py"},
        )
    )
    repeated_inspection = create_tool_response(
        ToolInvocation(
            id="read-repeat",
            tool_name="read_file",
            arguments={"path": "first.py"},
        )
    )
    different_withheld_inspection = create_tool_response(
        ToolInvocation(
            id="read-different",
            tool_name="read_file",
            arguments={"path": "different.py"},
        )
    )
    calculator_response = create_tool_response(
        ToolInvocation(
            id="calculate",
            tool_name="calculator",
            arguments={"expression": "2 + 2"},
        )
    )
    final_response = ChatResponse(text="Completed.")
    provider = FakeProvider(
        [
            first_inspection,
            repeated_inspection,
            different_withheld_inspection,
            calculator_response,
            final_response,
        ]
    )
    request = ChatRequest(messages=[], tools=(read_file, calculator))

    result = run_tool_calling_loop(
        provider,
        request,
        registry,
        max_tool_rounds=2,
    )

    assert result is final_response
    assert read_executions == [{"path": "first.py"}]
    assert calculator_executions == [{"expression": "2 + 2"}]
    assert provider.requests[2].tools == (calculator,)
    assert provider.requests[3].tools == (calculator,)
    assert provider.requests[4].tools is request.tools


def test_noninspection_round_resets_the_inspection_streak() -> None:
    """Allow additional inspections after meaningful non-inspection progress."""

    read_file = create_read_file_definition()
    calculator = create_calculator_definition()
    registry = ToolRegistry()
    registry.register(read_file, lambda arguments: {"content": "value"})
    registry.register(calculator, lambda arguments: {"value": 4})
    first_inspections = [
        create_tool_response(
            ToolInvocation(
                id=f"first-{index}",
                tool_name="read_file",
                arguments={"path": f"first-{index}.py"},
            )
        )
        for index in range(5)
    ]
    calculator_response = create_tool_response(
        ToolInvocation(
            id="calculate",
            tool_name="calculator",
            arguments={"expression": "2 + 2"},
        )
    )
    second_inspections = [
        create_tool_response(
            ToolInvocation(
                id=f"second-{index}",
                tool_name="read_file",
                arguments={"path": f"second-{index}.py"},
            )
        )
        for index in range(5)
    ]
    final_response = ChatResponse(text="Completed.")
    provider = FakeProvider(
        [
            *first_inspections,
            calculator_response,
            *second_inspections,
            final_response,
        ]
    )
    request = ChatRequest(messages=[], tools=(read_file, calculator))

    result = run_tool_calling_loop(
        provider,
        request,
        registry,
        max_tool_rounds=11,
    )

    assert result is final_response
    assert all(
        provider_request.tools is request.tools
        for provider_request in provider.requests
    )


def test_existing_sixteen_round_inspection_streak_starts_withheld_recovery() -> None:
    """Recover immediately when request history already reaches the limit."""

    read_file = create_read_file_definition()
    calculator = create_calculator_definition()
    completed_rounds = tuple(
        ToolInteractionRound(
            response=create_tool_response(
                ToolInvocation(
                    id=f"read-{index}",
                    tool_name="read_file",
                    arguments={"path": f"module-{index}.py"},
                )
            ),
            results=(
                ToolResult(
                    invocation_id=f"read-{index}",
                    status="success",
                    output={"content": "value"},
                ),
            ),
        )
        for index in range(16)
    )
    final_response = ChatResponse(text="Use the existing inspection results.")
    provider = FakeProvider([final_response])
    request = ChatRequest(
        messages=[],
        tools=(read_file, calculator),
        tool_interactions=completed_rounds,
    )

    result = run_tool_calling_loop(
        provider,
        request,
        ToolRegistry(),
        max_tool_rounds=1,
    )

    assert result is final_response
    assert provider.requests[0].tools == (calculator,)
    assert provider.requests[0].tool_interactions is completed_rounds
    assert provider.requests[0].system_prompt is not None
    assert "module-0.py" not in provider.requests[0].system_prompt
    assert request.tools == (read_file, calculator)


def test_approval_preview_error_resets_inspection_streak() -> None:
    """Treat a recovered non-inspection preview error as a streak break."""

    read_file = create_read_file_definition()
    calculator = create_calculator_definition()
    registry = ToolRegistry()
    registry.register(read_file, lambda arguments: {"content": "value"})

    def fail_preview(_arguments):
        raise CompletionError("invalid target")

    registry.register(
        calculator,
        lambda arguments: {"value": 4},
        requires_approval=True,
        approval_preview=fail_preview,
    )
    first_inspections = [
        create_tool_response(
            ToolInvocation(
                id=f"first-{index}",
                tool_name="read_file",
                arguments={"path": f"first-{index}.py"},
            )
        )
        for index in range(5)
    ]
    preview_error_response = create_tool_response(
        ToolInvocation(
            id="invalid-calculation",
            tool_name="calculator",
            arguments={"expression": "2 + 2"},
        )
    )
    final_inspection = create_tool_response(
        ToolInvocation(
            id="final-read",
            tool_name="read_file",
            arguments={"path": "final.py"},
        )
    )
    final_response = ChatResponse(text="Completed.")
    provider = FakeProvider(
        [
            *first_inspections,
            preview_error_response,
            final_inspection,
            final_response,
        ]
    )
    request = ChatRequest(
        messages=[],
        tools=(read_file, calculator),
    )

    result = run_tool_calling_loop(
        provider,
        request,
        registry,
        max_tool_rounds=7,
        recover_approval_preview_errors=True,
    )

    assert result is final_response
    assert all(
        provider_request.tools is request.tools
        for provider_request in provider.requests
    )


def test_unsafe_batch_recovery_keeps_inspection_tools_withheld() -> None:
    """Keep streak recovery active through one unsafe non-inspection batch."""

    read_file = create_read_file_definition()
    calculator = create_calculator_definition()
    read_executions: list[dict[str, object]] = []
    calculator_executions: list[dict[str, object]] = []
    registry = ToolRegistry()
    registry.register(
        read_file,
        lambda arguments: read_executions.append(arguments) or {"content": "value"},
    )
    registry.register(
        calculator,
        lambda arguments: calculator_executions.append(arguments) or {"value": 4},
    )
    inspections = [
        create_tool_response(
            ToolInvocation(
                id=f"read-{index}",
                tool_name="read_file",
                arguments={"path": f"module-{index}.py"},
            )
        )
        for index in range(16)
    ]
    unsafe_response = create_tool_response(
        ToolInvocation(
            id="duplicate-1",
            tool_name="calculator",
            arguments={"expression": "2 + 2"},
        ),
        ToolInvocation(
            id="duplicate-2",
            tool_name="calculator",
            arguments={"expression": "2 + 2"},
        ),
    )
    calculator_response = create_tool_response(
        ToolInvocation(
            id="calculate",
            tool_name="calculator",
            arguments={"expression": "2 + 2"},
        )
    )
    final_response = ChatResponse(text="Completed.")
    provider = FakeProvider(
        [
            *inspections,
            unsafe_response,
            calculator_response,
            final_response,
        ]
    )
    request = ChatRequest(
        messages=[],
        tools=(read_file, calculator),
    )

    result = run_tool_calling_loop(
        provider,
        request,
        registry,
        max_tool_rounds=17,
    )

    assert result is final_response
    assert len(read_executions) == 16
    assert calculator_executions == [{"expression": "2 + 2"}]
    assert provider.requests[16].tools == (calculator,)
    assert provider.requests[17].tools == (calculator,)
    assert provider.requests[18].tools is request.tools
