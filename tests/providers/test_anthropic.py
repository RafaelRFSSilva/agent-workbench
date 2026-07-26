"""Tests for the Anthropic provider."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from anthropic import (
    APIConnectionError,
    AuthenticationError,
    NotFoundError,
    RateLimitError,
)

from agent_workbench.context import (
    CONTEXT_DOCUMENTS_HEADER,
    ContextDocument,
)
from agent_workbench.errors import CompletionError
from agent_workbench.generation import GenerationConfig
from agent_workbench.messages import (
    ChatRequest,
    ChatResponse,
    ToolInteractionRound,
)
from agent_workbench.providers.anthropic import AnthropicProvider
from agent_workbench.structured_outputs import JSONResponseFormat
from agent_workbench.tools import ToolDefinition, ToolInvocation, ToolResult


def test_complete_returns_concatenated_text_blocks() -> None:
    """Return text content and pass the expected request to Anthropic."""

    create = Mock(
        return_value=SimpleNamespace(
            content=[
                SimpleNamespace(type="text", text="Hello"),
                SimpleNamespace(type="text", text=" world"),
            ]
        )
    )
    client = SimpleNamespace(
        messages=SimpleNamespace(create=create),
    )
    provider = AnthropicProvider(
        model_name="claude-test",
        client=client,
        max_tokens=256,
    )
    messages = [
        {
            "role": "user",
            "content": "Hello",
        }
    ]

    request = ChatRequest(
        messages=messages,
        system_prompt="You are a software reviewer.",
    )
    result = provider.complete(request)

    assert result == ChatResponse(
        text="Hello world",
    )
    create.assert_called_once_with(
        model="claude-test",
        max_tokens=256,
        messages=messages,
        system="You are a software reviewer.",
    )


def test_complete_translates_connection_errors() -> None:
    """Translate Anthropic connection failures into CompletionError."""

    request = httpx.Request(
        "POST",
        "https://api.anthropic.com/v1/messages",
    )
    create = Mock(
        side_effect=APIConnectionError(request=request),
    )
    client = SimpleNamespace(
        messages=SimpleNamespace(create=create),
    )
    provider = AnthropicProvider(
        model_name="claude-test",
        client=client,
    )

    with pytest.raises(
        CompletionError,
        match="Unable to connect to Anthropic",
    ):
        provider.complete(ChatRequest(messages=[]))


def test_complete_translates_authentication_errors() -> None:
    """Translate Anthropic authentication failures into CompletionError."""

    request = httpx.Request(
        "POST",
        "https://api.anthropic.com/v1/messages",
    )
    response = httpx.Response(401, request=request)
    error = AuthenticationError(
        "Authentication failed",
        response=response,
        body=None,
    )
    create = Mock(side_effect=error)
    client = SimpleNamespace(
        messages=SimpleNamespace(create=create),
    )
    provider = AnthropicProvider(
        model_name="claude-test",
        client=client,
    )

    with pytest.raises(
        CompletionError,
        match="authentication failed",
    ):
        provider.complete(ChatRequest(messages=[]))


def test_complete_translates_missing_model_errors() -> None:
    """Translate missing Anthropic models into CompletionError."""

    request = httpx.Request(
        "POST",
        "https://api.anthropic.com/v1/messages",
    )
    response = httpx.Response(404, request=request)
    error = NotFoundError(
        "Model not found",
        response=response,
        body=None,
    )
    create = Mock(side_effect=error)
    client = SimpleNamespace(
        messages=SimpleNamespace(create=create),
    )
    provider = AnthropicProvider(
        model_name="claude-test",
        client=client,
    )

    with pytest.raises(
        CompletionError,
        match="Model 'claude-test' is not available through Anthropic",
    ):
        provider.complete(ChatRequest(messages=[]))


def test_complete_translates_rate_limit_errors() -> None:
    """Translate Anthropic rate limits into CompletionError."""

    request = httpx.Request(
        "POST",
        "https://api.anthropic.com/v1/messages",
    )
    response = httpx.Response(429, request=request)
    error = RateLimitError(
        "Rate limited",
        response=response,
        body=None,
    )
    create = Mock(side_effect=error)
    client = SimpleNamespace(
        messages=SimpleNamespace(create=create),
    )
    provider = AnthropicProvider(
        model_name="claude-test",
        client=client,
    )

    with pytest.raises(
        CompletionError,
        match="rate limit or account quota",
    ):
        provider.complete(ChatRequest(messages=[]))


def test_context_documents_are_added_to_system_instructions() -> None:
    """Send context documents through Anthropic's system parameter."""

    create = Mock(
        return_value=SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="text",
                    text="Anthropic context received",
                )
            ]
        )
    )
    client = SimpleNamespace(
        messages=SimpleNamespace(create=create),
    )
    provider = AnthropicProvider(
        model_name="claude-test",
        client=client,
        max_tokens=256,
    )
    messages = [
        {
            "role": "user",
            "content": "Summarize the project.",
        }
    ]

    request = ChatRequest(
        messages=messages,
        context_documents=(
            ContextDocument(
                source=Path("README.md"),
                content="Agent Workbench documentation.",
            ),
        ),
    )

    result = provider.complete(request)

    assert result == ChatResponse(
        text="Anthropic context received",
    )
    create.assert_called_once_with(
        model="claude-test",
        max_tokens=256,
        messages=messages,
        system=(
            f"{CONTEXT_DOCUMENTS_HEADER}\n\n"
            '<context_document source="README.md">\n'
            "Agent Workbench documentation.\n"
            "</context_document>"
        ),
    )


