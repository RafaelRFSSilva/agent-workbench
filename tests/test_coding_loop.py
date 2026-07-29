"""Vertical tests for the supervised autonomous coding loop."""

import hashlib
from collections.abc import Iterable
from pathlib import Path
import subprocess

import pytest

from agent_workbench.coding_loop import (
    MAX_AUTONOMOUS_COMPLETION_CONTINUATIONS,
    run_autonomous_coding_task,
)
from agent_workbench.errors import CompletionError
from agent_workbench.git_tools import register_git_tools
from agent_workbench.messages import ChatRequest, ChatResponse
from agent_workbench.session import AgentSession, SessionId
from agent_workbench.tool_registry import ToolRegistry
from agent_workbench.tools import (
    ToolApprovalDecision,
    ToolInvocation,
)
from agent_workbench.validation_tools import register_validation_tools
from agent_workbench.workspace import Workspace
from agent_workbench.workspace_actions import register_workspace_action_tools
from agent_workbench.workspace_tools import register_workspace_tools


class ScriptedProvider:
    """Return deterministic responses for one complete coding workflow."""

    name = "scripted"
    model_name = "scripted-coding-model"

    def __init__(self, responses: Iterable[ChatResponse]) -> None:
        self._responses = iter(responses)
        self.requests: list[ChatRequest] = []

    def complete(self, request: ChatRequest) -> ChatResponse:
        """Record each request and return the next scripted response."""

        self.requests.append(request)
        return next(self._responses)


