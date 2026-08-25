"""Vertical tests for the deterministic coding workflow controller."""

import hashlib
import subprocess
from collections.abc import Iterable
from pathlib import Path

import pytest

from agent_workbench import validation_tools, workspace_actions
from agent_workbench.coding_loop import (
    MAX_CONTROLLED_ACTION_ARGUMENT_VALIDATION_FAILURES,
    DEFAULT_DISCOVER_MAX_TOOL_ROUNDS,
    DEFAULT_EDIT_COMPLETION_CONTINUATIONS,
    DEFAULT_MAX_REPAIR_ATTEMPTS,
    DEFAULT_REPAIR_COMPLETION_CONTINUATIONS,
    MAX_ACTION_FAILURE_EVIDENCE_CHARACTERS,
    MAX_ACTION_FAILURE_EVIDENCE_ITEM_CHARACTERS,
    MAX_ACTION_FAILURE_EVIDENCE_ITEMS,
    MAX_REPAIR_VALIDATION_EVIDENCE_CHARACTERS,
    MAX_REPAIR_VALIDATION_FIELD_CHARACTERS,
    CodingProgressEvent,
    CodingProgressKind,
    CodingModelSendTrace,
    CodingPhase,
    CodingWorkflowLimits,
    _CONTROLLED_EDIT_SELECTION_GUIDANCE,
    _bounded_validation_failure_evidence,
    _format_validation_failure_evidence,
    _sanitize_prompt_text,
    _sanitize_validation_output,
    run_autonomous_coding_task,
)
from agent_workbench.errors import CompletionError
from agent_workbench.git_tools import (
    INSPECT_GIT_DIFF_DEFINITION,
    INSPECT_GIT_STATUS_DEFINITION,
    inspect_workspace_git_diff,
    inspect_workspace_git_status,
    register_git_tools,
)
from agent_workbench.messages import ChatRequest, ChatResponse
from agent_workbench.session import AgentSession, SessionId
from agent_workbench.symbol_tools import register_symbol_tools
from agent_workbench.tool_registry import ToolRegistry
from agent_workbench.tools import (
    ToolApprovalDecision,
    ToolInvocation,
    ToolResult,
)
from agent_workbench.validation_tools import register_validation_tools
from agent_workbench.workspace import Workspace
from agent_workbench.workspace_actions import register_workspace_action_tools
from agent_workbench.workspace_tools import register_workspace_tools


class ScriptedProvider:
    """Return deterministic responses and retain every phase request."""

    name = "scripted"
    model_name = "scripted-coding-model"

    def __init__(
        self,
        responses: Iterable[ChatResponse | CompletionError],
    ) -> None:
        self._responses = iter(responses)
        self.requests: list[ChatRequest] = []

    def complete(self, request: ChatRequest) -> ChatResponse:
        """Record one request and return the next scripted response."""

        self.requests.append(request)
        response = next(self._responses)
        if isinstance(response, CompletionError):
            raise response
        return response


def run_git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run one Git command inside a disposable test repository."""

    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def create_coding_repository(root: Path, *, passing: bool = False) -> Path:
    """Create one committed Python project with a fixed or defective function."""

    root.mkdir()
    run_git(root, "init", "-b", "main")
    run_git(root, "config", "user.name", "Test User")
    run_git(root, "config", "user.email", "test@example.com")
    operator = "+" if passing else "-"
    (root / "module.py").write_text(
        f"def add(left: int, right: int) -> int:\n    return left {operator} right\n",
        encoding="utf-8",
    )
    (root / "test_module.py").write_text(
        "from module import add\n"
        "\n"
        "\n"
        "def test_add() -> None:\n"
        "    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    run_git(root, "add", "module.py", "test_module.py")
    run_git(root, "commit", "-m", "initial")
    return root


def create_coding_registry(
    workspace: Workspace,
    *,
    fail_git_diff: bool = False,
) -> ToolRegistry:
    """Register real bounded tools, optionally forcing final diff failure."""

    registry = ToolRegistry()
    register_workspace_tools(registry, workspace)
    register_symbol_tools(registry, workspace)
    if fail_git_diff:
        registry.register(
            INSPECT_GIT_STATUS_DEFINITION,
            lambda arguments: inspect_workspace_git_status(workspace, arguments),
        )
        registry.register(
            INSPECT_GIT_DIFF_DEFINITION,
            lambda arguments: (_ for _ in ()).throw(ValueError("forced failure")),
        )
    else:
        register_git_tools(registry, workspace)
    register_workspace_action_tools(registry, workspace)
    register_validation_tools(registry, workspace)
    return registry


def create_session(
    repository: Path,
    provider: ScriptedProvider,
    *,
    fail_git_diff: bool = False,
    max_tool_rounds: int = 8,
) -> AgentSession:
    """Create one action-enabled scripted coding session."""

    return AgentSession(
        id=SessionId("deterministic-coding-test"),
        provider=provider,
        tool_registry=create_coding_registry(
            Workspace(repository),
            fail_git_diff=fail_git_diff,
        ),
        max_tool_rounds=max_tool_rounds,
    )


def tool_response(
    invocation_id: str,
    tool_name: str,
    arguments: dict[str, object],
    *,
    response_repair_attempt_count: int = 0,
) -> ChatResponse:
    """Create one deterministic single-tool provider response."""

    return ChatResponse(
        tool_invocations=(
            ToolInvocation(
                id=invocation_id,
                tool_name=tool_name,
                arguments=arguments,
            ),
        ),
        response_repair_attempt_count=response_repair_attempt_count,
    )


def replacement_response(
    invocation_id: str,
    *,
    expected_content: str,
    expected_text: str,
    replacement_text: str,
) -> ChatResponse:
    """Create one optimistic literal replacement response."""

    return tool_response(
        invocation_id,
        "apply_text_replacement",
        {
            "path": "module.py",
            "expected_text": expected_text,
            "replacement_text": replacement_text,
            "expected_file_sha256": hashlib.sha256(
                expected_content.encode("utf-8")
            ).hexdigest(),
        },
    )


def rewrite_response(
    invocation_id: str,
    *,
    expected_content: str,
    replacement_content: str,
) -> ChatResponse:
    """Create one optimistic SHA-guarded whole-file rewrite response."""

    return tool_response(
        invocation_id,
        "apply_file_rewrite",
        {
            "path": "module.py",
            "expected_file_sha256": hashlib.sha256(
                expected_content.encode("utf-8")
            ).hexdigest(),
            "replacement_content": replacement_content,
        },
    )


def line_range_response(
    invocation_id: str,
    *,
    expected_content: str,
    start_line: int,
    end_line: int,
    replacement_content: str,
) -> ChatResponse:
    """Create one optimistic SHA-guarded line-range response."""

    return tool_response(
        invocation_id,
        "apply_line_range_replacement",
        {
            "path": "module.py",
            "start_line": start_line,
            "end_line": end_line,
            "replacement_content": replacement_content,
            "expected_file_sha256": hashlib.sha256(
                expected_content.encode("utf-8")
            ).hexdigest(),
        },
    )


def approve(_request) -> ToolApprovalDecision:
    """Approve one action inside a disposable test repository."""

    return ToolApprovalDecision.APPROVE


def test_controlled_edit_guidance_reuses_successful_result_sha_and_rereads_failures() -> (
    None
):
    """Tell the model when resulting SHA evidence is reusable or invalid."""

    assert "resulting_file_sha256" in _CONTROLLED_EDIT_SELECTION_GUIDANCE
    assert "instead of rereading solely to obtain a SHA" in (
        _CONTROLLED_EDIT_SELECTION_GUIDANCE
    )
    assert "If an action fails, is stale, or does not apply, reread the target" in (
        _CONTROLLED_EDIT_SELECTION_GUIDANCE
    )


def test_controller_runs_discover_edit_validate_verify_and_done(
    tmp_path: Path,
) -> None:
    """Advance only from observed actions and controller-run gate evidence."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    provider = ScriptedProvider(
        [
            tool_response("read", "read_file", {"path": "module.py"}),
            ChatResponse(text="Discovery complete."),
            replacement_response(
                "edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="Corrected the add implementation."),
            ChatResponse(text="Corrected the add implementation."),
        ]
    )
    progress = []

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        tool_approval_handler=approve,
        progress_event_observer=progress.append,
    )

    assert result.final_phase is CodingPhase.DONE
    assert result.workspace_change_applied is True
    assert result.repair_attempt_count == 0
    assert result.completion_continuation_count == 1
    assert result.tool_round_count == 7
    assert result.executed_tool_names == (
        "read_file",
        "apply_text_replacement",
        "run_ruff_format",
        "run_ruff_check",
        "run_pytest",
        "inspect_git_status",
        "inspect_git_diff",
    )
    assert result.approved_action_names == (
        "apply_text_replacement",
        "run_ruff_format",
        "run_ruff_check",
        "run_pytest",
    )
    assert result.approved_workspace_paths == ("module.py",)
    assert result.validation_succeeded is True
    assert len(result.tool_results) == result.tool_round_count
    assert all(tool_result.status == "success" for tool_result in result.tool_results)
    assert result.inspected_git_status_after_change is True
    assert result.inspected_git_diff_after_change is True
    assert result.assistant_summary == "Corrected the add implementation."
    assert run_git(repository, "status", "--short").stdout == " M module.py\n"
    assert [event.kind for event in progress] == [
        CodingProgressKind.PHASE_STARTED,
        CodingProgressKind.PHASE_COMPLETED,
        CodingProgressKind.PHASE_STARTED,
        CodingProgressKind.WORKSPACE_CHANGED,
        CodingProgressKind.PHASE_STARTED,
        CodingProgressKind.VALIDATION_RESULT,
        CodingProgressKind.VALIDATION_RESULT,
        CodingProgressKind.VALIDATION_RESULT,
        CodingProgressKind.PHASE_STARTED,
        CodingProgressKind.CHANGED_PATH_COUNT,
        CodingProgressKind.DONE,
    ]
    assert [event.phase for event in progress] == [
        CodingPhase.DISCOVER,
        CodingPhase.DISCOVER,
        CodingPhase.EDIT,
        CodingPhase.EDIT,
        CodingPhase.VALIDATE,
        CodingPhase.VALIDATE,
        CodingPhase.VALIDATE,
        CodingPhase.VALIDATE,
        CodingPhase.VERIFY,
        CodingPhase.VERIFY,
        CodingPhase.DONE,
    ]
    assert progress[3].path == "module.py"
    assert progress[5].tool_name == "run_ruff_format"
    assert progress[7].validation_summary == "1 passed"
    assert progress[9].changed_path_count == 1

    discover_tools = {tool.name for tool in provider.requests[0].tools}
    assert {
        "list_files",
        "read_file",
        "search_text",
        "search_symbols",
        "inspect_git_status",
        "inspect_git_diff",
    } == discover_tools
    assert not discover_tools.intersection(
        {
            "apply_file_patch",
            "apply_file_rewrite",
            "apply_text_replacement",
            "apply_line_range_replacement",
            "apply_workspace_changes",
            "run_ruff_format",
            "run_ruff_check",
            "run_pytest",
        }
    )
    edit_tools = {tool.name for tool in provider.requests[2].tools}
    assert {
        "apply_file_patch",
        "apply_file_rewrite",
        "apply_text_replacement",
        "apply_line_range_replacement",
        "apply_workspace_changes",
    }.issubset(edit_tools)
    assert not edit_tools.intersection(
        {"run_ruff_format", "run_ruff_check", "run_pytest"}
    )


def test_line_range_action_is_compatible_with_final_workspace_verification(
    tmp_path: Path,
) -> None:
    """Track an approved range edit through validation and final Git inspection."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    provider = ScriptedProvider(
        [
            tool_response("read", "read_file", {"path": "module.py"}),
            ChatResponse(text="Discovery complete."),
            line_range_response(
                "edit",
                expected_content=original,
                start_line=2,
                end_line=2,
                replacement_content="    return left + right\n",
            ),
            ChatResponse(text="Corrected the add implementation."),
            ChatResponse(text="Corrected the add implementation."),
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    assert result.final_phase is CodingPhase.DONE
    assert result.workspace_change_applied is True
    assert result.approved_workspace_paths == ("module.py",)
    assert result.inspected_git_status_after_change is True
    assert result.inspected_git_diff_after_change is True
    assert "apply_line_range_replacement" in result.approved_action_names
    assert run_git(repository, "status", "--short").stdout == " M module.py\n"


def test_large_file_line_range_regression_changes_only_inspected_middle_range(
    tmp_path: Path,
) -> None:
    """Avoid fragile exact-fragment retries while preserving surrounding bytes."""

    repository = create_coding_repository(tmp_path / "project")
    prefix = "".join(f"# prefix {index:04d}\n" for index in range(1, 601))
    selected = "    return left - right\n"
    suffix = "\n\n" + "".join(f"# suffix {index:04d}\n" for index in range(1, 601))
    original = (
        prefix + "\n\ndef add(left: int, right: int) -> int:\n" + selected + suffix
    )
    target = repository / "module.py"
    target.write_bytes(original.encode("utf-8"))
    run_git(repository, "add", "module.py")
    run_git(repository, "commit", "-m", "add large source fixture")
    expected_sha256 = hashlib.sha256(original.encode("utf-8")).hexdigest()
    approvals = []
    provider = ScriptedProvider(
        [
            tool_response(
                "inspect-middle",
                "read_file",
                {"path": "module.py", "line_start": 600, "line_end": 607},
            ),
            ChatResponse(text="Inspected the target range."),
            line_range_response(
                "edit-middle",
                expected_content=original,
                start_line=604,
                end_line=604,
                replacement_content="    return left + right\n",
            ),
            ChatResponse(text="Changed only the inspected range."),
            ChatResponse(text="Changed only the inspected range."),
        ]
    )

    def approve_and_capture(request) -> ToolApprovalDecision:
        if request.invocation.tool_name == "apply_line_range_replacement":
            approvals.append(request.preview)
        return ToolApprovalDecision.APPROVE

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation in the large source file.",
        tool_approval_handler=approve_and_capture,
    )

    updated = target.read_bytes()
    prefix_bytes = (prefix + "\n\ndef add(left: int, right: int) -> int:\n").encode(
        "utf-8"
    )
    suffix_bytes = suffix.encode("utf-8")
    assert updated[: len(prefix_bytes)] == prefix_bytes
    assert updated[-len(suffix_bytes) :] == suffix_bytes
    assert updated == (prefix_bytes + b"    return left + right\n" + suffix_bytes)
    assert expected_sha256 != hashlib.sha256(updated).hexdigest()
    assert approvals == [
        {
            "path": "module.py",
            "operation": "update",
            "old_size_bytes": len(original.encode("utf-8")),
            "new_size_bytes": len(updated),
            "changed_lines": 2,
            "start_line": 604,
            "end_line": 604,
            "diff": (
                "--- a/module.py\n"
                "+++ b/module.py\n"
                "@@ -601,7 +601,7 @@\n"
                " \n"
                " \n"
                " def add(left: int, right: int) -> int:\n"
                "-    return left - right\n"
                "+    return left + right\n"
                " \n"
                " \n"
                " # suffix 0001\n"
            ),
        }
    ]
    assert result.final_phase is CodingPhase.DONE
    assert result.approved_workspace_paths == ("module.py",)
    assert run_git(repository, "status", "--short").stdout == " M module.py\n"


def test_invalid_patch_arguments_are_corrected_before_one_approved_edit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Return shape guidance, then preview, approve, and execute exactly once."""

    repository = create_coding_repository(tmp_path / "project")
    original = (repository / "module.py").read_text(encoding="utf-8")
    corrected = original.replace("left - right", "left + right")
    unsupported_names = (
        str(repository / "private.env"),
        "PRIVATE_TOKEN = 'secret-value'\nprint(PRIVATE_TOKEN)",
        "line\nbreak\rreturn\ttab\x1b[31mred",
    )
    invalid_arguments = {
        "path": "module.py",
        "expected_content": original,
        "replacement_content": corrected,
    }
    invalid_arguments.update(dict.fromkeys(unsupported_names, "untrusted value"))
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            tool_response(
                "invalid-edit",
                "apply_file_patch",
                invalid_arguments,
            ),
            tool_response(
                "corrected-edit",
                "apply_file_patch",
                {
                    "path": "module.py",
                    "expected_content": original,
                    "replacement_content": corrected,
                },
            ),
            ChatResponse(text="Edit complete."),
            ChatResponse(text="Edit complete."),
        ]
    )
    replacements = []
    previews = []
    original_replace = workspace_actions._replace_file_atomically
    original_preview = workspace_actions.preview_file_patch

    def record_replace(workspace, patch):
        replacements.append(patch.relative_path)
        original_replace(workspace, patch)

    def record_preview(workspace, arguments):
        previews.append(arguments)
        return original_preview(workspace, arguments)

    monkeypatch.setattr(
        workspace_actions,
        "_replace_file_atomically",
        record_replace,
    )
    monkeypatch.setattr(
        workspace_actions,
        "preview_file_patch",
        record_preview,
    )
    approvals = []

    def approve_corrected(request):
        if request.invocation.tool_name == "apply_file_patch":
            assert (repository / "module.py").read_text(encoding="utf-8") == original
        approvals.append(request)
        return ToolApprovalDecision.APPROVE

    progress: list[CodingProgressEvent] = []
    result = run_autonomous_coding_task(
        create_session(repository, provider, max_tool_rounds=1),
        "Correct the add implementation.",
        tool_approval_handler=approve_corrected,
        progress_event_observer=progress.append,
    )

    invalid_result = provider.requests[2].tool_interactions[0].results[0]
    assert invalid_result.status == "error"
    assert invalid_result.error is not None
    assert "Tool 'apply_file_patch' argument validation failed" in invalid_result.error
    assert (
        "arguments contain 3 unsupported fields; additional fields are not allowed"
        in invalid_result.error
    )
    assert "Required structured shape" in invalid_result.error
    assert "Issue a corrected apply_file_patch tool call" in invalid_result.error
    assert len(invalid_result.error) <= 800
    for unsupported_name in unsupported_names:
        assert unsupported_name not in invalid_result.error
    assert str(repository) not in invalid_result.error
    assert "PRIVATE_TOKEN" not in invalid_result.error
    assert "secret-value" not in invalid_result.error
    assert "\x1b[31m" not in invalid_result.error
    assert [
        request.invocation.id
        for request in approvals
        if request.invocation.tool_name == "apply_file_patch"
    ] == ["corrected-edit"]
    assert previews == [
        {
            "path": "module.py",
            "expected_content": original,
            "replacement_content": corrected,
        }
    ]
    assert replacements == ["module.py"]
    rejected = [
        event
        for event in progress
        if event.kind is CodingProgressKind.ACTION_ARGUMENTS_REJECTED
    ]
    assert len(rejected) == 1
    assert rejected[0].path == "module.py"
    assert rejected[0].reason is not None
    assert "arguments contain 3 unsupported fields" in rejected[0].reason
    for unsupported_name in unsupported_names:
        assert unsupported_name not in rejected[0].reason
    assert str(repository) not in rejected[0].reason
    assert "PRIVATE_TOKEN" not in rejected[0].reason
    assert "\x1b[31m" not in rejected[0].reason
    assert result.final_phase is CodingPhase.DONE
    assert (repository / "module.py").read_text(encoding="utf-8") == corrected


