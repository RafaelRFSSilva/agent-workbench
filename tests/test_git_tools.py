"""Tests for safe read-only Git workspace tools."""

import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from agent_workbench.errors import WorkspacePathError
from agent_workbench.git_tools import (
    GIT_TIMEOUT_SECONDS,
    MAX_GIT_OUTPUT_BYTES,
    MAX_UNTRACKED_EVIDENCE_BYTES,
    MAX_UNTRACKED_FILES,
    MAX_UNTRACKED_FILE_BYTES,
    MAX_UNTRACKED_OMISSION_METADATA_BYTES,
    inspect_workspace_git_diff,
    inspect_workspace_git_status,
    register_git_tools,
)
from agent_workbench.tool_registry import ToolRegistry
from agent_workbench.tools import ToolDefinition
from agent_workbench.workspace import Workspace


def create_workspace(tmp_path: Path) -> tuple[Path, Workspace]:
    """Create a temporary workspace and its canonical resolver."""

    root = tmp_path / "workspace"
    root.mkdir()

    return root, Workspace(root)


def run_git(root: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    """Run a fixed Git test command in a temporary repository."""

    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
    )


def initialize_repository(root: Path) -> None:
    """Create a committed temporary Git repository without network access."""

    run_git(root, "init")
    run_git(root, "config", "user.name", "Agent Workbench Tests")
    run_git(root, "config", "user.email", "tests@example.invalid")
    (root / "README.md").write_text("baseline\n", encoding="utf-8")
    run_git(root, "add", "README.md")
    run_git(root, "commit", "-m", "baseline")


def create_existing_definition() -> ToolDefinition:
    """Create a definition that precedes Git inspection tools."""

    return ToolDefinition(
        name="existing",
        description="Return an existing value.",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    )


def serialized_result_size(result: object) -> int:
    """Measure one result through the documented stable JSON boundary."""

    return len(
        json.dumps(
            result,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


def test_registers_git_tools_with_exact_schemas_and_order(tmp_path: Path) -> None:
    """Append fixed Git inspection definitions in deterministic order."""

    _, workspace = create_workspace(tmp_path)
    registry = ToolRegistry()
    registry.register(create_existing_definition(), lambda arguments: {"value": "ok"})

    register_git_tools(registry, workspace)

    assert [definition.name for definition in registry.definitions] == [
        "existing",
        "inspect_git_status",
        "inspect_git_diff",
    ]
    assert [definition.description for definition in registry.definitions[1:]] == [
        "Inspect the Git working-tree status inside the authorized workspace.",
        (
            "Inspect the current unstaged, staged, and safe untracked Git "
            "evidence inside the authorized workspace."
        ),
    ]
    assert [definition.input_schema for definition in registry.definitions[1:]] == [
        {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "enum": ["", "."],
                    "description": (
                        "Optional workspace-root alias. Omit this property when "
                        "possible."
                    ),
                }
            },
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Optional workspace-relative path. Omit it, use an empty "
                        "string, or use '.' to inspect the complete workspace diff."
                    ),
                }
            },
            "additionalProperties": False,
        },
    ]


def test_inspects_clean_and_dirty_status_without_absolute_paths(tmp_path: Path) -> None:
    """Return bounded repository-relative status output for clean and dirty trees."""

    root, workspace = create_workspace(tmp_path)
    initialize_repository(root)

    clean_result = inspect_workspace_git_status(workspace, {})
    (root / "README.md").write_text("changed\n", encoding="utf-8")
    dirty_result = inspect_workspace_git_status(workspace, {})

    assert clean_result["status"].startswith("## ")
    assert clean_result["changed_paths"] == []
    assert clean_result["unsafe_changed_path_count"] == 0
    assert "README.md" in dirty_result["status"]
    assert dirty_result["changed_paths"] == ["README.md"]
    assert dirty_result["unsafe_changed_path_count"] == 0
    assert str(root) not in dirty_result["status"]


def test_status_reports_sorted_tracked_untracked_and_renamed_paths(
    tmp_path: Path,
) -> None:
    """Return deterministic typed changed-path evidence without mutating Git."""

    root, workspace = create_workspace(tmp_path)
    initialize_repository(root)
    run_git(root, "mv", "README.md", "renamed.md")
    (root / "alpha.py").write_text("value = 1\n", encoding="utf-8")

    result = inspect_workspace_git_status(workspace, {})

    assert result["changed_paths"] == ["README.md", "alpha.py", "renamed.md"]
    assert result["unsafe_changed_path_count"] == 0
    assert "README.md -> renamed.md" in result["status"]
    assert run_git(root, "status", "--porcelain=v1").stdout == (
        b"R  README.md -> renamed.md\n?? alpha.py\n"
    )


