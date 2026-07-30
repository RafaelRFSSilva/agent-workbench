"""Vertical tests for the deterministic coding workflow controller."""

import hashlib
import subprocess
from collections.abc import Iterable
from pathlib import Path

import pytest

from agent_workbench.coding_loop import (
    DEFAULT_DISCOVER_MAX_TOOL_ROUNDS,
    DEFAULT_EDIT_COMPLETION_CONTINUATIONS,
    DEFAULT_MAX_REPAIR_ATTEMPTS,
    DEFAULT_REPAIR_COMPLETION_CONTINUATIONS,
    CodingPhase,
    CodingWorkflowLimits,
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
from agent_workbench.tools import ToolApprovalDecision, ToolInvocation
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
) -> ChatResponse:
    """Create one deterministic single-tool provider response."""

    return ChatResponse(
        tool_invocations=(
            ToolInvocation(
                id=invocation_id,
                tool_name=tool_name,
                arguments=arguments,
            ),
        )
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


def approve(_request) -> ToolApprovalDecision:
    """Approve one action inside a disposable test repository."""

    return ToolApprovalDecision.APPROVE


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
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    assert result.final_phase is CodingPhase.DONE
    assert result.workspace_change_applied is True
    assert result.repair_attempt_count == 0
    assert result.completion_continuation_count == 0
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
    assert result.validation_succeeded is True
    assert len(result.tool_results) == result.tool_round_count
    assert all(tool_result.status == "success" for tool_result in result.tool_results)
    assert result.inspected_git_status_after_change is True
    assert result.inspected_git_diff_after_change is True
    assert result.assistant_summary == "Corrected the add implementation."
    assert run_git(repository, "status", "--short").stdout == " M module.py\n"

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
            "apply_text_replacement",
            "apply_workspace_changes",
            "run_ruff_format",
            "run_ruff_check",
            "run_pytest",
        }
    )
    edit_tools = {tool.name for tool in provider.requests[2].tools}
    assert {
        "apply_file_patch",
        "apply_text_replacement",
        "apply_workspace_changes",
    }.issubset(edit_tools)
    assert not edit_tools.intersection(
        {"run_ruff_format", "run_ruff_check", "run_pytest"}
    )


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
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    assert result.final_phase is CodingPhase.DONE
    assert result.completion_continuation_count == 1
    continuation = provider.requests[2].messages[-1]["content"]
    assert "Current phase: EDIT" in continuation
    assert "no successful new workspace change was observed" in continuation
    assert "Assistant prose is not evidence" in continuation


def test_edit_round_exhaustion_continues_then_changes_file(
    tmp_path: Path,
) -> None:
    """Consume one EDIT continuation after inspection-only round exhaustion."""

    repository = create_coding_repository(tmp_path / "project")
    original = "def add(left: int, right: int) -> int:\n    return left - right\n"
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            tool_response("edit-read-module", "read_file", {"path": "module.py"}),
            tool_response(
                "edit-read-test",
                "read_file",
                {"path": "test_module.py"},
            ),
            replacement_response(
                "edit-after-exhaustion",
                expected_content=original,
                expected_text="return left - right",
                replacement_text="return left + right",
            ),
            ChatResponse(text="Edit complete."),
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider, max_tool_rounds=1),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    assert result.final_phase is CodingPhase.DONE
    assert result.completion_continuation_count == 1
    continuation = provider.requests[3].messages[-1]["content"]
    assert "Current phase: EDIT" in continuation
    assert "exhausted its tool-round budget" in continuation
    assert "without completing the required workspace change" in continuation


def test_repair_round_exhaustion_continues_then_repairs(
    tmp_path: Path,
) -> None:
    """Consume one REPAIR continuation after inspection-only exhaustion."""

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
            tool_response("repair-read", "read_file", {"path": "module.py"}),
            tool_response(
                "repair-extra-read",
                "read_file",
                {"path": "test_module.py"},
            ),
            replacement_response(
                "repair-after-exhaustion",
                expected_content=multiplied,
                expected_text="return left * right",
                replacement_text="return left + right",
            ),
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
    assert result.completion_continuation_count == 1
    continuation = provider.requests[5].messages[-1]["content"]
    assert "Current phase: REPAIR" in continuation
    assert "exhausted its tool-round budget" in continuation


