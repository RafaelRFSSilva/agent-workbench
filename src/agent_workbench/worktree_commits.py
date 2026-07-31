"""Validated planning for approved commits inside isolated Git worktrees."""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
import difflib
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import signal
import stat
import subprocess
import threading
from typing import cast

from agent_workbench.errors import CompletionError, ConfigurationError
from agent_workbench.line_changes import count_changed_lines
from agent_workbench.recovery import (
    IsolatedCommitRecoveryEvidence,
    IsolatedCommitRecoveryPhase,
    RecoveryStatus,
)
from agent_workbench.tools import (
    JSONObject,
    JSONValue,
    ToolApprovalDecision,
)
from agent_workbench.worktrees import WorktreeHandle, inspect_git_worktree

MAX_COMMIT_FILE_BYTES = 100 * 1024
"""Maximum old or current bytes accepted for one committed file."""

MAX_COMMIT_FILE_CHANGED_LINES = 500
"""Maximum removed and added lines accepted for one committed file."""

MAX_COMMIT_FILES = 32
"""Maximum number of paths accepted by one isolated commit."""

MAX_COMMIT_OLD_BYTES = 1024 * 1024
"""Maximum combined old tracked bytes accepted by one isolated commit."""

MAX_COMMIT_CURRENT_BYTES = 1024 * 1024
"""Maximum combined current bytes accepted by one isolated commit."""

MAX_COMMIT_CHANGED_LINES = 4_000
"""Maximum combined removed and added lines accepted by one commit."""

MAX_COMMIT_PREVIEW_BYTES = 512 * 1024
"""Maximum encoded complete approval preview size."""

MAX_COMMIT_MESSAGE_BYTES = 4 * 1024
"""Maximum exact UTF-8 commit-message size."""

GIT_COMMIT_TIMEOUT_SECONDS = 5
"""Maximum duration for one fixed planning Git command."""

MAX_COMMIT_GIT_OUTPUT_BYTES = 2 * 1024 * 1024
"""Maximum retained bytes for each Git output stream."""

_SAFE_GIT_CONFIG = (
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
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
_ZERO_OBJECT_IDS = {"0" * 40, "0" * 64}


@dataclass(frozen=True, slots=True)
class _CommitChange:
    """Store one immutable exact changed-file snapshot."""

    path: str
    operation: str
    old_content: bytes = field(repr=False)
    current_content: bytes = field(repr=False)
    old_mode: int | None
    current_mode: int
    changed_lines: int
    diff: str = field(repr=False)

    @property
    def old_size_bytes(self) -> int:
        """Return the exact old byte count."""

        return len(self.old_content)

    @property
    def new_size_bytes(self) -> int:
        """Return the exact current byte count."""

        return len(self.current_content)

    def preview(self) -> JSONObject:
        """Return deterministic safe complete change metadata."""

        return {
            "path": self.path,
            "operation": self.operation,
            "old_size_bytes": self.old_size_bytes,
            "new_size_bytes": self.new_size_bytes,
            "changed_lines": self.changed_lines,
            "diff": self.diff,
        }


@dataclass(frozen=True, slots=True, init=False)
class IsolatedCommitPlan:
    """Pin one completely validated isolated local commit request."""

    worktree: WorktreeHandle = field(repr=False)
    old_head: str
    source_head: str
    source_branch: str
    branch_name: str
    commit_message: str = field(repr=False)
    paths: tuple[str, ...]
    operation_count: int
    added_count: int
    modified_count: int
    total_old_size_bytes: int
    total_new_size_bytes: int
    total_changed_lines: int
    diff_fingerprint: str
    _author_name: str = field(repr=False)
    _author_email: str = field(repr=False)
    _changes: tuple[_CommitChange, ...] = field(repr=False)
    _preview_json: str = field(repr=False)

    def __init__(self) -> None:
        """Prevent callers from constructing an unvalidated plan."""

        raise ConfigurationError(
            "isolated commit plans must be created by plan_isolated_commit."
        )

    @classmethod
    def _validated(
        cls,
        *,
        worktree: WorktreeHandle,
        old_head: str,
        source_head: str,
        source_branch: str,
        branch_name: str,
        commit_message: str,
        author_name: str,
        author_email: str,
        changes: tuple[_CommitChange, ...],
        diff_fingerprint: str,
        preview_json: str,
    ) -> "IsolatedCommitPlan":
        """Construct one plan from values fully validated in this module."""

        plan = object.__new__(cls)
        added_count = sum(change.operation == "add" for change in changes)
        modified_count = sum(change.operation == "modify" for change in changes)
        values = {
            "worktree": worktree,
            "old_head": old_head,
            "source_head": source_head,
            "source_branch": source_branch,
            "branch_name": branch_name,
            "commit_message": commit_message,
            "paths": tuple(change.path for change in changes),
            "operation_count": len(changes),
            "added_count": added_count,
            "modified_count": modified_count,
            "total_old_size_bytes": sum(change.old_size_bytes for change in changes),
            "total_new_size_bytes": sum(change.new_size_bytes for change in changes),
            "total_changed_lines": sum(change.changed_lines for change in changes),
            "diff_fingerprint": diff_fingerprint,
            "_author_name": author_name,
            "_author_email": author_email,
            "_changes": changes,
            "_preview_json": preview_json,
        }
        for name, value in values.items():
            object.__setattr__(plan, name, value)
        return plan

    @property
    def preview(self) -> JSONObject:
        """Return an independent copy of the complete safe approval preview."""

        return cast(JSONObject, json.loads(self._preview_json))


class IsolatedCommitAction(StrEnum):
    """Identify one operator-side isolated commit action."""

    CREATE = "create_isolated_commit"


@dataclass(frozen=True, slots=True, init=False)
class IsolatedCommitApprovalRequest:
    """Provide one immutable exact isolated-commit preview for approval."""

    action: IsolatedCommitAction
    _preview_json: str = field(repr=False)

    def __init__(
        self,
        action: IsolatedCommitAction,
        preview: JSONValue,
    ) -> None:
        """Validate and snapshot one strict-JSON approval preview."""

        if not isinstance(action, IsolatedCommitAction):
            raise ConfigurationError(
                "isolated commit approval action must be an IsolatedCommitAction."
            )
        try:
            preview_json = json.dumps(
                preview,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError):
            raise ConfigurationError(
                "isolated commit approval preview must be strict JSON."
            ) from None
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "_preview_json", preview_json)

    @property
    def preview(self) -> JSONObject:
        """Return an independent copy of the exact approval preview."""

        return cast(JSONObject, json.loads(self._preview_json))


type IsolatedCommitApprovalHandler = Callable[
    [IsolatedCommitApprovalRequest],
    ToolApprovalDecision,
]


@dataclass(frozen=True, slots=True)
class IsolatedCommitResult:
    """Return safe verified metadata for one created local commit."""

    branch_name: str
    old_head: str
    new_head: str
    commit_message: str
    paths: tuple[str, ...]
    operation_count: int
    added_count: int
    modified_count: int