@pytest.mark.parametrize("root_alias", ["", "."])
def test_accepts_explicit_root_aliases_for_status_and_full_diff(
    tmp_path: Path,
    root_alias: str,
) -> None:
    """Treat common model-generated root aliases as complete Git inspection."""

    root, workspace = create_workspace(tmp_path)
    initialize_repository(root)
    (root / "README.md").write_text("changed\n", encoding="utf-8")

    assert inspect_workspace_git_status(
        workspace,
        {"path": root_alias},
    ) == inspect_workspace_git_status(workspace, {})
    assert inspect_workspace_git_diff(
        workspace,
        {"path": root_alias},
    ) == inspect_workspace_git_diff(workspace, {})


@pytest.mark.parametrize(
    "arguments",
    [
        {"path": "README.md"},
        {"path": None},
        {"unexpected": ""},
    ],
)
def test_rejects_non_root_status_arguments(
    tmp_path: Path,
    arguments: dict[str, object],
) -> None:
    """Keep status inspection fixed to the authorized workspace root."""

    root, workspace = create_workspace(tmp_path)
    initialize_repository(root)

    with pytest.raises(ValueError, match="root path|empty or"):
        inspect_workspace_git_status(workspace, arguments)


def test_inspects_unstaged_staged_and_path_scoped_diffs(tmp_path: Path) -> None:
    """Separate fixed unstaged and staged diffs with an optional safe pathspec."""

    root, workspace = create_workspace(tmp_path)
    initialize_repository(root)
    (root / "README.md").write_text("unstaged\n", encoding="utf-8")
    (root / "staged.txt").write_text("staged\n", encoding="utf-8")
    run_git(root, "add", "staged.txt")

    full_result = inspect_workspace_git_diff(workspace, {})
    scoped_result = inspect_workspace_git_diff(workspace, {"path": "README.md"})
    status_after_inspection = run_git(root, "status", "--porcelain=v1").stdout

    assert "-baseline" in full_result["unstaged"]
    assert "staged.txt" in full_result["staged"]
    assert scoped_result["unstaged"] == full_result["unstaged"]
    assert scoped_result["staged"] == ""
    assert str(root) not in str(full_result)
    assert status_after_inspection == b" M README.md\nA  staged.txt\n"


def test_includes_untracked_text_diff_without_mutating_index(
    tmp_path: Path,
) -> None:
    """Represent one new text file while leaving it untracked and unstaged."""

    root, workspace = create_workspace(tmp_path)
    initialize_repository(root)
    (root / "new.py").write_text("value = 1\n", encoding="utf-8")
    status_before = run_git(root, "status", "--porcelain=v1").stdout
    index_before = run_git(root, "diff", "--cached", "--binary").stdout

    result = inspect_workspace_git_diff(workspace, {})

    assert result["unstaged"] == ""
    assert result["staged"] == ""
    assert "diff --git a/new.py b/new.py" in result["untracked"]
    assert "new file mode 100644" in result["untracked"]
    assert "--- /dev/null" in result["untracked"]
    assert "+++ b/new.py" in result["untracked"]
    assert "+value = 1" in result["untracked"]
    assert result["untracked_omitted"] == []
    assert run_git(root, "diff", "--cached", "--binary").stdout == index_before
    assert run_git(root, "status", "--porcelain=v1").stdout == status_before
    assert status_before == b"?? new.py\n"


def test_orders_multiple_untracked_files_and_honors_path_scope(
    tmp_path: Path,
) -> None:
    """Return complete new-file evidence in deterministic relative-path order."""

    root, workspace = create_workspace(tmp_path)
    initialize_repository(root)
    nested = root / "middle"
    nested.mkdir()
    (root / "zeta.txt").write_text("zeta\n", encoding="utf-8")
    (root / "alpha.txt").write_text("alpha\n", encoding="utf-8")
    (nested / "item.txt").write_text("middle\n", encoding="utf-8")

    full = inspect_workspace_git_diff(workspace, {})
    scoped = inspect_workspace_git_diff(workspace, {"path": "middle/item.txt"})

    assert full["untracked"].index("a/alpha.txt") < full["untracked"].index(
        "a/middle/item.txt"
    )
    assert full["untracked"].index("a/middle/item.txt") < full["untracked"].index(
        "a/zeta.txt"
    )
    assert "middle/item.txt" in scoped["untracked"]
    assert "alpha.txt" not in scoped["untracked"]
    assert "zeta.txt" not in scoped["untracked"]


def test_preserves_tracked_diff_while_adding_untracked_evidence(
    tmp_path: Path,
) -> None:
    """Keep existing tracked and staged fields unchanged for mixed changes."""

    root, workspace = create_workspace(tmp_path)
    initialize_repository(root)
    (root / "README.md").write_text("tracked change\n", encoding="utf-8")
    (root / "new.txt").write_text("untracked change\n", encoding="utf-8")

    result = inspect_workspace_git_diff(workspace, {})

    assert "-baseline" in result["unstaged"]
    assert "+tracked change" in result["unstaged"]
    assert result["staged"] == ""
    assert "+untracked change" in result["untracked"]


