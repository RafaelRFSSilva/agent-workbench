"""Tests for supervised Git worktree planning and lifecycle."""

from dataclasses import FrozenInstanceError
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from agent_workbench.errors import ConfigurationError
from agent_workbench.tools import ToolApprovalDecision
from agent_workbench.worktrees import (
    WorktreeAction,
    WorktreeApprovalRequest,
    WorktreeHandle,
    WorktreePlan,
    WorktreeRemovalPlan,
    create_git_worktree,
    inspect_git_worktree,
    plan_git_worktree,
    plan_git_worktree_removal,
    remove_git_worktree,
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
    """Create one clean primary repository with a committed file."""

    root.mkdir()
    run_git(root, "init", "-b", "main")
    run_git(root, "config", "user.name", "Test User")
    run_git(root, "config", "user.email", "test@example.com")
    (root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    run_git(root, "add", "tracked.txt")
    run_git(root, "commit", "-m", "initial")
    return root


def assert_plan_error(
    source: Path,
    branch: object,
    target: Path,
    match: str,
) -> None:
    """Assert one concise planning error without absolute path leakage."""

    with pytest.raises(ConfigurationError, match=match) as raised:
        plan_git_worktree(source, branch, target)  # type: ignore[arg-type]

    assert str(source.resolve(strict=False)) not in str(raised.value)
    assert str(target.resolve(strict=False)) not in str(raised.value)


def test_plan_is_immutable_slotted_safe_and_deterministic(tmp_path: Path) -> None:
    """Return an immutable plan with hidden canonical paths and stable preview."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "worktree"

    first = plan_git_worktree(source, "agent/task", target)
    second = plan_git_worktree(source, "agent/task", target)

    assert isinstance(first, WorktreePlan)
    assert first == second
    assert not hasattr(first, "__dict__")
    with pytest.raises(FrozenInstanceError):
        first.branch_name = "other"  # type: ignore[misc]
    assert str(source.resolve()) not in repr(first)
    assert str(target.resolve()) not in repr(first)
    assert first.source_repository == source.resolve()
    assert first.target_path == target.resolve()
    assert first.target_display == "../worktree"
    assert len(first.source_head) == 40
    assert first.source_head == run_git(source, "rev-parse", "HEAD").stdout.strip()

    expected_preview = {
        "action": "create_worktree",
        "source_repository": ".",
        "pinned_head": first.source_head,
        "branch_name": "agent/task",
        "target": "../worktree",
        "command": [
            "git",
            "-C",
            ".",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "worktree",
            "add",
            "-b",
            "agent/task",
            "../worktree",
            first.source_head,
        ],
        "scope": "Creates one local branch and one local worktree only.",
        "exclusions": "No commit, merge, push, or branch deletion will occur.",
    }
    assert first.preview == expected_preview
    mutated = first.preview
    mutated["branch_name"] = "mutated"
    assert first.preview == expected_preview
    assert str(source.resolve()) not in str(first.preview)
    assert str(target.resolve()) not in str(first.preview)


def test_plan_accepts_relative_target_and_ignored_local_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Resolve a relative absent target while honoring Git ignore semantics."""

    source = create_repository(tmp_path / "source")
    (source / ".gitignore").write_text("ignored.local\n", encoding="utf-8")
    run_git(source, "add", ".gitignore")
    run_git(source, "commit", "-m", "ignore local")
    (source / "ignored.local").write_text("local\n", encoding="utf-8")
    monkeypatch.chdir(source)

    plan = plan_git_worktree(Path("."), "agent/relative", Path("../isolated"))

    assert plan.source_repository == source.resolve()
    assert plan.target_path == (tmp_path / "isolated").resolve()
    assert plan.target_display == "../isolated"
    assert Path.cwd() == source
    assert not plan.target_path.exists()
    assert (
        run_git(
            source,
            "show-ref",
            "--verify",
            "--quiet",
            "--",
            "refs/heads/agent/relative",
            check=False,
        ).returncode
        == 1
    )


@pytest.mark.parametrize("kind", ["missing", "file", "non_repository", "subdirectory"])
def test_plan_rejects_invalid_source_paths(tmp_path: Path, kind: str) -> None:
    """Require the supplied source itself to be one primary Git top-level."""

    source = tmp_path / "source"
    target = tmp_path / "worktree"
    if kind == "file":
        source.write_text("not a directory\n", encoding="utf-8")
    elif kind == "non_repository":
        source.mkdir()
    elif kind == "subdirectory":
        repository = create_repository(source)
        source = repository / "nested"
        source.mkdir()

    assert_plan_error(source, "agent/task", target, "source repository")


def test_plan_rejects_bare_linked_and_unborn_repositories(tmp_path: Path) -> None:
    """Accept only a primary non-bare repository with a committed HEAD."""

    bare = tmp_path / "bare.git"
    bare.mkdir()
    run_git(bare, "init", "--bare")
    assert_plan_error(bare, "agent/bare", tmp_path / "bare-target", "primary")

    primary = create_repository(tmp_path / "primary")
    linked = tmp_path / "linked"
    run_git(primary, "worktree", "add", "-b", "linked-source", str(linked))
    assert_plan_error(linked, "agent/linked", tmp_path / "linked-target", "primary")

    unborn = tmp_path / "unborn"
    unborn.mkdir()
    run_git(unborn, "init", "-b", "main")
    assert_plan_error(unborn, "agent/unborn", tmp_path / "unborn-target", "HEAD")


@pytest.mark.parametrize("change", ["staged", "unstaged", "untracked"])
def test_plan_rejects_every_dirty_source_state(tmp_path: Path, change: str) -> None:
    """Include staged, unstaged, and untracked paths in the clean requirement."""

    source = create_repository(tmp_path / "source")
    if change == "staged":
        (source / "tracked.txt").write_text("staged\n", encoding="utf-8")
        run_git(source, "add", "tracked.txt")
    elif change == "unstaged":
        (source / "tracked.txt").write_text("unstaged\n", encoding="utf-8")
    else:
        (source / "untracked.txt").write_text("untracked\n", encoding="utf-8")

    assert_plan_error(source, "agent/dirty", tmp_path / "target", "clean")


@pytest.mark.parametrize(
    "marker",
    [
        "MERGE_HEAD",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "BISECT_LOG",
        "rebase-merge",
        "rebase-apply",
        "sequencer",
        "index.lock",
    ],
)
def test_plan_rejects_in_progress_git_operations(
    tmp_path: Path,
    marker: str,
) -> None:
    """Reject known operation and lock markers without reading their contents."""

    source = create_repository(tmp_path / "source")
    operation_path = source / ".git" / marker
    if "." in marker and marker != "index.lock":
        operation_path.write_text("marker\n", encoding="utf-8")
    elif marker in {"rebase-merge", "rebase-apply", "sequencer"}:
        operation_path.mkdir()
    else:
        operation_path.write_text("marker\n", encoding="utf-8")

    assert_plan_error(source, "agent/operation", tmp_path / "target", "in progress")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("filter.unsafe.clean", "/tmp/clean"),
        ("filter.unsafe.smudge", "/tmp/smudge"),
        ("filter.unsafe.process", "/tmp/process"),
        ("diff.external", "/tmp/diff"),
        ("diff.unsafe.command", "/tmp/diff"),
        ("diff.unsafe.textconv", "/tmp/textconv"),
    ],
)
def test_plan_rejects_repository_local_external_programs(
    tmp_path: Path,
    key: str,
    value: str,
) -> None:
    """Reject local filter and diff configuration that could execute programs."""

    source = create_repository(tmp_path / "source")
    run_git(source, "config", "--local", key, value)

    assert_plan_error(source, "agent/config", tmp_path / "target", "unsafe")


