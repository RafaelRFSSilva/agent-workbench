"""Immutable provider-independent evidence for conservative manual recovery."""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
import re

from agent_workbench.errors import ConfigurationError

_GIT_OBJECT_ID_PATTERN = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})")


class RecoveryStatus(StrEnum):
    """Represent one safely observed three-state recovery fact."""

    UNKNOWN = "unknown"
    NO = "no"
    YES = "yes"


class IsolatedCommitRecoveryPhase(StrEnum):
    """Identify the isolated-commit phase that required manual inspection."""

    EXACT_STAGING = "exact_staging"
    STAGED_STATE_VERIFICATION = "staged_state_verification"
    LOCAL_COMMIT_CREATION = "local_commit_creation"
    COMMIT_VERIFICATION = "commit_verification"


class WorktreeRecoveryPhase(StrEnum):
    """Identify the worktree lifecycle phase requiring manual inspection."""

    CREATION = "creation"
    REMOVAL = "removal"
    REMOVAL_VERIFICATION = "removal_verification"


@dataclass(frozen=True, slots=True, init=False)
class IsolatedCommitRecoveryEvidence:
    """Store bounded safe evidence after an isolated-commit failure."""

    phase: IsolatedCommitRecoveryPhase
    target_display: str
    expected_branch: str
    observed_branch: str | None
    expected_head: str
    observed_head: str | None
    index_dirty: RecoveryStatus
    staged_paths: tuple[str, ...]
    worktree_dirty: RecoveryStatus

    def __init__(
        self,
        *,
        phase: IsolatedCommitRecoveryPhase,
        target_display: str,
        expected_branch: str,
        observed_branch: str | None,
        expected_head: str,
        observed_head: str | None,
        index_dirty: RecoveryStatus,
        staged_paths: Iterable[str],
        worktree_dirty: RecoveryStatus,
    ) -> None:
        """Validate and snapshot one operator-safe recovery observation."""

        if not isinstance(phase, IsolatedCommitRecoveryPhase):
            raise ConfigurationError(
                "recovery phase must be an IsolatedCommitRecoveryPhase."
            )
        if not isinstance(index_dirty, RecoveryStatus):
            raise ConfigurationError("index recovery state must be a RecoveryStatus.")
        if not isinstance(worktree_dirty, RecoveryStatus):
            raise ConfigurationError(
                "worktree recovery state must be a RecoveryStatus."
            )

        safe_target = _validate_target_display(target_display)
        safe_expected_branch = _validate_branch(
            expected_branch,
            field_name="expected branch",
        )
        safe_observed_branch = (
            None
            if observed_branch is None
            else _validate_branch(
                observed_branch,
                field_name="observed branch",
            )
        )
        safe_expected_head = _validate_head(
            expected_head,
            field_name="expected HEAD",
        )
        safe_observed_head = (
            None
            if observed_head is None
            else _validate_head(
                observed_head,
                field_name="observed HEAD",
            )
        )
        safe_staged_paths = _snapshot_staged_paths(staged_paths)

        if index_dirty is RecoveryStatus.YES and not safe_staged_paths:
            raise ConfigurationError(
                "dirty index recovery evidence requires at least one staged path."
            )
        if index_dirty is not RecoveryStatus.YES and safe_staged_paths:
            raise ConfigurationError(
                "clean or unknown index recovery evidence cannot contain staged paths."
            )

        object.__setattr__(self, "phase", phase)
        object.__setattr__(self, "target_display", safe_target)
        object.__setattr__(self, "expected_branch", safe_expected_branch)
        object.__setattr__(self, "observed_branch", safe_observed_branch)
        object.__setattr__(self, "expected_head", safe_expected_head)
        object.__setattr__(self, "observed_head", safe_observed_head)
        object.__setattr__(self, "index_dirty", index_dirty)
        object.__setattr__(self, "staged_paths", safe_staged_paths)
        object.__setattr__(self, "worktree_dirty", worktree_dirty)

    @property
    def head_changed(self) -> RecoveryStatus:
        """Derive whether the observed HEAD differs from the expected HEAD."""

        if self.observed_head is None:
            return RecoveryStatus.UNKNOWN
        if self.observed_head == self.expected_head:
            return RecoveryStatus.NO
        return RecoveryStatus.YES


