"""Approved optimistic single-file actions inside an authorized workspace."""

import difflib
import json
import os
import secrets
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from agent_workbench.errors import WorkspaceTransactionError
from agent_workbench.tool_registry import ToolRegistry
from agent_workbench.tools import JSONObject, ToolDefinition
from agent_workbench.workspace import Workspace
from agent_workbench.workspace_tools import MAX_FILE_SIZE_BYTES

MAX_PATCH_CONTENT_BYTES = MAX_FILE_SIZE_BYTES
"""Maximum UTF-8 byte size accepted for patch content."""

MAX_CHANGED_LINES = 500
"""Maximum removed and added lines accepted by one patch."""

MAX_PATCH_PREVIEW_BYTES = 64 * 1024
"""Maximum byte size of the complete approval diff."""

MAX_TRANSACTION_FILES = 16
"""Maximum number of files in one approved workspace transaction."""

MAX_TRANSACTION_EXPECTED_BYTES = 512 * 1024
"""Maximum combined expected-content bytes in one transaction."""

MAX_TRANSACTION_REPLACEMENT_BYTES = 512 * 1024
"""Maximum combined replacement-content bytes in one transaction."""

MAX_TRANSACTION_CHANGED_LINES = 2_000
"""Maximum combined added and removed lines in one transaction."""

MAX_TRANSACTION_PREVIEW_BYTES = 256 * 1024
"""Maximum byte size of one complete combined transaction preview."""

APPLY_FILE_PATCH_DEFINITION = ToolDefinition(
    name="apply_file_patch",
    description=(
        "Apply one approved optimistic UTF-8 file patch inside the authorized "
        "workspace."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "expected_content": {"type": "string"},
            "replacement_content": {"type": "string"},
            "create_if_missing": {"type": "boolean", "default": False},
        },
        "required": ["path", "expected_content", "replacement_content"],
        "additionalProperties": False,
    },
)

_CHANGE_SCHEMA: JSONObject = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "expected_content": {"type": "string"},
        "replacement_content": {"type": "string"},
        "create_if_missing": {"type": "boolean", "default": False},
    },
    "required": ["path", "expected_content", "replacement_content"],
    "additionalProperties": False,
}

APPLY_WORKSPACE_CHANGES_DEFINITION = ToolDefinition(
    name="apply_workspace_changes",
    description=(
        "Apply one approved transactional set of UTF-8 file creations and "
        "updates inside the authorized workspace. Each changes array element "
        "must contain path, expected_content, replacement_content, and optional "
        "create_if_missing."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "changes": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_TRANSACTION_FILES,
                "items": _CHANGE_SCHEMA,
            }
        },
        "required": ["changes"],
        "additionalProperties": False,
    },
)


@dataclass(frozen=True, slots=True)
class _PreparedPatch:
    """Store one validated patch snapshot for preview or execution."""

    target: Path
    relative_path: str
    operation: str
    expected_content: str
    replacement_content: str
    old_size_bytes: int
    new_size_bytes: int
    changed_lines: int
    diff: str
    existing_mode: int | None

    def metadata(self) -> JSONObject:
        """Return bounded provider-independent result metadata."""

        return {
            "path": self.relative_path,
            "operation": self.operation,
            "old_size_bytes": self.old_size_bytes,
            "new_size_bytes": self.new_size_bytes,
            "changed_lines": self.changed_lines,
        }


@dataclass(frozen=True, slots=True)
class _WorkspaceChangePlan:
    """Store one immutable validated deterministic transaction plan."""

    patches: tuple[_PreparedPatch, ...]
    created_count: int
    updated_count: int
    total_old_size_bytes: int
    total_new_size_bytes: int
    total_changed_lines: int

    def metadata(self, *, include_diffs: bool) -> JSONObject:
        """Return deterministic safe preview or result metadata."""

        changes = []
        for patch in self.patches:
            change = patch.metadata()
            if include_diffs:
                change["diff"] = patch.diff
            changes.append(change)
        return {
            "operation_count": len(self.patches),
            "created_count": self.created_count,
            "updated_count": self.updated_count,
            "total_old_size_bytes": self.total_old_size_bytes,
            "total_new_size_bytes": self.total_new_size_bytes,
            "total_changed_lines": self.total_changed_lines,
            "changes": changes,
        }


