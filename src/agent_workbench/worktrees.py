"""Validated planning for supervised local Git worktrees."""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import signal
import stat
import subprocess
import threading
from typing import cast

from agent_workbench.errors import CompletionError, ConfigurationError
from agent_workbench.recovery import (
    RecoveryStatus,
    WorktreeRecoveryEvidence,
    WorktreeRecoveryPhase,
)
from agent_workbench.tools import (
    JSONObject,
    JSONValue,
    ToolApprovalDecision,
)

GIT_TIMEOUT_SECONDS = 3
"""Maximum duration for one fixed Git planning command."""

MAX_GIT_OUTPUT_BYTES = 100 * 1024
"""Maximum retained bytes for each Git output stream."""

_SAFE_GIT_CONFIG = (
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
)
_UNSAFE_LOCAL_CONFIG_PATTERN = (
    r"^(filter\..*\.(clean|smudge|process)|diff\.external|"
    r"diff\..*\.(command|textconv))$"
)
_IN_PROGRESS_GIT_PATHS = (
    "MERGE_HEAD",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
    "BISECT_LOG",
    "rebase-merge",
    "rebase-apply",
    "sequencer",
    "index.lock",
)


@dataclass(frozen=True, slots=True, init=False)
class WorktreePlan:
    """Pin one completely validated local worktree creation request."""

    source_repository: Path = field(repr=False)
    source_head: str
    branch_name: str
    target_path: Path = field(repr=False)
    target_display: str

    def __init__(self) -> None:
        """Prevent callers from constructing an unvalidated plan."""

        raise ConfigurationError("worktree plans must be created by plan_git_worktree.")

    @classmethod
    def _validated(
        cls,
        *,
        source_repository: Path,
        source_head: str,
        branch_name: str,
        target_path: Path,
        target_display: str,
    ) -> "WorktreePlan":
        """Construct a plan from values validated in this module."""

        plan = object.__new__(cls)
        object.__setattr__(plan, "source_repository", source_repository)
        object.__setattr__(plan, "source_head", source_head)
        object.__setattr__(plan, "branch_name", branch_name)
        object.__setattr__(plan, "target_path", target_path)
        object.__setattr__(plan, "target_display", target_display)
        return plan

    @property
    def preview(self) -> JSONObject:
        """Return an independent deterministic operator-safe creation preview."""

        return {
            "action": "create_worktree",
            "source_repository": ".",
            "pinned_head": self.source_head,
            "branch_name": self.branch_name,
            "target": self.target_display,
            "command": [
                "git",
                "-C",
                ".",
                *_SAFE_GIT_CONFIG,
                "worktree",
                "add",
                "-b",
                self.branch_name,
                self.target_display,
                self.source_head,
            ],
            "scope": "Creates one local branch and one local worktree only.",
            "exclusions": ("No commit, merge, push, or branch deletion will occur."),
        }


class WorktreeAction(StrEnum):
    """Identify one operator-side local worktree lifecycle action."""

    CREATE = "create"
    REMOVE = "remove"


@dataclass(frozen=True, slots=True, init=False)
class WorktreeApprovalRequest:
    """Provide one immutable exact worktree action preview for approval."""

    action: WorktreeAction
    _preview_json: str = field(repr=False)

    def __init__(
        self,
        action: WorktreeAction,
        preview: JSONValue,
    ) -> None:
        """Validate and snapshot one strict-JSON approval preview."""

        if not isinstance(action, WorktreeAction):
            raise ConfigurationError(
                "worktree approval action must be a WorktreeAction."
            )
        try:
            preview_json = json.dumps(
                preview,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError):
            raise ConfigurationError(
                "worktree approval preview must be strict JSON."
            ) from None
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "_preview_json", preview_json)

    @property
    def preview(self) -> JSONValue:
        """Return an independent copy of the exact action preview."""

        return cast(JSONValue, json.loads(self._preview_json))


type WorktreeApprovalHandler = Callable[
    [WorktreeApprovalRequest],
    ToolApprovalDecision,
]


@dataclass(frozen=True, slots=True, init=False)
class WorktreeHandle:
    """Identify one completely verified created local worktree."""

    source_repository: Path = field(repr=False)
    source_head: str
    branch_name: str
    worktree_path: Path = field(repr=False)
    target_display: str

    def __init__(self) -> None:
        """Prevent callers from constructing an unverified handle."""

        raise ConfigurationError(
            "worktree handles are returned by create_git_worktree."
        )

    @classmethod
    def _validated(
        cls,
        *,
        source_repository: Path,
        source_head: str,
        branch_name: str,
        worktree_path: Path,
        target_display: str,
    ) -> "WorktreeHandle":
        """Construct one handle from fully verified local Git state."""

        handle = object.__new__(cls)
        object.__setattr__(handle, "source_repository", source_repository)
        object.__setattr__(handle, "source_head", source_head)
        object.__setattr__(handle, "branch_name", branch_name)
        object.__setattr__(handle, "worktree_path", worktree_path)
        object.__setattr__(handle, "target_display", target_display)
        return handle


@dataclass(frozen=True, slots=True)
class WorktreeState:
    """Expose bounded safe state for one verified worktree handle."""

    registered: bool
    branch_name: str
    head: str
    clean: bool
    changed_entry_count: int
    target_display: str