def test_plan_allows_safely_overridden_fsmonitor_and_inert_required_flag(
    tmp_path: Path,
) -> None:
    """Avoid rejecting local settings that cannot execute under fixed overrides."""

    source = create_repository(tmp_path / "source")
    run_git(source, "config", "--local", "core.fsmonitor", "/tmp/fsmonitor")
    run_git(source, "config", "--local", "filter.inert.required", "false")

    plan = plan_git_worktree(source, "agent/safe-config", tmp_path / "target")

    assert plan.branch_name == "agent/safe-config"


@pytest.mark.parametrize(
    ("branch", "match"),
    [
        ("", "non-blank"),
        (" agent/task", "valid local branch"),
        ("agent/task ", "valid local branch"),
        ("-option", "option-like"),
        ("HEAD", "valid local branch"),
        ("bad..name", "valid local branch"),
        (123, "string"),
    ],
)
def test_plan_rejects_invalid_branch_names(
    tmp_path: Path,
    branch: object,
    match: str,
) -> None:
    """Validate exact unmodified branch names through Git check-ref-format."""

    source = create_repository(tmp_path / "source")

    assert_plan_error(source, branch, tmp_path / "target", match)


def test_plan_rejects_existing_branch_without_creating_another(
    tmp_path: Path,
) -> None:
    """Require the requested local branch to remain absent during planning."""

    source = create_repository(tmp_path / "source")
    run_git(source, "branch", "agent/existing")
    before = run_git(source, "for-each-ref", "--format=%(refname)", "refs/heads").stdout

    assert_plan_error(source, "agent/existing", tmp_path / "target", "already exists")

    after = run_git(source, "for-each-ref", "--format=%(refname)", "refs/heads").stdout
    assert after == before