@dataclass(frozen=True, slots=True, init=False)
class WorktreeRecoveryEvidence:
    """Store safe evidence after a worktree lifecycle failure."""

    phase: WorktreeRecoveryPhase
    target_display: str
    expected_branch: str
    expected_source_head: str
    observed_source_head: str | None
    expected_worktree_head: str
    observed_worktree_head: str | None
    observed_branch: str | None
    branch_present: RecoveryStatus
    target_present: RecoveryStatus
    registered: RecoveryStatus

    def __init__(
        self,
        *,
        phase: WorktreeRecoveryPhase,
        target_display: str,
        expected_branch: str,
        expected_source_head: str,
        observed_source_head: str | None,
        expected_worktree_head: str,
        observed_worktree_head: str | None,
        observed_branch: str | None,
        branch_present: RecoveryStatus,
        target_present: RecoveryStatus,
        registered: RecoveryStatus,
    ) -> None:
        """Validate and snapshot one operator-safe lifecycle observation."""

        if not isinstance(phase, WorktreeRecoveryPhase):
            raise ConfigurationError(
                "worktree recovery phase must be a WorktreeRecoveryPhase."
            )
        if not isinstance(branch_present, RecoveryStatus):
            raise ConfigurationError(
                "worktree recovery branch state must be a RecoveryStatus."
            )
        if not isinstance(target_present, RecoveryStatus):
            raise ConfigurationError(
                "worktree recovery target state must be a RecoveryStatus."
            )
        if not isinstance(registered, RecoveryStatus):
            raise ConfigurationError(
                "worktree recovery registration state must be a RecoveryStatus."
            )

        safe_target = _validate_target_display(target_display)
        safe_expected_branch = _validate_branch(
            expected_branch,
            field_name="expected branch",
        )
        safe_observed_branch = (
            None
            if observed_branch is None
            else _validate_branch(
                observed_branch,
                field_name="observed branch",
            )
        )
        safe_expected_source_head = _validate_head(
            expected_source_head,
            field_name="expected source HEAD",
        )
        safe_observed_source_head = (
            None
            if observed_source_head is None
            else _validate_head(
                observed_source_head,
                field_name="observed source HEAD",
            )
        )
        safe_expected_worktree_head = _validate_head(
            expected_worktree_head,
            field_name="expected worktree HEAD",
        )
        safe_observed_worktree_head = (
            None
            if observed_worktree_head is None
            else _validate_head(
                observed_worktree_head,
                field_name="observed worktree HEAD",
            )
        )

        object.__setattr__(self, "phase", phase)
        object.__setattr__(self, "target_display", safe_target)
        object.__setattr__(self, "expected_branch", safe_expected_branch)
        object.__setattr__(
            self,
            "expected_source_head",
            safe_expected_source_head,
        )
        object.__setattr__(
            self,
            "observed_source_head",
            safe_observed_source_head,
        )
        object.__setattr__(
            self,
            "expected_worktree_head",
            safe_expected_worktree_head,
        )
        object.__setattr__(
            self,
            "observed_worktree_head",
            safe_observed_worktree_head,
        )
        object.__setattr__(self, "observed_branch", safe_observed_branch)
        object.__setattr__(self, "branch_present", branch_present)
        object.__setattr__(self, "target_present", target_present)
        object.__setattr__(self, "registered", registered)

    @property
    def source_head_changed(self) -> RecoveryStatus:
        """Derive whether the source HEAD differs from its expected identity."""

        if self.observed_source_head is None:
            return RecoveryStatus.UNKNOWN
        if self.observed_source_head == self.expected_source_head:
            return RecoveryStatus.NO
        return RecoveryStatus.YES

    @property
    def worktree_head_changed(self) -> RecoveryStatus:
        """Derive whether the worktree HEAD differs from its expected identity."""

        if self.observed_worktree_head is None:
            return RecoveryStatus.UNKNOWN
        if self.observed_worktree_head == self.expected_worktree_head:
            return RecoveryStatus.NO
        return RecoveryStatus.YES


def _validate_target_display(value: object) -> str:
    """Require one safe relative single-line worktree display value."""

    if (
        not isinstance(value, str)
        or not value.strip()
        or "\0" in value
        or "\n" in value
        or "\r" in value
        or PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
    ):
        raise ConfigurationError(
            "recovery target display must be a safe relative single-line string."
        )
    return value


def _validate_branch(value: object, *, field_name: str) -> str:
    """Require one exact non-blank single-line branch identity."""

    if (
        not isinstance(value, str)
        or not value.strip()
        or "\0" in value
        or "\n" in value
        or "\r" in value
    ):
        raise ConfigurationError(
            f"recovery {field_name} must be a non-blank single-line string."
        )
    return value


def _validate_head(value: object, *, field_name: str) -> str:
    """Require one complete SHA-1 or SHA-256 Git object identifier."""

    if not isinstance(value, str) or _GIT_OBJECT_ID_PATTERN.fullmatch(value) is None:
        raise ConfigurationError(
            f"recovery {field_name} must be a complete Git object identifier."
        )
    return value


def _snapshot_staged_paths(paths: Iterable[str]) -> tuple[str, ...]:
    """Validate and snapshot canonical portable staged paths."""

    if isinstance(paths, str):
        raise ConfigurationError(
            "recovery staged paths must be an iterable of path strings."
        )

    try:
        staged_paths = tuple(paths)
    except TypeError as exc:
        raise ConfigurationError("recovery staged paths must be an iterable.") from exc

    seen: set[str] = set()
    for path in staged_paths:
        if not isinstance(path, str) or not path.strip():
            raise ConfigurationError(
                "each recovery staged path must be a non-blank string."
            )
        if "\0" in path or "\n" in path or "\r" in path or "\\" in path:
            raise ConfigurationError(
                "each recovery staged path must be a canonical portable relative path."
            )

        pure = PurePosixPath(path)
        if (
            pure.is_absolute()
            or str(pure) != path
            or path in {".", ".."}
            or ".." in pure.parts
        ):
            raise ConfigurationError(
                "each recovery staged path must be a canonical portable relative path."
            )

        if path in seen:
            raise ConfigurationError(
                "recovery staged paths cannot contain duplicate entries."
            )
        seen.add(path)

    return staged_paths
