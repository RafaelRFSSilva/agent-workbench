"""Provider-independent read-only workspace tools."""

from pathlib import Path

from agent_workbench.errors import ToolArgumentError
from agent_workbench.tool_registry import ToolRegistry
from agent_workbench.tools import JSONObject, ToolDefinition
from agent_workbench.workspace import Workspace

MAX_DIRECTORY_ENTRIES = 128
"""Maximum direct directory entries returned by list_files."""

MAX_FILE_SIZE_BYTES = 100 * 1024
"""Maximum UTF-8 file size returned by read_file."""

MAX_READ_LINES = 400
"""Maximum number of lines returned by one partial read_file request."""

MAX_SEARCH_QUERY_LENGTH = 256
"""Maximum number of characters accepted in a search query."""

MAX_SEARCH_FILES = 512
"""Maximum regular files inspected by one search."""

MAX_SEARCH_FILE_BYTES = MAX_FILE_SIZE_BYTES
"""Maximum bytes inspected from one searched file."""

MAX_SEARCH_MATCHES = 256
"""Maximum matching lines returned by one search."""

MAX_SEARCH_LINE_LENGTH = 1_000
"""Maximum characters returned for one matching line."""

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

_READ_FILE_INPUT_SCHEMA: JSONObject = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
        },
        "line_start": {
            "type": "integer",
            "minimum": 1,
        },
        "line_end": {
            "type": "integer",
            "minimum": 1,
        },
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
    description=(
        "Read all or an inclusive bounded line range from a UTF-8 text file "
        "inside the authorized workspace."
    ),
    input_schema=_READ_FILE_INPUT_SCHEMA,
)

SEARCH_TEXT_DEFINITION = ToolDefinition(
    name="search_text",
    description="Search UTF-8 text files inside the authorized workspace.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
            },
            "path": {
                "type": "string",
            },
            "case_sensitive": {
                "type": "boolean",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
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
    registry.register(
        SEARCH_TEXT_DEFINITION,
        lambda arguments: search_workspace_text(workspace, arguments),
    )


def list_workspace_files(
    workspace: Workspace,
    arguments: object,
) -> JSONObject:
    """List direct entries of a workspace directory without recursion."""

    directory_path = workspace.resolve(_get_requested_path("list_files", arguments))

    if not directory_path.is_dir():
        raise ToolArgumentError("list_files path must reference a directory.")

    try:
        children = sorted(
            directory_path.iterdir(),
            key=lambda child: child.name,
        )
    except OSError:
        raise ValueError("Unable to list workspace directory.") from None

    if len(children) > MAX_DIRECTORY_ENTRIES:
        raise ToolArgumentError("workspace directory contains too many entries.")

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
    """Read a bounded UTF-8 file or inclusive line range."""

    requested_path, line_start, line_end = _get_read_arguments(arguments)
    file_path = workspace.resolve(requested_path)

    if not file_path.is_file():
        raise ToolArgumentError("read_file path must reference a regular file.")

    try:
        size_bytes = file_path.stat().st_size
    except OSError:
        raise ValueError("Unable to inspect workspace file.") from None

    if size_bytes > MAX_FILE_SIZE_BYTES:
        raise ToolArgumentError(
            f"workspace file exceeds the {MAX_FILE_SIZE_BYTES}-byte limit."
        )

    try:
        with file_path.open("rb") as source:
            content_bytes = source.read(MAX_FILE_SIZE_BYTES + 1)
    except OSError:
        raise ValueError("Unable to read workspace file.") from None

    if len(content_bytes) > MAX_FILE_SIZE_BYTES:
        raise ToolArgumentError(
            f"workspace file exceeds the {MAX_FILE_SIZE_BYTES}-byte limit."
        )

    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise ToolArgumentError("read_file requires valid UTF-8.") from None

    result: JSONObject = {
        "path": _workspace_relative_path(workspace, file_path),
        "content": content,
        "size_bytes": len(content_bytes),
    }

    if line_start is None and line_end is None:
        return result

    partial_content, returned_start, returned_end, total_lines = _slice_file_content(
        content,
        line_start=line_start,
        line_end=line_end,
    )
    result.update(
        {
            "content": partial_content,
            "line_start": returned_start,
            "line_end": returned_end,
            "total_lines": total_lines,
            "truncated": returned_start > 1 or returned_end < total_lines,
        }
    )
    return result


def search_workspace_text(
    workspace: Workspace,
    arguments: object,
) -> JSONObject:
    """Search bounded UTF-8 text content inside the authorized workspace."""

    query, requested_path, case_sensitive = _get_search_arguments(arguments)
    search_path = workspace.resolve(requested_path)
    search_root = _workspace_relative_path(workspace, search_path)
    comparable_query = query if case_sensitive else query.lower()
    matches: list[JSONObject] = []
    files_inspected = 0
    truncated = False

    for file_path in _iter_search_files(search_path):
        if files_inspected >= MAX_SEARCH_FILES:
            truncated = True
            break

        files_inspected += 1
        content = _read_search_file(file_path)

        if content is None:
            try:
                if file_path.stat().st_size > MAX_SEARCH_FILE_BYTES:
                    truncated = True
            except OSError:
                pass
            continue

        canonical_file_path = workspace.resolve(file_path.relative_to(workspace.root))
        relative_file_path = _workspace_relative_path(workspace, canonical_file_path)

        for line_number, line in enumerate(content.splitlines(), start=1):
            comparable_line = line if case_sensitive else line.lower()
            match_index = comparable_line.find(comparable_query)

            if match_index < 0:
                continue

            if len(matches) >= MAX_SEARCH_MATCHES:
                return {
                    "path": search_root,
                    "matches": matches,
                    "truncated": True,
                }

            limited_line, line_was_truncated = _limit_search_line(line, match_index)
            truncated = truncated or line_was_truncated
            matches.append(
                {
                    "path": relative_file_path,
                    "line_number": line_number,
                    "line": limited_line,
                }
            )

    return {
        "path": search_root,
        "matches": matches,
        "truncated": truncated,
    }


def _get_requested_path(tool_name: str, arguments: object) -> Path:
    """Validate and convert a tool path argument."""

    if not isinstance(arguments, dict) or set(arguments) != {"path"}:
        raise ToolArgumentError(f"{tool_name} accepts only one path string argument.")

    path = arguments["path"]

    if not isinstance(path, str):
        raise ToolArgumentError(f"{tool_name} accepts only one path string argument.")

    return Path(path)


def _get_read_arguments(
    arguments: object,
) -> tuple[Path, int | None, int | None]:
    """Validate and normalize one full or partial file read request."""

    allowed_fields = {"path", "line_start", "line_end"}

    if (
        not isinstance(arguments, dict)
        or "path" not in arguments
        or set(arguments) - allowed_fields
    ):
        raise ToolArgumentError(
            "read_file accepts only path, line_start, and line_end, with path required."
        )

    path = arguments["path"]
    line_start = arguments.get("line_start")
    line_end = arguments.get("line_end")

    if not isinstance(path, str):
        raise ToolArgumentError("read_file path must be a string.")

    for field_name, value in (
        ("line_start", line_start),
        ("line_end", line_end),
    ):
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 1
        ):
            raise ToolArgumentError(
                f"read_file {field_name} must be a positive integer."
            )

    resolved_start = line_start or 1

    if line_end is not None and line_end < resolved_start:
        raise ToolArgumentError("read_file line_end must not be before line_start.")

    if line_end is not None and line_end - resolved_start + 1 > MAX_READ_LINES:
        raise ToolArgumentError(
            f"read_file ranges must not exceed {MAX_READ_LINES} lines."
        )

    return Path(path), line_start, line_end