@pytest.mark.parametrize(
    "tool_name",
    ["apply_file_patch", "apply_line_range_replacement"],
)
def test_repeated_invalid_controlled_action_arguments_remain_bounded(
    tmp_path: Path,
    tool_name: str,
) -> None:
    """Preserve the workspace after the fixed controlled-action recovery bound."""

    repository = create_coding_repository(tmp_path / "project")
    original = (repository / "module.py").read_text(encoding="utf-8")
    unsupported_name = (
        f"{repository}/private.env\nPRIVATE_TOKEN=secret-value\rreturn\ttab\x1b[31mred"
    )
    valid_arguments: dict[str, object]
    if tool_name == "apply_file_patch":
        valid_arguments = {
            "path": "module.py",
            "expected_content": original,
            "replacement_content": original.replace(
                "left - right",
                "left + right",
            ),
        }
    else:
        valid_arguments = {
            "path": "module.py",
            "start_line": 2,
            "end_line": 2,
            "replacement_content": "    return left + right\n",
            "expected_file_sha256": hashlib.sha256(
                original.encode("utf-8")
            ).hexdigest(),
        }
    invalid_responses = [
        tool_response(
            f"invalid-{index}",
            tool_name,
            {**valid_arguments, unsupported_name: index},
        )
        for index in range(MAX_CONTROLLED_ACTION_ARGUMENT_VALIDATION_FAILURES)
    ]
    responses = [ChatResponse(text="Discovery complete.")]
    for invalid_response in invalid_responses:
        responses.extend(
            [
                invalid_response,
                ChatResponse(text="I did not correct the invocation."),
            ]
        )
    provider = ScriptedProvider(responses)
    approvals = []
    progress: list[CodingProgressEvent] = []

    with pytest.raises(CompletionError) as raised:
        run_autonomous_coding_task(
            create_session(repository, provider),
            "Correct the add implementation.",
            tool_approval_handler=(
                lambda request: (
                    approvals.append(request) or ToolApprovalDecision.APPROVE
                )
            ),
            progress_event_observer=progress.append,
        )

    message = str(raised.value)
    assert "controlled action argument recovery limit reached" in message
    assert "phase=EDIT" in message
    assert f"tool={tool_name}" in message
    assert "argument_validation_failures=2" in message
    assert "tool_round_count=" in message
    assert "correction_opportunity_provided=true" in message
    assert str(repository) not in message
    assert unsupported_name not in message
    assert "PRIVATE_TOKEN" not in message
    assert "secret-value" not in message
    assert "\x1b[31m" not in message
    assert approvals == []
    assert (repository / "module.py").read_text(encoding="utf-8") == original
    assert run_git(repository, "status", "--short").stdout == ""
    assert (
        sum(
            event.kind is CodingProgressKind.ACTION_ARGUMENTS_REJECTED
            for event in progress
        )
        == MAX_CONTROLLED_ACTION_ARGUMENT_VALIDATION_FAILURES
    )
    assert progress[-1].kind is CodingProgressKind.TERMINAL_FAILURE
    assert progress[-1].workspace_preserved is True
    for event in progress:
        if event.reason is None:
            continue
        assert unsupported_name not in event.reason
        assert str(repository) not in event.reason
        assert "PRIVATE_TOKEN" not in event.reason
        assert "secret-value" not in event.reason
        assert "\x1b[31m" not in event.reason


def test_multi_file_edit_recovers_after_successful_controlled_action(
    tmp_path: Path,
) -> None:
    """Treat malformed action recovery as consecutive within one EDIT sequence."""

    repository = create_coding_repository(tmp_path / "project")
    original_module = (repository / "module.py").read_text(encoding="utf-8")
    updated_module = original_module.replace("left - right", "left + right")
    original_test = (repository / "test_module.py").read_text(encoding="utf-8")
    updated_test = original_test.replace("def test_add()", "def test_add_numbers()")

    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            tool_response(
                "invalid-module",
                "apply_file_rewrite",
                {
                    "path": "module.py",
                    "expected_file_sha256": hashlib.sha256(
                        original_module.encode("utf-8")
                    ).hexdigest(),
                    "replacement_content": updated_module,
                    "unsupported": "field",
                },
            ),
            tool_response(
                "corrected-module",
                "apply_file_rewrite",
                {
                    "path": "module.py",
                    "expected_file_sha256": hashlib.sha256(
                        original_module.encode("utf-8")
                    ).hexdigest(),
                    "replacement_content": updated_module,
                },
            ),
            tool_response(
                "invalid-test",
                "apply_text_replacement",
                {
                    "path": "test_module.py",
                    "expected_text": "def test_add() -> None:\n",
                },
            ),
            tool_response(
                "corrected-test",
                "apply_text_replacement",
                {
                    "path": "test_module.py",
                    "expected_text": "def test_add() -> None:\n",
                    "replacement_text": "def test_add_numbers() -> None:\n",
                    "expected_file_sha256": hashlib.sha256(
                        original_test.encode("utf-8")
                    ).hexdigest(),
                },
            ),
            tool_response(
                "apply-init",
                "apply_file_patch",
                {
                    "path": "__init__.py",
                    "expected_content": "",
                    "replacement_content": '"""Project package marker."""\n',
                    "create_if_missing": True,
                },
            ),
            ChatResponse(text="Edit complete."),
            ChatResponse(text="Edit complete."),
        ]
    )
    progress: list[CodingProgressEvent] = []

    result = run_autonomous_coding_task(
        create_session(repository, provider, max_tool_rounds=32),
        "Update module and tests across multiple files.",
        tool_approval_handler=approve,
        progress_event_observer=progress.append,
    )

    assert result.final_phase is CodingPhase.DONE
    assert result.validation_succeeded is True
    assert result.approved_workspace_paths == (
        "__init__.py",
        "module.py",
        "test_module.py",
    )
    assert (repository / "module.py").read_text(encoding="utf-8") == updated_module
    assert (repository / "test_module.py").read_text(encoding="utf-8") == updated_test
    assert (repository / "__init__.py").read_text(encoding="utf-8") == (
        '"""Project package marker."""\n'
    )

    rejected_paths = [
        event.path
        for event in progress
        if event.kind is CodingProgressKind.ACTION_ARGUMENTS_REJECTED
    ]
    changed_paths = [
        event.path
        for event in progress
        if event.kind is CodingProgressKind.WORKSPACE_CHANGED
    ]
    assert rejected_paths == ["module.py", "test_module.py"]
    assert changed_paths == ["module.py", "test_module.py", "__init__.py"]


def test_edit_uses_derived_inspection_budget_before_approved_change(
    tmp_path: Path,
) -> None:
    """Reach DONE after exhausting productive inspection in EDIT."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    inspections = [
        tool_response(
            f"search-{index}",
            "search_text",
            {"query": f"distinct-query-{index}"},
        )
        for index in range(19)
    ]
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            *inspections,
            replacement_response(
                "edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="Edit complete."),
            ChatResponse(text="Edit complete."),
        ]
    )
    approval_names: list[str] = []

    def record_approval(request) -> ToolApprovalDecision:
        approval_names.append(request.invocation.tool_name)
        return ToolApprovalDecision.APPROVE

    result = run_autonomous_coding_task(
        create_session(repository, provider, max_tool_rounds=20),
        "Correct the add implementation.",
        tool_approval_handler=record_approval,
    )

    assert result.final_phase is CodingPhase.DONE
    assert result.workspace_change_applied is True
    assert result.executed_tool_names[:20] == (
        *("search_text" for _ in range(19)),
        "apply_text_replacement",
    )
    assert approval_names == [
        "apply_text_replacement",
        "run_ruff_format",
        "run_ruff_check",
        "run_pytest",
    ]
    assert all(
        "search_text" in {tool.name for tool in request.tools}
        for request in provider.requests[1:20]
    )
    assert "search_text" not in {tool.name for tool in provider.requests[20].tools}
    assert "apply_text_replacement" in {
        tool.name for tool in provider.requests[20].tools
    }
    assert provider.requests[20].system_prompt is not None
    assert "productive read-only inspection budget is exhausted" in (
        provider.requests[20].system_prompt
    )
    assert (repository / "module.py").read_text(encoding="utf-8") == original.replace(
        "return left - right",
        "return left + right",
    )


def test_edit_rejects_inspection_after_budget_guidance_without_changes(
    tmp_path: Path,
) -> None:
    """Preserve the workspace when bounded synthesis still requests inspection."""

    repository = create_coding_repository(tmp_path / "project")
    inspections = [
        tool_response(
            f"search-{index}",
            "search_text",
            {"query": f"distinct-query-{index}"},
        )
        for index in range(19)
    ]
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            *inspections,
            tool_response("ignored-first", "read_file", {"path": "module.py"}),
            tool_response("ignored-second", "read_file", {"path": "test_module.py"}),
        ]
    )
    approval_names: list[str] = []

    def record_approval(request) -> ToolApprovalDecision:
        approval_names.append(request.invocation.tool_name)
        return ToolApprovalDecision.APPROVE

    with pytest.raises(
        CompletionError,
        match=(
            r"phase EDIT: model-facing phase failed: The provider repeatedly "
            r"requested a read-only inspection tool.*"
            r"requested_inspection=read_file.*"
            r"tool_round_count=19/20.*"
            r"productive_inspection_count=19.*"
            r"duplicate_count=0.*"
            r"inspection_streak_count=19.*"
            r"inspection_budget=19.*"
            r"reserved_synthesis_action_rounds=1.*"
            r"response_repair_attempt_count=0"
        ),
    ):
        run_autonomous_coding_task(
            create_session(repository, provider, max_tool_rounds=20),
            "Correct the add implementation.",
            tool_approval_handler=record_approval,
        )

    assert approval_names == []
    assert "read_file" not in {tool.name for tool in provider.requests[20].tools}
    assert "apply_text_replacement" in {
        tool.name for tool in provider.requests[20].tools
    }
    assert run_git(repository, "status", "--short").stdout == ""


def test_edit_executes_repeated_safe_read_then_applies_approved_change(
    tmp_path: Path,
) -> None:
    """Resume EDIT after a response-recovery read repeats the latest inspection."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            tool_response("initial-read", "read_file", {"path": "module.py"}),
            tool_response(
                "recovery-read",
                "read_file",
                {"path": "module.py"},
                response_repair_attempt_count=1,
            ),
            replacement_response(
                "edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="Edit complete."),
            ChatResponse(text="Edit complete."),
        ]
    )
    approval_names: list[str] = []

    def record_approval(request) -> ToolApprovalDecision:
        approval_names.append(request.invocation.tool_name)
        return ToolApprovalDecision.APPROVE

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        tool_approval_handler=record_approval,
    )

    assert result.final_phase is CodingPhase.DONE
    assert result.repair_attempt_count == 0
    assert result.tool_round_count == 8
    assert result.executed_tool_names[:3] == (
        "read_file",
        "read_file",
        "apply_text_replacement",
    )
    assert approval_names == [
        "apply_text_replacement",
        "run_ruff_format",
        "run_ruff_check",
        "run_pytest",
    ]
    assert "read_file" in {tool.name for tool in provider.requests[3].tools}
    assert len(provider.requests[3].tool_interactions) == 2
    assert (
        provider.requests[3].tool_interactions[1].response.response_repair_attempt_count
        == 1
    )
    assert all(
        round_.results[0].status == "success"
        for round_ in provider.requests[3].tool_interactions
    )
    assert run_git(repository, "status", "--short").stdout == " M module.py\n"


