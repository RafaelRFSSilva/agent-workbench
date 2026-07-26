"""Tests for the OpenAI provider."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from openai import APIConnectionError, APIStatusError

from agent_workbench.context import (
    CONTEXT_DOCUMENTS_HEADER,
    ContextDocument,
)
from agent_workbench.errors import CompletionError
from agent_workbench.generation import GenerationConfig
from agent_workbench.messages import (
    ChatRequest,
    ChatResponse,
    Message,
    ToolInteractionRound,
)
from agent_workbench.providers.openai import OpenAIProvider
from agent_workbench.structured_outputs import JSONResponseFormat
from agent_workbench.tools import ToolDefinition, ToolInvocation, ToolResult


def create_fake_client(outcome: str | Exception) -> tuple[SimpleNamespace, Mock]:
    """Create a fake OpenAI client with a configured response or error."""

    create_mock = Mock()

    if isinstance(outcome, Exception):
        create_mock.side_effect = outcome
    else:
        create_mock.return_value = SimpleNamespace(
            output_text=outcome,
            output=[],
        )

    client = SimpleNamespace(
        responses=SimpleNamespace(create=create_mock),
    )

    return client, create_mock


def create_status_error(
    status_code: int,
    message: str,
) -> APIStatusError:
    """Create an OpenAI status error for provider tests."""

    request = httpx.Request(
        "POST",
        "https://api.openai.com/v1/responses",
    )
    response = httpx.Response(
        status_code,
        request=request,
    )

    return APIStatusError(
        message,
        response=response,
        body={
            "error": {
                "message": message,
            }
        },
    )


def test_provider_returns_response_text() -> None:
    """Return text produced by the OpenAI Responses API."""

    messages: list[Message] = [
        {
            "role": "user",
            "content": "Hello",
        }
    ]
    client, create_mock = create_fake_client("OpenAI provider working")
    provider = OpenAIProvider(
        model_name="test-model",
        client=client,
    )

    request = ChatRequest(
        messages=messages,
        system_prompt="You are a software reviewer.",
    )
    result = provider.complete(request)

    assert provider.name == "OpenAI"
    assert result == ChatResponse(
        text="OpenAI provider working",
    )
    create_mock.assert_called_once_with(
        model="test-model",
        input=messages,
        instructions="You are a software reviewer.",
    )


def test_connection_error_is_translated() -> None:
    """Translate an OpenAI connection failure into an application error."""

    request = httpx.Request(
        "POST",
        "https://api.openai.com/v1/responses",
    )
    error = APIConnectionError(request=request)
    client, _ = create_fake_client(error)
    provider = OpenAIProvider(
        model_name="test-model",
        client=client,
    )

    with pytest.raises(
        CompletionError,
        match="Unable to connect to OpenAI",
    ):
        provider.complete(ChatRequest(messages=[]))


def test_authentication_error_is_translated() -> None:
    """Provide a clear error when OpenAI authentication fails."""

    error = create_status_error(
        401,
        "Invalid API key.",
    )
    client, _ = create_fake_client(error)
    provider = OpenAIProvider(
        model_name="test-model",
        client=client,
    )

    with pytest.raises(
        CompletionError,
        match="OpenAI authentication failed",
    ):
        provider.complete(ChatRequest(messages=[]))


def test_missing_model_error_is_translated() -> None:
    """Provide a clear error when the configured model is unavailable."""

    error = create_status_error(
        404,
        "Model not found.",
    )
    client, _ = create_fake_client(error)
    provider = OpenAIProvider(
        model_name="missing-model",
        client=client,
    )

    with pytest.raises(
        CompletionError,
        match="Model 'missing-model' is not available through OpenAI",
    ):
        provider.complete(ChatRequest(messages=[]))


def test_rate_limit_error_is_translated() -> None:
    """Provide a clear error when a rate or quota limit is reached."""

    error = create_status_error(
        429,
        "Rate limit reached.",
    )
    client, _ = create_fake_client(error)
    provider = OpenAIProvider(
        model_name="test-model",
        client=client,
    )

    with pytest.raises(
        CompletionError,
        match="rate limit or account quota was exceeded",
    ):
        provider.complete(ChatRequest(messages=[]))


def test_context_documents_are_added_to_instructions() -> None:
    """Send context documents through OpenAI instructions."""

    client, create_mock = create_fake_client("OpenAI context received")
    provider = OpenAIProvider(
        model_name="test-model",
        client=client,
    )
    messages: list[Message] = [
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
        text="OpenAI context received",
    )
    create_mock.assert_called_once_with(
        model="test-model",
        input=messages,
        instructions=(
            f"{CONTEXT_DOCUMENTS_HEADER}\n\n"
            '<context_document source="README.md">\n'
            "Agent Workbench documentation.\n"
            "</context_document>"
        ),
    )


def test_generation_config_is_translated_to_openai_arguments() -> None:
    """Translate shared generation settings into OpenAI arguments."""

    client, create_mock = create_fake_client("Configured OpenAI response")
    provider = OpenAIProvider(
        model_name="test-model",
        client=client,
    )
    messages: list[Message] = [
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
        text="Configured OpenAI response",
    )
    create_mock.assert_called_once_with(
        model="test-model",
        input=messages,
        temperature=0.2,
        top_p=0.8,
        max_output_tokens=256,
    )


def test_response_format_is_translated_to_openai_text_config() -> None:
    """Translate the shared response format into OpenAI text configuration."""

    structured_response = '{"summary":"No critical issues.","risk_level":"low"}'
    client, create_mock = create_fake_client(structured_response)
    provider = OpenAIProvider(
        model_name="test-model",
        client=client,
    )
    messages: list[Message] = [
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
    create_mock.assert_called_once_with(
        model="test-model",
        input=messages,
        text={
            "format": {
                "type": "json_schema",
                "name": "software_review",
                "schema": schema,
                "strict": True,
            }
        },
    )


def test_tools_are_translated_to_openai_functions() -> None:
    """Translate shared tool definitions into OpenAI functions."""

    client, create_mock = create_fake_client("")
    provider = OpenAIProvider(
        model_name="test-model",
        client=client,
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
    create_mock.assert_called_once_with(
        model="test-model",
        input=request.messages,
        tools=[
            {
                "type": "function",
                "name": "calculator",
                "description": "Evaluate a mathematical expression.",
                "parameters": calculator_schema,
                "strict": True,
            },
            {
                "type": "function",
                "name": "project_information",
                "description": "Return project information.",
                "parameters": project_information_schema,
                "strict": True,
            },
        ],
    )


def test_function_calls_are_translated_to_tool_invocations() -> None:
    """Translate OpenAI function calls into shared tool invocations."""

    response = SimpleNamespace(
        output_text="Calling the requested tools.",
        output=[
            SimpleNamespace(
                type="function_call",
                call_id="call_calculator",
                name="calculator",
                arguments='{"expression":"2 + 2"}',
            ),
            SimpleNamespace(
                type="message",
            ),
            SimpleNamespace(
                type="function_call",
                call_id="call_project_information",
                name="project_information",
                arguments="{}",
            ),
        ],
    )
    create_mock = Mock(return_value=response)
    client = SimpleNamespace(
        responses=SimpleNamespace(create=create_mock),
    )
    provider = OpenAIProvider(
        model_name="test-model",
        client=client,
    )

    result = provider.complete(
        ChatRequest(
            messages=[
                {
                    "role": "user",
                    "content": "Calculate two plus two and describe the project.",
                }
            ],
        )
    )

    assert result == ChatResponse(
        text="Calling the requested tools.",
        tool_invocations=(
            ToolInvocation(
                id="call_calculator",
                tool_name="calculator",
                arguments={
                    "expression": "2 + 2",
                },
            ),
            ToolInvocation(
                id="call_project_information",
                tool_name="project_information",
                arguments={},
            ),
        ),
    )


@pytest.mark.parametrize(
    "arguments",
    [
        '{"expression":',
        '["2 + 2"]',
    ],
)
def test_malformed_function_arguments_raise_completion_error(
    arguments: str,
) -> None:
    """Reject malformed OpenAI function arguments as provider response errors."""

    response = SimpleNamespace(
        output_text="",
        output=[
            SimpleNamespace(
                type="function_call",
                call_id="call_calculator",
                name="calculator",
                arguments=arguments,
            )
        ],
    )
    client = SimpleNamespace(
        responses=SimpleNamespace(create=Mock(return_value=response)),
    )
    provider = OpenAIProvider(
        model_name="test-model",
        client=client,
    )

    with pytest.raises(
        CompletionError,
        match="malformed tool invocation",
    ):
        provider.complete(ChatRequest(messages=[]))


def test_tool_interactions_are_translated_to_openai_input_items() -> None:
    """Translate ordered shared tool interactions into Responses API input."""

    client, create_mock = create_fake_client("Tool interactions processed")
    provider = OpenAIProvider(
        model_name="test-model",
        client=client,
    )
    first_result = ToolResult(
        invocation_id="call-1",
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
                    id="call-1",
                    tool_name="calculator",
                    arguments={
                        "expression": "2 + 2",
                    },
                ),
                ToolInvocation(
                    id="call-2",
                    tool_name="project_information",
                    arguments={},
                ),
            ),
        ),
        results=(
            first_result,
            ToolResult(
                invocation_id="call-2",
                status="error",
                error="Project information is unavailable.",
            ),
        ),
    )
    second_round = ToolInteractionRound(
        response=ChatResponse(
            tool_invocations=(
                ToolInvocation(
                    id="call-3",
                    tool_name="identity",
                    arguments={},
                ),
                ToolInvocation(
                    id="call-4",
                    tool_name="increment",
                    arguments={
                        "left": 2,
                        "right": 3,
                    },
                ),
                ToolInvocation(
                    id="call-5",
                    tool_name="list_files",
                    arguments={},
                ),
            ),
        ),
        results=(
            ToolResult(
                invocation_id="call-3",
                status="success",
            ),
            ToolResult(
                invocation_id="call-4",
                status="success",
                output=5,
            ),
            ToolResult(
                invocation_id="call-5",
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
    messages: list[Message] = [
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

    assert result == ChatResponse(text="Tool interactions processed")
    assert first_result.output == {
        "value": 4,
    }
    create_mock.assert_called_once_with(
        model="test-model",
        input=[
            *messages,
            {
                "role": "assistant",
                "content": "I will calculate and inspect the project.",
            },
            {
                "type": "function_call",
                "call_id": "call-1",
                "name": "calculator",
                "arguments": '{"expression":"2 + 2"}',
            },
            {
                "type": "function_call",
                "call_id": "call-2",
                "name": "project_information",
                "arguments": "{}",
            },
            {
                "type": "function_call_output",
                "call_id": "call-1",
                "output": '{"output":{"value":4},"status":"success"}',
            },
            {
                "type": "function_call_output",
                "call_id": "call-2",
                "output": (
                    '{"error":"Project information is unavailable.","status":"error"}'
                ),
            },
            {
                "type": "function_call",
                "call_id": "call-3",
                "name": "identity",
                "arguments": "{}",
            },
            {
                "type": "function_call",
                "call_id": "call-4",
                "name": "increment",
                "arguments": '{"left":2,"right":3}',
            },
            {
                "type": "function_call",
                "call_id": "call-5",
                "name": "list_files",
                "arguments": "{}",
            },
            {
                "type": "function_call_output",
                "call_id": "call-3",
                "output": '{"output":null,"status":"success"}',
            },
            {
                "type": "function_call_output",
                "call_id": "call-4",
                "output": '{"output":5,"status":"success"}',
            },
            {
                "type": "function_call_output",
                "call_id": "call-5",
                "output": ('{"output":["README.md",{"count":2}],"status":"success"}'),
            },
        ],
    )
