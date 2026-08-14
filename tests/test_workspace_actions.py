"""Tests for approved optimistic single-file workspace patches."""

import hashlib
import os
import stat
from pathlib import Path

import pytest

from agent_workbench.errors import CompletionError, WorkspaceTransactionError
from agent_workbench.messages import ChatRequest, ChatResponse
from agent_workbench.tool_calling import run_tool_calling_loop
from agent_workbench.tool_registry import ToolRegistry
from agent_workbench.tools import (
    ToolApprovalDecision,
    ToolDefinition,
    ToolInvocation,
)
from agent_workbench.workspace import Workspace
from agent_workbench.workspace_actions import (
    APPLY_FILE_PATCH_DEFINITION,
    APPLY_FILE_REWRITE_DEFINITION,
    APPLY_LINE_RANGE_REPLACEMENT_DEFINITION,
    APPLY_TEXT_REPLACEMENT_DEFINITION,
    APPLY_WORKSPACE_CHANGES_DEFINITION,
    MAX_CHANGED_LINES,
    MAX_PATCH_CONTENT_BYTES,
    MAX_TEXT_REPLACEMENT_BYTES,
    MAX_TEXT_REPLACEMENT_OCCURRENCES,
    MAX_TRANSACTION_CHANGED_LINES,
    MAX_TRANSACTION_EXPECTED_BYTES,
    MAX_TRANSACTION_FILES,
    MAX_TRANSACTION_REPLACEMENT_BYTES,
    apply_file_patch,
    apply_file_rewrite,
    apply_line_range_replacement,
    apply_text_replacement,
    apply_workspace_changes,
    preview_file_patch,
    preview_file_rewrite,
    preview_line_range_replacement,
    preview_text_replacement,
    preview_workspace_changes,
    register_workspace_action_tools,
)


class FakeProvider:
    """Return configured tool-calling responses."""

    name = "Fake"
    model_name = "fake-model"

    def __init__(self, responses: list[ChatResponse]) -> None:
        self._responses = iter(responses)

    def complete(self, request: ChatRequest) -> ChatResponse:
        """Return the next configured response."""

        return next(self._responses)


def create_workspace(tmp_path: Path) -> tuple[Path, Workspace]:
    """Create one authorized empty workspace."""

    root = tmp_path / "workspace"
    root.mkdir()
    return root, Workspace(root)


def patch_arguments(
    *,
    path: str = "module.py",
    expected: str = "value = 1\n",
    replacement: str = "value = 2\n",
    create: bool = False,
) -> dict[str, object]:
    """Create one valid patch argument mapping."""

    return {
        "path": path,
        "expected_content": expected,
        "replacement_content": replacement,
        "create_if_missing": create,
    }


def text_replacement_arguments(
    *,
    path: str = "module.py",
    expected: str = "value = 1",
    replacement: str = "value = 2",
    file_content: str = "value = 1\n",
    expected_sha256: str | None = None,
    occurrences: int | None = None,
) -> dict[str, object]:
    """Create one valid literal text-replacement argument mapping."""

    arguments: dict[str, object] = {
        "path": path,
        "expected_text": expected,
        "replacement_text": replacement,
        "expected_file_sha256": (
            expected_sha256
            if expected_sha256 is not None
            else hashlib.sha256(file_content.encode("utf-8")).hexdigest()
        ),
    }
    if occurrences is not None:
        arguments["expected_occurrences"] = occurrences
    return arguments


def file_rewrite_arguments(
    *,
    path: str = "module.py",
    file_content: str = "value = 1\n",
    replacement: str = "value = 2\n",
    expected_sha256: str | None = None,
) -> dict[str, object]:
    """Create one valid SHA-guarded whole-file rewrite mapping."""

    return {
        "path": path,
        "expected_file_sha256": (
            expected_sha256
            if expected_sha256 is not None
            else hashlib.sha256(file_content.encode("utf-8")).hexdigest()
        ),
        "replacement_content": replacement,
    }


def line_range_replacement_arguments(
    *,
    path: str = "module.py",
    start_line: int = 1,
    end_line: int = 1,
    replacement: str = "value = 2\n",
    file_content: str = "value = 1\n",
    expected_sha256: str | None = None,
) -> dict[str, object]:
    """Create one valid SHA-guarded line-range replacement mapping."""

    return {
        "path": path,
        "start_line": start_line,
        "end_line": end_line,
        "replacement_content": replacement,
        "expected_file_sha256": (
            expected_sha256
            if expected_sha256 is not None
            else hashlib.sha256(file_content.encode("utf-8")).hexdigest()
        ),
    }


def transaction_arguments(
    *changes: dict[str, object],
) -> dict[str, object]:
    """Create one transaction argument mapping."""

    return {"changes": list(changes)}


def invoke_patch(
    registry: ToolRegistry,
    arguments: dict[str, object],
    *,
    decision: ToolApprovalDecision | None,
) -> None:
    """Run one patch invocation through the provider-independent loop."""

    provider = FakeProvider(
        [
            ChatResponse(
                text="",
                tool_invocations=(
                    ToolInvocation(
                        id="patch-1",
                        tool_name="apply_file_patch",
                        arguments=arguments,
                    ),
                ),
            ),
            ChatResponse(text="Done."),
        ]
    )
    handler = None if decision is None else lambda request: decision
    run_tool_calling_loop(
        provider,
        ChatRequest(messages=[]),
        registry,
        max_tool_rounds=1,
        tool_approval_handler=handler,
    )


def invoke_text_replacement(
    registry: ToolRegistry,
    arguments: dict[str, object],
    *,
    decision: ToolApprovalDecision | None,
    before_approval=None,
) -> None:
    """Run one literal replacement through the provider-independent loop."""

    provider = FakeProvider(
        [
            ChatResponse(
                text="",
                tool_invocations=(
                    ToolInvocation(
                        id="replacement-1",
                        tool_name="apply_text_replacement",
                        arguments=arguments,
                    ),
                ),
            ),
            ChatResponse(text="Done."),
        ]
    )

    def decide(request):
        if before_approval is not None:
            before_approval()
        assert decision is not None
        return decision

    run_tool_calling_loop(
        provider,
        ChatRequest(messages=[]),
        registry,
        max_tool_rounds=1,
        tool_approval_handler=None if decision is None else decide,
    )


def invoke_line_range_replacement(
    registry: ToolRegistry,
    arguments: dict[str, object],
    *,
    decision: ToolApprovalDecision | None,
    before_approval=None,
) -> None:
    """Run one line-range replacement through the provider-independent loop."""

    provider = FakeProvider(
        [
            ChatResponse(
                text="",
                tool_invocations=(
                    ToolInvocation(
                        id="line-range-1",
                        tool_name="apply_line_range_replacement",
                        arguments=arguments,
                    ),
                ),
            ),
            ChatResponse(text="Done."),
        ]
    )

    def decide(request):
        if before_approval is not None:
            before_approval()
        assert decision is not None
        return decision

    run_tool_calling_loop(
        provider,
        ChatRequest(messages=[]),
        registry,
        max_tool_rounds=1,
        tool_approval_handler=None if decision is None else decide,
    )


def test_registration_preserves_existing_tools_and_exact_definition(
    tmp_path: Path,
) -> None:
    """Append one approval-required tool with a closed portable schema."""

    _, workspace = create_workspace(tmp_path)
    existing = ToolDefinition(
        name="existing",
        description="Existing tool.",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    )
    registry = ToolRegistry()
    registry.register(existing, lambda arguments: None)

    register_workspace_action_tools(registry, workspace)

    assert registry.definitions == (
        existing,
        APPLY_FILE_PATCH_DEFINITION,
        APPLY_FILE_REWRITE_DEFINITION,
        APPLY_TEXT_REPLACEMENT_DEFINITION,
        APPLY_LINE_RANGE_REPLACEMENT_DEFINITION,
        APPLY_WORKSPACE_CHANGES_DEFINITION,
    )
    assert APPLY_FILE_PATCH_DEFINITION.name == "apply_file_patch"
    assert APPLY_FILE_PATCH_DEFINITION.description == (
        "Apply one approved optimistic UTF-8 file patch inside the authorized "
        "workspace when complete exact current content is known or creating a file. "
        "Successful results include resulting_file_sha256 for chained edits."
    )
    assert APPLY_FILE_PATCH_DEFINITION.input_schema == {
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
    assert registry.requires_approval(
        ToolInvocation(
            id="patch",
            tool_name="apply_file_patch",
            arguments=patch_arguments(),
        )
    )
    assert APPLY_LINE_RANGE_REPLACEMENT_DEFINITION.input_schema == {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1},
            "replacement_content": {"type": "string"},
            "expected_file_sha256": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
            },
        },
        "required": [
            "path",
            "start_line",
            "end_line",
            "replacement_content",
            "expected_file_sha256",
        ],
        "additionalProperties": False,
    }
    assert registry.requires_approval(
        ToolInvocation(
            id="line-range",
            tool_name="apply_line_range_replacement",
            arguments=line_range_replacement_arguments(),
        )
    )
    assert APPLY_FILE_REWRITE_DEFINITION.name == "apply_file_rewrite"
    assert APPLY_FILE_REWRITE_DEFINITION.input_schema == {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "expected_file_sha256": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
            },
            "replacement_content": {"type": "string"},
        },
        "required": [
            "path",
            "expected_file_sha256",
            "replacement_content",
        ],
        "additionalProperties": False,
    }
    assert registry.requires_approval(
        ToolInvocation(
            id="rewrite",
            tool_name="apply_file_rewrite",
            arguments=file_rewrite_arguments(),
        )
    )
    assert APPLY_TEXT_REPLACEMENT_DEFINITION.name == "apply_text_replacement"
    assert APPLY_TEXT_REPLACEMENT_DEFINITION.description == (
        "Replace a reasonably small exact current literal fragment in one "
        "existing UTF-8 file using an exact current SHA-256 from read_file or a "
        "successful prior action result when its content is known; successful "
        "results include resulting_file_sha256."
    )
    assert APPLY_TEXT_REPLACEMENT_DEFINITION.input_schema == {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "expected_text": {"type": "string"},
            "replacement_text": {"type": "string"},
            "expected_file_sha256": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
            },
            "expected_occurrences": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_TEXT_REPLACEMENT_OCCURRENCES,
                "default": 1,
            },
        },
        "required": [
            "path",
            "expected_text",
            "replacement_text",
            "expected_file_sha256",
        ],
        "additionalProperties": False,
    }
    assert registry.requires_approval(
        ToolInvocation(
            id="replacement",
            tool_name="apply_text_replacement",
            arguments=text_replacement_arguments(),
        )
    )