def test_generation_config_is_translated_to_anthropic_arguments() -> None:
    """Translate shared generation settings into Anthropic arguments."""

    create = Mock(
        return_value=SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="text",
                    text="Configured Anthropic response",
                )
            ]
        )
    )
    client = SimpleNamespace(
        messages=SimpleNamespace(create=create),
    )
    provider = AnthropicProvider(
        model_name="claude-test",
        client=client,
    )
    messages = [
        {
            "role": "user",
            "content": "Generate a short response.",
        }
    ]

    request = ChatRequest(
        messages=messages,
        generation_config=GenerationConfig(
            temperature=0.2,
            top_p=0.8,
            max_output_tokens=256,
        ),
    )

    result = provider.complete(request)

    assert result == ChatResponse(
        text="Configured Anthropic response",
    )
    create.assert_called_once_with(
        model="claude-test",
        max_tokens=256,
        messages=messages,
        temperature=0.2,
        top_p=0.8,
    )


def test_response_format_is_translated_to_anthropic_output_config() -> None:
    """Translate the shared response format into Anthropic output config."""

    structured_response = '{"summary":"No critical issues.","risk_level":"low"}'
    create = Mock(
        return_value=SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="text",
                    text=structured_response,
                )
            ]
        )
    )
    client = SimpleNamespace(
        messages=SimpleNamespace(create=create),
    )
    provider = AnthropicProvider(
        model_name="claude-test",
        client=client,
        max_tokens=256,
    )
    messages = [
        {
            "role": "user",
            "content": "Review this implementation.",
        }
    ]
    schema = {
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
    }

    request = ChatRequest(
        messages=messages,
        response_format=JSONResponseFormat(
            name="software_review",
            schema=schema,
        ),
    )

    result = provider.complete(request)

    assert result == ChatResponse(
        text=structured_response,
    )

    create.assert_called_once_with(
        model="claude-test",
        max_tokens=256,
        messages=messages,
        output_config={
            "format": {
                "type": "json_schema",
                "schema": schema,
            }
        },
    )


def test_tools_are_translated_to_anthropic_tools() -> None:
    """Translate shared tool definitions into Anthropic tools."""

    create = Mock(return_value=SimpleNamespace(content=[]))
    client = SimpleNamespace(
        messages=SimpleNamespace(create=create),
    )
    provider = AnthropicProvider(
        model_name="claude-test",
        client=client,
        max_tokens=256,
    )
    calculator_schema = {
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
    }
    project_information_schema = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    request = ChatRequest(
        messages=[
            {
                "role": "user",
                "content": "Calculate two plus two and describe the project.",
            }
        ],
        tools=(
            ToolDefinition(
                name="calculator",
                description="Evaluate a mathematical expression.",
                input_schema=calculator_schema,
            ),
            ToolDefinition(
                name="project_information",
                description="Return project information.",
                input_schema=project_information_schema,
            ),
        ),
    )

    assert provider.complete(request) == ChatResponse()
    create.assert_called_once_with(
        model="claude-test",
        max_tokens=256,
        messages=request.messages,
        tools=[
            {
                "name": "calculator",
                "description": "Evaluate a mathematical expression.",
                "input_schema": calculator_schema,
            },
            {
                "name": "project_information",
                "description": "Return project information.",
                "input_schema": project_information_schema,
            },
        ],
    )


def test_tool_use_blocks_are_translated_to_tool_invocations() -> None:
    """Translate Anthropic tool-use blocks into shared tool invocations."""

    create = Mock(
        return_value=SimpleNamespace(
            content=[
                SimpleNamespace(type="text", text="Calling the requested tools. "),
                SimpleNamespace(
                    type="tool_use",
                    id="toolu_calculator",
                    name="calculator",
                    input={
                        "expression": "2 + 2",
                    },
                ),
                SimpleNamespace(type="text", text="Waiting for results."),
                SimpleNamespace(
                    type="tool_use",
                    id="toolu_project_information",
                    name="project_information",
                    input={},
                ),
            ]
        )
    )
    client = SimpleNamespace(
        messages=SimpleNamespace(create=create),
    )
    provider = AnthropicProvider(
        model_name="claude-test",
        client=client,
    )

    result = provider.complete(
        ChatRequest(
            messages=[
                {
                    "role": "user",
                    "content": "Calculate two plus two and describe the project.",
                }
            ]
        )
    )

    assert result == ChatResponse(
        text="Calling the requested tools. Waiting for results.",
        tool_invocations=(
            ToolInvocation(
                id="toolu_calculator",
                tool_name="calculator",
                arguments={
                    "expression": "2 + 2",
                },
            ),
            ToolInvocation(
                id="toolu_project_information",
                tool_name="project_information",
                arguments={},
            ),
        ),
    )


