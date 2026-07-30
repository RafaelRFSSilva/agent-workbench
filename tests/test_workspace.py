"""Tests for safe workspace path resolution."""

from pathlib import Path

import pytest

from agent_workbench.errors import ConfigurationError, WorkspacePathError
from agent_workbench.workspace import (
    DEFAULT_IGNORED_TRAVERSAL_DIRECTORY_NAMES,
    Workspace,
)


def create_workspace(tmp_path: Path) -> tuple[Path, Workspace]:
    """Create a workspace directory and resolver."""

    root = tmp_path / "workspace"
    root.mkdir()

    return root, Workspace(root)


def test_default_traversal_directory_policy_is_centralized_and_immutable() -> None:
    """Define one exact immutable policy for recursive repository inspection."""

    assert DEFAULT_IGNORED_TRAVERSAL_DIRECTORY_NAMES == frozenset(
        {
            ".git",
            ".venv",
            "venv",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            ".mypy_cache",
            ".tox",
            ".nox",
            "node_modules",
            "dist",
            "build",
            ".next",
            ".turbo",
            "coverage",
            "htmlcov",
        }
    )


def test_workspace_uses_a_canonical_absolute_root(tmp_path: Path) -> None:
    """Canonicalize an existing workspace directory."""

    root = tmp_path / "workspace"
    root.mkdir()

    workspace = Workspace(root)

    assert workspace.root == root.resolve()
    assert workspace.root.is_absolute()


