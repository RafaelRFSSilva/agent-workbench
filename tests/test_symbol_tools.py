"""Tests for safe provider-independent Python symbol search."""

from pathlib import Path

import pytest

from agent_workbench.errors import WorkspacePathError
from agent_workbench.symbol_tools import (
    MAX_QUALIFIED_NAME_LENGTH,
    MAX_SYMBOL_FILE_BYTES,
    MAX_SYMBOL_FILES,
    MAX_SYMBOL_MATCHES,
    MAX_SYMBOL_QUERY_LENGTH,
    register_symbol_tools,
    search_workspace_symbols,
)
from agent_workbench.tool_registry import ToolRegistry
from agent_workbench.tools import ToolDefinition
from agent_workbench.workspace import (
    DEFAULT_IGNORED_TRAVERSAL_DIRECTORY_NAMES,
    Workspace,
)


def create_workspace(tmp_path: Path) -> tuple[Path, Workspace]:
    """Create an empty authorized workspace."""

    root = tmp_path / "workspace"
    root.mkdir()
    return root, Workspace(root)


def create_existing_definition() -> ToolDefinition:
    """Create a tool definition that must remain before symbol search."""

    return ToolDefinition(
        name="existing",
        description="Return an existing value.",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    )


def test_registers_exact_symbol_tool_and_preserves_existing_tools(
    tmp_path: Path,
) -> None:
    """Append the exact portable symbol definition to an existing registry."""

    _, workspace = create_workspace(tmp_path)
    existing = create_existing_definition()
    registry = ToolRegistry()
    registry.register(existing, lambda arguments: {"value": "existing"})

    register_symbol_tools(registry, workspace)

    assert registry.definitions[0] == existing
    definition = registry.definitions[1]
    assert definition.name == "search_symbols"
    assert definition.description == (
        "Search Python symbol definitions inside the authorized workspace."
    )
    assert definition.input_schema == {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "path": {"type": "string"},
            "kind": {
                "type": "string",
                "enum": ["any", "class", "function", "method"],
            },
            "case_sensitive": {"type": "boolean"},
        },
        "required": ["query"],
        "additionalProperties": False,
    }


def test_searches_root_with_complete_stable_output_and_symbol_kinds(
    tmp_path: Path,
) -> None:
    """Return classes, functions, methods, async flags, and qualified names."""

    root, workspace = create_workspace(tmp_path)
    source = root / "symbols.py"
    source.write_text(
        """
class ProbeClass:
    class InnerProbe:
        async def inspect_probe(self):
            pass

    def instance_probe(self):
        def nested_probe():
            pass

    @classmethod
    def class_probe(cls):
        pass

    @staticmethod
    def static_probe():
        pass

async def async_probe():
    pass

def outer_probe():
    def inner_probe():
        pass
""".lstrip(),
        encoding="utf-8",
    )

    result = search_workspace_symbols(workspace, {"query": "probe"})

    assert result == {
        "query": "probe",
        "path": ".",
        "kind": "any",
        "case_sensitive": False,
        "matches": [
            {
                "path": "symbols.py",
                "name": "ProbeClass",
                "qualified_name": "ProbeClass",
                "kind": "class",
                "is_async": False,
                "line_number": 1,
            },
            {
                "path": "symbols.py",
                "name": "InnerProbe",
                "qualified_name": "ProbeClass.InnerProbe",
                "kind": "class",
                "is_async": False,
                "line_number": 2,
            },
            {
                "path": "symbols.py",
                "name": "inspect_probe",
                "qualified_name": "ProbeClass.InnerProbe.inspect_probe",
                "kind": "method",
                "is_async": True,
                "line_number": 3,
            },
            {
                "path": "symbols.py",
                "name": "instance_probe",
                "qualified_name": "ProbeClass.instance_probe",
                "kind": "method",
                "is_async": False,
                "line_number": 6,
            },
            {
                "path": "symbols.py",
                "name": "nested_probe",
                "qualified_name": "ProbeClass.instance_probe.nested_probe",
                "kind": "method",
                "is_async": False,
                "line_number": 7,
            },
            {
                "path": "symbols.py",
                "name": "class_probe",
                "qualified_name": "ProbeClass.class_probe",
                "kind": "method",
                "is_async": False,
                "line_number": 11,
            },
            {
                "path": "symbols.py",
                "name": "static_probe",
                "qualified_name": "ProbeClass.static_probe",
                "kind": "method",
                "is_async": False,
                "line_number": 15,
            },
            {
                "path": "symbols.py",
                "name": "async_probe",
                "qualified_name": "async_probe",
                "kind": "function",
                "is_async": True,
                "line_number": 18,
            },
            {
                "path": "symbols.py",
                "name": "outer_probe",
                "qualified_name": "outer_probe",
                "kind": "function",
                "is_async": False,
                "line_number": 21,
            },
            {
                "path": "symbols.py",
                "name": "inner_probe",
                "qualified_name": "outer_probe.inner_probe",
                "kind": "function",
                "is_async": False,
                "line_number": 22,
            },
        ],
        "files_inspected": 1,
        "files_skipped": 0,
        "truncated": False,
    }