@pytest.mark.parametrize(
    "tool_use",
    [
        SimpleNamespace(
            type="tool_use",
            id="",
            name="calculator",
            input={},
        ),
        SimpleNamespace(
            type="tool_use",
            id="toolu_calculator",
            name="calculator",
            input=[],
        ),
    ],
)
def test_malformed_tool_use_blocks_raise_completion_error(
    tool_use: SimpleNamespace,
) -> None:
    """Reject malformed Anthropic tool-use data as a provider response error."""

    create = Mock(return_value=SimpleNamespace(content=[tool_use]))
    client = SimpleNamespace(
        messages=SimpleNamespace(create=create),
    )
    provider = AnthropicProvider(
        model_name="claude-test",
        client=client,
    )

    with pytest.raises(
        CompletionError,
        match="malformed tool invocation",
    ):
        provider.complete(ChatRequest(messages=[]))


def test_tool_interactions_are_translated_to_anthropic_messages() -> None:
    """Translate ordered shared tool interactions into Anthropic messages."""

    create = Mock(return_value=SimpleNamespace(content=[]))
    client = SimpleNamespace(
        messages=SimpleNamespace(create=create),
    )
    provider = AnthropicProvider(
        model_name="claude-test",
        client=client,
        max_tokens=256,
    )
    first_result = ToolResult(
        invocation_id="toolu-1",
        status="success",
        output={
            "value": 4,
        },
    )
    first_round = ToolInteractionRound(
        response=ChatResponse(
            text="I will calculate and inspect the project.",
            tool_invocations=(
                ToolInvocation(
                    id="toolu-1",
                    tool_name="calculator",
                    arguments={
                        "expression": "2 + 2",
                    },
                ),
                ToolInvocation(
                    id="toolu-2",
                    tool_name="project_information",
                    arguments={},
                ),
            ),
        ),
        results=(
            first_result,
            ToolResult(
                invocation_id="toolu-2",
                status="error",
                error="Project information is unavailable.",
            ),
        ),
    )
    second_round = ToolInteractionRound(
        response=ChatResponse(
            tool_invocations=(
                ToolInvocation(
                    id="toolu-3",
                    tool_name="identity",
                    arguments={},
                ),
                ToolInvocation(
                    id="toolu-4",
                    tool_name="increment",
                    arguments={
                        "left": 2,
                        "right": 3,
                    },
                ),
                ToolInvocation(
                    id="toolu-5",
                    tool_name="list_files",
                    arguments={},
                ),
            ),
        ),
        results=(
            ToolResult(
                invocation_id="toolu-3",
                status="success",
            ),
            ToolResult(
                invocation_id="toolu-4",
                status="success",
                output=5,
            ),
            ToolResult(
                invocation_id="toolu-5",
                status="success",
                output=[
                    "README.md",
                    {
                        "count": 2,
                    },
                ],
            ),
        ),
    )
    messages = [
        {
            "role": "user",
            "content": "Calculate and inspect the project.",
        }
    ]

    result = provider.complete(
        ChatRequest(
            messages=messages,
            tool_interactions=(
                first_round,
                second_round,
            ),
        )
    )

    assert result == ChatResponse()
    assert first_result.output == {
        "value": 4,
    }
    create.assert_called_once_with(
        model="claude-test",
        max_tokens=256,
        messages=[
            *messages,
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "I will calculate and inspect the project.",
                    },
                    {
                        "type": "tool_use",
                        "id": "toolu-1",
                        "name": "calculator",
                        "input": {
                            "expression": "2 + 2",
                        },
                    },
                    {
                        "type": "tool_use",
                        "id": "toolu-2",
                        "name": "project_information",
                        "input": {},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu-1",
                        "content": '{"output":{"value":4},"status":"success"}',
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu-2",
                        "content": (
                            '{"error":"Project information is unavailable.",'
                            '"status":"error"}'
                        ),
                        "is_error": True,
                    },
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu-3",
                        "name": "identity",
                        "input": {},
                    },
                    {
                        "type": "tool_use",
                        "id": "toolu-4",
                        "name": "increment",
                        "input": {
                            "left": 2,
                            "right": 3,
                        },
                    },
                    {
                        "type": "tool_use",
                        "id": "toolu-5",
                        "name": "list_files",
                        "input": {},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu-3",
                        "content": '{"output":null,"status":"success"}',
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu-4",
                        "content": '{"output":5,"status":"success"}',
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu-5",
                        "content": (
                            '{"output":["README.md",{"count":2}],"status":"success"}'
                        ),
                    },
                ],
            },
        ],
    )
