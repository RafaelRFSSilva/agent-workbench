"""Tests for approved local commits inside verified isolated worktrees."""

from dataclasses import FrozenInstanceError
import os
from pathlib import Path
import subprocess

import pytest

from agent_workbench.errors import CompletionError, ConfigurationError
from agent_workbench.recovery import (
    IsolatedCommitRecoveryPhase,
    RecoveryStatus,
)
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
    IsolatedCommitAction,
    IsolatedCommitApprovalRequest,
    IsolatedCommitPlan,
    IsolatedCommitResult,
    create_isolated_commit,
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


def create_isolated_worktree_with_tracked_content(
    tmp_path: Path,
    content: bytes,
) -> tuple[Path, WorktreeHandle]:
    """Create an isolated worktree with exact committed tracked-file bytes."""

    source = create_repository(tmp_path / "source")
    tracked = source / "tracked.txt"
    if tracked.read_bytes() != content:
        tracked.write_bytes(content)
        run_git(source, "add", "tracked.txt")
        run_git(source, "commit", "-m", "line-ending baseline")
    target = tmp_path / "isolated"
    plan = plan_git_worktree(source, "agent/task", target)
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


def capture_recovery_phases(monkeypatch, module):
    """Record the recovery phase supplied by one isolated-commit failure."""

    phases: list[IsolatedCommitRecoveryPhase] = []
    original = module._commit_failure_message

    def capture(plan, reason, phase):
        phases.append(phase)
        return original(plan, reason, phase)

    monkeypatch.setattr(module, "_commit_failure_message", capture)
    return phases


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
    ("original", "replacement"),
    [(b"tracked\r\n", b"tracked\n"), (b"tracked\n", b"tracked\r\n")],
)
def test_plan_counts_line_ending_only_changes(
    tmp_path: Path,
    original: bytes,
    replacement: bytes,
) -> None:
    """Use the shared terminator-aware count in isolated commit planning."""

    _, handle = create_isolated_worktree_with_tracked_content(tmp_path, original)
    target = handle.worktree_path / "tracked.txt"
    target.write_bytes(replacement)

    plan = plan_isolated_commit(handle, "fix: line ending")

    assert plan.total_changed_lines == 2
    assert plan.preview["changes"][0]["changed_lines"] == 2
    assert target.read_bytes() == replacement


@pytest.mark.parametrize(
    ("old_ending", "new_ending"),
    [(b"\r\n", b"\n"), (b"\n", b"\r\n")],
)
def test_plan_rejects_501_line_ending_only_changes(
    tmp_path: Path,
    old_ending: bytes,
    new_ending: bytes,
) -> None:
    """Enforce the exact 500-line limit for terminator-only commit changes."""

    original = b"".join(f"line {index}".encode() + old_ending for index in range(501))
    replacement = b"".join(
        f"line {index}".encode() + new_ending for index in range(501)
    )
    _, handle = create_isolated_worktree_with_tracked_content(tmp_path, original)
    target = handle.worktree_path / "tracked.txt"
    target.write_bytes(replacement)

    with pytest.raises(
        ConfigurationError,
        match=r"^isolated commit file exceeds the 500-changed-line limit\.$",
    ):
        plan_isolated_commit(handle, "fix: line endings")

    assert target.read_bytes() == replacement
    assert run_git(handle.worktree_path, "diff", "--cached", "--quiet").returncode == 0


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


def test_plan_rejects_changes_outside_expected_paths_before_index_mutation(
    tmp_path: Path,
) -> None:
    """Compare the complete worktree path set with the controller allowlist."""

    _, handle = create_isolated_worktree(tmp_path)
    worktree = handle.worktree_path
    (worktree / "tracked.txt").write_text("approved\n", encoding="utf-8")
    (worktree / "unrelated.txt").write_text("unrelated\n", encoding="utf-8")
    before_index = index_bytes(worktree)

    with pytest.raises(
        ConfigurationError,
        match="outside the successful approved workspace actions",
    ):
        plan_isolated_commit(
            handle,
            "fix: expected paths",
            expected_paths=("tracked.txt",),
        )

    assert index_bytes(worktree) == before_index
    assert run_git(worktree, "diff", "--cached", "--name-only").stdout == ""
    assert run_git(worktree, "status", "--short").stdout == (
        " M tracked.txt\n?? unrelated.txt\n"
    )
    assert run_git(worktree, "log", "-1", "--pretty=%s").stdout.strip() == "initial"


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

    run_git(
        handle.worktree_path,
        "update-ref",
        "--no-deref",
        "HEAD",
        handle.source_head,
    )
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


