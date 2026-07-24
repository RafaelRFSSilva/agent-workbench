"""Tests for the Ollama provider."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from ollama import ResponseError

from agent_workbench.context import (
    CONTEXT_DOCUMENTS_HEADER,
    ContextDocument,
)
from agent_workbench.errors import CompletionError
from agent_workbench.messages import ChatRequest
from agent_workbench.providers.ollama import OllamaProvider


def test_provider_returns_model_response(monkeypatch) -> None:
    """Return text and translate the shared request for Ollama."""

    captured_arguments = {}

    def fake_chat(**kwargs):
        captured_arguments.update(kwargs)

        return SimpleNamespace(
            message=SimpleNamespace(
                content="provider working",
            )
        )

    monkeypatch.setattr(
        "agent_workbench.providers.ollama.chat",
        fake_chat,
    )

    provider = OllamaProvider(model_name="test-model")
    request = ChatRequest(
        system_prompt="You are a software reviewer.",
        messages=[
            {
                "role": "user",
                "content": "Review this code.",
            }
        ],
    )

    assert provider.complete(request) == "provider working"
    assert captured_arguments == {
        "model": "test-model",
        "messages": [
            {
                "role": "system",
                "content": "You are a software reviewer.",
            },
            {
                "role": "user",
                "content": "Review this code.",
            },
        ],
        "stream": False,
    }


def test_connection_error_is_translated(monkeypatch) -> None:
    """Translate an Ollama connection failure into an application error."""

    def fake_chat(**kwargs) -> None:
        raise ConnectionError

    monkeypatch.setattr("agent_workbench.providers.ollama.chat", fake_chat)

    provider = OllamaProvider(model_name="test-model")

    with pytest.raises(
        CompletionError,
        match="Unable to connect to Ollama",
    ):
        provider.complete(ChatRequest(messages=[]))


def test_missing_model_error_is_translated(monkeypatch) -> None:
    """Provide a clear error when the configured model is unavailable."""

    def fake_chat(**kwargs) -> None:
        raise ResponseError("model not found", 404)

    monkeypatch.setattr("agent_workbench.providers.ollama.chat", fake_chat)

    provider = OllamaProvider(model_name="missing-model")

    with pytest.raises(
        CompletionError,
        match="Model 'missing-model' is not available",
    ):
        provider.complete(ChatRequest(messages=[]))


def test_context_documents_are_added_as_system_instructions(
    monkeypatch,
) -> None:
    """Send context documents through Ollama's system message."""

    captured_arguments = {}

    def fake_chat(**kwargs):
        captured_arguments.update(kwargs)

        return SimpleNamespace(
            message=SimpleNamespace(
                content="context received",
            )
        )

    monkeypatch.setattr(
        "agent_workbench.providers.ollama.chat",
        fake_chat,
    )

    provider = OllamaProvider(model_name="test-model")
    request = ChatRequest(
        messages=[
            {
                "role": "user",
                "content": "Summarize the project.",
            }
        ],
        context_documents=(
            ContextDocument(
                source=Path("README.md"),
                content="Agent Workbench documentation.",
            ),
        ),
    )

    assert provider.complete(request) == "context received"
    assert captured_arguments == {
        "model": "test-model",
        "messages": [
            {
                "role": "system",
                "content": (
                    f"{CONTEXT_DOCUMENTS_HEADER}\n\n"
                    '<context_document source="README.md">\n'
                    "Agent Workbench documentation.\n"
                    "</context_document>"
                ),
            },
            {
                "role": "user",
                "content": "Summarize the project.",
            },
        ],
        "stream": False,
    }