def _slice_file_content(
    content: str,
    *,
    line_start: int | None,
    line_end: int | None,
) -> tuple[str, int, int, int]:
    """Return one inclusive bounded line range with deterministic metadata."""

    lines = content.splitlines(keepends=True)
    total_lines = len(lines)
    resolved_start = line_start or 1
    requested_end = line_end or resolved_start + MAX_READ_LINES - 1

    if resolved_start > total_lines:
        raise ToolArgumentError(
            "read_file line_start exceeds the total file line count."
        )

    selected_lines = lines[resolved_start - 1 : requested_end]
    returned_end = resolved_start + len(selected_lines) - 1

    return "".join(selected_lines), resolved_start, returned_end, total_lines


def _get_search_arguments(arguments: object) -> tuple[str, Path, bool]:
    """Validate and normalize portable search arguments."""

    if not isinstance(arguments, dict) or not {"query"} <= set(arguments):
        raise ToolArgumentError("search_text requires valid search arguments.")

    if set(arguments) - {"query", "path", "case_sensitive"}:
        raise ToolArgumentError("search_text requires valid search arguments.")

    query = arguments["query"]

    if not isinstance(query, str) or not query.strip():
        raise ToolArgumentError("search_text requires a non-blank query.")

    if len(query) > MAX_SEARCH_QUERY_LENGTH:
        raise ToolArgumentError(
            f"search query exceeds the {MAX_SEARCH_QUERY_LENGTH}-character limit."
        )

    path = arguments.get("path", ".")
    case_sensitive = arguments.get("case_sensitive", False)

    if not isinstance(path, str) or not isinstance(case_sensitive, bool):
        raise ToolArgumentError("search_text requires valid search arguments.")

    return query, Path(path), case_sensitive


def _iter_search_files(search_path: Path):
    """Yield regular search files in deterministic relative path order."""

    if search_path.is_file():
        yield search_path
        return

    if not search_path.is_dir():
        raise ToolArgumentError(
            "search_text path must reference a regular file or directory."
        )

    try:
        children = sorted(search_path.iterdir(), key=lambda child: child.name)
    except OSError:
        return

    for child in children:
        if child.is_symlink():
            continue

        if child.is_file():
            yield child
        elif child.is_dir():
            yield from _iter_search_files(child)


def _read_search_file(file_path: Path) -> str | None:
    """Return bounded valid UTF-8 content, skipping unreadable files safely."""

    try:
        if file_path.stat().st_size > MAX_SEARCH_FILE_BYTES:
            return None

        with file_path.open("rb") as source:
            content_bytes = source.read(MAX_SEARCH_FILE_BYTES + 1)
    except OSError:
        return None

    if len(content_bytes) > MAX_SEARCH_FILE_BYTES:
        return None

    try:
        return content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _limit_search_line(line: str, match_index: int) -> tuple[str, bool]:
    """Return a bounded matching line while retaining the literal match."""

    if len(line) <= MAX_SEARCH_LINE_LENGTH:
        return line, False

    maximum_content_length = MAX_SEARCH_LINE_LENGTH - 3
    start = min(match_index, len(line) - maximum_content_length)

    return f"{line[start : start + maximum_content_length]}...", True


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