@pytest.mark.parametrize(
    "target_kind",
    [
        "missing_parent",
        "file_parent",
        "symlink_parent",
        "existing_file",
        "existing_directory",
        "inside_source",
        "equal_source",
        "parent_source",
        "inside_git",
        "option_like",
    ],
)
def test_plan_rejects_unsafe_targets(tmp_path: Path, target_kind: str) -> None:
    """Require one absent non-overlapping target under a safe existing parent."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "target"
    if target_kind == "missing_parent":
        target = tmp_path / "missing" / "target"
    elif target_kind == "file_parent":
        parent = tmp_path / "file"
        parent.write_text("file\n", encoding="utf-8")
        target = parent / "target"
    elif target_kind == "symlink_parent":
        real_parent = tmp_path / "real"
        real_parent.mkdir()
        linked_parent = tmp_path / "linked-parent"
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        target = linked_parent / "target"
    elif target_kind == "existing_file":
        target.write_text("exists\n", encoding="utf-8")
    elif target_kind == "existing_directory":
        target.mkdir()
    elif target_kind == "inside_source":
        target = source / "target"
    elif target_kind == "equal_source":
        target = source
    elif target_kind == "parent_source":
        target = tmp_path
    elif target_kind == "inside_git":
        target = source / ".git" / "target"
    elif target_kind == "option_like":
        target = tmp_path / "-target"

    assert_plan_error(source, "agent/target", target, "target")


@pytest.mark.parametrize("record_state", ["registered", "locked", "prunable"])
def test_plan_rejects_registered_worktree_collisions(
    tmp_path: Path,
    record_state: str,
) -> None:
    """Reject exact, containing, locked, and prunable registered records."""

    source = create_repository(tmp_path / "source")
    registered = tmp_path / "registered"
    run_git(source, "worktree", "add", "-b", f"record-{record_state}", str(registered))
    target = registered / "nested"
    if record_state == "locked":
        run_git(source, "worktree", "lock", str(registered))
        shutil.rmtree(registered)
        target = registered
    elif record_state == "prunable":
        shutil.rmtree(registered)
        target = registered

    assert_plan_error(source, "agent/collision", target, "registered worktree")


def test_plan_rejects_target_parent_containing_registered_worktree(
    tmp_path: Path,
) -> None:
    """Never plan a target whose directory would contain another worktree."""

    source = create_repository(tmp_path / "source")
    container = tmp_path / "container"
    container.mkdir()
    registered = container / "registered"
    run_git(source, "worktree", "add", "-b", "record-parent", str(registered))

    assert_plan_error(source, "agent/parent", container, "target")


def test_plan_uses_fixed_non_shell_git_commands_and_minimal_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Use only fixed local Git commands without forwarding parent secrets."""

    source = create_repository(tmp_path / "source")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    from agent_workbench import worktrees

    calls = []
    original = worktrees.subprocess.Popen

    def record(command, **kwargs):
        calls.append((command, kwargs))
        return original(command, **kwargs)

    monkeypatch.setattr(worktrees.subprocess, "Popen", record)

    plan_git_worktree(source, "agent/fixed", tmp_path / "target")

    assert calls
    allowed_subcommands = {
        "rev-parse",
        "status",
        "worktree",
        "check-ref-format",
        "show-ref",
        "config",
    }
    for command, kwargs in calls:
        assert command[0:3] == ["git", "-C", str(source.resolve())]
        assert "core.fsmonitor=false" in command
        assert "core.hooksPath=/dev/null" in command
        assert kwargs["shell"] is False
        assert kwargs["cwd"] == source.resolve()
        assert kwargs["stdin"] is subprocess.DEVNULL
        environment = kwargs["env"]
        assert environment == {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_EXTERNAL_DIFF": "",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "HOME": "/nonexistent",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "XDG_CONFIG_HOME": "/nonexistent",
        }
        assert "OPENAI_API_KEY" not in environment
        assert "AWS_SECRET_ACCESS_KEY" not in environment
        subcommand = next(token for token in command if token in allowed_subcommands)
        assert subcommand in allowed_subcommands
        assert all(token not in {"fetch", "push", "pull", "clone"} for token in command)


