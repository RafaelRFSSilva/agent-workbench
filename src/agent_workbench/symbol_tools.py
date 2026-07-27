"""Provider-independent safe Python symbol search."""

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agent_workbench.tool_registry import ToolRegistry
from agent_workbench.tools import JSONObject, ToolDefinition
from agent_workbench.workspace import Workspace
from agent_workbench.workspace_tools import MAX_FILE_SIZE_BYTES

MAX_SYMBOL_QUERY_LENGTH = 256
"""Maximum characters accepted in a symbol query."""

MAX_SYMBOL_FILES = 512
"""Maximum Python files inspected by one symbol search."""

MAX_SYMBOL_FILE_BYTES = MAX_FILE_SIZE_BYTES
"""Maximum bytes inspected from one Python file."""

MAX_SYMBOL_MATCHES = 256
"""Maximum symbol definitions returned by one search."""

MAX_QUALIFIED_NAME_LENGTH = 512
"""Maximum characters returned in one qualified symbol name."""

type SymbolKind = Literal["class", "function", "method"]
type SymbolKindFilter = Literal["any", "class", "function", "method"]

SEARCH_SYMBOLS_DEFINITION = ToolDefinition(
    name="search_symbols",
    description="Search Python symbol definitions inside the authorized workspace.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
            },
            "path": {
                "type": "string",
            },
            "kind": {
                "type": "string",
                "enum": ["any", "class", "function", "method"],
            },
            "case_sensitive": {
                "type": "boolean",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
)


@dataclass(frozen=True, slots=True)
class _Symbol:
    """Represent one lexical Python definition before JSON conversion."""

    name: str
    qualified_name: str
    kind: SymbolKind
    is_async: bool
    line_number: int