def test_line_range_model_contract_documents_range_and_required_sha(
    tmp_path: Path,
) -> None:
    """Expose one closed model-facing schema with explicit line semantics."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text("old\n", encoding="utf-8")
    registry = ToolRegistry()
    register_workspace_action_tools(registry, workspace)
    schema = APPLY_LINE_RANGE_REPLACEMENT_DEFINITION.input_schema

    assert "one-based inclusive" in (
        APPLY_LINE_RANGE_REPLACEMENT_DEFINITION.description
    )
    assert "SHA-256" in APPLY_LINE_RANGE_REPLACEMENT_DEFINITION.description
    assert schema["required"] == [
        "path",
        "start_line",
        "end_line",
        "replacement_content",
        "expected_file_sha256",
    ]
    assert schema["additionalProperties"] is False

    invalid = ToolInvocation(
        id="unsupported-line-range",
        tool_name="apply_line_range_replacement",
        arguments={
            **line_range_replacement_arguments(file_content="old\n"),
            "expected_content": "old\n",
        },
    )
    validation_error = registry.argument_validation_error(invalid)
    assert validation_error is not None
    assert "1 unsupported field" in validation_error
    assert "additional fields are not allowed" in validation_error
    with pytest.raises(CompletionError, match="argument validation failed"):
        registry.create_approval_request(invalid)
    assert target.read_text(encoding="utf-8") == "old\n"


@pytest.mark.parametrize(
    ("arguments", "issue"),
    [
        (
            {"path": "module.py"},
            "missing required fields: expected_content, replacement_content",
        ),
        (
            {
                "path": "module.py",
                "expected_content": "",
                "replacement_content": 1,
            },
            "replacement_content must be a string",
        ),
        (
            {
                "path": "module.py",
                "expected_content": "",
                "replacement_content": "updated\n",
                "patch": "legacy text",
            },
            (
                "arguments contain 1 unsupported field; "
                "additional fields are not allowed"
            ),
        ),
    ],
)
def test_advertised_patch_schema_rejects_runtime_invalid_shapes_before_preview(
    tmp_path: Path,
    arguments: dict[str, object],
    issue: str,
) -> None:
    """Use the registered advertised schema before any approval preview."""

    _, workspace = create_workspace(tmp_path)
    registry = ToolRegistry()
    register_workspace_action_tools(registry, workspace)
    invocation = ToolInvocation(
        id="invalid-patch",
        tool_name="apply_file_patch",
        arguments=arguments,
    )

    error = registry.argument_validation_error(invocation)

    assert error is not None
    assert issue in error
    assert (
        "Required structured shape: {path: string, expected_content: string, "
        "replacement_content: string, create_if_missing?: boolean}"
    ) in error
    assert "legacy text" not in error


@pytest.mark.parametrize(
    ("unsupported_names", "expected_issue", "forbidden_fragments"),
    [
        (
            ("/home/operator/private/.env",),
            (
                "arguments contain 1 unsupported field; "
                "additional fields are not allowed"
            ),
            ("/home/operator", "private", ".env"),
        ),
        (
            ("PRIVATE_TOKEN = 'secret-value'\nprint(PRIVATE_TOKEN)",),
            (
                "arguments contain 1 unsupported field; "
                "additional fields are not allowed"
            ),
            ("PRIVATE_TOKEN", "secret-value", "print"),
        ),
        (
            (
                (
                    "CONTROL_LINE_MARKER\nCONTROL_BREAK_MARKER"
                    "\rCONTROL_RETURN_MARKER\tCONTROL_TAB_MARKER"
                    "\x1b[31mCONTROL_RED_MARKER"
                ),
            ),
            (
                "arguments contain 1 unsupported field; "
                "additional fields are not allowed"
            ),
            (
                "CONTROL_LINE_MARKER",
                "CONTROL_BREAK_MARKER",
                "CONTROL_RETURN_MARKER",
                "CONTROL_TAB_MARKER",
                "\x1b[31m",
                "CONTROL_RED_MARKER",
            ),
        ),
        (
            ("first-private-field", "second-secret-field", "third-source-field"),
            (
                "arguments contain 3 unsupported fields; "
                "additional fields are not allowed"
            ),
            ("first-private", "second-secret", "third-source"),
        ),
        (
            ("very-private-" + ("x" * 2_000),),
            (
                "arguments contain 1 unsupported field; "
                "additional fields are not allowed"
            ),
            ("very-private", "x" * 40),
        ),
    ],
)
def test_unsupported_argument_names_are_replaced_by_a_bounded_structural_count(
    tmp_path: Path,
    unsupported_names: tuple[str, ...],
    expected_issue: str,
    forbidden_fragments: tuple[str, ...],
) -> None:
    """Never expose model-controlled object keys in schema diagnostics."""

    _, workspace = create_workspace(tmp_path)
    registry = ToolRegistry()
    register_workspace_action_tools(registry, workspace)
    arguments = patch_arguments()
    arguments.update(dict.fromkeys(unsupported_names, "untrusted value"))

    error = registry.argument_validation_error(
        ToolInvocation(
            id="invalid-patch",
            tool_name="apply_file_patch",
            arguments=arguments,
        )
    )

    assert error is not None
    assert expected_issue in error
    assert len(error) <= 800
    assert "Tool 'apply_file_patch' argument validation failed" in error
    assert "Required structured shape" in error
    assert "Issue a corrected apply_file_patch tool call" in error
    for fragment in forbidden_fragments:
        assert fragment not in error


def test_nested_unsupported_argument_name_uses_only_the_trusted_schema_path(
    tmp_path: Path,
) -> None:
    """Identify a nested location without exposing its model-controlled child key."""

    _, workspace = create_workspace(tmp_path)
    registry = ToolRegistry()
    register_workspace_action_tools(registry, workspace)
    private_name = "NESTED_PRIVATE_TOKEN\n\x1b[31msecret"
    change = patch_arguments()
    change[private_name] = "untrusted value"

    error = registry.argument_validation_error(
        ToolInvocation(
            id="invalid-transaction",
            tool_name="apply_workspace_changes",
            arguments=transaction_arguments(change),
        )
    )

    assert error is not None
    assert (
        "changes[0] contains 1 unsupported field; additional fields are not allowed"
    ) in error
    assert private_name not in error
    assert "NESTED_PRIVATE_TOKEN" not in error
    assert "\x1b[31m" not in error


def test_advertised_patch_schema_accepts_the_runtime_patch_shape(
    tmp_path: Path,
) -> None:
    """Keep valid structured patch arguments unchanged through both validators."""

    root, workspace = create_workspace(tmp_path)
    (root / "module.py").write_text("old\n", encoding="utf-8")
    arguments = patch_arguments(
        path="module.py",
        expected="old\n",
        replacement="new\n",
    )
    registry = ToolRegistry()
    register_workspace_action_tools(registry, workspace)
    invocation = ToolInvocation(
        id="valid-patch",
        tool_name="apply_file_patch",
        arguments=arguments,
    )

    assert registry.argument_validation_error(invocation) is None
    assert preview_file_patch(workspace, invocation.arguments)["path"] == "module.py"
    assert (root / "module.py").read_text(encoding="utf-8") == "old\n"


def test_existing_file_preview_is_complete_deterministic_and_non_mutating(
    tmp_path: Path,
) -> None:
    """Return safe update metadata and a complete unified diff without writing."""

    root, workspace = create_workspace(tmp_path)
    target = root / "src" / "module.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")

    first = preview_file_patch(
        workspace,
        patch_arguments(path="src/./module.py"),
    )
    second = preview_file_patch(
        workspace,
        patch_arguments(path="src/module.py"),
    )

    assert (
        first
        == second
        == {
            "path": "src/module.py",
            "operation": "update",
            "old_size_bytes": 10,
            "new_size_bytes": 10,
            "changed_lines": 2,
            "diff": (
                "--- a/src/module.py\n"
                "+++ b/src/module.py\n"
                "@@ -1 +1 @@\n"
                "-value = 1\n"
                "+value = 2\n"
            ),
        }
    )
    assert str(root) not in first["diff"]
    assert "resulting_file_sha256" not in first
    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_file_rewrite_preview_and_apply_use_sha_without_expected_content(
    tmp_path: Path,
) -> None:
    """Preview the complete diff and atomically rewrite one existing file."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text("value = 1\n", encoding="utf-8")
    arguments = file_rewrite_arguments()

    preview = preview_file_rewrite(workspace, arguments)

    assert preview == {
        "path": "module.py",
        "operation": "update",
        "old_size_bytes": 10,
        "new_size_bytes": 10,
        "changed_lines": 2,
        "diff": (
            "--- a/module.py\n+++ b/module.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n"
        ),
    }
    assert "expected_content" not in arguments
    assert target.read_text(encoding="utf-8") == "value = 1\n"

    result = apply_file_rewrite(workspace, arguments)

    assert result == {
        "path": "module.py",
        "operation": "update",
        "old_size_bytes": 10,
        "new_size_bytes": 10,
        "changed_lines": 2,
        "resulting_file_sha256": hashlib.sha256(b"value = 2\n").hexdigest(),
    }
    assert target.read_text(encoding="utf-8") == "value = 2\n"


def test_file_rewrite_rejects_stale_sha_without_mutation(tmp_path: Path) -> None:
    """Reject stale optimistic state before preview or execution."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text("value = 1\n", encoding="utf-8")
    arguments = file_rewrite_arguments(expected_sha256="0" * 64)

    with pytest.raises(ValueError, match="expected_file_sha256 does not match"):
        preview_file_rewrite(workspace, arguments)
    with pytest.raises(ValueError, match="expected_file_sha256 does not match"):
        apply_file_rewrite(workspace, arguments)

    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_file_rewrite_rechecks_sha_after_approval(tmp_path: Path) -> None:
    """Reject a concurrent mutation between complete preview and execution."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text("value = 1\n", encoding="utf-8")
    registry = ToolRegistry()
    register_workspace_action_tools(registry, workspace)
    provider = FakeProvider(
        [
            ChatResponse(
                tool_invocations=(
                    ToolInvocation(
                        id="rewrite",
                        tool_name="apply_file_rewrite",
                        arguments=file_rewrite_arguments(),
                    ),
                )
            ),
            ChatResponse(text="Handled failure."),
        ]
    )

    def approve_after_change(_request):
        target.write_text("concurrent = True\n", encoding="utf-8")
        return ToolApprovalDecision.APPROVE

    run_tool_calling_loop(
        provider,
        ChatRequest(messages=[]),
        registry,
        max_tool_rounds=1,
        tool_approval_handler=approve_after_change,
    )

    assert target.read_text(encoding="utf-8") == "concurrent = True\n"


def test_file_rewrite_preserves_exact_permissions(tmp_path: Path) -> None:
    """Preserve the existing regular file mode across atomic replacement."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text("value = 1\n", encoding="utf-8")
    target.chmod(0o751)

    apply_file_rewrite(workspace, file_rewrite_arguments())

    assert stat.S_IMODE(target.stat().st_mode) == 0o751


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/outside.py",
        "../outside.py",
        ".git/config",
        "missing/module.py",
        "missing.py",
    ],
)
def test_file_rewrite_rejects_unsafe_and_missing_paths(
    tmp_path: Path,
    path: str,
) -> None:
    """Allow only existing contained regular-file targets."""

    root, workspace = create_workspace(tmp_path)
    (root / "module.py").write_text("value = 1\n", encoding="utf-8")

    with pytest.raises(ValueError):
        preview_file_rewrite(
            workspace,
            file_rewrite_arguments(path=path),
        )

    assert (root / "module.py").read_text(encoding="utf-8") == "value = 1\n"


def test_file_rewrite_rejects_symlinks_without_mutation(tmp_path: Path) -> None:
    """Never follow a symlink target or parent for whole-file rewrites."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text("value = 1\n", encoding="utf-8")
    (root / "linked.py").symlink_to(target)

    with pytest.raises(ValueError, match="symlink"):
        preview_file_rewrite(
            workspace,
            file_rewrite_arguments(path="linked.py"),
        )

    assert target.read_text(encoding="utf-8") == "value = 1\n"