def test_commit_approval_request_and_result_are_immutable_and_slotted(
    tmp_path: Path,
) -> None:
    """Expose copy-safe explicit lifecycle models with safe result metadata."""

    _, handle = create_isolated_worktree(tmp_path)
    (handle.worktree_path / "tracked.txt").write_text(
        "changed\n",
        encoding="utf-8",
    )
    plan = plan_isolated_commit(handle, "fix: tracked")
    request = IsolatedCommitApprovalRequest(
        IsolatedCommitAction.CREATE,
        plan.preview,
    )

    assert not hasattr(request, "__dict__")
    with pytest.raises(FrozenInstanceError):
        request.action = IsolatedCommitAction.CREATE  # type: ignore[misc]
    assert request.action is IsolatedCommitAction.CREATE
    preview = request.preview
    preview["paths"].append("unexpected")
    assert request.preview["paths"] == ["tracked.txt"]

    result = IsolatedCommitResult(
        branch_name="agent/task",
        old_head="a" * 40,
        new_head="b" * 40,
        commit_message="fix: tracked",
        paths=("tracked.txt",),
        operation_count=1,
        added_count=0,
        modified_count=1,
    )
    assert not hasattr(result, "__dict__")
    with pytest.raises(FrozenInstanceError):
        result.new_head = "c" * 40  # type: ignore[misc]


@pytest.mark.parametrize("approval_kind", ["missing", "deny", "invalid", "failure"])
def test_commit_requires_one_explicit_approval_before_staging(
    tmp_path: Path,
    approval_kind: str,
) -> None:
    """Perform no Git mutation without one exact valid caller decision."""

    _, handle = create_isolated_worktree(tmp_path)
    worktree = handle.worktree_path
    (worktree / "tracked.txt").write_text("changed\n", encoding="utf-8")
    plan = plan_isolated_commit(handle, "fix: approval")
    old_index = index_bytes(worktree)
    old_head = run_git(worktree, "rev-parse", "HEAD").stdout.strip()
    requests = []

    def handler(request):
        requests.append(request)
        if approval_kind == "deny":
            return ToolApprovalDecision.DENY
        if approval_kind == "invalid":
            return True
        raise RuntimeError("injected")

    selected = None if approval_kind == "missing" else handler
    with pytest.raises(CompletionError, match="approval"):
        create_isolated_commit(plan, selected)  # type: ignore[arg-type]

    assert len(requests) == (0 if approval_kind == "missing" else 1)
    assert index_bytes(worktree) == old_index
    assert run_git(worktree, "diff", "--cached", "--quiet").returncode == 0
    assert run_git(worktree, "rev-parse", "HEAD").stdout.strip() == old_head