def test_edit_round_exhaustion_after_change_advances_to_validation(
    tmp_path: Path,
) -> None:
    """Accept a successful EDIT action even without a final model response."""

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
            tool_response("unexecuted-read", "read_file", {"path": "module.py"}),
        ]
    )

    result = run_autonomous_coding_task(
        create_session(repository, provider, max_tool_rounds=1),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    assert result.final_phase is CodingPhase.DONE
    assert result.completion_continuation_count == 0
    assert result.assistant_summary == (
        "A successful workspace change was applied before the model-facing "
        "phase exhausted its tool-round budget."
    )


def test_repeated_edit_round_exhaustion_stops_at_continuation_limit(
    tmp_path: Path,
) -> None:
    """Stop after two bounded EDIT continuations without a change."""

    repository = create_coding_repository(tmp_path / "project")
    provider = ScriptedProvider(
        [
            ChatResponse(text="Discovery complete."),
            tool_response("read-1", "read_file", {"path": "module.py"}),
            tool_response("exhaust-1", "read_file", {"path": "test_module.py"}),
            tool_response("read-2", "read_file", {"path": "module.py"}),
            tool_response("exhaust-2", "read_file", {"path": "test_module.py"}),
            tool_response("read-3", "read_file", {"path": "module.py"}),
            tool_response("exhaust-3", "read_file", {"path": "test_module.py"}),
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
            replacement_response(
                "repair",
                expected_content=multiplied,
                expected_text="return left * right",
                replacement_text="return left + right",
            ),
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

    repair_prompt = provider.requests[3].messages[-1]["content"]
    assert "Original objective:\nCorrect the add implementation." in repair_prompt
    assert "Inspect [absolute-path] if needed." in repair_prompt
    assert "[redacted sensitive content]" in repair_prompt
    assert "/home/example/private" not in repair_prompt
    assert "must-not-enter-repair-evidence" not in repair_prompt
    assert "Current phase: REPAIR" in repair_prompt
    assert "Repair attempt: 1/2" in repair_prompt
    assert "run_pytest: status=success, exit_code=1" in repair_prompt
    assert "Current changed-file paths: module.py" in repair_prompt
    assert "requires another successful controlled workspace change" in repair_prompt


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
            replacement_response(
                "repair-1",
                expected_content=multiplied,
                expected_text="return left * right",
                replacement_text="return left / right",
            ),
            ChatResponse(text="First repair complete."),
            replacement_response(
                "repair-2",
                expected_content=divided,
                expected_text="return left / right",
                replacement_text="return left // right",
            ),
            ChatResponse(text="Second repair complete."),
        ]
    )

    with pytest.raises(
        CompletionError,
        match=(
            r"phase REPAIR: validation still failed.*"
            r"repair_attempts=2, completion_continuations=0"
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
            r"change.*repair_attempts=2, completion_continuations=4"
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
    """Reject VERIFY when the only untracked change has no safe diff evidence."""

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
        ]
    )

    with pytest.raises(
        CompletionError,
        match=r"phase VERIFY: final Git diff is empty",
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
            ChatResponse(text="No repair yet."),
            replacement_response(
                "repair",
                expected_content=multiplied,
                expected_text="return left * right",
                replacement_text="return left + right",
            ),
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
    phase_prompt_indexes = (0, 1, 2, 4, 5)
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
                replacement_response(
                    "repair",
                    expected_content=multiplied,
                    expected_text="return left * right",
                    replacement_text="return left + right",
                ),
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
                replacement_response(
                    "only-repair",
                    expected_content=multiplied,
                    expected_text="return left * right",
                    replacement_text="return left / right",
                ),
                ChatResponse(text="Only repair complete."),
            ]
        )

        with pytest.raises(
            CompletionError,
            match=r"repair_attempts=1, completion_continuations=0",
        ):
            run_autonomous_coding_task(
                create_session(repository, provider),
                "Correct the add function.",
                tool_approval_handler=approve,
                limits=CodingWorkflowLimits(repair_attempts=1),
            )
