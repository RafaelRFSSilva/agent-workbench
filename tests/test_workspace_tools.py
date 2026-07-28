"""Tests for safe read-only workspace tools."""

import os
from pathlib import Path

import pytest

from agent_workbench.errors import ToolArgumentError, WorkspacePathError
from agent_workbench.tool_registry import ToolRegistry
from agent_workbench.tools import ToolDefinition, ToolInvocation
from agent_workbench.workspace import Workspace
from agent_workbench.workspace_tools import (
    MAX_DIRECTORY_ENTRIES,
    MAX_FILE_SIZE_BYTES,
    MAX_READ_LINES,
    MAX_SEARCH_FILE_BYTES,
    MAX_SEARCH_FILES,
    MAX_SEARCH_LINE_LENGTH,
    MAX_SEARCH_MATCHES,
    MAX_SEARCH_QUERY_LENGTH,
    list_workspace_files,
    read_workspace_file,
    register_workspace_tools,
    search_workspace_text,
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
    """Register workspace tools in deterministic order with portable schemas."""

    _, workspace = create_workspace(tmp_path)
    registry = ToolRegistry()

    register_workspace_tools(registry, workspace)

    assert tuple(definition.name for definition in registry.definitions) == (
        "list_files",
        "read_file",
        "search_text",
    )
    assert tuple(definition.description for definition in registry.definitions) == (
        "List the direct entries of a directory inside the authorized workspace.",
        (
            "Read all or an inclusive bounded line range from a UTF-8 text file "
            "inside the authorized workspace."
        ),
        "Search UTF-8 text files inside the authorized workspace.",
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
                },
                "line_start": {
                    "type": "integer",
                    "minimum": 1,
                },
                "line_end": {
                    "type": "integer",
                    "minimum": 1,
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                },
                "path": {
                    "type": "string",
                },
                "case_sensitive": {
                    "type": "boolean",
                },
            },
            "required": ["query"],
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
        "search_text",
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

    with pytest.raises(ValueError, match="list_files path must reference a directory"):
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


def test_reads_an_inclusive_line_range_with_metadata(tmp_path: Path) -> None:
    """Return exactly the requested inclusive lines and bounded metadata."""

    root, workspace = create_workspace(tmp_path)
    content = "alpha\nbeta\ngamma\ndelta\n"
    (root / "notes.txt").write_text(content, encoding="utf-8")

    result = read_workspace_file(
        workspace,
        {"path": "notes.txt", "line_start": 2, "line_end": 3},
    )

    assert result == {
        "path": "notes.txt",
        "content": "beta\ngamma\n",
        "size_bytes": len(content.encode("utf-8")),
        "line_start": 2,
        "line_end": 3,
        "total_lines": 4,
        "truncated": True,
    }


def test_partial_read_defaults_to_first_line_and_requested_end(
    tmp_path: Path,
) -> None:
    """Start at line one when only an inclusive end line is supplied."""

    root, workspace = create_workspace(tmp_path)
    content = "alpha\nbeta\ngamma\n"
    (root / "notes.txt").write_text(content, encoding="utf-8")

    result = read_workspace_file(
        workspace,
        {"path": "notes.txt", "line_end": 2},
    )

    assert result["content"] == "alpha\nbeta\n"
    assert result["line_start"] == 1
    assert result["line_end"] == 2
    assert result["total_lines"] == 3
    assert result["truncated"] is True


def test_partial_read_caps_an_open_ended_range(tmp_path: Path) -> None:
    """Return at most MAX_READ_LINES when no explicit end line is supplied."""

    root, workspace = create_workspace(tmp_path)
    lines = [f"line-{index}\n" for index in range(1, MAX_READ_LINES + 3)]
    content = "".join(lines)
    (root / "notes.txt").write_text(content, encoding="utf-8")

    result = read_workspace_file(
        workspace,
        {"path": "notes.txt", "line_start": 2},
    )

    assert result["content"] == "".join(lines[1 : MAX_READ_LINES + 1])
    assert result["line_start"] == 2
    assert result["line_end"] == MAX_READ_LINES + 1
    assert result["total_lines"] == MAX_READ_LINES + 2
    assert result["truncated"] is True