def test_supports_nested_directory_and_explicit_python_file_search(
    tmp_path: Path,
) -> None:
    """Search canonical nested directories and individual Python files."""

    root, workspace = create_workspace(tmp_path)
    nested = root / "src" / "package" / "module.py"
    nested.parent.mkdir(parents=True)
    nested.write_text("def target_symbol():\n    pass\n", encoding="utf-8")
    (root / "outside.py").write_text(
        "def target_outside():\n    pass\n", encoding="utf-8"
    )

    directory_result = search_workspace_symbols(
        workspace,
        {"query": "target", "path": "src/./package"},
    )
    file_result = search_workspace_symbols(
        workspace,
        {"query": "target", "path": "src/package/../package/module.py"},
    )

    assert directory_result["path"] == "src/package"
    assert file_result["path"] == "src/package/module.py"
    assert directory_result["matches"] == file_result["matches"]
    assert directory_result["matches"][0]["path"] == "src/package/module.py"


@pytest.mark.parametrize(
    ("kind", "expected_names"),
    [
        ("any", ["TargetClass", "target_method", "target_function"]),
        ("class", ["TargetClass"]),
        ("function", ["target_function"]),
        ("method", ["target_method"]),
    ],
)
def test_filters_supported_symbol_kinds(
    tmp_path: Path,
    kind: str,
    expected_names: list[str],
) -> None:
    """Filter classes, functions, and methods without provider behavior."""

    root, workspace = create_workspace(tmp_path)
    (root / "module.py").write_text(
        "class TargetClass:\n"
        "    def target_method(self):\n"
        "        pass\n"
        "def target_function():\n"
        "    pass\n",
        encoding="utf-8",
    )

    result = search_workspace_symbols(
        workspace,
        {"query": "target", "kind": kind},
    )

    assert [match["name"] for match in result["matches"]] == expected_names
    assert result["kind"] == kind


def test_matches_names_and_qualified_names_with_case_control(tmp_path: Path) -> None:
    """Use literal name and qualified-name substring matching with explicit casing."""

    root, workspace = create_workspace(tmp_path)
    (root / "module.py").write_text(
        "class OuterScope:\n    def child(self):\n        pass\n",
        encoding="utf-8",
    )

    name_result = search_workspace_symbols(workspace, {"query": "CHILD"})
    qualified_result = search_workspace_symbols(
        workspace,
        {"query": "OuterScope.child", "kind": "method"},
    )
    sensitive_result = search_workspace_symbols(
        workspace,
        {"query": "CHILD", "case_sensitive": True},
    )

    assert [match["name"] for match in name_result["matches"]] == ["child"]
    assert [match["qualified_name"] for match in qualified_result["matches"]] == [
        "OuterScope.child"
    ]
    assert sensitive_result["matches"] == []


def test_rejects_blank_invalid_and_oversized_queries(tmp_path: Path) -> None:
    """Validate all portable search arguments and query length boundaries."""

    _, workspace = create_workspace(tmp_path)
    boundary_query = "a" * MAX_SYMBOL_QUERY_LENGTH

    boundary_result = search_workspace_symbols(workspace, {"query": boundary_query})
    assert boundary_result["query"] == boundary_query

    with pytest.raises(ValueError, match="non-blank query"):
        search_workspace_symbols(workspace, {"query": "  "})

    with pytest.raises(ValueError, match="query exceeds"):
        search_workspace_symbols(
            workspace,
            {"query": "a" * (MAX_SYMBOL_QUERY_LENGTH + 1)},
        )

    with pytest.raises(ValueError, match="search arguments"):
        search_workspace_symbols(workspace, {"query": "a", "kind": "invalid"})

    with pytest.raises(ValueError, match="search arguments"):
        search_workspace_symbols(workspace, {"query": "a", "kind": []})


