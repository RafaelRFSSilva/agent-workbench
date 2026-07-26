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
from agent_workbench.messages import ChatRequest, ChatResponse
from agent_workbench.providers.ollama import OllamaProvider
from agent_workbench.generation import GenerationConfig
from agent_workbench.structured_outputs import JSONResponseFormat
from agent_workbench.tools import ToolDefinition, ToolInvocation


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

    assert provider.complete(request) == ChatResponse(
        text="provider working",
    )
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

    assert provider.complete(request) == ChatResponse(
        text="context received",
    )
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


def test_generation_config_is_translated_to_ollama_options(
    monkeypatch,
) -> None:
    """Translate shared generation settings into Ollama options."""

    captured_arguments = {}

    def fake_chat(**kwargs):
        captured_arguments.update(kwargs)

        return SimpleNamespace(
            message=SimpleNamespace(
                content="configured response",
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
                "content": "Generate a short response.",
            }
        ],
        generation_config=GenerationConfig(
            temperature=0.2,
            top_p=0.8,
            max_output_tokens=256,
        ),
    )

    assert provider.complete(request) == ChatResponse(
        text="configured response",
    )
    assert captured_arguments == {
        "model": "test-model",
        "messages": [
            {
                "role": "user",
                "content": "Generate a short response.",
            }
        ],
        "options": {
            "temperature": 0.2,
            "top_p": 0.8,
            "num_predict": 256,
        },
        "stream": False,
    }


def test_response_format_is_translated_to_ollama_schema(
    monkeypatch,
) -> None:
    """Translate the shared response format into Ollama's format argument."""

    captured_arguments = {}

    def fake_chat(**kwargs):
        captured_arguments.update(kwargs)

        return SimpleNamespace(
            message=SimpleNamespace(
                content='{"summary":"No critical issues.","risk_level":"low"}',
            )
        )

    monkeypatch.setattr(
        "agent_workbench.providers.ollama.chat",
        fake_chat,
    )

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

    provider = OllamaProvider(model_name="test-model")
    request = ChatRequest(
        messages=[
            {
                "role": "user",
                "content": "Review this implementation.",
            }
        ],
        response_format=JSONResponseFormat(
            name="software_review",
            schema=schema,
        ),
    )

    assert provider.complete(request) == ChatResponse(
        text='{"summary":"No critical issues.","risk_level":"low"}',
    )
    assert captured_arguments == {
        "model": "test-model",
        "messages": [
            {
                "role": "user",
                "content": "Review this implementation.",
            }
        ],
        "stream": False,
        "format": schema,
    }


def test_tools_are_translated_to_ollama_functions(
    monkeypatch,
) -> None:
    """Translate shared tool definitions into Ollama functions."""

    captured_arguments = {}

    def fake_chat(**kwargs):
        captured_arguments.update(kwargs)

        return SimpleNamespace(
            message=SimpleNamespace(
                content="",
                tool_calls=None,
            )
        )

    monkeypatch.setattr(
        "agent_workbench.providers.ollama.chat",
        fake_chat,
    )

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

    provider = OllamaProvider(model_name="test-model")
    request = ChatRequest(
        messages=[
            {
                "role": "user",
                "content": "Calculate two plus two.",
            }
        ],
        tools=(calculator,),
    )

    assert provider.complete(request) == ChatResponse()
    assert captured_arguments == {
        "model": "test-model",
        "messages": [
            {
                "role": "user",
                "content": "Calculate two plus two.",
            }
        ],
        "stream": False,
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "calculator",
                    "description": "Evaluate a mathematical expression.",
                    "parameters": {
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
                },
            }
        ],
    }


def test_tool_calls_are_translated_to_tool_invocations(
    monkeypatch,
) -> None:
    """Translate Ollama tool calls into shared tool invocations."""

    def fake_chat(**kwargs):
        return SimpleNamespace(
            message=SimpleNamespace(
                content="Calling the requested tools.",
                tool_calls=[
                    SimpleNamespace(
                        function=SimpleNamespace(
                            name="calculator",
                            arguments={
                                "expression": "2 + 2",
                            },
                        )
                    ),
                    SimpleNamespace(
                        function=SimpleNamespace(
                            name="project_information",
                            arguments={},
                        )
                    ),
                ],
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
                "content": "Calculate two plus two and describe the project.",
            }
        ],
    )

    assert provider.complete(request) == ChatResponse(
        text="Calling the requested tools.",
        tool_invocations=(
            ToolInvocation(
                id="ollama-tool-call-1",
                tool_name="calculator",
                arguments={
                    "expression": "2 + 2",
                },
            ),
            ToolInvocation(
                id="ollama-tool-call-2",
                tool_name="project_information",
                arguments={},
            ),
        ),
    )
