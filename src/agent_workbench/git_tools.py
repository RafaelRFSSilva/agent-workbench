"""Provider-independent safe read-only Git inspection tools."""

import subprocess
from pathlib import Path

from agent_workbench.tool_registry import ToolRegistry
from agent_workbench.tools import JSONObject, ToolDefinition
from agent_workbench.workspace import Workspace

GIT_TIMEOUT_SECONDS = 3
"""Maximum duration allowed for one fixed Git inspection command."""

MAX_GIT_OUTPUT_BYTES = 100 * 1024
"""Maximum combined captured output returned by one Git inspection."""

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
        "Inspect the current unstaged and staged Git diff inside the authorized "
        "workspace."
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

    if (
        len(unstaged_output.encode()) + len(staged_output.encode())
        > MAX_GIT_OUTPUT_BYTES
    ):
        raise ValueError("Git output exceeds the configured size limit.")

    return {
        "unstaged": unstaged_output,
        "staged": staged_output,
    }


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
