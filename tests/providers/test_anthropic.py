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
from agent_workbench.messages import ChatRequest
from agent_workbench.providers.anthropic import AnthropicProvider


def test_complete_returns_concatenated_text_blocks() -> None:
    """Return text content and pass the expected request to Anthropic."""

    create = Mock(
        return_value=SimpleNamespace(
            content=[
                SimpleNamespace(type="text", text="Hello"),
                SimpleNamespace(type="tool_use"),
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

    assert result == "Hello world"
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

    assert result == "Anthropic context received"
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

    assert result == "Configured Anthropic response"
    create.assert_called_once_with(
        model="claude-test",
        max_tokens=256,
        messages=messages,
        temperature=0.2,
        top_p=0.8,
    )