def test_rejects_partial_read_start_past_the_file_end(
    tmp_path: Path,
) -> None:
    """Reject a partial range whose first line is beyond the file."""

    root, workspace = create_workspace(tmp_path)
    (root / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8")

    with pytest.raises(ValueError, match="exceeds the total file line count"):
        read_workspace_file(
            workspace,
            {"path": "notes.txt", "line_start": 5, "line_end": 6},
        )


@pytest.mark.parametrize(
    "arguments, message",
    [
        (
            {"path": "notes.txt", "unknown": 1},
            "accepts only path, line_start, and line_end",
        ),
        ({"path": "notes.txt", "line_start": 0}, "line_start"),
        ({"path": "notes.txt", "line_start": -1}, "line_start"),
        ({"path": "notes.txt", "line_start": True}, "line_start"),
        ({"path": "notes.txt", "line_start": "1"}, "line_start"),
        ({"path": "notes.txt", "line_end": 0}, "line_end"),
        (
            {"path": "notes.txt", "line_start": 3, "line_end": 2},
            "must not be before",
        ),
    ],
)
def test_rejects_invalid_partial_read_arguments(
    tmp_path: Path,
    arguments: dict[str, object],
    message: str,
) -> None:
    """Reject unknown, non-integer, non-positive, and reversed ranges."""

    root, workspace = create_workspace(tmp_path)
    (root / "notes.txt").write_text("notes\n", encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        read_workspace_file(workspace, arguments)


def test_rejects_partial_read_ranges_above_the_line_limit(
    tmp_path: Path,
) -> None:
    """Reject explicit ranges whose inclusive span exceeds the limit."""

    root, workspace = create_workspace(tmp_path)
    (root / "notes.txt").write_text("notes\n", encoding="utf-8")

    with pytest.raises(ValueError, match="ranges must not exceed"):
        read_workspace_file(
            workspace,
            {
                "path": "notes.txt",
                "line_start": 1,
                "line_end": MAX_READ_LINES + 1,
            },
        )


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

    with pytest.raises(
        ValueError, match="read_file path must reference a regular file"
    ):
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


def test_registry_handler_returns_partial_read_metadata(tmp_path: Path) -> None:
    """Execute a partial read_file request through ToolRegistry."""

    root, workspace = create_workspace(tmp_path)
    (root / "notes.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    registry = ToolRegistry()
    register_workspace_tools(registry, workspace)

    result = registry.execute(
        ToolInvocation(
            id="read-partial-1",
            tool_name="read_file",
            arguments={
                "path": "notes.txt",
                "line_start": 2,
                "line_end": 2,
            },
        )
    )

    assert result.status == "success"
    assert result.output == {
        "path": "notes.txt",
        "content": "beta\n",
        "size_bytes": len("alpha\nbeta\ngamma\n".encode("utf-8")),
        "line_start": 2,
        "line_end": 2,
        "total_lines": 3,
        "truncated": True,
    }


def test_registry_returns_recoverable_list_files_argument_error(
    tmp_path: Path,
) -> None:
    """Return a safe correctable error for invalid list_files arguments."""

    _, workspace = create_workspace(tmp_path)
    registry = ToolRegistry()
    register_workspace_tools(registry, workspace)

    result = registry.execute(
        ToolInvocation(
            id="list-invalid-1",
            tool_name="list_files",
            arguments={"path": ".", "depth": 1},
        )
    )

    assert result.status == "error"
    assert result.error == (
        "Invalid tool arguments: list_files accepts only one path string argument."
    )


def test_registry_returns_recoverable_read_file_range_error(
    tmp_path: Path,
) -> None:
    """Explain an excessive inclusive line range without internal details."""

    root, workspace = create_workspace(tmp_path)
    (root / "notes.txt").write_text("notes\n", encoding="utf-8")
    registry = ToolRegistry()
    register_workspace_tools(registry, workspace)

    result = registry.execute(
        ToolInvocation(
            id="read-invalid-1",
            tool_name="read_file",
            arguments={
                "path": "notes.txt",
                "line_start": 1,
                "line_end": MAX_READ_LINES + 1,
            },
        )
    )

    assert result.status == "error"
    assert result.error == (
        "Invalid tool arguments: "
        f"read_file ranges must not exceed {MAX_READ_LINES} lines."
    )


def test_registry_returns_static_workspace_resolution_error(
    tmp_path: Path,
) -> None:
    """Do not expose traversal targets through recoverable workspace errors."""

    _, workspace = create_workspace(tmp_path)
    registry = ToolRegistry()
    register_workspace_tools(registry, workspace)

    result = registry.execute(
        ToolInvocation(
            id="read-outside-1",
            tool_name="read_file",
            arguments={"path": "../private.txt"},
        )
    )

    assert result.status == "error"
    assert result.error == (
        "Invalid tool arguments: workspace path is unavailable "
        "or outside the authorized workspace."
    )
    assert "../private.txt" not in str(result)


def test_workspace_validation_uses_explicit_tool_argument_error(
    tmp_path: Path,
) -> None:
    """Classify only intentionally safe workspace validation failures."""

    root, workspace = create_workspace(tmp_path)
    (root / "notes.txt").write_text("notes\n", encoding="utf-8")

    with pytest.raises(
        ToolArgumentError,
        match="line_start must be a positive integer",
    ):
        read_workspace_file(
            workspace,
            {"path": "notes.txt", "line_start": 0},
        )


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


def test_searches_root_and_nested_files_in_deterministic_path_order(
    tmp_path: Path,
) -> None:
    """Search root and nested text files in workspace-relative path order."""

    root, workspace = create_workspace(tmp_path)
    (root / "zeta.txt").write_text("needle zeta\n", encoding="utf-8")
    nested_path = root / "alpha" / "notes.txt"
    nested_path.parent.mkdir()
    nested_path.write_text("needle alpha\n", encoding="utf-8")

    result = search_workspace_text(workspace, {"query": "needle"})

    assert result == {
        "path": ".",
        "matches": [
            {
                "path": "alpha/notes.txt",
                "line_number": 1,
                "line": "needle alpha",
            },
            {
                "path": "zeta.txt",
                "line_number": 1,
                "line": "needle zeta",
            },
        ],
        "truncated": False,
    }


def test_searches_a_single_file_and_nested_directory(tmp_path: Path) -> None:
    """Accept either a single canonical file or a nested directory."""

    root, workspace = create_workspace(tmp_path)
    file_path = root / "docs" / "notes.txt"
    file_path.parent.mkdir()
    file_path.write_text("first\nneedle\n", encoding="utf-8")

    file_result = search_workspace_text(
        workspace,
        {"query": "needle", "path": "docs/./notes.txt"},
    )
    directory_result = search_workspace_text(
        workspace,
        {"query": "needle", "path": "docs"},
    )

    expected_matches = [
        {
            "path": "docs/notes.txt",
            "line_number": 2,
            "line": "needle",
        }
    ]
    assert file_result == {
        "path": "docs/notes.txt",
        "matches": expected_matches,
        "truncated": False,
    }
    assert directory_result == {
        "path": "docs",
        "matches": expected_matches,
        "truncated": False,
    }


def test_search_includes_hidden_files_and_honors_case_sensitivity(
    tmp_path: Path,
) -> None:
    """Search hidden files with explicit case-sensitive matching control."""

    root, workspace = create_workspace(tmp_path)
    (root / ".hidden.txt").write_text("Needle\n", encoding="utf-8")

    insensitive_result = search_workspace_text(workspace, {"query": "needle"})
    sensitive_result = search_workspace_text(
        workspace,
        {"query": "needle", "case_sensitive": True},
    )

    assert insensitive_result["matches"] == [
        {
            "path": ".hidden.txt",
            "line_number": 1,
            "line": "Needle",
        }
    ]
    assert sensitive_result["matches"] == []


def test_search_returns_one_match_per_matching_line(tmp_path: Path) -> None:
    """Preserve matching line order without duplicating repeated occurrences."""

    root, workspace = create_workspace(tmp_path)
    (root / "notes.txt").write_text(
        "needle needle\nother\nneedle\n",
        encoding="utf-8",
    )

    result = search_workspace_text(workspace, {"query": "needle"})

    assert result["matches"] == [
        {
            "path": "notes.txt",
            "line_number": 1,
            "line": "needle needle",
        },
        {
            "path": "notes.txt",
            "line_number": 3,
            "line": "needle",
        },
    ]


def test_search_rejects_blank_queries_and_invalid_arguments(tmp_path: Path) -> None:
    """Require one non-blank query and only the documented fields."""

    _, workspace = create_workspace(tmp_path)

    with pytest.raises(ValueError, match="requires a non-blank query"):
        search_workspace_text(workspace, {"query": "   "})

    with pytest.raises(ValueError, match="requires valid search arguments"):
        search_workspace_text(workspace, {"query": "needle", "unknown": True})


def test_search_delegates_traversal_and_external_symlink_rejection(
    tmp_path: Path,
) -> None:
    """Keep traversal and external-symlink containment in Workspace."""

    root, workspace = create_workspace(tmp_path)
    external_path = tmp_path / "external.txt"
    external_path.write_text("needle", encoding="utf-8")
    (root / "external-link.txt").symlink_to(external_path)

    with pytest.raises(WorkspacePathError, match="resolves outside the workspace"):
        search_workspace_text(workspace, {"query": "needle", "path": "../external.txt"})

    with pytest.raises(WorkspacePathError, match="resolves outside the workspace"):
        search_workspace_text(
            workspace,
            {"query": "needle", "path": "external-link.txt"},
        )


def test_search_does_not_follow_directory_symlinks_during_recursion(
    tmp_path: Path,
) -> None:
    """Skip directory symlinks encountered while walking a directory."""

    root, workspace = create_workspace(tmp_path)
    target_directory = root / "target"
    target_directory.mkdir()
    (target_directory / "secret.txt").write_text("needle", encoding="utf-8")
    (root / "directory-link").symlink_to(target_directory, target_is_directory=True)

    result = search_workspace_text(workspace, {"query": "needle", "path": "."})

    assert result["matches"] == [
        {
            "path": "target/secret.txt",
            "line_number": 1,
            "line": "needle",
        }
    ]


def test_search_skips_invalid_utf8_and_uses_internal_file_symlink_target(
    tmp_path: Path,
) -> None:
    """Ignore invalid text and permit a directly requested internal file symlink."""

    root, workspace = create_workspace(tmp_path)
    target = root / "data" / "notes.txt"
    target.parent.mkdir()
    target.write_text("needle", encoding="utf-8")
    (root / "notes-link.txt").symlink_to(target)
    (root / "invalid.bin").write_bytes(b"\xffneedle")

    root_result = search_workspace_text(workspace, {"query": "needle"})
    symlink_result = search_workspace_text(
        workspace,
        {"query": "needle", "path": "notes-link.txt"},
    )

    assert root_result["matches"] == [
        {
            "path": "data/notes.txt",
            "line_number": 1,
            "line": "needle",
        }
    ]
    assert symlink_result["path"] == "data/notes.txt"
    assert symlink_result["matches"] == root_result["matches"]


def test_search_enforces_query_file_byte_match_and_line_limits(
    tmp_path: Path,
) -> None:
    """Bound all search inputs and returned result data deterministically."""

    root, workspace = create_workspace(tmp_path)

    with pytest.raises(ValueError, match="query exceeds"):
        search_workspace_text(workspace, {"query": "a" * (MAX_SEARCH_QUERY_LENGTH + 1)})

    for index in range(MAX_SEARCH_FILES + 1):
        content = "needle\n" if index == MAX_SEARCH_FILES else "other\n"
        (root / f"file-{index:03d}.txt").write_text(content, encoding="utf-8")

    file_limited_result = search_workspace_text(workspace, {"query": "needle"})

    assert file_limited_result["matches"] == []
    assert file_limited_result["truncated"] is True

    for path in root.iterdir():
        path.unlink()
    (root / "large.txt").write_bytes(b"needle" + b"a" * MAX_SEARCH_FILE_BYTES)

    byte_limited_result = search_workspace_text(workspace, {"query": "needle"})

    assert byte_limited_result["matches"] == []
    assert byte_limited_result["truncated"] is True

    (root / "large.txt").unlink()
    (root / "matches.txt").write_text(
        "".join("needle\n" for _ in range(MAX_SEARCH_MATCHES + 1)),
        encoding="utf-8",
    )

    match_limited_result = search_workspace_text(workspace, {"query": "needle"})

    assert len(match_limited_result["matches"]) == MAX_SEARCH_MATCHES
    assert match_limited_result["truncated"] is True

    (root / "matches.txt").unlink()
    (root / "line.txt").write_text(
        "needle" + "a" * MAX_SEARCH_LINE_LENGTH,
        encoding="utf-8",
    )

    line_limited_result = search_workspace_text(workspace, {"query": "needle"})

    assert len(line_limited_result["matches"][0]["line"]) == MAX_SEARCH_LINE_LENGTH
    assert line_limited_result["truncated"] is True
    assert str(root) not in str(line_limited_result)