@dataclass(frozen=True, slots=True, init=False)
class WorktreeRestartObservation:
    """Store bounded factual restart observations for one expected worktree."""

    observed_source_head: str | None
    observed_source_branch: str | None

    branch_present: RecoveryStatus
    observed_branch_head: str | None

    registered: RecoveryStatus
    observed_registered_branch: str | None
    observed_registered_head: str | None
    registration_locked: RecoveryStatus
    registration_prunable: RecoveryStatus

    target_present: RecoveryStatus
    target_is_directory: RecoveryStatus
    worktree_identity_valid: RecoveryStatus

    observed_worktree_branch: str | None
    observed_worktree_head: str | None

    index_dirty: RecoveryStatus
    staged_paths: tuple[str, ...]
    staged_paths_complete: RecoveryStatus
    worktree_dirty: RecoveryStatus

    def __init__(
        self,
        *,
        observed_source_head: str | None,
        observed_source_branch: str | None,
        branch_present: RecoveryStatus,
        observed_branch_head: str | None,
        registered: RecoveryStatus,
        observed_registered_branch: str | None,
        observed_registered_head: str | None,
        registration_locked: RecoveryStatus,
        registration_prunable: RecoveryStatus,
        target_present: RecoveryStatus,
        target_is_directory: RecoveryStatus,
        worktree_identity_valid: RecoveryStatus,
        observed_worktree_branch: str | None,
        observed_worktree_head: str | None,
        index_dirty: RecoveryStatus,
        staged_paths: tuple[str, ...],
        staged_paths_complete: RecoveryStatus,
        worktree_dirty: RecoveryStatus,
    ) -> None:
        """Validate and snapshot one restart observation."""

        for name, value in (
            ("branch present", branch_present),
            ("registered", registered),
            ("registration locked", registration_locked),
            ("registration prunable", registration_prunable),
            ("target present", target_present),
            ("target is directory", target_is_directory),
            ("worktree identity", worktree_identity_valid),
            ("index dirty", index_dirty),
            ("staged path completeness", staged_paths_complete),
            ("worktree dirty", worktree_dirty),
        ):
            if not isinstance(value, RecoveryStatus):
                raise ConfigurationError(
                    f"worktree restart {name} state must be a RecoveryStatus."
                )

        safe_observed_source_head = _validate_optional_object_id(
            observed_source_head,
            field_name="observed source HEAD",
        )
        safe_observed_source_branch = _validate_optional_branch(
            observed_source_branch,
            field_name="observed source branch",
        )
        safe_observed_branch_head = _validate_optional_object_id(
            observed_branch_head,
            field_name="observed branch HEAD",
        )
        safe_observed_registered_branch = _validate_optional_branch(
            observed_registered_branch,
            field_name="observed registered branch",
        )
        safe_observed_registered_head = _validate_optional_object_id(
            observed_registered_head,
            field_name="observed registered HEAD",
        )
        safe_observed_worktree_branch = _validate_optional_branch(
            observed_worktree_branch,
            field_name="observed worktree branch",
        )
        safe_observed_worktree_head = _validate_optional_object_id(
            observed_worktree_head,
            field_name="observed worktree HEAD",
        )
        safe_staged_paths = _snapshot_restart_staged_paths(staged_paths)

        if (
            branch_present is not RecoveryStatus.YES
            and safe_observed_branch_head is not None
        ):
            raise ConfigurationError(
                "worktree restart branch observation is internally inconsistent."
            )

        if registered is RecoveryStatus.NO:
            if (
                safe_observed_registered_branch is not None
                or safe_observed_registered_head is not None
                or registration_locked is not RecoveryStatus.NO
                or registration_prunable is not RecoveryStatus.NO
            ):
                raise ConfigurationError(
                    "worktree restart registration observation is internally inconsistent."
                )
        elif registered is RecoveryStatus.UNKNOWN:
            if (
                safe_observed_registered_branch is not None
                or safe_observed_registered_head is not None
                or registration_locked is not RecoveryStatus.UNKNOWN
                or registration_prunable is not RecoveryStatus.UNKNOWN
            ):
                raise ConfigurationError(
                    "worktree restart registration observation is internally inconsistent."
                )
        elif (
            registration_locked is RecoveryStatus.UNKNOWN
            or registration_prunable is RecoveryStatus.UNKNOWN
        ):
            raise ConfigurationError(
                "worktree restart registration observation is internally inconsistent."
            )

        if registered is not RecoveryStatus.YES:
            if (
                target_present is not RecoveryStatus.UNKNOWN
                or target_is_directory is not RecoveryStatus.UNKNOWN
                or worktree_identity_valid is not RecoveryStatus.UNKNOWN
                or safe_observed_worktree_branch is not None
                or safe_observed_worktree_head is not None
                or index_dirty is not RecoveryStatus.UNKNOWN
                or safe_staged_paths
                or staged_paths_complete is not RecoveryStatus.UNKNOWN
                or worktree_dirty is not RecoveryStatus.UNKNOWN
            ):
                raise ConfigurationError(
                    "worktree restart target observation is internally inconsistent."
                )

        if target_present is RecoveryStatus.NO:
            if (
                target_is_directory is not RecoveryStatus.NO
                or worktree_identity_valid is not RecoveryStatus.UNKNOWN
                or safe_observed_worktree_branch is not None
                or safe_observed_worktree_head is not None
                or index_dirty is not RecoveryStatus.UNKNOWN
                or safe_staged_paths
                or staged_paths_complete is not RecoveryStatus.UNKNOWN
                or worktree_dirty is not RecoveryStatus.UNKNOWN
            ):
                raise ConfigurationError(
                    "worktree restart target observation is internally inconsistent."
                )

        if target_present is RecoveryStatus.UNKNOWN:
            if (
                target_is_directory is not RecoveryStatus.UNKNOWN
                or worktree_identity_valid is not RecoveryStatus.UNKNOWN
                or safe_observed_worktree_branch is not None
                or safe_observed_worktree_head is not None
                or index_dirty is not RecoveryStatus.UNKNOWN
                or safe_staged_paths
                or staged_paths_complete is not RecoveryStatus.UNKNOWN
                or worktree_dirty is not RecoveryStatus.UNKNOWN
            ):
                raise ConfigurationError(
                    "worktree restart target observation is internally inconsistent."
                )

        if target_is_directory is not RecoveryStatus.YES:
            if (
                worktree_identity_valid is not RecoveryStatus.UNKNOWN
                or safe_observed_worktree_branch is not None
                or safe_observed_worktree_head is not None
                or index_dirty is not RecoveryStatus.UNKNOWN
                or safe_staged_paths
                or staged_paths_complete is not RecoveryStatus.UNKNOWN
                or worktree_dirty is not RecoveryStatus.UNKNOWN
            ):
                raise ConfigurationError(
                    "worktree restart target observation is internally inconsistent."
                )

        if worktree_identity_valid is not RecoveryStatus.YES:
            if (
                safe_observed_worktree_branch is not None
                or safe_observed_worktree_head is not None
                or index_dirty is not RecoveryStatus.UNKNOWN
                or safe_staged_paths
                or staged_paths_complete is not RecoveryStatus.UNKNOWN
                or worktree_dirty is not RecoveryStatus.UNKNOWN
            ):
                raise ConfigurationError(
                    "worktree restart worktree-state observation is internally inconsistent."
                )

        if staged_paths_complete is RecoveryStatus.YES:
            if index_dirty is RecoveryStatus.UNKNOWN:
                raise ConfigurationError(
                    "worktree restart staged-path observation is internally inconsistent."
                )
            if index_dirty is RecoveryStatus.NO:
                if safe_staged_paths:
                    raise ConfigurationError(
                        "worktree restart staged-path observation is internally inconsistent."
                    )
            elif not safe_staged_paths:
                raise ConfigurationError(
                    "worktree restart staged-path observation is internally inconsistent."
                )
        elif staged_paths_complete is RecoveryStatus.UNKNOWN:
            if safe_staged_paths or index_dirty is RecoveryStatus.NO:
                raise ConfigurationError(
                    "worktree restart staged-path observation is internally inconsistent."
                )
        else:
            raise ConfigurationError(
                "worktree restart staged-path observation is internally inconsistent."
            )

        if staged_paths_complete is not RecoveryStatus.YES and safe_staged_paths:
            raise ConfigurationError(
                "worktree restart staged-path observation is internally inconsistent."
            )

        if (
            index_dirty is RecoveryStatus.YES
            and worktree_dirty is not RecoveryStatus.YES
        ):
            raise ConfigurationError(
                "worktree restart worktree-dirty observation is internally inconsistent."
            )
        if worktree_dirty is RecoveryStatus.NO and index_dirty is not RecoveryStatus.NO:
            raise ConfigurationError(
                "worktree restart worktree-dirty observation is internally inconsistent."
            )

        values = {
            "observed_source_head": safe_observed_source_head,
            "observed_source_branch": safe_observed_source_branch,
            "branch_present": branch_present,
            "observed_branch_head": safe_observed_branch_head,
            "registered": registered,
            "observed_registered_branch": safe_observed_registered_branch,
            "observed_registered_head": safe_observed_registered_head,
            "registration_locked": registration_locked,
            "registration_prunable": registration_prunable,
            "target_present": target_present,
            "target_is_directory": target_is_directory,
            "worktree_identity_valid": worktree_identity_valid,
            "observed_worktree_branch": safe_observed_worktree_branch,
            "observed_worktree_head": safe_observed_worktree_head,
            "index_dirty": index_dirty,
            "staged_paths": safe_staged_paths,
            "staged_paths_complete": staged_paths_complete,
            "worktree_dirty": worktree_dirty,
        }
        for name, value in values.items():
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True, init=False)
class WorktreeRemovalPlan:
    """Pin one verified clean worktree removal request."""

    source_repository: Path = field(repr=False)
    source_head: str
    branch_name: str
    worktree_path: Path = field(repr=False)
    worktree_head: str
    target_display: str

    def __init__(self) -> None:
        """Prevent callers from constructing an unvalidated removal plan."""

        raise ConfigurationError(
            "worktree removal plans must be created by plan_git_worktree_removal."
        )

    @classmethod
    def _validated(
        cls,
        *,
        handle: WorktreeHandle,
        worktree_head: str,
    ) -> "WorktreeRemovalPlan":
        """Construct a removal plan from a verified clean worktree."""

        plan = object.__new__(cls)
        object.__setattr__(plan, "source_repository", handle.source_repository)
        object.__setattr__(plan, "source_head", handle.source_head)
        object.__setattr__(plan, "branch_name", handle.branch_name)
        object.__setattr__(plan, "worktree_path", handle.worktree_path)
        object.__setattr__(plan, "worktree_head", worktree_head)
        object.__setattr__(plan, "target_display", handle.target_display)
        return plan

    @property
    def preview(self) -> JSONObject:
        """Return one independent deterministic clean-removal preview."""

        return {
            "action": "remove_worktree",
            "source_repository": ".",
            "branch_name": self.branch_name,
            "worktree_head": self.worktree_head,
            "target": self.target_display,
            "command": [
                "git",
                "-C",
                ".",
                *_SAFE_GIT_CONFIG,
                "worktree",
                "remove",
                self.target_display,
            ],
            "scope": (
                "Removes only the clean local worktree directory and registration."
            ),
            "branch": "The local branch will remain.",
            "exclusions": (
                "No force, branch deletion, commit, merge, push, reset, "
                "clean, or stash will occur."
            ),
        }


