"""Tests for provider-independent chat request types."""

from pathlib import Path

from agent_workbench.context import ContextDocument
from agent_workbench.generation import GenerationConfig
from agent_workbench.messages import ChatRequest
from agent_workbench.structured_outputs import JSONResponseFormat
from agent_workbench.tools import ToolDefinition


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