def test_edit_rejects_ordinary_duplicate_then_allows_alternative_inspection(
    tmp_path: Path,
) -> None:
    """Reach DONE after an ordinary duplicate result and a different inspection."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            tool_response("initial-read", "read_file", {"path": "module.py"}),
            tool_response("duplicate-read", "read_file", {"path": "module.py"}),
            tool_response(
                "alternative-search",
                "search_text",
                {"query": "assert add", "path": "test_module.py"},
            ),
            replacement_response(
                "edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="Edit complete."),
            ChatResponse(text="Edit complete."),
        ]
    )
    approval_names: list[str] = []

    def record_approval(request) -> ToolApprovalDecision:
        approval_names.append(request.invocation.tool_name)
        return ToolApprovalDecision.APPROVE

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        tool_approval_handler=record_approval,
    )

    assert result.final_phase is CodingPhase.DONE
    assert result.repair_attempt_count == 0
    assert approval_names == [
        "apply_text_replacement",
        "run_ruff_format",
        "run_ruff_check",
        "run_pytest",
    ]
    duplicate_round = provider.requests[3].tool_interactions[1]
    assert duplicate_round.results[0].status == "error"
    assert duplicate_round.results[0].error is not None
    assert "same read-only invocation already completed" in (
        duplicate_round.results[0].error
    )
    alternative_round = provider.requests[4].tool_interactions[2]
    assert alternative_round.response.tool_invocations[0].tool_name == "search_text"
    assert alternative_round.results[0].status == "success"
    available_after_duplicate = {
        definition.name for definition in provider.requests[3].tools
    }
    assert {
        "list_files",
        "read_file",
        "search_text",
        "search_symbols",
        "inspect_git_status",
        "inspect_git_diff",
    }.issubset(available_after_duplicate)
    assert run_git(repository, "status", "--short").stdout == " M module.py\n"


def test_edit_infinite_duplicate_stops_with_diagnostics_and_no_approval(
    tmp_path: Path,
) -> None:
    """Bound ordinary duplicate repetition without changing the workspace."""

    repository = create_coding_repository(tmp_path / "project")
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            *(
                tool_response(
                    f"repeated-read-{index}",
                    "read_file",
                    {"path": "module.py"},
                )
                for index in range(4)
            ),
        ]
    )
    approval_names: list[str] = []

    def record_approval(request) -> ToolApprovalDecision:
        approval_names.append(request.invocation.tool_name)
        return ToolApprovalDecision.APPROVE

    with pytest.raises(
        CompletionError,
        match=(
            r"phase EDIT: model-facing phase failed: The maximum number of tool "
            r"execution rounds was exceeded.*"
            r"requested_inspection=read_file.*"
            r"tool_round_count=3/3.*"
            r"duplicate_count=3.*"
            r"inspection_streak_count=0.*"
            r"response_repair_attempt_count=0.*"
            r"alternative_inspection_tools_available=true"
        ),
    ):
        run_autonomous_coding_task(
            create_session(repository, provider, max_tool_rounds=3),
            "Correct the add implementation.",
            tool_approval_handler=record_approval,
        )

    assert approval_names == []
    assert run_git(repository, "status", "--short").stdout == ""


def test_formats_only_successful_approved_python_path_and_preserves_baseline_dirty(
    tmp_path: Path,
) -> None:
    """Never format an unrelated baseline-dirty Python file."""

    repository = create_coding_repository(tmp_path / "project", passing=True)
    original_module = (repository / "module.py").read_text(encoding="utf-8")
    dirty_test = (
        "from module import add\n\n\ndef test_add() -> None:\n    assert add(1,2)==3\n"
    )
    (repository / "test_module.py").write_text(dirty_test, encoding="utf-8")
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            replacement_response(
                "edit",
                expected_content=original_module,
                expected_text="return left + right",
                replacement_text="return  left+right",
            ),
            ChatResponse(text="Edit complete."),
            ChatResponse(text="Edit complete."),
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Exercise scoped formatting.",
        tool_approval_handler=approve,
    )

    format_runs = [
        run for run in result.validation_runs if run.tool_name == "run_ruff_format"
    ]
    assert [run.target_paths for run in format_runs] == [("module.py",)]
    assert format_runs[0].skipped is False
    assert (
        (repository / "module.py")
        .read_text(encoding="utf-8")
        .endswith("return left + right\n")
    )
    assert (repository / "test_module.py").read_text(encoding="utf-8") == dirty_test
    assert result.baseline_changed_paths == ("test_module.py",)


def test_formats_multiple_successful_approved_python_paths_in_sorted_order(
    tmp_path: Path,
) -> None:
    """Format every approved changed Python file in deterministic path order."""

    repository = create_coding_repository(tmp_path / "project", passing=True)
    (repository / "alpha.py").write_text("value = 1\n", encoding="utf-8")
    (repository / "zeta.py").write_text("value = 1\n", encoding="utf-8")
    run_git(repository, "add", "alpha.py", "zeta.py")
    run_git(repository, "commit", "-m", "add formatting fixtures")
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            tool_response(
                "edit",
                "apply_workspace_changes",
                {
                    "changes": [
                        {
                            "path": "zeta.py",
                            "expected_content": "value = 1\n",
                            "replacement_content": "value=2\n",
                        },
                        {
                            "path": "alpha.py",
                            "expected_content": "value = 1\n",
                            "replacement_content": "value=2\n",
                        },
                    ]
                },
            ),
            ChatResponse(text="Edit complete."),
            ChatResponse(text="Edit complete."),
        ]
    )
    observed = []

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Update both modules.",
        tool_approval_handler=approve,
        tool_round_observer=observed.append,
    )

    format_targets = [
        invocation.arguments["path"]
        for round_ in observed
        for invocation in round_.response.tool_invocations
        if invocation.tool_name == "run_ruff_format"
    ]
    assert format_targets == ["alpha.py", "zeta.py"]
    assert [
        run.target_paths
        for run in result.validation_runs
        if run.tool_name == "run_ruff_format"
    ] == [("alpha.py",), ("zeta.py",)]


def test_non_python_change_skips_formatter_but_runs_project_checks(
    tmp_path: Path,
) -> None:
    """Represent the safe formatter skip while retaining read-only validation."""

    repository = create_coding_repository(tmp_path / "project", passing=True)
    (repository / "README.md").write_text("before\n", encoding="utf-8")
    run_git(repository, "add", "README.md")
    run_git(repository, "commit", "-m", "add readme")
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            tool_response(
                "edit",
                "apply_file_patch",
                {
                    "path": "README.md",
                    "expected_content": "before\n",
                    "replacement_content": "after\n",
                },
            ),
            ChatResponse(text="Edit complete."),
            ChatResponse(text="Edit complete."),
        ]
    )

    progress = []
    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Update the documentation.",
        tool_approval_handler=approve,
        progress_event_observer=progress.append,
    )

    assert result.validation_runs[0].tool_name == "run_ruff_format"
    assert result.validation_runs[0].skipped is True
    assert result.validation_runs[0].target_paths == ()
    assert "run_ruff_format" not in result.executed_tool_names
    assert tuple(run.tool_name for run in result.validation_runs[1:]) == (
        "run_ruff_check",
        "run_pytest",
    )
    assert result.validation_succeeded is True
    format_event = next(
        event for event in progress if event.tool_name == "run_ruff_format"
    )
    assert format_event.skipped is True
    assert format_event.validation_summary is None


def test_failed_controlled_action_emits_bounded_typed_progress(
    tmp_path: Path,
) -> None:
    """Report one safe action failure without parsing assistant prose."""

    repository = create_coding_repository(tmp_path / "project")
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            tool_response(
                "stale",
                "apply_text_replacement",
                {
                    "path": "module.py",
                    "expected_text": "missing text",
                    "replacement_text": "replacement",
                    "expected_file_sha256": "0" * 64,
                },
            ),
            ChatResponse(text="I claim this worked."),
            ChatResponse(text="Still complete."),
            ChatResponse(text="No change needed."),
        ]
    )
    progress: list[CodingProgressEvent] = []

    with pytest.raises(CompletionError):
        run_autonomous_coding_task(
            create_session(repository, provider),
            "Attempt a stale edit.",
            tool_approval_handler=approve,
            progress_event_observer=progress.append,
        )

    failure = next(
        event for event in progress if event.kind is CodingProgressKind.ACTION_FAILED
    )
    assert failure.phase is CodingPhase.EDIT
    assert failure.path == "module.py"
    assert failure.reason == (
        "Approval preview failed for apply_text_replacement: "
        "apply_text_replacement expected_file_sha256 does not match the current file."
    )
    assert failure.later_action_rejected is False
    terminal = progress[-1]
    assert terminal.kind is CodingProgressKind.TERMINAL_FAILURE
    assert terminal.workspace_preserved is True
    assert terminal.repair_attempt == 0
    assert (
        sum(
            event.kind is CodingProgressKind.PHASE_STARTED
            and event.phase is CodingPhase.EDIT
            for event in progress
        )
        == 1
    )


def test_stale_repeated_patch_after_success_is_rejected_as_a_later_action(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Apply once and distinguish a later stale repeat without reverting it."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    corrected = "def add(left: int, right: int) -> int:\n    return left + right\n"
    arguments = {
        "path": "module.py",
        "expected_content": original,
        "replacement_content": corrected,
    }
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            tool_response("ollama-tool-call-1", "apply_file_patch", arguments),
            tool_response("ollama-tool-call-1", "apply_file_patch", arguments),
            ChatResponse(text="Edit complete."),
            ChatResponse(text="Edit complete."),
        ]
    )
    replacements: list[str] = []
    original_replace = workspace_actions._replace_file_atomically

    def record_replace(workspace, patch):
        replacements.append(patch.relative_path)
        original_replace(workspace, patch)

    monkeypatch.setattr(
        workspace_actions,
        "_replace_file_atomically",
        record_replace,
    )
    approvals = []

    def approve_once(request):
        approvals.append(request)
        return ToolApprovalDecision.APPROVE

    progress: list[CodingProgressEvent] = []
    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        tool_approval_handler=approve_once,
        progress_event_observer=progress.append,
    )

    assert replacements == ["module.py"]
    assert (
        sum(request.invocation.tool_name == "apply_file_patch" for request in approvals)
        == 1
    )
    assert (repository / "module.py").read_text(encoding="utf-8") == corrected
    changed = [
        event
        for event in progress
        if event.kind is CodingProgressKind.WORKSPACE_CHANGED
    ]
    failures = [
        event for event in progress if event.kind is CodingProgressKind.ACTION_FAILED
    ]
    assert [event.path for event in changed] == ["module.py"]
    assert len(failures) == 1
    assert failures[0].path == "module.py"
    assert failures[0].later_action_rejected is True
    assert failures[0].reason == (
        "Approval preview failed for apply_file_patch: "
        "apply_file_patch expected content does not match."
    )
    patch_results = [
        result
        for tool_name, result in zip(
            result.executed_tool_names,
            result.tool_results,
            strict=True,
        )
        if tool_name == "apply_file_patch"
    ]
    assert [patch_result.status for patch_result in patch_results] == [
        "success",
        "error",
    ]
    assert result.validation_succeeded is True
    assert result.final_phase is CodingPhase.DONE


def test_validation_failure_emits_repair_attempt_and_safe_pytest_summary(
    tmp_path: Path,
) -> None:
    """Expose controller-owned repair progress and bounded pytest counts."""

    repository = create_coding_repository(tmp_path / "project")
    original = (repository / "module.py").read_text(encoding="utf-8")
    multiplied = original.replace("left - right", "left * right")
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            replacement_response(
                "bad-edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left * right",
            ),
            ChatResponse(text="Edit complete."),
            ChatResponse(text="Edit complete."),
            replacement_response(
                "repair",
                expected_content=multiplied,
                expected_text="return left * right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="Repair complete."),
            ChatResponse(text="Repair complete."),
        ]
    )
    progress: list[CodingProgressEvent] = []
    traces: list[CodingModelSendTrace] = []

    run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        tool_approval_handler=approve,
        progress_event_observer=progress.append,
        model_send_trace_observer=traces.append,
    )

    pytest_events = [event for event in progress if event.tool_name == "run_pytest"]
    assert [event.validation_summary for event in pytest_events] == [
        "1 failed",
        "1 passed",
    ]
    repair = next(
        event for event in progress if event.kind is CodingProgressKind.REPAIR_STARTED
    )
    assert repair.phase is CodingPhase.REPAIR
    assert repair.repair_attempt == 1
    assert repair.max_repair_attempts == 2
    repair_traces = [trace for trace in traces if trace.phase is CodingPhase.REPAIR]
    assert repair_traces
    assert all(not trace.decision_mode for trace in repair_traces)


def test_formatter_created_unexpected_path_fails_before_done(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Fail immediately when mutable validation changes an unapproved path."""

    repository = create_coding_repository(tmp_path / "project")
    original = (repository / "module.py").read_text(encoding="utf-8")
    original_run_validation = validation_tools.run_validation

    def intrusive_validation(workspace, tool_name, arguments):
        result = original_run_validation(workspace, tool_name, arguments)
        if tool_name == "run_ruff_format":
            (repository / "unexpected.py").write_text("value = 1\n", encoding="utf-8")
        return result

    monkeypatch.setattr(validation_tools, "run_validation", intrusive_validation)
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            replacement_response(
                "edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="Edit complete."),
            ChatResponse(text="Edit complete."),
        ]
    )

    with pytest.raises(
        CompletionError,
        match=(
            r"phase VALIDATE: unexpected changed paths after run_ruff_format: "
            r"unexpected\.py"
        ),
    ):
        run_autonomous_coding_task(
            create_session(repository, provider),
            "Correct the add implementation.",
            tool_approval_handler=approve,
        )

    assert (repository / "unexpected.py").exists()


@pytest.mark.parametrize("unexpected_kind", ["tracked", "untracked"])
def test_final_unexpected_paths_are_rejected_before_done(
    tmp_path: Path,
    monkeypatch,
    unexpected_kind: str,
) -> None:
    """Reject unexpected final tracked and untracked paths after validation."""

    repository = create_coding_repository(tmp_path / "project")
    original = (repository / "module.py").read_text(encoding="utf-8")
    original_status = inspect_workspace_git_status
    status_calls = 0

    def intrusive_status(workspace, arguments):
        nonlocal status_calls
        status_calls += 1
        if status_calls == 5:
            if unexpected_kind == "tracked":
                (repository / "test_module.py").write_text(
                    "def test_unexpected() -> None:\n    assert True\n",
                    encoding="utf-8",
                )
            else:
                (repository / "unexpected.txt").write_text(
                    "preserve me\n",
                    encoding="utf-8",
                )
        return original_status(workspace, arguments)

    monkeypatch.setattr(
        "agent_workbench.git_tools.inspect_workspace_git_status",
        intrusive_status,
    )
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            replacement_response(
                "edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="Edit complete."),
            ChatResponse(text="Edit complete."),
        ]
    )
    expected_path = (
        "test_module.py" if unexpected_kind == "tracked" else "unexpected.txt"
    )

    with pytest.raises(
        CompletionError,
        match=rf"phase VERIFY: unexpected changed paths before DONE: {expected_path}",
    ):
        run_autonomous_coding_task(
            create_session(repository, provider),
            "Correct the add implementation.",
            tool_approval_handler=approve,
        )

    assert (repository / expected_path).exists()


def test_discovery_round_limit_advances_to_edit(tmp_path: Path) -> None:
    """Advance after four discovery rounds even when inspection would continue."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    provider = ScriptedProvider(
        [
            tool_response("read-module", "read_file", {"path": "module.py"}),
            tool_response(
                "read-test",
                "read_file",
                {"path": "test_module.py"},
            ),
            tool_response("list", "list_files", {"path": "."}),
            tool_response(
                "search",
                "search_text",
                {"query": "add", "path": "."},
            ),
            tool_response("fifth-inspection", "read_file", {"path": "module.py"}),
            replacement_response(
                "edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="Edit complete."),
            ChatResponse(text="Edit complete."),
        ]
    )

    session = create_session(repository, provider)
    result = run_autonomous_coding_task(
        session,
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    assert result.final_phase is CodingPhase.DONE
    assert result.executed_tool_names[:4] == (
        "read_file",
        "read_file",
        "list_files",
        "search_text",
    )
    assert "fifth-inspection" not in {
        result.invocation_id for result in result.tool_results
    }
    edit_prompt = provider.requests[5].messages[-1]["content"]
    assert "Current phase: EDIT" in edit_prompt
    assert "Observed tool rounds: 4" in edit_prompt
    assert "read_file | paths: module.py" in edit_prompt
    assert "read_file | paths: test_module.py" in edit_prompt
    assert "list_files | paths:" in edit_prompt
    assert "search_text | paths:" in edit_prompt
    assert all(
        "Current phase: DISCOVER" not in message["content"]
        for message in session.messages
    )


def test_discovery_evidence_and_summary_are_bounded_and_sanitized(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Exclude private, sensitive, and oversized discovery data from EDIT."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    oversized = "safe-prefix-" + ("x" * 10_000) + "-oversized-tail"

    def unsafe_read(_workspace, _arguments):
        return {
            "path": "module.py",
            "content": (
                "TOKEN=discovery-secret\n"
                "/home/private/repository/secret.py\n"
                f"{oversized}\n"
            ),
            "size_bytes": len(oversized),
            "sha256": "a" * 64,
        }

    monkeypatch.setattr(
        "agent_workbench.workspace_tools.read_workspace_file",
        unsafe_read,
    )
    provider = ScriptedProvider(
        [
            tool_response("read", "read_file", {"path": "module.py"}),
            ChatResponse(
                text=(
                    "Found module.py.\n"
                    "PASSWORD=discovery-summary-secret\n"
                    "Inspect /home/private/repository next.\n"
                    f"{oversized}"
                )
            ),
            replacement_response(
                "edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="Edit complete."),
            ChatResponse(text="Edit complete."),
        ]
    )

    run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    edit_prompt = provider.requests[2].messages[-1]["content"]
    assert "read_file | paths: module.py" in edit_prompt
    assert "size_bytes=" in edit_prompt
    assert "Discovery summary: Found module.py." in edit_prompt
    assert "[redacted sensitive content]" in edit_prompt
    assert "[absolute-path]" in edit_prompt
    assert "discovery-secret" not in edit_prompt
    assert "/home/private" not in edit_prompt
    assert "-oversized-tail" not in edit_prompt
    assert len(edit_prompt) < 10_000


def test_edit_completion_continuation_then_successful_change(
    tmp_path: Path,
) -> None:
    """Reject an early EDIT completion and continue until a real action succeeds."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            ChatResponse(text="The task is complete."),
            replacement_response(
                "edit-after-continuation",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="Now the implementation is corrected."),
            ChatResponse(text="Now the implementation is corrected."),
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    assert result.final_phase is CodingPhase.DONE
    assert result.completion_continuation_count == 2
    continuation = provider.requests[2].messages[-1]["content"]
    assert "Current phase: EDIT" in continuation
    assert "no successful new workspace change was observed" in continuation
    assert "Assistant prose is not evidence" in continuation