@dataclass(frozen=True, slots=True)
class _GitOutput:
    """Store one bounded fixed Git command outcome."""

    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class _WorktreeRecord:
    """Store the identity and exceptional flags of one registered worktree."""

    reported_path: Path = field(repr=False)
    path: Path
    head: str | None
    branch: str | None
    locked: bool
    prunable: bool


def plan_git_worktree(
    source_repository: Path,
    branch_name: str,
    target_path: Path,
) -> WorktreePlan:
    """Validate and pin one local branch and absent worktree target."""

    source = _validate_source_path(source_repository)
    source_head, records = _inspect_source_repository(source)
    _validate_branch(source, branch_name)
    target, target_display = _validate_target(
        source,
        target_path,
        records,
    )

    return WorktreePlan._validated(
        source_repository=source,
        source_head=source_head,
        branch_name=branch_name,
        target_path=target,
        target_display=target_display,
    )


def create_git_worktree(
    plan: WorktreePlan,
    approval_handler: WorktreeApprovalHandler | None,
) -> WorktreeHandle:
    """Create and verify one exact approved local branch and worktree."""

    if not isinstance(plan, WorktreePlan):
        raise ConfigurationError("worktree creation requires a WorktreePlan.")

    _require_worktree_approval(
        WorktreeAction.CREATE,
        plan.preview,
        approval_handler,
    )

    try:
        current_plan = plan_git_worktree(
            plan.source_repository,
            plan.branch_name,
            plan.target_path,
        )
    except ConfigurationError:
        raise CompletionError(
            "Worktree creation plan is stale; no worktree was created."
        ) from None
    if current_plan != plan:
        raise CompletionError(
            "Worktree creation plan is stale; no worktree was created."
        )

    try:
        outcome = _run_git(
            plan.source_repository,
            (
                "worktree",
                "add",
                "-b",
                plan.branch_name,
                str(plan.target_path),
                plan.source_head,
            ),
        )
    except ConfigurationError:
        raise CompletionError(
            _worktree_failure_message(
                plan,
                "Git worktree creation failed; partial state was preserved "
                "for manual recovery",
                WorktreeRecoveryPhase.CREATION,
            )
        ) from None
    if outcome.returncode != 0:
        raise CompletionError(
            _worktree_failure_message(
                plan,
                "Git worktree creation failed; partial state was preserved "
                "for manual recovery",
                WorktreeRecoveryPhase.CREATION,
            )
        ) from None

    try:
        _verify_created_worktree(plan)
    except (CompletionError, ConfigurationError):
        raise CompletionError(
            _worktree_failure_message(
                plan,
                "Git worktree creation failed; partial state was preserved "
                "for manual recovery",
                WorktreeRecoveryPhase.CREATION,
            )
        ) from None

    return WorktreeHandle._validated(
        source_repository=plan.source_repository,
        source_head=plan.source_head,
        branch_name=plan.branch_name,
        worktree_path=plan.target_path,
        target_display=plan.target_display,
    )


def inspect_git_worktree(handle: WorktreeHandle) -> WorktreeState:
    """Return safe current state after fully revalidating a worktree handle."""

    if not isinstance(handle, WorktreeHandle):
        raise ConfigurationError("worktree inspection requires a WorktreeHandle.")
    return _inspect_worktree_identity(handle)


def inspect_worktree_restart_state(
    source_repository: Path,
    target_display: str,
    branch_name: str,
) -> WorktreeRestartObservation:
    """Observe current restart-time Git and worktree facts without mutation."""

    source = _validate_source_path(source_repository)
    safe_target_display = _validate_restart_target_display(target_display)
    _validate_restart_branch_name(source, branch_name)
    _validate_restart_source_repository(source)

    observed_source_head = _observe_source_head(source)
    observed_source_branch = _observe_source_branch(source)
    branch_present, observed_branch_head = _observe_branch_ref(source, branch_name)
    registration = _observe_registered_restart_target(source, safe_target_display)

    target_present = RecoveryStatus.UNKNOWN
    target_is_directory = RecoveryStatus.UNKNOWN
    worktree_identity_valid = RecoveryStatus.UNKNOWN
    observed_worktree_branch: str | None = None
    observed_worktree_head: str | None = None
    index_dirty = RecoveryStatus.UNKNOWN
    staged_paths: tuple[str, ...] = ()
    staged_paths_complete = RecoveryStatus.UNKNOWN
    worktree_dirty = RecoveryStatus.UNKNOWN

    target_path = registration.target_path
    if registration.registered is RecoveryStatus.YES and target_path is not None:
        target_present, target_is_directory = _observe_target_path(target_path)
        if (
            target_present is RecoveryStatus.YES
            and target_is_directory is RecoveryStatus.YES
        ):
            worktree_identity_valid = _observe_worktree_top_level(target_path)
            if worktree_identity_valid is RecoveryStatus.YES:
                observed_worktree_head = _observe_worktree_head(target_path)
                observed_worktree_branch = _observe_worktree_branch(target_path)
                index_dirty, staged_paths, staged_paths_complete = (
                    _observe_staged_paths(target_path)
                )
                worktree_dirty = _observe_worktree_dirty(
                    target_path,
                    index_dirty=index_dirty,
                )

    return WorktreeRestartObservation(
        observed_source_head=observed_source_head,
        observed_source_branch=observed_source_branch,
        branch_present=branch_present,
        observed_branch_head=observed_branch_head,
        registered=registration.registered,
        observed_registered_branch=registration.observed_registered_branch,
        observed_registered_head=registration.observed_registered_head,
        registration_locked=registration.registration_locked,
        registration_prunable=registration.registration_prunable,
        target_present=target_present,
        target_is_directory=target_is_directory,
        worktree_identity_valid=worktree_identity_valid,
        observed_worktree_branch=observed_worktree_branch,
        observed_worktree_head=observed_worktree_head,
        index_dirty=index_dirty,
        staged_paths=staged_paths,
        staged_paths_complete=staged_paths_complete,
        worktree_dirty=worktree_dirty,
    )


def plan_git_worktree_removal(
    handle: WorktreeHandle,
) -> WorktreeRemovalPlan:
    """Plan removal only for one verified completely clean worktree."""

    state = inspect_git_worktree(handle)
    if not state.clean:
        raise CompletionError(
            "Isolated worktree must be completely clean before removal."
        )
    return WorktreeRemovalPlan._validated(
        handle=handle,
        worktree_head=state.head,
    )