@dataclass(frozen=True, slots=True)
class _GitOutput:
    """Store bounded exact output from one fixed Git command."""

    returncode: int
    stdout: bytes
    stderr: bytes


@dataclass(slots=True)
class _BoundedCapture:
    """Drain one process stream while retaining a bounded byte prefix."""

    content: bytearray = field(default_factory=bytearray)
    truncated: bool = False
    failure: Exception | None = None

    def read(self, stream) -> None:
        """Drain one stream without retaining bytes beyond the safe limit."""

        try:
            while chunk := stream.read(8192):
                remaining = MAX_COMMIT_GIT_OUTPUT_BYTES - len(self.content)
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


def plan_isolated_commit(
    worktree: WorktreeHandle,
    commit_message: str,
    *,
    expected_paths: Iterable[str] | None = None,
) -> IsolatedCommitPlan:
    """Validate and snapshot every eligible change in one isolated worktree."""

    if not isinstance(worktree, WorktreeHandle):
        raise ConfigurationError(
            "isolated commit planning requires a verified WorktreeHandle."
        )
    if worktree.worktree_path == worktree.source_repository:
        raise ConfigurationError(
            "isolated commit planning cannot use the primary working tree."
        )

    message = _validate_commit_message(commit_message)
    safe_expected_paths = _snapshot_expected_paths(expected_paths)
    state = _inspect_commit_worktree(worktree)
    author_name = _read_local_identity(worktree.worktree_path, "user.name")
    author_email = _read_local_identity(worktree.worktree_path, "user.email")
    _reject_in_progress_operation(worktree.worktree_path)
    _require_clean_index(worktree.worktree_path)
    changes = _collect_changes(
        worktree.worktree_path,
        state.head,
        expected_paths=safe_expected_paths,
    )
    _validate_complete_limits(changes)

    fingerprint = _fingerprint(
        worktree=worktree,
        old_head=state.head,
        source_branch=state.source_branch,
        commit_message=message,
        author_name=author_name,
        author_email=author_email,
        changes=changes,
    )
    preview = _build_preview(
        branch_name=worktree.branch_name,
        old_head=state.head,
        commit_message=message,
        changes=changes,
        fingerprint=fingerprint,
    )
    preview_json = _serialize_preview(preview)

    return IsolatedCommitPlan._validated(
        worktree=worktree,
        old_head=state.head,
        source_head=worktree.source_head,
        source_branch=state.source_branch,
        branch_name=worktree.branch_name,
        commit_message=message,
        author_name=author_name,
        author_email=author_email,
        changes=changes,
        diff_fingerprint=fingerprint,
        preview_json=preview_json,
    )


def create_isolated_commit(
    plan: IsolatedCommitPlan,
    approval_handler: IsolatedCommitApprovalHandler | None,
) -> IsolatedCommitResult:
    """Stage, commit, and verify one exact approved isolated plan."""

    if not isinstance(plan, IsolatedCommitPlan):
        raise ConfigurationError(
            "isolated commit creation requires an IsolatedCommitPlan."
        )
    _require_commit_approval(plan, approval_handler)

    try:
        current_plan = plan_isolated_commit(
            plan.worktree,
            plan.commit_message,
            expected_paths=plan.paths,
        )
    except ConfigurationError:
        raise CompletionError(
            "Isolated commit plan is stale; no paths were staged and no "
            "commit was created."
        ) from None
    if current_plan != plan:
        raise CompletionError(
            "Isolated commit plan is stale; no paths were staged and no "
            "commit was created."
        )

    try:
        stage_outcome = _run_git(
            plan.worktree.worktree_path,
            ("add", "--", *plan.paths),
        )
    except ConfigurationError:
        raise CompletionError(
            _commit_failure_message(
                plan,
                "Exact staging failed",
                IsolatedCommitRecoveryPhase.EXACT_STAGING,
            )
        ) from None
    if stage_outcome.returncode != 0:
        raise CompletionError(
            _commit_failure_message(
                plan,
                "Exact staging failed",
                IsolatedCommitRecoveryPhase.EXACT_STAGING,
            )
        )

    try:
        _verify_staged_plan(plan)
    except (CompletionError, ConfigurationError):
        raise CompletionError(
            _commit_failure_message(
                plan,
                "Staged-state verification failed",
                IsolatedCommitRecoveryPhase.STAGED_STATE_VERIFICATION,
            )
        ) from None

    commit_arguments = (
        "-c",
        "commit.gpgSign=false",
        "-c",
        "tag.gpgSign=false",
        "-c",
        "core.editor=false",
        "commit",
        "--no-verify",
        "--no-gpg-sign",
        "--cleanup=verbatim",
        "--file=-",
    )
    try:
        commit_outcome = _run_git(
            plan.worktree.worktree_path,
            commit_arguments,
            input_bytes=plan.commit_message.encode("utf-8"),
        )
    except ConfigurationError:
        raise CompletionError(
            _commit_failure_message(
                plan,
                "Local commit creation failed",
                IsolatedCommitRecoveryPhase.LOCAL_COMMIT_CREATION,
            )
        ) from None
    if commit_outcome.returncode != 0:
        raise CompletionError(
            _commit_failure_message(
                plan,
                "Local commit creation failed",
                IsolatedCommitRecoveryPhase.LOCAL_COMMIT_CREATION,
            )
        )

    try:
        new_head = _read_head(plan.worktree.worktree_path)
        _verify_created_commit(plan, new_head)
    except (CompletionError, ConfigurationError):
        raise CompletionError(
            _commit_failure_message(
                plan,
                "Commit verification failed",
                IsolatedCommitRecoveryPhase.COMMIT_VERIFICATION,
            )
        ) from None

    return IsolatedCommitResult(
        branch_name=plan.branch_name,
        old_head=plan.old_head,
        new_head=new_head,
        commit_message=plan.commit_message,
        paths=plan.paths,
        operation_count=plan.operation_count,
        added_count=plan.added_count,
        modified_count=plan.modified_count,
    )


def _require_commit_approval(
    plan: IsolatedCommitPlan,
    approval_handler: IsolatedCommitApprovalHandler | None,
) -> None:
    """Require one exact explicit decision before any index mutation."""

    if approval_handler is None:
        raise CompletionError("Isolated commit creation requires explicit approval.")
    request = IsolatedCommitApprovalRequest(
        IsolatedCommitAction.CREATE,
        plan.preview,
    )
    try:
        decision = approval_handler(request)
    except Exception:
        raise CompletionError("Unable to obtain isolated commit approval.") from None
    if decision is ToolApprovalDecision.DENY:
        raise CompletionError("Isolated commit approval was denied.")
    if decision is not ToolApprovalDecision.APPROVE:
        raise CompletionError("Isolated commit approval decision is invalid.")


@dataclass(frozen=True, slots=True)
class _CommitWorktreeState:
    """Store verified identities required for immutable planning."""

    head: str
    source_branch: str


