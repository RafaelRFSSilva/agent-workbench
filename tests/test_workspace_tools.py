"""Tests for safe read-only workspace tools."""

import os
from pathlib import Path

import pytest

from agent_workbench.errors import WorkspacePathError
from agent_workbench.tool_registry import ToolRegistry
from agent_workbench.tools import ToolDefinition, ToolInvocation
from agent_workbench.workspace import Workspace
from agent_workbench.workspace_tools import (
    MAX_DIRECTORY_ENTRIES,
    MAX_FILE_SIZE_BYTES,
    list_workspace_files,
    read_workspace_file,
    register_workspace_tools,
)


def create_workspace(tmp_path: Path) -> tuple[Path, Workspace]:
    """Create an empty workspace and its resolver."""

    root = tmp_path / "workspace"
    root.mkdir()

    return root, Workspace(root)


def create_existing_definition() -> ToolDefinition:
    """Create a definition that precedes workspace tools in a registry."""

    return ToolDefinition(
        name="existing",
        description="Return an existing value.",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    )


def test_registers_workspace_tools_in_order_with_exact_schemas(
    tmp_path: Path,
) -> None:
    """Register list_files before read_file with their portable schemas."""

    _, workspace = create_workspace(tmp_path)
    registry = ToolRegistry()

    register_workspace_tools(registry, workspace)

    assert tuple(definition.name for definition in registry.definitions) == (
        "list_files",
        "read_file",
    )
    assert tuple(definition.description for definition in registry.definitions) == (
        "List the direct entries of a directory inside the authorized workspace.",
        "Read a UTF-8 text file inside the authorized workspace.",
    )
    assert tuple(definition.input_schema for definition in registry.definitions) == (
        {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    )


def test_preserves_existing_registry_tools(tmp_path: Path) -> None:
    """Append workspace tools without changing existing registrations."""

    _, workspace = create_workspace(tmp_path)
    existing_definition = create_existing_definition()
    registry = ToolRegistry()
    registry.register(existing_definition, lambda arguments: {"value": "existing"})

    register_workspace_tools(registry, workspace)

    assert registry.definitions[0] == existing_definition
    assert tuple(definition.name for definition in registry.definitions) == (
        "existing",
        "list_files",
        "read_file",
    )


def test_lists_root_entries_with_deterministic_order_and_hidden_entries(
    tmp_path: Path,
) -> None:
    """List direct root entries by name, including hidden entries."""

    root, workspace = create_workspace(tmp_path)
    (root / "zeta.txt").write_text("zeta", encoding="utf-8")
    (root / ".hidden").write_text("hidden", encoding="utf-8")
    (root / "alpha").mkdir()
    target = root / "target.txt"
    target.write_text("target", encoding="utf-8")
    (root / "target-link.txt").symlink_to(target)

    result = list_workspace_files(workspace, {"path": "."})

    assert result == {
        "path": ".",
        "entries": [
            {
                "name": ".hidden",
                "path": ".hidden",
                "type": "file",
            },
            {
                "name": "alpha",
                "path": "alpha",
                "type": "directory",
            },
            {
                "name": "target-link.txt",
                "path": "target-link.txt",
                "type": "symlink",
            },
            {
                "name": "target.txt",
                "path": "target.txt",
                "type": "file",
            },
            {
                "name": "zeta.txt",
                "path": "zeta.txt",
                "type": "file",
            },
        ],
    }


def test_lists_a_nested_directory_without_recursing(tmp_path: Path) -> None:
    """List only the requested directory's direct children."""

    root, workspace = create_workspace(tmp_path)
    nested_directory = root / "nested"
    nested_directory.mkdir()
    (nested_directory / "child.txt").write_text("child", encoding="utf-8")
    deep_directory = nested_directory / "deep"
    deep_directory.mkdir()
    (deep_directory / "grandchild.txt").write_text("grandchild", encoding="utf-8")

    result = list_workspace_files(workspace, {"path": "nested"})

    assert result == {
        "path": "nested",
        "entries": [
            {
                "name": "child.txt",
                "path": "nested/child.txt",
                "type": "file",
            },
            {
                "name": "deep",
                "path": "nested/deep",
                "type": "directory",
            },
        ],
    }


def test_classifies_other_entries_when_supported(tmp_path: Path) -> None:
    """Classify non-file, non-directory, non-symlink entries as other."""

    if not hasattr(os, "mkfifo"):
        pytest.skip("named pipes are not supported on this platform")

    root, workspace = create_workspace(tmp_path)
    fifo_path = root / "events.pipe"
    os.mkfifo(fifo_path)

    result = list_workspace_files(workspace, {"path": "."})

    assert result["entries"] == [
        {
            "name": "events.pipe",
            "path": "events.pipe",
            "type": "other",
        }
    ]


def test_rejects_listing_a_file(tmp_path: Path) -> None:
    """Require list_files paths to resolve to directories."""

    root, workspace = create_workspace(tmp_path)
    (root / "notes.txt").write_text("notes", encoding="utf-8")

    with pytest.raises(ValueError, match="list_files requires a directory"):
        list_workspace_files(workspace, {"path": "notes.txt"})


def test_rejects_directory_entry_count_above_the_limit(tmp_path: Path) -> None:
    """Reject directories whose direct entry count exceeds the cap."""

    root, workspace = create_workspace(tmp_path)

    for index in range(MAX_DIRECTORY_ENTRIES + 1):
        (root / f"entry-{index:03d}.txt").write_text("entry", encoding="utf-8")

    with pytest.raises(ValueError, match="directory contains too many entries"):
        list_workspace_files(workspace, {"path": "."})


def test_reads_valid_utf8_with_canonical_relative_path(tmp_path: Path) -> None:
    """Return valid UTF-8 content and its canonical workspace-relative path."""

    root, workspace = create_workspace(tmp_path)
    notes_path = root / "docs" / "notes.txt"
    notes_path.parent.mkdir()
    notes_path.write_text("Olá\n", encoding="utf-8")

    result = read_workspace_file(workspace, {"path": "docs/./notes.txt"})

    assert result == {
        "path": "docs/notes.txt",
        "content": "Olá\n",
        "size_bytes": len("Olá\n".encode("utf-8")),
    }


def test_reads_through_an_allowed_internal_symlink(tmp_path: Path) -> None:
    """Read an internal symlink through its canonical target path."""

    root, workspace = create_workspace(tmp_path)
    target = root / "data" / "notes.txt"
    target.parent.mkdir()
    target.write_text("notes", encoding="utf-8")
    (root / "notes-link.txt").symlink_to(target)

    result = read_workspace_file(workspace, {"path": "notes-link.txt"})

    assert result == {
        "path": "data/notes.txt",
        "content": "notes",
        "size_bytes": 5,
    }


def test_rejects_reading_a_directory(tmp_path: Path) -> None:
    """Require read_file paths to resolve to regular files."""

    root, workspace = create_workspace(tmp_path)
    (root / "docs").mkdir()

    with pytest.raises(ValueError, match="read_file requires a regular file"):
        read_workspace_file(workspace, {"path": "docs"})


@pytest.mark.parametrize(
    "tool",
    [
        list_workspace_files,
        read_workspace_file,
    ],
)
def test_delegates_missing_and_traversal_paths_to_workspace(
    tmp_path: Path,
    tool,
) -> None:
    """Preserve Workspace missing-path and traversal rejection behavior."""

    root, workspace = create_workspace(tmp_path)
    external_path = tmp_path / "external.txt"
    external_path.write_text("external", encoding="utf-8")
    (root / "directory").mkdir()

    with pytest.raises(WorkspacePathError, match="does not exist"):
        tool(workspace, {"path": "missing"})

    with pytest.raises(WorkspacePathError, match="resolves outside the workspace"):
        tool(workspace, {"path": "../external.txt"})


@pytest.mark.parametrize(
    "tool",
    [
        list_workspace_files,
        read_workspace_file,
    ],
)
def test_delegates_external_symlink_rejection_to_workspace(
    tmp_path: Path,
    tool,
) -> None:
    """Preserve Workspace rejection of symlinks targeting outside the root."""

    root, workspace = create_workspace(tmp_path)
    external_path = tmp_path / "external.txt"
    external_path.write_text("external", encoding="utf-8")
    (root / "external-link").symlink_to(external_path)

    with pytest.raises(WorkspacePathError, match="resolves outside the workspace"):
        tool(workspace, {"path": "external-link"})


def test_rejects_invalid_utf8(tmp_path: Path) -> None:
    """Reject text files that are not valid UTF-8."""

    root, workspace = create_workspace(tmp_path)
    (root / "invalid.txt").write_bytes(b"\xff\xfe")

    with pytest.raises(ValueError, match="read_file requires valid UTF-8"):
        read_workspace_file(workspace, {"path": "invalid.txt"})


def test_reads_a_file_at_the_size_limit(tmp_path: Path) -> None:
    """Accept a UTF-8 file whose byte size equals the read limit."""

    root, workspace = create_workspace(tmp_path)
    (root / "maximum.txt").write_bytes(b"a" * MAX_FILE_SIZE_BYTES)

    result = read_workspace_file(workspace, {"path": "maximum.txt"})

    assert result["size_bytes"] == MAX_FILE_SIZE_BYTES
    assert result["content"] == "a" * MAX_FILE_SIZE_BYTES


def test_rejects_a_file_larger_than_the_size_limit(tmp_path: Path) -> None:
    """Reject files larger than the read limit before returning content."""

    root, workspace = create_workspace(tmp_path)
    (root / "large.txt").write_bytes(b"a" * (MAX_FILE_SIZE_BYTES + 1))

    with pytest.raises(ValueError, match="file exceeds the"):
        read_workspace_file(workspace, {"path": "large.txt"})


def test_registry_handler_returns_strict_workspace_tool_output(tmp_path: Path) -> None:
    """Execute the registered read_file handler through ToolRegistry."""

    root, workspace = create_workspace(tmp_path)
    (root / "notes.txt").write_text("notes", encoding="utf-8")
    registry = ToolRegistry()
    register_workspace_tools(registry, workspace)

    result = registry.execute(
        ToolInvocation(
            id="read-1",
            tool_name="read_file",
            arguments={"path": "notes.txt"},
        )
    )

    assert result.status == "success"
    assert result.output == {
        "path": "notes.txt",
        "content": "notes",
        "size_bytes": 5,
    }


def test_handlers_do_not_mutate_arguments_or_returned_data(tmp_path: Path) -> None:
    """Keep input arguments and independently returned data unchanged."""

    root, workspace = create_workspace(tmp_path)
    (root / "notes.txt").write_text("notes", encoding="utf-8")
    arguments = {"path": "."}
    original_arguments = {"path": "."}

    first_result = list_workspace_files(workspace, arguments)
    first_result["entries"].clear()
    second_result = list_workspace_files(workspace, arguments)

    assert arguments == original_arguments
    assert second_result == {
        "path": ".",
        "entries": [
            {
                "name": "notes.txt",
                "path": "notes.txt",
                "type": "file",
            }
        ],
    }