def remove_git_worktree(
    plan: WorktreeRemovalPlan,
    approval_handler: WorktreeApprovalHandler | None,
) -> None:
    """Remove one exact approved clean worktree while preserving its branch."""

    if not isinstance(plan, WorktreeRemovalPlan):
        raise ConfigurationError("worktree removal requires a WorktreeRemovalPlan.")

    _require_worktree_approval(
        WorktreeAction.REMOVE,
        plan.preview,
        approval_handler,
    )

    handle = WorktreeHandle._validated(
        source_repository=plan.source_repository,
        source_head=plan.source_head,
        branch_name=plan.branch_name,
        worktree_path=plan.worktree_path,
        target_display=plan.target_display,
    )
    try:
        state = inspect_git_worktree(handle)
    except (CompletionError, ConfigurationError):
        raise CompletionError(
            "Worktree removal plan is stale; the worktree was preserved."
        ) from None
    if not state.clean or state.head != plan.worktree_head:
        raise CompletionError(
            "Worktree removal plan is stale; the worktree was preserved."
        )

    try:
        outcome = _run_git(
            plan.source_repository,
            ("worktree", "remove", str(plan.worktree_path)),
        )
    except ConfigurationError:
        raise CompletionError(
            _worktree_failure_message(
                plan,
                "Clean worktree removal failed; remaining state was preserved "
                "for manual recovery",
                WorktreeRecoveryPhase.REMOVAL,
            )
        ) from None
    if outcome.returncode != 0:
        raise CompletionError(
            _worktree_failure_message(
                plan,
                "Clean worktree removal failed; remaining state was preserved "
                "for manual recovery",
                WorktreeRecoveryPhase.REMOVAL,
            )
        )

    try:
        _verify_removed_worktree(plan)
    except (CompletionError, ConfigurationError):
        raise CompletionError(
            _worktree_failure_message(
                plan,
                "Worktree removal verification was ambiguous; remaining state "
                "was preserved for manual recovery",
                WorktreeRecoveryPhase.REMOVAL_VERIFICATION,
            )
        ) from None


def _require_worktree_approval(
    action: WorktreeAction,
    preview: JSONValue,
    approval_handler: WorktreeApprovalHandler | None,
) -> None:
    """Require one explicit exact operator decision without caching it."""

    action_name = "creation" if action is WorktreeAction.CREATE else "removal"
    if approval_handler is None:
        raise CompletionError(f"Worktree {action_name} requires explicit approval.")

    request = WorktreeApprovalRequest(action, preview)
    try:
        decision = approval_handler(request)
    except Exception:
        raise CompletionError(
            f"Unable to obtain worktree {action_name} approval."
        ) from None

    if decision is ToolApprovalDecision.DENY:
        raise CompletionError(f"Worktree {action_name} approval was denied.")
    if decision is not ToolApprovalDecision.APPROVE:
        raise CompletionError(f"Worktree {action_name} approval decision is invalid.")


def _verify_created_worktree(plan: WorktreePlan) -> None:
    """Require complete created identity and unchanged source state."""

    try:
        target_status = os.lstat(plan.target_path)
    except OSError:
        raise CompletionError("Created worktree target is unavailable.") from None
    if not stat.S_ISDIR(target_status.st_mode):
        raise CompletionError("Created worktree target is invalid.")

    source_head, records = _inspect_source_repository(plan.source_repository)
    if source_head != plan.source_head:
        raise CompletionError("Source HEAD changed during worktree creation.")
    record = _find_worktree_record(records, plan.target_path)
    if (
        record is None
        or record.locked
        or record.prunable
        or record.branch != f"refs/heads/{plan.branch_name}"
        or record.head != plan.source_head
    ):
        raise CompletionError("Created worktree registration is invalid.")

    top_level = _run_git(plan.target_path, ("rev-parse", "--show-toplevel"))
    try:
        reported_top_level = Path(top_level.stdout.strip()).resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError):
        raise CompletionError("Created worktree top-level is invalid.") from None
    if top_level.returncode != 0 or reported_top_level != plan.target_path:
        raise CompletionError("Created worktree top-level is invalid.")

    head = _worktree_head(plan.target_path)
    branch = _worktree_branch(plan.target_path)
    if head != plan.source_head or branch != plan.branch_name:
        raise CompletionError("Created worktree identity does not match its plan.")
    if not _branch_exists(plan.source_repository, plan.branch_name):
        raise CompletionError("Created local worktree branch is unavailable.")

    upstream = _run_git(
        plan.target_path,
        (
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}",
        ),
    )
    if upstream.returncode == 0:
        raise CompletionError("Created worktree branch unexpectedly has an upstream.")


def _inspect_worktree_identity(handle: WorktreeHandle) -> WorktreeState:
    """Revalidate source, registration, branch, target, and bounded status."""

    source_head, records = _inspect_source_repository(handle.source_repository)
    if source_head != handle.source_head:
        raise CompletionError("Worktree source identity has changed.")

    record = _find_worktree_record(records, handle.worktree_path)
    if (
        record is None
        or record.locked
        or record.prunable
        or record.branch != f"refs/heads/{handle.branch_name}"
    ):
        raise CompletionError("Worktree registration or branch identity is invalid.")

    try:
        target_status = os.lstat(handle.worktree_path)
    except OSError:
        raise CompletionError("Worktree target is unavailable.") from None
    if not stat.S_ISDIR(target_status.st_mode):
        raise CompletionError("Worktree target identity is invalid.")

    top_level = _run_git(
        handle.worktree_path,
        ("rev-parse", "--show-toplevel"),
    )
    try:
        reported_top_level = Path(top_level.stdout.strip()).resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError):
        raise CompletionError("Worktree top-level identity is invalid.") from None
    if top_level.returncode != 0 or reported_top_level != handle.worktree_path:
        raise CompletionError("Worktree top-level identity is invalid.")

    head = _worktree_head(handle.worktree_path)
    branch = _worktree_branch(handle.worktree_path)
    if branch != handle.branch_name or record.head != head:
        raise CompletionError("Worktree branch or HEAD identity is invalid.")

    status_output = _run_git(
        handle.worktree_path,
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"),
    )
    if status_output.returncode != 0:
        raise CompletionError("Unable to inspect isolated worktree status.")
    changed_entry_count = sum(bool(entry) for entry in status_output.stdout.split("\0"))
    return WorktreeState(
        registered=True,
        branch_name=branch,
        head=head,
        clean=changed_entry_count == 0,
        changed_entry_count=changed_entry_count,
        target_display=handle.target_display,
    )


def _verify_removed_worktree(plan: WorktreeRemovalPlan) -> None:
    """Verify absence of the worktree and preservation of source and branch."""

    try:
        os.lstat(plan.worktree_path)
    except FileNotFoundError:
        pass
    except OSError:
        raise CompletionError("Unable to verify removed worktree target.") from None
    else:
        raise CompletionError("Removed worktree target still exists.")

    source_head, records = _inspect_source_repository(plan.source_repository)
    if source_head != plan.source_head:
        raise CompletionError("Source HEAD changed during worktree removal.")
    if _find_worktree_record(records, plan.worktree_path) is not None:
        raise CompletionError("Removed worktree remains registered.")
    if not _branch_exists(plan.source_repository, plan.branch_name):
        raise CompletionError("Local worktree branch was not preserved.")


def _worktree_head(worktree_path: Path) -> str:
    """Return one verified complete current worktree commit id."""

    output = _run_git(
        worktree_path,
        ("rev-parse", "--verify", "HEAD^{commit}"),
    )
    head = output.stdout.strip()
    if output.returncode != 0 or not _is_full_object_id(head):
        raise CompletionError("Worktree HEAD is invalid.")
    return head


def _worktree_branch(worktree_path: Path) -> str:
    """Return one attached local branch name or reject detached state."""

    output = _run_git(
        worktree_path,
        ("symbolic-ref", "--quiet", "--short", "HEAD"),
    )
    branch = output.stdout.strip()
    if output.returncode != 0 or not branch:
        raise CompletionError("Worktree must remain on its requested branch.")
    return branch


def _branch_exists(source: Path, branch_name: str) -> bool:
    """Return whether one exact validated local branch exists."""

    output = _run_git(
        source,
        (
            "show-ref",
            "--verify",
            "--quiet",
            "--",
            f"refs/heads/{branch_name}",
        ),
    )
    if output.returncode not in (0, 1):
        raise CompletionError("Unable to inspect local worktree branch.")
    return output.returncode == 0


