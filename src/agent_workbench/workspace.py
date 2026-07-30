"""Safe provider-independent workspace path resolution."""

from dataclasses import dataclass
from pathlib import Path

from agent_workbench.errors import ConfigurationError, WorkspacePathError


DEFAULT_IGNORED_TRAVERSAL_DIRECTORY_NAMES = frozenset(
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
"""Directory names omitted from recursive repository inspection."""


@dataclass(frozen=True, slots=True)
class Workspace:
    """Resolve existing paths within one authorized workspace root."""

    root: Path

    def __post_init__(self) -> None:
        """Validate and store the canonical workspace root."""

        supplied_root = self.root.expanduser()

        try:
            canonical_root = supplied_root.resolve(strict=True)
        except FileNotFoundError:
            raise ConfigurationError(
                f"Workspace root does not exist: {supplied_root}"
            ) from None
        except (OSError, RuntimeError):
            raise ConfigurationError(
                f"Unable to resolve workspace root: {supplied_root}"
            ) from None

        if not canonical_root.is_dir():
            raise ConfigurationError(
                f"Workspace root is not a directory: {supplied_root}"
            )

        object.__setattr__(self, "root", canonical_root)

    def resolve(self, path: Path) -> Path:
        """Return an existing canonical path contained by the workspace."""

        if path.is_absolute():
            raise WorkspacePathError(f"Workspace path must be relative: {path}")

        try:
            canonical_path = (self.root / path).resolve(strict=True)
        except FileNotFoundError:
            raise WorkspacePathError(f"Workspace path does not exist: {path}") from None
        except (OSError, RuntimeError):
            raise WorkspacePathError(
                f"Unable to resolve workspace path: {path}"
            ) from None

        try:
            canonical_path.relative_to(self.root)
        except ValueError:
            raise WorkspacePathError(
                f"Workspace path resolves outside the workspace: {path}"
            ) from None

        return canonical_path

    def is_ignored_traversal_path(self, path: Path) -> bool:
        """Return whether a contained path enters an ignored directory."""

        try:
            relative_path = path.relative_to(self.root)
        except ValueError:
            return True
        return any(
            part in DEFAULT_IGNORED_TRAVERSAL_DIRECTORY_NAMES
            for part in relative_path.parts
        )