@pytest.mark.parametrize(
    "sha256",
    [
        "",
        "0" * 63,
        "0" * 65,
        "G" * 64,
        "A" * 64,
        1,
    ],
)
def test_file_rewrite_rejects_invalid_sha(tmp_path: Path, sha256: object) -> None:
    """Require exactly 64 lowercase hexadecimal digest characters."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text("value = 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="64 lowercase hexadecimal"):
        preview_file_rewrite(
            workspace,
            file_rewrite_arguments(expected_sha256=sha256),  # type: ignore[arg-type]
        )

    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_file_rewrite_preserves_utf8_nul_size_changed_line_and_diff_limits(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Reuse every bounded content and complete-preview boundary."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text("value = 1\n", encoding="utf-8")
    digest = hashlib.sha256(b"value = 1\n").hexdigest()

    for replacement in (
        "\ud800",
        "bad\0content",
        "x" * (MAX_PATCH_CONTENT_BYTES + 1),
        "".join("x\n" for _ in range(MAX_CHANGED_LINES + 1)),
    ):
        with pytest.raises(ValueError):
            preview_file_rewrite(
                workspace,
                file_rewrite_arguments(
                    replacement=replacement,
                    expected_sha256=digest,
                ),
            )
        assert target.read_text(encoding="utf-8") == "value = 1\n"

    target.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="valid UTF-8"):
        preview_file_rewrite(
            workspace,
            file_rewrite_arguments(
                file_content="",
                expected_sha256=hashlib.sha256(b"\xff").hexdigest(),
            ),
        )
    target.write_text("value = 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "agent_workbench.workspace_actions.MAX_PATCH_PREVIEW_BYTES",
        8,
    )
    with pytest.raises(ValueError, match="preview"):
        preview_file_rewrite(workspace, file_rewrite_arguments())


def test_new_file_preview_uses_dev_null_without_creating_file(
    tmp_path: Path,
) -> None:
    """Represent creation accurately while leaving the workspace unchanged."""

    root, workspace = create_workspace(tmp_path)

    preview = preview_file_patch(
        workspace,
        patch_arguments(
            path="created.py",
            expected="",
            replacement="value = 1\n",
            create=True,
        ),
    )

    assert preview == {
        "path": "created.py",
        "operation": "create",
        "old_size_bytes": 0,
        "new_size_bytes": 10,
        "changed_lines": 1,
        "diff": ("--- /dev/null\n+++ b/created.py\n@@ -0,0 +1 @@\n+value = 1\n"),
    }
    assert not (root / "created.py").exists()


def test_preview_marks_content_without_a_trailing_newline(tmp_path: Path) -> None:
    """Keep a unified diff complete for arbitrary valid UTF-8 text."""

    root, workspace = create_workspace(tmp_path)
    (root / "module.py").write_text("old", encoding="utf-8")

    preview = preview_file_patch(
        workspace,
        patch_arguments(expected="old", replacement="new"),
    )

    assert preview["diff"] == (
        "--- a/module.py\n"
        "+++ b/module.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "\\ No newline at end of file\n"
        "+new\n"
        "\\ No newline at end of file\n"
    )


def test_approved_update_and_creation_return_bounded_metadata(
    tmp_path: Path,
) -> None:
    """Apply approved updates and new files without returning content or diffs."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text("value = 1\n", encoding="utf-8")
    registry = ToolRegistry()
    register_workspace_action_tools(registry, workspace)

    update = registry.execute(
        ToolInvocation(
            id="update",
            tool_name="apply_file_patch",
            arguments=patch_arguments(),
        )
    )
    creation = registry.execute(
        ToolInvocation(
            id="create",
            tool_name="apply_file_patch",
            arguments=patch_arguments(
                path="created.py",
                expected="",
                replacement="",
                create=True,
            ),
        )
    )

    assert update.output == {
        "path": "module.py",
        "operation": "update",
        "old_size_bytes": 10,
        "new_size_bytes": 10,
        "changed_lines": 2,
        "resulting_file_sha256": hashlib.sha256(b"value = 2\n").hexdigest(),
    }
    assert creation.output == {
        "path": "created.py",
        "operation": "create",
        "old_size_bytes": 0,
        "new_size_bytes": 0,
        "changed_lines": 0,
        "resulting_file_sha256": hashlib.sha256(b"").hexdigest(),
    }
    assert target.read_text(encoding="utf-8") == "value = 2\n"
    assert (root / "created.py").is_file()
    assert (root / "created.py").read_text(encoding="utf-8") == ""


@pytest.mark.parametrize(
    ("decision", "message"),
    [
        (None, "approval is required"),
        (ToolApprovalDecision.DENY, "approval was denied"),
    ],
)
def test_missing_or_denied_approval_never_writes(
    tmp_path: Path,
    decision,
    message,
) -> None:
    """Keep the target unchanged unless the exact invocation is approved."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text("value = 1\n", encoding="utf-8")
    registry = ToolRegistry()
    register_workspace_action_tools(registry, workspace)

    with pytest.raises(CompletionError, match=message):
        invoke_patch(registry, patch_arguments(), decision=decision)

    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_execution_rechecks_content_after_approval(tmp_path: Path) -> None:
    """Reject a stale action when content changes after its preview."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text("value = 1\n", encoding="utf-8")
    registry = ToolRegistry()
    register_workspace_action_tools(registry, workspace)
    provider = FakeProvider(
        [
            ChatResponse(
                tool_invocations=(
                    ToolInvocation(
                        id="patch",
                        tool_name="apply_file_patch",
                        arguments=patch_arguments(),
                    ),
                )
            )
        ]
    )

    def approve_after_change(request):
        target.write_text("concurrent = True\n", encoding="utf-8")
        return ToolApprovalDecision.APPROVE

    with pytest.raises(StopIteration):
        run_tool_calling_loop(
            provider,
            ChatRequest(messages=[]),
            registry,
            max_tool_rounds=1,
            tool_approval_handler=approve_after_change,
        )

    assert target.read_text(encoding="utf-8") == "concurrent = True\n"


def test_new_file_creation_never_overwrites_a_concurrent_target(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Use exclusive creation if a file appears after validation."""

    root, workspace = create_workspace(tmp_path)
    target = root / "created.py"
    original_open = os.open

    def race_open(path, flags, mode):
        target.write_text("concurrent\n", encoding="utf-8")
        return original_open(path, flags, mode)

    monkeypatch.setattr("agent_workbench.workspace_actions.os.open", race_open)

    with pytest.raises(ValueError, match="changed"):
        apply_file_patch(
            workspace,
            patch_arguments(
                path="created.py",
                expected="",
                replacement="replacement\n",
                create=True,
            ),
        )

    assert target.read_text(encoding="utf-8") == "concurrent\n"


@pytest.mark.parametrize(
    "arguments",
    [
        patch_arguments(expected="stale\n"),
        patch_arguments(create=True),
        patch_arguments(
            path="missing.py",
            expected="not empty",
            replacement="new\n",
            create=True,
        ),
        patch_arguments(path="missing.py"),
    ],
)
def test_compare_and_swap_semantics_reject_invalid_file_state(
    tmp_path: Path,
    arguments,
) -> None:
    """Reject stale, contradictory, and missing-target patch requests."""

    root, workspace = create_workspace(tmp_path)
    (root / "module.py").write_text("value = 1\n", encoding="utf-8")

    with pytest.raises(ValueError):
        preview_file_patch(workspace, arguments)

    assert (root / "module.py").read_text(encoding="utf-8") == "value = 1\n"


def test_content_size_and_changed_line_limits(tmp_path: Path) -> None:
    """Accept exact byte boundaries and reject oversized or broad patches."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    boundary = "".join(f"{index:04d}{'x' * 95}\n" for index in range(1024))
    replacement = boundary.replace("0000", "changed".ljust(4)[:4], 1)
    target.write_text(boundary, encoding="utf-8")

    preview = preview_file_patch(
        workspace,
        patch_arguments(expected=boundary, replacement=replacement),
    )
    assert preview["new_size_bytes"] == MAX_PATCH_CONTENT_BYTES

    for arguments in (
        patch_arguments(expected="x" * (MAX_PATCH_CONTENT_BYTES + 1)),
        patch_arguments(replacement="x" * (MAX_PATCH_CONTENT_BYTES + 1)),
        patch_arguments(
            expected="",
            replacement="".join("x\n" for _ in range(MAX_CHANGED_LINES + 1)),
        ),
    ):
        with pytest.raises(ValueError):
            preview_file_patch(workspace, arguments)


@pytest.mark.parametrize(
    ("original", "replacement"),
    [("value\r\n", "value\n"), ("value\n", "value\r\n")],
)
def test_patch_and_rewrite_count_line_ending_only_changes(
    tmp_path: Path,
    original: str,
    replacement: str,
) -> None:
    """Use line-terminator-aware accounting in patch and rewrite previews."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_bytes(original.encode("utf-8"))

    patch_preview = preview_file_patch(
        workspace,
        patch_arguments(expected=original, replacement=replacement),
    )
    rewrite_preview = preview_file_rewrite(
        workspace,
        file_rewrite_arguments(
            file_content=original,
            replacement=replacement,
        ),
    )

    assert patch_preview["changed_lines"] == 2
    assert rewrite_preview["changed_lines"] == 2
    assert target.read_bytes() == original.encode("utf-8")


def test_complete_preview_size_limit_rejects_instead_of_truncating(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Never ask for approval using an incomplete diff."""

    root, workspace = create_workspace(tmp_path)
    (root / "module.py").write_text("old\n", encoding="utf-8")
    monkeypatch.setattr(
        "agent_workbench.workspace_actions.MAX_PATCH_PREVIEW_BYTES",
        8,
    )

    with pytest.raises(ValueError, match="preview"):
        preview_file_patch(
            workspace, patch_arguments(expected="old\n", replacement="new\n")
        )


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/outside.py",
        "../outside.py",
        "nested/../../outside.py",
        "../workspace-backup/outside.py",
        ".git/config",
        "nested/.git/config",
        "",
        ".",
        "missing/module.py",
    ],
)
def test_unsafe_or_invalid_write_targets_are_rejected(
    tmp_path: Path,
    path: str,
) -> None:
    """Reject escapes, Git internals, non-files, and missing parents."""

    root, workspace = create_workspace(tmp_path)
    sibling = tmp_path / "workspace-backup"
    sibling.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("outside\n", encoding="utf-8")

    with pytest.raises(ValueError):
        preview_file_patch(
            workspace,
            patch_arguments(
                path=path,
                expected="" if path not in {"", "."} else "value = 1\n",
                replacement="changed\n",
                create=True,
            ),
        )

    assert outside.read_text(encoding="utf-8") == "outside\n"


