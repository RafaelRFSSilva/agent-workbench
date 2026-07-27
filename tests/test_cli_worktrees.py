"""Tests for supervised Git worktree isolation in the CLI."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from agent_workbench.arguments import RuntimeConfiguration
from agent_workbench.cli import (
    _prompt_for_worktree_approval,
    main,
)
from agent_workbench.errors import CompletionError, ConfigurationError
from agent_workbench.session import SessionId
from agent_workbench.tools import ToolApprovalDecision
from agent_workbench.worktrees import (
    WorktreeAction,
    WorktreeApprovalRequest,
    WorktreeState,
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


def configure_isolated_main(
    monkeypatch,
    *,
    state: WorktreeState,
    enable_actions: bool = False,
    show_tool_traces: bool = False,
):
    """Install deterministic isolation collaborators around main()."""

    runtime = configuration(
        workspace_root=Path("/source"),
        worktree_path=Path("/isolated"),
        worktree_branch="agent/task",
        enable_actions=enable_actions,
        show_tool_traces=show_tool_traces,
    )
    plan = Mock()
    plan.preview = creation_request().preview
    plan.source_head = "a" * 40
    plan.branch_name = "agent/task"
    plan.target_display = "../isolated"
    handle = Mock()
    handle.branch_name = "agent/task"
    handle.target_display = "../isolated"
    handle.worktree_path = Path("/isolated")
    session = Mock()
    session.tool_registry = Mock()
    isolated = Mock()
    isolated.worktree = handle
    isolated.session = session
    removal = Mock()
    removal.preview = removal_request().preview

    monkeypatch.setattr(
        "agent_workbench.cli.resolve_runtime_configuration",
        Mock(return_value=runtime),
    )
    monkeypatch.setattr(
        "agent_workbench.cli.plan_git_worktree",
        Mock(return_value=plan),
    )
    monkeypatch.setattr(
        "agent_workbench.cli.create_isolated_agent_session",
        Mock(return_value=isolated),
    )
    monkeypatch.setattr(
        "agent_workbench.cli.inspect_git_worktree",
        Mock(return_value=state),
    )
    monkeypatch.setattr(
        "agent_workbench.cli.plan_git_worktree_removal",
        Mock(return_value=removal),
    )
    monkeypatch.setattr("agent_workbench.cli.run_cli", Mock())
    monkeypatch.setattr(
        "agent_workbench.cli.create_agent_session",
        Mock(side_effect=AssertionError("source session must not be created")),
    )

    return runtime, plan, handle, session, isolated, removal


def test_main_default_path_never_runs_worktree_code(monkeypatch) -> None:
    """Preserve the exact existing source-session path without isolation options."""

    runtime = configuration()
    session = Mock()
    session.tool_registry = None
    create_session = Mock(return_value=session)
    run_cli = Mock()
    plan = Mock(side_effect=AssertionError("worktree planning must not run"))
    monkeypatch.setattr(
        "agent_workbench.cli.resolve_runtime_configuration",
        Mock(return_value=runtime),
    )
    monkeypatch.setattr("agent_workbench.cli.create_agent_session", create_session)
    monkeypatch.setattr("agent_workbench.cli.plan_git_worktree", plan)
    monkeypatch.setattr("agent_workbench.cli.run_cli", run_cli)

    main([])

    create_session.assert_called_once()
    run_cli.assert_called_once_with(session, agent_profile=None)
    plan.assert_not_called()


def test_main_creates_one_isolated_session_and_preserves_dirty_worktree(
    monkeypatch,
    capsys,
) -> None:
    """Run the prebuilt session once and preserve dirty output for manual review."""

    state = WorktreeState(
        registered=True,
        branch_name="agent/task",
        head="a" * 40,
        clean=False,
        changed_entry_count=2,
        target_display="../isolated",
    )
    runtime, plan, handle, session, isolated, _ = configure_isolated_main(
        monkeypatch,
        state=state,
        enable_actions=True,
        show_tool_traces=True,
    )
    create_calls = []

    def create_worktree(supplied_plan, approval_handler):
        create_calls.append(supplied_plan)
        assert approval_handler(creation_request()) is ToolApprovalDecision.APPROVE
        return handle

    monkeypatch.setattr(
        "agent_workbench.cli.create_git_worktree",
        create_worktree,
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "yes")

    main([])

    assert create_calls == [plan]
    create_isolated = pytest.importorskip(
        "agent_workbench.cli"
    ).create_isolated_agent_session
    create_isolated.assert_called_once_with(
        SessionId("cli-session"),
        runtime,
        handle,
    )
    run_cli = pytest.importorskip("agent_workbench.cli").run_cli
    run_cli.assert_called_once_with(
        session,
        agent_profile=None,
        show_tool_traces=True,
        enable_actions=True,
    )
    output = capsys.readouterr().out
    assert "preserved for manual review" in output
    assert "../isolated" in output
    assert "agent/task" in output
    assert "Changed entries: 2" in output
    assert "inspect, commit, or clean it manually" in output
    assert "Remove clean isolated worktree?" not in output


@pytest.mark.parametrize("remove_answer", ["yes", "no"])
def test_main_offers_separate_clean_removal_and_preserves_branch(
    monkeypatch,
    capsys,
    remove_answer,
) -> None:
    """Approve or deny clean removal independently after the CLI exits."""

    state = WorktreeState(
        registered=True,
        branch_name="agent/task",
        head="a" * 40,
        clean=True,
        changed_entry_count=0,
        target_display="../isolated",
    )
    _, plan, handle, _, _, removal = configure_isolated_main(
        monkeypatch,
        state=state,
    )
    decisions = []

    def create_worktree(_plan, approval_handler):
        assert approval_handler(creation_request()) is ToolApprovalDecision.APPROVE
        return handle

    def remove_worktree(supplied_plan, approval_handler):
        assert supplied_plan is removal
        decision = approval_handler(removal_request())
        decisions.append(decision)
        if decision is ToolApprovalDecision.DENY:
            raise CompletionError("Worktree removal approval was denied.")

    monkeypatch.setattr("agent_workbench.cli.create_git_worktree", create_worktree)
    monkeypatch.setattr("agent_workbench.cli.remove_git_worktree", remove_worktree)
    answers = iter(["yes", remove_answer])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    main([])

    assert decisions == [
        (
            ToolApprovalDecision.APPROVE
            if remove_answer == "yes"
            else ToolApprovalDecision.DENY
        )
    ]
    output = capsys.readouterr().out
    if remove_answer == "yes":
        assert "Clean isolated worktree removed" in output
        assert "local branch agent/task remains" in output
    else:
        assert "approval was denied" in output
        assert "preserved" in output


@pytest.mark.parametrize("failure_point", ["creation", "session", "inspection"])
def test_main_reports_isolation_failures_without_destructive_recovery(
    monkeypatch,
    capsys,
    failure_point,
) -> None:
    """Render safe lifecycle failures and preserve partial or created state."""

    state = WorktreeState(
        registered=True,
        branch_name="agent/task",
        head="a" * 40,
        clean=True,
        changed_entry_count=0,
        target_display="../isolated",
    )
    _, _, handle, _, _, _ = configure_isolated_main(monkeypatch, state=state)

    def create_worktree(_plan, approval_handler):
        assert approval_handler(creation_request()) is ToolApprovalDecision.APPROVE
        if failure_point == "creation":
            raise CompletionError("partial state was preserved for manual recovery.")
        return handle

    monkeypatch.setattr("agent_workbench.cli.create_git_worktree", create_worktree)
    if failure_point == "session":
        monkeypatch.setattr(
            "agent_workbench.cli.create_isolated_agent_session",
            Mock(
                side_effect=ConfigurationError(
                    "worktree ../isolated was preserved for manual recovery."
                )
            ),
        )
    elif failure_point == "inspection":
        monkeypatch.setattr(
            "agent_workbench.cli.inspect_git_worktree",
            Mock(side_effect=CompletionError("worktree state is ambiguous.")),
        )
    monkeypatch.setattr("builtins.input", lambda _prompt: "yes")

    main([])

    output = capsys.readouterr().out
    assert "preserved" in output or "ambiguous" in output
    assert "Traceback" not in output
    assert "/source" not in output
    assert "Target: /isolated" not in output
