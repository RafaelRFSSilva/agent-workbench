"""Tests for provider-independent chat request types."""

from pathlib import Path

from agent_workbench.context import ContextDocument
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
