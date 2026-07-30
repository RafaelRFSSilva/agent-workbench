"""Provider-independent safe read-only Git inspection tools."""

import difflib
import json
import stat
import subprocess
from pathlib import Path, PurePosixPath

from agent_workbench.tool_registry import ToolRegistry
from agent_workbench.tools import JSONObject, ToolDefinition
from agent_workbench.workspace import Workspace

GIT_TIMEOUT_SECONDS = 3
"""Maximum duration allowed for one fixed Git inspection command."""

MAX_GIT_OUTPUT_BYTES = 100 * 1024
"""Maximum captured output or serialized evidence returned by Git inspection."""

MAX_UNTRACKED_FILES = 64
"""Maximum untracked files represented by one Git diff inspection."""

MAX_UNTRACKED_FILE_BYTES = 32 * 1024
"""Maximum bytes read from one untracked file."""

MAX_UNTRACKED_EVIDENCE_BYTES = 64 * 1024
"""Maximum combined diff-like evidence for untracked files."""

MAX_UNTRACKED_OMISSION_METADATA_BYTES = 16 * 1024
"""Maximum stable serialized omission metadata returned by Git inspection."""

MAX_UNTRACKED_DISPLAY_PATH_CHARACTERS = 512
"""Maximum safe relative path length exposed in untracked evidence."""

INSPECT_GIT_STATUS_DEFINITION = ToolDefinition(
    name="inspect_git_status",
    description="Inspect the Git working-tree status inside the authorized workspace.",
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "enum": ["", "."],
                "description": (
                    "Optional workspace-root alias. Omit this property when possible."
                ),
            }
        },
        "additionalProperties": False,
    },
)

INSPECT_GIT_DIFF_DEFINITION = ToolDefinition(
    name="inspect_git_diff",
    description=(
        "Inspect the current unstaged, staged, and safe untracked Git evidence "
        "inside the authorized workspace."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Optional workspace-relative path. Omit it, use an empty string, "
                    "or use '.' to inspect the complete workspace diff."
                ),
            }
        },
        "additionalProperties": False,
    },
)


def register_git_tools(
    registry: ToolRegistry,
    workspace: Workspace,
) -> None:
    """Register safe read-only Git inspection tools in deterministic order."""

    registry.register(
        INSPECT_GIT_STATUS_DEFINITION,
        lambda arguments: inspect_workspace_git_status(workspace, arguments),
    )
    registry.register(
        INSPECT_GIT_DIFF_DEFINITION,
        lambda arguments: inspect_workspace_git_diff(workspace, arguments),
    )


def inspect_workspace_git_status(
    workspace: Workspace,
    arguments: object,
) -> JSONObject:
    """Return fixed Git status output for the authorized workspace root."""

    _require_root_arguments("inspect_git_status", arguments)
    output = _run_git(
        workspace,
        [
            "git",
            "-c",
            "core.pager=cat",
            "status",
            "--short",
            "--branch",
        ],
    )

    return {"status": output}


def inspect_workspace_git_diff(
    workspace: Workspace,
    arguments: object,
) -> JSONObject:
    """Return separate fixed unstaged and staged diffs for one workspace."""

    pathspec = _get_diff_pathspec(workspace, arguments)
    base_arguments = [
        "git",
        "-c",
        "diff.external=",
        "-c",
        "core.pager=cat",
        "diff",
    ]
    unstaged_output = _run_git(
        workspace,
        [
            *base_arguments,
            "--no-ext-diff",
            "--",
            *pathspec,
        ],
    )
    staged_output = _run_git(
        workspace,
        [
            *base_arguments,
            "--cached",
            "--no-ext-diff",
            "--",
            *pathspec,
        ],
    )
    untracked_output = _run_git(
        workspace,
        [
            "git",
            "-c",
            "core.pager=cat",
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            *pathspec,
        ],
    )
    untracked_evidence, untracked_omitted = _build_untracked_evidence(
        workspace,
        untracked_output,
    )
    untracked_omitted = _bound_untracked_omissions(untracked_omitted)
    result: JSONObject = {
        "unstaged": unstaged_output,
        "staged": staged_output,
        "untracked": untracked_evidence,
        "untracked_omitted": untracked_omitted,
    }
    if len(_serialize_git_evidence(result)) > MAX_GIT_OUTPUT_BYTES:
        if untracked_omitted and not _omissions_are_aggregated(untracked_omitted):
            result["untracked_omitted"] = _aggregate_untracked_omissions(
                untracked_omitted
            )
        if len(_serialize_git_evidence(result)) > MAX_GIT_OUTPUT_BYTES:
            raise ValueError("Git output exceeds the configured size limit.")

    return result