@dataclass(slots=True)
class _PreparedTransactionChange:
    """Store prepared replacement and rollback material for one change."""

    patch: _PreparedPatch
    replacement_path: Path | None = None
    rollback_path: Path | None = None


def register_workspace_action_tools(
    registry: ToolRegistry,
    workspace: Workspace,
) -> None:
    """Register the approved workspace patch tool."""

    registry.register(
        APPLY_FILE_PATCH_DEFINITION,
        lambda arguments: apply_file_patch(workspace, arguments),
        requires_approval=True,
        approval_preview=lambda arguments: preview_file_patch(workspace, arguments),
    )
    registry.register(
        APPLY_WORKSPACE_CHANGES_DEFINITION,
        lambda arguments: apply_workspace_changes(workspace, arguments),
        requires_approval=True,
        approval_preview=lambda arguments: preview_workspace_changes(
            workspace,
            arguments,
        ),
        propagates_completion_errors=True,
    )


def preview_file_patch(
    workspace: Workspace,
    arguments: object,
) -> JSONObject:
    """Validate one patch and return its complete deterministic approval preview."""

    patch = _prepare_patch(
        workspace,
        arguments,
        preview_limit_bytes=MAX_PATCH_PREVIEW_BYTES,
        validation_name="apply_file_patch",
    )
    return {
        **patch.metadata(),
        "diff": patch.diff,
    }


def apply_file_patch(
    workspace: Workspace,
    arguments: object,
) -> JSONObject:
    """Revalidate and atomically apply one optimistic single-file patch."""

    patch = _prepare_patch(
        workspace,
        arguments,
        preview_limit_bytes=MAX_PATCH_PREVIEW_BYTES,
        validation_name="apply_file_patch",
    )

    if patch.operation == "create":
        _create_file_exclusively(patch)
    else:
        _replace_file_atomically(workspace, patch)

    return patch.metadata()


def preview_workspace_changes(
    workspace: Workspace,
    arguments: object,
) -> JSONObject:
    """Return one complete deterministic transaction approval preview."""

    return _prepare_workspace_change_plan(workspace, arguments).metadata(
        include_diffs=True,
    )


def apply_workspace_changes(
    workspace: Workspace,
    arguments: object,
) -> JSONObject:
    """Apply one revalidated transaction with handled-failure rollback."""

    try:
        plan = _prepare_workspace_change_plan(workspace, arguments)
    except ValueError:
        raise WorkspaceTransactionError(
            "Workspace transaction is stale or invalid; no files were changed."
        ) from None

    try:
        prepared = _prepare_transaction_changes(plan)
    except Exception:
        raise WorkspaceTransactionError(
            "Workspace transaction preparation failed; no files were changed."
        ) from None

    try:
        try:
            current_plan = _prepare_workspace_change_plan(workspace, arguments)
        except ValueError:
            raise WorkspaceTransactionError(
                "Workspace transaction is stale; no files were changed."
            ) from None
        if current_plan != plan:
            raise WorkspaceTransactionError(
                "Workspace transaction is stale; no files were changed."
            )

        applied: list[_PreparedTransactionChange] = []
        try:
            for change in prepared:
                _commit_prepared_change(change)
                applied.append(change)
        except Exception:
            rollback_failures = _rollback_applied_changes(applied)
            if rollback_failures:
                affected = ", ".join(rollback_failures)
                raise WorkspaceTransactionError(
                    "Workspace transaction failed and rollback was incomplete; "
                    f"inspect these paths manually: {affected}."
                ) from None
            if applied:
                raise WorkspaceTransactionError(
                    "Workspace transaction failed; applied changes were rolled back."
                ) from None
            raise WorkspaceTransactionError(
                "Workspace transaction failed before any files were changed."
            ) from None
    finally:
        _cleanup_prepared_changes(prepared)

    return plan.metadata(include_diffs=False)