def _inspect_commit_worktree(worktree: WorktreeHandle) -> _CommitWorktreeState:
    """Revalidate linked-worktree identity and capture primary branch state."""

    try:
        state = inspect_git_worktree(worktree)
    except ConfigurationError:
        raise ConfigurationError(
            "isolated commit source repository or handle is invalid."
        ) from None
    except CompletionError:
        raise ConfigurationError(
            "isolated commit worktree identity or branch is invalid."
        ) from None
    if (
        not state.registered
        or state.branch_name != worktree.branch_name
        or worktree.worktree_path == worktree.source_repository
    ):
        raise ConfigurationError(
            "isolated commit worktree registration or branch is invalid."
        )

    top_level = _run_git(
        worktree.worktree_path,
        ("rev-parse", "--show-toplevel"),
    )
    if top_level.returncode != 0:
        raise ConfigurationError("isolated commit worktree is unavailable.")
    try:
        reported = Path(_decode_line(top_level.stdout, "worktree top-level")).resolve(
            strict=True
        )
    except (FileNotFoundError, OSError, RuntimeError):
        raise ConfigurationError("isolated commit worktree is unavailable.") from None
    if reported != worktree.worktree_path:
        raise ConfigurationError("isolated commit worktree identity is invalid.")

    branch = _read_symbolic_branch(worktree.worktree_path)
    if branch != worktree.branch_name:
        raise ConfigurationError("isolated commit worktree branch is invalid.")
    head = _read_head(worktree.worktree_path)
    if head != state.head:
        raise ConfigurationError("isolated commit worktree HEAD is ambiguous.")

    source_branch = _read_symbolic_branch(worktree.source_repository)
    source_head = _read_head(worktree.source_repository)
    if source_head != worktree.source_head:
        raise ConfigurationError("isolated commit source HEAD has changed.")
    _reject_in_progress_operation(worktree.source_repository)
    _require_no_upstream(worktree.worktree_path)
    return _CommitWorktreeState(head=head, source_branch=source_branch)


def _validate_commit_message(message: object) -> str:
    """Validate an exact bounded message without rewriting it."""

    if not isinstance(message, str):
        raise ConfigurationError("commit message must be a string.")
    if not message.strip():
        raise ConfigurationError("commit message must not be blank.")
    if message.startswith("-"):
        raise ConfigurationError("commit message must not begin with '-'.")
    if "\0" in message:
        raise ConfigurationError("commit message must not contain NUL.")
    try:
        encoded = message.encode("utf-8")
    except UnicodeEncodeError:
        raise ConfigurationError("commit message must be valid UTF-8.") from None
    if len(encoded) > MAX_COMMIT_MESSAGE_BYTES:
        raise ConfigurationError(
            f"commit message exceeds the {MAX_COMMIT_MESSAGE_BYTES}-byte limit."
        )
    return message


def _query_local_identity(repository: Path, key: str) -> str | None:
    """Return a valid identity string, None if absent or blank, or raise on error.

    Return None:  key not found (exit 1) or the decoded value is empty.
    Return str:   non-empty value with no control characters.
    Raise:        non-1 Git failure, invalid UTF-8, malformed output,
                  or a value containing control characters.
    """

    output = _run_git(repository, ("config", "--local", "--get", key))
    if output.returncode == 1:
        return None  # key absent
    if output.returncode != 0:
        raise ConfigurationError(
            "repository-local identity configuration returned an unexpected error."
        )
    value = _decode_line(output.stdout, "repository-local identity")
    if not value:
        return None  # blank value treated as absent
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ConfigurationError(
            "repository-local author identity is missing or invalid."
        )
    return value


def _read_local_identity(repository: Path, key: str) -> str:
    """Return one safe exact repository-local identity value."""

    value = _query_local_identity(repository, key)
    if value is None:
        raise ConfigurationError(
            "isolated commit requires repository-local author identity."
        )
    return value


_IDENTITY_EXAMPLES: dict[str, str] = {
    "user.name": '"Your Name"',
    "user.email": '"you@example.com"',
}


def require_local_author_identity(workspace: Path) -> None:
    """Raise ConfigurationError early if repository-local Git identity is missing.

    Checks only local config; global and system identity are never accepted.
    Absent or blank fields are aggregated into one actionable error message.
    Operational failures and invalid data (UTF-8, control characters) propagate.
    """

    missing: list[str] = []
    for key in ("user.name", "user.email"):
        value = _query_local_identity(workspace, key)  # raises on error; None if absent
        if value is None:
            missing.append(key)

    if not missing:
        return

    fields = " and ".join(missing)
    quoted = shlex.quote(str(workspace))
    configure_lines = "\n".join(
        f"    git -C {quoted} config --local {key} {_IDENTITY_EXAMPLES[key]}"
        for key in missing
    )
    raise ConfigurationError(
        f"isolated commit requires repository-local Git author identity; "
        f"missing or blank: {fields}. Configure with:\n{configure_lines}"
    )


def _read_symbolic_branch(repository: Path) -> str:
    """Return one attached local branch or reject detached state."""

    output = _run_git(
        repository,
        ("symbolic-ref", "--quiet", "--short", "HEAD"),
    )
    if output.returncode != 0:
        raise ConfigurationError(
            "isolated commit requires the expected attached local branch."
        )
    branch = _decode_line(output.stdout, "Git branch")
    if not branch:
        raise ConfigurationError(
            "isolated commit requires the expected attached local branch."
        )
    return branch


def _read_head(repository: Path) -> str:
    """Return one complete verified commit identifier."""

    output = _run_git(
        repository,
        ("rev-parse", "--verify", "HEAD^{commit}"),
    )
    head = _decode_line(output.stdout, "Git HEAD") if output.returncode == 0 else ""
    if not _is_full_object_id(head):
        raise ConfigurationError("isolated commit requires a valid HEAD.")
    return head


def _require_no_upstream(worktree: Path) -> None:
    """Require the isolated branch to remain local-only."""

    output = _run_git(
        worktree,
        (
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}",
        ),
    )
    if output.returncode == 0:
        raise ConfigurationError("isolated commit branch must not have an upstream.")
    if output.returncode not in (1, 128):
        raise ConfigurationError("unable to inspect isolated commit upstream.")


def _reject_in_progress_operation(repository: Path) -> None:
    """Reject known in-progress Git operation state without reading it."""

    for name in _IN_PROGRESS_GIT_PATHS:
        output = _run_git(repository, ("rev-parse", "--git-path", name))
        if output.returncode != 0:
            raise ConfigurationError("unable to inspect Git operation state.")
        try:
            git_path = Path(_decode_line(output.stdout, "Git operation path"))
            if not git_path.is_absolute():
                git_path = repository / git_path
            if os.path.lexists(git_path):
                raise ConfigurationError(
                    "isolated commit cannot run during a Git operation."
                )
        except OSError:
            raise ConfigurationError("unable to inspect Git operation state.") from None