def _bound_untracked_omissions(
    omissions: list[JSONObject],
) -> list[JSONObject]:
    """Collapse omission details whose stable JSON exceeds its own budget."""

    if len(_serialize_git_evidence(omissions)) <= MAX_UNTRACKED_OMISSION_METADATA_BYTES:
        return omissions
    return _aggregate_untracked_omissions(omissions)


def _aggregate_untracked_omissions(
    omissions: list[JSONObject],
) -> list[JSONObject]:
    """Return deterministic reason counts without retaining individual paths."""

    reason_counts: dict[str, int] = {}
    file_count = 0
    for omission in omissions:
        reason = omission.get("reason")
        if not isinstance(reason, str):
            reason = "unavailable"
        represented_count = omission.get("file_count", 1)
        if (
            isinstance(represented_count, bool)
            or not isinstance(represented_count, int)
            or represented_count < 1
        ):
            represented_count = 1
        reason_counts[reason] = reason_counts.get(reason, 0) + represented_count
        file_count += represented_count
    return [
        {
            "reason": "omission_metadata_limit",
            "file_count": file_count,
            "reason_counts": dict(sorted(reason_counts.items())),
        }
    ]


def _omissions_are_aggregated(omissions: list[JSONObject]) -> bool:
    """Return whether omissions already contain one bounded aggregate."""

    return (
        len(omissions) == 1 and omissions[0].get("reason") == "omission_metadata_limit"
    )


def _serialize_git_evidence(value: object) -> bytes:
    """Serialize returned evidence deterministically for complete size checks."""

    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, UnicodeEncodeError, ValueError):
        raise ValueError("Git output exceeds the configured size limit.") from None


def _build_untracked_evidence(
    workspace: Workspace,
    output: str,
) -> tuple[str, list[JSONObject]]:
    """Build bounded deterministic new-file diffs and safe omission metadata."""

    raw_paths = sorted(path for path in output.split("\0") if path)
    evidence_parts: list[str] = []
    omitted: list[JSONObject] = []
    unsafe_or_ignored_count = 0
    evidence_bytes = 0

    for raw_path in raw_paths[:MAX_UNTRACKED_FILES]:
        resolved = _resolve_safe_untracked_path(workspace, raw_path)
        if resolved is None:
            unsafe_or_ignored_count += 1
            continue
        relative_path, target = resolved
        item, omission = _build_untracked_file_evidence(relative_path, target)
        if omission is not None:
            omitted.append(omission)
            continue
        assert item is not None
        item_bytes = len(item.encode("utf-8"))
        if evidence_bytes + item_bytes > MAX_UNTRACKED_EVIDENCE_BYTES:
            omitted.append(
                {
                    "path": relative_path,
                    "reason": "exceeds_combined_evidence_limit",
                }
            )
            continue
        evidence_parts.append(item)
        evidence_bytes += item_bytes

    if len(raw_paths) > MAX_UNTRACKED_FILES:
        omitted.append(
            {
                "reason": "file_count_limit",
                "file_count": len(raw_paths) - MAX_UNTRACKED_FILES,
            }
        )
    if unsafe_or_ignored_count:
        omitted.append(
            {
                "reason": "unsafe_or_ignored",
                "file_count": unsafe_or_ignored_count,
            }
        )

    return "".join(evidence_parts), omitted


def _resolve_safe_untracked_path(
    workspace: Workspace,
    raw_path: str,
) -> tuple[str, Path] | None:
    """Resolve one display-safe regular candidate without following links."""

    if (
        not raw_path
        or len(raw_path) > MAX_UNTRACKED_DISPLAY_PATH_CHARACTERS
        or "\\" in raw_path
        or any(ord(character) < 32 or ord(character) == 127 for character in raw_path)
    ):
        return None
    pure_path = PurePosixPath(raw_path)
    if (
        pure_path.is_absolute()
        or not pure_path.parts
        or any(part in {"", ".", ".."} for part in pure_path.parts)
        or ".git" in pure_path.parts
        or ".env" in pure_path.parts
        or pure_path.parts[0].startswith(("-", ":"))
    ):
        return None

    target = workspace.root.joinpath(*pure_path.parts)
    if workspace.is_ignored_traversal_path(target):
        return None
    try:
        canonical = target.resolve(strict=True)
        canonical.relative_to(workspace.root)
    except (OSError, RuntimeError, ValueError):
        return None
    return raw_path, target