def test_decorated_symbols_are_parsed_without_executing_project_code(
    tmp_path: Path,
) -> None:
    """Use AST parsing without evaluating decorators or top-level statements."""

    root, workspace = create_workspace(tmp_path)
    marker = root / "executed.marker"
    (root / "dangerous.py").write_text(
        "from pathlib import Path\n"
        "Path('executed.marker').write_text('executed')\n"
        "@unknown_decorator\n"
        "def decorated_symbol():\n"
        "    pass\n",
        encoding="utf-8",
    )

    result = search_workspace_symbols(workspace, {"query": "decorated"})

    assert [match["name"] for match in result["matches"]] == ["decorated_symbol"]
    assert not marker.exists()


def test_orders_results_by_path_line_and_qualified_name_without_duplicates(
    tmp_path: Path,
) -> None:
    """Return deterministic unique matches across hidden and normal paths."""

    root, workspace = create_workspace(tmp_path)
    hidden = root / ".hidden" / "alpha.py"
    hidden.parent.mkdir()
    hidden.write_text("def ordered_symbol():\n    pass\n", encoding="utf-8")
    (root / "zeta.py").write_text(
        "def ordered_symbol():\n    pass\n",
        encoding="utf-8",
    )
    (root / "notes.txt").write_text("def ordered_symbol(): pass", encoding="utf-8")

    first_result = search_workspace_symbols(workspace, {"query": "ordered"})
    second_result = search_workspace_symbols(workspace, {"query": "ordered"})

    assert [match["path"] for match in first_result["matches"]] == [
        ".hidden/alpha.py",
        "zeta.py",
    ]
    assert first_result == second_result
    assert len(first_result["matches"]) == len(
        {
            (match["path"], match["line_number"], match["qualified_name"])
            for match in first_result["matches"]
        }
    )


def test_directory_search_skips_generated_directories_deterministically(
    tmp_path: Path,
) -> None:
    """Inspect ordered source files without entering centralized ignored paths."""

    root, workspace = create_workspace(tmp_path)
    alpha = root / "alpha"
    alpha.mkdir()
    (alpha / "source.py").write_text(
        "def target_alpha():\n    pass\n",
        encoding="utf-8",
    )
    (root / "zeta.py").write_text(
        "def target_zeta():\n    pass\n",
        encoding="utf-8",
    )
    for directory_name in DEFAULT_IGNORED_TRAVERSAL_DIRECTORY_NAMES:
        ignored = root / directory_name
        ignored.mkdir()
        (ignored / "ignored.py").write_text(
            "def target_ignored():\n    pass\n",
            encoding="utf-8",
        )

    result = search_workspace_symbols(workspace, {"query": "target"})

    assert [match["path"] for match in result["matches"]] == [
        "alpha/source.py",
        "zeta.py",
    ]
    assert result["files_inspected"] == 2
    assert result["files_skipped"] == 0


def test_directory_search_skips_invalid_syntax_encoding_and_oversized_files(
    tmp_path: Path,
) -> None:
    """Count unsafe or unsupported Python files as skipped without leaking details."""

    root, workspace = create_workspace(tmp_path)
    (root / "valid.py").write_text("def safe_symbol():\n    pass\n", encoding="utf-8")
    (root / "syntax.py").write_text("def broken(:\n", encoding="utf-8")
    (root / "invalid.py").write_bytes(b"\xff\xfe")
    (root / "large.py").write_bytes(b"#" * (MAX_SYMBOL_FILE_BYTES + 1))

    result = search_workspace_symbols(workspace, {"query": "symbol"})

    assert [match["name"] for match in result["matches"]] == ["safe_symbol"]
    assert result["files_inspected"] == 4
    assert result["files_skipped"] == 3
    assert str(root) not in str(result)


@pytest.mark.parametrize(
    ("filename", "content", "message"),
    [
        ("notes.txt", b"text", "requires a regular .py file"),
        ("invalid.py", b"\xff", "valid UTF-8"),
        ("syntax.py", b"def broken(:\n", "valid Python syntax"),
        ("large.py", b"#" * (MAX_SYMBOL_FILE_BYTES + 1), "exceeds"),
    ],
)
def test_explicit_file_search_reports_concise_validation_errors(
    tmp_path: Path,
    filename: str,
    content: bytes,
    message: str,
) -> None:
    """Reject invalid explicit files with stable messages and no absolute path."""

    root, workspace = create_workspace(tmp_path)
    (root / filename).write_bytes(content)

    with pytest.raises(ValueError, match=message) as exc_info:
        search_workspace_symbols(workspace, {"query": "symbol", "path": filename})

    assert str(root) not in str(exc_info.value)


