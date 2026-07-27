"""Tests for approved optimistic single-file workspace patches."""

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
    APPLY_WORKSPACE_CHANGES_DEFINITION,
    MAX_CHANGED_LINES,
    MAX_PATCH_CONTENT_BYTES,
    MAX_TRANSACTION_CHANGED_LINES,
    MAX_TRANSACTION_EXPECTED_BYTES,
    MAX_TRANSACTION_FILES,
    MAX_TRANSACTION_REPLACEMENT_BYTES,
    apply_file_patch,
    apply_workspace_changes,
    preview_file_patch,
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
        APPLY_WORKSPACE_CHANGES_DEFINITION,
    )
    assert APPLY_FILE_PATCH_DEFINITION.name == "apply_file_patch"
    assert APPLY_FILE_PATCH_DEFINITION.description == (
        "Apply one approved optimistic UTF-8 file patch inside the authorized "
        "workspace."
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
    assert target.read_text(encoding="utf-8") == "value = 1\n"


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
    }
    assert creation.output == {
        "path": "created.py",
        "operation": "create",
        "old_size_bytes": 0,
        "new_size_bytes": 0,
        "changed_lines": 0,
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


def test_transaction_registration_uses_exact_closed_nested_schema(
    tmp_path: Path,
) -> None:
    """Append the transaction after the compatible single-file action."""

    _, workspace = create_workspace(tmp_path)
    registry = ToolRegistry()

    register_workspace_action_tools(registry, workspace)

    assert registry.definitions == (
        APPLY_FILE_PATCH_DEFINITION,
        APPLY_WORKSPACE_CHANGES_DEFINITION,
    )
    assert APPLY_WORKSPACE_CHANGES_DEFINITION.name == "apply_workspace_changes"
    assert APPLY_WORKSPACE_CHANGES_DEFINITION.description == (
        "Apply one approved transactional set of UTF-8 file creations and "
        "updates inside the authorized workspace."
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
            },
            {
                "path": "z.py",
                "operation": "update",
                "old_size_bytes": 4,
                "new_size_bytes": 8,
                "changed_lines": 2,
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