class _SymbolCollector(ast.NodeVisitor):
    """Collect matching definitions using lexical AST scope only."""

    def __init__(
        self,
        query: str,
        kind: SymbolKindFilter,
        case_sensitive: bool,
    ) -> None:
        self._query = query if case_sensitive else query.lower()
        self._kind = kind
        self._case_sensitive = case_sensitive
        self._scope: list[tuple[str, bool]] = []
        self.symbols: list[_Symbol] = []
        self.truncated = False

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Collect one class and visit definitions in its lexical scope."""

        if self._match_limit_reached:
            return

        self._collect(node.name, "class", False, node.lineno)
        self._scope.append((node.name, True))
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Collect one synchronous function or method."""

        self._visit_function(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Collect one asynchronous function or method."""

        self._visit_function(node, is_async=True)

    @property
    def _match_limit_reached(self) -> bool:
        return len(self.symbols) >= MAX_SYMBOL_MATCHES and self.truncated

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        is_async: bool,
    ) -> None:
        """Collect a function using enclosing class scope for classification."""

        if self._match_limit_reached:
            return

        kind: SymbolKind = (
            "method" if any(is_class for _, is_class in self._scope) else "function"
        )
        self._collect(node.name, kind, is_async, node.lineno)
        self._scope.append((node.name, False))
        self.generic_visit(node)
        self._scope.pop()

    def _collect(
        self,
        name: str,
        kind: SymbolKind,
        is_async: bool,
        line_number: int,
    ) -> None:
        """Collect one unique bounded match when filters allow it."""

        qualified_name = ".".join([*(name for name, _ in self._scope), name])
        comparable_name = name if self._case_sensitive else name.lower()
        comparable_qualified_name = (
            qualified_name if self._case_sensitive else qualified_name.lower()
        )

        if self._kind != "any" and self._kind != kind:
            return

        if self._query not in comparable_name and self._query not in (
            comparable_qualified_name
        ):
            return

        if len(qualified_name) > MAX_QUALIFIED_NAME_LENGTH:
            self.truncated = True
            return

        symbol = _Symbol(
            name=name,
            qualified_name=qualified_name,
            kind=kind,
            is_async=is_async,
            line_number=line_number,
        )

        if symbol in self.symbols:
            return

        if len(self.symbols) >= MAX_SYMBOL_MATCHES:
            self.truncated = True
            return

        self.symbols.append(symbol)


def register_symbol_tools(
    registry: ToolRegistry,
    workspace: Workspace,
) -> None:
    """Append safe Python symbol search to a provider-independent registry."""

    registry.register(
        SEARCH_SYMBOLS_DEFINITION,
        lambda arguments: search_workspace_symbols(workspace, arguments),
    )


def search_workspace_symbols(
    workspace: Workspace,
    arguments: object,
) -> JSONObject:
    """Search bounded Python definitions without importing or executing code."""

    query, requested_path, kind, case_sensitive = _get_symbol_arguments(arguments)
    search_path = workspace.resolve(requested_path)
    search_root = _workspace_relative_path(workspace, search_path)

    if search_path.is_file():
        _validate_explicit_python_file(search_path)
        source = _read_explicit_python_file(search_path)
        tree = _parse_explicit_python_file(source)
        collector = _SymbolCollector(query, kind, case_sensitive)
        collector.visit(tree)
        matches = _symbol_matches(search_root, collector.symbols)
        return _symbol_result(
            query=query,
            path=search_root,
            kind=kind,
            case_sensitive=case_sensitive,
            matches=matches,
            files_inspected=1,
            files_skipped=0,
            truncated=collector.truncated,
        )

    if not search_path.is_dir():
        raise ValueError("search_symbols requires a regular .py file or directory.")

    matches: list[JSONObject] = []
    seen_files: set[Path] = set()
    files_inspected = 0
    files_skipped = 0
    truncated = False

    for discovered_path in _iter_python_files(search_path):
        canonical_path = workspace.resolve(discovered_path.relative_to(workspace.root))

        if canonical_path in seen_files:
            continue

        seen_files.add(canonical_path)

        if files_inspected >= MAX_SYMBOL_FILES:
            truncated = True
            break

        files_inspected += 1
        source = _read_directory_python_file(canonical_path)

        if source is None:
            files_skipped += 1
            continue

        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            files_skipped += 1
            continue

        collector = _SymbolCollector(query, kind, case_sensitive)
        collector.visit(tree)
        relative_path = _workspace_relative_path(workspace, canonical_path)
        truncated = truncated or collector.truncated

        for match in _symbol_matches(relative_path, collector.symbols):
            if len(matches) >= MAX_SYMBOL_MATCHES:
                truncated = True
                break
            if match not in matches:
                matches.append(match)

        if len(matches) >= MAX_SYMBOL_MATCHES:
            truncated = True
            break

    matches.sort(
        key=lambda match: (
            str(match["path"]),
            int(match["line_number"]),
            str(match["qualified_name"]),
        )
    )

    return _symbol_result(
        query=query,
        path=search_root,
        kind=kind,
        case_sensitive=case_sensitive,
        matches=matches,
        files_inspected=files_inspected,
        files_skipped=files_skipped,
        truncated=truncated,
    )


def _get_symbol_arguments(
    arguments: object,
) -> tuple[str, Path, SymbolKindFilter, bool]:
    """Validate and normalize provider-independent symbol arguments."""

    allowed_fields = {"query", "path", "kind", "case_sensitive"}

    if (
        not isinstance(arguments, dict)
        or "query" not in arguments
        or set(arguments) - allowed_fields
    ):
        raise ValueError("search_symbols requires symbol search arguments.")

    query = arguments["query"]
    path = arguments.get("path", ".")
    kind = arguments.get("kind", "any")
    case_sensitive = arguments.get("case_sensitive", False)

    if not isinstance(query, str) or not query.strip():
        raise ValueError("search_symbols requires a non-blank query.")

    if len(query) > MAX_SYMBOL_QUERY_LENGTH:
        raise ValueError(
            f"symbol query exceeds the {MAX_SYMBOL_QUERY_LENGTH}-character limit."
        )

    if (
        not isinstance(path, str)
        or not isinstance(kind, str)
        or kind not in {"any", "class", "function", "method"}
        or not isinstance(case_sensitive, bool)
    ):
        raise ValueError("search_symbols requires symbol search arguments.")

    return query, Path(path), kind, case_sensitive


def _iter_python_files(search_path: Path):
    """Yield non-symlink regular Python files in deterministic path order."""

    try:
        children = sorted(search_path.iterdir(), key=lambda child: child.name)
    except OSError:
        return

    for child in children:
        if child.is_symlink():
            continue
        if child.is_file() and child.suffix == ".py":
            yield child
        elif child.is_dir():
            yield from _iter_python_files(child)


def _validate_explicit_python_file(file_path: Path) -> None:
    """Require one canonical regular Python file for explicit inspection."""

    if not file_path.is_file() or file_path.suffix != ".py":
        raise ValueError("search_symbols requires a regular .py file.")


def _read_explicit_python_file(file_path: Path) -> str:
    """Read one explicit Python file with stable validation errors."""

    try:
        size_bytes = file_path.stat().st_size
    except OSError:
        raise ValueError("Unable to inspect the requested Python file.") from None

    if size_bytes > MAX_SYMBOL_FILE_BYTES:
        raise ValueError(f"Python file exceeds the {MAX_SYMBOL_FILE_BYTES}-byte limit.")

    try:
        with file_path.open("rb") as source:
            content_bytes = source.read(MAX_SYMBOL_FILE_BYTES + 1)
    except OSError:
        raise ValueError("Unable to read the requested Python file.") from None

    if len(content_bytes) > MAX_SYMBOL_FILE_BYTES:
        raise ValueError(f"Python file exceeds the {MAX_SYMBOL_FILE_BYTES}-byte limit.")

    try:
        return content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("search_symbols requires valid UTF-8.") from None


def _read_directory_python_file(file_path: Path) -> str | None:
    """Read a discovered Python file, skipping unsafe content without details."""

    try:
        if file_path.stat().st_size > MAX_SYMBOL_FILE_BYTES:
            return None
        with file_path.open("rb") as source:
            content_bytes = source.read(MAX_SYMBOL_FILE_BYTES + 1)
    except OSError:
        return None

    if len(content_bytes) > MAX_SYMBOL_FILE_BYTES:
        return None

    try:
        return content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _parse_explicit_python_file(source: str) -> ast.Module:
    """Parse one explicit file with a concise safe syntax error."""

    try:
        return ast.parse(source)
    except (SyntaxError, ValueError):
        raise ValueError("search_symbols requires valid Python syntax.") from None


def _symbol_matches(path: str, symbols: list[_Symbol]) -> list[JSONObject]:
    """Convert lexical symbols into strict portable match objects."""

    return [
        {
            "path": path,
            "name": symbol.name,
            "qualified_name": symbol.qualified_name,
            "kind": symbol.kind,
            "is_async": symbol.is_async,
            "line_number": symbol.line_number,
        }
        for symbol in symbols
    ]


def _symbol_result(
    *,
    query: str,
    path: str,
    kind: SymbolKindFilter,
    case_sensitive: bool,
    matches: list[JSONObject],
    files_inspected: int,
    files_skipped: int,
    truncated: bool,
) -> JSONObject:
    """Build one strict stable JSON-compatible symbol result."""

    return {
        "query": query,
        "path": path,
        "kind": kind,
        "case_sensitive": case_sensitive,
        "matches": matches,
        "files_inspected": files_inspected,
        "files_skipped": files_skipped,
        "truncated": truncated,
    }


def _workspace_relative_path(workspace: Workspace, path: Path) -> str:
    """Return a canonical portable workspace-relative path."""

    relative_path = path.relative_to(workspace.root).as_posix()
    return relative_path or "."
