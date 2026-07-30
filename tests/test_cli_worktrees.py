"""Tests for supervised Git worktree isolation in the CLI."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_workbench.arguments import RuntimeConfiguration
from agent_workbench.cli import (
    AUTONOMOUS_MAX_TOOL_ROUNDS,
    _display_tool_round,
    _prompt_for_isolated_commit_approval,
    _prompt_for_tool_approval,
    _prompt_for_worktree_approval,
    main,
)
from agent_workbench.coding_loop import CodingPhase
from agent_workbench.errors import CompletionError
from agent_workbench.session import SessionId
from agent_workbench.tools import ToolApprovalDecision
from agent_workbench.worktrees import (
    WorktreeAction,
    WorktreeApprovalRequest,
)


def configuration(**overrides: object) -> RuntimeConfiguration:
    """Create one resolved runtime configuration for CLI orchestration."""

    values: dict[str, object] = {
        "provider_name": "ollama",
        "model_name": "test-model",
    }
    values.update(overrides)
    return RuntimeConfiguration(**values)  # type: ignore[arg-type]


def creation_request() -> WorktreeApprovalRequest:
    """Create one complete safe worktree creation approval request."""

    return WorktreeApprovalRequest(
        WorktreeAction.CREATE,
        {
            "action": "create_worktree",
            "source_repository": ".",
            "pinned_head": "a" * 40,
            "branch_name": "agent/task",
            "target": "../isolated",
            "command": [
                "git",
                "-C",
                ".",
                "worktree",
                "add",
                "-b",
                "agent/task",
                "../isolated",
                "a" * 40,
            ],
            "scope": "Creates one local branch and one local worktree only.",
            "exclusions": "No commit, merge, push, or branch deletion will occur.",
        },
    )


def removal_request() -> WorktreeApprovalRequest:
    """Create one complete safe clean-removal approval request."""

    return WorktreeApprovalRequest(
        WorktreeAction.REMOVE,
        {
            "action": "remove_worktree",
            "source_repository": ".",
            "branch_name": "agent/task",
            "worktree_head": "a" * 40,
            "target": "../isolated",
            "command": [
                "git",
                "-C",
                ".",
                "worktree",
                "remove",
                "../isolated",
            ],
            "scope": "Removes only the clean worktree.",
            "branch": "The local branch will remain.",
            "exclusions": "No force or branch deletion.",
        },
    )


@pytest.mark.parametrize(
    ("action", "answer", "expected_prompt", "expected"),
    [
        (
            WorktreeAction.CREATE,
            "y",
            "Approve worktree creation? [y/N]: ",
            ToolApprovalDecision.APPROVE,
        ),
        (
            WorktreeAction.CREATE,
            "YES",
            "Approve worktree creation? [y/N]: ",
            ToolApprovalDecision.APPROVE,
        ),
        (
            WorktreeAction.CREATE,
            "",
            "Approve worktree creation? [y/N]: ",
            ToolApprovalDecision.DENY,
        ),
        (
            WorktreeAction.REMOVE,
            "yes",
            "Remove clean isolated worktree? [y/N]: ",
            ToolApprovalDecision.APPROVE,
        ),
        (
            WorktreeAction.REMOVE,
            "anything",
            "Remove clean isolated worktree? [y/N]: ",
            ToolApprovalDecision.DENY,
        ),
    ],
)
def test_worktree_approval_renders_complete_safe_preview_and_defaults_deny(
    monkeypatch,
    capsys,
    action,
    answer,
    expected_prompt,
    expected,
) -> None:
    """Render exact lifecycle effects and accept only explicit yes values."""

    prompts = []
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: prompts.append(prompt) or answer,
    )
    request = (
        creation_request() if action is WorktreeAction.CREATE else removal_request()
    )

    decision = _prompt_for_worktree_approval(request)

    output = capsys.readouterr().out
    assert decision is expected
    assert prompts == [expected_prompt]
    assert "agent/task" in output
    assert "../isolated" in output
    assert "Fixed command:" in output
    assert "/tmp/" not in output
    if action is WorktreeAction.CREATE:
        assert "local branch and worktree will be created" in output
        assert "No commit, merge, or push" in output
        assert "primary source working tree must remain clean" in output
        assert "partial creation" in output
    else:
        assert "local branch will remain" in output
        assert "No force" in output


@pytest.mark.parametrize("failure", [EOFError(), KeyboardInterrupt()])
def test_worktree_approval_input_interruption_denies(
    monkeypatch,
    failure,
) -> None:
    """Treat unavailable or interrupted worktree approval input as denial."""

    monkeypatch.setattr("builtins.input", Mock(side_effect=failure))

    assert (
        _prompt_for_worktree_approval(creation_request()) is ToolApprovalDecision.DENY
    )


def isolated_workflow_result() -> SimpleNamespace:
    """Create one successful result for CLI presentation tests."""

    return SimpleNamespace(
        worktree=SimpleNamespace(target_display="../isolated"),
        coding_result=SimpleNamespace(
            assistant_summary="Corrected and validated the implementation.",
            final_phase=CodingPhase.DONE,
            tool_round_count=6,
            repair_attempt_count=1,
            completion_continuation_count=2,
            validation_succeeded=True,
            inspected_git_status=True,
            inspected_git_diff=True,
            workspace_change_applied=True,
        ),
        commit_result=SimpleNamespace(
            branch_name="agent/task",
            new_head="b" * 40,
            operation_count=2,
        ),
        final_worktree_state=SimpleNamespace(clean=True),
    )


def test_main_default_path_never_runs_isolated_workflow(monkeypatch) -> None:
    """Preserve the existing source-session path without isolation options."""

    runtime = configuration()
    session = Mock(tool_registry=None)
    create_session = Mock(return_value=session)
    run_cli = Mock()
    isolated_workflow = Mock(
        side_effect=AssertionError("isolated workflow must not run")
    )
    monkeypatch.setattr("agent_workbench.cli.load_environment", Mock())
    monkeypatch.setattr(
        "agent_workbench.cli.resolve_runtime_configuration",
        Mock(return_value=runtime),
    )
    monkeypatch.setattr("agent_workbench.cli.create_agent_session", create_session)
    monkeypatch.setattr("agent_workbench.cli.run_cli", run_cli)
    monkeypatch.setattr(
        "agent_workbench.cli.run_isolated_autonomous_workflow",
        isolated_workflow,
    )

    main([])

    create_session.assert_called_once_with(SessionId("cli-session"), runtime)
    run_cli.assert_called_once_with(session, agent_profile=None)
    isolated_workflow.assert_not_called()


@pytest.mark.parametrize(
    ("show_tool_traces", "expected_observer"),
    [
        (False, None),
        (True, _display_tool_round),
    ],
)
def test_main_delegates_complete_isolated_workflow_once(
    monkeypatch,
    capsys,
    show_tool_traces,
    expected_observer,
) -> None:
    """Forward exact CLI inputs and approval handlers to one orchestrator."""

    runtime = configuration(
        workspace_root=Path("/source"),
        enable_actions=True,
        show_tool_traces=show_tool_traces,
        worktree_path=Path("/isolated"),
        worktree_branch="agent/task",
    )
    workflow = Mock(return_value=isolated_workflow_result())
    create_session = Mock(
        side_effect=AssertionError("source session must not be created")
    )
    monkeypatch.setattr("agent_workbench.cli.load_environment", Mock())
    monkeypatch.setattr(
        "agent_workbench.cli.resolve_runtime_configuration",
        Mock(return_value=runtime),
    )
    monkeypatch.setattr(
        "agent_workbench.cli.run_isolated_autonomous_workflow",
        workflow,
    )
    monkeypatch.setattr("agent_workbench.cli.create_agent_session", create_session)
    arguments = [
        "--workspace",
        "/source",
        "--enable-actions",
        "--task",
        "Correct the implementation.",
        "--worktree-path",
        "/isolated",
        "--worktree-branch",
        "agent/task",
        "--commit-message",
        "fix: exact message  ",
    ]
    if show_tool_traces:
        arguments.append("--show-tool-traces")

    main(arguments)

    workflow.assert_called_once_with(
        SessionId("cli-session"),
        runtime,
        "agent/task",
        Path("/isolated"),
        "Correct the implementation.",
        "fix: exact message  ",
        worktree_approval_handler=_prompt_for_worktree_approval,
        tool_approval_handler=_prompt_for_tool_approval,
        commit_approval_handler=_prompt_for_isolated_commit_approval,
        tool_round_observer=expected_observer,
        max_tool_rounds=AUTONOMOUS_MAX_TOOL_ROUNDS,
    )
    create_session.assert_not_called()
    output = capsys.readouterr().out
    assert "Assistant: Corrected and validated the implementation." in output
    assert "Isolated autonomous workflow result:" in output
    assert "Worktree: ../isolated" in output
    assert "Branch: agent/task" in output
    assert "New isolated HEAD: " + "b" * 40 in output
    evidence = (
        "  Final phase: DONE\n"
        "  Tool rounds: 6\n"
        "  Workspace change applied: yes\n"
        "  Repair attempts: 1\n"
        "  Completion continuations: 2\n"
        "  Validation succeeded: yes\n"
        "  Git status inspected: yes\n"
        "  Git diff inspected: yes\n"
    )
    assert evidence in output
    assert "Final worktree clean: yes" in output
    assert "Primary working tree unchanged" in output
    assert "Worktree and local branch preserved" in output
    assert "No merge, push, worktree removal, or branch deletion" in output


def test_main_forwards_custom_tool_round_limit_to_isolated_workflow(
    monkeypatch,
) -> None:
    """Forward one resolved custom limit to isolated orchestration."""

    runtime = configuration(
        workspace_root=Path("/source"),
        enable_actions=True,
        max_tool_rounds=48,
        worktree_path=Path("/isolated"),
        worktree_branch="agent/task",
    )
    workflow = Mock(return_value=isolated_workflow_result())
    monkeypatch.setattr("agent_workbench.cli.load_environment", Mock())
    monkeypatch.setattr(
        "agent_workbench.cli.resolve_runtime_configuration",
        Mock(return_value=runtime),
    )
    monkeypatch.setattr(
        "agent_workbench.cli.run_isolated_autonomous_workflow",
        workflow,
    )
    monkeypatch.setattr(
        "agent_workbench.cli.create_agent_session",
        Mock(side_effect=AssertionError("source session must not be created")),
    )

    main(
        [
            "--workspace",
            "/source",
            "--enable-actions",
            "--task",
            "Correct the implementation.",
            "--max-tool-rounds",
            "48",
            "--worktree-path",
            "/isolated",
            "--worktree-branch",
            "agent/task",
            "--commit-message",
            "fix: exact",
        ]
    )

    assert workflow.call_args.kwargs["max_tool_rounds"] == 48


def test_main_reports_isolated_workflow_failure_without_fallback(
    monkeypatch,
    capsys,
) -> None:
    """Render one safe workflow error without starting a source session."""

    runtime = configuration(
        workspace_root=Path("/source"),
        enable_actions=True,
        worktree_path=Path("/isolated"),
        worktree_branch="agent/task",
    )
    workflow = Mock(
        side_effect=CompletionError(
            "Autonomous coding failed. The worktree and local branch were "
            "preserved for manual recovery."
        )
    )
    create_session = Mock(
        side_effect=AssertionError("source session must not be created")
    )
    monkeypatch.setattr("agent_workbench.cli.load_environment", Mock())
    monkeypatch.setattr(
        "agent_workbench.cli.resolve_runtime_configuration",
        Mock(return_value=runtime),
    )
    monkeypatch.setattr(
        "agent_workbench.cli.run_isolated_autonomous_workflow",
        workflow,
    )
    monkeypatch.setattr("agent_workbench.cli.create_agent_session", create_session)

    main(
        [
            "--workspace",
            "/source",
            "--enable-actions",
            "--task",
            "Correct the implementation.",
            "--worktree-path",
            "/isolated",
            "--worktree-branch",
            "agent/task",
            "--commit-message",
            "fix: exact",
        ]
    )

    create_session.assert_not_called()
    output = capsys.readouterr().out
    assert "Isolated autonomous workflow error:" in output
    assert "preserved for manual recovery" in output
    assert "Traceback" not in output
    assert "Isolated autonomous workflow result:" not in output


@pytest.mark.parametrize(
    ("workspace_change_applied", "expected"),
    [
        (True, "yes"),
        (False, "no"),
    ],
)
def test_workspace_change_applied_line_in_cli_output(
    monkeypatch, capsys, workspace_change_applied, expected
):
    runtime = configuration(
        workspace_root=Path("/source"),
        enable_actions=True,
        worktree_path=Path("/isolated"),
        worktree_branch="agent/task",
    )

    def isolated_workflow_result() -> SimpleNamespace:
        return SimpleNamespace(
            worktree=SimpleNamespace(target_display="../isolated"),
            coding_result=SimpleNamespace(
                assistant_summary="Corrected and validated the implementation.",
                final_phase=CodingPhase.DONE,
                tool_round_count=6,
                repair_attempt_count=0,
                completion_continuation_count=0,
                validation_succeeded=True,
                inspected_git_status=True,
                inspected_git_diff=True,
                workspace_change_applied=workspace_change_applied,
            ),
            commit_result=SimpleNamespace(
                branch_name="agent/task",
                new_head="b" * 40,
                operation_count=2,
            ),
            final_worktree_state=SimpleNamespace(clean=True),
        )

    workflow = Mock(return_value=isolated_workflow_result())
    create_session = Mock(
        side_effect=AssertionError("source session must not be created")
    )
    monkeypatch.setattr("agent_workbench.cli.load_environment", Mock())
    monkeypatch.setattr(
        "agent_workbench.cli.resolve_runtime_configuration", Mock(return_value=runtime)
    )
    monkeypatch.setattr(
        "agent_workbench.cli.run_isolated_autonomous_workflow", workflow
    )
    monkeypatch.setattr("agent_workbench.cli.create_agent_session", create_session)

    main(
        [
            "--workspace",
            "/source",
            "--enable-actions",
            "--task",
            "Correct the add implementation.",
            "--worktree-path",
            "/isolated",
            "--worktree-branch",
            "agent/task",
            "--commit-message",
            "fix: correct add implementation",
        ]
    )

    output = capsys.readouterr().out
    assert f"Workspace change applied: {expected}" in output
    idx_ws = output.index(f"Workspace change applied: {expected}")
    idx_val = output.index("Validation succeeded:")
    assert idx_ws < idx_val