def _require_clean_index(worktree: Path) -> None:
    """Reject every pre-existing staged, intent-to-add, or unmerged entry."""

    staged = _run_git(
        worktree,
        ("diff", "--cached", "--quiet", "--no-ext-diff", "--"),
    )
    if staged.returncode == 1:
        raise ConfigurationError(
            "isolated commit requires a completely clean real index."
        )
    if staged.returncode != 0:
        raise ConfigurationError("unable to inspect the isolated commit index.")

    entries = _run_git(worktree, ("ls-files", "--stage", "-z"))
    if entries.returncode != 0:
        raise ConfigurationError("unable to inspect the isolated commit index.")
    for entry in entries.stdout.split(b"\0"):
        if not entry:
            continue
        metadata, separator, _path = entry.partition(b"\t")
        parts = metadata.split()
        if not separator or len(parts) != 3:
            raise ConfigurationError("isolated commit index state is invalid.")
        try:
            object_id = parts[1].decode("ascii")
            stage = int(parts[2])
        except (UnicodeDecodeError, ValueError):
            raise ConfigurationError(
                "isolated commit index state is invalid."
            ) from None
        if object_id in _ZERO_OBJECT_IDS or stage != 0:
            raise ConfigurationError(
                "isolated commit requires a completely clean real index."
            )


def _collect_changes(
    worktree: Path,
    old_head: str,
    *,
    expected_paths: tuple[str, ...] | None,
) -> tuple[_CommitChange, ...]:
    """Collect every eligible working-tree change in deterministic order."""

    ignored = _run_git(
        worktree,
        ("ls-files", "--others", "--ignored", "--exclude-standard", "-z"),
    )
    if ignored.returncode != 0:
        raise ConfigurationError("unable to inspect ignored worktree paths.")
    if ignored.stdout:
        raise ConfigurationError(
            "isolated commit planning rejects ignored worktree files."
        )

    status_output = _run_git(
        worktree,
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"),
    )
    if status_output.returncode != 0:
        raise ConfigurationError("unable to inspect isolated commit changes.")
    status_entries = _parse_status(status_output.stdout)
    _reject_unreported_unsafe_entries(
        worktree,
        tuple(raw_path for _status, raw_path in status_entries),
    )
    if not status_entries:
        raise ConfigurationError(
            "isolated commit requires at least one eligible change."
        )

    resolved_entries = tuple(
        (status_code, *_resolve_changed_path(worktree, raw_path))
        for status_code, raw_path in status_entries
    )
    observed_paths = tuple(
        sorted(relative_path for _status, relative_path, _target in resolved_entries)
    )
    if expected_paths is not None and observed_paths != expected_paths:
        raise ConfigurationError(
            "isolated commit contains a changed path outside the successful "
            "approved workspace actions."
        )

    changes = []
    canonical_paths: set[Path] = set()
    for status_code, relative_path, target in resolved_entries:
        canonical = target.resolve(strict=True)
        if canonical in canonical_paths:
            raise ConfigurationError("isolated commit paths are ambiguous.")
        canonical_paths.add(canonical)
        if status_code == "??":
            change = _prepare_added_change(relative_path, target)
        elif status_code == " M":
            change = _prepare_modified_change(
                worktree,
                old_head,
                relative_path,
                target,
            )
        else:
            raise ConfigurationError(
                "isolated commit contains an unsupported change type."
            )
        changes.append(change)
    return tuple(sorted(changes, key=lambda change: change.path))


def _snapshot_expected_paths(
    paths: Iterable[str] | None,
) -> tuple[str, ...] | None:
    """Validate and snapshot an optional exact controller-approved path set."""

    if paths is None:
        return None
    if isinstance(paths, str):
        raise ConfigurationError(
            "isolated commit expected paths must be an iterable of paths."
        )
    try:
        values = tuple(paths)
    except TypeError:
        raise ConfigurationError(
            "isolated commit expected paths must be an iterable of paths."
        ) from None

    validated = []
    for path in values:
        if (
            not isinstance(path, str)
            or not path
            or any(ord(character) < 32 or ord(character) == 127 for character in path)
        ):
            raise ConfigurationError("isolated commit expected path is unsafe.")
        pure = PurePosixPath(path)
        if (
            pure.is_absolute()
            or pure.as_posix() != path
            or not pure.parts
            or any(part in ("", ".", "..") for part in pure.parts)
            or ".git" in pure.parts
            or pure.parts[0].startswith(("-", ":"))
        ):
            raise ConfigurationError("isolated commit expected path is unsafe.")
        validated.append(path)
    if len(validated) != len(set(validated)):
        raise ConfigurationError("isolated commit expected paths contain duplicates.")
    return tuple(sorted(validated))


def _verify_staged_plan(plan: IsolatedCommitPlan) -> None:
    """Require the real index and staged content to equal the approved plan."""

    _verify_plan_identity(
        plan,
        expected_worktree_head=plan.old_head,
    )
    status_output = _run_git(
        plan.worktree.worktree_path,
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"),
    )
    if status_output.returncode != 0:
        raise CompletionError("Unable to inspect staged isolated commit state.")
    staged_status = _parse_expected_staged_status(status_output.stdout)
    if tuple(sorted(staged_status)) != plan.paths:
        raise CompletionError("Staged path set differs from the approved plan.")

    actual_changes = tuple(
        _read_staged_change(
            plan.worktree.worktree_path,
            change,
            staged_status[change.path],
        )
        for change in plan._changes
    )
    if actual_changes != plan._changes:
        raise CompletionError("Staged content differs from the approved plan.")

    author_name = _read_local_identity(
        plan.worktree.worktree_path,
        "user.name",
    )
    author_email = _read_local_identity(
        plan.worktree.worktree_path,
        "user.email",
    )
    fingerprint = _fingerprint(
        worktree=plan.worktree,
        old_head=plan.old_head,
        source_branch=plan.source_branch,
        commit_message=plan.commit_message,
        author_name=author_name,
        author_email=author_email,
        changes=actual_changes,
    )
    if fingerprint != plan.diff_fingerprint:
        raise CompletionError("Staged diff fingerprint differs from approval.")


def _parse_expected_staged_status(output: bytes) -> dict[str, str]:
    """Return exact staged paths while rejecting unstaged or unexpected state."""

    staged: dict[str, str] = {}
    for entry in output.split(b"\0"):
        if not entry:
            continue
        if len(entry) < 4 or entry[2:3] != b" ":
            raise CompletionError("Staged Git status is invalid.")
        try:
            status_code = entry[:2].decode("ascii")
            path = entry[3:].decode("utf-8")
        except UnicodeDecodeError:
            raise CompletionError("Staged Git status is invalid.") from None
        if status_code not in ("A ", "M "):
            raise CompletionError(
                "Staged state contains an unsupported or unstaged change."
            )
        if path in staged:
            raise CompletionError("Staged path set is ambiguous.")
        staged[path] = status_code[0]
    return staged


