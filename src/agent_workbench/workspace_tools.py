"""Provider-independent read-only workspace tools."""

from pathlib import Path

from agent_workbench.tool_registry import ToolRegistry
from agent_workbench.tools import JSONObject, ToolDefinition
from agent_workbench.workspace import Workspace

MAX_DIRECTORY_ENTRIES = 128
"""Maximum direct directory entries returned by list_files."""

MAX_FILE_SIZE_BYTES = 100 * 1024
"""Maximum UTF-8 file size returned by read_file."""

_PATH_INPUT_SCHEMA: JSONObject = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
        }
    },
    "required": ["path"],
    "additionalProperties": False,
}

LIST_FILES_DEFINITION = ToolDefinition(
    name="list_files",
    description="List the direct entries of a directory inside the authorized workspace.",
    input_schema=_PATH_INPUT_SCHEMA,
)

READ_FILE_DEFINITION = ToolDefinition(
    name="read_file",
    description="Read a UTF-8 text file inside the authorized workspace.",
    input_schema=_PATH_INPUT_SCHEMA,
)


def register_workspace_tools(
    registry: ToolRegistry,
    workspace: Workspace,
) -> None:
    """Register read-only workspace tools in deterministic order."""

    registry.register(
        LIST_FILES_DEFINITION,
        lambda arguments: list_workspace_files(workspace, arguments),
    )
    registry.register(
        READ_FILE_DEFINITION,
        lambda arguments: read_workspace_file(workspace, arguments),
    )


def list_workspace_files(
    workspace: Workspace,
    arguments: object,
) -> JSONObject:
    """List direct entries of a workspace directory without recursion."""

    directory_path = workspace.resolve(_get_requested_path("list_files", arguments))

    if not directory_path.is_dir():
        raise ValueError("list_files requires a directory.")

    try:
        children = sorted(
            directory_path.iterdir(),
            key=lambda child: child.name,
        )
    except OSError:
        raise ValueError("Unable to list workspace directory.") from None

    if len(children) > MAX_DIRECTORY_ENTRIES:
        raise ValueError("workspace directory contains too many entries.")

    return {
        "path": _workspace_relative_path(workspace, directory_path),
        "entries": [
            {
                "name": child.name,
                "path": _workspace_relative_path(workspace, child),
                "type": _entry_type(child),
            }
            for child in children
        ],
    }


def read_workspace_file(
    workspace: Workspace,
    arguments: object,
) -> JSONObject:
    """Read a bounded UTF-8 file from the authorized workspace."""

    file_path = workspace.resolve(_get_requested_path("read_file", arguments))

    if not file_path.is_file():
        raise ValueError("read_file requires a regular file.")

    try:
        size_bytes = file_path.stat().st_size
    except OSError:
        raise ValueError("Unable to inspect workspace file.") from None

    if size_bytes > MAX_FILE_SIZE_BYTES:
        raise ValueError(
            f"workspace file exceeds the {MAX_FILE_SIZE_BYTES}-byte limit."
        )

    try:
        with file_path.open("rb") as source:
            content_bytes = source.read(MAX_FILE_SIZE_BYTES + 1)
    except OSError:
        raise ValueError("Unable to read workspace file.") from None

    if len(content_bytes) > MAX_FILE_SIZE_BYTES:
        raise ValueError(
            f"workspace file exceeds the {MAX_FILE_SIZE_BYTES}-byte limit."
        )

    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("read_file requires valid UTF-8.") from None

    return {
        "path": _workspace_relative_path(workspace, file_path),
        "content": content,
        "size_bytes": len(content_bytes),
    }


def _get_requested_path(tool_name: str, arguments: object) -> Path:
    """Validate and convert a tool path argument."""

    if not isinstance(arguments, dict) or set(arguments) != {"path"}:
        raise ValueError(f"{tool_name} requires a path string.")

    path = arguments["path"]

    if not isinstance(path, str):
        raise ValueError(f"{tool_name} requires a path string.")

    return Path(path)


def _workspace_relative_path(workspace: Workspace, path: Path) -> str:
    """Return the portable canonical workspace-relative representation."""

    return path.relative_to(workspace.root).as_posix()


def _entry_type(path: Path) -> str:
    """Classify a direct directory entry without following symlinks."""

    if path.is_symlink():
        return "symlink"

    if path.is_file():
        return "file"

    if path.is_dir():
        return "directory"

    return "other"
