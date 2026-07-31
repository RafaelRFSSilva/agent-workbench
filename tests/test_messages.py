"""Tests for provider-independent chat request types."""

from pathlib import Path

import pytest

from agent_workbench.context import ContextDocument
from agent_workbench.errors import ConfigurationError
from agent_workbench.generation import GenerationConfig
from agent_workbench.messages import (
    ChatRequest,
    ChatResponse,
    ToolInteractionRound,
)
from agent_workbench.structured_outputs import JSONResponseFormat
from agent_workbench.tools import (
    ToolDefinition,
    ToolInvocation,
    ToolResult,
)


def test_chat_request_has_no_context_documents_by_default() -> None:
    """Create a chat request without context documents."""

    request = ChatRequest(
        messages=[
            {
                "role": "user",
                "content": "Hello.",
            }
        ]
    )

    assert request.context_documents == ()


def test_chat_request_preserves_context_document_order() -> None:
    """Preserve context documents in their supplied order."""

    first_document = ContextDocument(
        source=Path("README.md"),
        content="First document.",
    )
    second_document = ContextDocument(
        source=Path("pyproject.toml"),
        content="Second document.",
    )

    request = ChatRequest(
        messages=[],
        context_documents=(
            first_document,
            second_document,
        ),
    )

    assert request.context_documents == (
        first_document,
        second_document,
    )


def test_chat_request_uses_default_generation_config() -> None:
    """Use provider defaults when generation parameters are omitted."""

    request = ChatRequest(messages=[])

    assert request.generation_config == GenerationConfig()
    assert request.generation_config.temperature is None
    assert request.generation_config.top_p is None
    assert request.generation_config.max_output_tokens is None


def test_chat_request_preserves_generation_config() -> None:
    """Preserve the supplied provider-independent generation configuration."""

    generation_config = GenerationConfig(
        temperature=0.2,
        top_p=0.8,
        max_output_tokens=512,
    )

    request = ChatRequest(
        messages=[],
        generation_config=generation_config,
    )

    assert request.generation_config == generation_config


def test_chat_request_has_no_response_format_by_default() -> None:
    """Use unstructured text responses when no format is supplied."""

    request = ChatRequest(messages=[])

    assert request.response_format is None


def test_chat_request_preserves_response_format() -> None:
    """Preserve the supplied provider-independent response format."""

    response_format = JSONResponseFormat(
        name="software_review",
        schema={
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                },
                "risk_level": {
                    "type": "string",
                    "enum": [
                        "low",
                        "medium",
                        "high",
                    ],
                },
            },
            "required": [
                "summary",
                "risk_level",
            ],
            "additionalProperties": False,
        },
    )

    request = ChatRequest(
        messages=[],
        response_format=response_format,
    )

    assert request.response_format == response_format


def test_chat_request_has_no_tools_by_default() -> None:
    """Create a chat request without available tools."""

    request = ChatRequest(messages=[])

    assert request.tools == ()


