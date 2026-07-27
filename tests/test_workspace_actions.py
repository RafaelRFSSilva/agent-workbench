"""Tests for approved optimistic single-file workspace patches."""

import os
import stat
from pathlib import Path

import pytest

from agent_workbench.errors import CompletionError
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
    MAX_CHANGED_LINES,
    MAX_PATCH_CONTENT_BYTES,
    apply_file_patch,
    preview_file_patch,
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

    assert registry.definitions == (existing, APPLY_FILE_PATCH_DEFINITION)
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
