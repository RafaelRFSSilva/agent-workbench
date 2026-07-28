"""Tests for safe read-only Git workspace tools."""

import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from agent_workbench.errors import WorkspacePathError
from agent_workbench.git_tools import (
    GIT_TIMEOUT_SECONDS,
    MAX_GIT_OUTPUT_BYTES,
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
        "Inspect the current unstaged and staged Git diff inside the authorized workspace.",
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
    assert "README.md" in dirty_result["status"]
    assert str(root) not in dirty_result["status"]


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

    status_call, diff_unstaged_call, diff_staged_call = run_mock.call_args_list
    assert status_call.args[0] == [
        "git",
        "-c",
        "core.pager=cat",
        "status",
        "--short",
        "--branch",
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