def test_plan_git_output_is_bounded_and_timeout_is_safe(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Translate bounded-output and timeout failures without host-path leakage."""

    source = create_repository(tmp_path / "source")
    from agent_workbench import worktrees

    monkeypatch.setattr(worktrees, "MAX_GIT_OUTPUT_BYTES", 8)
    assert_plan_error(source, "agent/bounded", tmp_path / "target", "output")

    monkeypatch.setattr(worktrees, "MAX_GIT_OUTPUT_BYTES", 100 * 1024)

    class TimeoutProcess:
        pid = 987654321
        stdout = None
        stderr = None

        def __init__(self, *args, **kwargs):
            self.stdout = subprocess.PIPE
            self.stderr = subprocess.PIPE

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired(["git"], timeout)

    monkeypatch.setattr(worktrees.subprocess, "Popen", TimeoutProcess)
    monkeypatch.setattr(worktrees, "_terminate_process_group", lambda process: None)

    assert_plan_error(source, "agent/timeout", tmp_path / "target", "timed out")


def approve_worktree(request: WorktreeApprovalRequest) -> ToolApprovalDecision:
    """Approve one supervised worktree lifecycle request."""

    return ToolApprovalDecision.APPROVE


def create_approved_worktree(
    tmp_path: Path,
    *,
    branch: str = "agent/task",
) -> tuple[Path, WorktreePlan, WorktreeHandle]:
    """Create one real approved worktree in a disposable repository."""

    source = create_repository(tmp_path / "source")
    plan = plan_git_worktree(source, branch, tmp_path / "isolated")
    handle = create_git_worktree(plan, approve_worktree)
    return source, plan, handle


def test_lifecycle_models_are_immutable_slotted_and_copy_safe(
    tmp_path: Path,
) -> None:
    """Keep approval, handle, state, and removal plan data immutable and safe."""

    source = create_repository(tmp_path / "source")
    plan = plan_git_worktree(source, "agent/models", tmp_path / "isolated")
    request = WorktreeApprovalRequest(WorktreeAction.CREATE, plan.preview)

    assert not hasattr(request, "__dict__")
    with pytest.raises(FrozenInstanceError):
        request.action = WorktreeAction.REMOVE  # type: ignore[misc]
    preview = request.preview
    preview["action"] = "mutated"
    assert request.preview["action"] == "create_worktree"
    assert str(source.resolve()) not in repr(request)

    handle = create_git_worktree(plan, approve_worktree)
    assert isinstance(handle, WorktreeHandle)
    assert not hasattr(handle, "__dict__")
    assert str(source.resolve()) not in repr(handle)
    assert str(handle.worktree_path) not in repr(handle)
    with pytest.raises(FrozenInstanceError):
        handle.branch_name = "other"  # type: ignore[misc]

    removal = plan_git_worktree_removal(handle)
    assert isinstance(removal, WorktreeRemovalPlan)
    assert not hasattr(removal, "__dict__")
    assert str(source.resolve()) not in repr(removal)
    assert str(handle.worktree_path) not in repr(removal)


@pytest.mark.parametrize("behavior", ["missing", "deny", "invalid", "failure"])
def test_creation_requires_one_explicit_valid_approval(
    tmp_path: Path,
    behavior: str,
) -> None:
    """Create no branch or target without one exact explicit approval."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"
    plan = plan_git_worktree(source, "agent/approval", target)
    requests = []

    def handler(request):
        requests.append(request)
        if behavior == "deny":
            return ToolApprovalDecision.DENY
        if behavior == "invalid":
            return True
        raise RuntimeError("approval unavailable")

    with pytest.raises(Exception, match="approval"):
        create_git_worktree(plan, None if behavior == "missing" else handler)

    assert not target.exists()
    assert (
        run_git(
            source,
            "show-ref",
            "--verify",
            "--quiet",
            "--",
            "refs/heads/agent/approval",
            check=False,
        ).returncode
        == 1
    )
    assert len(requests) == (0 if behavior == "missing" else 1)


def test_creation_is_approved_once_verified_and_has_no_upstream(
    tmp_path: Path,
) -> None:
    """Create one local branch/worktree only after preview and verify identity."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"
    plan = plan_git_worktree(source, "agent/approved", target)
    requests = []

    def handler(request):
        assert not target.exists()
        assert (
            run_git(
                source,
                "show-ref",
                "--verify",
                "--quiet",
                "--",
                "refs/heads/agent/approved",
                check=False,
            ).returncode
            == 1
        )
        requests.append(request)
        return ToolApprovalDecision.APPROVE

    handle = create_git_worktree(plan, handler)

    assert len(requests) == 1
    assert requests[0].action is WorktreeAction.CREATE
    assert requests[0].preview == plan.preview
    assert handle.source_repository == source.resolve()
    assert handle.source_head == plan.source_head
    assert handle.branch_name == "agent/approved"
    assert handle.worktree_path == target.resolve()
    assert target.is_dir()
    assert run_git(target, "rev-parse", "HEAD").stdout.strip() == plan.source_head
    assert (
        run_git(target, "branch", "--show-current").stdout.strip() == "agent/approved"
    )
    assert (
        run_git(
            target,
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}",
            check=False,
        ).returncode
        != 0
    )
    assert run_git(source, "status", "--porcelain").stdout == ""


@pytest.mark.parametrize(
    "stale_state",
    ["head", "dirty", "branch", "target", "unsafe_config", "parent_symlink"],
)
def test_creation_revalidates_every_safety_boundary_after_approval(
    tmp_path: Path,
    stale_state: str,
) -> None:
    """Perform no worktree mutation when any pinned creation state becomes stale."""

    source = create_repository(tmp_path / "source")
    target_parent = tmp_path / "targets"
    target_parent.mkdir()
    target = target_parent / "isolated"
    plan = plan_git_worktree(source, "agent/stale", target)

    def make_stale(_request):
        if stale_state == "head":
            (source / "second.txt").write_text("second\n", encoding="utf-8")
            run_git(source, "add", "second.txt")
            run_git(source, "commit", "-m", "second")
        elif stale_state == "dirty":
            (source / "tracked.txt").write_text("dirty\n", encoding="utf-8")
        elif stale_state == "branch":
            run_git(source, "branch", "agent/stale")
        elif stale_state == "target":
            target.mkdir()
        elif stale_state == "unsafe_config":
            run_git(source, "config", "--local", "filter.bad.smudge", "/tmp/bad")
        else:
            moved_parent = tmp_path / "moved-targets"
            target_parent.rename(moved_parent)
            target_parent.symlink_to(moved_parent, target_is_directory=True)
        return ToolApprovalDecision.APPROVE

    with pytest.raises(Exception, match="stale"):
        create_git_worktree(plan, make_stale)

    assert run_git(
        source,
        "show-ref",
        "--verify",
        "--quiet",
        "--",
        "refs/heads/agent/stale",
        check=False,
    ).returncode == (0 if stale_state == "branch" else 1)
    if stale_state not in {"target", "parent_symlink"}:
        assert not target.exists()


def test_creation_uses_one_fixed_non_shell_mutation_command(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Disable hooks/fsmonitor and use no caller flags or parent environment."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"
    plan = plan_git_worktree(source, "agent/fixed-create", target)
    from agent_workbench import worktrees

    calls = []
    original = worktrees.subprocess.Popen

    def record(command, **kwargs):
        calls.append((command, kwargs))
        return original(command, **kwargs)

    monkeypatch.setattr(worktrees.subprocess, "Popen", record)
    create_git_worktree(plan, approve_worktree)

    mutations = [
        (command, kwargs)
        for command, kwargs in calls
        if "add" in command and "worktree" in command
    ]
    assert len(mutations) == 1
    command, kwargs = mutations[0]
    assert command == [
        "git",
        "-C",
        str(source.resolve()),
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "worktree",
        "add",
        "-b",
        "agent/fixed-create",
        str(target.resolve()),
        plan.source_head,
    ]
    assert kwargs["shell"] is False
    assert kwargs["start_new_session"] is True
    assert kwargs["env"]["GIT_CONFIG_GLOBAL"] == os.devnull
    assert "OPENAI_API_KEY" not in kwargs["env"]


@pytest.mark.parametrize("partial", ["branch", "target", "registered"])
def test_creation_failure_reports_partial_state_without_cleanup(
    tmp_path: Path,
    monkeypatch,
    partial: str,
) -> None:
    """Preserve and safely report every partial creation outcome."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"
    plan = plan_git_worktree(source, "agent/partial", target)
    from agent_workbench import worktrees

    original = worktrees._run_git

    def fail_creation(repository, arguments):
        if arguments[:2] != ("worktree", "add"):
            return original(repository, arguments)
        if partial == "branch":
            run_git(source, "branch", "agent/partial")
        elif partial == "target":
            target.mkdir()
        else:
            run_git(
                source,
                "worktree",
                "add",
                "-b",
                "agent/partial",
                str(target),
                plan.source_head,
            )
        return worktrees._GitOutput(1, "", "injected")

    monkeypatch.setattr(worktrees, "_run_git", fail_creation)

    with pytest.raises(Exception, match="partial state") as raised:
        create_git_worktree(plan, approve_worktree)

    message = str(raised.value)
    assert "agent/partial" in message
    assert "../isolated" in message
    assert str(source.resolve()) not in message
    if partial == "branch":
        assert not target.exists()
    else:
        assert target.exists()
    assert run_git(
        source,
        "show-ref",
        "--verify",
        "--quiet",
        "--",
        "refs/heads/agent/partial",
        check=False,
    ).returncode == (1 if partial == "target" else 0)


def test_inspection_reports_clean_and_all_dirty_entry_classes(
    tmp_path: Path,
) -> None:
    """Report bounded immutable identity and tracked/staged/untracked dirtiness."""

    _, _, handle = create_approved_worktree(tmp_path)

    clean = inspect_git_worktree(handle)
    assert clean.registered is True
    assert clean.branch_name == "agent/task"
    assert clean.head == handle.source_head
    assert clean.clean is True
    assert clean.changed_entry_count == 0
    assert clean.target_display == "../isolated"
    assert str(handle.worktree_path) not in repr(clean)

    (handle.worktree_path / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    assert inspect_git_worktree(handle).changed_entry_count == 1
    run_git(handle.worktree_path, "add", "tracked.txt")
    assert inspect_git_worktree(handle).changed_entry_count == 1
    (handle.worktree_path / "untracked.txt").write_text("new\n", encoding="utf-8")
    dirty = inspect_git_worktree(handle)
    assert dirty.clean is False
    assert dirty.changed_entry_count == 2


@pytest.mark.parametrize("identity_failure", ["branch", "detached", "missing"])
def test_inspection_rejects_changed_or_missing_identity(
    tmp_path: Path,
    identity_failure: str,
) -> None:
    """Never trust a switched, detached, or missing worktree."""

    _, _, handle = create_approved_worktree(tmp_path)
    if identity_failure == "branch":
        run_git(handle.worktree_path, "switch", "-c", "other")
    elif identity_failure == "detached":
        run_git(handle.worktree_path, "switch", "--detach")
    else:
        shutil.rmtree(handle.worktree_path)

    with pytest.raises(Exception) as raised:
        inspect_git_worktree(handle)

    assert str(handle.worktree_path) not in str(raised.value)


def test_clean_removal_requires_approval_and_preserves_branch(
    tmp_path: Path,
) -> None:
    """Remove only the clean registered worktree while retaining its local branch."""

    source, _, handle = create_approved_worktree(tmp_path)
    removal = plan_git_worktree_removal(handle)
    requests = []

    def approve(request):
        requests.append(request)
        return ToolApprovalDecision.APPROVE

    remove_git_worktree(removal, approve)

    assert len(requests) == 1
    assert requests[0].action is WorktreeAction.REMOVE
    assert requests[0].preview == removal.preview
    assert not handle.worktree_path.exists()
    assert (
        str(handle.worktree_path)
        not in run_git(
            source,
            "worktree",
            "list",
            "--porcelain",
        ).stdout
    )
    assert (
        run_git(
            source,
            "show-ref",
            "--verify",
            "--quiet",
            "--",
            "refs/heads/agent/task",
            check=False,
        ).returncode
        == 0
    )
    assert run_git(source, "status", "--porcelain").stdout == ""


def test_clean_removal_uses_one_fixed_non_force_command(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Run only the exact fixed removal command without force or branch deletion."""

    source, _, handle = create_approved_worktree(tmp_path)
    removal = plan_git_worktree_removal(handle)
    from agent_workbench import worktrees

    calls = []
    original = worktrees.subprocess.Popen

    def record(command, **kwargs):
        calls.append((command, kwargs))
        return original(command, **kwargs)

    monkeypatch.setattr(worktrees.subprocess, "Popen", record)
    remove_git_worktree(removal, approve_worktree)

    mutations = [
        (command, kwargs)
        for command, kwargs in calls
        if "remove" in command and "worktree" in command
    ]
    assert len(mutations) == 1
    command, kwargs = mutations[0]
    assert command == [
        "git",
        "-C",
        str(source.resolve()),
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "worktree",
        "remove",
        str(handle.worktree_path),
    ]
    assert "--force" not in command
    assert "-f" not in command
    assert kwargs["shell"] is False
    assert kwargs["start_new_session"] is True
    assert kwargs["env"]["GIT_CONFIG_GLOBAL"] == os.devnull


@pytest.mark.parametrize("behavior", ["missing", "deny", "invalid", "failure"])
def test_removal_requires_one_explicit_valid_approval(
    tmp_path: Path,
    behavior: str,
) -> None:
    """Preserve the worktree and branch on absent, denied, or invalid approval."""

    source, _, handle = create_approved_worktree(tmp_path)
    removal = plan_git_worktree_removal(handle)

    def handler(_request):
        if behavior == "deny":
            return ToolApprovalDecision.DENY
        if behavior == "invalid":
            return True
        raise RuntimeError("approval unavailable")

    with pytest.raises(Exception, match="approval"):
        remove_git_worktree(removal, None if behavior == "missing" else handler)

    assert handle.worktree_path.exists()
    assert "agent/task" in run_git(source, "branch", "--list", "agent/task").stdout


@pytest.mark.parametrize("dirty_kind", ["tracked", "staged", "untracked"])
def test_dirty_worktree_is_rejected_before_removal_approval(
    tmp_path: Path,
    dirty_kind: str,
) -> None:
    """Reject dirty cleanup planning without requesting approval."""

    _, _, handle = create_approved_worktree(tmp_path)
    if dirty_kind == "untracked":
        (handle.worktree_path / "untracked.txt").write_text("new\n", encoding="utf-8")
    else:
        (handle.worktree_path / "tracked.txt").write_text("dirty\n", encoding="utf-8")
        if dirty_kind == "staged":
            run_git(handle.worktree_path, "add", "tracked.txt")

    with pytest.raises(Exception, match="clean"):
        plan_git_worktree_removal(handle)

    assert handle.worktree_path.exists()


def test_locked_worktree_is_rejected_before_removal_approval(
    tmp_path: Path,
) -> None:
    """Preserve a locked worktree without creating a removal plan."""

    source, _, handle = create_approved_worktree(tmp_path)
    run_git(source, "worktree", "lock", str(handle.worktree_path))

    with pytest.raises(Exception, match="registration"):
        plan_git_worktree_removal(handle)

    assert handle.worktree_path.exists()


def test_removal_revalidates_after_approval_and_never_forces(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Preserve a newly dirty target and issue no force or branch-delete command."""

    source, _, handle = create_approved_worktree(tmp_path)
    removal = plan_git_worktree_removal(handle)
    from agent_workbench import worktrees

    calls = []
    original = worktrees._run_git

    def record(repository, arguments):
        calls.append(arguments)
        return original(repository, arguments)

    monkeypatch.setattr(worktrees, "_run_git", record)

    def dirty_after_preview(_request):
        (handle.worktree_path / "untracked.txt").write_text("new\n", encoding="utf-8")
        return ToolApprovalDecision.APPROVE

    with pytest.raises(Exception, match="stale"):
        remove_git_worktree(removal, dirty_after_preview)

    assert handle.worktree_path.exists()
    assert not any(arguments[:2] == ("worktree", "remove") for arguments in calls)
    assert not any("--force" in arguments or "-D" in arguments for arguments in calls)
    assert "agent/task" in run_git(source, "branch", "--list", "agent/task").stdout


def test_removal_command_failure_preserves_worktree_without_force(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Report failed clean removal and preserve all remaining recoverable state."""

    source, _, handle = create_approved_worktree(tmp_path)
    removal = plan_git_worktree_removal(handle)
    from agent_workbench import worktrees

    original = worktrees._run_git
    mutation_arguments = []

    def fail_removal(repository, arguments):
        if arguments[:2] == ("worktree", "remove"):
            mutation_arguments.append(arguments)
            return worktrees._GitOutput(1, "", "injected")
        return original(repository, arguments)

    monkeypatch.setattr(worktrees, "_run_git", fail_removal)

    with pytest.raises(Exception, match="preserved"):
        remove_git_worktree(removal, approve_worktree)

    assert handle.worktree_path.exists()
    assert mutation_arguments == [
        ("worktree", "remove", str(handle.worktree_path)),
    ]
    assert "--force" not in mutation_arguments[0]
    assert "agent/task" in run_git(source, "branch", "--list", "agent/task").stdout