def _find_worktree_record(
    records: tuple[_WorktreeRecord, ...],
    path: Path,
) -> _WorktreeRecord | None:
    """Return the unique registered record for one canonical path."""

    matches = [record for record in records if record.path == path]
    if len(matches) > 1:
        raise CompletionError("Worktree registration is ambiguous.")
    return matches[0] if matches else None


def _collect_worktree_recovery_evidence(
    plan: WorktreePlan | WorktreeRemovalPlan,
    phase: WorktreeRecoveryPhase,
) -> WorktreeRecoveryEvidence:
    """Collect conservative read-only evidence after a lifecycle failure."""

    if isinstance(plan, WorktreePlan):
        target_path = plan.target_path
        expected_worktree_head = plan.source_head
    elif isinstance(plan, WorktreeRemovalPlan):
        target_path = plan.worktree_path
        expected_worktree_head = plan.worktree_head
    else:
        raise ConfigurationError(
            "worktree recovery evidence requires a worktree lifecycle plan."
        )

    observed_source_head: str | None = None
    observed_worktree_head: str | None = None
    observed_branch: str | None = None

    branch_present = RecoveryStatus.UNKNOWN
    target_present = RecoveryStatus.UNKNOWN
    registered = RecoveryStatus.UNKNOWN

    try:
        source_output = _run_git(
            plan.source_repository,
            ("rev-parse", "--verify", "HEAD^{commit}"),
        )
        source_head = source_output.stdout.strip()
        if source_output.returncode == 0 and _is_full_object_id(source_head):
            observed_source_head = source_head
    except (CompletionError, ConfigurationError):
        pass

    try:
        branch_present = (
            RecoveryStatus.YES
            if _branch_exists(
                plan.source_repository,
                plan.branch_name,
            )
            else RecoveryStatus.NO
        )
    except (CompletionError, ConfigurationError):
        pass

    try:
        os.lstat(target_path)
    except FileNotFoundError:
        target_present = RecoveryStatus.NO
    except OSError:
        pass
    else:
        target_present = RecoveryStatus.YES

    record: _WorktreeRecord | None = None

    try:
        worktree_output = _run_git(
            plan.source_repository,
            ("worktree", "list", "--porcelain", "-z"),
        )
        if worktree_output.returncode == 0:
            records = _parse_worktree_records(worktree_output.stdout)
            record = _find_worktree_record(records, target_path)
            registered = RecoveryStatus.YES if record is not None else RecoveryStatus.NO
    except (CompletionError, ConfigurationError):
        pass

    if record is not None:
        if record.head is not None and _is_full_object_id(record.head):
            observed_worktree_head = record.head

        branch_prefix = "refs/heads/"
        if (
            record.branch is not None
            and record.branch.startswith(branch_prefix)
            and record.branch != branch_prefix
        ):
            observed_branch = record.branch.removeprefix(branch_prefix)

    if target_present is RecoveryStatus.YES:
        try:
            observed_worktree_head = _worktree_head(target_path)
        except (CompletionError, ConfigurationError):
            pass

        try:
            observed_branch = _worktree_branch(target_path)
        except (CompletionError, ConfigurationError):
            pass

    try:
        return WorktreeRecoveryEvidence(
            phase=phase,
            target_display=plan.target_display,
            expected_branch=plan.branch_name,
            expected_source_head=plan.source_head,
            observed_source_head=observed_source_head,
            expected_worktree_head=expected_worktree_head,
            observed_worktree_head=observed_worktree_head,
            observed_branch=observed_branch,
            branch_present=branch_present,
            target_present=target_present,
            registered=registered,
        )
    except ConfigurationError:
        return WorktreeRecoveryEvidence(
            phase=phase,
            target_display=plan.target_display,
            expected_branch=plan.branch_name,
            expected_source_head=plan.source_head,
            observed_source_head=None,
            expected_worktree_head=expected_worktree_head,
            observed_worktree_head=None,
            observed_branch=None,
            branch_present=RecoveryStatus.UNKNOWN,
            target_present=RecoveryStatus.UNKNOWN,
            registered=RecoveryStatus.UNKNOWN,
        )


def _worktree_failure_message(
    plan: WorktreePlan | WorktreeRemovalPlan,
    reason: str,
    phase: WorktreeRecoveryPhase,
) -> str:
    """Format safe lifecycle recovery evidence without performing cleanup."""

    evidence = _collect_worktree_recovery_evidence(
        plan,
        phase,
    )

    presence_labels = {
        RecoveryStatus.UNKNOWN: "unknown",
        RecoveryStatus.NO: "absent",
        RecoveryStatus.YES: "present",
    }

    return (
        f"{reason} "
        f"(branch {evidence.expected_branch}: "
        f"{presence_labels[evidence.branch_present]}; "
        f"target {evidence.target_display}: "
        f"{presence_labels[evidence.target_present]}; "
        f"registered: {presence_labels[evidence.registered]}; "
        f"source HEAD changed: {evidence.source_head_changed.value}; "
        f"worktree HEAD changed: {evidence.worktree_head_changed.value})."
    )


def _validate_source_path(source_repository: object) -> Path:
    """Return one canonical existing directory supplied as a Path."""

    if not isinstance(source_repository, Path):
        raise ConfigurationError("source repository path must be a Path.")

    try:
        source = source_repository.expanduser().resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError):
        raise ConfigurationError(
            "source repository must be an existing directory."
        ) from None

    if not source.is_dir():
        raise ConfigurationError("source repository must be an existing directory.")

    git_directory = source / ".git"
    try:
        git_status = os.lstat(git_directory)
    except OSError:
        raise ConfigurationError(
            "source repository must be a primary Git working tree."
        ) from None
    if not stat.S_ISDIR(git_status.st_mode):
        raise ConfigurationError(
            "source repository must be a primary Git working tree."
        )

    return source


def _validate_restart_source_repository(source: Path) -> None:
    """Validate the restart source as an exact primary non-bare Git worktree."""

    top_level = _run_git(source, ("rev-parse", "--show-toplevel"))
    if top_level.returncode != 0:
        raise ConfigurationError("source repository is not a valid Git repository.")
    try:
        reported_top_level = Path(top_level.stdout.strip()).resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError):
        raise ConfigurationError("source repository top-level is invalid.") from None
    if reported_top_level != source:
        raise ConfigurationError(
            "source repository path must be the exact Git top-level."
        )

    bare = _run_git(source, ("rev-parse", "--is-bare-repository"))
    if bare.returncode != 0 or bare.stdout.strip() != "false":
        raise ConfigurationError(
            "source repository must be a primary non-bare working tree."
        )

    unsafe_config = _run_git(
        source,
        (
            "config",
            "--local",
            "--get-regexp",
            _UNSAFE_LOCAL_CONFIG_PATTERN,
        ),
    )
    if unsafe_config.returncode not in (0, 1):
        raise ConfigurationError(
            "unable to inspect repository-local Git configuration."
        )
    if unsafe_config.returncode == 0 and unsafe_config.stdout:
        raise ConfigurationError(
            "source repository contains unsafe local Git execution configuration."
        )


def _validate_restart_branch_name(source: Path, branch_name: object) -> None:
    """Validate one exact local branch name for restart inspection."""

    if not isinstance(branch_name, str):
        raise ConfigurationError("restart branch name must be a string.")
    if not branch_name.strip():
        raise ConfigurationError("restart branch name must be non-blank.")
    if branch_name.startswith("-"):
        raise ConfigurationError("restart branch name must not be option-like.")
    if branch_name == "HEAD":
        raise ConfigurationError("restart branch name must be a valid local branch.")

    checked = _run_git(
        source,
        ("check-ref-format", "--branch", branch_name),
    )
    if checked.returncode != 0:
        raise ConfigurationError("restart branch name must be a valid local branch.")


