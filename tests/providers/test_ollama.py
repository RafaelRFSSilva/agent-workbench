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
from agent_workbench.messages import (
    ChatRequest,
    ChatResponse,
    ToolInteractionRound,
)
from agent_workbench.providers.ollama import OllamaProvider
from agent_workbench.generation import GenerationConfig
from agent_workbench.structured_outputs import JSONResponseFormat
from agent_workbench.tools import ToolDefinition, ToolInvocation, ToolResult
from agent_workbench.workspace_actions import (
    APPLY_FILE_PATCH_DEFINITION,
    APPLY_WORKSPACE_CHANGES_DEFINITION,
)


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


def test_malformed_tool_call_error_is_retried_once_with_fresh_user_guidance(
    monkeypatch,
) -> None:
    """Retry malformed tool JSON once with new guidance and unchanged options."""

    calls = []
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

    def fake_chat(**kwargs):
        calls.append(kwargs)

        if len(calls) == 1:
            raise ResponseError(
                'error parsing tool call: raw=\'{"expression":"2 + 2"}}\'',
                500,
            )

        return SimpleNamespace(
            message=SimpleNamespace(
                content="Recovered response.",
                tool_calls=None,
            )
        )

    monkeypatch.setattr("agent_workbench.providers.ollama.chat", fake_chat)

    provider = OllamaProvider(model_name="test-model")
    request = ChatRequest(
        system_prompt="Use tools carefully.",
        messages=[
            {
                "role": "user",
                "content": "Calculate two plus two.",
            }
        ],
        generation_config=GenerationConfig(
            temperature=0.0,
            top_p=1.0,
            max_output_tokens=256,
        ),
        tools=(calculator,),
    )

    assert provider.complete(request) == ChatResponse(
        text="Recovered response.",
        response_repair_attempt_count=1,
    )
    assert len(calls) == 2
    assert calls[0]["messages"] == [
        {
            "role": "system",
            "content": "Use tools carefully.",
        },
        {
            "role": "user",
            "content": "Calculate two plus two.",
        },
    ]
    assert calls[1]["messages"][:-1] == calls[0]["messages"]
    assert calls[1]["messages"][-1] == {
        "role": "user",
        "content": (
            "The previous response was rejected because at least one tool call "
            "used malformed JSON. Retry the same task now. Generate fresh "
            "arguments instead of quoting, reusing, or repairing the rejected "
            "arguments. Every tool call must contain exactly one valid JSON "
            "object matching the supplied schema, with all required arguments "
            "present."
        ),
    }
    assert calls[1]["options"] == calls[0]["options"]
    assert calls[1]["tools"] == calls[0]["tools"]
    assert calls[0]["messages"] == [
        {
            "role": "system",
            "content": "Use tools carefully.",
        },
        {
            "role": "user",
            "content": "Calculate two plus two.",
        },
    ]


def test_repeated_malformed_tool_call_errors_stop_after_one_retry(
    monkeypatch,
) -> None:
    """Stop safely after one corrective retry of malformed tool-call JSON."""

    calls = []

    def fake_chat(**kwargs):
        calls.append(kwargs)
        raise ResponseError(
            'error parsing tool call: raw=\'{"path":"tests"}}\'',
            500,
        )

    monkeypatch.setattr("agent_workbench.providers.ollama.chat", fake_chat)

    provider = OllamaProvider(model_name="test-model")

    with pytest.raises(
        CompletionError,
        match=(
            "after 2 attempts because the model repeatedly generated "
            "malformed tool-call JSON"
        ),
    ) as error:
        provider.complete(
            ChatRequest(
                messages=[
                    {
                        "role": "user",
                        "content": "Inspect the tests.",
                    }
                ],
            )
        )

    assert len(calls) == 2
    assert calls[0]["messages"] == [
        {
            "role": "user",
            "content": "Inspect the tests.",
        }
    ]
    assert calls[1]["messages"][:-1] == calls[0]["messages"]
    assert calls[1]["messages"][-1]["role"] == "user"
    assert "malformed JSON" in calls[1]["messages"][-1]["content"]
    assert "raw=" not in calls[1]["messages"][-1]["content"]
    assert "raw=" not in str(error.value)


def test_non_tool_call_response_error_is_not_retried(monkeypatch) -> None:
    """Preserve immediate failure for unrelated Ollama response errors."""

    calls = []

    def fake_chat(**kwargs):
        calls.append(kwargs)
        raise ResponseError("internal server failure", 500)

    monkeypatch.setattr("agent_workbench.providers.ollama.chat", fake_chat)

    provider = OllamaProvider(model_name="test-model")

    with pytest.raises(
        CompletionError,
        match="Ollama request failed: internal server failure",
    ):
        provider.complete(ChatRequest(messages=[]))

    assert len(calls) == 1


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