def test_read_only_edit_inspection_enters_decision_mode(
    tmp_path: Path,
) -> None:
    """Expose only workspace actions after an inspection-only EDIT call."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            tool_response("edit-read", "read_file", {"path": "module.py"}),
            ChatResponse(text="Inspection complete."),
            replacement_response(
                "decision-edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="Edit applied."),
            ChatResponse(text="No further changes are needed."),
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    assert result.final_phase is CodingPhase.DONE
    decision_tools = {tool.name for tool in provider.requests[3].tools}
    assert decision_tools == {
        "apply_file_patch",
        "apply_file_rewrite",
        "apply_line_range_replacement",
        "apply_text_replacement",
        "apply_workspace_changes",
    }
    assert "read_file" not in decision_tools
    assert (
        "Repository evidence has already been gathered"
        in provider.requests[3].messages[-1]["content"]
    )


def test_edit_decision_mode_persists_through_prose_and_still_bounds_completions(
    tmp_path: Path,
) -> None:
    """Keep read-only tools withheld until the existing continuation limit fails."""

    repository = create_coding_repository(tmp_path / "project")
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            tool_response("edit-read", "read_file", {"path": "module.py"}),
            ChatResponse(text="Inspection complete."),
            ChatResponse(text="I need more time."),
            ChatResponse(text="Still considering the change."),
        ]
    )

    with pytest.raises(CompletionError, match="completion continuation limit reached"):
        run_autonomous_coding_task(
            create_session(repository, provider),
            "Correct the add implementation.",
            tool_approval_handler=approve,
        )

    for request in provider.requests[4:]:
        request_tools = {tool.name for tool in request.tools}
        assert "read_file" not in request_tools
        assert "apply_text_replacement" in request_tools


def test_model_send_trace_reports_edit_scope_transitions(
    tmp_path: Path,
) -> None:
    """Trace discovery, decision-mode persistence, and normal confirmation scope."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            tool_response("edit-read", "read_file", {"path": "module.py"}),
            ChatResponse(text="Inspection complete."),
            ChatResponse(text="I need more time."),
            replacement_response(
                "decision-edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="Edit applied."),
            ChatResponse(text="No further changes are needed."),
        ]
    )
    traces: list[CodingModelSendTrace] = []

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        tool_approval_handler=approve,
        model_send_trace_observer=traces.append,
        limits=CodingWorkflowLimits(edit_completion_continuations=4),
    )

    assert result.final_phase is CodingPhase.DONE
    assert [trace.phase for trace in traces] == [
        CodingPhase.DISCOVER,
        CodingPhase.EDIT,
        CodingPhase.EDIT,
        CodingPhase.EDIT,
        CodingPhase.EDIT,
    ]
    assert traces[0].allowed_tool_names == (
        "inspect_git_diff",
        "inspect_git_status",
        "list_files",
        "read_file",
        "search_symbols",
        "search_text",
    )
    assert traces[1].decision_mode is False
    assert "read_file" in traces[1].allowed_tool_names
    assert traces[2].decision_mode is True
    assert "read_file" not in traces[2].allowed_tool_names
    assert traces[3].decision_mode is True
    assert "read_file" not in traces[3].allowed_tool_names
    assert traces[4].decision_mode is False
    assert "read_file" in traces[4].allowed_tool_names


def test_successful_edit_from_decision_mode_preserves_confirmation_flow(
    tmp_path: Path,
) -> None:
    """Allow a decision-mode action to proceed through normal confirmation."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            tool_response("edit-read", "read_file", {"path": "module.py"}),
            ChatResponse(text="Inspection complete."),
            replacement_response(
                "decision-edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="Edit applied."),
            ChatResponse(text="No further changes are needed."),
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    assert result.final_phase is CodingPhase.DONE
    assert result.workspace_change_applied is True
    assert result.completion_continuation_count == 2
    confirmation_tools = {tool.name for tool in provider.requests[5].tools}
    assert "read_file" in confirmation_tools


def test_successful_decision_mode_change_survives_exhaustion_before_confirmation(
    tmp_path: Path,
) -> None:
    """Restore read-only tools after an exhausted successful decision-mode call."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            tool_response("edit-read", "read_file", {"path": "module.py"}),
            ChatResponse(text="Inspection complete."),
            replacement_response(
                "decision-edit-exhausted",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            replacement_response(
                "decision-edit-after-success",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            replacement_response(
                "decision-edit-exhaustion-trigger",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="The change is complete."),
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider, max_tool_rounds=2),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    assert DEFAULT_EDIT_COMPLETION_CONTINUATIONS == 2
    assert result.final_phase is CodingPhase.DONE
    assert result.workspace_change_applied is True
    assert result.completion_continuation_count == 2
    decision_tools = {tool.name for tool in provider.requests[3].tools}
    confirmation_tools = {tool.name for tool in provider.requests[6].tools}
    assert "read_file" not in decision_tools
    assert "apply_text_replacement" in decision_tools
    assert "read_file" in confirmation_tools
    assert (repository / "module.py").read_text(encoding="utf-8") == original.replace(
        "return left - right",
        "return left + right",
    )


def test_failed_edit_from_decision_mode_restores_read_only_tools(
    tmp_path: Path,
) -> None:
    """Restore safe inspection after a stale decision-mode mutation attempt."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            tool_response("edit-read", "read_file", {"path": "module.py"}),
            ChatResponse(text="Inspection complete."),
            replacement_response(
                "stale-edit",
                expected_content="stale content",
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="The previous action failed; I will refresh the file."),
            replacement_response(
                "fresh-edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="Edit applied."),
            ChatResponse(text="No further changes are needed."),
        ]
    )
    traces: list[CodingModelSendTrace] = []

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        tool_approval_handler=approve,
        limits=CodingWorkflowLimits(edit_completion_continuations=3),
        model_send_trace_observer=traces.append,
    )

    assert result.final_phase is CodingPhase.DONE
    assert result.workspace_change_applied is True
    decision_tools = {tool.name for tool in provider.requests[3].tools}
    recovery_tools = {tool.name for tool in provider.requests[5].tools}
    assert "read_file" not in decision_tools
    assert "read_file" in recovery_tools
    assert "apply_text_replacement" in recovery_tools
    assert any(tool_result.status == "error" for tool_result in result.tool_results)
    edit_traces = [trace for trace in traces if trace.phase is CodingPhase.EDIT]
    failed_action_trace_index = next(
        index for index, trace in enumerate(edit_traces) if trace.decision_mode
    )
    failed_action_trace = edit_traces[failed_action_trace_index]
    recovery_trace = edit_traces[failed_action_trace_index + 1]
    assert failed_action_trace.allowed_tool_names == (
        "apply_file_patch",
        "apply_file_rewrite",
        "apply_line_range_replacement",
        "apply_text_replacement",
        "apply_workspace_changes",
    )
    assert recovery_trace.decision_mode is False
    assert recovery_trace.allowed_tool_names == (
        "apply_file_patch",
        "apply_file_rewrite",
        "apply_line_range_replacement",
        "apply_text_replacement",
        "apply_workspace_changes",
        "inspect_git_diff",
        "inspect_git_status",
        "list_files",
        "read_file",
        "search_symbols",
        "search_text",
    )


def test_normal_edit_successful_change_requires_completion_confirmation(
    tmp_path: Path,
) -> None:
    """A normal (non-exhausted) successful EDIT change must not validate yet."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            replacement_response(
                "normal-edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="Edit applied."),
            ChatResponse(text="Confirming no further changes are needed."),
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    # Exactly one bounded continuation is required after the successful
    # mutation, and only the mutation-free continuation confirms completion;
    # validation runs only after that confirmation.
    assert result.final_phase is CodingPhase.DONE
    assert result.completion_continuation_count == 1
    assert result.workspace_change_applied is True
    assert result.approved_workspace_paths == ("module.py",)
    assert result.validation_succeeded is True
    assert result.assistant_summary == "Confirming no further changes are needed."
    assert len(provider.requests) == 4


def test_normal_edit_completion_confirmation_prompt_preserves_task_context(
    tmp_path: Path,
) -> None:
    """The normal post-change confirmation must repeat objective and criteria."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    criteria = (
        "Preserve the public function signature.",
        "Correct the arithmetic behavior.",
    )
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            replacement_response(
                "normal-edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="Edit applied."),
            ChatResponse(text="Confirming no further changes are needed."),
        ]
    )

    run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        acceptance_criteria=criteria,
        tool_approval_handler=approve,
    )

    confirmation_prompt = provider.requests[-1].messages[-1]["content"]
    assert "Original objective:\nCorrect the add implementation." in confirmation_prompt
    assert "Preserve the public function signature." in confirmation_prompt
    assert "Correct the arithmetic behavior." in confirmation_prompt
    assert "already applied" in confirmation_prompt
    assert "not evidence that editing is complete" in confirmation_prompt
    assert "make another controlled workspace change now" in confirmation_prompt
    assert (
        "finish this response without inventing another workspace change"
        in confirmation_prompt
    )
    # This is the normal-completion path, not tool-round exhaustion.
    assert "exhausted its tool-round budget" not in confirmation_prompt


def test_failed_mutation_during_normal_post_change_confirmation_does_not_confirm(
    tmp_path: Path,
) -> None:
    """A failed action during the normal confirmation must not confirm completion."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            replacement_response(
                "normal-edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="Edit applied."),
            replacement_response(
                "stale-confirmation-attempt",
                expected_content="stale content that will never match\n",
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="I believe editing is already complete."),
        ]
    )

    with pytest.raises(
        CompletionError,
        match=(
            r"phase EDIT: completion continuation limit reached after no "
            r"successful new workspace change was observed.*"
            r"repair_attempts=0, completion_continuations=1"
        ),
    ):
        run_autonomous_coding_task(
            create_session(repository, provider),
            "Correct the add implementation.",
            tool_approval_handler=approve,
            limits=CodingWorkflowLimits(edit_completion_continuations=1),
        )

    # The earlier preserved successful change is never rolled back, even
    # though the later stale attempt failed to confirm completion.
    assert run_git(repository, "status", "--short").stdout == " M module.py\n"


def test_repeated_normal_post_change_completion_remains_bounded(
    tmp_path: Path,
) -> None:
    """Repeated unconfirmed normal successful changes must fail closed, not loop."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    fixed = "def add(left: int, right: int) -> int:\n    return left + right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            replacement_response(
                "edit-1",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="First edit applied."),
            replacement_response(
                "edit-2",
                expected_content=fixed,
                expected_text="return left + right",
                replacement_text="return left + right  # confirmed",
            ),
            ChatResponse(text="Second edit applied."),
        ]
    )

    with pytest.raises(
        CompletionError,
        match=(
            r"phase EDIT: completion continuation limit reached after a "
            r"successful workspace change was applied but has not yet been "
            r"confirmed complete.*repair_attempts=0, completion_continuations=1"
        ),
    ):
        run_autonomous_coding_task(
            create_session(repository, provider),
            "Correct the add implementation.",
            tool_approval_handler=approve,
            limits=CodingWorkflowLimits(edit_completion_continuations=1),
        )

    # Both preserved changes remain on disk; the phase failed closed instead
    # of looping forever or validating unconfirmed work.
    assert run_git(repository, "status", "--short").stdout == " M module.py\n"


def test_normal_repair_successful_change_requires_completion_confirmation(
    tmp_path: Path,
) -> None:
    """A normal (non-exhausted) successful REPAIR change must not revalidate yet."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    multiplied = "def add(left: int, right: int) -> int:\n    return left * right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            replacement_response(
                "bad-edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left * right",
            ),
            ChatResponse(text="Edit applied."),
            ChatResponse(text="Edit confirmed complete."),
            replacement_response(
                "normal-repair",
                expected_content=multiplied,
                expected_text="return left * right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="Repair applied."),
            ChatResponse(text="Repair confirmed complete."),
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    # Revalidation only happens once, after the repair's own bounded
    # confirmation; the successful repair mutation alone does not trigger it.
    pytest_runs = [
        run.exit_code for run in result.validation_runs if run.tool_name == "run_pytest"
    ]
    assert pytest_runs == [1, 0]
    assert result.repair_attempt_count == 1
    assert result.completion_continuation_count == 2
    assert result.final_phase is CodingPhase.DONE


def test_stale_patch_failure_reaches_next_edit_prompt_and_clears_after_rewrite(
    tmp_path: Path,
) -> None:
    """Carry safe stale evidence once, then clear it after a successful rewrite."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    multiplied = "def add(left: int, right: int) -> int:\n    return left * right\n"
    corrected = "def add(left: int, right: int) -> int:\n    return left + right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            tool_response(
                "stale-patch",
                "apply_file_patch",
                {
                    "path": "module.py",
                    "expected_content": (
                        "STALE-EXPECTED /home/private/expected.py SECRET-CONTENT"
                    ),
                    "replacement_content": (
                        "REPLACEMENT /home/private/replacement.py SECRET-CONTENT"
                    ),
                },
            ),
            ChatResponse(text="The patch could not be applied."),
            rewrite_response(
                "rewrite",
                expected_content=original,
                replacement_content=multiplied,
            ),
            ChatResponse(text="Rewrite applied."),
            ChatResponse(text="Rewrite applied."),
            rewrite_response(
                "repair",
                expected_content=multiplied,
                replacement_content=corrected,
            ),
            ChatResponse(text="Repair applied."),
            ChatResponse(text="Repair applied."),
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    continuation = provider.requests[3].messages[-1]["content"]
    assert "Action failure evidence:" in continuation
    assert "tool=apply_file_patch" in continuation
    assert "path=module.py" in continuation
    assert "phase=EDIT" in continuation
    assert "attempt=1" in continuation
    assert "expected content does not match" in continuation
    assert "The previous action did not change the workspace." in continuation
    assert "call read_file for the complete file" in continuation
    assert "partial line-range read" in continuation
    assert "SHA from that complete latest read" in continuation
    assert "replacement_content must contain the complete resulting file" in (
        continuation
    )
    assert "apply_file_rewrite" in continuation
    assert "short literal snippet copied exactly from the latest file" in continuation
    assert "STALE-EXPECTED" not in continuation
    assert "REPLACEMENT" not in continuation
    assert "SECRET-CONTENT" not in continuation
    assert "/home/private" not in continuation

    repair_prompts = list(
        dict.fromkeys(
            request.messages[-1]["content"]
            for request in provider.requests
            if "Current phase: REPAIR" in request.messages[-1]["content"]
        )
    )
    assert repair_prompts
    assert all(
        "expected content does not match" not in prompt for prompt in repair_prompts
    )
    assert result.final_phase is CodingPhase.DONE


def test_failed_text_replacement_reaches_next_repair_prompt(
    tmp_path: Path,
) -> None:
    """Carry one failed exact replacement into a later REPAIR send."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    multiplied = "def add(left: int, right: int) -> int:\n    return left * right\n"
    corrected = "def add(left: int, right: int) -> int:\n    return left + right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            rewrite_response(
                "bad-edit",
                expected_content=original,
                replacement_content=multiplied,
            ),
            ChatResponse(text="Initial edit applied."),
            ChatResponse(text="Initial edit applied."),
            replacement_response(
                "failed-replacement",
                expected_content=multiplied,
                expected_text="not present",
                replacement_text="SECRET-REPLACEMENT",
            ),
            ChatResponse(text="Exact replacement failed."),
            rewrite_response(
                "repair",
                expected_content=multiplied,
                replacement_content=corrected,
            ),
            ChatResponse(text="Repair applied."),
            ChatResponse(text="Repair applied."),
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    prompts = [
        request.messages[-1]["content"]
        for request in provider.requests
        if "Current phase: REPAIR" in request.messages[-1]["content"]
    ]
    continuation = next(
        prompt for prompt in prompts if "Action failure evidence:" in prompt
    )
    assert "tool=apply_text_replacement" in continuation
    assert "path=module.py" in continuation
    assert "phase=REPAIR" in continuation
    assert "attempt=1" in continuation
    assert "expected 1 occurrence(s) but found 0" in continuation
    assert "not present" not in continuation
    assert "SECRET-REPLACEMENT" not in continuation
    assert result.final_phase is CodingPhase.DONE


