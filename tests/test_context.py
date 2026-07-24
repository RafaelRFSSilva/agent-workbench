"""Tests for context document loading and validation."""

from pathlib import Path

import pytest

from agent_workbench.context import (
    CONTEXT_DOCUMENTS_HEADER,
    MAX_CONTEXT_FILE_SIZE_BYTES,
    SUPPORTED_CONTEXT_FILE_SUFFIXES,
    ContextDocument,
    build_system_instructions,
    format_context_documents,
    load_context_document,
)
from agent_workbench.errors import ConfigurationError


@pytest.mark.parametrize(
    "suffix",
    sorted(SUPPORTED_CONTEXT_FILE_SUFFIXES),
)
def test_load_context_document_accepts_supported_extensions(
    tmp_path,
    suffix,
) -> None:
    """Load text files using every supported extension."""

    context_path = tmp_path / f"context{suffix}"
    context_path.write_text(
        "Useful project context.",
        encoding="utf-8",
    )

    document = load_context_document(context_path)

    assert document.source == context_path
    assert document.content == "Useful project context."


def test_load_context_document_preserves_original_content(
    tmp_path,
) -> None:
    """Preserve whitespace and line breaks from the source document."""

    context_path = tmp_path / "notes.md"
    original_content = "  First line.\nSecond line.\n"
    context_path.write_text(
        original_content,
        encoding="utf-8",
    )

    document = load_context_document(context_path)

    assert document.content == original_content


def test_load_context_document_accepts_uppercase_extension(
    tmp_path,
) -> None:
    """Treat supported file extensions case-insensitively."""

    context_path = tmp_path / "README.MD"
    context_path.write_text(
        "Project documentation.",
        encoding="utf-8",
    )

    document = load_context_document(context_path)

    assert document.source == context_path


def test_load_context_document_rejects_missing_file(
    tmp_path,
) -> None:
    """Reject a context path that does not exist."""

    context_path = tmp_path / "missing.md"

    with pytest.raises(
        ConfigurationError,
        match="Context file does not exist",
    ):
        load_context_document(context_path)


def test_load_context_document_rejects_directory(
    tmp_path,
) -> None:
    """Reject directories used as context files."""

    context_path = tmp_path / "context.md"
    context_path.mkdir()

    with pytest.raises(
        ConfigurationError,
        match="Context path is not a file",
    ):
        load_context_document(context_path)


def test_load_context_document_rejects_unsupported_extension(
    tmp_path,
) -> None:
    """Reject files outside the supported text formats."""

    context_path = tmp_path / "document.pdf"
    context_path.write_text(
        "Unsupported content.",
        encoding="utf-8",
    )

    with pytest.raises(
        ConfigurationError,
        match="Unsupported context file extension",
    ):
        load_context_document(context_path)


def test_load_context_document_rejects_invalid_utf8(
    tmp_path,
) -> None:
    """Reject context files that cannot be decoded as UTF-8."""

    context_path = tmp_path / "invalid.txt"
    context_path.write_bytes(b"\xff\xfe\x00")

    with pytest.raises(
        ConfigurationError,
        match="Context file is not valid UTF-8",
    ):
        load_context_document(context_path)


def test_load_context_document_rejects_blank_content(
    tmp_path,
) -> None:
    """Reject context files containing only whitespace."""

    context_path = tmp_path / "blank.txt"
    context_path.write_text(
        " \n\t",
        encoding="utf-8",
    )

    with pytest.raises(
        ConfigurationError,
        match="Context file is empty",
    ):
        load_context_document(context_path)


def test_load_context_document_rejects_oversized_file(
    tmp_path,
) -> None:
    """Reject context files larger than the configured limit."""

    context_path = tmp_path / "large.txt"
    context_path.write_bytes(b"a" * (MAX_CONTEXT_FILE_SIZE_BYTES + 1))

    with pytest.raises(
        ConfigurationError,
        match="Context file exceeds",
    ):
        load_context_document(context_path)


def test_load_context_document_accepts_file_at_size_limit(
    tmp_path,
) -> None:
    """Accept a context file exactly at the configured size limit."""

    context_path = tmp_path / "maximum.txt"
    context_path.write_bytes(b"a" * MAX_CONTEXT_FILE_SIZE_BYTES)

    document = load_context_document(context_path)

    assert len(document.content.encode("utf-8")) == MAX_CONTEXT_FILE_SIZE_BYTES


def test_format_context_documents_returns_none_without_documents() -> None:
    """Return no context block when no documents were supplied."""

    assert format_context_documents(()) is None


def test_format_context_documents_preserves_order_and_sources() -> None:
    """Render context documents in order with their source paths."""

    documents = (
        ContextDocument(
            source=Path("README.md"),
            content="First document.",
        ),
        ContextDocument(
            source=Path("pyproject.toml"),
            content="Second document.",
        ),
    )

    formatted_context = format_context_documents(documents)

    assert formatted_context == (
        f"{CONTEXT_DOCUMENTS_HEADER}\n\n"
        '<context_document source="README.md">\n'
        "First document.\n"
        "</context_document>\n\n"
        '<context_document source="pyproject.toml">\n'
        "Second document.\n"
        "</context_document>"
    )


def test_format_context_documents_escapes_source_path() -> None:
    """Escape characters that could break the source attribute."""

    documents = (
        ContextDocument(
            source=Path('docs/research & "notes".md'),
            content="Reference material.",
        ),
    )

    formatted_context = format_context_documents(documents)

    assert formatted_context is not None
    assert 'source="docs/research &amp; &quot;notes&quot;.md"' in formatted_context


def test_build_system_instructions_combines_prompt_and_context() -> None:
    """Place context after the active system prompt."""

    documents = (
        ContextDocument(
            source=Path("README.md"),
            content="Project documentation.",
        ),
    )

    formatted_context = format_context_documents(documents)
    instructions = build_system_instructions(
        "You are a strict software reviewer.",
        documents,
    )

    assert formatted_context is not None
    assert instructions == (
        f"You are a strict software reviewer.\n\n{formatted_context}"
    )


def test_build_system_instructions_supports_context_without_prompt() -> None:
    """Use context as system instructions when no prompt was supplied."""

    documents = (
        ContextDocument(
            source=Path("README.md"),
            content="Project documentation.",
        ),
    )

    assert build_system_instructions(
        None,
        documents,
    ) == format_context_documents(documents)


def test_build_system_instructions_preserves_prompt_without_context() -> None:
    """Leave the system prompt unchanged when context is absent."""

    assert (
        build_system_instructions(
            "You are a strict software reviewer.",
            (),
        )
        == "You are a strict software reviewer."
    )
