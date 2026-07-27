"""Tests for approved local commits inside verified isolated worktrees."""

from dataclasses import FrozenInstanceError
import os
from pathlib import Path
import subprocess

import pytest

from agent_workbench.errors import ConfigurationError
from agent_workbench.tools import ToolApprovalDecision
from agent_workbench.worktree_commits import (
    MAX_COMMIT_CHANGED_LINES,
    MAX_COMMIT_CURRENT_BYTES,
    MAX_COMMIT_FILES,
    MAX_COMMIT_MESSAGE_BYTES,
    MAX_COMMIT_OLD_BYTES,
    MAX_COMMIT_PREVIEW_BYTES,
    MAX_COMMIT_FILE_BYTES,
    MAX_COMMIT_FILE_CHANGED_LINES,
    IsolatedCommitPlan,
    plan_isolated_commit,
)
from agent_workbench.worktrees import (
    WorktreeHandle,
    create_git_worktree,
    plan_git_worktree,
)


def run_git(repository: Path, *arguments: str, check: bool = True):
    """Run Git against one disposable test repository."""

    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=check,
        capture_output=True,
        text=True,
    )


def create_repository(root: Path) -> Path:
    """Create one clean primary repository with local commit identity."""

    root.mkdir()
    run_git(root, "init", "-b", "main")
    run_git(root, "config", "user.name", "Commit Test User")
    run_git(root, "config", "user.email", "commit-test@example.invalid")
    (root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    run_git(root, "add", "tracked.txt")
    run_git(root, "commit", "-m", "initial")
    return root


def create_isolated_worktree(
    tmp_path: Path,
    *,
    branch: str = "agent/task",
) -> tuple[Path, WorktreeHandle]:
    """Create one approved linked worktree from a clean primary source."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"
    plan = plan_git_worktree(source, branch, target)
    handle = create_git_worktree(
        plan,
        lambda _request: ToolApprovalDecision.APPROVE,
    )
    return source, handle


def index_bytes(worktree: Path) -> bytes:
    """Return the linked worktree's real index bytes."""

    index_path = run_git(
        worktree,
        "rev-parse",
        "--git-path",
        "index",
    ).stdout.strip()
    return Path(index_path).read_bytes()


def assert_plan_error(
    handle: object,
    message: object,
    match: str,
) -> None:
    """Require one safe planning error."""

    with pytest.raises(ConfigurationError, match=match) as raised:
        plan_isolated_commit(handle, message)  # type: ignore[arg-type]

    if isinstance(handle, WorktreeHandle):
        assert str(handle.source_repository) not in str(raised.value)
        assert str(handle.worktree_path) not in str(raised.value)


def test_plan_is_immutable_slotted_complete_safe_and_deterministic(
    tmp_path: Path,
) -> None:
    """Snapshot mixed changes, exact message, complete diffs, and safe preview."""

    source, handle = create_isolated_worktree(tmp_path)
    worktree = handle.worktree_path
    old_index = index_bytes(worktree)
    old_source_status = run_git(source, "status", "--short").stdout
    (worktree / "tracked.txt").write_text("changed\n", encoding="utf-8")
    new_path = worktree / "new.py"
    new_path.write_text("value = 1\n", encoding="utf-8")
    message = "feat: preserve exact message\n\nDetailed body.\n"

    first = plan_isolated_commit(handle, message)
    second = plan_isolated_commit(handle, message)

    assert isinstance(first, IsolatedCommitPlan)
    assert first == second
    assert not hasattr(first, "__dict__")
    with pytest.raises(FrozenInstanceError):
        first.branch_name = "other"  # type: ignore[misc]
    assert str(source.resolve()) not in repr(first)
    assert str(worktree.resolve()) not in repr(first)
    assert first.worktree is handle
    assert first.old_head == handle.source_head
    assert first.source_head == handle.source_head
    assert first.source_branch == "main"
    assert first.branch_name == "agent/task"
    assert first.commit_message == message
    assert first.paths == ("new.py", "tracked.txt")
    assert first.operation_count == 2
    assert first.added_count == 1
    assert first.modified_count == 1
    assert first.total_old_size_bytes == len(b"tracked\n")
    assert first.total_new_size_bytes == len(b"value = 1\nchanged\n")
    assert first.total_changed_lines == 3
    assert len(first.diff_fingerprint) == 64

    preview = first.preview
    assert preview["action"] == "create_isolated_commit"
    assert preview["branch"] == "agent/task"
    assert preview["old_head"] == handle.source_head
    assert preview["commit_message"] == message
    assert preview["paths"] == ["new.py", "tracked.txt"]
    assert preview["operation_count"] == 2
    assert preview["added_count"] == 1
    assert preview["modified_count"] == 1
    assert preview["diff_fingerprint"] == first.diff_fingerprint
    assert preview["command"] == (
        "git add -- <approved paths> && git commit --no-verify --no-gpg-sign --file=-"
    )
    assert preview["guarantees"] == [
        "local isolated branch only",
        "no amend",
        "no merge",
        "no push",
        "no branch deletion",
    ]
    changes = preview["changes"]
    assert isinstance(changes, list)
    assert [change["path"] for change in changes] == ["new.py", "tracked.txt"]
    assert [change["operation"] for change in changes] == ["add", "modify"]
    assert "--- /dev/null" in changes[0]["diff"]
    assert "+++ b/new.py" in changes[0]["diff"]
    assert "--- a/tracked.txt" in changes[1]["diff"]
    assert "+changed" in changes[1]["diff"]
    assert str(source.resolve()) not in str(preview)
    assert str(worktree.resolve()) not in str(preview)

    preview["paths"].append("mutated.py")
    assert first.preview["paths"] == ["new.py", "tracked.txt"]
    assert index_bytes(worktree) == old_index
    assert run_git(worktree, "diff", "--cached", "--quiet").returncode == 0
    assert run_git(worktree, "rev-parse", "HEAD").stdout.strip() == handle.source_head
    assert run_git(source, "status", "--short").stdout == old_source_status


def test_plan_preserves_no_final_newline_information(tmp_path: Path) -> None:
    """Include standard no-final-newline markers in complete diffs."""

    _, handle = create_isolated_worktree(tmp_path)
    (handle.worktree_path / "tracked.txt").write_text(
        "changed",
        encoding="utf-8",
    )

    plan = plan_isolated_commit(handle, "fix: newline")

    change = plan.preview["changes"][0]
    assert change["diff"].count("\\ No newline at end of file") == 1


@pytest.mark.parametrize(
    "message",
    [
        "",
        " ",
        "\n\t",
        "-option",
        "contains\0nul",
        42,
    ],
)
def test_plan_rejects_invalid_commit_messages(
    tmp_path: Path,
    message: object,
) -> None:
    """Require a bounded exact safe message without normalizing it."""

    _, handle = create_isolated_worktree(tmp_path)
    (handle.worktree_path / "tracked.txt").write_text(
        "changed\n",
        encoding="utf-8",
    )

    assert_plan_error(handle, message, "commit message")


def test_plan_accepts_exact_message_limit_and_rejects_over_limit(
    tmp_path: Path,
) -> None:
    """Apply the encoded 4 KiB message boundary exactly."""

    _, handle = create_isolated_worktree(tmp_path)
    (handle.worktree_path / "tracked.txt").write_text(
        "changed\n",
        encoding="utf-8",
    )
    exact = "m" * MAX_COMMIT_MESSAGE_BYTES

    assert plan_isolated_commit(handle, exact).commit_message == exact
    assert_plan_error(handle, exact + "m", "commit message")


def test_plan_rejects_zero_changes_and_invalid_handles(tmp_path: Path) -> None:
    """Require a verified dirty linked worktree."""

    source, handle = create_isolated_worktree(tmp_path)

    assert_plan_error(handle, "feat: nothing", "eligible")
    assert_plan_error(object(), "feat: invalid", "WorktreeHandle")
    assert_plan_error(source, "feat: primary", "WorktreeHandle")


def test_plan_rejects_forged_primary_handle_and_active_git_operation(
    tmp_path: Path,
) -> None:
    """Reject an untrusted primary handle and isolated operation state."""

    source, handle = create_isolated_worktree(tmp_path)
    forged = WorktreeHandle._validated(
        source_repository=source.resolve(),
        source_head=handle.source_head,
        branch_name="main",
        worktree_path=source.resolve(),
        target_display=".",
    )
    (source / "tracked.txt").write_text("changed\n", encoding="utf-8")
    assert_plan_error(forged, "feat: forged", "primary")
    (source / "tracked.txt").write_text("tracked\n", encoding="utf-8")

    (handle.worktree_path / "tracked.txt").write_text(
        "changed\n",
        encoding="utf-8",
    )
    merge_head = Path(
        run_git(
            handle.worktree_path,
            "rev-parse",
            "--git-path",
            "MERGE_HEAD",
        ).stdout.strip()
    )
    merge_head.write_text(handle.source_head + "\n", encoding="utf-8")
    assert_plan_error(handle, "feat: operation", "Git operation")


@pytest.mark.parametrize(
    "staged_kind",
    ["modified", "new", "deleted", "renamed", "intent"],
)
def test_plan_rejects_every_preexisting_staged_state(
    tmp_path: Path,
    staged_kind: str,
) -> None:
    """Never combine a new plan with real-index state."""

    _, handle = create_isolated_worktree(tmp_path)
    worktree = handle.worktree_path
    if staged_kind == "modified":
        (worktree / "tracked.txt").write_text("changed\n", encoding="utf-8")
        run_git(worktree, "add", "tracked.txt")
    elif staged_kind == "new":
        (worktree / "new.txt").write_text("new\n", encoding="utf-8")
        run_git(worktree, "add", "new.txt")
    elif staged_kind == "deleted":
        (worktree / "tracked.txt").unlink()
        run_git(worktree, "add", "--", "tracked.txt")
    elif staged_kind == "renamed":
        run_git(worktree, "mv", "tracked.txt", "renamed.txt")
    else:
        (worktree / "intent.txt").write_text("intent\n", encoding="utf-8")
        run_git(worktree, "add", "-N", "intent.txt")

    before = index_bytes(worktree)

    assert_plan_error(handle, "feat: staged", "index")
    assert index_bytes(worktree) == before


@pytest.mark.parametrize(
    "change_kind",
    [
        "deletion",
        "mode",
        "symlink",
        "broken_symlink",
        "binary",
        "nul",
        "invalid_utf8",
        "ignored",
        "pathspec_magic",
        "option_path",
    ],
)
def test_plan_rejects_unsupported_or_unsafe_changes(
    tmp_path: Path,
    change_kind: str,
) -> None:
    """Reject the entire plan rather than silently omitting unsafe entries."""

    _, handle = create_isolated_worktree(tmp_path)
    worktree = handle.worktree_path
    tracked = worktree / "tracked.txt"
    match = "unsupported"
    if change_kind == "deletion":
        tracked.unlink()
    elif change_kind == "mode":
        tracked.chmod(0o755)
    elif change_kind == "symlink":
        (worktree / "link").symlink_to("tracked.txt")
    elif change_kind == "broken_symlink":
        (worktree / "link").symlink_to("missing.txt")
    elif change_kind == "binary":
        (worktree / "binary.bin").write_bytes(b"\x89PNG\r\n\x1a\n")
        match = "UTF-8"
    elif change_kind == "nul":
        (worktree / "nul.txt").write_bytes(b"text\0value\n")
        match = "NUL"
    elif change_kind == "invalid_utf8":
        (worktree / "invalid.txt").write_bytes(b"\xff")
        match = "UTF-8"
    elif change_kind == "ignored":
        (worktree / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
        run_git(worktree, "add", ".gitignore")
        run_git(worktree, "commit", "-m", "ignore file")
        (worktree / "ignored.txt").write_text("ignored\n", encoding="utf-8")
        tracked.write_text("changed\n", encoding="utf-8")
        match = "ignored"
    elif change_kind == "pathspec_magic":
        (worktree / ":magic.txt").write_text("magic\n", encoding="utf-8")
        match = "path"
    else:
        (worktree / "-option.txt").write_text("option\n", encoding="utf-8")
        match = "path"

    assert_plan_error(handle, "feat: unsafe", match)


def test_plan_rejects_dirty_primary_and_changed_worktree_identity(
    tmp_path: Path,
) -> None:
    """Revalidate source cleanliness, registration, branch, and attachment."""

    source, handle = create_isolated_worktree(tmp_path)
    (handle.worktree_path / "tracked.txt").write_text(
        "changed\n",
        encoding="utf-8",
    )
    (source / "tracked.txt").write_text("source dirty\n", encoding="utf-8")
    assert_plan_error(handle, "feat: dirty source", "source")
    (source / "tracked.txt").write_text("tracked\n", encoding="utf-8")

    run_git(handle.worktree_path, "switch", "--detach")
    assert_plan_error(handle, "feat: detached", "branch")


def test_plan_rejects_missing_local_identity(tmp_path: Path) -> None:
    """Require repository-local author identity without inventing it."""

    source, handle = create_isolated_worktree(tmp_path)
    run_git(source, "config", "--unset", "user.email")
    (handle.worktree_path / "tracked.txt").write_text(
        "changed\n",
        encoding="utf-8",
    )

    assert_plan_error(handle, "feat: identity", "identity")


def test_plan_enforces_file_count_and_aggregate_limits(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Apply named complete-plan limits without mutating the index."""

    _, handle = create_isolated_worktree(tmp_path)
    worktree = handle.worktree_path
    import agent_workbench.worktree_commits as commits

    monkeypatch.setattr(commits, "MAX_COMMIT_FILES", 2)
    for name in ("a.py", "b.py", "c.py"):
        (worktree / name).write_text(f"{name}\n", encoding="utf-8")
    assert_plan_error(handle, "feat: files", "file limit")
    (worktree / "c.py").unlink()

    monkeypatch.setattr(commits, "MAX_COMMIT_FILES", MAX_COMMIT_FILES)
    monkeypatch.setattr(commits, "MAX_COMMIT_OLD_BYTES", 1)
    (worktree / "tracked.txt").write_text("changed\n", encoding="utf-8")
    assert_plan_error(handle, "feat: old bytes", "old-byte")

    monkeypatch.setattr(commits, "MAX_COMMIT_OLD_BYTES", MAX_COMMIT_OLD_BYTES)
    monkeypatch.setattr(commits, "MAX_COMMIT_CURRENT_BYTES", 1)
    assert_plan_error(handle, "feat: current bytes", "current-byte")

    monkeypatch.setattr(
        commits,
        "MAX_COMMIT_CURRENT_BYTES",
        MAX_COMMIT_CURRENT_BYTES,
    )
    monkeypatch.setattr(commits, "MAX_COMMIT_CHANGED_LINES", 1)
    assert_plan_error(handle, "feat: lines", "changed-line")


def test_plan_enforces_per_file_and_complete_preview_limits(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Reject oversized content, line changes, and encoded complete previews."""

    _, handle = create_isolated_worktree(tmp_path)
    worktree = handle.worktree_path
    import agent_workbench.worktree_commits as commits

    monkeypatch.setattr(commits, "MAX_COMMIT_FILE_BYTES", 7)
    (worktree / "tracked.txt").write_text("changed\n", encoding="utf-8")
    assert_plan_error(handle, "feat: size", "file exceeds")

    monkeypatch.setattr(commits, "MAX_COMMIT_FILE_BYTES", MAX_COMMIT_FILE_BYTES)
    monkeypatch.setattr(commits, "MAX_COMMIT_FILE_CHANGED_LINES", 1)
    assert_plan_error(handle, "feat: lines", "changed-line")

    monkeypatch.setattr(
        commits,
        "MAX_COMMIT_FILE_CHANGED_LINES",
        MAX_COMMIT_FILE_CHANGED_LINES,
    )
    monkeypatch.setattr(commits, "MAX_COMMIT_PREVIEW_BYTES", 100)
    assert_plan_error(handle, "feat: preview", "preview")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO is not portable")
def test_plan_rejects_special_files(tmp_path: Path) -> None:
    """Reject special untracked filesystem entries conservatively."""

    _, handle = create_isolated_worktree(tmp_path)
    os.mkfifo(handle.worktree_path / "special")

    assert_plan_error(handle, "feat: special", "regular files")


def test_git_planning_environment_is_minimal_and_cwd_is_unchanged(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Use a fixed credential-free local environment without changing cwd."""

    import agent_workbench.worktree_commits as commits

    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Injected")
    environment = commits._git_environment()
    original_cwd = Path.cwd()
    _, handle = create_isolated_worktree(tmp_path)
    (handle.worktree_path / "tracked.txt").write_text(
        "changed\n",
        encoding="utf-8",
    )

    plan_isolated_commit(handle, "feat: safe environment")

    assert "OPENAI_API_KEY" not in environment
    assert "GIT_AUTHOR_NAME" not in environment
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert environment["GIT_CONFIG_GLOBAL"] == os.devnull
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert Path.cwd() == original_cwd


def test_named_limits_have_required_values() -> None:
    """Keep the first approved commit boundary explicit and reviewable."""

    assert MAX_COMMIT_FILE_BYTES == 100 * 1024
    assert MAX_COMMIT_FILE_CHANGED_LINES == 500
    assert MAX_COMMIT_FILES == 32
    assert MAX_COMMIT_OLD_BYTES == 1024 * 1024
    assert MAX_COMMIT_CURRENT_BYTES == 1024 * 1024
    assert MAX_COMMIT_CHANGED_LINES == 4_000
    assert MAX_COMMIT_PREVIEW_BYTES == 512 * 1024
    assert MAX_COMMIT_MESSAGE_BYTES == 4 * 1024