def test_all_symlink_write_paths_are_rejected(tmp_path: Path) -> None:
    """Reject internal, external, broken, and parent-directory symlinks."""

    root, workspace = create_workspace(tmp_path)
    actual = root / "actual.py"
    actual.write_text("value = 1\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("outside\n", encoding="utf-8")
    (root / "internal.py").symlink_to(actual)
    (root / "external.py").symlink_to(outside)
    (root / "broken.py").symlink_to(root / "missing.py")
    actual_directory = root / "actual"
    actual_directory.mkdir()
    (root / "linked").symlink_to(actual_directory, target_is_directory=True)

    for path in ("internal.py", "external.py", "broken.py", "linked/new.py"):
        with pytest.raises(ValueError, match="symlink"):
            preview_file_patch(
                workspace,
                patch_arguments(
                    path=path,
                    expected="" if path.endswith("new.py") else "value = 1\n",
                    replacement="changed\n",
                    create=path.endswith("new.py"),
                ),
            )

    assert actual.read_text(encoding="utf-8") == "value = 1\n"
    assert outside.read_text(encoding="utf-8") == "outside\n"


def test_rejects_directory_special_invalid_utf8_and_nul(tmp_path: Path) -> None:
    """Accept only regular strict UTF-8 files and NUL-free text."""

    root, workspace = create_workspace(tmp_path)
    (root / "directory").mkdir()
    (root / "binary.py").write_bytes(b"\xff")
    fifo = root / "pipe"
    os.mkfifo(fifo)

    for arguments in (
        patch_arguments(path="directory"),
        patch_arguments(path="binary.py"),
        patch_arguments(path="pipe"),
        patch_arguments(expected="value = 1\n\0"),
        patch_arguments(replacement="value = 2\n\0"),
    ):
        with pytest.raises(ValueError):
            preview_file_patch(workspace, arguments)


def test_update_preserves_permissions_and_cleans_temporary_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Keep portable mode bits and remove temporary files after failures."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text("value = 1\n", encoding="utf-8")
    target.chmod(0o640)

    result = apply_file_patch(workspace, patch_arguments())

    assert result["operation"] == "update"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert list(root.glob(".agent-workbench-patch-*")) == []

    monkeypatch.setattr(
        "agent_workbench.workspace_actions.os.replace",
        lambda source, destination: (_ for _ in ()).throw(OSError("failed")),
    )
    with pytest.raises(ValueError):
        apply_file_patch(
            workspace,
            patch_arguments(expected="value = 2\n", replacement="value = 3\n"),
        )
    assert list(root.glob(".agent-workbench-patch-*")) == []
    assert target.read_text(encoding="utf-8") == "value = 2\n"


def test_calls_preserve_cwd_arguments_results_and_registry_isolation(
    tmp_path: Path,
) -> None:
    """Keep caller data and independent registries unaffected by mutations."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text("value = 1\n", encoding="utf-8")
    original_cwd = Path.cwd()
    arguments = patch_arguments()
    first = ToolRegistry()
    second = ToolRegistry()
    register_workspace_action_tools(first, workspace)

    preview = preview_file_patch(workspace, arguments)
    preview["path"] = "changed.py"

    assert arguments == patch_arguments()
    assert preview_file_patch(workspace, arguments)["path"] == "module.py"
    assert second.definitions == ()
    assert Path.cwd() == original_cwd


def test_text_replacement_preview_is_complete_and_non_mutating(
    tmp_path: Path,
) -> None:
    """Build one complete diff from a small exact literal fragment."""

    root, workspace = create_workspace(tmp_path)
    target = root / "src" / "module.py"
    target.parent.mkdir()
    target.write_text(
        "def add(left: int, right: int) -> int:\n    return left - right\n",
        encoding="utf-8",
    )

    preview = preview_text_replacement(
        workspace,
        text_replacement_arguments(
            path="src/./module.py",
            expected="return left - right",
            replacement="return left + right",
            file_content=(
                "def add(left: int, right: int) -> int:\n    return left - right\n"
            ),
        ),
    )

    assert preview == {
        "path": "src/module.py",
        "operation": "update",
        "old_size_bytes": 63,
        "new_size_bytes": 63,
        "changed_lines": 2,
        "occurrences_replaced": 1,
        "diff": (
            "--- a/src/module.py\n"
            "+++ b/src/module.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def add(left: int, right: int) -> int:\n"
            "-    return left - right\n"
            "+    return left + right\n"
        ),
    }
    assert target.read_text(encoding="utf-8").endswith("return left - right\n")


def test_text_replacement_applies_exact_multiple_occurrences_and_preserves_mode(
    tmp_path: Path,
) -> None:
    """Replace exactly the declared non-overlapping occurrences atomically."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text("old\nold\nkeep\n", encoding="utf-8")
    target.chmod(0o640)

    result = apply_text_replacement(
        workspace,
        text_replacement_arguments(
            expected="old",
            replacement="new",
            file_content="old\nold\nkeep\n",
            occurrences=2,
        ),
    )

    assert result == {
        "path": "module.py",
        "operation": "update",
        "old_size_bytes": 13,
        "new_size_bytes": 13,
        "changed_lines": 4,
        "occurrences_replaced": 2,
        "resulting_file_sha256": hashlib.sha256(b"new\nnew\nkeep\n").hexdigest(),
    }
    assert target.read_text(encoding="utf-8") == "new\nnew\nkeep\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert list(root.glob(".agent-workbench-patch-*")) == []


def test_text_replacement_defaults_to_one_occurrence(tmp_path: Path) -> None:
    """Use one required literal occurrence when the optional count is omitted."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text("before TOKEN after\n", encoding="utf-8")

    result = apply_text_replacement(
        workspace,
        text_replacement_arguments(
            expected="TOKEN",
            replacement="VALUE",
            file_content="before TOKEN after\n",
        ),
    )

    assert result["occurrences_replaced"] == 1
    assert target.read_text(encoding="utf-8") == "before VALUE after\n"


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (
            {"path": "module.py", "expected_text": "old"},
            "requires path, expected_text, replacement_text",
        ),
        (
            {
                "path": "module.py",
                "expected_text": "old",
                "replacement_text": "new",
                "unknown": True,
            },
            "requires path, expected_text, replacement_text",
        ),
        (
            text_replacement_arguments(expected="", replacement="new"),
            "must not be empty",
        ),
        (
            text_replacement_arguments(expected="same", replacement="same"),
            "must differ",
        ),
        (
            {
                **text_replacement_arguments(),
                "expected_file_sha256": "INVALID",
            },
            "64 lowercase hexadecimal",
        ),
        (
            {
                **text_replacement_arguments(),
                "expected_occurrences": True,
            },
            "between 1 and",
        ),
        (
            {
                **text_replacement_arguments(),
                "expected_occurrences": 0,
            },
            "between 1 and",
        ),
        (
            {
                **text_replacement_arguments(),
                "expected_occurrences": MAX_TEXT_REPLACEMENT_OCCURRENCES + 1,
            },
            "between 1 and",
        ),
    ],
)
def test_text_replacement_rejects_invalid_arguments(
    tmp_path: Path,
    arguments: dict[str, object],
    message: str,
) -> None:
    """Reject incomplete, ambiguous, no-op, and invalid-count requests."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text("value = 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        preview_text_replacement(workspace, arguments)

    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_text_replacement_shared_errors_name_the_current_action(
    tmp_path: Path,
) -> None:
    "Keep reused path and content diagnostics specific to this action."

    root, workspace = create_workspace(tmp_path)

    with pytest.raises(
        ValueError,
        match="apply_text_replacement path must not contain traversal",
    ):
        preview_text_replacement(
            workspace,
            text_replacement_arguments(
                path="../outside.py",
                expected="old",
                replacement="new",
            ),
        )

    boundary = "old" + "x" * (MAX_PATCH_CONTENT_BYTES - len("old"))
    (root / "module.py").write_text(boundary, encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="apply_text_replacement replacement_content exceeds",
    ):
        preview_text_replacement(
            workspace,
            text_replacement_arguments(
                expected="old",
                replacement="y" * MAX_TEXT_REPLACEMENT_BYTES,
                file_content=boundary,
            ),
        )


def test_text_replacement_rejects_occurrence_mismatch_without_writing(
    tmp_path: Path,
) -> None:
    """Require the exact current occurrence count before preview or execution."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text("TOKEN\nTOKEN\n", encoding="utf-8")

    with pytest.raises(ValueError, match="expected 1 occurrence.*found 2"):
        preview_text_replacement(
            workspace,
            text_replacement_arguments(
                expected="TOKEN",
                replacement="VALUE",
                file_content="TOKEN\nTOKEN\n",
            ),
        )

    assert target.read_text(encoding="utf-8") == "TOKEN\nTOKEN\n"


def test_text_replacement_enforces_fragment_changed_line_and_preview_limits(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Bound literal fragments, resulting changes, and complete approval diffs."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text("old\n", encoding="utf-8")

    with pytest.raises(ValueError, match="expected_text exceeds"):
        preview_text_replacement(
            workspace,
            text_replacement_arguments(
                expected="x" * (MAX_TEXT_REPLACEMENT_BYTES + 1),
                replacement="new",
            ),
        )

    monkeypatch.setattr(
        "agent_workbench.workspace_actions.MAX_CHANGED_LINES",
        1,
    )
    with pytest.raises(ValueError, match="changed-line"):
        preview_text_replacement(
            workspace,
            text_replacement_arguments(
                expected="old",
                replacement="new",
                file_content="old\n",
            ),
        )

    monkeypatch.setattr(
        "agent_workbench.workspace_actions.MAX_CHANGED_LINES",
        MAX_CHANGED_LINES,
    )
    monkeypatch.setattr(
        "agent_workbench.workspace_actions.MAX_PATCH_PREVIEW_BYTES",
        8,
    )
    with pytest.raises(ValueError, match="preview"):
        preview_text_replacement(
            workspace,
            text_replacement_arguments(
                expected="old",
                replacement="new",
                file_content="old\n",
            ),
        )


def test_text_replacement_reuses_safe_existing_file_boundary(
    tmp_path: Path,
) -> None:
    """Reject missing, unsafe, symlink, directory, and invalid UTF-8 targets."""

    root, workspace = create_workspace(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("old\n", encoding="utf-8")
    (root / "external.py").symlink_to(outside)
    (root / "directory").mkdir()
    (root / "binary.py").write_bytes(b"\xff")

    for path in (
        "missing.py",
        "../outside.py",
        ".git/config",
        "external.py",
        "directory",
        "binary.py",
    ):
        with pytest.raises(ValueError):
            preview_text_replacement(
                workspace,
                text_replacement_arguments(
                    path=path,
                    expected="old",
                    replacement="new",
                ),
            )

    assert outside.read_text(encoding="utf-8") == "old\n"


@pytest.mark.parametrize(
    "decision",
    [None, ToolApprovalDecision.DENY],
)
def test_text_replacement_missing_or_denied_approval_never_writes(
    tmp_path: Path,
    decision: ToolApprovalDecision | None,
) -> None:
    """Keep the target unchanged without approval for the exact invocation."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text("old\n", encoding="utf-8")
    registry = ToolRegistry()
    register_workspace_action_tools(registry, workspace)

    with pytest.raises(CompletionError):
        invoke_text_replacement(
            registry,
            text_replacement_arguments(
                expected="old",
                replacement="new",
                file_content="old\n",
            ),
            decision=decision,
        )

    assert target.read_text(encoding="utf-8") == "old\n"


def test_text_replacement_rechecks_complete_content_after_approval(
    tmp_path: Path,
) -> None:
    """Reject a stale approved replacement without overwriting concurrent work."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text("old\n", encoding="utf-8")
    registry = ToolRegistry()
    register_workspace_action_tools(registry, workspace)

    invoke_text_replacement(
        registry,
        text_replacement_arguments(
            expected="old",
            replacement="new",
            file_content="old\n",
        ),
        decision=ToolApprovalDecision.APPROVE,
        before_approval=lambda: target.write_text(
            "concurrent\n",
            encoding="utf-8",
        ),
    )

    assert target.read_text(encoding="utf-8") == "concurrent\n"


@pytest.mark.parametrize(
    ("original", "start_line", "end_line", "replacement", "expected"),
    [
        ("old\nkeep\n", 1, 1, "new\n", "new\nkeep\n"),
        ("one\ntwo\nthree\nfour\n", 2, 3, "middle\n", "one\nmiddle\nfour\n"),
        ("first\nlast", 2, 2, "final", "first\nfinal"),
        (
            "before\nreplace\nafter\n",
            2,
            2,
            "new one\nnew two\n",
            "before\nnew one\nnew two\nafter\n",
        ),
    ],
)
def test_line_range_replacement_applies_exact_inclusive_range(
    tmp_path: Path,
    original: str,
    start_line: int,
    end_line: int,
    replacement: str,
    expected: str,
) -> None:
    """Replace one or more complete lines without changing surrounding bytes."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text(original, encoding="utf-8", newline="")
    target.chmod(0o640)

    result = apply_line_range_replacement(
        workspace,
        line_range_replacement_arguments(
            start_line=start_line,
            end_line=end_line,
            replacement=replacement,
            file_content=original,
        ),
    )

    assert result["path"] == "module.py"
    assert result["operation"] == "update"
    assert result["start_line"] == start_line
    assert result["end_line"] == end_line
    assert "diff" not in result
    assert target.read_bytes() == expected.encode("utf-8")
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert list(root.glob(".agent-workbench-patch-*")) == []


def test_line_range_replacement_changes_middle_of_large_file_only(
    tmp_path: Path,
) -> None:
    """Splice a known middle range without rewriting either surrounding region."""

    root, workspace = create_workspace(tmp_path)
    lines = [f"line {index:04d}\r\n" for index in range(1, 2_001)]
    original = "".join(lines)
    target = root / "large.txt"
    target.write_bytes(original.encode("utf-8"))

    apply_line_range_replacement(
        workspace,
        line_range_replacement_arguments(
            path="large.txt",
            start_line=1_000,
            end_line=1_001,
            replacement="replacement a\r\nreplacement b\r\n",
            file_content=original,
        ),
    )

    expected = (
        "".join(lines[:999])
        + "replacement a\r\nreplacement b\r\n"
        + "".join(lines[1_001:])
    ).encode("utf-8")
    assert target.read_bytes() == expected


def test_line_range_replacement_preview_is_complete_and_non_mutating(
    tmp_path: Path,
) -> None:
    """Show exact range, byte counts, changed lines, and the complete diff."""

    root, workspace = create_workspace(tmp_path)
    original = "first\nold one\nold two\nlast\n"
    target = root / "src" / "module.py"
    target.parent.mkdir()
    target.write_text(original, encoding="utf-8")

    preview = preview_line_range_replacement(
        workspace,
        line_range_replacement_arguments(
            path="src/./module.py",
            start_line=2,
            end_line=3,
            replacement="new one\nnew two\n",
            file_content=original,
        ),
    )

    assert preview == {
        "path": "src/module.py",
        "operation": "update",
        "old_size_bytes": 27,
        "new_size_bytes": 27,
        "changed_lines": 4,
        "start_line": 2,
        "end_line": 3,
        "diff": (
            "--- a/src/module.py\n"
            "+++ b/src/module.py\n"
            "@@ -1,4 +1,4 @@\n"
            " first\n"
            "-old one\n"
            "-old two\n"
            "+new one\n"
            "+new two\n"
            " last\n"
        ),
    }
    assert target.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (
            {"path": "module.py"},
            "requires path, start_line, end_line, replacement_content",
        ),
        (
            {
                **line_range_replacement_arguments(),
                "unsupported": True,
            },
            "requires path, start_line, end_line, replacement_content",
        ),
        (
            line_range_replacement_arguments(start_line=0),
            "start_line must be an integer greater than or equal to 1",
        ),
        (
            line_range_replacement_arguments(start_line=2, end_line=1),
            "end_line must be greater than or equal to start_line",
        ),
        (
            line_range_replacement_arguments(expected_sha256="INVALID"),
            "64 lowercase hexadecimal",
        ),
    ],
)
def test_line_range_replacement_rejects_invalid_arguments_without_writing(
    tmp_path: Path,
    arguments: dict[str, object],
    message: str,
) -> None:
    """Reject invalid ranges, hashes, and closed-schema violations."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text("value = 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        preview_line_range_replacement(workspace, arguments)

    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_line_range_replacement_rejects_range_beyond_file(tmp_path: Path) -> None:
    """Reject an inclusive end line outside the current exact file."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text("one\ntwo\n", encoding="utf-8")

    with pytest.raises(ValueError, match="line range exceeds the current file"):
        preview_line_range_replacement(
            workspace,
            line_range_replacement_arguments(
                start_line=2,
                end_line=3,
                file_content="one\ntwo\n",
            ),
        )

    assert target.read_text(encoding="utf-8") == "one\ntwo\n"


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("missing.py", "target does not exist"),
        ("directory", "requires a regular file"),
        ("external.py", "does not allow symlink paths"),
        ("/outside.py", "path must be relative"),
        ("../outside.py", "path must not contain traversal"),
        ("binary.py", "requires valid UTF-8"),
    ],
)
def test_line_range_replacement_reuses_safe_existing_file_boundary(
    tmp_path: Path,
    path: str,
    message: str,
) -> None:
    """Reject missing, directory, symlink, escape, and invalid UTF-8 targets."""

    root, workspace = create_workspace(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("old\n", encoding="utf-8")
    (root / "external.py").symlink_to(outside)
    (root / "directory").mkdir()
    (root / "binary.py").write_bytes(b"\xff")

    with pytest.raises(ValueError, match=message):
        preview_line_range_replacement(
            workspace,
            line_range_replacement_arguments(
                path=path,
                expected_sha256=(
                    hashlib.sha256(b"\xff").hexdigest() if path == "binary.py" else None
                ),
            ),
        )

    assert outside.read_text(encoding="utf-8") == "old\n"


def test_line_range_replacement_rejects_stale_sha_without_writing(
    tmp_path: Path,
) -> None:
    """Require the exact current complete-file SHA before preview and apply."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text("old\n", encoding="utf-8")

    with pytest.raises(ValueError, match="expected_file_sha256 does not match"):
        preview_line_range_replacement(
            workspace,
            line_range_replacement_arguments(expected_sha256="0" * 64),
        )

    assert target.read_text(encoding="utf-8") == "old\n"


def test_line_range_replacement_enforces_file_changed_line_and_preview_limits(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Bound replacement content, resulting file, changed lines, and preview."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text("old\n", encoding="utf-8")

    with pytest.raises(ValueError, match="replacement_content exceeds"):
        preview_line_range_replacement(
            workspace,
            line_range_replacement_arguments(
                replacement="x" * (MAX_PATCH_CONTENT_BYTES + 1),
                file_content="old\n",
            ),
        )

    monkeypatch.setattr("agent_workbench.workspace_actions.MAX_CHANGED_LINES", 1)
    with pytest.raises(ValueError, match="changed-line"):
        preview_line_range_replacement(
            workspace,
            line_range_replacement_arguments(file_content="old\n"),
        )

    monkeypatch.setattr(
        "agent_workbench.workspace_actions.MAX_CHANGED_LINES", MAX_CHANGED_LINES
    )
    monkeypatch.setattr("agent_workbench.workspace_actions.MAX_PATCH_PREVIEW_BYTES", 8)
    with pytest.raises(ValueError, match="preview"):
        preview_line_range_replacement(
            workspace,
            line_range_replacement_arguments(file_content="old\n"),
        )

    assert target.read_text(encoding="utf-8") == "old\n"


def test_line_range_replacement_enforces_real_file_and_500_changed_line_limits(
    tmp_path: Path,
) -> None:
    """Preserve the shared 100 KiB file and 500 changed-line boundaries."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    oversized = b"x" * (MAX_PATCH_CONTENT_BYTES + 1)
    target.write_bytes(oversized)

    with pytest.raises(ValueError, match="workspace file exceeds"):
        preview_line_range_replacement(
            workspace,
            line_range_replacement_arguments(
                expected_sha256=hashlib.sha256(oversized).hexdigest()
            ),
        )

    original = "".join(f"old {index}\n" for index in range(251))
    replacement = "".join(f"new {index}\n" for index in range(251))
    target.write_text(original, encoding="utf-8")
    with pytest.raises(ValueError, match="500-changed-line limit"):
        preview_line_range_replacement(
            workspace,
            line_range_replacement_arguments(
                start_line=1,
                end_line=251,
                replacement=replacement,
                file_content=original,
            ),
        )

    assert target.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    ("old_ending", "new_ending"),
    [("\r\n", "\n"), ("\n", "\r\n")],
)
def test_line_range_replacement_counts_and_limits_line_ending_only_changes(
    tmp_path: Path,
    old_ending: str,
    new_ending: str,
) -> None:
    """Report bounded terminator changes and reject 501-line conversions."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    bounded_original = "".join(f"line {index}{old_ending}" for index in range(3))
    bounded_replacement = "".join(f"line {index}{new_ending}" for index in range(3))
    target.write_bytes(bounded_original.encode("utf-8"))

    preview = preview_line_range_replacement(
        workspace,
        line_range_replacement_arguments(
            start_line=1,
            end_line=3,
            replacement=bounded_replacement,
            file_content=bounded_original,
        ),
    )

    assert preview["changed_lines"] == 6
    assert target.read_bytes() == bounded_original.encode("utf-8")

    broad_original = "".join(f"line {index}{old_ending}" for index in range(501))
    broad_replacement = "".join(f"line {index}{new_ending}" for index in range(501))
    target.write_bytes(broad_original.encode("utf-8"))

    with pytest.raises(
        ValueError,
        match=r"^line-range replacement exceeds the 500-changed-line limit\.$",
    ):
        preview_line_range_replacement(
            workspace,
            line_range_replacement_arguments(
                start_line=1,
                end_line=501,
                replacement=broad_replacement,
                file_content=broad_original,
            ),
        )

    assert target.read_bytes() == broad_original.encode("utf-8")


@pytest.mark.parametrize("decision", [None, ToolApprovalDecision.DENY])
def test_line_range_replacement_missing_or_denied_approval_never_writes(
    tmp_path: Path,
    decision: ToolApprovalDecision | None,
) -> None:
    """Keep the exact target unchanged unless its complete preview is approved."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text("old\n", encoding="utf-8")
    registry = ToolRegistry()
    register_workspace_action_tools(registry, workspace)

    with pytest.raises(CompletionError):
        invoke_line_range_replacement(
            registry,
            line_range_replacement_arguments(file_content="old\n"),
            decision=decision,
        )

    assert target.read_text(encoding="utf-8") == "old\n"