def test_omits_oversized_and_binary_untracked_contents_with_metadata(
    tmp_path: Path,
) -> None:
    """Return bounded safe markers without exposing unsupported file bodies."""

    assert MAX_UNTRACKED_FILES == 64
    assert MAX_UNTRACKED_FILE_BYTES == 32 * 1024
    assert MAX_UNTRACKED_EVIDENCE_BYTES == 64 * 1024
    root, workspace = create_workspace(tmp_path)
    initialize_repository(root)
    (root / "large.txt").write_bytes(b"x" * (MAX_UNTRACKED_FILE_BYTES + 1))
    (root / "binary.dat").write_bytes(b"private\0\xffcontent")

    result = inspect_workspace_git_diff(workspace, {})

    assert result["untracked"] == ""
    assert result["untracked_omitted"] == [
        {
            "path": "binary.dat",
            "reason": "binary_or_non_utf8",
            "size_bytes": 16,
        },
        {
            "path": "large.txt",
            "reason": "exceeds_file_size_limit",
            "size_bytes": MAX_UNTRACKED_FILE_BYTES + 1,
        },
    ]
    assert "private" not in str(result)
    assert "content" not in str(result)


def test_omits_sensitive_and_generated_untracked_paths_without_exposure(
    tmp_path: Path,
) -> None:
    """Exclude private and traversal-ignored paths from all visible evidence."""

    root, workspace = create_workspace(tmp_path)
    initialize_repository(root)
    (root / ".env").write_text("TOKEN=private\n", encoding="utf-8")
    for directory_name in (".venv", "node_modules", "dist", "build"):
        directory = root / directory_name
        directory.mkdir()
        (directory / "private.txt").write_text(
            f"private {directory_name}\n",
            encoding="utf-8",
        )

    result = inspect_workspace_git_diff(workspace, {})

    assert result["untracked"] == ""
    assert result["untracked_omitted"] == [
        {
            "reason": "unsafe_or_ignored",
            "file_count": 5,
        }
    ]
    assert ".env" not in str(result)
    assert ".venv" not in str(result)
    assert "node_modules" not in str(result)
    assert "private" not in str(result)


def test_collapses_many_long_omission_paths_with_stable_bounded_metadata(
    tmp_path: Path,
) -> None:
    """Aggregate excess omission metadata without exposing additional paths."""

    assert MAX_UNTRACKED_OMISSION_METADATA_BYTES == 16 * 1024
    root, workspace = create_workspace(tmp_path)
    initialize_repository(root)
    for index in range(MAX_UNTRACKED_FILES):
        name = f"binary-{index:02d}-" + ("x" * 220) + ".dat"
        (root / name).write_bytes(b"\0")

    first = inspect_workspace_git_diff(workspace, {})
    second = inspect_workspace_git_diff(workspace, {})

    assert first == second
    assert first["untracked"] == ""
    assert first["untracked_omitted"] == [
        {
            "reason": "omission_metadata_limit",
            "file_count": MAX_UNTRACKED_FILES,
            "reason_counts": {
                "binary_or_non_utf8": MAX_UNTRACKED_FILES,
            },
        }
    ]
    assert "binary-00" not in str(first)
    assert serialized_result_size(first) <= MAX_GIT_OUTPUT_BYTES


def test_mixed_untracked_diff_and_omission_metadata_are_both_bounded(
    tmp_path: Path,
) -> None:
    """Retain useful safe evidence and one bounded unsupported-file marker."""

    root, workspace = create_workspace(tmp_path)
    initialize_repository(root)
    (root / "created.py").write_text("value = 1\n", encoding="utf-8")
    (root / "binary.dat").write_bytes(b"\0private")

    result = inspect_workspace_git_diff(workspace, {})

    assert "a/created.py" in result["untracked"]
    assert result["untracked_omitted"] == [
        {
            "path": "binary.dat",
            "reason": "binary_or_non_utf8",
            "size_bytes": 8,
        }
    ]
    assert "private" not in str(result)
    assert serialized_result_size(result) <= MAX_GIT_OUTPUT_BYTES