def test_action_failure_evidence_uses_conservative_generic_sanitizer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Keep credential and token values out of an EDIT continuation."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    corrected = "def add(left: int, right: int) -> int:\n    return left + right\n"
    original_create_approval_request = ToolRegistry.create_approval_request

    def create_approval_request(registry, invocation):
        if invocation.id == "credential-failure":
            raise CompletionError(
                "OPENAI_API_KEY=edit-credential-value\n"
                'REPAIR_TOKEN = "generic-action-value"'
            )
        return original_create_approval_request(registry, invocation)

    monkeypatch.setattr(
        ToolRegistry,
        "create_approval_request",
        create_approval_request,
    )
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            tool_response(
                "credential-failure",
                "apply_file_patch",
                {
                    "path": "module.py",
                    "expected_content": original,
                    "replacement_content": corrected,
                },
            ),
            ChatResponse(text="The action failed."),
            rewrite_response(
                "rewrite",
                expected_content=original,
                replacement_content=corrected,
            ),
            ChatResponse(text="Rewrite applied."),
            ChatResponse(text="Rewrite applied."),
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    continuation = provider.requests[3].messages[-1]["content"]
    assert "Action failure evidence:" in continuation
    assert "edit-credential-value" not in continuation
    assert "generic-action-value" not in continuation
    assert "[redacted sensitive content]" in continuation
    assert result.final_phase is CodingPhase.DONE


def test_repeated_action_failures_are_sanitized_and_bounded(
    tmp_path: Path,
) -> None:
    """Bound repeated evidence by item, per-item, and combined named limits."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    corrected = "def add(left: int, right: int) -> int:\n    return left + right\n"
    failures = [
        tool_response(
            f"failure-{index}",
            "apply_file_patch",
            {
                "path": (
                    "module.py" if index % 2 == 0 else "/home/private/SECRET-CONTENT.py"
                ),
                "expected_content": f"EXPECTED-SECRET-{index}",
                "replacement_content": f"REPLACEMENT-SECRET-{index}",
            },
        )
        for index in range(MAX_ACTION_FAILURE_EVIDENCE_ITEMS)
    ]
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            *failures,
            tool_response(
                "round-limit-trigger",
                "apply_file_patch",
                {
                    "path": "module.py",
                    "expected_content": "STALE",
                    "replacement_content": "SECRET",
                },
            ),
            rewrite_response(
                "rewrite",
                expected_content=original,
                replacement_content=corrected,
            ),
            ChatResponse(text="Rewrite applied."),
            ChatResponse(text="Rewrite applied."),
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    continuation = provider.requests[-3].messages[-1]["content"]
    evidence_block = continuation.split("Action failure evidence:\n", 1)[1].split(
        "\n\n", 1
    )[0]
    evidence_lines = evidence_block.splitlines()
    assert len(evidence_lines) <= MAX_ACTION_FAILURE_EVIDENCE_ITEMS
    assert all(
        len(line.removeprefix(f"{index}. "))
        <= (MAX_ACTION_FAILURE_EVIDENCE_ITEM_CHARACTERS)
        for index, line in enumerate(evidence_lines, start=1)
    )
    assert (
        sum(
            len(line.removeprefix(f"{index}. "))
            for index, line in enumerate(evidence_lines, start=1)
        )
        <= MAX_ACTION_FAILURE_EVIDENCE_CHARACTERS
    )
    assert "/home/private" not in continuation
    assert "EXPECTED-SECRET" not in continuation
    assert "REPLACEMENT-SECRET" not in continuation
    assert result.final_phase is CodingPhase.DONE


def test_unrelated_tool_error_is_not_action_failure_evidence(tmp_path: Path) -> None:
    """Do not classify a failed inspection as a controlled-action failure."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    corrected = "def add(left: int, right: int) -> int:\n    return left + right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            tool_response("missing-read", "read_file", {"path": "missing.py"}),
            ChatResponse(text="Inspection failed."),
            rewrite_response(
                "rewrite",
                expected_content=original,
                replacement_content=corrected,
            ),
            ChatResponse(text="Rewrite applied."),
            ChatResponse(text="Rewrite applied."),
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    continuation = provider.requests[3].messages[-1]["content"]
    assert "Action failure evidence:" not in continuation
    assert "tool=read_file" not in continuation
    assert result.final_phase is CodingPhase.DONE


def test_edit_round_exhaustion_continues_then_changes_file(
    tmp_path: Path,
) -> None:
    """Consume one EDIT continuation after an unsuccessful action round."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            replacement_response(
                "invalid-edit",
                expected_content="stale content\n",
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            replacement_response(
                "unexecuted-edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            replacement_response(
                "edit-after-exhaustion",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="Edit complete."),
            ChatResponse(text="Edit complete."),
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider, max_tool_rounds=1),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    assert result.final_phase is CodingPhase.DONE
    assert result.completion_continuation_count == 2
    continuation = provider.requests[3].messages[-1]["content"]
    assert "Current phase: EDIT" in continuation
    assert "exhausted its tool-round budget" in continuation
    assert "without completing the required workspace change" in continuation


def test_repair_round_exhaustion_continues_then_repairs(
    tmp_path: Path,
) -> None:
    """Consume one REPAIR continuation after an unsuccessful action round."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    multiplied = "def add(left: int, right: int) -> int:\n    return left * right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            replacement_response(
                "bad-edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left * right",
            ),
            ChatResponse(text="Bad edit complete."),
            ChatResponse(text="Bad edit complete."),
            replacement_response(
                "invalid-repair",
                expected_content="stale content\n",
                expected_text="return left * right",
                replacement_text="return left + right",
            ),
            replacement_response(
                "unexecuted-repair",
                expected_content=multiplied,
                expected_text="return left * right",
                replacement_text="return left + right",
            ),
            replacement_response(
                "repair-after-exhaustion",
                expected_content=multiplied,
                expected_text="return left * right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="Repair complete."),
            ChatResponse(text="Repair complete."),
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider, max_tool_rounds=1),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    assert result.final_phase is CodingPhase.DONE
    assert result.repair_attempt_count == 1
    assert result.completion_continuation_count == 3
    continuation = provider.requests[6].messages[-1]["content"]
    assert "Current phase: REPAIR" in continuation
    assert "exhausted its tool-round budget" in continuation


def test_edit_round_exhaustion_after_change_requires_continuation(
    tmp_path: Path,
) -> None:
    """Do not treat exhaustion after a successful EDIT change as complete."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            replacement_response(
                "edit-before-exhaustion",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            replacement_response(
                "unexecuted-edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="Edit confirmed complete."),
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider, max_tool_rounds=1),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    # The preserved change only reaches DONE after the bounded continuation
    # confirms completion; it must not validate immediately on exhaustion.
    assert result.final_phase is CodingPhase.DONE
    assert result.completion_continuation_count == 1
    assert result.approved_workspace_paths == ("module.py",)
    assert result.assistant_summary == "Edit confirmed complete."

    continuation = provider.requests[-1].messages[-1]["content"]
    assert "Current phase: EDIT" in continuation
    assert "exhausted its tool-round budget" in continuation
    assert "preserved" in continuation
    assert "not evidence that editing is complete" in continuation
    assert "Acceptance criteria:" in continuation
    assert "Correct the add implementation." in continuation


def test_edit_round_exhaustion_continuation_applies_additional_change(
    tmp_path: Path,
) -> None:
    """Preserve and combine an additional change made during the continuation."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    original_test_module = (
        "from module import add\n"
        "\n"
        "\n"
        "def test_add() -> None:\n"
        "    assert add(1, 2) == 3\n"
    )
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            replacement_response(
                "edit-before-exhaustion",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            replacement_response(
                "unexecuted-edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            tool_response(
                "second-change",
                "apply_text_replacement",
                {
                    "path": "test_module.py",
                    "expected_text": "from module import add\n",
                    "replacement_text": (
                        "from module import add\n# Regression coverage note.\n"
                    ),
                    "expected_file_sha256": hashlib.sha256(
                        original_test_module.encode("utf-8")
                    ).hexdigest(),
                },
            ),
            ChatResponse(text="Both changes complete."),
            ChatResponse(text="Both changes complete."),
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider, max_tool_rounds=1),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    assert result.final_phase is CodingPhase.DONE
    assert result.completion_continuation_count == 2
    assert set(result.approved_workspace_paths) == {"module.py", "test_module.py"}
    assert result.validation_succeeded is True
    assert {
        tool_result.output.get("path")
        for tool_name, tool_result in zip(
            result.executed_tool_names, result.tool_results, strict=True
        )
        if tool_name == "run_ruff_format" and isinstance(tool_result.output, dict)
    } == {"module.py", "test_module.py"}


def test_repeated_edit_round_exhaustion_with_changes_fails_closed(
    tmp_path: Path,
) -> None:
    """Bound repeated tool-round exhaustion even when each call changes files."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    fixed = "def add(left: int, right: int) -> int:\n    return left + right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            replacement_response(
                "edit-1",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            replacement_response(
                "unexecuted-1",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            replacement_response(
                "edit-2",
                expected_content=fixed,
                expected_text="return left + right",
                replacement_text="return left + right  # confirmed",
            ),
            replacement_response(
                "unexecuted-2",
                expected_content=fixed,
                expected_text="return left + right",
                replacement_text="return left + right  # confirmed",
            ),
        ]
    )

    with pytest.raises(
        CompletionError,
        match=(
            r"phase EDIT: completion continuation limit reached after the "
            r"model-facing call exhausted its tool-round budget.*"
            r"repair_attempts=0, completion_continuations=1"
        ),
    ):
        run_autonomous_coding_task(
            create_session(repository, provider, max_tool_rounds=1),
            "Correct the add implementation.",
            tool_approval_handler=approve,
            limits=CodingWorkflowLimits(edit_completion_continuations=1),
        )

    # Both preserved changes remain on disk (never rolled back) even though
    # the phase failed closed instead of validating incomplete work.
    assert run_git(repository, "status", "--short").stdout == " M module.py\n"


def test_repair_round_exhaustion_after_change_requires_continuation(
    tmp_path: Path,
) -> None:
    """Do not treat exhaustion after a successful REPAIR change as complete."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    multiplied = "def add(left: int, right: int) -> int:\n    return left * right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            replacement_response(
                "bad-edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left * right",
            ),
            ChatResponse(text="Bad edit complete."),
            ChatResponse(text="Bad edit complete."),
            replacement_response(
                "repair-fix",
                expected_content=multiplied,
                expected_text="return left * right",
                replacement_text="return left + right",
            ),
            replacement_response(
                "unexecuted-repair",
                expected_content=multiplied,
                expected_text="return left * right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="Repair confirmed complete."),
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider, max_tool_rounds=1),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    assert result.final_phase is CodingPhase.DONE
    assert result.repair_attempt_count == 1
    assert result.completion_continuation_count == 2
    assert result.validation_succeeded is True
    assert result.assistant_summary == "Repair confirmed complete."

    continuation = provider.requests[-1].messages[-1]["content"]
    assert "Current phase: REPAIR" in continuation
    assert "exhausted its tool-round budget" in continuation
    assert "preserved" in continuation
    assert "not evidence that editing is complete" in continuation


def test_edit_post_exhaustion_continuation_with_failed_action_does_not_confirm(
    tmp_path: Path,
) -> None:
    """A failed mutation during the confirmation continuation must not confirm."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    fixed = "def add(left: int, right: int) -> int:\n    return left + right\n"
    original_test_module = (
        "from module import add\n"
        "\n"
        "\n"
        "def test_add() -> None:\n"
        "    assert add(1, 2) == 3\n"
    )
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            replacement_response(
                "edit-before-exhaustion",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            replacement_response(
                "unexecuted-edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            replacement_response(
                "stale-continuation-attempt",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="I believe editing is already complete."),
            tool_response(
                "second-change",
                "apply_text_replacement",
                {
                    "path": "test_module.py",
                    "expected_text": "from module import add\n",
                    "replacement_text": (
                        "from module import add\n# Regression coverage note.\n"
                    ),
                    "expected_file_sha256": hashlib.sha256(
                        original_test_module.encode("utf-8")
                    ).hexdigest(),
                },
            ),
            ChatResponse(text="Second change complete."),
            ChatResponse(text="Second change complete."),
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider, max_tool_rounds=1),
        "Correct the add implementation.",
        tool_approval_handler=approve,
        limits=CodingWorkflowLimits(edit_completion_continuations=3),
    )

    # The failed continuation attempt must not be mistaken for confirmation;
    # a further continuation and a real change were still required.
    assert result.final_phase is CodingPhase.DONE
    assert result.completion_continuation_count == 3
    assert set(result.approved_workspace_paths) == {"module.py", "test_module.py"}
    assert Path(repository / "module.py").read_text(encoding="utf-8") == fixed

    final_continuation = provider.requests[-1].messages[-1]["content"]
    assert "Action failure evidence:" in final_continuation
    assert "Acceptance criteria:" in final_continuation
    assert "Correct the add implementation." in final_continuation


def test_repeated_edit_post_exhaustion_failed_action_fails_closed(
    tmp_path: Path,
) -> None:
    """Bound a post-exhaustion continuation whose mutation attempt keeps failing."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            replacement_response(
                "edit-before-exhaustion",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            replacement_response(
                "unexecuted-edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            replacement_response(
                "stale-continuation-attempt",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="I believe editing is already complete."),
        ]
    )

    with pytest.raises(
        CompletionError,
        match=(
            r"phase EDIT: completion continuation limit reached after no "
            r"successful new workspace change was observed.*"
            r"repair_attempts=0, completion_continuations=1"
        ),
    ):
        run_autonomous_coding_task(
            create_session(repository, provider, max_tool_rounds=1),
            "Correct the add implementation.",
            tool_approval_handler=approve,
            limits=CodingWorkflowLimits(edit_completion_continuations=1),
        )

    # The originally preserved change remains on disk even though the phase
    # failed closed instead of validating on unconfirmed prose.
    assert run_git(repository, "status", "--short").stdout == " M module.py\n"