def test_enforces_file_and_match_limits_with_truncation(tmp_path: Path) -> None:
    """Inspect and return only the deterministic bounded prefixes."""

    root, workspace = create_workspace(tmp_path)
    for index in range(MAX_SYMBOL_FILES + 1):
        (root / f"file-{index:03d}.py").write_text(
            "def unrelated():\n    pass\n",
            encoding="utf-8",
        )

    file_limited_result = search_workspace_symbols(workspace, {"query": "target"})

    assert file_limited_result["files_inspected"] == MAX_SYMBOL_FILES
    assert file_limited_result["truncated"] is True

    match_root = root / "matches"
    match_root.mkdir()
    (match_root / "symbols.py").write_text(
        "".join(
            f"def target_{index:03d}():\n    pass\n"
            for index in range(MAX_SYMBOL_MATCHES + 1)
        ),
        encoding="utf-8",
    )

    match_limited_result = search_workspace_symbols(
        workspace,
        {"query": "target", "path": "matches"},
    )

    assert len(match_limited_result["matches"]) == MAX_SYMBOL_MATCHES
    assert match_limited_result["truncated"] is True


def test_skips_symbols_with_qualified_names_above_the_limit(tmp_path: Path) -> None:
    """Never return a qualified name longer than the documented cap."""

    root, workspace = create_workspace(tmp_path)
    outer_name = "Outer" + "A" * 250
    inner_name = "Inner" + "B" * 250
    method_name = "target_method"
    (root / "long.py").write_text(
        f"class {outer_name}:\n"
        f"    class {inner_name}:\n"
        f"        def {method_name}(self):\n"
        "            pass\n",
        encoding="utf-8",
    )

    result = search_workspace_symbols(workspace, {"query": "target_method"})

    assert result["matches"] == []
    assert result["truncated"] is True
    assert all(
        len(match["qualified_name"]) <= MAX_QUALIFIED_NAME_LENGTH
        for match in result["matches"]
    )


def test_preserves_workspace_containment_and_recursive_symlink_boundaries(
    tmp_path: Path,
) -> None:
    """Reject unsafe explicit paths and skip symlinks found during recursion."""

    root, workspace = create_workspace(tmp_path)
    external = tmp_path / "external.py"
    external.write_text("def secret_symbol():\n    pass\n", encoding="utf-8")
    external_directory = tmp_path / "external"
    external_directory.mkdir()
    (external_directory / "secret.py").write_text(
        "def secret_symbol():\n    pass\n",
        encoding="utf-8",
    )
    (root / "external-file.py").symlink_to(external)
    (root / "external-directory").symlink_to(
        external_directory,
        target_is_directory=True,
    )
    (root / "broken.py").symlink_to(root / "missing.py")

    with pytest.raises(WorkspacePathError):
        search_workspace_symbols(
            workspace, {"query": "secret", "path": "../external.py"}
        )
    with pytest.raises(WorkspacePathError):
        search_workspace_symbols(
            workspace,
            {"query": "secret", "path": "external-file.py"},
        )
    with pytest.raises(WorkspacePathError):
        search_workspace_symbols(workspace, {"query": "secret", "path": "broken.py"})

    recursive_result = search_workspace_symbols(workspace, {"query": "secret"})
    assert recursive_result["matches"] == []
    assert str(tmp_path) not in str(recursive_result)


def test_accepts_explicit_internal_python_file_symlink(tmp_path: Path) -> None:
    """Search an internal file symlink through its canonical target path."""

    root, workspace = create_workspace(tmp_path)
    target = root / "src" / "target.py"
    target.parent.mkdir()
    target.write_text("def linked_symbol():\n    pass\n", encoding="utf-8")
    (root / "link.py").symlink_to(target)

    result = search_workspace_symbols(
        workspace,
        {"query": "linked", "path": "link.py"},
    )

    assert result["path"] == "src/target.py"
    assert result["matches"][0]["path"] == "src/target.py"


def test_does_not_mutate_inputs_or_share_returned_structure(tmp_path: Path) -> None:
    """Keep caller arguments and future results independent of prior mutation."""

    root, workspace = create_workspace(tmp_path)
    (root / "module.py").write_text(
        "def stable_symbol():\n    pass\n", encoding="utf-8"
    )
    arguments = {"query": "stable", "path": "."}
    original_arguments = dict(arguments)

    first_result = search_workspace_symbols(workspace, arguments)
    first_result["matches"].clear()
    second_result = search_workspace_symbols(workspace, arguments)

    assert arguments == original_arguments
    assert [match["name"] for match in second_result["matches"]] == ["stable_symbol"]
