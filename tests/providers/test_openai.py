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
from agent_workbench.messages import ChatRequest, Message
from agent_workbench.providers.openai import OpenAIProvider


def create_fake_client(outcome: str | Exception) -> tuple[SimpleNamespace, Mock]:
    """Create a fake OpenAI client with a configured response or error."""

    create_mock = Mock()

    if isinstance(outcome, Exception):
        create_mock.side_effect = outcome
    else:
        create_mock.return_value = SimpleNamespace(output_text=outcome)

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
    assert result == "OpenAI provider working"
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

    assert result == "OpenAI context received"
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

    assert result == "Configured OpenAI response"
    create_mock.assert_called_once_with(
        model="test-model",
        input=messages,
        temperature=0.2,
        top_p=0.8,
        max_output_tokens=256,
    )