def test_repair_post_exhaustion_continuation_with_failed_action_does_not_confirm(
    tmp_path: Path,
) -> None:
    """A failed REPAIR mutation after exhaustion must not be mistaken for repair."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    multiplied = "def add(left: int, right: int) -> int:\n    return left * right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            replacement_response(
                "bad-edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left * right",
            ),
            ChatResponse(text="Bad edit complete."),
            ChatResponse(text="Bad edit complete."),
            replacement_response(
                "repair-fix",
                expected_content=multiplied,
                expected_text="return left * right",
                replacement_text="return left + right",
            ),
            replacement_response(
                "unexecuted-repair",
                expected_content=multiplied,
                expected_text="return left * right",
                replacement_text="return left + right",
            ),
            replacement_response(
                "stale-repair-continuation-attempt",
                expected_content=multiplied,
                expected_text="return left * right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="I believe the repair is already complete."),
        ]
    )

    with pytest.raises(
        CompletionError,
        match=(
            r"phase REPAIR: repair completed without a successful new "
            r"workspace change.*repair_attempts=1, completion_continuations=2"
        ),
    ):
        run_autonomous_coding_task(
            create_session(repository, provider, max_tool_rounds=1),
            "Correct the add implementation.",
            tool_approval_handler=approve,
            limits=CodingWorkflowLimits(
                repair_attempts=1,
                repair_completion_continuations=1,
            ),
        )


def test_edit_read_only_exhaustion_after_change_keeps_confirmation_pending(
    tmp_path: Path,
) -> None:
    """A read-only-only exhaustion must not discard a pending confirmation."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    fixed = "def add(left: int, right: int) -> int:\n    return left + right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            replacement_response(
                "edit-1",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            replacement_response(
                "stale-edit-2",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            tool_response(
                "blocked-attempt",
                "apply_text_replacement",
                {
                    "path": "module.py",
                    "expected_text": "return left + right",
                    "replacement_text": "return left + right",
                    "expected_file_sha256": hashlib.sha256(
                        fixed.encode("utf-8")
                    ).hexdigest(),
                },
            ),
            tool_response("bad-read-1", "read_file", {"path": "missing1.py"}),
            tool_response("bad-read-2", "read_file", {"path": "missing2.py"}),
            tool_response("bad-read-3", "read_file", {"path": "missing3.py"}),
            ChatResponse(text="Confirming completion after inspection."),
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider, max_tool_rounds=2),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    assert result.final_phase is CodingPhase.DONE
    assert result.completion_continuation_count == 2
    assert result.validation_succeeded is True
    assert Path(repository / "module.py").read_text(encoding="utf-8") == fixed
    assert result.assistant_summary == "Confirming completion after inspection."

    final_continuation = provider.requests[-1].messages[-1]["content"]
    assert "Acceptance criteria:" in final_continuation
    assert "not evidence that editing is complete" in final_continuation


def test_repeated_read_only_exhaustion_after_change_stops_at_continuation_limit(
    tmp_path: Path,
) -> None:
    """Bound repeated read-only exhaustion even while confirmation is pending."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    fixed = "def add(left: int, right: int) -> int:\n    return left + right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            replacement_response(
                "edit-1",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            replacement_response(
                "stale-edit-2",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            tool_response(
                "blocked-attempt",
                "apply_text_replacement",
                {
                    "path": "module.py",
                    "expected_text": "return left + right",
                    "replacement_text": "return left + right",
                    "expected_file_sha256": hashlib.sha256(
                        fixed.encode("utf-8")
                    ).hexdigest(),
                },
            ),
            tool_response("bad-read-1", "read_file", {"path": "missing1.py"}),
            tool_response("bad-read-2", "read_file", {"path": "missing2.py"}),
            tool_response("bad-read-3", "read_file", {"path": "missing3.py"}),
        ]
    )

    with pytest.raises(
        CompletionError,
        match=(
            r"phase EDIT: completion continuation limit reached after the "
            r"model-facing call exhausted its tool-round budget while only "
            r"performing read-only inspection.*"
            r"repair_attempts=0, completion_continuations=1"
        ),
    ):
        run_autonomous_coding_task(
            create_session(repository, provider, max_tool_rounds=2),
            "Correct the add implementation.",
            tool_approval_handler=approve,
            limits=CodingWorkflowLimits(edit_completion_continuations=1),
        )

    assert Path(repository / "module.py").read_text(encoding="utf-8") == fixed


def test_repeated_edit_round_exhaustion_stops_at_continuation_limit(
    tmp_path: Path,
) -> None:
    """Stop after two bounded EDIT continuations without a change."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            *(
                response
                for index in range(3)
                for response in (
                    replacement_response(
                        f"invalid-{index}",
                        expected_content="stale content\n",
                        expected_text="return left - right",
                        replacement_text="return left + right",
                    ),
                    replacement_response(
                        f"unexecuted-{index}",
                        expected_content=original,
                        expected_text="return left - right",
                        replacement_text="return left + right",
                    ),
                )
            ),
        ]
    )

    with pytest.raises(
        CompletionError,
        match=(
            r"phase EDIT: completion continuation limit reached after the "
            r"model-facing call exhausted its tool-round budget.*"
            r"repair_attempts=0, completion_continuations=2"
        ),
    ):
        run_autonomous_coding_task(
            create_session(repository, provider, max_tool_rounds=1),
            "Correct the add implementation.",
            tool_approval_handler=approve,
        )


def test_unrelated_model_phase_completion_error_remains_terminal(
    tmp_path: Path,
) -> None:
    """Do not convert unrelated completion failures into continuations."""

    repository = create_coding_repository(tmp_path / "project")
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            CompletionError("Unrelated provider failure."),
        ]
    )

    with pytest.raises(
        CompletionError,
        match=(
            r"phase EDIT: model-facing phase failed: Unrelated provider failure.*"
            r"completion_continuations=0"
        ),
    ):
        run_autonomous_coding_task(
            create_session(repository, provider, max_tool_rounds=1),
            "Correct the add implementation.",
            tool_approval_handler=approve,
        )


def test_failed_validation_enters_repair_and_revalidates(
    tmp_path: Path,
) -> None:
    """Repair one failing edit and rerun the complete validation sequence."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    multiplied = "def add(left: int, right: int) -> int:\n    return left * right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            replacement_response(
                "bad-edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left * right",
            ),
            ChatResponse(text="Applied the first edit."),
            ChatResponse(text="Applied the first edit."),
            replacement_response(
                "repair",
                expected_content=multiplied,
                expected_text="return left * right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="Repaired the failing implementation."),
            ChatResponse(text="Repaired the failing implementation."),
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        (
            "Correct the add implementation.\n"
            "Inspect /home/example/private if needed.\n"
            "TOKEN=must-not-enter-repair-evidence"
        ),
        tool_approval_handler=approve,
    )

    assert result.final_phase is CodingPhase.DONE
    assert result.repair_attempt_count == 1
    assert tuple(run.tool_name for run in result.validation_runs) == (
        "run_ruff_format",
        "run_ruff_check",
        "run_pytest",
        "run_ruff_format",
        "run_ruff_check",
        "run_pytest",
    )
    assert result.validation_runs[2].exit_code == 1
    assert result.validation_runs[-1].exit_code == 0

    repair_prompt = provider.requests[4].messages[-1]["content"]
    assert "Original objective:\nCorrect the add implementation." in repair_prompt
    assert "Inspect [absolute-path] if needed." in repair_prompt
    assert "[redacted sensitive content]" in repair_prompt
    assert "/home/example/private" not in repair_prompt
    assert "must-not-enter-repair-evidence" not in repair_prompt
    assert "Current phase: REPAIR" in repair_prompt
    assert "Repair attempt: 1/2" in repair_prompt
    assert "tool_name=run_pytest" in repair_prompt
    assert "result_status=success" in repair_prompt
    assert "exit_code=1" in repair_prompt
    assert "Current changed-file paths: module.py" in repair_prompt
    assert "requires another successful controlled workspace change" in repair_prompt


def test_repair_prompt_preserves_all_safe_failure_and_runtime_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Present every safe pytest assertion and dynamic runtime requirement."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    multiplied = "def add(left: int, right: int) -> int:\n    return left * right\n"
    corrected = "def add(left: int, right: int) -> int:\n    return left + right\n"
    dynamic_value = "RUNTIME7Q2M9"
    pytest_runs = 0

    def validation(_workspace, tool_name, _arguments):
        nonlocal pytest_runs
        if tool_name != "run_pytest":
            return {
                "exit_code": 0,
                "stdout": "",
                "stderr": "",
            }
        pytest_runs += 1
        if pytest_runs == 1:
            return {
                "exit_code": 1,
                "stdout": (
                    "FAILED test_first - AssertionError: expected 3, got 2\n"
                    "FAILED test_second - AssertionError: feature flag missing\n"
                    f'REPAIR_TOKEN = "{dynamic_value}"\n'
                    f"generated_token_identifier={dynamic_value}\n"
                    "dynamic runtime requirement: use the generated value above\n"
                    "status=failed OPENAI_API_KEY=mixed-credential-must-be-redacted\n"
                    "PASSWORD=credential-must-be-redacted\n"
                    "failure loaded from /home/private/project/test_module.py\n"
                ),
                "stderr": (
                    "safe stderr assertion detail\n"
                    "API_KEY=credential-must-also-be-redacted\n"
                    ".env contains private configuration\n"
                ),
            }
        return {
            "exit_code": 0,
            "stdout": "all repaired\n",
            "stderr": "",
        }

    monkeypatch.setattr(
        "agent_workbench.validation_tools.run_validation",
        validation,
    )
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            rewrite_response(
                "bad-edit",
                expected_content=original,
                replacement_content=multiplied,
            ),
            ChatResponse(text="Initial edit applied."),
            ChatResponse(text="Initial edit applied."),
            rewrite_response(
                "repair",
                expected_content=multiplied,
                replacement_content=corrected,
            ),
            ChatResponse(text="Repair applied."),
            ChatResponse(text="Repair applied."),
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    repair_prompt = next(
        request.messages[-1]["content"]
        for request in provider.requests
        if "Current phase: REPAIR" in request.messages[-1]["content"]
    )
    assert "Validation failure 1:" in repair_prompt
    assert "tool_name=run_pytest" in repair_prompt
    assert "result_status=success" in repair_prompt
    assert "exit_code=1" in repair_prompt
    assert "stdout_excerpt:" in repair_prompt
    assert "stderr_excerpt:" in repair_prompt
    assert "FAILED test_first - AssertionError: expected 3, got 2" in repair_prompt
    assert "FAILED test_second - AssertionError: feature flag missing" in repair_prompt
    assert f'REPAIR_TOKEN = "{dynamic_value}"' in repair_prompt
    assert f"generated_token_identifier={dynamic_value}" in repair_prompt
    assert "dynamic runtime requirement: use the generated value above" in repair_prompt
    assert "safe stderr assertion detail" in repair_prompt
    assert "mixed-credential-must-be-redacted" not in repair_prompt
    assert "status=failed OPENAI_API_KEY" not in repair_prompt
    assert "credential-must-be-redacted" not in repair_prompt
    assert "credential-must-also-be-redacted" not in repair_prompt
    assert ".env contains private configuration" not in repair_prompt
    assert "/home/private" not in repair_prompt
    assert "[redacted sensitive content]" in repair_prompt
    assert "resolve every listed validation failure" in repair_prompt
    assert "Do not ignore dynamic runtime requirements" in repair_prompt
    assert "Apply another successful controlled workspace change" in repair_prompt
    assert "Do not call Ruff or pytest directly" in repair_prompt
    assert "latest file SHA" in repair_prompt
    assert "apply_file_rewrite" in repair_prompt
    assert "call read_file for the complete file" in repair_prompt
    assert "partial line-range read" in repair_prompt
    assert "SHA from that complete latest read" in repair_prompt
    assert "replacement_content must contain the complete resulting file" in (
        repair_prompt
    )
    assert "controller will run the full validation sequence afterward" in repair_prompt
    assert "Current changed-file paths: module.py" in repair_prompt
    assert result.repair_attempt_count == 1
    assert result.final_phase is CodingPhase.DONE


@pytest.mark.parametrize(
    ("sensitive_line", "secret_value"),
    [
        ("API_KEY=api-key-value", "api-key-value"),
        ("OPENAI_API_KEY=openai-key-value", "openai-key-value"),
        ("AWS_ACCESS_KEY_ID=AKIATESTVALUE1234", "AKIATESTVALUE1234"),
        (
            "AWS_SECRET_ACCESS_KEY=aws-secret-access-value",
            "aws-secret-access-value",
        ),
        ("AWS_SESSION_TOKEN=aws-session-value", "aws-session-value"),
        ("PASSWORD=password-value", "password-value"),
        ("DB_PASSWORD=database-password-value", "database-password-value"),
        ("SECRET=secret-value", "secret-value"),
        ("ACCESS_TOKEN=access-token-value", "access-token-value"),
        ("AUTH_TOKEN=auth-token-value", "auth-token-value"),
        ("GITHUB_TOKEN=github-token-value", "github-token-value"),
        ('{"OPENAI_API_KEY": "json-credential-value"}', "json-credential-value"),
        ("DB_PASSWORD: yaml-credential-value", "yaml-credential-value"),
        ('client_secret = "toml-credential-value"', "toml-credential-value"),
        ("Authorization: Bearer authorization-value", "authorization-value"),
        (
            "Authorization Bearer alternate-authorization-value",
            "alternate-authorization-value",
        ),
        ("Bearer ghp_obviousBearerValue12345", "ghp_obviousBearerValue12345"),
        (".env contains dotenv-value", "dotenv-value"),
        (".env.local contains local-dotenv-value", "local-dotenv-value"),
        (".env.production contains production-dotenv-value", "production-dotenv-value"),
        ("config/.env.staging contains staging-dotenv-value", "staging-dotenv-value"),
    ],
)
def test_validation_output_sanitizer_redacts_credentials(
    sensitive_line: str,
    secret_value: str,
) -> None:
    """Redact common credential forms without exposing their values."""

    sanitized = _sanitize_validation_output(
        f"safe prefix\n{sensitive_line}\nsafe suffix"
    )

    assert "safe prefix" in sanitized
    assert "safe suffix" in sanitized
    assert secret_value not in sanitized
    assert "[redacted sensitive content]" in sanitized


@pytest.mark.parametrize(
    ("sensitive_line", "secret_value"),
    [
        ("status=failed OPENAI_API_KEY=openai-secret", "openai-secret"),
        (
            "generated_token_identifier=RUNTIME7Q2M9 DB_PASSWORD=db-secret",
            "db-secret",
        ),
        ('{"status":"failed","GITHUB_TOKEN":"github-secret"}', "github-secret"),
        ('{"safe":"value","AWS_SESSION_TOKEN":"aws-secret"}', "aws-secret"),
        ("note=safe, AWS_SECRET_ACCESS_KEY=aws-secret", "aws-secret"),
        ("safe=true AUTH_TOKEN=auth-secret", "auth-secret"),
        ("safe: true, Authorization: Bearer bearer-secret", "bearer-secret"),
    ],
)
def test_validation_output_sanitizer_redacts_mixed_assignments(
    sensitive_line: str,
    secret_value: str,
) -> None:
    """Inspect later assignments even when a safe field appears first."""

    first = _sanitize_validation_output(sensitive_line)
    second = _sanitize_validation_output(sensitive_line)

    assert first == second
    assert first == "[redacted sensitive content]"
    assert secret_value not in first


def test_validation_output_sanitizer_preserves_safe_dynamic_requirements() -> None:
    """Keep unrelated token identifiers and runtime values exact."""

    output = (
        'REPAIR_TOKEN = "RUNTIME7Q2M9"\n'
        "generated_token_identifier=RUNTIME7Q2M9\n"
        "dynamic runtime requirement: use RUNTIME7Q2M9"
    )

    assert _sanitize_validation_output(output) == output


def test_validation_output_sanitizer_redacts_absolute_private_paths() -> None:
    """Keep the existing absolute-path boundary in validation output."""

    sanitized = _sanitize_validation_output(
        "failure at /home/private/project/test_module.py:12"
    )

    assert "/home/private" not in sanitized
    assert "[absolute-path]" in sanitized


def test_generic_prompt_sanitizer_remains_conservative_for_token_lines() -> None:
    """Do not use validation's permissive token handling for generic evidence."""

    sanitized = _sanitize_prompt_text(
        'REPAIR_TOKEN = "GENERIC-MUST-BE-REDACTED"\n'
        "generated_token_identifier=GENERIC-MUST-ALSO-BE-REDACTED"
    )

    assert "GENERIC-MUST-BE-REDACTED" not in sanitized
    assert "GENERIC-MUST-ALSO-BE-REDACTED" not in sanitized
    assert sanitized.splitlines() == [
        "[redacted sensitive content]",
        "[redacted sensitive content]",
    ]


