"""Tests for explicitly approved isolated commits in the CLI."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_workbench.arguments import RuntimeConfiguration
from agent_workbench.cli import (
    _prompt_for_isolated_commit_approval,
    main,
)
from agent_workbench.errors import CompletionError, ConfigurationError
from agent_workbench.tools import ToolApprovalDecision
from agent_workbench.worktree_commits import (
    IsolatedCommitAction,
    IsolatedCommitApprovalRequest,
)
from agent_workbench.worktrees import (
    WorktreeAction,
    WorktreeApprovalRequest,
    WorktreeState,
)


def _commit_preview() -> dict[str, object]:
    return {
        "action": "create_isolated_commit",
        "branch": "agent/task",
        "old_head": "a" * 40,
        "commit_message": "fix: exact\n\nbody",
        "operation_count": 2,
        "added_count": 1,
        "modified_count": 1,
        "total_changed_lines": 4,
        "paths": ["new.py", "tracked.py"],
        "changes": [
            {
                "path": "new.py",
                "operation": "add",
                "old_size_bytes": 0,
                "new_size_bytes": 10,
                "changed_lines": 1,
                "diff": "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+value = 1\n",
            },
            {
                "path": "tracked.py",
                "operation": "modify",
                "old_size_bytes": 10,
                "new_size_bytes": 12,
                "changed_lines": 3,
                "diff": (
                    "--- a/tracked.py\n+++ b/tracked.py\n"
                    "@@ -1 +1 @@\n-old = 1\n+new = 2\n"
                ),
            },
        ],
        "diff_fingerprint": "f" * 64,
        "command": "git add -- <approved paths> && git commit",
        "guarantees": ["local isolated branch only"],
    }


def _creation_request() -> WorktreeApprovalRequest:
    return WorktreeApprovalRequest(
        WorktreeAction.CREATE,
        {
            "action": "create_worktree",
            "source_repository": ".",
            "pinned_head": "a" * 40,
            "branch_name": "agent/task",
            "target": "../isolated",
            "command": ["git", "worktree", "add"],
        },
    )


def _removal_request() -> WorktreeApprovalRequest:
    return WorktreeApprovalRequest(
        WorktreeAction.REMOVE,
        {
            "action": "remove_worktree",
            "source_repository": ".",
            "branch_name": "agent/task",
            "worktree_head": "b" * 40,
            "target": "../isolated",
            "command": ["git", "worktree", "remove"],
        },
    )


def _state(*, clean: bool, head: str = "a" * 40) -> WorktreeState:
    return WorktreeState(
        registered=True,
        branch_name="agent/task",
        head=head,
        clean=clean,
        changed_entry_count=0 if clean else 2,
        target_display="../isolated",
    )


def _configure_isolated_main(monkeypatch, inspection) -> SimpleNamespace:
    runtime = RuntimeConfiguration(
        provider_name="ollama",
        model_name="test-model",
        workspace_root=SimpleNamespace(),
        worktree_path=SimpleNamespace(),
        worktree_branch="agent/task",
    )
    handle = SimpleNamespace(
        branch_name="agent/task",
        target_display="../isolated",
    )
    session = Mock(tool_registry=None)
    isolated = SimpleNamespace(worktree=handle, session=session)
    create_worktree = Mock(return_value=handle)

    monkeypatch.setattr(
        "agent_workbench.cli.resolve_runtime_configuration",
        Mock(return_value=runtime),
    )
    monkeypatch.setattr(
        "agent_workbench.cli.plan_git_worktree",
        Mock(return_value=SimpleNamespace()),
    )
    monkeypatch.setattr("agent_workbench.cli.create_git_worktree", create_worktree)
    monkeypatch.setattr(
        "agent_workbench.cli.create_isolated_agent_session",
        Mock(return_value=isolated),
    )
    monkeypatch.setattr("agent_workbench.cli.run_cli", Mock())
    monkeypatch.setattr(
        "agent_workbench.cli.inspect_git_worktree",
        inspection,
    )
    return SimpleNamespace(
        handle=handle,
        create_worktree=create_worktree,
        session=session,
    )


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("y", ToolApprovalDecision.APPROVE),
        ("YES", ToolApprovalDecision.APPROVE),
        ("", ToolApprovalDecision.DENY),
        ("approve", ToolApprovalDecision.DENY),
    ],
)
def test_commit_approval_renders_complete_preview_before_one_prompt(
    monkeypatch,
    capsys,
    answer,
    expected,
) -> None:
    """Render every approved path and diff before accepting exact yes values."""

    prompts = []
    rendered_before_prompt = []

    def approve(prompt):
        prompts.append(prompt)
        rendered_before_prompt.append(capsys.readouterr().out)
        return answer

    monkeypatch.setattr("builtins.input", approve)
    request = IsolatedCommitApprovalRequest(
        IsolatedCommitAction.CREATE,
        _commit_preview(),
    )

    decision = _prompt_for_isolated_commit_approval(request)

    output = rendered_before_prompt[0] + capsys.readouterr().out
    assert decision is expected
    assert prompts == ["Approve isolated commit? [y/N]: "]
    assert "Isolated commit approval required" in output
    assert "agent/task" in output
    assert "a" * 40 in output
    assert "fix: exact\n\nbody" in output
    assert "Operations: 2" in output
    assert "Added: 1" in output
    assert "Modified: 1" in output
    assert "Changed lines: 4" in output
    assert output.index("new.py") < output.index("tracked.py")
    assert "--- /dev/null\n+++ b/new.py" in output
    assert "--- a/tracked.py\n+++ b/tracked.py" in output
    assert "+++ b/tracked.py" in rendered_before_prompt[0]
    assert "isolated local branch" in output
    assert "exact listed paths" in output
    assert "No amend, merge, push, or branch deletion" in output
    assert "manual recovery" in output
    assert "destructive automatic cleanup" in output
    assert "/tmp/" not in output


@pytest.mark.parametrize("failure", [EOFError(), KeyboardInterrupt()])
def test_commit_approval_interruption_denies(monkeypatch, failure) -> None:
    """Treat interrupted final commit approval as an explicit denial."""

    monkeypatch.setattr("builtins.input", Mock(side_effect=failure))
    request = IsolatedCommitApprovalRequest(
        IsolatedCommitAction.CREATE,
        _commit_preview(),
    )

    assert _prompt_for_isolated_commit_approval(request) is ToolApprovalDecision.DENY


@pytest.mark.parametrize("message_input", ["", EOFError(), KeyboardInterrupt()])
def test_dirty_exit_without_message_preserves_without_planning(
    monkeypatch,
    capsys,
    message_input,
) -> None:
    """Keep the dirty worktree untouched for blank or interrupted messages."""

    configured = _configure_isolated_main(
        monkeypatch,
        Mock(return_value=_state(clean=False)),
    )
    planner = Mock(side_effect=AssertionError("planner must not run"))
    creator = Mock(side_effect=AssertionError("creator must not run"))
    remover = Mock(side_effect=AssertionError("remover must not run"))
    monkeypatch.setattr(
        "agent_workbench.worktree_commits.plan_isolated_commit",
        planner,
    )
    monkeypatch.setattr(
        "agent_workbench.worktree_commits.create_isolated_commit",
        creator,
    )
    monkeypatch.setattr("agent_workbench.cli.remove_git_worktree", remover)
    inputs = [message_input]

    def answer(prompt):
        selected = inputs.pop(0)
        if isinstance(selected, BaseException):
            raise selected
        return selected

    monkeypatch.setattr("builtins.input", answer)

    main([])

    planner.assert_not_called()
    creator.assert_not_called()
    remover.assert_not_called()
    configured.create_worktree.assert_called_once()
    output = capsys.readouterr().out
    assert "Changed entries: 2" in output
    assert "preserved for manual review" in output
    assert "Remove clean isolated worktree?" not in output


@pytest.mark.parametrize(
    "reason",
    [
        "deletions are unsupported",
        "binary file is unsupported",
        "symlinks are unsupported",
        "index must be clean",
        "local Git identity is missing",
        "complete preview exceeds the limit",
    ],
)
def test_planning_rejection_preserves_without_approval_or_removal(
    monkeypatch,
    capsys,
    reason,
) -> None:
    """Render one safe planning failure without staging or removal."""

    configured = _configure_isolated_main(
        monkeypatch,
        Mock(return_value=_state(clean=False)),
    )
    planner = Mock(side_effect=ConfigurationError(reason))
    creator = Mock(side_effect=AssertionError("creator must not run"))
    remover = Mock(side_effect=AssertionError("remover must not run"))
    monkeypatch.setattr(
        "agent_workbench.worktree_commits.plan_isolated_commit",
        planner,
    )
    monkeypatch.setattr(
        "agent_workbench.worktree_commits.create_isolated_commit",
        creator,
    )
    monkeypatch.setattr("agent_workbench.cli.remove_git_worktree", remover)
    answers = iter(["fix: requested"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    main([])

    planner.assert_called_once_with(
        configured.handle,
        "fix: requested",
    )
    creator.assert_not_called()
    remover.assert_not_called()
    output = capsys.readouterr().out
    assert reason in output
    assert "preserved for manual recovery" in output
    assert "Approve isolated commit?" not in output
    assert "Remove clean isolated worktree?" not in output
    assert "Traceback" not in output


@pytest.mark.parametrize("approval", ["", "no", "arbitrary"])
def test_commit_denial_preserves_exact_message_and_dirty_worktree(
    monkeypatch,
    capsys,
    approval,
) -> None:
    """Pass the raw message unchanged and create at most through denial."""

    configured = _configure_isolated_main(
        monkeypatch,
        Mock(return_value=_state(clean=False)),
    )
    plan = SimpleNamespace(preview=_commit_preview())
    planner = Mock(return_value=plan)
    create_calls = []

    def create_commit(supplied_plan, handler):
        create_calls.append(supplied_plan)
        request = IsolatedCommitApprovalRequest(
            IsolatedCommitAction.CREATE,
            supplied_plan.preview,
        )
        if handler(request) is ToolApprovalDecision.DENY:
            raise CompletionError("Isolated commit approval was denied.")
        raise AssertionError("approval unexpectedly succeeded")

    monkeypatch.setattr(
        "agent_workbench.worktree_commits.plan_isolated_commit",
        planner,
    )
    monkeypatch.setattr(
        "agent_workbench.worktree_commits.create_isolated_commit",
        create_commit,
    )
    remover = Mock(side_effect=AssertionError("remover must not run"))
    monkeypatch.setattr("agent_workbench.cli.remove_git_worktree", remover)
    message = "fix: exact message  "
    answers = iter([message, approval])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    main([])

    planner.assert_called_once_with(configured.handle, message)
    assert create_calls == [plan]
    remover.assert_not_called()
    output = capsys.readouterr().out
    assert output.count("Isolated commit approval required") == 1
    assert "approval was denied" in output
    assert "preserved for manual recovery" in output


@pytest.mark.parametrize("remove_answer", ["yes", "no"])
def test_successful_commit_reinspects_and_separately_offers_removal(
    monkeypatch,
    capsys,
    remove_answer,
) -> None:
    """Verify safe result output before an independent clean-removal decision."""

    inspection = Mock(
        side_effect=[_state(clean=False), _state(clean=True, head="b" * 40)]
    )
    configured = _configure_isolated_main(monkeypatch, inspection)
    plan = SimpleNamespace(preview=_commit_preview())
    planner = Mock(return_value=plan)
    result = SimpleNamespace(
        branch_name="agent/task",
        old_head="a" * 40,
        new_head="b" * 40,
        commit_message="fix: exact",
        paths=("new.py", "tracked.py"),
        operation_count=2,
        added_count=1,
        modified_count=1,
    )
    commit_calls = []

    def create_commit(supplied_plan, handler):
        commit_calls.append(supplied_plan)
        assert (
            handler(
                IsolatedCommitApprovalRequest(
                    IsolatedCommitAction.CREATE,
                    supplied_plan.preview,
                )
            )
            is ToolApprovalDecision.APPROVE
        )
        return result

    removal_plan = SimpleNamespace()
    removal_planner = Mock(return_value=removal_plan)
    removal_decisions = []

    def remove_worktree(supplied_plan, handler):
        assert supplied_plan is removal_plan
        decision = handler(_removal_request())
        removal_decisions.append(decision)
        if decision is ToolApprovalDecision.DENY:
            raise CompletionError("Worktree removal approval was denied.")

    monkeypatch.setattr(
        "agent_workbench.worktree_commits.plan_isolated_commit",
        planner,
    )
    monkeypatch.setattr(
        "agent_workbench.worktree_commits.create_isolated_commit",
        create_commit,
    )
    monkeypatch.setattr(
        "agent_workbench.cli.plan_git_worktree_removal",
        removal_planner,
    )
    monkeypatch.setattr("agent_workbench.cli.remove_git_worktree", remove_worktree)
    answers = iter(["fix: exact", "yes", remove_answer])
    prompts = []
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: prompts.append(prompt) or next(answers),
    )

    main([])

    planner.assert_called_once_with(configured.handle, "fix: exact")
    assert commit_calls == [plan]
    assert inspection.call_count == 2
    removal_planner.assert_called_once_with(configured.handle)
    assert removal_decisions == [
        (
            ToolApprovalDecision.APPROVE
            if remove_answer == "yes"
            else ToolApprovalDecision.DENY
        )
    ]
    output = capsys.readouterr().out
    assert "Isolated commit created" in output
    assert "b" * 40 in output
    assert "Primary working tree unchanged" in output
    assert prompts == [
        "Commit message (blank to preserve worktree): ",
        "Approve isolated commit? [y/N]: ",
        "Remove clean isolated worktree? [y/N]: ",
    ]
    if remove_answer == "yes":
        assert "Clean isolated worktree removed" in output
        assert "local branch agent/task remains" in output
    else:
        assert "approval was denied" in output
        assert "local branch agent/task remains" in output


@pytest.mark.parametrize(
    "reason",
    [
        "Exact staging failed; manual inspection is required (staged paths: new.py).",
        "Local commit creation failed; manual inspection is required.",
        "Commit verification failed; HEAD changed: yes.",
    ],
)
def test_commit_failure_preserves_state_without_clean_removal(
    monkeypatch,
    capsys,
    reason,
) -> None:
    """Expose safe execution recovery details without a removal attempt."""

    _configure_isolated_main(
        monkeypatch,
        Mock(return_value=_state(clean=False)),
    )
    plan = SimpleNamespace(preview=_commit_preview())
    monkeypatch.setattr(
        "agent_workbench.worktree_commits.plan_isolated_commit",
        Mock(return_value=plan),
    )

    def fail_commit(supplied_plan, handler):
        assert (
            handler(
                IsolatedCommitApprovalRequest(
                    IsolatedCommitAction.CREATE,
                    supplied_plan.preview,
                )
            )
            is ToolApprovalDecision.APPROVE
        )
        raise CompletionError(reason)

    monkeypatch.setattr(
        "agent_workbench.worktree_commits.create_isolated_commit",
        fail_commit,
    )
    remover = Mock(side_effect=AssertionError("remover must not run"))
    monkeypatch.setattr("agent_workbench.cli.remove_git_worktree", remover)
    answers = iter(["fix: exact", "yes"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    main([])

    remover.assert_not_called()
    output = capsys.readouterr().out
    assert reason in output
    assert "preserved for manual recovery" in output
    assert "Remove clean isolated worktree?" not in output
    assert "Traceback" not in output


def test_clean_isolated_exit_never_loads_commit_flow(monkeypatch) -> None:
    """Keep the existing clean-removal path independent of commit planning."""

    _configure_isolated_main(
        monkeypatch,
        Mock(return_value=_state(clean=True)),
    )
    planner = Mock(side_effect=AssertionError("planner must not run"))
    monkeypatch.setattr(
        "agent_workbench.worktree_commits.plan_isolated_commit",
        planner,
    )
    monkeypatch.setattr(
        "agent_workbench.cli.plan_git_worktree_removal",
        Mock(side_effect=CompletionError("preserve clean worktree")),
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "yes")

    main([])

    planner.assert_not_called()
