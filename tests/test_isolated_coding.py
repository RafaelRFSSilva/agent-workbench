"""Tests for the complete isolated autonomous coding workflow."""

from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_workbench.arguments import RuntimeConfiguration
from agent_workbench.coding_loop import (
    AutonomousCodingResult,
    CodingPhase,
    ValidationRun,
)
from agent_workbench.errors import CompletionError, ConfigurationError
from agent_workbench.isolated_coding import (
    IsolatedAutonomousWorkflowResult,
    run_isolated_autonomous_workflow,
)
from agent_workbench.session import SessionId
from agent_workbench.tasks import TaskSpec
from agent_workbench.tools import ToolApprovalDecision
from agent_workbench.worktree_commits import MAX_COMMIT_MESSAGE_BYTES


def run_git(
    repository: Path,
    *arguments: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run Git against one disposable repository."""

    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=check,
        capture_output=True,
        text=True,
    )


def create_repository(root: Path) -> Path:
    """Create one clean Python repository with local commit identity."""

    root.mkdir()
    run_git(root, "init", "-b", "main")
    run_git(root, "config", "user.name", "Workflow Test User")
    run_git(root, "config", "user.email", "workflow-test@example.invalid")
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
    run_git(root, "add", ".")
    run_git(root, "commit", "-m", "initial")
    return root


def configuration(source: Path) -> RuntimeConfiguration:
    """Create one minimal source-bound runtime configuration."""

    return RuntimeConfiguration(
        provider_name="ollama",
        model_name="test-model",
        workspace_root=source,
        enable_actions=True,
    )


def approve(_request) -> ToolApprovalDecision:
    """Approve one disposable supervised action."""

    return ToolApprovalDecision.APPROVE


def coding_result(
    *,
    final_phase: CodingPhase = CodingPhase.DONE,
    workspace_change_applied: bool = True,
    validation_succeeded: bool = True,
    validation_after_change: bool = True,
    inspected_git_status: bool = True,
    inspected_git_diff: bool = True,
    git_inspection_after_change: bool = True,
) -> AutonomousCodingResult:
    """Create one deterministic autonomous coding outcome."""

    change_index = 2 if workspace_change_applied else None
    validation_offset = 3 if validation_after_change else 0
    git_offset = 5 if git_inspection_after_change else 1
    pytest_exit_code = 0 if validation_succeeded else 1
    return AutonomousCodingResult(
        task_spec=TaskSpec(
            objective="Correct the add implementation.",
            acceptance_criteria=("Run validation.",),
        ),
        assistant_summary="Corrected the implementation.",
        final_phase=final_phase,
        workspace_change_applied=workspace_change_applied,
        repair_attempt_count=0,
        completion_continuation_count=0,
        tool_round_count=6,
        executed_tool_names=(
            "read_file",
            "apply_workspace_changes",
            "run_ruff_format",
            "run_ruff_check",
            "run_pytest",
            "inspect_git_status",
            "inspect_git_diff",
        ),
        approved_action_names=(
            "apply_workspace_changes",
            "run_ruff_format",
            "run_ruff_check",
            "run_pytest",
        ),
        validation_runs=(
            ValidationRun(
                tool_name="run_ruff_format",
                result_status="success",
                exit_code=0,
                sequence_index=validation_offset,
            ),
            ValidationRun(
                tool_name="run_ruff_check",
                result_status="success",
                exit_code=0,
                sequence_index=validation_offset + 1,
            ),
            ValidationRun(
                tool_name="run_pytest",
                result_status="success",
                exit_code=pytest_exit_code,
                sequence_index=validation_offset + 2,
            ),
        ),
        tool_results=(),
        inspected_git_status=inspected_git_status,
        inspected_git_diff=inspected_git_diff,
        last_workspace_change_sequence_index=change_index,
        latest_git_status_sequence_index=(git_offset if inspected_git_status else None),
        latest_git_diff_sequence_index=(git_offset + 1 if inspected_git_diff else None),
    )


def assert_no_worktree_mutation(
    source: Path,
    target: Path,
    branch_name: str,
) -> None:
    """Require complete absence of branch, target, and source changes."""

    assert not target.exists()
    assert run_git(source, "branch", "--list", branch_name).stdout == ""
    assert run_git(source, "status", "--short").stdout == ""


def test_runs_isolated_task_and_creates_verified_local_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Keep the source unchanged while committing the isolated task result."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"
    original_head = run_git(source, "rev-parse", "HEAD").stdout.strip()
    captured = {}

    def create_isolated(session_id, runtime, worktree, *, max_tool_rounds):
        captured["session_id"] = session_id
        captured["runtime"] = runtime
        captured["worktree"] = worktree
        captured["max_tool_rounds"] = max_tool_rounds
        return SimpleNamespace(
            worktree=worktree,
            session=object(),
        )

    def run_coding(
        session,
        prompt,
        *,
        tool_approval_handler,
        tool_round_observer,
        acceptance_criteria,
    ):
        assert session is not None
        assert prompt == "Correct the add implementation."
        assert callable(tool_approval_handler)
        assert tool_round_observer is None
        assert tuple(acceptance_criteria) == (
            "Implement the requested behavior with bounded workspace changes.",
            "Run Ruff formatting and static analysis and resolve introduced issues.",
            "Run pytest and resolve introduced regressions.",
            "Inspect the final Git status and diff before reporting completion.",
        )
        worktree = captured["worktree"]
        (worktree.worktree_path / "module.py").write_text(
            "def add(left: int, right: int) -> int:\n    return left + right\n",
            encoding="utf-8",
        )
        return coding_result()

    monkeypatch.setattr(
        "agent_workbench.isolated_coding.create_isolated_agent_session",
        create_isolated,
    )
    monkeypatch.setattr(
        "agent_workbench.isolated_coding.run_autonomous_coding_task",
        run_coding,
    )

    result = run_isolated_autonomous_workflow(
        SessionId("isolated-workflow"),
        configuration(source),
        "agent/fix-add",
        target,
        "Correct the add implementation.",
        "fix: correct add implementation",
        worktree_approval_handler=approve,
        tool_approval_handler=approve,
        commit_approval_handler=approve,
    )

    assert isinstance(result, IsolatedAutonomousWorkflowResult)
    assert not hasattr(result, "__dict__")
    with pytest.raises(FrozenInstanceError):
        result.coding_result = coding_result()  # type: ignore[misc]

    assert captured["session_id"] == SessionId("isolated-workflow")
    assert captured["runtime"] == configuration(source)
    assert captured["max_tool_rounds"] == 16
    assert result.worktree is captured["worktree"]
    assert result.commit_result.branch_name == "agent/fix-add"
    assert result.commit_result.commit_message == "fix: correct add implementation"
    assert result.commit_result.paths == ("module.py",)
    assert result.final_worktree_state.clean is True
    assert result.final_worktree_state.head == result.commit_result.new_head

    assert run_git(source, "rev-parse", "HEAD").stdout.strip() == original_head
    assert (
        (source / "module.py")
        .read_text(encoding="utf-8")
        .endswith("return left - right\n")
    )
    assert (
        (target / "module.py")
        .read_text(encoding="utf-8")
        .endswith("return left + right\n")
    )
    assert run_git(target, "status", "--short").stdout == ""
    assert (
        run_git(target, "log", "-1", "--pretty=%s").stdout.strip()
        == "fix: correct add implementation"
    )


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (
            coding_result(final_phase=CodingPhase.VERIFY),
            "deterministic DONE phase",
        ),
        (
            coding_result(workspace_change_applied=False),
            "successful approved workspace change",
        ),
        (
            coding_result(validation_succeeded=False),
            "successful Ruff and pytest validation after the latest workspace change",
        ),
        (
            coding_result(validation_after_change=False),
            "successful Ruff and pytest validation after the latest workspace change",
        ),
        (
            coding_result(inspected_git_status=False),
            "final Git status and diff after the latest workspace change",
        ),
        (
            coding_result(inspected_git_diff=False),
            "final Git status and diff after the latest workspace change",
        ),
        (
            coding_result(git_inspection_after_change=False),
            "final Git status and diff after the latest workspace change",
        ),
    ],
)
def test_failed_commit_gate_preserves_dirty_worktree_without_commit(
    tmp_path: Path,
    monkeypatch,
    result: AutonomousCodingResult,
    message: str,
) -> None:
    """Preserve isolated changes when validation or inspection is incomplete."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"
    original_head = run_git(source, "rev-parse", "HEAD").stdout.strip()
    captured = {}

    def create_isolated(_session_id, _runtime, worktree, *, max_tool_rounds):
        assert max_tool_rounds == 16
        captured["worktree"] = worktree
        return SimpleNamespace(
            worktree=worktree,
            session=object(),
        )

    def run_coding(_session, _prompt, **_kwargs):
        worktree = captured["worktree"]
        (worktree.worktree_path / "module.py").write_text(
            "def add(left: int, right: int) -> int:\n    return left + right\n",
            encoding="utf-8",
        )
        return result

    def reject_commit_planning(*_args, **_kwargs):
        raise AssertionError("commit planning must not run")

    monkeypatch.setattr(
        "agent_workbench.isolated_coding.create_isolated_agent_session",
        create_isolated,
    )
    monkeypatch.setattr(
        "agent_workbench.isolated_coding.run_autonomous_coding_task",
        run_coding,
    )
    monkeypatch.setattr(
        "agent_workbench.isolated_coding.plan_isolated_commit",
        reject_commit_planning,
    )

    with pytest.raises(CompletionError, match=message):
        run_isolated_autonomous_workflow(
            SessionId("failed-gate"),
            configuration(source),
            "agent/failed-gate",
            target,
            "Correct the add implementation.",
            "fix: should not be created",
            worktree_approval_handler=approve,
            tool_approval_handler=approve,
            commit_approval_handler=approve,
        )

    assert target.exists()
    assert run_git(target, "status", "--short").stdout == " M module.py\n"
    assert run_git(source, "rev-parse", "HEAD").stdout.strip() == original_head
    assert run_git(source, "branch", "--list", "agent/failed-gate").stdout.strip()
    assert run_git(target, "log", "-1", "--pretty=%s").stdout.strip() == "initial"


def test_deterministic_coding_failure_never_plans_a_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Preserve the isolated worktree before any commit planning on failure."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"
    captured = {}

    def create_isolated(_session_id, _runtime, worktree, *, max_tool_rounds):
        assert max_tool_rounds == 16
        captured["worktree"] = worktree
        return SimpleNamespace(worktree=worktree, session=object())

    def fail_coding(_session, _prompt, **_kwargs):
        worktree = captured["worktree"]
        (worktree.worktree_path / "module.py").write_text(
            "def add(left: int, right: int) -> int:\n    return left * right\n",
            encoding="utf-8",
        )
        raise CompletionError(
            "Deterministic coding failed in phase VALIDATE: pytest failed. "
            "repair_attempts=2, completion_continuations=0."
        )

    commit_planning = Mock(side_effect=AssertionError("commit planning must not run"))
    monkeypatch.setattr(
        "agent_workbench.isolated_coding.create_isolated_agent_session",
        create_isolated,
    )
    monkeypatch.setattr(
        "agent_workbench.isolated_coding.run_autonomous_coding_task",
        fail_coding,
    )
    monkeypatch.setattr(
        "agent_workbench.isolated_coding.plan_isolated_commit",
        commit_planning,
    )

    with pytest.raises(
        CompletionError,
        match=r"Autonomous coding failed: Deterministic coding failed in phase VALIDATE",
    ):
        run_isolated_autonomous_workflow(
            SessionId("deterministic-failure"),
            configuration(source),
            "agent/deterministic-failure",
            target,
            "Correct the add implementation.",
            "fix: must not be created",
            worktree_approval_handler=approve,
            tool_approval_handler=approve,
            commit_approval_handler=approve,
        )

    commit_planning.assert_not_called()
    assert target.exists()
    assert run_git(target, "status", "--short").stdout == " M module.py\n"
    assert run_git(target, "log", "-1", "--pretty=%s").stdout.strip() == "initial"


@pytest.mark.parametrize(
    ("session_id", "disable_actions", "criteria", "message", "error"),
    [
        (
            "invalid-session",
            False,
            ("criterion",),
            "fix: valid",
            "requires a SessionId",
        ),
        (
            SessionId("disabled-actions"),
            True,
            ("criterion",),
            "fix: valid",
            "requires controlled actions",
        ),
        (
            SessionId("invalid-criteria"),
            False,
            "not a criteria collection",
            "fix: valid",
            "task specification is invalid",
        ),
        (
            SessionId("invalid-message"),
            False,
            ("criterion",),
            "-invalid",
            "must not begin",
        ),
        (
            SessionId("oversized-message"),
            False,
            ("criterion",),
            "x" * (MAX_COMMIT_MESSAGE_BYTES + 1),
            "byte limit",
        ),
    ],
)
def test_invalid_inputs_are_rejected_before_worktree_creation(
    tmp_path: Path,
    session_id: object,
    disable_actions: bool,
    criteria: object,
    message: str,
    error: str,
) -> None:
    """Perform no Git mutation when any workflow preflight input is invalid."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"
    runtime = configuration(source)
    if disable_actions:
        runtime = replace(runtime, enable_actions=False)

    with pytest.raises(ConfigurationError, match=error):
        run_isolated_autonomous_workflow(
            session_id,  # type: ignore[arg-type]
            runtime,
            "agent/invalid",
            target,
            "Correct the add implementation.",
            message,
            worktree_approval_handler=approve,
            tool_approval_handler=approve,
            commit_approval_handler=approve,
            acceptance_criteria=criteria,  # type: ignore[arg-type]
        )

    assert_no_worktree_mutation(source, target, "agent/invalid")


def test_invalid_handler_is_rejected_before_worktree_creation(
    tmp_path: Path,
) -> None:
    """Reject an invalid approval callback before any Git mutation."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"

    with pytest.raises(ConfigurationError, match="tool approval handler"):
        run_isolated_autonomous_workflow(
            SessionId("invalid-handler"),
            configuration(source),
            "agent/invalid-handler",
            target,
            "Correct the add implementation.",
            "fix: valid",
            worktree_approval_handler=approve,
            tool_approval_handler=None,  # type: ignore[arg-type]
            commit_approval_handler=approve,
        )

    assert_no_worktree_mutation(source, target, "agent/invalid-handler")


def test_isolated_session_failure_preserves_created_worktree(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Preserve the approved worktree when isolated session construction fails."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"

    def fail_session(*_args, **_kwargs):
        raise ConfigurationError("provider construction failed.")

    monkeypatch.setattr(
        "agent_workbench.isolated_coding.create_isolated_agent_session",
        fail_session,
    )

    with pytest.raises(
        CompletionError,
        match="Isolated session construction failed",
    ):
        run_isolated_autonomous_workflow(
            SessionId("session-failure"),
            configuration(source),
            "agent/session-failure",
            target,
            "Correct the add implementation.",
            "fix: valid",
            worktree_approval_handler=approve,
            tool_approval_handler=approve,
            commit_approval_handler=approve,
        )

    assert target.exists()
    assert run_git(target, "status", "--short").stdout == ""
    assert run_git(source, "branch", "--list", "agent/session-failure").stdout.strip()
    assert run_git(source, "status", "--short").stdout == ""