def test_near_limit_tracked_diff_counts_omission_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Measure every returned field through compact deterministic JSON."""

    root, workspace = create_workspace(tmp_path)
    (root / "binary.dat").write_bytes(b"\0")
    near_limit_tracked = "x" * (MAX_GIT_OUTPUT_BYTES - 512)
    run_mock = Mock(
        side_effect=[
            near_limit_tracked,
            "",
            "binary.dat\0",
        ]
    )
    monkeypatch.setattr("agent_workbench.git_tools._run_git", run_mock)

    result = inspect_workspace_git_diff(workspace, {})

    assert result["untracked_omitted"]
    assert serialized_result_size(result) <= MAX_GIT_OUTPUT_BYTES
    assert serialized_result_size(result) > MAX_GIT_OUTPUT_BYTES - 1024


def test_inspects_empty_repository_without_untracked_evidence(tmp_path: Path) -> None:
    """Return stable empty fields for an unborn repository with no files."""

    root, workspace = create_workspace(tmp_path)
    run_git(root, "init")

    result = inspect_workspace_git_diff(workspace, {})

    assert result == {
        "unstaged": "",
        "staged": "",
        "untracked": "",
        "untracked_omitted": [],
    }


def test_rejects_traversal_and_non_repository_workspaces(tmp_path: Path) -> None:
    """Delegate path containment and reject workspaces without a Git worktree."""

    root, workspace = create_workspace(tmp_path)
    external = tmp_path / "external.txt"
    external.write_text("outside", encoding="utf-8")

    with pytest.raises(ValueError, match="not a Git worktree"):
        inspect_workspace_git_status(workspace, {})

    with pytest.raises(WorkspacePathError, match="resolves outside the workspace"):
        inspect_workspace_git_diff(workspace, {"path": "../external.txt"})


def test_uses_fixed_non_shell_commands_and_disables_external_diffs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Do not accept caller command flags or invoke a shell for Git inspection."""

    _, workspace = create_workspace(tmp_path)
    run_mock = Mock(
        return_value=subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=b"## main\n",
            stderr=b"",
        )
    )
    monkeypatch.setattr("agent_workbench.git_tools.subprocess.run", run_mock)

    inspect_workspace_git_status(workspace, {})
    inspect_workspace_git_diff(workspace, {})

    (
        status_call,
        diff_unstaged_call,
        diff_staged_call,
        untracked_call,
    ) = run_mock.call_args_list
    assert status_call.args[0] == [
        "git",
        "-c",
        "core.pager=cat",
        "status",
        "--short",
        "--branch",
        "--untracked-files=all",
        "-z",
    ]
    assert diff_unstaged_call.args[0] == [
        "git",
        "-c",
        "diff.external=",
        "-c",
        "core.pager=cat",
        "diff",
        "--no-ext-diff",
        "--",
    ]
    assert diff_staged_call.args[0] == [
        "git",
        "-c",
        "diff.external=",
        "-c",
        "core.pager=cat",
        "diff",
        "--cached",
        "--no-ext-diff",
        "--",
    ]
    assert untracked_call.args[0] == [
        "git",
        "-c",
        "core.pager=cat",
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
        "--",
    ]
    assert all(call.kwargs["shell"] is False for call in run_mock.call_args_list)
    assert all(
        call.kwargs["timeout"] == GIT_TIMEOUT_SECONDS
        for call in run_mock.call_args_list
    )
    assert all(
        call.kwargs["env"]["GIT_EXTERNAL_DIFF"] == ""
        for call in run_mock.call_args_list
    )


def test_reports_safe_timeout_failure(tmp_path: Path, monkeypatch) -> None:
    """Convert a Git timeout into a concise application-level ValueError."""

    _, workspace = create_workspace(tmp_path)
    monkeypatch.setattr(
        "agent_workbench.git_tools.subprocess.run",
        Mock(side_effect=subprocess.TimeoutExpired(["git"], GIT_TIMEOUT_SECONDS)),
    )

    with pytest.raises(ValueError, match="timed out"):
        inspect_workspace_git_status(workspace, {})


def test_reports_missing_git_safely(tmp_path: Path, monkeypatch) -> None:
    """Convert an unavailable executable into a concise ValueError."""

    _, workspace = create_workspace(tmp_path)
    monkeypatch.setattr(
        "agent_workbench.git_tools.subprocess.run",
        Mock(side_effect=FileNotFoundError),
    )

    with pytest.raises(ValueError, match="executable is unavailable"):
        inspect_workspace_git_status(workspace, {})


def test_reports_oversized_and_failed_git_output_safely(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Reject oversized output and command failures without returning stderr."""

    _, workspace = create_workspace(tmp_path)
    run_mock = Mock(
        return_value=subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=b"x" * (MAX_GIT_OUTPUT_BYTES + 1),
            stderr=b"",
        )
    )
    monkeypatch.setattr("agent_workbench.git_tools.subprocess.run", run_mock)

    with pytest.raises(ValueError, match="output exceeds"):
        inspect_workspace_git_status(workspace, {})

    run_mock.return_value = subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout=b"",
        stderr=b"/absolute/path must stay private",
    )

    with pytest.raises(ValueError, match="Git command failed") as exc_info:
        inspect_workspace_git_status(workspace, {})

    assert "/absolute/path" not in str(exc_info.value)