def run_git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run one Git command inside a disposable test repository."""

    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def create_coding_repository(root: Path) -> Path:
    """Create one committed Python project containing an intentional defect."""

    root.mkdir()
    run_git(root, "init", "-b", "main")
    run_git(root, "config", "user.name", "Test User")
    run_git(root, "config", "user.email", "test@example.com")

    (root / "module.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left - right\n",
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


def create_coding_registry(workspace: Workspace) -> ToolRegistry:
    """Register the real tools needed by one bounded coding task."""

    registry = ToolRegistry()
    register_workspace_tools(registry, workspace)
    register_git_tools(registry, workspace)
    register_workspace_action_tools(registry, workspace)
    register_validation_tools(registry, workspace)
    return registry


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


def test_runs_complete_inspect_edit_validate_and_diff_cycle(
    tmp_path: Path,
) -> None:
    """Complete one real bounded coding workflow from a single prompt."""

    repository = create_coding_repository(tmp_path / "project")
    workspace = Workspace(repository)
    registry = create_coding_registry(workspace)

    corrected_content = (
        "def add(left: int, right: int) -> int:\n    return left + right\n"
    )

    provider = ScriptedProvider(
        [
            tool_response(
                "list",
                "list_files",
                {"path": "."},
            ),
            tool_response(
                "read",
                "read_file",
                {"path": "module.py"},
            ),
            tool_response(
                "replacement",
                "apply_text_replacement",
                {
                    "path": "module.py",
                    "expected_text": "return left - right",
                    "replacement_text": "return left + right",
                    "expected_file_sha256": hashlib.sha256(
                        (
                            "def add(left: int, right: int) -> int:\n"
                            "    return left - right\n"
                        ).encode("utf-8")
                    ).hexdigest(),
                },
            ),
            tool_response(
                "ruff",
                "run_ruff_check",
                {"path": "."},
            ),
            tool_response(
                "pytest",
                "run_pytest",
                {"path": "."},
            ),
            tool_response(
                "status",
                "inspect_git_status",
                {},
            ),
            tool_response(
                "diff",
                "inspect_git_diff",
                {},
            ),
            ChatResponse(
                text=(
                    "Corrected the add implementation. "
                    "Ruff and pytest completed successfully, and the final "
                    "Git status and diff were inspected."
                )
            ),
        ]
    )

    session = AgentSession(
        id=SessionId("autonomous-coding-test"),
        provider=provider,
        tool_registry=registry,
        max_tool_rounds=8,
    )

    approval_requests = []

    def approve(request):
        approval_requests.append(request)
        return ToolApprovalDecision.APPROVE

    result = run_autonomous_coding_task(
        session,
        "Correct the add function so that the existing test passes.",
        tool_approval_handler=approve,
    )

    assert (repository / "module.py").read_text(encoding="utf-8") == corrected_content

    assert result.task_spec.objective == (
        "Correct the add function so that the existing test passes."
    )
    assert result.tool_round_count == 7
    assert result.executed_tool_names == (
        "list_files",
        "read_file",
        "apply_text_replacement",
        "run_ruff_check",
        "run_pytest",
        "inspect_git_status",
        "inspect_git_diff",
    )
    assert result.approved_action_names == (
        "apply_text_replacement",
        "run_ruff_check",
        "run_pytest",
    )
    assert tuple(run.tool_name for run in result.validation_runs) == (
        "run_ruff_check",
        "run_pytest",
    )
    assert all(run.result_status == "success" for run in result.validation_runs)
    assert all(run.exit_code == 0 for run in result.validation_runs)
    assert result.validation_succeeded is True
    assert result.inspected_git_status is True
    assert result.inspected_git_diff is True
    assert "Corrected the add implementation" in result.assistant_summary

    assert len(approval_requests) == 3

    assert len(provider.requests) == 8
    first_prompt = provider.requests[0].messages[0]["content"]
    assert "Correct the add function" in first_prompt
    assert "Run run_ruff_check and run_pytest" in first_prompt
    assert "Prefer apply_text_replacement for small exact edits" in first_prompt
    assert "inspect_git_status with {}" in first_prompt
    assert "inspect_git_diff with {}" in first_prompt

    assert run_git(repository, "status", "--short").stdout == " M module.py\n"
    assert "return left + right" in run_git(repository, "diff").stdout


def test_recovers_from_invalid_validation_preview_and_retries(
    tmp_path: Path,
) -> None:
    """Return an invalid approval preview to the model and accept its retry."""

    repository = create_coding_repository(tmp_path / "project")
    (repository / "module.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left + right\n",
        encoding="utf-8",
    )

    workspace = Workspace(repository)
    registry = create_coding_registry(workspace)

    provider = ScriptedProvider(
        [
            tool_response(
                "invalid-pytest",
                "run_pytest",
                {"path": "tests/test_module.py"},
            ),
            tool_response(
                "valid-pytest",
                "run_pytest",
                {"path": "."},
            ),
            ChatResponse(text="Recovered from the invalid path and completed pytest."),
            tool_response(
                "ruff",
                "run_ruff_check",
                {"path": "."},
            ),
            tool_response(
                "status",
                "inspect_git_status",
                {},
            ),
            tool_response(
                "diff",
                "inspect_git_diff",
                {},
            ),
            ChatResponse(text="Completed the remaining mandatory checks."),
        ]
    )

    session = AgentSession(
        id=SessionId("preview-recovery-test"),
        provider=provider,
        tool_registry=registry,
        max_tool_rounds=3,
    )

    approval_requests = []

    def approve(request):
        approval_requests.append(request)
        return ToolApprovalDecision.APPROVE

    result = run_autonomous_coding_task(
        session,
        "Run the project tests.",
        tool_approval_handler=approve,
    )

    assert len(approval_requests) == 2
    assert tuple(request.invocation.id for request in approval_requests) == (
        "valid-pytest",
        "ruff",
    )

    assert result.tool_round_count == 5
    assert result.executed_tool_names == (
        "run_pytest",
        "run_pytest",
        "run_ruff_check",
        "inspect_git_status",
        "inspect_git_diff",
    )
    assert result.approved_action_names == (
        "run_pytest",
        "run_ruff_check",
    )

    assert tuple(validation.result_status for validation in result.validation_runs) == (
        "error",
        "success",
        "success",
    )
    assert tuple(validation.exit_code for validation in result.validation_runs) == (
        None,
        0,
        0,
    )
    assert result.validation_succeeded is True
    assert result.inspected_git_status is True
    assert result.inspected_git_diff is True

    continuation_prompt = provider.requests[3].messages[-1]["content"]
    assert "successful run_ruff_check" in continuation_prompt
    assert "successful run_pytest" not in continuation_prompt
    assert "inspect_git_status" in continuation_prompt
    assert "inspect_git_diff" in continuation_prompt

    invalid_result = provider.requests[1].tool_interactions[0].results[0]
    assert invalid_result.status == "error"
    assert invalid_result.error == (
        "Approval preview failed for run_pytest: "
        "Workspace path does not exist: tests/test_module.py"
    )

    valid_result = provider.requests[2].tool_interactions[1].results[0]
    assert valid_result.status == "success"
    assert valid_result.output["exit_code"] == 0


def test_continues_once_after_an_early_final_response(
    tmp_path: Path,
) -> None:
    """Continue one edited task until all mandatory evidence is collected."""

    repository = create_coding_repository(tmp_path / "project")
    workspace = Workspace(repository)
    registry = create_coding_registry(workspace)
    original_content = (
        "def add(left: int, right: int) -> int:\n    return left - right\n"
    )

    provider = ScriptedProvider(
        [
            tool_response(
                "replacement",
                "apply_text_replacement",
                {
                    "path": "module.py",
                    "expected_text": "return left - right",
                    "replacement_text": "return left + right",
                    "expected_file_sha256": hashlib.sha256(
                        original_content.encode("utf-8")
                    ).hexdigest(),
                },
            ),
            ChatResponse(text="Implemented the requested edit."),
            tool_response("ruff", "run_ruff_check", {"path": "."}),
            tool_response("pytest", "run_pytest", {"path": "."}),
            tool_response("status", "inspect_git_status", {}),
            tool_response("diff", "inspect_git_diff", {}),
            ChatResponse(text="Validation and Git inspection are complete."),
        ]
    )
    session = AgentSession(
        id=SessionId("early-final-continuation"),
        provider=provider,
        tool_registry=registry,
        max_tool_rounds=4,
    )
    approval_requests = []
    observed_rounds = []

    def approve(request):
        approval_requests.append(request)
        return ToolApprovalDecision.APPROVE

    result = run_autonomous_coding_task(
        session,
        "Correct the add implementation.",
        tool_approval_handler=approve,
        tool_round_observer=observed_rounds.append,
    )

    assert MAX_AUTONOMOUS_COMPLETION_CONTINUATIONS == 1
    assert result.assistant_summary == "Validation and Git inspection are complete."
    assert result.tool_round_count == 5
    assert result.executed_tool_names == (
        "apply_text_replacement",
        "run_ruff_check",
        "run_pytest",
        "inspect_git_status",
        "inspect_git_diff",
    )
    assert result.approved_action_names == (
        "apply_text_replacement",
        "run_ruff_check",
        "run_pytest",
    )
    assert result.validation_succeeded is True
    assert result.inspected_git_status is True
    assert result.inspected_git_diff is True
    assert len(observed_rounds) == 5
    assert len(provider.requests) == 7

    continuation_prompt = provider.requests[2].messages[-1]["content"]
    expected_order = (
        "1. successful run_ruff_check\n"
        "2. successful run_pytest\n"
        "3. inspect_git_status\n"
        "4. inspect_git_diff"
    )
    assert expected_order in continuation_prompt
    assert "Do not repeat already completed work unnecessarily." in (
        continuation_prompt
    )


def test_continuation_requests_only_missing_git_inspection(
    tmp_path: Path,
) -> None:
    """Do not ask the model to repeat validations that already succeeded."""

    repository = create_coding_repository(tmp_path / "project")
    (repository / "module.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left + right\n",
        encoding="utf-8",
    )
    workspace = Workspace(repository)
    registry = create_coding_registry(workspace)

    provider = ScriptedProvider(
        [
            tool_response("ruff", "run_ruff_check", {"path": "."}),
            tool_response("pytest", "run_pytest", {"path": "."}),
            ChatResponse(text="Validation completed."),
            tool_response("status", "inspect_git_status", {}),
            tool_response("diff", "inspect_git_diff", {}),
            ChatResponse(text="Git inspection completed."),
        ]
    )
    session = AgentSession(
        id=SessionId("missing-git-continuation"),
        provider=provider,
        tool_registry=registry,
        max_tool_rounds=2,
    )

    result = run_autonomous_coding_task(
        session,
        "Validate and inspect the project.",
        tool_approval_handler=lambda _request: ToolApprovalDecision.APPROVE,
    )

    continuation_prompt = provider.requests[3].messages[-1]["content"]
    assert "1. inspect_git_status\n2. inspect_git_diff" in continuation_prompt
    assert "successful run_ruff_check" not in continuation_prompt
    assert "successful run_pytest" not in continuation_prompt
    assert result.validation_succeeded is True
    assert result.inspected_git_status is True
    assert result.inspected_git_diff is True


def test_unsuccessful_validation_gets_one_bounded_continuation(
    tmp_path: Path,
) -> None:
    """Request a failed validation again without issuing a third model turn."""

    repository = create_coding_repository(tmp_path / "project")
    workspace = Workspace(repository)
    registry = create_coding_registry(workspace)

    provider = ScriptedProvider(
        [
            tool_response("ruff", "run_ruff_check", {"path": "."}),
            tool_response("pytest", "run_pytest", {"path": "."}),
            tool_response("status", "inspect_git_status", {}),
            tool_response("diff", "inspect_git_diff", {}),
            ChatResponse(text="The checks were attempted."),
            ChatResponse(text="I cannot make further progress."),
        ]
    )
    session = AgentSession(
        id=SessionId("failed-validation-continuation"),
        provider=provider,
        tool_registry=registry,
        max_tool_rounds=4,
    )

    result = run_autonomous_coding_task(
        session,
        "Validate the intentionally failing project.",
        tool_approval_handler=lambda _request: ToolApprovalDecision.APPROVE,
    )

    continuation_prompt = provider.requests[5].messages[-1]["content"]
    assert "1. successful run_pytest" in continuation_prompt
    assert "successful run_ruff_check" not in continuation_prompt
    assert "inspect_git_status" not in continuation_prompt
    assert "inspect_git_diff" not in continuation_prompt
    assert len(provider.requests) == 6
    assert result.assistant_summary == "I cannot make further progress."
    assert result.validation_succeeded is False
    assert result.inspected_git_status is True
    assert result.inspected_git_diff is True


def test_approval_denial_does_not_issue_a_continuation(
    tmp_path: Path,
) -> None:
    """Preserve default-deny behavior without sending another model request."""

    repository = create_coding_repository(tmp_path / "project")
    workspace = Workspace(repository)
    registry = create_coding_registry(workspace)
    original_content = (
        "def add(left: int, right: int) -> int:\n    return left - right\n"
    )

    provider = ScriptedProvider(
        [
            tool_response(
                "replacement",
                "apply_text_replacement",
                {
                    "path": "module.py",
                    "expected_text": "return left - right",
                    "replacement_text": "return left + right",
                    "expected_file_sha256": hashlib.sha256(
                        original_content.encode("utf-8")
                    ).hexdigest(),
                },
            )
        ]
    )
    session = AgentSession(
        id=SessionId("denied-continuation"),
        provider=provider,
        tool_registry=registry,
        max_tool_rounds=1,
    )

    with pytest.raises(CompletionError, match="approval was denied"):
        run_autonomous_coding_task(
            session,
            "Correct the add implementation.",
            tool_approval_handler=lambda _request: ToolApprovalDecision.DENY,
        )

    assert len(provider.requests) == 1
    assert (repository / "module.py").read_text(encoding="utf-8") == (original_content)