def test_pre_mutation_handler_runs_once_after_approval_and_revalidation_before_add(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Invoke the optional pre-mutation hook exactly once at the last safe point."""

    _, handle = create_isolated_worktree(tmp_path)
    worktree = handle.worktree_path
    (worktree / "tracked.txt").write_text("changed\n", encoding="utf-8")
    plan = plan_isolated_commit(handle, "fix: hook ordering")
    module = pytest.importorskip("agent_workbench.worktree_commits")
    original_plan_isolated_commit = module.plan_isolated_commit
    original_run_git = module._run_git
    events: list[str] = []
    revalidated_plans: list[IsolatedCommitPlan] = []
    hook_plans: list[IsolatedCommitPlan] = []

    def wrapped_plan_isolated_commit(*args, **kwargs):
        current_plan = original_plan_isolated_commit(*args, **kwargs)
        events.append("revalidation")
        revalidated_plans.append(current_plan)
        return current_plan

    def recording_run_git(repository, arguments, *, input_bytes=None):
        arguments = tuple(arguments)
        if arguments[:2] == ("add", "--"):
            events.append("add")
        if "commit" in arguments:
            events.append("commit")
        return original_run_git(repository, arguments, input_bytes=input_bytes)

    def approval_handler(_request):
        events.append("approval")
        return ToolApprovalDecision.APPROVE

    def pre_mutation_handler(current_plan: IsolatedCommitPlan) -> None:
        events.append("handler")
        hook_plans.append(current_plan)

    monkeypatch.setattr(module, "plan_isolated_commit", wrapped_plan_isolated_commit)
    monkeypatch.setattr(module, "_run_git", recording_run_git)

    result = create_isolated_commit(
        plan,
        approval_handler,
        pre_mutation_handler=pre_mutation_handler,
    )

    assert result.old_head == plan.old_head
    assert events.index("approval") < events.index("revalidation")
    assert events.index("revalidation") < events.index("handler")
    assert events.index("handler") < events.index("add")
    assert hook_plans == [revalidated_plans[0]]
    assert hook_plans[0] is revalidated_plans[0]
    assert hook_plans[0] == plan
    assert events.count("handler") == 1


@pytest.mark.parametrize("approval_kind", ["missing", "deny", "invalid", "failure"])
def test_pre_mutation_handler_is_not_called_without_successful_approval(
    tmp_path: Path,
    approval_kind: str,
) -> None:
    """Never invoke the hook unless approval succeeds."""

    _, handle = create_isolated_worktree(tmp_path)
    worktree = handle.worktree_path
    (worktree / "tracked.txt").write_text("changed\n", encoding="utf-8")
    plan = plan_isolated_commit(handle, "fix: approval boundary")
    hook_calls = 0

    def pre_mutation_handler(_plan: IsolatedCommitPlan) -> None:
        nonlocal hook_calls
        hook_calls += 1

    def approval_handler(_request):
        if approval_kind == "deny":
            return ToolApprovalDecision.DENY
        if approval_kind == "invalid":
            return False
        raise RuntimeError("injected")

    selected = None if approval_kind == "missing" else approval_handler
    with pytest.raises(CompletionError, match="approval"):
        create_isolated_commit(
            plan,
            selected,  # type: ignore[arg-type]
            pre_mutation_handler=pre_mutation_handler,
        )

    assert hook_calls == 0
    assert run_git(worktree, "diff", "--cached", "--quiet").returncode == 0
    assert run_git(worktree, "rev-parse", "HEAD").stdout.strip() == plan.old_head


@pytest.mark.parametrize(
    "stale_kind",
    [
        "content",
        "unexpected",
        "head",
        "branch",
        "source_dirty",
        "index",
        "operation",
    ],
)
def test_pre_mutation_handler_is_not_called_for_stale_plan(
    tmp_path: Path,
    stale_kind: str,
) -> None:
    """Skip the hook entirely when the post-approval revalidation is stale."""

    source, handle = create_isolated_worktree(tmp_path)
    worktree = handle.worktree_path
    tracked = worktree / "tracked.txt"
    tracked.write_text("changed\n", encoding="utf-8")
    plan = plan_isolated_commit(handle, "fix: stale hook")
    old_head = plan.old_head
    hook_calls = 0

    def pre_mutation_handler(_plan: IsolatedCommitPlan) -> None:
        nonlocal hook_calls
        hook_calls += 1

    def approve(_request):
        if stale_kind == "content":
            tracked.write_text("changed again\n", encoding="utf-8")
        elif stale_kind == "unexpected":
            (worktree / "unexpected.txt").write_text("unexpected\n", encoding="utf-8")
        elif stale_kind == "head":
            run_git(worktree, "add", "--", "tracked.txt")
            run_git(worktree, "commit", "-m", "concurrent")
        elif stale_kind == "branch":
            run_git(worktree, "branch", "agent/other")
            run_git(worktree, "symbolic-ref", "HEAD", "refs/heads/agent/other")
        elif stale_kind == "source_dirty":
            (source / "tracked.txt").write_text("source dirty\n", encoding="utf-8")
        elif stale_kind == "index":
            run_git(worktree, "add", "--", "tracked.txt")
        else:
            git_path = Path(
                run_git(
                    worktree,
                    "rev-parse",
                    "--git-path",
                    "MERGE_HEAD",
                ).stdout.strip()
            )
            git_path.write_text(old_head + "\n", encoding="utf-8")
        return ToolApprovalDecision.APPROVE

    with pytest.raises(CompletionError, match="stale"):
        create_isolated_commit(
            plan,
            approve,
            pre_mutation_handler=pre_mutation_handler,
        )

    assert hook_calls == 0


def test_pre_mutation_handler_failure_aborts_before_git_add_and_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Convert hook failures into bounded CompletionError before mutation begins."""

    _, handle = create_isolated_worktree(tmp_path)
    worktree = handle.worktree_path
    (worktree / "tracked.txt").write_text("changed\n", encoding="utf-8")
    plan = plan_isolated_commit(handle, "fix: hook failure")
    module = pytest.importorskip("agent_workbench.worktree_commits")
    original_run_git = module._run_git
    commands: list[tuple[str, ...]] = []
    hook_calls = 0

    def recording_run_git(repository, arguments, *, input_bytes=None):
        arguments = tuple(arguments)
        commands.append(arguments)
        return original_run_git(repository, arguments, input_bytes=input_bytes)

    def pre_mutation_handler(_plan: IsolatedCommitPlan) -> None:
        nonlocal hook_calls
        hook_calls += 1
        raise RuntimeError("injected secret failure")

    monkeypatch.setattr(module, "_run_git", recording_run_git)

    with pytest.raises(
        CompletionError,
        match="Pre-mutation checkpoint failed; no commit mutation was started",
    ) as raised:
        create_isolated_commit(
            plan,
            lambda _request: ToolApprovalDecision.APPROVE,
            pre_mutation_handler=pre_mutation_handler,
        )

    assert hook_calls == 1
    assert "injected secret failure" not in str(raised.value)
    assert not any(arguments[:2] == ("add", "--") for arguments in commands)
    assert not any("commit" in arguments for arguments in commands)
    assert run_git(worktree, "diff", "--cached", "--quiet").returncode == 0
    assert run_git(worktree, "rev-parse", "HEAD").stdout.strip() == plan.old_head


def test_non_callable_pre_mutation_handler_is_rejected_before_approval(
    tmp_path: Path,
) -> None:
    """Reject invalid hook values before approval or mutation."""

    _, handle = create_isolated_worktree(tmp_path)
    worktree = handle.worktree_path
    (worktree / "tracked.txt").write_text("changed\n", encoding="utf-8")
    plan = plan_isolated_commit(handle, "fix: invalid hook")
    approval_calls = 0

    def approval_handler(_request):
        nonlocal approval_calls
        approval_calls += 1
        return ToolApprovalDecision.APPROVE

    with pytest.raises(ConfigurationError, match="pre-mutation handler"):
        create_isolated_commit(
            plan,
            approval_handler,
            pre_mutation_handler=True,  # type: ignore[arg-type]
        )

    assert approval_calls == 0
    assert run_git(worktree, "diff", "--cached", "--quiet").returncode == 0
    assert run_git(worktree, "rev-parse", "HEAD").stdout.strip() == plan.old_head


def test_approved_commit_stages_exact_paths_and_verifies_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Create one exact local commit and preserve the primary repository."""

    source, handle = create_isolated_worktree(tmp_path)
    worktree = handle.worktree_path
    (worktree / "tracked.txt").write_text("changed\n", encoding="utf-8")
    (worktree / "new.py").write_text("value = 1\n", encoding="utf-8")
    message = "fix: exact files\n\nPreserve this body."
    plan = plan_isolated_commit(handle, message)
    source_head = run_git(source, "rev-parse", "HEAD").stdout.strip()
    source_branch = run_git(source, "branch", "--show-current").stdout.strip()
    commands = []
    original_run_git = pytest.importorskip("agent_workbench.worktree_commits")._run_git

    def recording_run_git(repository, arguments, *, input_bytes=None):
        commands.append((repository, tuple(arguments), input_bytes))
        return original_run_git(
            repository,
            arguments,
            input_bytes=input_bytes,
        )

    monkeypatch.setattr(
        "agent_workbench.worktree_commits._run_git",
        recording_run_git,
    )
    approval_snapshots = []

    def approve(request):
        approval_snapshots.append(
            {
                "request": request,
                "head": run_git(worktree, "rev-parse", "HEAD").stdout.strip(),
                "index": index_bytes(worktree),
            }
        )
        return ToolApprovalDecision.APPROVE

    result = create_isolated_commit(plan, approve)

    assert len(approval_snapshots) == 1
    assert approval_snapshots[0]["request"].preview == plan.preview
    assert approval_snapshots[0]["head"] == plan.old_head
    assert result.branch_name == "agent/task"
    assert result.old_head == plan.old_head
    assert result.new_head != result.old_head
    assert result.commit_message == message
    assert result.paths == ("new.py", "tracked.txt")
    assert result.operation_count == 2
    assert result.added_count == 1
    assert result.modified_count == 1
    assert (
        run_git(worktree, "rev-parse", f"{result.new_head}^").stdout.strip()
        == result.old_head
    )
    assert (
        run_git(worktree, "show", "-s", "--format=%B", "HEAD").stdout.rstrip("\n")
        == message
    )
    assert run_git(worktree, "diff", "--cached", "--quiet").returncode == 0
    assert run_git(worktree, "status", "--short").stdout == ""
    assert run_git(worktree, "branch", "--show-current").stdout.strip() == "agent/task"
    assert run_git(source, "rev-parse", "HEAD").stdout.strip() == source_head
    assert run_git(source, "branch", "--show-current").stdout.strip() == source_branch
    assert run_git(source, "status", "--short").stdout == ""
    assert (
        run_git(
            worktree,
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}",
            check=False,
        ).returncode
        != 0
    )

    add_calls = [command for command in commands if command[1][:2] == ("add", "--")]
    assert add_calls == [
        (
            worktree,
            ("add", "--", "new.py", "tracked.txt"),
            None,
        )
    ]
    commit_calls = [command for command in commands if "--file=-" in command[1]]
    assert len(commit_calls) == 1
    commit_arguments = commit_calls[0][1]
    assert commit_arguments == (
        "-c",
        "commit.gpgSign=false",
        "-c",
        "tag.gpgSign=false",
        "-c",
        "core.editor=false",
        "commit",
        "--no-verify",
        "--no-gpg-sign",
        "--cleanup=verbatim",
        "--file=-",
    )
    assert commit_calls[0][2] == message.encode("utf-8")
    prohibited = {
        "--amend",
        "-a",
        "--all",
        "reset",
        "restore",
        "clean",
        "stash",
        "merge",
        "rebase",
        "push",
        "fetch",
    }
    assert not any(
        prohibited.intersection(arguments)
        for _repository, arguments, _input in commands
    )


@pytest.mark.parametrize(
    "stale_kind",
    [
        "content",
        "unexpected",
        "head",
        "branch",
        "source_dirty",
        "index",
        "operation",
    ],
)
def test_post_approval_stale_state_performs_no_new_staging_or_commit(
    tmp_path: Path,
    stale_kind: str,
) -> None:
    """Regenerate the complete plan after approval and reject every mismatch."""

    source, handle = create_isolated_worktree(tmp_path)
    worktree = handle.worktree_path
    tracked = worktree / "tracked.txt"
    tracked.write_text("changed\n", encoding="utf-8")
    plan = plan_isolated_commit(handle, "fix: stale")
    old_head = plan.old_head

    def approve(_request):
        if stale_kind == "content":
            tracked.write_text("changed again\n", encoding="utf-8")
        elif stale_kind == "unexpected":
            (worktree / "unexpected.txt").write_text(
                "unexpected\n",
                encoding="utf-8",
            )
        elif stale_kind == "head":
            run_git(worktree, "add", "--", "tracked.txt")
            run_git(worktree, "commit", "-m", "concurrent")
        elif stale_kind == "branch":
            run_git(worktree, "branch", "agent/other")
            run_git(worktree, "symbolic-ref", "HEAD", "refs/heads/agent/other")
        elif stale_kind == "source_dirty":
            (source / "tracked.txt").write_text("source dirty\n", encoding="utf-8")
        elif stale_kind == "index":
            run_git(worktree, "add", "--", "tracked.txt")
        else:
            git_path = Path(
                run_git(
                    worktree,
                    "rev-parse",
                    "--git-path",
                    "MERGE_HEAD",
                ).stdout.strip()
            )
            git_path.write_text(old_head + "\n", encoding="utf-8")
        return ToolApprovalDecision.APPROVE

    with pytest.raises(CompletionError, match="stale"):
        create_isolated_commit(plan, approve)

    if stale_kind not in {"head", "index"}:
        assert run_git(worktree, "diff", "--cached", "--quiet").returncode == 0
    if stale_kind != "head":
        assert run_git(worktree, "rev-parse", "HEAD").stdout.strip() == old_head


def test_staging_failure_preserves_partial_index_and_never_commits(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Report manual recovery without reset when exact staging partially fails."""

    _, handle = create_isolated_worktree(tmp_path)
    worktree = handle.worktree_path
    (worktree / "tracked.txt").write_text("changed\n", encoding="utf-8")
    (worktree / "new.py").write_text("value = 1\n", encoding="utf-8")
    plan = plan_isolated_commit(handle, "fix: stage failure")
    module = pytest.importorskip("agent_workbench.worktree_commits")
    original_run_git = module._run_git
    phases = capture_recovery_phases(monkeypatch, module)
    commands = []

    def fail_stage(repository, arguments, *, input_bytes=None):
        arguments = tuple(arguments)
        commands.append(arguments)
        if arguments[:2] == ("add", "--"):
            original_run_git(repository, ("add", "--", "new.py"))
            return module._GitOutput(1, b"", b"injected")
        return original_run_git(
            repository,
            arguments,
            input_bytes=input_bytes,
        )

    monkeypatch.setattr(module, "_run_git", fail_stage)

    with pytest.raises(CompletionError, match="manual inspection") as raised:
        create_isolated_commit(
            plan,
            lambda _request: ToolApprovalDecision.APPROVE,
        )

    assert "new.py" in str(raised.value)
    assert run_git(worktree, "rev-parse", "HEAD").stdout.strip() == plan.old_head
    assert run_git(worktree, "diff", "--cached", "--name-only").stdout.strip() == (
        "new.py"
    )
    assert not any("commit" in arguments for arguments in commands)
    assert not any(
        operation in arguments
        for arguments in commands
        for operation in ("reset", "restore", "clean", "stash")
    )
    assert phases == [IsolatedCommitRecoveryPhase.EXACT_STAGING]


@pytest.mark.parametrize("mismatch_kind", ["unexpected_path", "content"])
def test_post_staging_mismatch_preserves_index_and_never_commits(
    tmp_path: Path,
    monkeypatch,
    mismatch_kind: str,
) -> None:
    """Reject staged-set or staged-diff races without destructive recovery."""

    _, handle = create_isolated_worktree(tmp_path)
    worktree = handle.worktree_path
    tracked = worktree / "tracked.txt"
    tracked.write_text("changed\n", encoding="utf-8")
    plan = plan_isolated_commit(handle, "fix: staging mismatch")
    module = pytest.importorskip("agent_workbench.worktree_commits")
    original_run_git = module._run_git
    phases = capture_recovery_phases(monkeypatch, module)
    commit_attempted = False

    def mismatch_after_stage(repository, arguments, *, input_bytes=None):
        nonlocal commit_attempted
        arguments = tuple(arguments)
        if "--file=-" in arguments:
            commit_attempted = True
        outcome = original_run_git(
            repository,
            arguments,
            input_bytes=input_bytes,
        )
        if arguments[:2] == ("add", "--"):
            if mismatch_kind == "unexpected_path":
                (worktree / "unexpected.txt").write_text(
                    "unexpected\n",
                    encoding="utf-8",
                )
                original_run_git(repository, ("add", "--", "unexpected.txt"))
            else:
                tracked.write_text("different staged content\n", encoding="utf-8")
                original_run_git(repository, ("add", "--", "tracked.txt"))
        return outcome

    monkeypatch.setattr(module, "_run_git", mismatch_after_stage)

    with pytest.raises(CompletionError, match="manual inspection"):
        create_isolated_commit(
            plan,
            lambda _request: ToolApprovalDecision.APPROVE,
        )

    assert commit_attempted is False
    staged = run_git(worktree, "diff", "--cached", "--name-only").stdout.splitlines()
    assert "tracked.txt" in staged
    if mismatch_kind == "unexpected_path":
        assert "unexpected.txt" in staged
    assert run_git(worktree, "rev-parse", "HEAD").stdout.strip() == plan.old_head
    assert phases == [
        IsolatedCommitRecoveryPhase.STAGED_STATE_VERIFICATION,
    ]


def test_commit_failure_preserves_fully_staged_index_without_retry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Leave exact staged state available when fixed commit creation fails."""

    _, handle = create_isolated_worktree(tmp_path)
    worktree = handle.worktree_path
    (worktree / "tracked.txt").write_text("changed\n", encoding="utf-8")
    plan = plan_isolated_commit(handle, "fix: commit failure")
    module = pytest.importorskip("agent_workbench.worktree_commits")
    original_run_git = module._run_git
    phases = capture_recovery_phases(monkeypatch, module)
    commit_calls = 0

    def fail_commit(repository, arguments, *, input_bytes=None):
        nonlocal commit_calls
        if "commit" in arguments:
            commit_calls += 1
            return module._GitOutput(1, b"", b"injected")
        return original_run_git(
            repository,
            arguments,
            input_bytes=input_bytes,
        )

    monkeypatch.setattr(module, "_run_git", fail_commit)

    with pytest.raises(CompletionError, match="manual inspection"):
        create_isolated_commit(
            plan,
            lambda _request: ToolApprovalDecision.APPROVE,
        )

    assert commit_calls == 1
    assert run_git(worktree, "rev-parse", "HEAD").stdout.strip() == plan.old_head
    assert (
        run_git(worktree, "diff", "--cached", "--name-only").stdout.strip()
        == "tracked.txt"
    )
    assert phases == [
        IsolatedCommitRecoveryPhase.LOCAL_COMMIT_CREATION,
    ]


def test_ambiguous_post_commit_verification_preserves_new_head(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Never claim success or destructively recover after HEAD has changed."""

    _, handle = create_isolated_worktree(tmp_path)
    worktree = handle.worktree_path
    (worktree / "tracked.txt").write_text("changed\n", encoding="utf-8")
    plan = plan_isolated_commit(handle, "fix: ambiguous")
    module = pytest.importorskip("agent_workbench.worktree_commits")
    phases = capture_recovery_phases(monkeypatch, module)
    monkeypatch.setattr(
        module,
        "_verify_created_commit",
        lambda _plan, _new_head: (_ for _ in ()).throw(
            CompletionError("injected verification failure")
        ),
    )

    with pytest.raises(
        CompletionError,
        match="manual inspection.*HEAD changed: yes",
    ):
        create_isolated_commit(
            plan,
            lambda _request: ToolApprovalDecision.APPROVE,
        )

    assert run_git(worktree, "rev-parse", "HEAD").stdout.strip() != plan.old_head
    assert run_git(worktree, "branch", "--show-current").stdout.strip() == "agent/task"
    assert phases == [
        IsolatedCommitRecoveryPhase.COMMIT_VERIFICATION,
    ]


def test_collects_real_isolated_commit_recovery_evidence(
    tmp_path: Path,
) -> None:
    """Capture bounded structured evidence from the real isolated worktree."""

    _, handle = create_isolated_worktree(tmp_path)
    worktree = handle.worktree_path
    (worktree / "tracked.txt").write_text("changed\n", encoding="utf-8")
    (worktree / "new.py").write_text("value = 1\n", encoding="utf-8")
    plan = plan_isolated_commit(handle, "fix: recovery evidence")
    run_git(worktree, "add", "--", "new.py")

    module = pytest.importorskip("agent_workbench.worktree_commits")
    evidence = module._collect_commit_recovery_evidence(
        plan,
        IsolatedCommitRecoveryPhase.LOCAL_COMMIT_CREATION,
    )

    assert evidence.phase is IsolatedCommitRecoveryPhase.LOCAL_COMMIT_CREATION
    assert evidence.target_display == handle.target_display
    assert evidence.expected_branch == plan.branch_name
    assert evidence.observed_branch == plan.branch_name
    assert evidence.expected_head == plan.old_head
    assert evidence.observed_head == plan.old_head
    assert evidence.head_changed is RecoveryStatus.NO
    assert evidence.index_dirty is RecoveryStatus.YES
    assert evidence.staged_paths == ("new.py",)
    assert evidence.worktree_dirty is RecoveryStatus.YES
    assert str(handle.source_repository) not in repr(evidence)
    assert str(handle.worktree_path) not in repr(evidence)


def test_collects_unknown_recovery_state_when_git_inspection_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Represent unavailable observations without inventing Git state."""

    _, handle = create_isolated_worktree(tmp_path)
    worktree = handle.worktree_path
    (worktree / "tracked.txt").write_text("changed\n", encoding="utf-8")
    plan = plan_isolated_commit(handle, "fix: unavailable recovery evidence")
    module = pytest.importorskip("agent_workbench.worktree_commits")

    def fail_identity(*_args, **_kwargs):
        raise ConfigurationError("injected identity inspection failure")

    monkeypatch.setattr(module, "_read_head", fail_identity)
    monkeypatch.setattr(module, "_read_symbolic_branch", fail_identity)
    monkeypatch.setattr(
        module,
        "_run_git",
        lambda *_args, **_kwargs: module._GitOutput(
            1,
            b"",
            b"injected inspection failure",
        ),
    )

    evidence = module._collect_commit_recovery_evidence(
        plan,
        IsolatedCommitRecoveryPhase.COMMIT_VERIFICATION,
    )

    assert evidence.phase is IsolatedCommitRecoveryPhase.COMMIT_VERIFICATION
    assert evidence.observed_branch is None
    assert evidence.observed_head is None
    assert evidence.head_changed is RecoveryStatus.UNKNOWN
    assert evidence.index_dirty is RecoveryStatus.UNKNOWN
    assert evidence.staged_paths == ()
    assert evidence.worktree_dirty is RecoveryStatus.UNKNOWN


def test_second_commit_requires_a_fresh_plan_and_approval(tmp_path: Path) -> None:
    """Never cache approval across later local commits."""

    _, handle = create_isolated_worktree(tmp_path)
    worktree = handle.worktree_path
    tracked = worktree / "tracked.txt"
    tracked.write_text("first\n", encoding="utf-8")
    first = create_isolated_commit(
        plan_isolated_commit(handle, "fix: first"),
        lambda _request: ToolApprovalDecision.APPROVE,
    )
    tracked.write_text("second\n", encoding="utf-8")
    second_plan = plan_isolated_commit(handle, "fix: second")

    with pytest.raises(CompletionError, match="approval"):
        create_isolated_commit(second_plan, None)

    assert run_git(worktree, "rev-parse", "HEAD").stdout.strip() == first.new_head
