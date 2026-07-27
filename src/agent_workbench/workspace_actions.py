"""Approved optimistic single-file actions inside an authorized workspace."""

import difflib
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

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


def preview_file_patch(
    workspace: Workspace,
    arguments: object,
) -> JSONObject:
    """Validate one patch and return its complete deterministic approval preview."""

    patch = _prepare_patch(workspace, arguments)
    return {
        **patch.metadata(),
        "diff": patch.diff,
    }


def apply_file_patch(
    workspace: Workspace,
    arguments: object,
) -> JSONObject:
    """Revalidate and atomically apply one optimistic single-file patch."""

    patch = _prepare_patch(workspace, arguments)

    if patch.operation == "create":
        _create_file_exclusively(patch)
    else:
        _replace_file_atomically(workspace, patch)

    return patch.metadata()


def _prepare_patch(
    workspace: Workspace,
    arguments: object,
) -> _PreparedPatch:
    """Validate arguments, target state, limits, and the complete diff."""

    path, expected_content, replacement_content, create_if_missing = (
        _get_patch_arguments(arguments)
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
    if len(diff.encode("utf-8")) > MAX_PATCH_PREVIEW_BYTES:
        raise ValueError(
            f"complete patch preview exceeds the {MAX_PATCH_PREVIEW_BYTES}-byte limit."
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


def _get_patch_arguments(
    arguments: object,
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
    if (
        not isinstance(arguments, dict)
        or not required <= set(arguments)
        or set(arguments) - allowed
    ):
        raise ValueError("apply_file_patch requires structured patch arguments.")

    path = arguments["path"]
    expected_content = arguments["expected_content"]
    replacement_content = arguments["replacement_content"]
    create_if_missing = arguments.get("create_if_missing", False)
    if (
        not isinstance(path, str)
        or not isinstance(expected_content, str)
        or not isinstance(replacement_content, str)
        or not isinstance(create_if_missing, bool)
    ):
        raise ValueError("apply_file_patch requires structured patch arguments.")

    return path, expected_content, replacement_content, create_if_missing


def _encode_patch_content(field_name: str, content: str) -> bytes:
    """Validate NUL-free UTF-8 patch content within the byte limit."""

    if "\0" in content:
        raise ValueError(f"apply_file_patch {field_name} must not contain NUL bytes.")

    content_bytes = content.encode("utf-8")
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

    try:
        with target.open("rb") as source:
            content = source.read(MAX_PATCH_CONTENT_BYTES + 1)
    except OSError:
        raise ValueError("Unable to read patch target.") from None
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
