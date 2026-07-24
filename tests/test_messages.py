"""Tests for provider-independent chat request types."""

from pathlib import Path

from agent_workbench.context import ContextDocument
from agent_workbench.generation import GenerationConfig
from agent_workbench.messages import ChatRequest


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