def test_line_range_replacement_preview_failure_never_requests_or_writes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Fail closed when the complete approval preview cannot be produced."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text("old\n", encoding="utf-8")
    registry = ToolRegistry()
    register_workspace_action_tools(registry, workspace)
    monkeypatch.setattr("agent_workbench.workspace_actions.MAX_PATCH_PREVIEW_BYTES", 8)

    with pytest.raises(CompletionError, match="Approval preview failed"):
        invoke_line_range_replacement(
            registry,
            line_range_replacement_arguments(file_content="old\n"),
            decision=ToolApprovalDecision.APPROVE,
        )

    assert target.read_text(encoding="utf-8") == "old\n"


def test_line_range_replacement_successful_approval_mutates_once(
    tmp_path: Path,
) -> None:
    """Apply the exact previewed line range after one explicit approval."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text("before\nold\nafter\n", encoding="utf-8")
    registry = ToolRegistry()
    register_workspace_action_tools(registry, workspace)

    invoke_line_range_replacement(
        registry,
        line_range_replacement_arguments(
            start_line=2,
            end_line=2,
            replacement="new\n",
            file_content="before\nold\nafter\n",
        ),
        decision=ToolApprovalDecision.APPROVE,
    )

    assert target.read_text(encoding="utf-8") == "before\nnew\nafter\n"


def test_line_range_replacement_returns_exact_resulting_sha_without_final_newline(
    tmp_path: Path,
) -> None:
    """Hash the exact UTF-8 bytes produced by a successful range replacement."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_bytes("before\nold".encode("utf-8"))
    result = apply_line_range_replacement(
        workspace,
        line_range_replacement_arguments(
            start_line=2,
            end_line=2,
            replacement="é",
            file_content="before\nold",
        ),
    )

    actual_bytes = target.read_bytes()
    assert actual_bytes == "before\né".encode("utf-8")
    assert result["resulting_file_sha256"] == hashlib.sha256(actual_bytes).hexdigest()