def _validate_restart_target_display(target_display: object) -> str:
    """Validate a persisted relative worktree display for safe comparison only."""

    if (
        not isinstance(target_display, str)
        or not target_display.strip()
        or target_display == "."
        or "\\" in target_display
        or "\0" in target_display
        or "\n" in target_display
        or "\r" in target_display
        or PurePosixPath(target_display).is_absolute()
        or PureWindowsPath(target_display).is_absolute()
    ):
        raise ConfigurationError(
            "restart target display must be a safe relative single-line POSIX string."
        )
    pure = PurePosixPath(target_display)
    if str(pure) != target_display or any(part in ("", ".") for part in pure.parts):
        raise ConfigurationError(
            "restart target display must be a safe relative single-line POSIX string."
        )
    return target_display


def _validate_optional_branch(value: object, *, field_name: str) -> str | None:
    """Validate one optional observed branch name."""

    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value.strip()
        or "\0" in value
        or "\n" in value
        or "\r" in value
    ):
        raise ConfigurationError(
            f"worktree restart {field_name} must be a non-blank single-line string."
        )
    return value


def _validate_optional_object_id(value: object, *, field_name: str) -> str | None:
    """Validate one optional observed complete Git object identifier."""

    if value is None:
        return None
    if not isinstance(value, str) or not _is_full_object_id(value):
        raise ConfigurationError(
            f"worktree restart {field_name} must be a complete Git object identifier."
        )
    return value


def _snapshot_restart_staged_paths(paths: object) -> tuple[str, ...]:
    """Validate and snapshot safe staged paths observed during restart inspection."""

    if isinstance(paths, str):
        raise ConfigurationError(
            "restart staged paths must be an iterable of path strings."
        )
    try:
        staged_paths = tuple(paths)
    except TypeError as exc:
        raise ConfigurationError("restart staged paths must be an iterable.") from exc

    seen: set[str] = set()
    for path in staged_paths:
        if not isinstance(path, str) or not path.strip():
            raise ConfigurationError(
                "each restart staged path must be a non-blank string."
            )
        if "\\" in path or "\0" in path or "\n" in path or "\r" in path:
            raise ConfigurationError(
                "each restart staged path must be a canonical portable relative path."
            )
        pure = PurePosixPath(path)
        if (
            pure.is_absolute()
            or str(pure) != path
            or path in {".", ".."}
            or ".." in pure.parts
        ):
            raise ConfigurationError(
                "each restart staged path must be a canonical portable relative path."
            )
        if path in seen:
            raise ConfigurationError(
                "restart staged paths cannot contain duplicate entries."
            )
        seen.add(path)
    return staged_paths


def _observe_source_head(source: Path) -> str | None:
    """Observe the current source HEAD if safely available."""

    try:
        output = _run_git(source, ("rev-parse", "--verify", "HEAD^{commit}"))
    except ConfigurationError:
        return None
    head = output.stdout.strip()
    return head if output.returncode == 0 and _is_full_object_id(head) else None


def _observe_source_branch(source: Path) -> str | None:
    """Observe the current attached source branch if safely available."""

    try:
        output = _run_git(source, ("symbolic-ref", "--quiet", "--short", "HEAD"))
    except ConfigurationError:
        return None
    branch = output.stdout.strip()
    return branch if output.returncode == 0 and branch else None


def _observe_branch_ref(
    source: Path, branch_name: str
) -> tuple[RecoveryStatus, str | None]:
    """Observe exact local branch presence and head without touching remotes."""

    try:
        present = _run_git(
            source,
            (
                "show-ref",
                "--verify",
                "--quiet",
                "--",
                f"refs/heads/{branch_name}",
            ),
        )
    except ConfigurationError:
        return RecoveryStatus.UNKNOWN, None
    if present.returncode == 1:
        return RecoveryStatus.NO, None
    if present.returncode != 0:
        return RecoveryStatus.UNKNOWN, None

    try:
        head_output = _run_git(
            source,
            ("rev-parse", "--verify", f"refs/heads/{branch_name}^{{commit}}"),
        )
    except ConfigurationError:
        return RecoveryStatus.YES, None
    head = head_output.stdout.strip()
    return (
        RecoveryStatus.YES,
        head if head_output.returncode == 0 and _is_full_object_id(head) else None,
    )


@dataclass(frozen=True, slots=True)
class _RestartRegistrationObservation:
    """Carry derived registration observation facts for restart inspection."""

    registered: RecoveryStatus
    observed_registered_branch: str | None
    observed_registered_head: str | None
    registration_locked: RecoveryStatus
    registration_prunable: RecoveryStatus
    target_path: Path | None = field(repr=False)


def _observe_registered_restart_target(
    source: Path,
    target_display: str,
) -> _RestartRegistrationObservation:
    """Match one registered worktree by derived display, not reconstructed path."""

    try:
        worktree_output = _run_git(source, ("worktree", "list", "--porcelain", "-z"))
        if worktree_output.returncode != 0:
            raise ConfigurationError
        records = _parse_worktree_records(worktree_output.stdout)
    except ConfigurationError:
        return _RestartRegistrationObservation(
            registered=RecoveryStatus.UNKNOWN,
            observed_registered_branch=None,
            observed_registered_head=None,
            registration_locked=RecoveryStatus.UNKNOWN,
            registration_prunable=RecoveryStatus.UNKNOWN,
            target_path=None,
        )

    matches = []
    for record in records:
        derived_display = Path(os.path.relpath(record.reported_path, source)).as_posix()
        if derived_display == target_display:
            matches.append(record)

    if not matches:
        return _RestartRegistrationObservation(
            registered=RecoveryStatus.NO,
            observed_registered_branch=None,
            observed_registered_head=None,
            registration_locked=RecoveryStatus.NO,
            registration_prunable=RecoveryStatus.NO,
            target_path=None,
        )
    if len(matches) != 1:
        return _RestartRegistrationObservation(
            registered=RecoveryStatus.UNKNOWN,
            observed_registered_branch=None,
            observed_registered_head=None,
            registration_locked=RecoveryStatus.UNKNOWN,
            registration_prunable=RecoveryStatus.UNKNOWN,
            target_path=None,
        )

    record = matches[0]
    branch_prefix = "refs/heads/"
    observed_registered_branch = None
    if (
        record.branch is not None
        and record.branch.startswith(branch_prefix)
        and record.branch != branch_prefix
    ):
        observed_registered_branch = record.branch.removeprefix(branch_prefix)

    return _RestartRegistrationObservation(
        registered=RecoveryStatus.YES,
        observed_registered_branch=observed_registered_branch,
        observed_registered_head=record.head,
        registration_locked=RecoveryStatus.YES if record.locked else RecoveryStatus.NO,
        registration_prunable=RecoveryStatus.YES
        if record.prunable
        else RecoveryStatus.NO,
        target_path=record.reported_path,
    )


def _observe_target_path(target_path: Path) -> tuple[RecoveryStatus, RecoveryStatus]:
    """Observe target presence and directory-ness without following symlinks."""

    try:
        target_status = os.lstat(target_path)
    except FileNotFoundError:
        return RecoveryStatus.NO, RecoveryStatus.NO
    except OSError:
        return RecoveryStatus.UNKNOWN, RecoveryStatus.UNKNOWN
    if stat.S_ISDIR(target_status.st_mode):
        return RecoveryStatus.YES, RecoveryStatus.YES
    return RecoveryStatus.YES, RecoveryStatus.NO


def _observe_worktree_top_level(target_path: Path) -> RecoveryStatus:
    """Verify the target reports itself as the exact worktree top-level."""

    try:
        output = _run_git(target_path, ("rev-parse", "--show-toplevel"))
    except ConfigurationError:
        return RecoveryStatus.UNKNOWN
    reported = output.stdout.strip()
    if output.returncode != 0 or not reported:
        return RecoveryStatus.UNKNOWN
    if Path(os.path.abspath(reported)) == Path(os.path.abspath(target_path)):
        return RecoveryStatus.YES
    return RecoveryStatus.NO