def _read_staged_change(
    worktree: Path,
    approved: _CommitChange,
    staged_status: str,
) -> _CommitChange:
    """Reconstruct one exact staged change using real index objects."""

    expected_status = "A" if approved.operation == "add" else "M"
    if staged_status != expected_status:
        raise CompletionError("Staged operation differs from the approved plan.")
    mode, object_id = _read_index_entry(worktree, approved.path)
    content_output = _run_git(worktree, ("cat-file", "blob", object_id))
    if content_output.returncode != 0:
        raise CompletionError("Unable to read staged commit content.")
    current_content = content_output.stdout
    current_text = _decode_file_content(current_content)
    old_text = _decode_file_content(approved.old_content)
    changed_lines = count_changed_lines(old_text, current_text)
    operation = approved.operation
    return _CommitChange(
        path=approved.path,
        operation=operation,
        old_content=approved.old_content,
        current_content=current_content,
        old_mode=approved.old_mode,
        current_mode=mode,
        changed_lines=changed_lines,
        diff=_create_unified_diff(
            approved.path,
            old_text,
            current_text,
            operation=operation,
        ),
    )


def _verify_plan_identity(
    plan: IsolatedCommitPlan,
    *,
    expected_worktree_head: str,
) -> None:
    """Verify source, worktree, branch, operation, and upstream identities."""

    try:
        state = inspect_git_worktree(plan.worktree)
    except (CompletionError, ConfigurationError):
        raise CompletionError("Isolated commit worktree identity changed.") from None
    if (
        state.branch_name != plan.branch_name
        or state.head != expected_worktree_head
        or _read_symbolic_branch(plan.worktree.worktree_path) != plan.branch_name
        or _read_head(plan.worktree.worktree_path) != expected_worktree_head
    ):
        raise CompletionError("Isolated commit worktree identity changed.")
    if (
        _read_symbolic_branch(plan.worktree.source_repository) != plan.source_branch
        or _read_head(plan.worktree.source_repository) != plan.source_head
    ):
        raise CompletionError("Primary source identity changed.")
    _reject_in_progress_operation(plan.worktree.source_repository)
    _reject_in_progress_operation(plan.worktree.worktree_path)
    _require_no_upstream(plan.worktree.worktree_path)


def _verify_created_commit(
    plan: IsolatedCommitPlan,
    new_head: str,
) -> None:
    """Verify parent, message, paths, blobs, index, worktree, and source."""

    if new_head == plan.old_head:
        raise CompletionError("Isolated commit HEAD did not advance.")
    _verify_plan_identity(
        plan,
        expected_worktree_head=new_head,
    )

    parents = _run_git(
        plan.worktree.worktree_path,
        ("rev-list", "--parents", "-n", "1", new_head),
    )
    try:
        parent_fields = parents.stdout.decode("ascii").strip().split()
    except UnicodeDecodeError:
        raise CompletionError("Created commit parent state is invalid.") from None
    if parents.returncode != 0 or parent_fields != [new_head, plan.old_head]:
        raise CompletionError("Created commit must have exactly the old HEAD parent.")

    commit_object = _run_git(
        plan.worktree.worktree_path,
        ("cat-file", "commit", new_head),
    )
    separator = commit_object.stdout.find(b"\n\n")
    if commit_object.returncode != 0 or separator < 0:
        raise CompletionError("Unable to inspect the created commit message.")
    stored_message = commit_object.stdout[separator + 2 :]
    approved_message = plan.commit_message.encode("utf-8")
    if stored_message not in (approved_message, approved_message + b"\n"):
        raise CompletionError("Created commit message differs from approval.")

    committed_status = _read_committed_status(
        plan.worktree.worktree_path,
        new_head,
    )
    expected_status = {
        change.path: ("A" if change.operation == "add" else "M")
        for change in plan._changes
    }
    if committed_status != expected_status:
        raise CompletionError("Created commit path set differs from approval.")

    committed_changes = tuple(
        _read_committed_change(
            plan.worktree.worktree_path,
            new_head,
            change,
        )
        for change in plan._changes
    )
    if committed_changes != plan._changes:
        raise CompletionError("Created commit diff differs from approval.")

    _require_clean_index(plan.worktree.worktree_path)
    status = _run_git(
        plan.worktree.worktree_path,
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"),
    )
    if status.returncode != 0 or status.stdout:
        raise CompletionError("Created commit did not leave a clean worktree.")


def _read_committed_status(worktree: Path, commit: str) -> dict[str, str]:
    """Return exact add/modify status for one created commit."""

    output = _run_git(
        worktree,
        (
            "diff-tree",
            "--no-commit-id",
            "--name-status",
            "--no-renames",
            "-r",
            "-z",
            commit,
        ),
    )
    if output.returncode != 0:
        raise CompletionError("Unable to inspect created commit paths.")
    fields = [field for field in output.stdout.split(b"\0") if field]
    committed: dict[str, str] = {}
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if b"\t" in field:
            status_bytes, path_bytes = field.split(b"\t", 1)
        else:
            if index >= len(fields):
                raise CompletionError("Created commit path status is invalid.")
            status_bytes = field
            path_bytes = fields[index]
            index += 1
        try:
            status_code = status_bytes.decode("ascii")
            path = path_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raise CompletionError("Created commit path status is invalid.") from None
        if status_code not in ("A", "M") or path in committed:
            raise CompletionError("Created commit contains an unsupported path.")
        committed[path] = status_code
    return committed


def _read_committed_change(
    worktree: Path,
    commit: str,
    approved: _CommitChange,
) -> _CommitChange:
    """Reconstruct one committed tree entry and complete unified diff."""

    mode, object_id = _read_tree_entry(worktree, commit, approved.path)
    content_output = _run_git(worktree, ("cat-file", "blob", object_id))
    if content_output.returncode != 0:
        raise CompletionError("Unable to read created commit content.")
    current_content = content_output.stdout
    old_text = _decode_file_content(approved.old_content)
    current_text = _decode_file_content(current_content)
    return _CommitChange(
        path=approved.path,
        operation=approved.operation,
        old_content=approved.old_content,
        current_content=current_content,
        old_mode=approved.old_mode,
        current_mode=mode,
        changed_lines=count_changed_lines(old_text, current_text),
        diff=_create_unified_diff(
            approved.path,
            old_text,
            current_text,
            operation=approved.operation,
        ),
    )


def _read_tree_entry(
    worktree: Path,
    commit: str,
    relative_path: str,
) -> tuple[int, str]:
    """Return one exact tree mode and blob id."""

    output = _run_git(
        worktree,
        ("ls-tree", "-z", commit, "--", relative_path),
    )
    entries = [entry for entry in output.stdout.split(b"\0") if entry]
    if output.returncode != 0 or len(entries) != 1:
        raise CompletionError("Created commit tree entry is invalid.")
    metadata, separator, path = entries[0].partition(b"\t")
    parts = metadata.split()
    if not separator or path != relative_path.encode() or len(parts) != 3:
        raise CompletionError("Created commit tree entry is invalid.")
    try:
        mode = int(parts[0], 8)
        entry_type = parts[1].decode("ascii")
        object_id = parts[2].decode("ascii")
    except (UnicodeDecodeError, ValueError):
        raise CompletionError("Created commit tree entry is invalid.") from None
    if entry_type != "blob" or not _is_full_object_id(object_id):
        raise CompletionError("Created commit tree entry is unsupported.")
    return mode, object_id