def test_stale_result_has_no_successful_hash_evidence(tmp_path: Path) -> None:
    """Reject stale execution without manufacturing resulting-file evidence."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text("old\n", encoding="utf-8")
    result = ToolRegistry()
    register_workspace_action_tools(result, workspace)
    tool_result = result.execute(
        ToolInvocation(
            id="stale",
            tool_name="apply_file_rewrite",
            arguments=file_rewrite_arguments(
                file_content="old\n",
                expected_sha256="0" * 64,
            ),
        )
    )

    assert tool_result.status == "error"
    assert not isinstance(tool_result.output, dict) or (
        "resulting_file_sha256" not in tool_result.output
    )


def test_line_range_replacement_approved_action_rechecks_stale_file(
    tmp_path: Path,
) -> None:
    """Revalidate the exact approved file immediately before atomic mutation."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text("old\n", encoding="utf-8")
    registry = ToolRegistry()
    register_workspace_action_tools(registry, workspace)

    invoke_line_range_replacement(
        registry,
        line_range_replacement_arguments(file_content="old\n"),
        decision=ToolApprovalDecision.APPROVE,
        before_approval=lambda: target.write_text("concurrent\n", encoding="utf-8"),
    )

    assert target.read_text(encoding="utf-8") == "concurrent\n"


@pytest.mark.parametrize(
    "change_kind",
    ["content", "identical-inode", "chmod", "symlink", "deletion"],
)
def test_line_range_approved_snapshot_rejects_identity_and_metadata_changes(
    tmp_path: Path,
    change_kind: str,
) -> None:
    """Bind approval to exact bytes, inode, mode, timestamps, and file type."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_bytes(b"old\n")
    target.chmod(0o640)
    original_inode = target.stat().st_ino
    outside = tmp_path / "outside.py"
    outside.write_bytes(b"outside\n")
    registry = ToolRegistry()
    register_workspace_action_tools(registry, workspace)

    def change_approved_target() -> None:
        if change_kind == "content":
            target.write_bytes(b"concurrent\n")
        elif change_kind == "identical-inode":
            substitute = root / "substitute.py"
            substitute.write_bytes(b"old\n")
            substitute.chmod(0o640)
            os.replace(substitute, target)
            assert target.stat().st_ino != original_inode
        elif change_kind == "chmod":
            target.chmod(0o600)
        elif change_kind == "symlink":
            target.unlink()
            target.symlink_to(outside)
        else:
            target.unlink()

    invoke_line_range_replacement(
        registry,
        line_range_replacement_arguments(file_content="old\n"),
        decision=ToolApprovalDecision.APPROVE,
        before_approval=change_approved_target,
    )

    if change_kind == "content":
        assert target.read_bytes() == b"concurrent\n"
    elif change_kind == "identical-inode":
        assert target.read_bytes() == b"old\n"
        assert target.stat().st_ino != original_inode
    elif change_kind == "chmod":
        assert target.read_bytes() == b"old\n"
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
    elif change_kind == "symlink":
        assert target.is_symlink()
        assert outside.read_bytes() == b"outside\n"
    else:
        assert not target.exists()
    assert list(root.glob(".agent-workbench-patch-*")) == []


@pytest.mark.parametrize("change_kind", ["content", "identical-inode"])
def test_line_range_final_handoff_rejects_descriptor_and_path_changes(
    tmp_path: Path,
    monkeypatch,
    change_kind: str,
) -> None:
    """Reject deterministic changes after the initial final-handoff read."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_bytes(b"old\n")
    target.chmod(0o640)
    original_inode = target.stat().st_ino

    def change_during_handoff(_patch) -> None:
        if change_kind == "content":
            target.write_bytes(b"concurrent\n")
        else:
            substitute = root / "substitute.py"
            substitute.write_bytes(b"old\n")
            substitute.chmod(0o640)
            os.replace(substitute, target)
            assert target.stat().st_ino != original_inode

    monkeypatch.setattr(
        "agent_workbench.workspace_actions._before_atomic_replace_final_check",
        change_during_handoff,
    )

    with pytest.raises(
        ValueError,
        match=r"^apply_file_patch target changed before replacement\.$",
    ):
        apply_line_range_replacement(
            workspace,
            line_range_replacement_arguments(file_content="old\n"),
        )

    if change_kind == "content":
        assert target.read_bytes() == b"concurrent\n"
    else:
        assert target.read_bytes() == b"old\n"
        assert target.stat().st_ino != original_inode
    assert list(root.glob(".agent-workbench-patch-*")) == []


def test_line_range_final_handoff_unchanged_target_succeeds(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Keep the target descriptor open through a successful final check."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_bytes(b"old\n")
    target.chmod(0o751)
    checks = []
    monkeypatch.setattr(
        "agent_workbench.workspace_actions._before_atomic_replace_final_check",
        lambda patch: checks.append(patch.relative_path),
    )

    result = apply_line_range_replacement(
        workspace,
        line_range_replacement_arguments(file_content="old\n"),
    )

    assert result["path"] == "module.py"
    assert checks == ["module.py"]
    assert target.read_bytes() == b"value = 2\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o751
    assert list(root.glob(".agent-workbench-patch-*")) == []


def test_transaction_registration_uses_exact_closed_nested_schema(
    tmp_path: Path,
) -> None:
    """Append the transaction after the compatible single-file actions."""

    _, workspace = create_workspace(tmp_path)
    registry = ToolRegistry()

    register_workspace_action_tools(registry, workspace)

    assert registry.definitions == (
        APPLY_FILE_PATCH_DEFINITION,
        APPLY_FILE_REWRITE_DEFINITION,
        APPLY_TEXT_REPLACEMENT_DEFINITION,
        APPLY_LINE_RANGE_REPLACEMENT_DEFINITION,
        APPLY_WORKSPACE_CHANGES_DEFINITION,
    )
    assert APPLY_WORKSPACE_CHANGES_DEFINITION.name == "apply_workspace_changes"
    assert APPLY_WORKSPACE_CHANGES_DEFINITION.description == (
        "Apply one approved transactional set of UTF-8 file creations and "
        "updates inside the authorized workspace. Each changes array element "
        "must contain path, expected_content, replacement_content, and optional "
        "create_if_missing. Successful changes include resulting_file_sha256."
    )
    assert APPLY_WORKSPACE_CHANGES_DEFINITION.input_schema == {
        "type": "object",
        "properties": {
            "changes": {
                "type": "array",
                "minItems": 1,
                "maxItems": 16,
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "expected_content": {"type": "string"},
                        "replacement_content": {"type": "string"},
                        "create_if_missing": {
                            "type": "boolean",
                            "default": False,
                        },
                    },
                    "required": [
                        "path",
                        "expected_content",
                        "replacement_content",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["changes"],
        "additionalProperties": False,
    }
    assert registry.requires_approval(
        ToolInvocation(
            id="transaction",
            tool_name="apply_workspace_changes",
            arguments=transaction_arguments(
                patch_arguments(
                    path="created.py",
                    expected="",
                    replacement="new\n",
                    create=True,
                )
            ),
        )
    )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            {
                "path": "module.py",
                "replacement_content": "new\n",
            },
            (
                "apply_workspace_changes change is missing required fields: "
                "expected_content"
            ),
        ),
        (
            {
                "patch": {
                    "path": "module.py",
                    "expected_content": "old\n",
                    "replacement_content": "new\n",
                }
            },
            (
                "apply_workspace_changes change is missing required fields: "
                "expected_content, path, replacement_content"
            ),
        ),
        (
            {
                **patch_arguments(),
                "operation": "update",
            },
            "apply_workspace_changes change has unsupported fields: operation",
        ),
        (
            patch_arguments(path=1),  # type: ignore[arg-type]
            "apply_workspace_changes change path must be a string",
        ),
        (
            patch_arguments(create="false"),  # type: ignore[arg-type]
            "apply_workspace_changes change create_if_missing must be a boolean",
        ),
    ],
)
def test_transaction_nested_validation_errors_name_exact_invalid_fields(
    tmp_path: Path,
    change: dict[str, object],
    message: str,
) -> None:
    """Describe malformed nested objects without accepting or repairing them."""

    _, workspace = create_workspace(tmp_path)

    with pytest.raises(ValueError, match=message):
        preview_workspace_changes(
            workspace,
            transaction_arguments(change),
        )


def test_single_file_validation_error_remains_compatible(tmp_path: Path) -> None:
    """Keep the existing single-file action name in its malformed-input error."""

    _, workspace = create_workspace(tmp_path)

    with pytest.raises(
        ValueError,
        match="apply_file_patch requires structured patch arguments",
    ):
        preview_file_patch(workspace, {"path": "module.py"})


