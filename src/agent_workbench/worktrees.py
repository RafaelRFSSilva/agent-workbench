"""Validated planning for supervised local Git worktrees."""

from dataclasses import dataclass, field
import os
from pathlib import Path
import signal
import stat
import subprocess
import threading

from agent_workbench.errors import ConfigurationError
from agent_workbench.tools import JSONObject

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


@dataclass(frozen=True, slots=True)
class _GitOutput:
    """Store one bounded fixed Git command outcome."""

    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class _WorktreeRecord:
    """Store the identity and exceptional flags of one registered worktree."""

    path: Path
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
        if current is None or not separator:
            raise ConfigurationError("registered worktree state is ambiguous.")
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
    path = Path(raw_path)
    if not path.is_absolute():
        raise ConfigurationError("registered worktree state is ambiguous.")
    try:
        canonical_path = path.resolve(strict=False)
    except (OSError, RuntimeError):
        raise ConfigurationError("registered worktree state is ambiguous.") from None

    raw_branch = fields.get("branch")
    branch = raw_branch if isinstance(raw_branch, str) else None
    return _WorktreeRecord(
        path=canonical_path,
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
        raise ConfigurationError("unable to start Git planning command.") from None

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
        raise ConfigurationError("Git planning command timed out.")
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