def _collect_commit_recovery_evidence(
    plan: IsolatedCommitPlan,
    phase: IsolatedCommitRecoveryPhase,
) -> IsolatedCommitRecoveryEvidence:
    """Collect bounded read-only Git evidence after an isolated-commit failure."""

    observed_head: str | None = None
    observed_branch: str | None = None
    index_dirty = RecoveryStatus.UNKNOWN
    worktree_dirty = RecoveryStatus.UNKNOWN
    staged_paths: tuple[str, ...] = ()

    try:
        observed_head = _read_head(plan.worktree.worktree_path)
    except ConfigurationError:
        pass

    try:
        observed_branch = _read_symbolic_branch(plan.worktree.worktree_path)
    except ConfigurationError:
        pass

    try:
        staged = _run_git(
            plan.worktree.worktree_path,
            ("diff", "--cached", "--name-only", "-z", "--"),
        )
        if staged.returncode == 0:
            decoded_paths: list[str] = []
            paths_are_safe = True

            for raw_path in staged.stdout.split(b"\0"):
                if not raw_path:
                    continue
                try:
                    relative_path, _target = _resolve_changed_path(
                        plan.worktree.worktree_path,
                        raw_path,
                    )
                except ConfigurationError:
                    paths_are_safe = False
                    break
                decoded_paths.append(relative_path)

            if paths_are_safe:
                staged_paths = tuple(sorted(decoded_paths))
                index_dirty = RecoveryStatus.YES if staged_paths else RecoveryStatus.NO
    except ConfigurationError:
        pass

    try:
        status = _run_git(
            plan.worktree.worktree_path,
            ("status", "--porcelain=v1", "-z", "--untracked-files=all"),
        )
        if status.returncode == 0:
            worktree_dirty = RecoveryStatus.YES if status.stdout else RecoveryStatus.NO
    except ConfigurationError:
        pass

    try:
        return IsolatedCommitRecoveryEvidence(
            phase=phase,
            target_display=plan.worktree.target_display,
            expected_branch=plan.branch_name,
            observed_branch=observed_branch,
            expected_head=plan.old_head,
            observed_head=observed_head,
            index_dirty=index_dirty,
            staged_paths=staged_paths,
            worktree_dirty=worktree_dirty,
        )
    except ConfigurationError:
        return IsolatedCommitRecoveryEvidence(
            phase=phase,
            target_display=plan.worktree.target_display,
            expected_branch=plan.branch_name,
            observed_branch=None,
            expected_head=plan.old_head,
            observed_head=None,
            index_dirty=RecoveryStatus.UNKNOWN,
            staged_paths=(),
            worktree_dirty=RecoveryStatus.UNKNOWN,
        )


def _commit_failure_message(
    plan: IsolatedCommitPlan,
    reason: str,
    phase: IsolatedCommitRecoveryPhase,
) -> str:
    """Format bounded structured recovery evidence for manual inspection."""

    evidence = _collect_commit_recovery_evidence(plan, phase)
    branch = evidence.observed_branch or evidence.expected_branch
    staged_display = (
        ", ".join(evidence.staged_paths)
        if evidence.staged_paths
        else "[none or unknown]"
    )

    return (
        f"{reason}; manual inspection is required "
        f"(HEAD changed: {evidence.head_changed.value}; branch: {branch}; "
        f"index dirty: {evidence.index_dirty.value}; "
        f"staged paths: {staged_display}; "
        f"worktree dirty: {evidence.worktree_dirty.value}). "
        "No automatic recovery was attempted."
    )


def _reject_unreported_unsafe_entries(
    worktree: Path,
    reported_paths: tuple[bytes, ...],
) -> None:
    """Cross-check Git status so special or ambiguous paths are never omitted."""

    tracked_output = _run_git(worktree, ("ls-files", "-z"))
    if tracked_output.returncode != 0:
        raise ConfigurationError("unable to enumerate tracked worktree paths.")
    try:
        tracked = {
            path.decode("utf-8") for path in tracked_output.stdout.split(b"\0") if path
        }
        reported = {path.decode("utf-8") for path in reported_paths}
    except UnicodeDecodeError:
        raise ConfigurationError("isolated commit paths must be valid UTF-8.") from None

    try:
        for directory, directory_names, file_names in os.walk(
            worktree,
            followlinks=False,
        ):
            parent = Path(directory)
            if parent == worktree and ".git" in directory_names:
                directory_names.remove(".git")
            for name in tuple(directory_names):
                candidate = parent / name
                relative = candidate.relative_to(worktree).as_posix()
                if candidate.is_symlink():
                    directory_names.remove(name)
                    if relative not in tracked:
                        raise ConfigurationError(
                            "isolated commit contains an unreported unsafe path."
                        )
            for name in file_names:
                candidate = parent / name
                relative = candidate.relative_to(worktree).as_posix()
                if relative == ".git" or relative in tracked or relative in reported:
                    continue
                candidate_status = os.lstat(candidate)
                if not stat.S_ISREG(candidate_status.st_mode):
                    raise ConfigurationError(
                        "isolated commit supports regular files only."
                    )
                raise ConfigurationError(
                    "isolated commit contains an unreported changed path."
                )
    except ConfigurationError:
        raise
    except (OSError, UnicodeError, ValueError):
        raise ConfigurationError(
            "unable to enumerate isolated commit paths safely."
        ) from None


def _parse_status(output: bytes) -> tuple[tuple[str, bytes], ...]:
    """Parse exact NUL-delimited porcelain entries and reject ambiguity."""

    entries = output.split(b"\0")
    parsed = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        if len(entry) < 4 or entry[2:3] != b" ":
            raise ConfigurationError("isolated commit Git status is invalid.")
        try:
            status_code = entry[:2].decode("ascii")
        except UnicodeDecodeError:
            raise ConfigurationError("isolated commit Git status is invalid.") from None
        path = entry[3:]
        if status_code[0] not in (" ", "?"):
            raise ConfigurationError(
                "isolated commit requires a completely clean real index."
            )
        if status_code == " A":
            raise ConfigurationError(
                "isolated commit requires a completely clean real index."
            )
        if status_code not in (" M", "??"):
            if "U" in status_code:
                raise ConfigurationError(
                    "isolated commit rejects conflicted or unmerged paths."
                )
            raise ConfigurationError(
                "isolated commit contains an unsupported change type."
            )
        parsed.append((status_code, path))
    return tuple(parsed)


