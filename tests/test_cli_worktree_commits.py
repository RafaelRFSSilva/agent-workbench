"""Tests for explicitly approved isolated commits in the CLI."""

from unittest.mock import Mock

import pytest

from agent_workbench.cli import _prompt_for_isolated_commit_approval
from agent_workbench.tools import ToolApprovalDecision
from agent_workbench.worktree_commits import (
    IsolatedCommitAction,
    IsolatedCommitApprovalRequest,
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


def test_commit_approval_rejects_invalid_preview_without_prompt(monkeypatch) -> None:
    """Default deny before prompting when the immutable preview is unavailable."""

    prompt = Mock(side_effect=AssertionError("approval prompt must not run"))
    monkeypatch.setattr("builtins.input", prompt)
    request = IsolatedCommitApprovalRequest(
        IsolatedCommitAction.CREATE,
        None,  # type: ignore[arg-type]
    )

    assert _prompt_for_isolated_commit_approval(request) is ToolApprovalDecision.DENY
    prompt.assert_not_called()