def _build_untracked_file_evidence(
    relative_path: str,
    target: Path,
) -> tuple[str | None, JSONObject | None]:
    """Return one bounded UTF-8 new-file diff or safe omission metadata."""

    try:
        target_status = target.lstat()
    except OSError:
        return None, {
            "path": relative_path,
            "reason": "unreadable",
        }
    size_bytes = target_status.st_size
    if not stat.S_ISREG(target_status.st_mode):
        return None, {
            "path": relative_path,
            "reason": "unsupported_file_type",
            "size_bytes": size_bytes,
        }
    if size_bytes > MAX_UNTRACKED_FILE_BYTES:
        return None, {
            "path": relative_path,
            "reason": "exceeds_file_size_limit",
            "size_bytes": size_bytes,
        }

    try:
        with target.open("rb") as source:
            content_bytes = source.read(MAX_UNTRACKED_FILE_BYTES + 1)
    except OSError:
        return None, {
            "path": relative_path,
            "reason": "unreadable",
            "size_bytes": size_bytes,
        }
    if len(content_bytes) > MAX_UNTRACKED_FILE_BYTES:
        return None, {
            "path": relative_path,
            "reason": "exceeds_file_size_limit",
            "size_bytes": len(content_bytes),
        }
    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return None, {
            "path": relative_path,
            "reason": "binary_or_non_utf8",
            "size_bytes": len(content_bytes),
        }
    if "\0" in content or any(
        ord(character) < 32 and character not in "\n\r\t" for character in content
    ):
        return None, {
            "path": relative_path,
            "reason": "binary_or_non_utf8",
            "size_bytes": len(content_bytes),
        }

    file_mode = "100755" if target_status.st_mode & stat.S_IXUSR else "100644"
    diff = "".join(
        difflib.unified_diff(
            [],
            content.splitlines(keepends=True),
            fromfile="/dev/null",
            tofile=f"b/{relative_path}",
            lineterm="\n",
        )
    )
    evidence = (
        f"diff --git a/{relative_path} b/{relative_path}\n"
        f"new file mode {file_mode}\n"
        f"{diff}"
    )
    if not evidence.endswith("\n"):
        evidence += "\n"
    return evidence, None


def _require_root_arguments(tool_name: str, arguments: object) -> None:
    """Accept only an omitted or explicit workspace-root path alias."""

    if not isinstance(arguments, dict) or set(arguments) - {"path"}:
        raise ValueError(f"{tool_name} requires an optional root path.")

    if "path" not in arguments:
        return

    path = arguments["path"]

    if not isinstance(path, str) or path not in {"", "."}:
        raise ValueError(f"{tool_name} path must be empty or '.'.")


def _get_diff_pathspec(
    workspace: Workspace,
    arguments: object,
) -> list[str]:
    """Resolve one optional path into a canonical workspace-relative pathspec."""

    if not isinstance(arguments, dict) or set(arguments) - {"path"}:
        raise ValueError("inspect_git_diff requires an optional path string.")

    if "path" not in arguments:
        return []

    path = arguments["path"]

    if not isinstance(path, str):
        raise ValueError("inspect_git_diff requires an optional path string.")

    if path in {"", "."}:
        return []

    canonical_path = workspace.resolve(Path(path))

    return [canonical_path.relative_to(workspace.root).as_posix()]


def _run_git(workspace: Workspace, arguments: list[str]) -> str:
    """Run one fixed non-shell Git command with bounded captured output."""

    environment = {
        "GIT_EXTERNAL_DIFF": "",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
    }

    try:
        completed_process = subprocess.run(
            arguments,
            cwd=workspace.root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=GIT_TIMEOUT_SECONDS,
            env=environment,
        )
    except FileNotFoundError:
        raise ValueError("Git executable is unavailable.") from None
    except subprocess.TimeoutExpired:
        raise ValueError("Git inspection timed out.") from None
    except OSError:
        raise ValueError("Unable to run Git inspection.") from None

    output_size = len(completed_process.stdout) + len(completed_process.stderr)

    if output_size > MAX_GIT_OUTPUT_BYTES:
        raise ValueError("Git output exceeds the configured size limit.")

    if completed_process.returncode != 0:
        if b"not a git repository" in completed_process.stderr.lower():
            raise ValueError("Workspace is not a Git worktree.")

        raise ValueError("Git command failed.")

    try:
        return completed_process.stdout.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("Git command produced invalid UTF-8 output.") from None