def _resolve_changed_path(worktree: Path, raw_path: bytes) -> tuple[str, Path]:
    """Return one safe canonical workspace-relative path and target."""

    try:
        decoded = raw_path.decode("utf-8")
    except UnicodeDecodeError:
        raise ConfigurationError("isolated commit paths must be valid UTF-8.") from None
    if not decoded or any(
        ord(character) < 32 or ord(character) == 127 for character in decoded
    ):
        raise ConfigurationError("isolated commit path is unsafe.")
    pure = PurePosixPath(decoded)
    if (
        pure.is_absolute()
        or not pure.parts
        or any(part in ("", ".", "..") for part in pure.parts)
        or ".git" in pure.parts
        or pure.parts[0].startswith(("-", ":"))
    ):
        raise ConfigurationError("isolated commit path is unsafe.")

    target = worktree.joinpath(*pure.parts)
    current = worktree
    for part in pure.parts[:-1]:
        current /= part
        try:
            current_status = os.lstat(current)
        except OSError:
            raise ConfigurationError("isolated commit path is unavailable.") from None
        if not stat.S_ISDIR(current_status.st_mode) or stat.S_ISLNK(
            current_status.st_mode
        ):
            raise ConfigurationError("isolated commit path has an unsafe parent.")
    try:
        target_status = os.lstat(target)
        if stat.S_ISLNK(target_status.st_mode):
            raise ConfigurationError("isolated commit symlinks are unsupported.")
        canonical = target.resolve(strict=True)
        canonical.relative_to(worktree)
    except ConfigurationError:
        raise
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        raise ConfigurationError(
            "isolated commit path is unavailable or external."
        ) from None
    if canonical != target:
        raise ConfigurationError("isolated commit symlinks are unsupported.")
    return pure.as_posix(), target


def _prepare_added_change(relative_path: str, target: Path) -> _CommitChange:
    """Snapshot one new untracked regular UTF-8 file."""

    current_content, current_mode = _read_regular_file(target)
    current_text = _decode_file_content(current_content)
    changed_lines = count_changed_lines("", current_text)
    _validate_file_limits(
        old_content=b"",
        current_content=current_content,
        changed_lines=changed_lines,
    )
    return _CommitChange(
        path=relative_path,
        operation="add",
        old_content=b"",
        current_content=current_content,
        old_mode=None,
        current_mode=current_mode,
        changed_lines=changed_lines,
        diff=_create_unified_diff(
            relative_path,
            "",
            current_text,
            operation="add",
        ),
    )


def _prepare_modified_change(
    worktree: Path,
    old_head: str,
    relative_path: str,
    target: Path,
) -> _CommitChange:
    """Snapshot one modified tracked regular UTF-8 file."""

    old_mode, object_id = _read_index_entry(worktree, relative_path)
    if old_mode not in (0o100644, 0o100755):
        raise ConfigurationError(
            "isolated commit contains an unsupported tracked file type."
        )
    old_output = _run_git(worktree, ("cat-file", "blob", object_id))
    if old_output.returncode != 0:
        raise ConfigurationError("unable to read tracked commit content.")
    old_content = old_output.stdout
    current_content, current_mode = _read_regular_file(target)
    if current_mode != old_mode:
        raise ConfigurationError(
            "isolated commit contains an unsupported mode or executable-bit change."
        )
    old_text = _decode_file_content(old_content)
    current_text = _decode_file_content(current_content)
    if old_content == current_content:
        raise ConfigurationError(
            "isolated commit contains an unsupported mode-only change."
        )
    head_output = _run_git(
        worktree,
        ("rev-parse", "--verify", f"{old_head}:{relative_path}"),
    )
    if (
        head_output.returncode != 0
        or _decode_line(head_output.stdout, "tracked object") != object_id
    ):
        raise ConfigurationError("tracked commit content is ambiguous.")
    changed_lines = count_changed_lines(old_text, current_text)
    _validate_file_limits(
        old_content=old_content,
        current_content=current_content,
        changed_lines=changed_lines,
    )
    return _CommitChange(
        path=relative_path,
        operation="modify",
        old_content=old_content,
        current_content=current_content,
        old_mode=old_mode,
        current_mode=current_mode,
        changed_lines=changed_lines,
        diff=_create_unified_diff(
            relative_path,
            old_text,
            current_text,
            operation="modify",
        ),
    )


def _read_index_entry(worktree: Path, relative_path: str) -> tuple[int, str]:
    """Return one exact stage-zero tracked index entry."""

    output = _run_git(
        worktree,
        ("ls-files", "--stage", "-z", "--", relative_path),
    )
    entries = [entry for entry in output.stdout.split(b"\0") if entry]
    if output.returncode != 0 or len(entries) != 1:
        raise ConfigurationError("tracked commit index entry is invalid.")
    metadata, separator, path = entries[0].partition(b"\t")
    parts = metadata.split()
    if not separator or path != relative_path.encode() or len(parts) != 3:
        raise ConfigurationError("tracked commit index entry is invalid.")
    try:
        mode = int(parts[0], 8)
        object_id = parts[1].decode("ascii")
        stage = int(parts[2])
    except (UnicodeDecodeError, ValueError):
        raise ConfigurationError("tracked commit index entry is invalid.") from None
    if stage != 0 or not _is_full_object_id(object_id):
        raise ConfigurationError("tracked commit index entry is invalid.")
    return mode, object_id


def _read_regular_file(target: Path) -> tuple[bytes, int]:
    """Read one bounded regular file without following it or racing identity."""

    try:
        target_status = os.lstat(target)
    except OSError:
        raise ConfigurationError("isolated commit file is unavailable.") from None
    if not stat.S_ISREG(target_status.st_mode) or stat.S_ISLNK(target_status.st_mode):
        raise ConfigurationError("isolated commit supports regular files only.")
    if target_status.st_size > MAX_COMMIT_FILE_BYTES:
        raise ConfigurationError(
            f"isolated commit file exceeds the {MAX_COMMIT_FILE_BYTES}-byte limit."
        )

    descriptor: int | None = None
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(target, flags)
        opened_status = os.fstat(descriptor)
        if (
            opened_status.st_dev,
            opened_status.st_ino,
        ) != (
            target_status.st_dev,
            target_status.st_ino,
        ):
            raise ConfigurationError("isolated commit file changed while reading.")
        with os.fdopen(descriptor, "rb") as source:
            descriptor = None
            content = source.read(MAX_COMMIT_FILE_BYTES + 1)
    except ConfigurationError:
        raise
    except OSError:
        raise ConfigurationError("unable to read isolated commit file.") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(content) > MAX_COMMIT_FILE_BYTES:
        raise ConfigurationError(
            f"isolated commit file exceeds the {MAX_COMMIT_FILE_BYTES}-byte limit."
        )
    file_mode = stat.S_IMODE(target_status.st_mode)
    git_mode = 0o100755 if file_mode & 0o111 else 0o100644
    return content, git_mode


def _decode_file_content(content: bytes) -> str:
    """Decode strict UTF-8 text and reject NUL bytes."""

    if b"\0" in content:
        raise ConfigurationError("isolated commit files must not contain NUL.")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        raise ConfigurationError(
            "isolated commit files must contain valid UTF-8."
        ) from None