def _prepare_patch(
    workspace: Workspace,
    arguments: object,
    *,
    preview_limit_bytes: int | None,
    validation_name: str,
) -> _PreparedPatch:
    """Validate arguments, target state, limits, and the complete diff."""

    path, expected_content, replacement_content, create_if_missing = (
        _get_patch_arguments(
            arguments,
            validation_name=validation_name,
        )
    )
    target, relative_path, target_status = _resolve_write_target(workspace, path)
    _encode_patch_content("expected_content", expected_content)
    replacement_bytes = _encode_patch_content(
        "replacement_content",
        replacement_content,
    )

    if target_status is None:
        if not create_if_missing:
            raise ValueError("apply_file_patch target does not exist.")
        if expected_content != "":
            raise ValueError("new-file expected_content must be empty.")
        old_content = ""
        old_bytes = b""
        operation = "create"
        existing_mode = None
    else:
        if create_if_missing:
            raise ValueError("create_if_missing requires a missing target.")
        old_bytes = _read_existing_file(target, target_status)
        try:
            old_content = old_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("apply_file_patch requires valid UTF-8.") from None
        if old_content != expected_content:
            raise ValueError("apply_file_patch expected content does not match.")
        operation = "update"
        existing_mode = stat.S_IMODE(target_status.st_mode)

    changed_lines = _count_changed_lines(old_content, replacement_content)
    if changed_lines > MAX_CHANGED_LINES:
        raise ValueError(f"patch exceeds the {MAX_CHANGED_LINES}-changed-line limit.")

    diff = _create_unified_diff(
        relative_path,
        old_content,
        replacement_content,
        operation=operation,
    )
    if (
        preview_limit_bytes is not None
        and len(diff.encode("utf-8")) > preview_limit_bytes
    ):
        raise ValueError(
            f"complete patch preview exceeds the {preview_limit_bytes}-byte limit."
        )

    return _PreparedPatch(
        target=target,
        relative_path=relative_path,
        operation=operation,
        expected_content=expected_content,
        replacement_content=replacement_content,
        old_size_bytes=len(old_bytes),
        new_size_bytes=len(replacement_bytes),
        changed_lines=changed_lines,
        diff=diff,
        existing_mode=existing_mode,
    )