def test_chat_request_preserves_tool_order() -> None:
    """Preserve tool definitions in their supplied order."""

    calculator = ToolDefinition(
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
    project_information = ToolDefinition(
        name="project_information",
        description="Return static project information.",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    )

    request = ChatRequest(
        messages=[],
        tools=(
            calculator,
            project_information,
        ),
    )

    assert request.tools == (
        calculator,
        project_information,
    )


def test_chat_response_has_no_tool_invocations_by_default() -> None:
    """Create a text response without tool invocations."""

    response = ChatResponse(text="Provider response.")

    assert response.text == "Provider response."
    assert response.tool_invocations == ()
    assert response.response_repair_attempt_count == 0


@pytest.mark.parametrize("value", [True, -1, 1.5, "1"])
def test_chat_response_rejects_invalid_response_repair_count(value: object) -> None:
    """Require non-negative integer provider response-repair accounting."""

    with pytest.raises(
        ConfigurationError,
        match="response repair attempt count must be a non-negative integer",
    ):
        ChatResponse(response_repair_attempt_count=value)  # type: ignore[arg-type]


def test_chat_response_preserves_text_exactly() -> None:
    """Preserve provider text without normalizing its whitespace."""

    response = ChatResponse(text="  Provider response.  ")

    assert response.text == "  Provider response.  "


def test_chat_response_preserves_tool_invocation_order() -> None:
    """Preserve tool invocations in provider response order."""

    first_invocation = ToolInvocation(
        id="call-1",
        tool_name="calculator",
        arguments={
            "expression": "2 + 2",
        },
    )
    second_invocation = ToolInvocation(
        id="call-2",
        tool_name="project_information",
        arguments={},
    )

    response = ChatResponse(
        tool_invocations=(
            first_invocation,
            second_invocation,
        ),
    )

    assert response.text == ""
    assert response.tool_invocations == (
        first_invocation,
        second_invocation,
    )


def test_chat_request_has_no_tool_interactions_by_default() -> None:
    """Create a chat request without completed tool interaction rounds."""

    request = ChatRequest(messages=[])

    assert request.tool_interactions == ()


def test_chat_request_preserves_multiple_tool_interaction_rounds() -> None:
    """Preserve ordered tool responses and results across multiple rounds."""

    calculator_invocation = ToolInvocation(
        id="call-1",
        tool_name="calculator",
        arguments={
            "expression": "2 + 2",
        },
    )
    project_invocation = ToolInvocation(
        id="call-2",
        tool_name="project_information",
        arguments={},
    )
    follow_up_invocation = ToolInvocation(
        id="call-3",
        tool_name="calculator",
        arguments={
            "expression": "4 * 2",
        },
    )
    first_round = ToolInteractionRound(
        response=ChatResponse(
            text="I will calculate and inspect the project.",
            tool_invocations=(
                calculator_invocation,
                project_invocation,
            ),
        ),
        results=(
            ToolResult(
                invocation_id="call-1",
                status="success",
                output={
                    "value": 4,
                },
            ),
            ToolResult(
                invocation_id="call-2",
                status="error",
                error="Project information is unavailable.",
            ),
        ),
    )
    second_round = ToolInteractionRound(
        response=ChatResponse(
            text="I need one follow-up calculation.",
            tool_invocations=(follow_up_invocation,),
        ),
        results=(
            ToolResult(
                invocation_id="call-3",
                status="success",
                output={
                    "value": 8,
                },
            ),
        ),
    )
    messages = [
        {
            "role": "user",
            "content": "Calculate and describe the project.",
        }
    ]

    request = ChatRequest(
        messages=messages,
        tool_interactions=(
            first_round,
            second_round,
        ),
    )

    assert request.messages == messages
    assert request.tool_interactions == (
        first_round,
        second_round,
    )
    assert first_round.response.text == ("I will calculate and inspect the project.")
    assert first_round.response.tool_invocations == (
        calculator_invocation,
        project_invocation,
    )
    assert tuple(result.invocation_id for result in first_round.results) == (
        "call-1",
        "call-2",
    )


def test_tool_interaction_round_requires_tool_invocations() -> None:
    """Reject interaction rounds without a tool invocation."""

    with pytest.raises(
        ConfigurationError,
        match="must contain at least one tool invocation",
    ):
        ToolInteractionRound(
            response=ChatResponse(text="No tool needed."),
            results=(),
        )


def test_tool_interaction_round_rejects_duplicate_invocation_ids() -> None:
    """Reject ambiguous interaction rounds with duplicate invocation IDs."""

    with pytest.raises(
        ConfigurationError,
        match="duplicate tool invocation ids",
    ):
        ToolInteractionRound(
            response=ChatResponse(
                tool_invocations=(
                    ToolInvocation(
                        id="call-1",
                        tool_name="calculator",
                        arguments={},
                    ),
                    ToolInvocation(
                        id="call-1",
                        tool_name="project_information",
                        arguments={},
                    ),
                ),
            ),
            results=(),
        )


def test_tool_interaction_round_rejects_missing_results() -> None:
    """Require one result for every requested tool invocation."""

    with pytest.raises(
        ConfigurationError,
        match="missing a result for invocation 'call-2'",
    ):
        ToolInteractionRound(
            response=ChatResponse(
                tool_invocations=(
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
            ),
            results=(
                ToolResult(
                    invocation_id="call-1",
                    status="success",
                    output={
                        "value": 4,
                    },
                ),
            ),
        )


def test_tool_interaction_round_rejects_duplicate_results() -> None:
    """Reject multiple results associated with the same invocation."""

    with pytest.raises(
        ConfigurationError,
        match="duplicate results for invocation 'call-1'",
    ):
        ToolInteractionRound(
            response=ChatResponse(
                tool_invocations=(
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
            ),
            results=(
                ToolResult(
                    invocation_id="call-1",
                    status="success",
                    output={
                        "value": 4,
                    },
                ),
                ToolResult(
                    invocation_id="call-1",
                    status="error",
                    error="Duplicate execution.",
                ),
            ),
        )


def test_tool_interaction_round_rejects_unknown_results() -> None:
    """Reject results that do not match a requested invocation."""

    with pytest.raises(
        ConfigurationError,
        match="result for unknown invocation 'call-unknown'",
    ):
        ToolInteractionRound(
            response=ChatResponse(
                tool_invocations=(
                    ToolInvocation(
                        id="call-1",
                        tool_name="calculator",
                        arguments={},
                    ),
                ),
            ),
            results=(
                ToolResult(
                    invocation_id="call-unknown",
                    status="success",
                    output={
                        "value": 4,
                    },
                ),
            ),
        )


def test_tool_interaction_round_rejects_incorrectly_ordered_results() -> None:
    """Require results to follow their tool invocation order."""

    with pytest.raises(
        ConfigurationError,
        match="results must follow tool invocation order",
    ):
        ToolInteractionRound(
            response=ChatResponse(
                tool_invocations=(
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
            ),
            results=(
                ToolResult(
                    invocation_id="call-2",
                    status="success",
                    output={
                        "project": "Agent Workbench",
                    },
                ),
                ToolResult(
                    invocation_id="call-1",
                    status="success",
                    output={
                        "value": 4,
                    },
                ),
            ),
        )