def _validate_file_limits(
    *,
    old_content: bytes,
    current_content: bytes,
    changed_lines: int,
) -> None:
    """Apply exact per-file content and change limits."""

    if (
        len(old_content) > MAX_COMMIT_FILE_BYTES
        or len(current_content) > MAX_COMMIT_FILE_BYTES
    ):
        raise ConfigurationError(
            f"isolated commit file exceeds the {MAX_COMMIT_FILE_BYTES}-byte limit."
        )
    if changed_lines > MAX_COMMIT_FILE_CHANGED_LINES:
        raise ConfigurationError(
            "isolated commit file exceeds the "
            f"{MAX_COMMIT_FILE_CHANGED_LINES}-changed-line limit."
        )


def _validate_complete_limits(changes: tuple[_CommitChange, ...]) -> None:
    """Apply complete-plan path, byte, and line limits."""

    if not changes:
        raise ConfigurationError(
            "isolated commit requires at least one eligible change."
        )
    if len(changes) > MAX_COMMIT_FILES:
        raise ConfigurationError(
            f"isolated commit exceeds the {MAX_COMMIT_FILES}-file limit."
        )
    old_bytes = sum(change.old_size_bytes for change in changes)
    if old_bytes > MAX_COMMIT_OLD_BYTES:
        raise ConfigurationError(
            f"isolated commit exceeds the {MAX_COMMIT_OLD_BYTES}-old-byte limit."
        )
    current_bytes = sum(change.new_size_bytes for change in changes)
    if current_bytes > MAX_COMMIT_CURRENT_BYTES:
        raise ConfigurationError(
            "isolated commit exceeds the "
            f"{MAX_COMMIT_CURRENT_BYTES}-current-byte limit."
        )
    changed_lines = sum(change.changed_lines for change in changes)
    if changed_lines > MAX_COMMIT_CHANGED_LINES:
        raise ConfigurationError(
            "isolated commit exceeds the "
            f"{MAX_COMMIT_CHANGED_LINES}-changed-line limit."
        )


def _create_unified_diff(
    relative_path: str,
    old_content: str,
    new_content: str,
    *,
    operation: str,
) -> str:
    """Return one complete deterministic unified diff."""

    from_file = "/dev/null" if operation == "add" else f"a/{relative_path}"
    lines = difflib.unified_diff(
        old_content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=from_file,
        tofile=f"b/{relative_path}",
        lineterm="\n",
    )
    complete_lines: list[str] = []
    for line in lines:
        complete_lines.append(line)
        if not line.endswith("\n"):
            complete_lines.append("\n\\ No newline at end of file\n")
    return "".join(complete_lines)


def _fingerprint(
    *,
    worktree: WorktreeHandle,
    old_head: str,
    source_branch: str,
    commit_message: str,
    author_name: str,
    author_email: str,
    changes: tuple[_CommitChange, ...],
) -> str:
    """Hash every approved identity, message, mode, byte, and diff snapshot."""

    fingerprint_data = {
        "source_head": worktree.source_head,
        "source_branch": source_branch,
        "old_head": old_head,
        "branch": worktree.branch_name,
        "commit_message": commit_message,
        "author_name": author_name,
        "author_email": author_email,
        "changes": [
            {
                "path": change.path,
                "operation": change.operation,
                "old_sha256": hashlib.sha256(change.old_content).hexdigest(),
                "current_sha256": hashlib.sha256(change.current_content).hexdigest(),
                "old_mode": change.old_mode,
                "current_mode": change.current_mode,
                "changed_lines": change.changed_lines,
                "diff": change.diff,
            }
            for change in changes
        ],
    }
    encoded = json.dumps(
        fingerprint_data,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _build_preview(
    *,
    branch_name: str,
    old_head: str,
    commit_message: str,
    changes: tuple[_CommitChange, ...],
    fingerprint: str,
) -> JSONObject:
    """Build one complete deterministic operator-safe preview."""

    added_count = sum(change.operation == "add" for change in changes)
    modified_count = sum(change.operation == "modify" for change in changes)
    return {
        "action": "create_isolated_commit",
        "branch": branch_name,
        "old_head": old_head,
        "commit_message": commit_message,
        "operation_count": len(changes),
        "added_count": added_count,
        "modified_count": modified_count,
        "total_old_size_bytes": sum(change.old_size_bytes for change in changes),
        "total_new_size_bytes": sum(change.new_size_bytes for change in changes),
        "total_changed_lines": sum(change.changed_lines for change in changes),
        "paths": [change.path for change in changes],
        "changes": [change.preview() for change in changes],
        "diff_fingerprint": fingerprint,
        "command": (
            "git add -- <approved paths> && "
            "git commit --no-verify --no-gpg-sign --file=-"
        ),
        "guarantees": [
            "local isolated branch only",
            "no amend",
            "no merge",
            "no push",
            "no branch deletion",
        ],
    }


def _serialize_preview(preview: JSONObject) -> str:
    """Serialize and bound one complete untruncated preview."""

    preview_json = json.dumps(
        preview,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(preview_json.encode("utf-8")) > MAX_COMMIT_PREVIEW_BYTES:
        raise ConfigurationError(
            "isolated commit complete preview exceeds the "
            f"{MAX_COMMIT_PREVIEW_BYTES}-byte limit."
        )
    return preview_json


def _decode_line(content: bytes, context: str) -> str:
    """Decode one deterministic UTF-8 line without normalizing its value."""

    try:
        decoded = content.decode("utf-8")
    except UnicodeDecodeError:
        raise ConfigurationError(f"{context} is not valid UTF-8.") from None
    if decoded.endswith("\n"):
        decoded = decoded[:-1]
    if "\n" in decoded or "\r" in decoded or "\0" in decoded:
        raise ConfigurationError(f"{context} is invalid.")
    return decoded


def _is_full_object_id(value: str) -> bool:
    """Return whether a value is a complete SHA-1 or SHA-256 object id."""

    return len(value) in (40, 64) and all(
        character in "0123456789abcdef" for character in value
    )


def _git_environment() -> dict[str, str]:
    """Return a fixed local-only Git environment without parent secrets."""

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


def _run_git(
    repository: Path,
    arguments: Sequence[str],
    *,
    input_bytes: bytes | None = None,
) -> _GitOutput:
    """Run one fixed non-shell Git command with bounded exact output."""

    command = [
        "git",
        "-C",
        str(repository),
        *_SAFE_GIT_CONFIG,
        *arguments,
    ]
    try:
        process = subprocess.Popen(
            command,
            cwd=repository,
            env=_git_environment(),
            shell=False,
            stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
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

    if input_bytes is not None:
        if process.stdin is None:
            _terminate_process_group(process)
            raise ConfigurationError("unable to supply Git command input.")
        try:
            process.stdin.write(input_bytes)
            process.stdin.close()
        except OSError:
            _terminate_process_group(process)
            raise ConfigurationError("unable to supply Git command input.") from None

    timed_out = False
    try:
        process.wait(timeout=GIT_COMMIT_TIMEOUT_SECONDS)
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
        stdout=bytes(stdout_capture.content),
        stderr=bytes(stderr_capture.content),
    )


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