def test_repair_validation_output_is_deterministically_bounded(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Apply named per-field and combined bounds without dropping metadata."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    multiplied = "def add(left: int, right: int) -> int:\n    return left * right\n"
    corrected = "def add(left: int, right: int) -> int:\n    return left + right\n"
    pytest_runs = 0
    oversized_stdout = "STDOUT-START\n" + ("x" * 20_000) + "\nSTDOUT-TAIL"
    oversized_stderr = "STDERR-START\n" + ("y" * 20_000) + "\nSTDERR-TAIL"

    def validation(_workspace, tool_name, _arguments):
        nonlocal pytest_runs
        if tool_name != "run_pytest":
            return {"exit_code": 0, "stdout": "", "stderr": ""}
        pytest_runs += 1
        if pytest_runs == 1:
            return {
                "exit_code": 1,
                "stdout": oversized_stdout,
                "stderr": oversized_stderr,
            }
        return {"exit_code": 0, "stdout": "passed\n", "stderr": ""}

    monkeypatch.setattr(
        "agent_workbench.validation_tools.run_validation",
        validation,
    )
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            rewrite_response(
                "bad-edit",
                expected_content=original,
                replacement_content=multiplied,
            ),
            ChatResponse(text="Initial edit applied."),
            ChatResponse(text="Initial edit applied."),
            ChatResponse(text="Repair is not complete."),
            rewrite_response(
                "repair",
                expected_content=multiplied,
                replacement_content=corrected,
            ),
            ChatResponse(text="Repair applied."),
            ChatResponse(text="Repair applied."),
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    repair_prompts = list(
        dict.fromkeys(
            request.messages[-1]["content"]
            for request in provider.requests
            if "Current phase: REPAIR" in request.messages[-1]["content"]
        )
    )
    assert len(repair_prompts) == 3
    evidence_blocks = [
        prompt.split("Failed validation evidence:\n", 1)[1].split(
            "\nCurrent changed-file paths:",
            1,
        )[0]
        for prompt in repair_prompts
    ]
    assert evidence_blocks[0] == evidence_blocks[1] == evidence_blocks[2]
    assert len(evidence_blocks[0]) <= MAX_REPAIR_VALIDATION_EVIDENCE_CHARACTERS
    assert evidence_blocks[0].count("[truncated]") == 2
    assert "STDOUT-START" in evidence_blocks[0]
    assert "STDERR-START" in evidence_blocks[0]
    assert "STDOUT-TAIL" not in evidence_blocks[0]
    assert "STDERR-TAIL" not in evidence_blocks[0]
    stdout_excerpt = (
        evidence_blocks[0]
        .split("stdout_excerpt:\n", 1)[1]
        .split(
            "\nstderr_excerpt:",
            1,
        )[0]
    )
    stderr_excerpt = evidence_blocks[0].split("stderr_excerpt:\n", 1)[1]
    assert len(stdout_excerpt) <= MAX_REPAIR_VALIDATION_FIELD_CHARACTERS
    assert len(stderr_excerpt) <= MAX_REPAIR_VALIDATION_FIELD_CHARACTERS
    assert result.final_phase is CodingPhase.DONE


@pytest.mark.parametrize(
    "output",
    [
        pytest.param(
            {"exit_code": 1, "stdout": "failure", "stderr": ""},
            id="missing",
        ),
        pytest.param(
            {
                "exit_code": 1,
                "stdout": "failure",
                "stderr": "",
                "command": None,
            },
            id="none",
        ),
        pytest.param(
            {
                "exit_code": 1,
                "stdout": "failure",
                "stderr": "",
                "command": [],
            },
            id="empty-list",
        ),
        pytest.param(
            {
                "exit_code": 1,
                "stdout": "failure",
                "stderr": "",
                "command": "python -m pytest",
            },
            id="string",
        ),
        pytest.param(
            {
                "exit_code": 1,
                "stdout": "failure",
                "stderr": "",
                "command": ["python", 3],
            },
            id="mixed-list",
        ),
        pytest.param(
            {
                "exit_code": 1,
                "stdout": "failure",
                "stderr": "",
                "command": {"program": "python"},
            },
            id="object",
        ),
    ],
)
def test_validation_failure_command_falls_back_when_unavailable_or_malformed(
    output: dict[str, object],
) -> None:
    """Render one deterministic fallback for unavailable command data."""

    result = ToolResult(
        invocation_id="validation",
        status="success",
        output=output,
    )

    rendered = _format_validation_failure_evidence(
        _bounded_validation_failure_evidence((("run_pytest", result),))
    )

    assert "result_status=success" in rendered
    assert "command=[unavailable]" in rendered
    assert "exit_code=1" in rendered
    assert "stdout_excerpt:\nfailure" in rendered
    assert "stderr_excerpt:\n[empty]" in rendered


def test_validation_failure_commands_render_as_ordered_compact_json() -> None:
    """Preserve validation ordering and render commands without shell quoting."""

    failures = (
        (
            "run_ruff_check",
            ToolResult(
                invocation_id="ruff",
                status="success",
                output={
                    "command": [
                        "python",
                        "-m",
                        "ruff",
                        "check",
                        "--no-cache",
                        "--color",
                        "never",
                        ".",
                    ],
                    "exit_code": 1,
                    "stdout": "module.py:1:1: F401 unused import",
                    "stderr": "",
                },
            ),
        ),
        (
            "run_pytest",
            ToolResult(
                invocation_id="pytest",
                status="success",
                output={
                    "command": [
                        "python",
                        "-m",
                        "pytest",
                        "-q",
                        "--color=no",
                        "-p",
                        "no:cacheprovider",
                        ".",
                    ],
                    "exit_code": 1,
                    "stdout": "FAILED test_module.py::test_add",
                    "stderr": "AssertionError: expected 3",
                },
            ),
        ),
    )

    rendered = _format_validation_failure_evidence(
        _bounded_validation_failure_evidence(failures)
    )

    assert rendered == (
        "Validation failure 1:\n"
        "tool_name=run_ruff_check\n"
        "result_status=success\n"
        'command=["python","-m","ruff","check","--no-cache","--color",'
        '"never","."]\n'
        "exit_code=1\n"
        "stdout_excerpt:\n"
        "module.py:1:1: F401 unused import\n"
        "stderr_excerpt:\n"
        "[empty]\n"
        "\n"
        "Validation failure 2:\n"
        "tool_name=run_pytest\n"
        "result_status=success\n"
        'command=["python","-m","pytest","-q","--color=no","-p",'
        '"no:cacheprovider","."]\n'
        "exit_code=1\n"
        "stdout_excerpt:\n"
        "FAILED test_module.py::test_add\n"
        "stderr_excerpt:\n"
        "AssertionError: expected 3"
    )


def test_validation_failure_command_counts_toward_existing_evidence_budget() -> None:
    """Keep command metadata and streams within the combined repair budget."""

    result = ToolResult(
        invocation_id="validation",
        status="success",
        output={
            "command": [
                "python",
                "-m",
                "pytest",
                "-q",
                "--color=no",
            ],
            "exit_code": 1,
            "stdout": "STDOUT-START\n" + ("x" * 20_000),
            "stderr": "STDERR-START\n" + ("y" * 20_000),
        },
    )

    rendered = _format_validation_failure_evidence(
        _bounded_validation_failure_evidence((("run_pytest", result),))
    )

    assert len(rendered) <= MAX_REPAIR_VALIDATION_EVIDENCE_CHARACTERS
    assert 'command=["python","-m","pytest","-q","--color=no"]' in rendered
    assert rendered.count("[truncated]") == 2
    assert "STDOUT-START" in rendered
    assert "STDERR-START" in rendered


def test_oversized_validation_command_cannot_exceed_evidence_budget() -> None:
    """Reject an oversized command without hiding a later bounded command."""

    oversized_result = ToolResult(
        invocation_id="oversized-command",
        status="success",
        output={
            "command": ["python", "x" * 20_000],
            "exit_code": 1,
            "stdout": "ruff failure",
            "stderr": "",
        },
    )
    bounded_result = ToolResult(
        invocation_id="bounded-command",
        status="success",
        output={
            "command": ["python", "-m", "pytest", "-q"],
            "exit_code": 1,
            "stdout": "pytest failure",
            "stderr": "AssertionError",
        },
    )

    rendered = _format_validation_failure_evidence(
        _bounded_validation_failure_evidence(
            (
                ("run_ruff_check", oversized_result),
                ("run_pytest", bounded_result),
            )
        )
    )

    records = rendered.split("\n\n")

    assert len(rendered) <= MAX_REPAIR_VALIDATION_EVIDENCE_CHARACTERS
    assert len(records) == 2

    assert "tool_name=run_ruff_check" in records[0]
    assert "command=[unavailable]" in records[0]
    assert "ruff failure" in records[0]
    assert ("x" * 100) not in records[0]

    assert "tool_name=run_pytest" in records[1]
    assert 'command=["python","-m","pytest","-q"]' in records[1]
    assert "pytest failure" in records[1]
    assert "AssertionError" in records[1]


def test_second_repair_resolves_every_failure_and_reaches_done(
    tmp_path: Path,
) -> None:
    """Revalidate after an incomplete first repair and accept a complete second."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    multiplied = "def add(left: int, right: int) -> int:\n    return left * right\n"
    divided = "def add(left: int, right: int) -> int:\n    return left / right\n"
    corrected = "def add(left: int, right: int) -> int:\n    return left + right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            rewrite_response(
                "bad-edit",
                expected_content=original,
                replacement_content=multiplied,
            ),
            ChatResponse(text="Initial edit applied."),
            ChatResponse(text="Initial edit applied."),
            rewrite_response(
                "incomplete-repair",
                expected_content=multiplied,
                replacement_content=divided,
            ),
            ChatResponse(text="First repair applied."),
            ChatResponse(text="First repair applied."),
            rewrite_response(
                "complete-repair",
                expected_content=divided,
                replacement_content=corrected,
            ),
            ChatResponse(text="Second repair applied."),
            ChatResponse(text="Second repair applied."),
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    pytest_runs = [
        run.exit_code for run in result.validation_runs if run.tool_name == "run_pytest"
    ]
    assert pytest_runs == [1, 1, 0]
    assert result.repair_attempt_count == 2
    assert result.final_phase is CodingPhase.DONE
    assert (repository / "module.py").read_text(encoding="utf-8") == corrected


def test_repeated_validation_failure_stops_at_repair_limit(
    tmp_path: Path,
) -> None:
    """Raise a phase-specific failure after two unsuccessful repairs."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    multiplied = "def add(left: int, right: int) -> int:\n    return left * right\n"
    divided = "def add(left: int, right: int) -> int:\n    return left / right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            replacement_response(
                "bad-edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left * right",
            ),
            ChatResponse(text="First edit complete."),
            ChatResponse(text="First edit complete."),
            replacement_response(
                "repair-1",
                expected_content=multiplied,
                expected_text="return left * right",
                replacement_text="return left / right",
            ),
            ChatResponse(text="First repair complete."),
            ChatResponse(text="First repair complete."),
            replacement_response(
                "repair-2",
                expected_content=divided,
                expected_text="return left / right",
                replacement_text="return left // right",
            ),
            ChatResponse(text="Second repair complete."),
            ChatResponse(text="Second repair complete."),
        ]
    )

    with pytest.raises(
        CompletionError,
        match=(
            r"phase REPAIR: validation still failed.*"
            r"repair_attempts=2, completion_continuations=3"
        ),
    ):
        run_autonomous_coding_task(
            create_session(repository, provider),
            "Correct the add implementation.",
            tool_approval_handler=approve,
        )

    assert DEFAULT_MAX_REPAIR_ATTEMPTS == 2


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        (
            "apply_file_rewrite",
            {
                "path": "module.py",
                "expected_file_sha256": hashlib.sha256(
                    (
                        "def add(left: int, right: int) -> int:\n"
                        "    return left - right\n"
                    ).encode("utf-8")
                ).hexdigest(),
                "replacement_content": (
                    "def add(left: int, right: int) -> int:\n    return left + right\n"
                ),
            },
        ),
        (
            "apply_file_patch",
            {
                "path": "module.py",
                "expected_content": (
                    "def add(left: int, right: int) -> int:\n    return left - right\n"
                ),
                "replacement_content": (
                    "def add(left: int, right: int) -> int:\n    return left + right\n"
                ),
            },
        ),
        (
            "apply_workspace_changes",
            {
                "changes": [
                    {
                        "path": "module.py",
                        "expected_content": (
                            "def add(left: int, right: int) -> int:\n"
                            "    return left - right\n"
                        ),
                        "replacement_content": (
                            "def add(left: int, right: int) -> int:\n"
                            "    return left + right\n"
                        ),
                    }
                ]
            },
        ),
    ],
)
def test_all_controlled_workspace_action_shapes_count_as_changes(
    tmp_path: Path,
    tool_name: str,
    arguments: dict[str, object],
) -> None:
    """Recognize successful patch and transaction metadata as real changes."""

    repository = create_coding_repository(tmp_path / "project")
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            tool_response("edit", tool_name, arguments),
            ChatResponse(text="Edit complete."),
            ChatResponse(text="Edit complete."),
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    assert result.final_phase is CodingPhase.DONE
    assert result.workspace_change_applied is True
    assert tool_name in result.executed_tool_names
    assert result.validation_succeeded is True


def test_successful_file_rewrite_reaches_validation_done_and_path_evidence(
    tmp_path: Path,
) -> None:
    """Treat a successful rewrite as the exact approved changed path."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    replacement = "def add(left: int, right: int) -> int:\n    return left + right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            rewrite_response(
                "rewrite",
                expected_content=original,
                replacement_content=replacement,
            ),
            ChatResponse(text="Rewrite complete."),
            ChatResponse(text="Rewrite complete."),
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    assert result.final_phase is CodingPhase.DONE
    assert result.validation_succeeded is True
    assert result.approved_workspace_paths == ("module.py",)
    assert "apply_file_rewrite" in result.approved_action_names


def test_repairs_without_new_changes_stop_without_revalidating(
    tmp_path: Path,
) -> None:
    """Bound each repair's completions and preserve the failed validation evidence."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            replacement_response(
                "bad-edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left * right",
            ),
            ChatResponse(text="Edit complete."),
            ChatResponse(text="Edit complete."),
            ChatResponse(text="Repair claim one."),
            ChatResponse(text="Repair claim two."),
            ChatResponse(text="Repair claim three."),
            ChatResponse(text="Second repair claim one."),
            ChatResponse(text="Second repair claim two."),
            ChatResponse(text="Second repair claim three."),
        ]
    )
    observed_rounds = []

    with pytest.raises(
        CompletionError,
        match=(
            r"phase REPAIR: repair completed without a successful new workspace "
            r"change.*repair_attempts=2, completion_continuations=5"
        ),
    ):
        run_autonomous_coding_task(
            create_session(repository, provider),
            "Correct the add implementation.",
            tool_approval_handler=approve,
            tool_round_observer=observed_rounds.append,
        )

    validation_names = [
        invocation.tool_name
        for round_ in observed_rounds
        for invocation in round_.response.tool_invocations
        if invocation.tool_name in {"run_ruff_format", "run_ruff_check", "run_pytest"}
    ]
    assert validation_names == [
        "run_ruff_format",
        "run_ruff_check",
        "run_pytest",
    ]


def test_assistant_success_claim_without_change_is_rejected(
    tmp_path: Path,
) -> None:
    """Treat repeated final prose as no workspace evidence."""

    repository = create_coding_repository(tmp_path / "project")
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            ChatResponse(text="Success."),
            ChatResponse(text="Definitely complete."),
            ChatResponse(text="No changes are necessary."),
        ]
    )

    with pytest.raises(
        CompletionError,
        match=(
            r"phase EDIT: completion continuation limit reached.*"
            r"repair_attempts=0, completion_continuations=2"
        ),
    ):
        run_autonomous_coding_task(
            create_session(repository, provider),
            "Correct the add implementation.",
            tool_approval_handler=approve,
        )

    assert DEFAULT_EDIT_COMPLETION_CONTINUATIONS == 2
    assert run_git(repository, "status", "--short").stdout == ""


def test_successful_actions_with_empty_final_diff_are_rejected(
    tmp_path: Path,
) -> None:
    """Require a non-empty final diff even after successful action results."""

    repository = create_coding_repository(tmp_path / "project", passing=True)
    original = "def add(left: int, right: int) -> int:\n    return left + right\n"
    defective = "def add(left: int, right: int) -> int:\n    return left - right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            replacement_response(
                "change",
                expected_content=original,
                expected_text="return left + right",
                replacement_text="return left - right",
            ),
            replacement_response(
                "revert",
                expected_content=defective,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="Actions complete."),
            ChatResponse(text="Actions complete."),
        ]
    )

    with pytest.raises(
        CompletionError,
        match=r"phase VERIFY: final Git diff is empty",
    ):
        run_autonomous_coding_task(
            create_session(repository, provider),
            "Exercise the controlled actions.",
            tool_approval_handler=approve,
        )

    assert run_git(repository, "status", "--short").stdout == ""