def _observe_worktree_head(target_path: Path) -> str | None:
    """Observe the current worktree HEAD if safely available."""

    try:
        output = _run_git(target_path, ("rev-parse", "--verify", "HEAD^{commit}"))
    except ConfigurationError:
        return None
    head = output.stdout.strip()
    return head if output.returncode == 0 and _is_full_object_id(head) else None


def _observe_worktree_branch(target_path: Path) -> str | None:
    """Observe the current attached worktree branch if safely available."""

    try:
        output = _run_git(target_path, ("symbolic-ref", "--quiet", "--short", "HEAD"))
    except ConfigurationError:
        return None
    branch = output.stdout.strip()
    return branch if output.returncode == 0 and branch else None


def _observe_staged_paths(
    target_path: Path,
) -> tuple[RecoveryStatus, tuple[str, ...], RecoveryStatus]:
    """Observe staged paths conservatively without leaking unsafe names."""

    try:
        output = _run_git(
            target_path,
            (
                "diff",
                "--cached",
                "--name-only",
                "-z",
                "--no-ext-diff",
                "--no-textconv",
                "--",
            ),
        )
    except ConfigurationError:
        return RecoveryStatus.UNKNOWN, (), RecoveryStatus.UNKNOWN
    if output.returncode != 0:
        return RecoveryStatus.UNKNOWN, (), RecoveryStatus.UNKNOWN
    if not output.stdout:
        return RecoveryStatus.NO, (), RecoveryStatus.YES

    observed_paths: list[str] = []
    for raw_path in output.stdout.split("\0"):
        if not raw_path:
            continue
        try:
            observed_paths.append(_validate_restart_stage_path(raw_path))
        except ConfigurationError:
            return RecoveryStatus.YES, (), RecoveryStatus.UNKNOWN
    return RecoveryStatus.YES, tuple(observed_paths), RecoveryStatus.YES


def _validate_restart_stage_path(path: str) -> str:
    """Validate one staged path for restart observation output."""

    if (
        not path
        or not path.strip()
        or "\\" in path
        or "\0" in path
        or "\n" in path
        or "\r" in path
    ):
        raise ConfigurationError("restart staged path is unsafe.")
    pure = PurePosixPath(path)
    if (
        pure.is_absolute()
        or str(pure) != path
        or path in {".", ".."}
        or ".." in pure.parts
    ):
        raise ConfigurationError("restart staged path is unsafe.")
    return path


def _observe_worktree_dirty(
    target_path: Path,
    *,
    index_dirty: RecoveryStatus,
) -> RecoveryStatus:
    """Observe whether the worktree currently contains any staged, unstaged, or untracked changes."""

    if index_dirty is RecoveryStatus.YES:
        return RecoveryStatus.YES

    dirty_observations: list[RecoveryStatus] = [index_dirty]

    try:
        unstaged = _run_git(
            target_path,
            ("diff-files", "--quiet", "--no-ext-diff", "--no-textconv", "--"),
        )
    except ConfigurationError:
        dirty_observations.append(RecoveryStatus.UNKNOWN)
    else:
        if unstaged.returncode == 1:
            return RecoveryStatus.YES
        if unstaged.returncode == 0:
            dirty_observations.append(RecoveryStatus.NO)
        else:
            dirty_observations.append(RecoveryStatus.UNKNOWN)

    try:
        untracked = _run_git(
            target_path,
            ("ls-files", "--others", "--exclude-standard", "-z"),
        )
    except ConfigurationError:
        dirty_observations.append(RecoveryStatus.UNKNOWN)
    else:
        if untracked.returncode == 0 and untracked.stdout:
            return RecoveryStatus.YES
        if untracked.returncode == 0:
            dirty_observations.append(RecoveryStatus.NO)
        else:
            dirty_observations.append(RecoveryStatus.UNKNOWN)

    if all(status is RecoveryStatus.NO for status in dirty_observations):
        return RecoveryStatus.NO
    return RecoveryStatus.UNKNOWN


def _inspect_source_repository(
    source: Path,
) -> tuple[str, tuple[_WorktreeRecord, ...]]:
    """Validate primary repository identity, state, and execution safety."""

    top_level = _run_git(source, ("rev-parse", "--show-toplevel"))
    if top_level.returncode != 0:
        raise ConfigurationError("source repository is not a valid Git repository.")
    try:
        reported_top_level = Path(top_level.stdout.strip()).resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError):
        raise ConfigurationError("source repository top-level is invalid.") from None
    if reported_top_level != source:
        raise ConfigurationError(
            "source repository path must be the exact Git top-level."
        )

    bare = _run_git(source, ("rev-parse", "--is-bare-repository"))
    if bare.returncode != 0 or bare.stdout.strip() != "false":
        raise ConfigurationError(
            "source repository must be a primary non-bare working tree."
        )

    head = _run_git(source, ("rev-parse", "--verify", "HEAD^{commit}"))
    source_head = head.stdout.strip()
    if head.returncode != 0 or not _is_full_object_id(source_head):
        raise ConfigurationError("source repository must have a committed HEAD.")

    _reject_in_progress_operation(source)

    status_output = _run_git(
        source,
        ("status", "--porcelain=v1", "--untracked-files=all"),
    )
    if status_output.returncode != 0:
        raise ConfigurationError("unable to inspect source repository status.")
    if status_output.stdout:
        raise ConfigurationError(
            "source repository must be completely clean before planning."
        )

    unsafe_config = _run_git(
        source,
        (
            "config",
            "--local",
            "--get-regexp",
            _UNSAFE_LOCAL_CONFIG_PATTERN,
        ),
    )
    if unsafe_config.returncode not in (0, 1):
        raise ConfigurationError(
            "unable to inspect repository-local Git configuration."
        )
    if unsafe_config.returncode == 0 and unsafe_config.stdout:
        raise ConfigurationError(
            "source repository contains unsafe local Git execution configuration."
        )

    worktree_output = _run_git(
        source,
        ("worktree", "list", "--porcelain", "-z"),
    )
    if worktree_output.returncode != 0:
        raise ConfigurationError("unable to inspect registered Git worktrees.")
    records = _parse_worktree_records(worktree_output.stdout)
    if not any(record.path == source for record in records):
        raise ConfigurationError(
            "source repository primary worktree registration is ambiguous."
        )

    return source_head, records


def _reject_in_progress_operation(source: Path) -> None:
    """Reject known primary-repository operation and lock markers."""

    git_directory = source / ".git"
    for relative_path in _IN_PROGRESS_GIT_PATHS:
        try:
            os.lstat(git_directory / relative_path)
        except FileNotFoundError:
            continue
        except OSError:
            raise ConfigurationError(
                "unable to inspect source repository operation state."
            ) from None
        raise ConfigurationError("source repository has a Git operation in progress.")


def _validate_branch(source: Path, branch_name: object) -> None:
    """Require one exact absent non-option local branch name."""

    if not isinstance(branch_name, str):
        raise ConfigurationError("worktree branch name must be a string.")
    if not branch_name.strip():
        raise ConfigurationError("worktree branch name must be non-blank.")
    if branch_name.startswith("-"):
        raise ConfigurationError("worktree branch name must not be option-like.")
    if branch_name == "HEAD":
        raise ConfigurationError("worktree branch name must be a valid local branch.")

    checked = _run_git(
        source,
        ("check-ref-format", "--branch", branch_name),
    )
    if checked.returncode != 0:
        raise ConfigurationError("worktree branch name must be a valid local branch.")

    existing = _run_git(
        source,
        (
            "show-ref",
            "--verify",
            "--quiet",
            "--",
            f"refs/heads/{branch_name}",
        ),
    )
    if existing.returncode == 0:
        raise ConfigurationError("worktree branch already exists locally.")
    if existing.returncode != 1:
        raise ConfigurationError("unable to inspect local worktree branch.")


