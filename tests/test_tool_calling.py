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
from agent_workbench.tool_calling import run_tool_calling_loop
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