def test_atomic_workspace_schema_is_delivered_to_ollama_unchanged(
    monkeypatch,
) -> None:
    """Preserve the exact closed nested transaction schema and guidance."""

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
    provider = OllamaProvider(model_name="test-model")

    assert (
        provider.complete(
            ChatRequest(
                messages=[{"role": "user", "content": "Change both files."}],
                tools=(APPLY_WORKSPACE_CHANGES_DEFINITION,),
            )
        )
        == ChatResponse()
    )

    function = captured_arguments["tools"][0]["function"]
    assert function["name"] == "apply_workspace_changes"
    assert function["description"] == (
        "Apply one approved transactional set of UTF-8 file creations and "
        "updates inside the authorized workspace. Each changes array element "
        "must contain path, expected_content, replacement_content, and optional "
        "create_if_missing. Successful changes include resulting_file_sha256."
    )
    assert function["parameters"] == APPLY_WORKSPACE_CHANGES_DEFINITION.input_schema
    assert function["parameters"]["required"] == ["changes"]
    changes = function["parameters"]["properties"]["changes"]
    assert changes["type"] == "array"
    assert changes["items"]["required"] == [
        "path",
        "expected_content",
        "replacement_content",
    ]
    assert changes["items"]["additionalProperties"] is False


def test_patch_schema_is_delivered_to_ollama_unchanged(monkeypatch) -> None:
    """Advertise the exact closed patch shape enforced by the generic runtime."""

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

    assert (
        OllamaProvider(model_name="test-model").complete(
            ChatRequest(
                messages=[{"role": "user", "content": "Update one file."}],
                tools=(APPLY_FILE_PATCH_DEFINITION,),
            )
        )
        == ChatResponse()
    )

    function = captured_arguments["tools"][0]["function"]
    assert function["name"] == "apply_file_patch"
    assert function["parameters"] == APPLY_FILE_PATCH_DEFINITION.input_schema
    assert function["parameters"]["required"] == [
        "path",
        "expected_content",
        "replacement_content",
    ]
    assert function["parameters"]["additionalProperties"] is False


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


def test_tool_interactions_are_translated_to_ollama_messages(
    monkeypatch,
) -> None:
    """Translate ordered shared tool interactions into Ollama messages."""

    captured_arguments = {}

    def fake_chat(**kwargs):
        captured_arguments.update(kwargs)

        return SimpleNamespace(
            message=SimpleNamespace(
                content="Tool interactions processed",
                tool_calls=None,
            )
        )

    monkeypatch.setattr(
        "agent_workbench.providers.ollama.chat",
        fake_chat,
    )

    first_result = ToolResult(
        invocation_id="provider-call-1",
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
                    id="provider-call-1",
                    tool_name="calculator",
                    arguments={
                        "expression": "2 + 2",
                    },
                ),
                ToolInvocation(
                    id="provider-call-2",
                    tool_name="project_information",
                    arguments={},
                ),
            ),
        ),
        results=(
            first_result,
            ToolResult(
                invocation_id="provider-call-2",
                status="error",
                error="Project information is unavailable.",
            ),
        ),
    )
    second_round = ToolInteractionRound(
        response=ChatResponse(
            tool_invocations=(
                ToolInvocation(
                    id="provider-call-3",
                    tool_name="identity",
                    arguments={},
                ),
                ToolInvocation(
                    id="provider-call-4",
                    tool_name="increment",
                    arguments={
                        "left": 2,
                        "right": 3,
                    },
                ),
                ToolInvocation(
                    id="provider-call-5",
                    tool_name="list_files",
                    arguments={},
                ),
            ),
        ),
        results=(
            ToolResult(
                invocation_id="provider-call-3",
                status="success",
            ),
            ToolResult(
                invocation_id="provider-call-4",
                status="success",
                output=5,
            ),
            ToolResult(
                invocation_id="provider-call-5",
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

    provider = OllamaProvider(model_name="test-model")
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
    assert captured_arguments == {
        "model": "test-model",
        "messages": [
            *messages,
            {
                "role": "assistant",
                "content": "I will calculate and inspect the project.",
                "tool_calls": [
                    {
                        "function": {
                            "name": "calculator",
                            "arguments": {
                                "expression": "2 + 2",
                            },
                        },
                    },
                    {
                        "function": {
                            "name": "project_information",
                            "arguments": {},
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "content": '{"output":{"value":4},"status":"success"}',
                "tool_name": "calculator",
            },
            {
                "role": "tool",
                "content": (
                    '{"error":"Project information is unavailable.","status":"error"}'
                ),
                "tool_name": "project_information",
            },
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "identity",
                            "arguments": {},
                        },
                    },
                    {
                        "function": {
                            "name": "increment",
                            "arguments": {
                                "left": 2,
                                "right": 3,
                            },
                        },
                    },
                    {
                        "function": {
                            "name": "list_files",
                            "arguments": {},
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "content": '{"output":null,"status":"success"}',
                "tool_name": "identity",
            },
            {
                "role": "tool",
                "content": '{"output":5,"status":"success"}',
                "tool_name": "increment",
            },
            {
                "role": "tool",
                "content": ('{"output":["README.md",{"count":2}],"status":"success"}'),
                "tool_name": "list_files",
            },
        ],
        "stream": False,
    }