def _prepare_workspace_change_plan(
    workspace: Workspace,
    arguments: object,
) -> _WorkspaceChangePlan:
    """Validate every requested change and return one sorted immutable plan."""

    changes = _get_workspace_change_arguments(arguments)
    patches = tuple(
        sorted(
            (
                _prepare_patch(
                    workspace,
                    change,
                    preview_limit_bytes=None,
                    validation_name="apply_workspace_changes change",
                )
                for change in changes
            ),
            key=lambda patch: patch.relative_path,
        )
    )
    normalized_targets = [os.path.normcase(str(patch.target)) for patch in patches]
    if len(normalized_targets) != len(set(normalized_targets)):
        raise ValueError("workspace transaction contains duplicate target paths.")

    total_expected_bytes = sum(
        len(patch.expected_content.encode("utf-8")) for patch in patches
    )
    total_replacement_bytes = sum(patch.new_size_bytes for patch in patches)
    total_changed_lines = sum(patch.changed_lines for patch in patches)
    if total_expected_bytes > MAX_TRANSACTION_EXPECTED_BYTES:
        raise ValueError(
            "workspace transaction exceeds the combined expected-content limit."
        )
    if total_replacement_bytes > MAX_TRANSACTION_REPLACEMENT_BYTES:
        raise ValueError(
            "workspace transaction exceeds the combined replacement-content limit."
        )
    if total_changed_lines > MAX_TRANSACTION_CHANGED_LINES:
        raise ValueError(
            "workspace transaction exceeds the combined changed-line limit."
        )

    plan = _WorkspaceChangePlan(
        patches=patches,
        created_count=sum(patch.operation == "create" for patch in patches),
        updated_count=sum(patch.operation == "update" for patch in patches),
        total_old_size_bytes=sum(patch.old_size_bytes for patch in patches),
        total_new_size_bytes=total_replacement_bytes,
        total_changed_lines=total_changed_lines,
    )
    preview_bytes = json.dumps(
        plan.metadata(include_diffs=True),
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(preview_bytes) > MAX_TRANSACTION_PREVIEW_BYTES:
        raise ValueError(
            "complete workspace transaction preview exceeds the "
            f"{MAX_TRANSACTION_PREVIEW_BYTES}-byte limit."
        )
    return plan


def _get_workspace_change_arguments(
    arguments: object,
) -> tuple[dict[str, object], ...]:
    """Validate the closed transaction argument container."""

    if not isinstance(arguments, dict) or set(arguments) != {"changes"}:
        raise ValueError("apply_workspace_changes requires a changes array.")
    changes = arguments["changes"]
    if not isinstance(changes, list):
        raise ValueError("apply_workspace_changes changes must be an array.")
    if not 1 <= len(changes) <= MAX_TRANSACTION_FILES:
        raise ValueError(
            "apply_workspace_changes changes must contain between 1 and "
            f"{MAX_TRANSACTION_FILES} items."
        )
    if not all(isinstance(change, dict) for change in changes):
        raise ValueError("apply_workspace_changes changes must contain objects.")
    return tuple(change.copy() for change in changes)


def _get_patch_arguments(
    arguments: object,
    *,
    validation_name: str,
) -> tuple[str, str, str, bool]:
    """Validate the closed structured patch argument object."""

    required = {
        "path",
        "expected_content",
        "replacement_content",
    }
    allowed = {
        *required,
        "create_if_missing",
    }
    if validation_name == "apply_file_patch" and (
        not isinstance(arguments, dict)
        or not required <= set(arguments)
        or set(arguments) - allowed
    ):
        raise ValueError("apply_file_patch requires structured patch arguments.")
    if not isinstance(arguments, dict):
        raise ValueError(f"{validation_name} must be an object.")

    missing = sorted(required - set(arguments))
    if missing:
        raise ValueError(
            f"{validation_name} is missing required fields: {', '.join(missing)}."
        )
    unsupported = sorted(set(arguments) - allowed)
    if unsupported:
        raise ValueError(
            f"{validation_name} has unsupported fields: {', '.join(unsupported)}."
        )

    path = arguments["path"]
    expected_content = arguments["expected_content"]
    replacement_content = arguments["replacement_content"]
    create_if_missing = arguments.get("create_if_missing", False)
    if validation_name == "apply_file_patch" and (
        not isinstance(path, str)
        or not isinstance(expected_content, str)
        or not isinstance(replacement_content, str)
        or not isinstance(create_if_missing, bool)
    ):
        raise ValueError("apply_file_patch requires structured patch arguments.")
    if not isinstance(path, str):
        raise ValueError(f"{validation_name} path must be a string.")
    if not isinstance(expected_content, str):
        raise ValueError(f"{validation_name} expected_content must be a string.")
    if not isinstance(replacement_content, str):
        raise ValueError(f"{validation_name} replacement_content must be a string.")
    if not isinstance(create_if_missing, bool):
        raise ValueError(f"{validation_name} create_if_missing must be a boolean.")

    return path, expected_content, replacement_content, create_if_missing


def _encode_patch_content(field_name: str, content: str) -> bytes:
    """Validate NUL-free UTF-8 patch content within the byte limit."""

    if "\0" in content:
        raise ValueError(f"apply_file_patch {field_name} must not contain NUL bytes.")

    try:
        content_bytes = content.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("apply_file_patch requires valid UTF-8.") from None
    if len(content_bytes) > MAX_PATCH_CONTENT_BYTES:
        raise ValueError(
            f"apply_file_patch {field_name} exceeds the "
            f"{MAX_PATCH_CONTENT_BYTES}-byte limit."
        )

    return content_bytes


def _resolve_write_target(
    workspace: Workspace,
    path: str,
) -> tuple[Path, str, os.stat_result | None]:
    """Resolve a file target without following any symlink component."""

    if "\0" in path:
        raise ValueError("apply_file_patch path must not contain NUL bytes.")

    requested = Path(path)
    if requested.is_absolute() or PureWindowsPath(path).is_absolute():
        raise ValueError("apply_file_patch path must be relative.")

    components = tuple(part for part in requested.parts if part not in ("", "."))
    if not components:
        raise ValueError("apply_file_patch requires a file path.")
    if ".." in components:
        raise ValueError("apply_file_patch path must not contain traversal.")
    if ".git" in components:
        raise ValueError("apply_file_patch cannot modify .git paths.")

    parent = workspace.root
    for component in components[:-1]:
        candidate = parent / component
        try:
            candidate_status = os.lstat(candidate)
        except FileNotFoundError:
            raise ValueError(
                "apply_file_patch parent directory does not exist."
            ) from None
        except OSError:
            raise ValueError("Unable to inspect patch parent directory.") from None
        if stat.S_ISLNK(candidate_status.st_mode):
            raise ValueError("apply_file_patch does not allow symlink paths.")
        if not stat.S_ISDIR(candidate_status.st_mode):
            raise ValueError("apply_file_patch parent must be a directory.")
        parent = candidate

    try:
        canonical_parent = parent.resolve(strict=True)
        canonical_parent.relative_to(workspace.root)
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        raise ValueError("apply_file_patch target is outside the workspace.") from None

    target = canonical_parent / components[-1]
    try:
        target_status = os.lstat(target)
    except FileNotFoundError:
        target_status = None
    except OSError:
        raise ValueError("Unable to inspect patch target.") from None

    if target_status is not None:
        if stat.S_ISLNK(target_status.st_mode):
            raise ValueError("apply_file_patch does not allow symlink paths.")
        if not stat.S_ISREG(target_status.st_mode):
            raise ValueError("apply_file_patch requires a regular file.")

    relative_path = Path(*components).as_posix()
    return target, relative_path, target_status


def _read_existing_file(
    target: Path,
    target_status: os.stat_result,
) -> bytes:
    """Read one bounded regular file without exposing host paths."""

    if not stat.S_ISREG(target_status.st_mode):
        raise ValueError("apply_file_patch requires a regular file.")
    if target_status.st_size > MAX_PATCH_CONTENT_BYTES:
        raise ValueError(
            f"workspace file exceeds the {MAX_PATCH_CONTENT_BYTES}-byte limit."
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
            raise ValueError("apply_file_patch target changed while reading.")
        with os.fdopen(descriptor, "rb") as source:
            descriptor = None
            content = source.read(MAX_PATCH_CONTENT_BYTES + 1)
    except ValueError:
        raise
    except OSError:
        raise ValueError("Unable to read patch target.") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(content) > MAX_PATCH_CONTENT_BYTES:
        raise ValueError(
            f"workspace file exceeds the {MAX_PATCH_CONTENT_BYTES}-byte limit."
        )
    return content


def _count_changed_lines(old_content: str, new_content: str) -> int:
    """Count removed and added lines using deterministic sequence matching."""

    old_lines = old_content.splitlines()
    new_lines = new_content.splitlines()
    changed_lines = 0
    for tag, old_start, old_end, new_start, new_end in difflib.SequenceMatcher(
        None,
        old_lines,
        new_lines,
        autojunk=False,
    ).get_opcodes():
        if tag != "equal":
            changed_lines += old_end - old_start
            changed_lines += new_end - new_start
    return changed_lines


def _create_unified_diff(
    relative_path: str,
    old_content: str,
    new_content: str,
    *,
    operation: str,
) -> str:
    """Return one complete deterministic unified diff."""

    from_file = "/dev/null" if operation == "create" else f"a/{relative_path}"
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


def _create_file_exclusively(patch: _PreparedPatch) -> None:
    """Create a new file without overwriting a concurrent target."""

    descriptor: int | None = None
    created = False
    completed = False
    try:
        descriptor = os.open(
            patch.target,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o666,
        )
        created = True
        with os.fdopen(descriptor, "wb") as destination:
            descriptor = None
            destination.write(patch.replacement_content.encode("utf-8"))
            destination.flush()
            os.fsync(destination.fileno())
        completed = True
    except FileExistsError:
        raise ValueError("apply_file_patch target changed before creation.") from None
    except OSError:
        raise ValueError("Unable to create patch target.") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if created and not completed:
            try:
                patch.target.unlink()
            except OSError:
                pass


def _replace_file_atomically(
    workspace: Workspace,
    patch: _PreparedPatch,
) -> None:
    """Write a same-directory temporary file and atomically replace the target."""

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=patch.target.parent,
            prefix=".agent-workbench-patch-",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(patch.replacement_content.encode("utf-8"))
            temporary.flush()
            os.fsync(temporary.fileno())

        if patch.existing_mode is not None:
            os.chmod(temporary_path, patch.existing_mode)

        current = _prepare_patch(
            workspace,
            {
                "path": patch.relative_path,
                "expected_content": patch.expected_content,
                "replacement_content": patch.replacement_content,
                "create_if_missing": False,
            },
            preview_limit_bytes=MAX_PATCH_PREVIEW_BYTES,
            validation_name="apply_file_patch",
        )
        if current.operation != "update":
            raise ValueError("apply_file_patch target changed before replacement.")

        os.replace(temporary_path, patch.target)
        temporary_path = None
    except ValueError:
        raise
    except OSError:
        raise ValueError("Unable to replace patch target.") from None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass


def _prepare_transaction_changes(
    plan: _WorkspaceChangePlan,
) -> list[_PreparedTransactionChange]:
    """Prepare all update replacement and rollback files before mutation."""

    prepared: list[_PreparedTransactionChange] = []
    reserved_paths = {patch.target for patch in plan.patches}
    try:
        for patch in plan.patches:
            change = _PreparedTransactionChange(patch=patch)
            prepared.append(change)
            if patch.operation == "update":
                assert patch.existing_mode is not None
                change.replacement_path = _write_transaction_temp(
                    patch.target.parent,
                    patch.replacement_content.encode("utf-8"),
                    patch.existing_mode,
                    reserved_paths,
                )
                change.rollback_path = _write_transaction_temp(
                    patch.target.parent,
                    patch.expected_content.encode("utf-8"),
                    patch.existing_mode,
                    reserved_paths,
                )
    except Exception:
        _cleanup_prepared_changes(prepared)
        raise
    return prepared


def _write_transaction_temp(
    parent: Path,
    content: bytes,
    mode: int,
    reserved_paths: set[Path],
) -> Path:
    """Write one collision-safe same-directory transaction temporary file."""

    while True:
        candidate = parent / (f".agent-workbench-transaction-{secrets.token_hex(16)}")
        if candidate not in reserved_paths:
            break

    descriptor: int | None = None
    completed = False
    try:
        descriptor = os.open(
            candidate,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        reserved_paths.add(candidate)
        with os.fdopen(descriptor, "wb") as destination:
            descriptor = None
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
        os.chmod(candidate, mode)
        completed = True
        return candidate
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if not completed:
            try:
                candidate.unlink()
            except OSError:
                pass


def _commit_prepared_change(change: _PreparedTransactionChange) -> None:
    """Commit one prepared change in deterministic plan order."""

    if change.patch.operation == "create":
        _create_file_exclusively(change.patch)
        return
    if change.replacement_path is None:
        raise OSError("transaction replacement was not prepared")
    os.replace(change.replacement_path, change.patch.target)


def _rollback_applied_change(change: _PreparedTransactionChange) -> None:
    """Restore one applied update or remove one transaction-created file."""

    if change.patch.operation == "create":
        change.patch.target.unlink()
        return
    if change.rollback_path is None:
        raise OSError("transaction rollback was not prepared")
    os.replace(change.rollback_path, change.patch.target)
    if change.patch.existing_mode is not None:
        os.chmod(change.patch.target, change.patch.existing_mode)


def _rollback_applied_changes(
    applied: list[_PreparedTransactionChange],
) -> list[str]:
    """Attempt reverse-order rollback and return safe failed relative paths."""

    failures = []
    for change in reversed(applied):
        try:
            _rollback_applied_change(change)
        except Exception:
            failures.append(change.patch.relative_path)
    return failures


def _cleanup_prepared_changes(
    prepared: list[_PreparedTransactionChange],
) -> None:
    """Remove all remaining transaction temporary files."""

    for change in prepared:
        for temporary_path in (
            change.replacement_path,
            change.rollback_path,
        ):
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except OSError:
                    pass