def test_workspace_accepts_a_relative_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Resolve a relative workspace root from the current directory."""

    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.chdir(tmp_path)

    workspace = Workspace(Path("workspace"))

    assert workspace.root == root.resolve()


def test_workspace_rejects_a_missing_root(tmp_path: Path) -> None:
    """Reject a workspace root that does not exist."""

    with pytest.raises(
        ConfigurationError,
        match="Workspace root does not exist",
    ):
        Workspace(tmp_path / "missing")


def test_workspace_rejects_a_file_root(tmp_path: Path) -> None:
    """Reject a regular file used as the workspace root."""

    root = tmp_path / "workspace.txt"
    root.write_text("not a directory", encoding="utf-8")

    with pytest.raises(
        ConfigurationError,
        match="Workspace root is not a directory",
    ):
        Workspace(root)


def test_workspace_resolves_dot_to_the_root(tmp_path: Path) -> None:
    """Resolve the current relative path to the workspace root."""

    root, workspace = create_workspace(tmp_path)

    assert workspace.resolve(Path(".")) == root.resolve()


def test_workspace_resolves_a_nested_file(tmp_path: Path) -> None:
    """Resolve an existing nested file canonically."""

    root, workspace = create_workspace(tmp_path)
    nested_file = root / "src" / "module.py"
    nested_file.parent.mkdir()
    nested_file.write_text("value = 1\n", encoding="utf-8")

    assert workspace.resolve(Path("src/module.py")) == nested_file.resolve()


def test_workspace_resolves_a_nested_directory(tmp_path: Path) -> None:
    """Resolve an existing nested directory canonically."""

    root, workspace = create_workspace(tmp_path)
    nested_directory = root / "src" / "package"
    nested_directory.mkdir(parents=True)

    assert workspace.resolve(Path("src/package")) == nested_directory.resolve()


def test_workspace_rejects_a_missing_requested_path(tmp_path: Path) -> None:
    """Reject a requested path that does not exist."""

    _, workspace = create_workspace(tmp_path)

    with pytest.raises(
        WorkspacePathError,
        match="Workspace path does not exist",
    ):
        workspace.resolve(Path("missing.txt"))


def test_workspace_rejects_an_absolute_requested_path(tmp_path: Path) -> None:
    """Reject absolute user paths even when they point inside the workspace."""

    root, workspace = create_workspace(tmp_path)

    with pytest.raises(
        WorkspacePathError,
        match="Workspace path must be relative",
    ):
        workspace.resolve(root)


def test_workspace_rejects_direct_traversal_escape(tmp_path: Path) -> None:
    """Reject a parent traversal that resolves outside the workspace."""

    _, workspace = create_workspace(tmp_path)
    external_file = tmp_path / "external.txt"
    external_file.write_text("external", encoding="utf-8")

    with pytest.raises(
        WorkspacePathError,
        match="resolves outside the workspace",
    ):
        workspace.resolve(Path("../external.txt"))


def test_workspace_rejects_normalized_traversal_escape(tmp_path: Path) -> None:
    """Reject normalized nested traversal outside the workspace."""

    root, workspace = create_workspace(tmp_path)
    (root / "nested").mkdir()
    external_file = tmp_path / "external.txt"
    external_file.write_text("external", encoding="utf-8")

    with pytest.raises(
        WorkspacePathError,
        match="resolves outside the workspace",
    ):
        workspace.resolve(Path("nested/../../external.txt"))


def test_workspace_rejects_sibling_prefix_confusion(tmp_path: Path) -> None:
    """Reject a sibling whose name begins with the workspace name."""

    _, workspace = create_workspace(tmp_path)
    sibling = tmp_path / "workspace-backup"
    sibling.mkdir()
    sibling_file = sibling / "secret.txt"
    sibling_file.write_text("secret", encoding="utf-8")

    with pytest.raises(
        WorkspacePathError,
        match="resolves outside the workspace",
    ):
        workspace.resolve(Path("../workspace-backup/secret.txt"))


def test_workspace_rejects_external_file_symlink(tmp_path: Path) -> None:
    """Reject a symlink whose file target is outside the workspace."""

    root, workspace = create_workspace(tmp_path)
    external_file = tmp_path / "external.txt"
    external_file.write_text("external", encoding="utf-8")
    (root / "external-link.txt").symlink_to(external_file)

    with pytest.raises(
        WorkspacePathError,
        match="resolves outside the workspace",
    ):
        workspace.resolve(Path("external-link.txt"))


def test_workspace_rejects_external_directory_symlink(tmp_path: Path) -> None:
    """Reject a nested path that escapes through a directory symlink."""

    root, workspace = create_workspace(tmp_path)
    external_directory = tmp_path / "external"
    external_directory.mkdir()
    external_file = external_directory / "secret.txt"
    external_file.write_text("secret", encoding="utf-8")
    (root / "external-directory").symlink_to(
        external_directory,
        target_is_directory=True,
    )

    with pytest.raises(
        WorkspacePathError,
        match="resolves outside the workspace",
    ):
        workspace.resolve(Path("external-directory/secret.txt"))


def test_workspace_rejects_a_broken_symlink(tmp_path: Path) -> None:
    """Reject a symlink whose target does not exist."""

    root, workspace = create_workspace(tmp_path)
    (root / "broken-link.txt").symlink_to(root / "missing.txt")

    with pytest.raises(
        WorkspacePathError,
        match="Workspace path does not exist",
    ):
        workspace.resolve(Path("broken-link.txt"))


def test_workspace_accepts_an_internal_symlink(tmp_path: Path) -> None:
    """Permit a symlink whose canonical target remains in the workspace."""

    root, workspace = create_workspace(tmp_path)
    target = root / "data" / "notes.txt"
    target.parent.mkdir()
    target.write_text("notes", encoding="utf-8")
    (root / "notes-link.txt").symlink_to(target)

    assert workspace.resolve(Path("notes-link.txt")) == target.resolve()


def test_workspace_does_not_mutate_supplied_paths(tmp_path: Path) -> None:
    """Leave root and requested Path values unchanged."""

    root = tmp_path / "workspace"
    root.mkdir()
    nested_file = root / "nested" / "file.txt"
    nested_file.parent.mkdir()
    nested_file.write_text("content", encoding="utf-8")
    supplied_root = root / "."
    supplied_path = Path("nested/../nested/file.txt")
    original_root = Path(supplied_root)
    original_path = Path(supplied_path)

    workspace = Workspace(supplied_root)
    resolved_path = workspace.resolve(supplied_path)

    assert supplied_root == original_root
    assert supplied_path == original_path
    assert resolved_path == nested_file.resolve()


def test_workspace_never_changes_the_process_working_directory(
    tmp_path: Path,
) -> None:
    """Leave the current working directory unchanged during resolution."""

    root, workspace = create_workspace(tmp_path)
    before = Path.cwd()

    workspace.resolve(Path("."))

    assert Path.cwd() == before
    assert workspace.root == root.resolve()