def _validate_target(
    source: Path,
    target_path: object,
    records: tuple[_WorktreeRecord, ...],
) -> tuple[Path, str]:
    """Return one absent canonical target with a safe relative display."""

    if not isinstance(target_path, Path):
        raise ConfigurationError("worktree target path must be a Path.")

    supplied = target_path.expanduser()
    if not supplied.name or supplied.name.startswith("-"):
        raise ConfigurationError(
            "worktree target must have a non-option-like final path component."
        )

    lexical_target = Path(os.path.abspath(supplied))
    try:
        os.lstat(lexical_target)
    except FileNotFoundError:
        pass
    except OSError:
        raise ConfigurationError("unable to inspect worktree target.") from None
    else:
        raise ConfigurationError("worktree target must not already exist.")

    canonical_parent = _validate_target_parent(lexical_target.parent)
    target = canonical_parent / lexical_target.name

    if (
        target == source
        or target.is_relative_to(source)
        or source.is_relative_to(target)
    ):
        raise ConfigurationError(
            "worktree target must be separate from the source repository."
        )

    for record in records:
        if (
            target == record.path
            or target.is_relative_to(record.path)
            or record.path.is_relative_to(target)
        ):
            raise ConfigurationError(
                "worktree target collides with a registered worktree."
            )

    target_display = Path(os.path.relpath(target, source)).as_posix()
    return target, target_display


def _validate_target_parent(parent: Path) -> Path:
    """Reject missing, non-directory, or symlinked target parent chains."""

    current = Path(parent.anchor)
    for component in parent.parts[1:]:
        current /= component
        try:
            status_result = os.lstat(current)
        except FileNotFoundError:
            raise ConfigurationError(
                "worktree target parent must already exist."
            ) from None
        except OSError:
            raise ConfigurationError(
                "unable to inspect worktree target parent."
            ) from None
        if stat.S_ISLNK(status_result.st_mode):
            raise ConfigurationError(
                "worktree target parent chain must not contain symlinks."
            )
        if not stat.S_ISDIR(status_result.st_mode):
            raise ConfigurationError("worktree target parent must be a directory.")

    try:
        return parent.resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError):
        raise ConfigurationError(
            "worktree target parent must be an existing directory."
        ) from None


def _parse_worktree_records(output: str) -> tuple[_WorktreeRecord, ...]:
    """Parse NUL-delimited porcelain output conservatively."""

    if "\ufffd" in output:
        raise ConfigurationError("registered worktree state is ambiguous.")

    records: list[_WorktreeRecord] = []
    current: dict[str, object] | None = None
    for token in output.split("\0"):
        if not token:
            if current is not None:
                records.append(_record_from_fields(current))
                current = None
            continue
        key, separator, value = token.partition(" ")
        if key == "worktree":
            if current is not None:
                raise ConfigurationError("registered worktree state is ambiguous.")
            current = {"path": value}
            continue
        if current is None:
            raise ConfigurationError("registered worktree state is ambiguous.")
        if not separator:
            if key not in {"bare", "detached", "locked", "prunable"}:
                raise ConfigurationError("registered worktree state is ambiguous.")
            value = ""
        if key in current:
            raise ConfigurationError("registered worktree state is ambiguous.")
        current[key] = value

    if current is not None:
        records.append(_record_from_fields(current))
    if not records or len({record.path for record in records}) != len(records):
        raise ConfigurationError("registered worktree state is ambiguous.")
    return tuple(records)


def _record_from_fields(fields: dict[str, object]) -> _WorktreeRecord:
    """Validate one parsed worktree record without exposing its host path."""

    raw_path = fields.get("path")
    if not isinstance(raw_path, str):
        raise ConfigurationError("registered worktree state is ambiguous.")
    reported_path = Path(raw_path)
    if not reported_path.is_absolute():
        raise ConfigurationError("registered worktree state is ambiguous.")
    try:
        canonical_path = reported_path.resolve(strict=False)
    except (OSError, RuntimeError):
        raise ConfigurationError("registered worktree state is ambiguous.") from None

    raw_branch = fields.get("branch")
    branch = raw_branch if isinstance(raw_branch, str) else None
    raw_head = fields.get("HEAD")
    head = (
        raw_head if isinstance(raw_head, str) and _is_full_object_id(raw_head) else None
    )
    return _WorktreeRecord(
        reported_path=reported_path,
        path=canonical_path,
        head=head,
        branch=branch,
        locked="locked" in fields,
        prunable="prunable" in fields,
    )


def _is_full_object_id(value: str) -> bool:
    """Return whether Git produced a complete SHA-1 or SHA-256 object id."""

    return len(value) in (40, 64) and all(
        character in "0123456789abcdef" for character in value
    )


def _git_environment() -> dict[str, str]:
    """Return a minimal local-only Git environment without parent secrets."""

    return {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_EXTERNAL_DIFF": "",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": "/nonexistent",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "XDG_CONFIG_HOME": "/nonexistent",
    }


def _run_git(source: Path, arguments: tuple[str, ...]) -> _GitOutput:
    """Run one fixed non-shell Git command with bounded streamed output."""

    command = [
        "git",
        "-C",
        str(source),
        *_SAFE_GIT_CONFIG,
        *arguments,
    ]
    try:
        process = subprocess.Popen(
            command,
            cwd=source,
            env=_git_environment(),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except FileNotFoundError:
        raise ConfigurationError("Git executable is unavailable.") from None
    except OSError:
        raise ConfigurationError("unable to start Git command.") from None

    if process.stdout is None or process.stderr is None:
        _terminate_process_group(process)
        raise ConfigurationError("unable to capture Git command output.")

    stdout_capture = _BoundedCapture()
    stderr_capture = _BoundedCapture()
    stdout_thread = threading.Thread(
        target=stdout_capture.read,
        args=(process.stdout,),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=stderr_capture.read,
        args=(process.stderr,),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    timed_out = False
    try:
        process.wait(timeout=GIT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_process_group(process)

    stdout_thread.join(timeout=1)
    stderr_thread.join(timeout=1)
    if stdout_thread.is_alive() or stderr_thread.is_alive():
        _terminate_process_group(process)
        process.stdout.close()
        process.stderr.close()
        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)

    if timed_out:
        raise ConfigurationError("Git command timed out.")
    if stdout_capture.failure is not None or stderr_capture.failure is not None:
        raise ConfigurationError("unable to capture Git command output.")
    if stdout_thread.is_alive() or stderr_thread.is_alive():
        raise ConfigurationError("unable to capture Git command output.")
    if stdout_capture.truncated or stderr_capture.truncated:
        raise ConfigurationError("Git command output exceeds the safe limit.")

    return _GitOutput(
        returncode=process.returncode,
        stdout=bytes(stdout_capture.content).decode("utf-8", errors="replace"),
        stderr=bytes(stderr_capture.content).decode("utf-8", errors="replace"),
    )


@dataclass(slots=True)
class _BoundedCapture:
    """Drain one process stream while retaining only a bounded byte prefix."""

    content: bytearray = field(default_factory=bytearray)
    truncated: bool = False
    failure: Exception | None = None

    def read(self, stream) -> None:
        """Drain bytes without retaining content beyond the configured limit."""

        try:
            while chunk := stream.read(8192):
                remaining = MAX_GIT_OUTPUT_BYTES - len(self.content)
                if remaining > 0:
                    self.content.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    self.truncated = True
        except Exception as error:
            self.failure = error
        finally:
            try:
                stream.close()
            except Exception:
                pass


def _terminate_process_group(process) -> None:
    """Terminate and reap one isolated Git process group."""

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass

    try:
        process.wait(timeout=1)
        return
    except subprocess.TimeoutExpired:
        pass

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass

    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        raise ConfigurationError("unable to terminate Git command.") from None