def test_safe_untracked_git_evidence_satisfies_final_diff_gate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Treat safe untracked text evidence as a non-empty verified change."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            replacement_response(
                "edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="Edit complete."),
            ChatResponse(text="Edit complete."),
        ]
    )
    monkeypatch.setattr(
        "agent_workbench.git_tools.inspect_workspace_git_diff",
        lambda _workspace, _arguments: {
            "unstaged": "",
            "staged": "",
            "untracked": (
                "diff --git a/new.py b/new.py\n"
                "new file mode 100644\n"
                "--- /dev/null\n"
                "+++ b/new.py\n"
                "@@ -0,0 +1 @@\n"
                "+value = 1\n"
            ),
            "untracked_omitted": [],
        },
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    assert result.final_phase is CodingPhase.DONE
    assert result.inspected_git_diff_after_change is True


def test_untracked_only_created_files_reach_done_without_staging(
    tmp_path: Path,
) -> None:
    """Validate and verify a source-and-test task composed only of new files."""

    repository = create_coding_repository(tmp_path / "project", passing=True)
    source_content = (
        "def multiply(left: int, right: int) -> int:\n    return left * right\n"
    )
    test_content = (
        "from created_module import multiply\n"
        "\n"
        "\n"
        "def test_multiply() -> None:\n"
        "    assert multiply(2, 3) == 6\n"
    )
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            tool_response(
                "create-files",
                "apply_workspace_changes",
                {
                    "changes": [
                        {
                            "path": "created_module.py",
                            "expected_content": "",
                            "replacement_content": source_content,
                            "create_if_missing": True,
                        },
                        {
                            "path": "test_created_module.py",
                            "expected_content": "",
                            "replacement_content": test_content,
                            "create_if_missing": True,
                        },
                    ]
                },
            ),
            ChatResponse(text="Created the source and focused test."),
            ChatResponse(text="Created the source and focused test."),
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Create a multiplication module and focused test.",
        tool_approval_handler=approve,
    )

    diff_index = result.executed_tool_names.index("inspect_git_diff")
    diff_output = result.tool_results[diff_index].output
    assert isinstance(diff_output, dict)
    assert result.final_phase is CodingPhase.DONE
    assert result.workspace_change_applied is True
    assert result.validation_succeeded is True
    assert result.inspected_git_status_after_change is True
    assert result.inspected_git_diff_after_change is True
    assert result.approved_workspace_paths == (
        "created_module.py",
        "test_created_module.py",
    )
    assert diff_output["unstaged"] == ""
    assert diff_output["staged"] == ""
    assert "a/created_module.py" in diff_output["untracked"]
    assert "a/test_created_module.py" in diff_output["untracked"]
    assert diff_output["untracked"].strip()
    assert run_git(repository, "diff", "--cached", "--name-only").stdout == ""
    assert run_git(repository, "status", "--short").stdout == (
        "?? created_module.py\n?? test_created_module.py\n"
    )


def test_omitted_only_untracked_file_cannot_reach_done(tmp_path: Path) -> None:
    """Reject an unsafe omitted path during VALIDATE before DONE."""

    repository = create_coding_repository(tmp_path / "project", passing=True)
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            tool_response(
                "create-unsafe",
                "apply_workspace_changes",
                {
                    "changes": [
                        {
                            "path": ".env",
                            "expected_content": "",
                            "replacement_content": "SAFE_PLACEHOLDER=1\n",
                            "create_if_missing": True,
                        }
                    ]
                },
            ),
            ChatResponse(text="Created the requested file."),
            ChatResponse(text="Created the requested file."),
        ]
    )

    with pytest.raises(
        CompletionError,
        match=(
            r"phase VALIDATE: unexpected unsafe changed path "
            r"after run_ruff_check"
        ),
    ):
        run_autonomous_coding_task(
            create_session(repository, provider),
            "Create one local environment placeholder.",
            tool_approval_handler=approve,
        )

    diff_output = inspect_workspace_git_diff(Workspace(repository), {})
    assert diff_output["unstaged"] == ""
    assert diff_output["staged"] == ""
    assert diff_output["untracked"] == ""
    assert diff_output["untracked_omitted"] == [
        {
            "reason": "unsafe_or_ignored",
            "file_count": 1,
        }
    ]
    assert run_git(repository, "diff", "--cached", "--name-only").stdout == ""
    assert run_git(repository, "status", "--short").stdout == "?? .env\n"


def test_failed_git_inspection_prevents_done(tmp_path: Path) -> None:
    """Reject successful validation when final Git diff inspection errors."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            replacement_response(
                "edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="Edit complete."),
            ChatResponse(text="Edit complete."),
        ]
    )

    with pytest.raises(
        CompletionError,
        match=r"phase VERIFY: inspect_git_diff returned an error",
    ):
        run_autonomous_coding_task(
            create_session(repository, provider, fail_git_diff=True),
            "Correct the add implementation.",
            tool_approval_handler=approve,
        )


def test_phase_prompts_include_explicit_evidence_and_attempt_counters(
    tmp_path: Path,
) -> None:
    """Carry objective, phase, evidence, requirements, and counters every time."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    provider = ScriptedProvider(
        [
            tool_response("read", "read_file", {"path": "module.py"}),
            ChatResponse(text="Discovery complete."),
            replacement_response(
                "edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="Edit complete."),
            ChatResponse(text="Edit complete."),
        ]
    )

    run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    discover_prompt = provider.requests[0].messages[-1]["content"]
    edit_prompt = provider.requests[2].messages[-1]["content"]
    for phase, prompt in (
        ("DISCOVER", discover_prompt),
        ("EDIT", edit_prompt),
    ):
        assert "Original objective:\nCorrect the add implementation." in prompt
        assert f"Current phase: {phase}" in prompt
        assert "Completed phase evidence:" in prompt
        assert "Outstanding requirements:" in prompt
        assert "Current attempt counters:" in prompt
        assert "Repair attempts: 0/2" in prompt
        assert "Completion continuations: 0" in prompt
        assert "Only the controller can advance phases or declare DONE." in prompt
    assert "Executed tools: read_file" in edit_prompt
    assert "Controlled edit selection:" in edit_prompt
    assert "apply_text_replacement" in edit_prompt
    assert "exact current fragment is known and reasonably small" in edit_prompt
    assert "apply_line_range_replacement" in edit_prompt
    assert "one-based and inclusive" in edit_prompt
    assert "exact current range content must be known" in edit_prompt
    assert "appropriate current read_file" in edit_prompt
    assert "successful prior action result" in edit_prompt
    assert "Never guess a hash or uninspected line numbers" in edit_prompt
    assert "Never use a whole-file rewrite to avoid an exact-content mismatch" in (
        edit_prompt
    )
    assert "Never weaken tests or validation" in edit_prompt
    assert DEFAULT_DISCOVER_MAX_TOOL_ROUNDS == 4
    assert DEFAULT_REPAIR_COMPLETION_CONTINUATIONS == 2


def test_custom_acceptance_criteria_reach_every_model_facing_phase(
    tmp_path: Path,
) -> None:
    """Preserve ordered sanitized criteria through all phase continuations."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    multiplied = "def add(left: int, right: int) -> int:\n    return left * right\n"
    criteria = (
        "Preserve the public function signature.",
        "Inspect /home/private/repository only when required.",
        "TOKEN=acceptance-secret",
        "Correct the arithmetic behavior.",
        "Keep the focused test passing.",
    )
    displayed_criteria = (
        "Preserve the public function signature.",
        "Inspect [absolute-path] only when required.",
        "[redacted sensitive content]",
        "Correct the arithmetic behavior.",
        "Keep the focused test passing.",
    )
    provider = ScriptedProvider(
        [
            ChatResponse(text="Found module.py and its focused test."),
            ChatResponse(text="No edit yet."),
            replacement_response(
                "bad-edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left * right",
            ),
            ChatResponse(text="Bad edit complete."),
            ChatResponse(text="Bad edit complete."),
            ChatResponse(text="No repair yet."),
            replacement_response(
                "repair",
                expected_content=multiplied,
                expected_text="return left * right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="Repair complete."),
            ChatResponse(text="Repair complete."),
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        acceptance_criteria=criteria,
        tool_approval_handler=approve,
    )

    assert result.final_phase is CodingPhase.DONE
    phase_prompt_indexes = (0, 1, 2, 5, 6)
    for request_index in phase_prompt_indexes:
        prompt = provider.requests[request_index].messages[-1]["content"]
        assert "Acceptance criteria:" in prompt
        positions = [
            prompt.index(f"{index}. {criterion}")
            for index, criterion in enumerate(displayed_criteria, start=1)
        ]
        assert positions == sorted(positions)
        assert "/home/private" not in prompt
        assert "acceptance-secret" not in prompt
    assert (
        "Discovery summary: Found module.py and its focused test."
        in (provider.requests[1].messages[-1]["content"])
    )


def test_approval_denial_fails_current_phase_without_validation(
    tmp_path: Path,
) -> None:
    """Preserve explicit default-deny behavior for model workspace actions."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            replacement_response(
                "edit",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
        ]
    )

    with pytest.raises(
        CompletionError,
        match=r"phase EDIT: model-facing phase failed: Tool action approval was denied",
    ):
        run_autonomous_coding_task(
            create_session(repository, provider),
            "Correct the add implementation.",
            tool_approval_handler=lambda _request: ToolApprovalDecision.DENY,
        )

    assert run_git(repository, "status", "--short").stdout == ""


class TestDeterministicRegressionBattery:
    """Exercise representative coding outcomes without any real model provider."""

    def test_changes_wrong_constant_and_passes(self, tmp_path: Path) -> None:
        """Correct one tracked constant and reach controller-owned DONE."""

        repository = create_coding_repository(tmp_path / "project", passing=True)
        original = "ANSWER = 1\n"
        replacement = "ANSWER = 2\n"
        (repository / "module.py").write_text(original, encoding="utf-8")
        (repository / "test_module.py").write_text(
            "from module import ANSWER\n"
            "\n"
            "\n"
            "def test_answer() -> None:\n"
            "    assert ANSWER == 2\n",
            encoding="utf-8",
        )
        run_git(repository, "add", "module.py", "test_module.py")
        run_git(repository, "commit", "-m", "constant fixture")
        provider = ScriptedProvider(
            [
                ChatResponse(text="Discovery complete."),
                replacement_response(
                    "constant-edit",
                    expected_content=original,
                    expected_text="ANSWER = 1",
                    replacement_text="ANSWER = 2",
                ),
                ChatResponse(text="Constant corrected."),
                ChatResponse(text="Constant corrected."),
            ]
        )

        result = run_autonomous_coding_task(
            create_session(repository, provider),
            "Correct the wrong constant.",
            tool_approval_handler=approve,
        )

        assert result.final_phase is CodingPhase.DONE
        assert result.validation_succeeded is True
        assert (repository / "module.py").read_text(encoding="utf-8") == replacement

    def test_repairs_function_after_first_pytest_failure(
        self,
        tmp_path: Path,
    ) -> None:
        """Use failed controller-run pytest evidence to drive one repair."""

        repository = create_coding_repository(tmp_path / "project")
        original = "def add(left: int, right: int) -> int:\n    return left - right\n"
        multiplied = "def add(left: int, right: int) -> int:\n    return left * right\n"
        provider = ScriptedProvider(
            [
                ChatResponse(text="Discovery complete."),
                replacement_response(
                    "bad-edit",
                    expected_content=original,
                    expected_text="return left - right",
                    replacement_text="return left * right",
                ),
                ChatResponse(text="First edit complete."),
                ChatResponse(text="First edit complete."),
                replacement_response(
                    "repair",
                    expected_content=multiplied,
                    expected_text="return left * right",
                    replacement_text="return left + right",
                ),
                ChatResponse(text="Repair complete."),
                ChatResponse(text="Repair complete."),
            ]
        )

        result = run_autonomous_coding_task(
            create_session(repository, provider),
            "Repair the add function.",
            tool_approval_handler=approve,
        )

        pytest_runs = [
            run for run in result.validation_runs if run.tool_name == "run_pytest"
        ]
        assert [run.exit_code for run in pytest_runs] == [1, 0]
        assert result.repair_attempt_count == 1
        assert result.final_phase is CodingPhase.DONE

    def test_adds_small_function_and_test(self, tmp_path: Path) -> None:
        """Add implementation and test changes in one approved transaction."""

        repository = create_coding_repository(tmp_path / "project", passing=True)
        original_module = (
            "def add(left: int, right: int) -> int:\n    return left + right\n"
        )
        replacement_module = (
            f"{original_module}\n\ndef double(value: int) -> int:\n"
            "    return value * 2\n"
        )
        original_test = (
            "from module import add\n"
            "\n"
            "\n"
            "def test_add() -> None:\n"
            "    assert add(1, 2) == 3\n"
        )
        replacement_test = (
            "from module import add, double\n"
            "\n"
            "\n"
            "def test_add() -> None:\n"
            "    assert add(1, 2) == 3\n"
            "\n"
            "\n"
            "def test_double() -> None:\n"
            "    assert double(3) == 6\n"
        )
        provider = ScriptedProvider(
            [
                ChatResponse(text="Discovery complete."),
                tool_response(
                    "add-function-and-test",
                    "apply_workspace_changes",
                    {
                        "changes": [
                            {
                                "path": "module.py",
                                "expected_content": original_module,
                                "replacement_content": replacement_module,
                            },
                            {
                                "path": "test_module.py",
                                "expected_content": original_test,
                                "replacement_content": replacement_test,
                            },
                        ]
                    },
                ),
                ChatResponse(text="Function and test added."),
                ChatResponse(text="Function and test added."),
            ]
        )

        result = run_autonomous_coding_task(
            create_session(repository, provider),
            "Add double and its test.",
            tool_approval_handler=approve,
        )

        assert result.final_phase is CodingPhase.DONE
        assert result.validation_succeeded is True
        assert "def double" in (repository / "module.py").read_text(encoding="utf-8")
        assert "test_double" in (repository / "test_module.py").read_text(
            encoding="utf-8"
        )

    def test_rejects_completion_without_diff(self, tmp_path: Path) -> None:
        """Reject repeated success prose before controller validation starts."""

        repository = create_coding_repository(tmp_path / "project", passing=True)
        provider = ScriptedProvider(
            [
                ChatResponse(text="Discovery complete."),
                ChatResponse(text="Complete."),
                ChatResponse(text="Still complete."),
                ChatResponse(text="No diff needed."),
            ]
        )

        with pytest.raises(
            CompletionError,
            match=r"phase EDIT: completion continuation limit reached",
        ):
            run_autonomous_coding_task(
                create_session(repository, provider),
                "Make the requested change.",
                tool_approval_handler=approve,
            )

        assert run_git(repository, "diff").stdout == ""

    def test_rejects_completion_while_tests_are_failing(
        self,
        tmp_path: Path,
    ) -> None:
        """Keep a failed pytest result from becoming successful model prose."""

        repository = create_coding_repository(tmp_path / "project")
        original = "def add(left: int, right: int) -> int:\n    return left - right\n"
        provider = ScriptedProvider(
            [
                ChatResponse(text="Discovery complete."),
                replacement_response(
                    "bad-edit",
                    expected_content=original,
                    expected_text="return left - right",
                    replacement_text="return left * right",
                ),
                ChatResponse(text="The task passes."),
                ChatResponse(text="The task passes."),
                ChatResponse(text="Repair is complete."),
                ChatResponse(text="Tests now pass."),
                ChatResponse(text="No change needed."),
                ChatResponse(text="Second repair complete."),
                ChatResponse(text="Everything passes."),
                ChatResponse(text="Done."),
            ]
        )
        observed_rounds = []

        with pytest.raises(
            CompletionError,
            match=r"phase REPAIR: repair completed without a successful new workspace change",
        ):
            run_autonomous_coding_task(
                create_session(repository, provider),
                "Correct the add function.",
                tool_approval_handler=approve,
                tool_round_observer=observed_rounds.append,
            )

        pytest_results = [
            result
            for round_ in observed_rounds
            for invocation, result in zip(
                round_.response.tool_invocations,
                round_.results,
                strict=True,
            )
            if invocation.tool_name == "run_pytest"
        ]
        assert pytest_results[-1].output["exit_code"] == 1

    def test_stops_at_configured_repair_limit(self, tmp_path: Path) -> None:
        """Honor a typed one-attempt repair configuration."""

        repository = create_coding_repository(tmp_path / "project")
        original = "def add(left: int, right: int) -> int:\n    return left - right\n"
        multiplied = "def add(left: int, right: int) -> int:\n    return left * right\n"
        provider = ScriptedProvider(
            [
                ChatResponse(text="Discovery complete."),
                replacement_response(
                    "bad-edit",
                    expected_content=original,
                    expected_text="return left - right",
                    replacement_text="return left * right",
                ),
                ChatResponse(text="First edit complete."),
                ChatResponse(text="First edit complete."),
                replacement_response(
                    "only-repair",
                    expected_content=multiplied,
                    expected_text="return left * right",
                    replacement_text="return left / right",
                ),
                ChatResponse(text="Only repair complete."),
                ChatResponse(text="Only repair complete."),
            ]
        )

        with pytest.raises(
            CompletionError,
            match=r"repair_attempts=1, completion_continuations=2",
        ):
            run_autonomous_coding_task(
                create_session(repository, provider),
                "Correct the add function.",
                tool_approval_handler=approve,
                limits=CodingWorkflowLimits(repair_attempts=1),
            )