def test_transaction_preview_is_complete_sorted_and_non_mutating(
    tmp_path: Path,
) -> None:
    """Plan all targets and complete diffs before writing in canonical order."""

    root, workspace = create_workspace(tmp_path)
    (root / "z.py").write_text("old\n", encoding="utf-8")
    arguments = transaction_arguments(
        patch_arguments(
            path="z.py",
            expected="old\n",
            replacement="updated\n",
        ),
        patch_arguments(
            path="./a.py",
            expected="",
            replacement="created\n",
            create=True,
        ),
    )

    preview = preview_workspace_changes(workspace, arguments)

    assert preview == {
        "operation_count": 2,
        "created_count": 1,
        "updated_count": 1,
        "total_old_size_bytes": 4,
        "total_new_size_bytes": 16,
        "total_changed_lines": 3,
        "changes": [
            {
                "path": "a.py",
                "operation": "create",
                "old_size_bytes": 0,
                "new_size_bytes": 8,
                "changed_lines": 1,
                "diff": "--- /dev/null\n+++ b/a.py\n@@ -0,0 +1 @@\n+created\n",
            },
            {
                "path": "z.py",
                "operation": "update",
                "old_size_bytes": 4,
                "new_size_bytes": 8,
                "changed_lines": 2,
                "diff": ("--- a/z.py\n+++ b/z.py\n@@ -1 +1 @@\n-old\n+updated\n"),
            },
        ],
    }
    assert (root / "z.py").read_text(encoding="utf-8") == "old\n"
    assert not (root / "a.py").exists()
    assert str(root) not in str(preview)
    preview["changes"] = []
    assert len(preview_workspace_changes(workspace, arguments)["changes"]) == 2


def test_transaction_applies_mixed_changes_and_returns_bounded_metadata(
    tmp_path: Path,
) -> None:
    """Commit updates and creations in one deterministic successful result."""

    root, workspace = create_workspace(tmp_path)
    target = root / "z.py"
    target.write_text("old\n", encoding="utf-8")
    target.chmod(0o640)
    arguments = transaction_arguments(
        patch_arguments(
            path="z.py",
            expected="old\n",
            replacement="updated\n",
        ),
        patch_arguments(
            path="a.py",
            expected="",
            replacement="created\n",
            create=True,
        ),
    )

    result = apply_workspace_changes(workspace, arguments)

    assert result == {
        "operation_count": 2,
        "created_count": 1,
        "updated_count": 1,
        "total_old_size_bytes": 4,
        "total_new_size_bytes": 16,
        "total_changed_lines": 3,
        "changes": [
            {
                "path": "a.py",
                "operation": "create",
                "old_size_bytes": 0,
                "new_size_bytes": 8,
                "changed_lines": 1,
                "resulting_file_sha256": hashlib.sha256(b"created\n").hexdigest(),
            },
            {
                "path": "z.py",
                "operation": "update",
                "old_size_bytes": 4,
                "new_size_bytes": 8,
                "changed_lines": 2,
                "resulting_file_sha256": hashlib.sha256(b"updated\n").hexdigest(),
            },
        ],
    }
    assert target.read_text(encoding="utf-8") == "updated\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert (root / "a.py").read_text(encoding="utf-8") == "created\n"
    assert list(root.glob(".agent-workbench-transaction-*")) == []
    result["changes"] = []
    assert arguments["changes"][0]["replacement_content"] == "updated\n"


@pytest.mark.parametrize("create", [False, True])
def test_transaction_applies_two_updates_or_two_creations(
    tmp_path: Path,
    create: bool,
) -> None:
    """Support homogeneous multi-file plans as well as mixed transactions."""

    root, workspace = create_workspace(tmp_path)
    if not create:
        (root / "a.py").write_text("a\n", encoding="utf-8")
        (root / "b.py").write_text("b\n", encoding="utf-8")
    arguments = transaction_arguments(
        patch_arguments(
            path="a.py",
            expected="" if create else "a\n",
            replacement="A\n",
            create=create,
        ),
        patch_arguments(
            path="b.py",
            expected="" if create else "b\n",
            replacement="B\n",
            create=create,
        ),
    )

    result = apply_workspace_changes(workspace, arguments)

    assert result["created_count"] == (2 if create else 0)
    assert result["updated_count"] == (0 if create else 2)
    assert (root / "a.py").read_text(encoding="utf-8") == "A\n"
    assert (root / "b.py").read_text(encoding="utf-8") == "B\n"


def test_transaction_handles_empty_and_no_final_newline_content(
    tmp_path: Path,
) -> None:
    """Preserve complete diff semantics for empty and unterminated files."""

    root, workspace = create_workspace(tmp_path)
    (root / "empty.py").write_text("", encoding="utf-8")
    (root / "plain.py").write_text("old", encoding="utf-8")
    arguments = transaction_arguments(
        patch_arguments(path="empty.py", expected="", replacement=""),
        patch_arguments(path="plain.py", expected="old", replacement="new"),
        patch_arguments(
            path="created.py",
            expected="",
            replacement="",
            create=True,
        ),
    )

    preview = preview_workspace_changes(workspace, arguments)
    result = apply_workspace_changes(workspace, arguments)

    plain_preview = next(
        change for change in preview["changes"] if change["path"] == "plain.py"
    )
    assert plain_preview["diff"].count("\\ No newline at end of file") == 2
    assert result["operation_count"] == 3
    assert (root / "empty.py").read_text(encoding="utf-8") == ""
    assert (root / "created.py").read_text(encoding="utf-8") == ""


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"changes": [], "extra": True},
        {"changes": {}},
        {"changes": []},
        {"changes": [patch_arguments(), patch_arguments()]},
        {"changes": [{"path": "module.py"}]},
        {
            "changes": [
                {
                    **patch_arguments(),
                    "unexpected": True,
                }
            ]
        },
        {
            "changes": [
                patch_arguments(create="yes"),  # type: ignore[arg-type]
            ]
        },
        {
            "changes": [
                patch_arguments(path="module.py"),
                patch_arguments(path="./module.py"),
            ]
        },
        {
            "changes": [
                patch_arguments(path=""),
            ]
        },
    ],
)
def test_transaction_rejects_invalid_structure_and_duplicate_targets(
    tmp_path: Path,
    arguments: object,
) -> None:
    """Reject malformed closed input and duplicate canonical paths."""

    root, workspace = create_workspace(tmp_path)
    (root / "module.py").write_text("value = 1\n", encoding="utf-8")

    with pytest.raises(ValueError):
        preview_workspace_changes(workspace, arguments)

    assert (root / "module.py").read_text(encoding="utf-8") == "value = 1\n"


def test_transaction_rejects_too_many_changes(tmp_path: Path) -> None:
    """Bound the number of targets before planning."""

    _, workspace = create_workspace(tmp_path)
    changes = [
        patch_arguments(
            path=f"{index}.py",
            expected="",
            replacement="",
            create=True,
        )
        for index in range(MAX_TRANSACTION_FILES + 1)
    ]

    with pytest.raises(ValueError, match="changes"):
        preview_workspace_changes(workspace, transaction_arguments(*changes))


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/outside.py",
        "../outside.py",
        "nested/../../outside.py",
        "../workspace-backup/outside.py",
        ".git/config",
        "nested/.git/config",
        "missing/file.py",
    ],
)
def test_transaction_applies_single_file_path_protections(
    tmp_path: Path,
    path: str,
) -> None:
    """Apply the strict existing write boundary independently to every target."""

    root, workspace = create_workspace(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("outside\n", encoding="utf-8")

    with pytest.raises(ValueError):
        preview_workspace_changes(
            workspace,
            transaction_arguments(
                patch_arguments(
                    path=path,
                    expected="",
                    replacement="bad\n",
                    create=True,
                )
            ),
        )

    assert outside.read_text(encoding="utf-8") == "outside\n"
    assert list(root.iterdir()) == []


def test_transaction_rejects_symlinks_directories_special_files_and_binary(
    tmp_path: Path,
) -> None:
    """Never follow a target or parent symlink or accept non-text files."""

    root, workspace = create_workspace(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("outside\n", encoding="utf-8")
    (root / "external.py").symlink_to(outside)
    (root / "broken.py").symlink_to(root / "missing.py")
    directory = root / "directory"
    directory.mkdir()
    (root / "linked").symlink_to(directory, target_is_directory=True)
    (root / "binary.py").write_bytes(b"\xff")
    os.mkfifo(root / "pipe")

    for path in (
        "external.py",
        "broken.py",
        "directory",
        "linked/new.py",
        "binary.py",
        "pipe",
    ):
        with pytest.raises(ValueError):
            preview_workspace_changes(
                workspace,
                transaction_arguments(
                    patch_arguments(
                        path=path,
                        expected="",
                        replacement="bad\n",
                        create=path == "linked/new.py",
                    )
                ),
            )

    assert outside.read_text(encoding="utf-8") == "outside\n"


def test_transaction_enforces_combined_content_and_changed_line_limits(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Reject aggregate limits and accept their exact boundaries."""

    root, workspace = create_workspace(tmp_path)
    (root / "first.py").write_text("a\n", encoding="utf-8")
    (root / "second.py").write_text("b\n", encoding="utf-8")
    arguments = transaction_arguments(
        patch_arguments(path="first.py", expected="a\n", replacement="c\n"),
        patch_arguments(path="second.py", expected="b\n", replacement="d\n"),
    )

    monkeypatch.setattr(
        "agent_workbench.workspace_actions.MAX_TRANSACTION_EXPECTED_BYTES",
        4,
    )
    monkeypatch.setattr(
        "agent_workbench.workspace_actions.MAX_TRANSACTION_REPLACEMENT_BYTES",
        4,
    )
    monkeypatch.setattr(
        "agent_workbench.workspace_actions.MAX_TRANSACTION_CHANGED_LINES",
        4,
    )
    assert preview_workspace_changes(workspace, arguments)["operation_count"] == 2

    monkeypatch.setattr(
        "agent_workbench.workspace_actions.MAX_TRANSACTION_EXPECTED_BYTES",
        3,
    )
    with pytest.raises(ValueError, match="expected"):
        preview_workspace_changes(workspace, arguments)
    monkeypatch.setattr(
        "agent_workbench.workspace_actions.MAX_TRANSACTION_EXPECTED_BYTES",
        MAX_TRANSACTION_EXPECTED_BYTES,
    )
    monkeypatch.setattr(
        "agent_workbench.workspace_actions.MAX_TRANSACTION_REPLACEMENT_BYTES",
        3,
    )
    with pytest.raises(ValueError, match="replacement"):
        preview_workspace_changes(workspace, arguments)
    monkeypatch.setattr(
        "agent_workbench.workspace_actions.MAX_TRANSACTION_REPLACEMENT_BYTES",
        MAX_TRANSACTION_REPLACEMENT_BYTES,
    )
    monkeypatch.setattr(
        "agent_workbench.workspace_actions.MAX_TRANSACTION_CHANGED_LINES",
        3,
    )
    with pytest.raises(ValueError, match="changed"):
        preview_workspace_changes(workspace, arguments)
    assert MAX_TRANSACTION_CHANGED_LINES == 2_000


def test_transaction_rejects_incomplete_combined_preview(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Reject the whole request instead of truncating any combined diff."""

    root, workspace = create_workspace(tmp_path)
    (root / "module.py").write_text("old\n", encoding="utf-8")
    monkeypatch.setattr(
        "agent_workbench.workspace_actions.MAX_TRANSACTION_PREVIEW_BYTES",
        8,
    )

    with pytest.raises(ValueError, match="preview"):
        preview_workspace_changes(
            workspace,
            transaction_arguments(
                patch_arguments(expected="old\n", replacement="new\n")
            ),
        )


def test_transaction_stale_revalidation_writes_nothing(
    tmp_path: Path,
) -> None:
    """Revalidate every target after approval before the first mutation."""

    root, workspace = create_workspace(tmp_path)
    first = root / "first.py"
    second = root / "second.py"
    first.write_text("one\n", encoding="utf-8")
    second.write_text("two\n", encoding="utf-8")
    registry = ToolRegistry()
    register_workspace_action_tools(registry, workspace)
    arguments = transaction_arguments(
        patch_arguments(path="first.py", expected="one\n", replacement="ONE\n"),
        patch_arguments(path="second.py", expected="two\n", replacement="TWO\n"),
    )
    provider = FakeProvider(
        [
            ChatResponse(
                tool_invocations=(
                    ToolInvocation(
                        id="transaction",
                        tool_name="apply_workspace_changes",
                        arguments=arguments,
                    ),
                )
            )
        ]
    )

    def approve_after_change(request):
        second.write_text("concurrent\n", encoding="utf-8")
        return ToolApprovalDecision.APPROVE

    with pytest.raises(WorkspaceTransactionError, match="stale"):
        run_tool_calling_loop(
            provider,
            ChatRequest(messages=[]),
            registry,
            max_tool_rounds=1,
            tool_approval_handler=approve_after_change,
        )

    assert first.read_text(encoding="utf-8") == "one\n"
    assert second.read_text(encoding="utf-8") == "concurrent\n"
    assert list(root.glob(".agent-workbench-transaction-*")) == []


@pytest.mark.parametrize(
    "race",
    ["new_target", "target_symlink", "parent_symlink"],
)
def test_transaction_stale_target_mapping_writes_nothing(
    tmp_path: Path,
    race: str,
) -> None:
    """Reject appeared targets and unsafe target or parent mappings after preview."""

    root, workspace = create_workspace(tmp_path)
    stable = root / "a.py"
    stable.write_text("stable\n", encoding="utf-8")
    external = tmp_path / "external"
    external.mkdir()
    outside = external / "outside.py"
    outside.write_text("outside\n", encoding="utf-8")
    if race == "new_target":
        requested_path = "b.py"
        requested_expected = ""
        requested_create = True
    elif race == "target_symlink":
        requested_path = "b.py"
        requested_expected = "inside\n"
        requested_create = False
        (root / requested_path).write_text(requested_expected, encoding="utf-8")
    else:
        requested_path = "nested/b.py"
        requested_expected = ""
        requested_create = True
        (root / "nested").mkdir()

    arguments = transaction_arguments(
        patch_arguments(
            path="a.py",
            expected="stable\n",
            replacement="changed\n",
        ),
        patch_arguments(
            path=requested_path,
            expected=requested_expected,
            replacement="requested\n",
            create=requested_create,
        ),
    )
    registry = ToolRegistry()
    register_workspace_action_tools(registry, workspace)
    provider = FakeProvider(
        [
            ChatResponse(
                tool_invocations=(
                    ToolInvocation(
                        id="transaction",
                        tool_name="apply_workspace_changes",
                        arguments=arguments,
                    ),
                )
            )
        ]
    )

    def approve_after_race(request):
        if race == "new_target":
            (root / requested_path).write_text("concurrent\n", encoding="utf-8")
        elif race == "target_symlink":
            (root / requested_path).unlink()
            (root / requested_path).symlink_to(outside)
        else:
            (root / "nested").rmdir()
            (root / "nested").symlink_to(external, target_is_directory=True)
        return ToolApprovalDecision.APPROVE

    with pytest.raises(WorkspaceTransactionError, match="stale"):
        run_tool_calling_loop(
            provider,
            ChatRequest(messages=[]),
            registry,
            max_tool_rounds=1,
            tool_approval_handler=approve_after_race,
        )

    assert stable.read_text(encoding="utf-8") == "stable\n"
    assert outside.read_text(encoding="utf-8") == "outside\n"
    assert list(root.glob(".agent-workbench-transaction-*")) == []


def test_transaction_failure_before_first_write_changes_nothing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Clean prepared material when the first commit operation fails."""

    root, workspace = create_workspace(tmp_path)
    target = root / "a.py"
    target.write_text("old\n", encoding="utf-8")
    monkeypatch.setattr(
        "agent_workbench.workspace_actions._commit_prepared_change",
        lambda prepared: (_ for _ in ()).throw(OSError("injected")),
    )

    with pytest.raises(WorkspaceTransactionError, match="before any files"):
        apply_workspace_changes(
            workspace,
            transaction_arguments(
                patch_arguments(
                    path="a.py",
                    expected="old\n",
                    replacement="new\n",
                )
            ),
        )

    assert target.read_text(encoding="utf-8") == "old\n"
    assert list(root.glob(".agent-workbench-transaction-*")) == []


def test_transaction_rolls_back_mixed_changes_in_reverse_order(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Restore every applied update and remove transaction-created files."""

    root, workspace = create_workspace(tmp_path)
    first = root / "a.py"
    third = root / "c.py"
    first.write_text("one\n", encoding="utf-8")
    first.chmod(0o640)
    third.write_text("three\n", encoding="utf-8")
    arguments = transaction_arguments(
        patch_arguments(path="a.py", expected="one\n", replacement="ONE\n"),
        patch_arguments(
            path="b.py",
            expected="",
            replacement="TWO\n",
            create=True,
        ),
        patch_arguments(path="c.py", expected="three\n", replacement="THREE\n"),
    )
    import agent_workbench.workspace_actions as actions

    original_commit = actions._commit_prepared_change
    original_rollback = actions._rollback_applied_change
    commits = 0
    rollbacks: list[str] = []

    def fail_third(prepared):
        nonlocal commits
        commits += 1
        if commits == 3:
            raise OSError("injected")
        original_commit(prepared)

    def record_rollback(prepared):
        rollbacks.append(prepared.patch.relative_path)
        original_rollback(prepared)

    monkeypatch.setattr(actions, "_commit_prepared_change", fail_third)
    monkeypatch.setattr(actions, "_rollback_applied_change", record_rollback)

    with pytest.raises(WorkspaceTransactionError, match="rolled back"):
        apply_workspace_changes(workspace, arguments)

    assert first.read_text(encoding="utf-8") == "one\n"
    assert stat.S_IMODE(first.stat().st_mode) == 0o640
    assert not (root / "b.py").exists()
    assert third.read_text(encoding="utf-8") == "three\n"
    assert rollbacks == ["b.py", "a.py"]
    assert list(root.glob(".agent-workbench-transaction-*")) == []

    monkeypatch.setattr(actions, "_commit_prepared_change", original_commit)
    monkeypatch.setattr(actions, "_rollback_applied_change", original_rollback)
    assert apply_workspace_changes(workspace, arguments)["operation_count"] == 3


def test_transaction_reports_incomplete_rollback_with_only_relative_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Warn safely when handled rollback cannot restore an applied target."""

    root, workspace = create_workspace(tmp_path)
    target = root / "a.py"
    target.write_text("one\n", encoding="utf-8")
    arguments = transaction_arguments(
        patch_arguments(path="a.py", expected="one\n", replacement="ONE\n"),
        patch_arguments(
            path="b.py",
            expected="",
            replacement="TWO\n",
            create=True,
        ),
    )
    import agent_workbench.workspace_actions as actions

    original_commit = actions._commit_prepared_change
    commits = 0

    def fail_second(prepared):
        nonlocal commits
        commits += 1
        if commits == 2:
            raise OSError("injected")
        original_commit(prepared)

    monkeypatch.setattr(actions, "_commit_prepared_change", fail_second)
    monkeypatch.setattr(
        actions,
        "_rollback_applied_change",
        lambda prepared: (_ for _ in ()).throw(OSError("rollback injected")),
    )

    with pytest.raises(WorkspaceTransactionError) as raised:
        apply_workspace_changes(workspace, arguments)

    message = str(raised.value)
    assert "rollback was incomplete" in message
    assert "a.py" in message
    assert str(root) not in message
    assert list(root.glob(".agent-workbench-transaction-*")) == []


@pytest.mark.parametrize(
    "decision",
    [None, ToolApprovalDecision.DENY],
)
def test_transaction_missing_or_denied_approval_writes_nothing(
    tmp_path: Path,
    decision,
) -> None:
    """Keep every target unchanged until the exact transaction is approved."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text("old\n", encoding="utf-8")
    registry = ToolRegistry()
    register_workspace_action_tools(registry, workspace)
    provider = FakeProvider(
        [
            ChatResponse(
                tool_invocations=(
                    ToolInvocation(
                        id="transaction",
                        tool_name="apply_workspace_changes",
                        arguments=transaction_arguments(
                            patch_arguments(expected="old\n", replacement="new\n")
                        ),
                    ),
                )
            )
        ]
    )

    with pytest.raises(CompletionError):
        run_tool_calling_loop(
            provider,
            ChatRequest(messages=[]),
            registry,
            max_tool_rounds=1,
            tool_approval_handler=(
                None if decision is None else lambda request: decision
            ),
        )

    assert target.read_text(encoding="utf-8") == "old\n"


def test_transaction_requires_fresh_approval_for_each_invocation(
    tmp_path: Path,
) -> None:
    """Approve each exact transaction independently and execute it once."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    target.write_text("zero\n", encoding="utf-8")
    registry = ToolRegistry()
    register_workspace_action_tools(registry, workspace)
    provider = FakeProvider(
        [
            ChatResponse(
                tool_invocations=(
                    ToolInvocation(
                        id="first",
                        tool_name="apply_workspace_changes",
                        arguments=transaction_arguments(
                            patch_arguments(
                                expected="zero\n",
                                replacement="one\n",
                            )
                        ),
                    ),
                )
            ),
            ChatResponse(
                tool_invocations=(
                    ToolInvocation(
                        id="second",
                        tool_name="apply_workspace_changes",
                        arguments=transaction_arguments(
                            patch_arguments(
                                expected="one\n",
                                replacement="two\n",
                            )
                        ),
                    ),
                )
            ),
            ChatResponse(text="Done."),
        ]
    )
    approvals: list[str] = []

    result = run_tool_calling_loop(
        provider,
        ChatRequest(messages=[]),
        registry,
        max_tool_rounds=2,
        tool_approval_handler=lambda request: (
            approvals.append(request.invocation.id) or ToolApprovalDecision.APPROVE
        ),
    )

    assert result.text == "Done."
    assert approvals == ["first", "second"]
    assert target.read_text(encoding="utf-8") == "two\n"
