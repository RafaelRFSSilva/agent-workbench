"""End-to-end CLI tests for read-only lifecycle recovery inspection."""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_workbench.cli import CANCELLATION_MESSAGE, main
from agent_workbench.coding_loop import (
    AutonomousCodingResult,
    CodingPhase,
    ValidationRun,
)
from agent_workbench.errors import CompletionError, ConfigurationError
from agent_workbench.isolated_coding import run_isolated_autonomous_workflow
from agent_workbench.lifecycle import (
    IsolatedCommitLifecyclePhase,
    IsolatedCommitLifecycleRecord,
)
from agent_workbench.lifecycle_store import IsolatedCommitLifecycleStore
from agent_workbench.arguments import RuntimeConfiguration
from agent_workbench.session import SessionId
from agent_workbench.tasks import TaskSpec
from agent_workbench.tools import ToolApprovalDecision


def run_git(
    repository: Path,
    *arguments: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run one deterministic Git command in a disposable repository."""

    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=check,
        capture_output=True,
        text=True,
    )


def create_repository(root: Path) -> Path:
    """Create one clean committed repository with local identity configured."""

    root.mkdir()
    run_git(root, "init", "-b", "main")
    run_git(root, "config", "user.name", "Recovery Test User")
    run_git(root, "config", "user.email", "recovery-test@example.invalid")
    (root / "module.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left - right\n",
        encoding="utf-8",
    )
    (root / "test_module.py").write_text(
        "from module import add\n\n\n"
        "def test_add() -> None:\n"
        "    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    run_git(root, "add", ".")
    run_git(root, "commit", "-m", "initial")
    return root


def create_store(tmp_path: Path) -> IsolatedCommitLifecycleStore:
    """Create one operator-managed existing lifecycle store directory."""

    directory = tmp_path / "lifecycle-store"
    directory.mkdir()
    return IsolatedCommitLifecycleStore(directory)


class RecordingLifecycleStore(IsolatedCommitLifecycleStore):
    """Capture lifecycle writes while delegating to the crash-safe store."""

    __slots__ = ("writes", "fail_on_call", "failure", "delegate_before_failure")

    def __init__(
        self,
        directory: Path,
        *,
        fail_on_call: int | None = None,
        failure: Exception | None = None,
        delegate_before_failure: bool = False,
    ) -> None:
        super().__init__(directory)
        self.writes: list[IsolatedCommitLifecycleRecord] = []
        self.fail_on_call = fail_on_call
        self.failure = failure or CompletionError("injected lifecycle store failure")
        self.delegate_before_failure = delegate_before_failure

    def write(self, record: IsolatedCommitLifecycleRecord) -> None:
        self.writes.append(record)
        should_fail = self.fail_on_call == len(self.writes)
        if should_fail and not self.delegate_before_failure:
            raise self.failure
        super().write(record)
        if should_fail and self.delegate_before_failure:
            raise self.failure


def create_lifecycle_store(
    tmp_path: Path,
    *,
    fail_on_call: int | None = None,
    failure: Exception | None = None,
    delegate_before_failure: bool = False,
) -> RecordingLifecycleStore:
    """Create one dedicated existing lifecycle store directory for testing."""

    directory = tmp_path / "lifecycle-store"
    directory.mkdir()
    return RecordingLifecycleStore(
        directory,
        fail_on_call=fail_on_call,
        failure=failure,
        delegate_before_failure=delegate_before_failure,
    )


def configuration(source: Path) -> RuntimeConfiguration:
    """Create one minimal source-bound runtime configuration."""

    return RuntimeConfiguration(
        provider_name="ollama",
        model_name="test-model",
        workspace_root=source,
        enable_actions=True,
    )


def approve(_request) -> ToolApprovalDecision:
    """Approve one deterministic supervised action."""

    return ToolApprovalDecision.APPROVE


def coding_result(
    *,
    approved_workspace_paths: tuple[str, ...] = ("module.py",),
) -> AutonomousCodingResult:
    """Create one deterministic successful autonomous coding result."""

    return AutonomousCodingResult(
        task_spec=TaskSpec(
            objective="Correct the add implementation.",
            acceptance_criteria=("Run validation.",),
        ),
        assistant_summary="Corrected the implementation.",
        final_phase=CodingPhase.DONE,
        workspace_change_applied=True,
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
                sequence_index=3,
            ),
            ValidationRun(
                tool_name="run_ruff_check",
                result_status="success",
                exit_code=0,
                sequence_index=4,
            ),
            ValidationRun(
                tool_name="run_pytest",
                result_status="success",
                exit_code=0,
                sequence_index=5,
            ),
        ),
        tool_results=(),
        inspected_git_status=True,
        inspected_git_diff=True,
        last_workspace_change_sequence_index=2,
        latest_git_status_sequence_index=5,
        latest_git_diff_sequence_index=6,
        approved_workspace_paths=approved_workspace_paths,
    )


def install_successful_coding_stub(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    """Patch isolated session creation and successful coding for workflow tests."""

    captured: dict[str, object] = {}

    def create_isolated(_session_id, _runtime, worktree, *, max_tool_rounds):
        assert max_tool_rounds == 16
        captured["worktree"] = worktree
        return SimpleNamespace(worktree=worktree, session=object())

    def run_coding(_session, _prompt, **_kwargs):
        worktree = captured["worktree"]
        worktree_path = worktree.worktree_path
        (worktree_path / "module.py").write_text(
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
    return captured


def lifecycle_filename(session_id: SessionId) -> str:
    """Return one deterministic lifecycle-record filename."""

    digest = hashlib.sha256(session_id.value.encode("utf-8")).hexdigest()
    return f"isolated-commit-{digest}.json"


def run_main_and_capture_exit_code(argv: list[str]) -> int:
    """Normalize SystemExit to one integer code for CLI assertions."""

    try:
        main(argv)
    except SystemExit as exc:
        if exc.code is None:
            return 0
        if isinstance(exc.code, int):
            return exc.code
        return 1
    return 0


def persist_record(
    store: IsolatedCommitLifecycleStore,
    *,
    session_id: SessionId,
    phase: IsolatedCommitLifecyclePhase,
    source_head: str,
    branch_name: str,
    old_head: str,
    paths: tuple[str, ...],
    commit_message: str,
    new_head: str | None,
) -> IsolatedCommitLifecycleRecord:
    """Persist one lifecycle record with complete required metadata."""

    record = IsolatedCommitLifecycleRecord(
        session_id=session_id,
        phase=phase,
        target_display="isolated",
        source_head=source_head,
        source_branch="main",
        branch_name=branch_name,
        old_head=old_head,
        paths=paths,
        diff_fingerprint="0" * 64,
        commit_message_fingerprint=hashlib.sha256(
            commit_message.encode("utf-8")
        ).hexdigest(),
        new_head=new_head,
    )
    store.write(record)
    return record


def snapshot_repo_state(repository: Path, branch_name: str) -> dict[str, str]:
    """Capture state that recover must not mutate."""

    return {
        "head": run_git(repository, "rev-parse", "HEAD").stdout.strip(),
        "status": run_git(repository, "status", "--short", "--branch").stdout,
        "worktree_list": run_git(repository, "worktree", "list", "--porcelain").stdout,
        "branch": run_git(repository, "branch", "--list", branch_name).stdout,
    }


def snapshot_source_and_worktree_state(
    source_repository: Path,
    worktree_repository: Path,
    branch_name: str,
) -> dict[str, str]:
    """Capture source and worktree state that recover must not mutate."""

    state = snapshot_repo_state(source_repository, branch_name)
    state["worktree_head"] = run_git(
        worktree_repository, "rev-parse", "HEAD"
    ).stdout.strip()
    state["worktree_status"] = run_git(
        worktree_repository,
        "status",
        "--short",
        "--branch",
    ).stdout
    return state


def create_isolated_worktree(source: Path, branch_name: str) -> Path:
    """Create one real registered worktree for restart-inspection scenarios."""

    target = source / "isolated"
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "worktree",
            "add",
            "-b",
            branch_name,
            str(target),
            "HEAD",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return target


def test_recover_planned_old_head_clean_index(tmp_path: Path, capsys) -> None:
    """A: PLANNED old HEAD clean index classifies old_head_clean_index."""

    source = create_repository(tmp_path / "source")
    source_head = run_git(source, "rev-parse", "HEAD").stdout.strip()
    branch_name = "agent/recover-a"
    create_isolated_worktree(source, branch_name)

    store = create_store(tmp_path)
    session_id = SessionId("task-a")
    persist_record(
        store,
        session_id=session_id,
        phase=IsolatedCommitLifecyclePhase.PLANNED,
        source_head=source_head,
        branch_name=branch_name,
        old_head=source_head,
        paths=("module.py",),
        commit_message="fix: task a",
        new_head=None,
    )

    persisted_before = store.read(session_id)
    assert persisted_before is not None

    before = snapshot_repo_state(source, branch_name)
    exit_code = run_main_and_capture_exit_code(
        [
            "recover",
            "--workspace",
            str(source),
            "--lifecycle-store",
            str(store._directory),  # type: ignore[attr-defined]
            "--session-id",
            session_id.value,
        ]
    )
    persisted_after = store.read(session_id)
    after = snapshot_repo_state(source, branch_name)

    assert exit_code == 0
    assert persisted_after == persisted_before
    assert before == after
    output = capsys.readouterr().out
    assert "[RECOVERY] Persisted phase: planned" in output
    assert "[RECOVERY] Classification: old_head_clean_index" in output
    assert "[RECOVERY] No recovery action was performed." in output
    assert (
        "[RECOVERY] Any future mutating recovery action requires fresh approval."
        in output
    )


def test_recover_execution_started_old_head_clean_index(
    tmp_path: Path,
    capsys,
) -> None:
    """B: EXECUTION_STARTED with clean old HEAD classifies old_head_clean_index."""

    source = create_repository(tmp_path / "source")
    source_head = run_git(source, "rev-parse", "HEAD").stdout.strip()
    branch_name = "agent/recover-b"
    create_isolated_worktree(source, branch_name)

    store = create_store(tmp_path)
    session_id = SessionId("task-b")
    persist_record(
        store,
        session_id=session_id,
        phase=IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
        source_head=source_head,
        branch_name=branch_name,
        old_head=source_head,
        paths=("module.py",),
        commit_message="fix: task b",
        new_head=None,
    )

    exit_code = run_main_and_capture_exit_code(
        [
            "recover",
            "--workspace",
            str(source),
            "--lifecycle-store",
            str(store._directory),  # type: ignore[attr-defined]
            "--session-id",
            session_id.value,
        ]
    )

    assert exit_code == 0
    assert "[RECOVERY] Classification: old_head_clean_index" in capsys.readouterr().out


def test_recover_execution_started_staged_paths_observed(
    tmp_path: Path,
    capsys,
) -> None:
    """C: EXECUTION_STARTED with expected staged paths classifies expected staging."""

    source = create_repository(tmp_path / "source")
    source_head = run_git(source, "rev-parse", "HEAD").stdout.strip()
    branch_name = "agent/recover-c"
    target = create_isolated_worktree(source, branch_name)

    (target / "module.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left + right\n",
        encoding="utf-8",
    )
    run_git(target, "add", "module.py")

    store = create_store(tmp_path)
    session_id = SessionId("task-c")
    persist_record(
        store,
        session_id=session_id,
        phase=IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
        source_head=source_head,
        branch_name=branch_name,
        old_head=source_head,
        paths=("module.py",),
        commit_message="fix: task c",
        new_head=None,
    )

    before = snapshot_repo_state(source, branch_name)
    exit_code = run_main_and_capture_exit_code(
        [
            "recover",
            "--workspace",
            str(source),
            "--lifecycle-store",
            str(store._directory),  # type: ignore[attr-defined]
            "--session-id",
            session_id.value,
        ]
    )
    after = snapshot_repo_state(source, branch_name)

    assert exit_code == 0
    assert before == after
    output = capsys.readouterr().out
    assert "[RECOVERY] Classification: expected_path_staging_observed" in output


def test_recover_commit_candidate_observed(tmp_path: Path, monkeypatch, capsys) -> None:
    """D: Real crash-window workflow yields commit_candidate_observed recovery."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"
    branch_name = "agent/recover-d"
    session_id = SessionId("task-d")
    commit_message = "fix: candidate"
    store = create_lifecycle_store(
        tmp_path,
        fail_on_call=3,
        failure=CompletionError("injected verified failure"),
    )
    install_successful_coding_stub(monkeypatch)

    with pytest.raises(
        CompletionError, match="VERIFIED lifecycle checkpoint persistence"
    ):
        run_isolated_autonomous_workflow(
            session_id,
            configuration(source),
            branch_name,
            target,
            "Correct the add implementation.",
            commit_message,
            worktree_approval_handler=approve,
            tool_approval_handler=approve,
            commit_approval_handler=approve,
            lifecycle_store=store,
        )

    assert [record.phase for record in store.writes] == [
        IsolatedCommitLifecyclePhase.PLANNED,
        IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
        IsolatedCommitLifecyclePhase.VERIFIED,
    ]
    persisted_before = store.read(session_id)
    assert persisted_before is not None
    assert persisted_before.phase is IsolatedCommitLifecyclePhase.EXECUTION_STARTED
    assert run_git(target, "log", "-1", "--pretty=%s").stdout.strip() == commit_message
    assert run_git(target, "branch", "--show-current").stdout.strip() == branch_name
    assert (
        run_git(source, "rev-parse", branch_name).stdout.strip()
        != persisted_before.old_head
    )

    before = snapshot_source_and_worktree_state(source, target, branch_name)
    exit_code = run_main_and_capture_exit_code(
        [
            "recover",
            "--workspace",
            str(source),
            "--lifecycle-store",
            str(store._directory),  # type: ignore[attr-defined]
            "--session-id",
            session_id.value,
        ]
    )
    persisted_after = store.read(session_id)
    after = snapshot_source_and_worktree_state(source, target, branch_name)

    assert exit_code == 0
    assert persisted_after == persisted_before
    assert persisted_after is not None
    assert persisted_after.phase is IsolatedCommitLifecyclePhase.EXECUTION_STARTED
    assert before == after

    output = capsys.readouterr().out
    assert "[RECOVERY] Persisted phase: execution_started" in output
    assert "[RECOVERY] Classification: commit_candidate_observed" in output
    assert "[RECOVERY] Candidate parent matches old HEAD: yes" in output
    assert "[RECOVERY] Candidate commit message matches: yes" in output
    assert "[RECOVERY] Candidate committed paths match expected: yes" in output
    assert "does not prove it is the exact originally approved commit" in output


def test_recover_verified_observed(tmp_path: Path, capsys) -> None:
    """E: VERIFIED record with observed persisted commit classification."""

    source = create_repository(tmp_path / "source")
    old_head = run_git(source, "rev-parse", "HEAD").stdout.strip()
    branch_name = "agent/recover-e"
    run_git(source, "checkout", "-b", branch_name)

    commit_message = "fix: verified"
    (source / "module.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left + right\n",
        encoding="utf-8",
    )
    run_git(source, "add", "module.py")
    run_git(source, "commit", "-m", commit_message)
    new_head = run_git(source, "rev-parse", "HEAD").stdout.strip()

    store = create_store(tmp_path)
    session_id = SessionId("task-e")
    persist_record(
        store,
        session_id=session_id,
        phase=IsolatedCommitLifecyclePhase.VERIFIED,
        source_head=old_head,
        branch_name=branch_name,
        old_head=old_head,
        paths=("module.py",),
        commit_message=commit_message,
        new_head=new_head,
    )

    exit_code = run_main_and_capture_exit_code(
        [
            "recover",
            "--workspace",
            str(source),
            "--lifecycle-store",
            str(store._directory),  # type: ignore[attr-defined]
            "--session-id",
            session_id.value,
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "[RECOVERY] Persisted phase: verified" in output
    assert "[RECOVERY] Classification: persisted_verified_commit_observed" in output


def test_recover_diverged_or_inconsistent(tmp_path: Path, capsys) -> None:
    """F: Incompatible branch state classifies diverged_or_inconsistent."""

    source = create_repository(tmp_path / "source")
    source_head = run_git(source, "rev-parse", "HEAD").stdout.strip()
    branch_name = "agent/recover-f"

    store = create_store(tmp_path)
    session_id = SessionId("task-f")
    persist_record(
        store,
        session_id=session_id,
        phase=IsolatedCommitLifecyclePhase.PLANNED,
        source_head=source_head,
        branch_name=branch_name,
        old_head=source_head,
        paths=("module.py",),
        commit_message="fix: task f",
        new_head=None,
    )

    exit_code = run_main_and_capture_exit_code(
        [
            "recover",
            "--workspace",
            str(source),
            "--lifecycle-store",
            str(store._directory),  # type: ignore[attr-defined]
            "--session-id",
            session_id.value,
        ]
    )

    assert exit_code == 0
    assert (
        "[RECOVERY] Classification: diverged_or_inconsistent" in capsys.readouterr().out
    )


def test_recover_insufficient_evidence_from_bounded_unknown_observation(
    tmp_path: Path,
    capsys,
) -> None:
    """G: Cover one bounded insufficient-evidence read-only observation path."""

    source = create_repository(tmp_path / "source")
    source_head = run_git(source, "rev-parse", "HEAD").stdout.strip()
    branch_name = "agent/recover-g"
    run_git(source, "branch", branch_name, source_head)

    store = create_store(tmp_path)
    session_id = SessionId("task-g")
    persist_record(
        store,
        session_id=session_id,
        phase=IsolatedCommitLifecyclePhase.PLANNED,
        source_head=source_head,
        branch_name=branch_name,
        old_head=source_head,
        paths=("module.py",),
        commit_message="fix: task g",
        new_head=None,
    )

    exit_code = run_main_and_capture_exit_code(
        [
            "recover",
            "--workspace",
            str(source),
            "--lifecycle-store",
            str(store._directory),  # type: ignore[attr-defined]
            "--session-id",
            session_id.value,
        ]
    )

    assert exit_code == 0
    assert "[RECOVERY] Classification: insufficient_evidence" in capsys.readouterr().out


def test_recover_missing_record_returns_exit_1(tmp_path: Path, capsys) -> None:
    """Return status 1 when the requested lifecycle record is absent."""

    source = create_repository(tmp_path / "source")
    store = create_store(tmp_path)

    exit_code = run_main_and_capture_exit_code(
        [
            "recover",
            "--workspace",
            str(source),
            "--lifecycle-store",
            str(store._directory),  # type: ignore[attr-defined]
            "--session-id",
            "missing-session",
        ]
    )

    assert exit_code == 1
    assert "requested lifecycle record was not found" in capsys.readouterr().out


def test_recover_invalid_lifecycle_store_returns_exit_1(
    tmp_path: Path,
    capsys,
) -> None:
    """Return status 1 for invalid lifecycle-store directories."""

    source = create_repository(tmp_path / "source")
    missing_store = tmp_path / "missing-store"

    exit_code = run_main_and_capture_exit_code(
        [
            "recover",
            "--workspace",
            str(source),
            "--lifecycle-store",
            str(missing_store),
            "--session-id",
            "task-invalid-store",
        ]
    )

    assert exit_code == 1
    assert "lifecycle store directory does not exist" in capsys.readouterr().out


def test_recover_corrupt_record_returns_exit_1(tmp_path: Path, capsys) -> None:
    """Return status 1 when the requested lifecycle record payload is corrupt."""

    source = create_repository(tmp_path / "source")
    store = create_store(tmp_path)
    session_id = SessionId("corrupt")
    (store._directory / lifecycle_filename(session_id)).write_bytes(b"{not-json\n")  # type: ignore[attr-defined]

    exit_code = run_main_and_capture_exit_code(
        [
            "recover",
            "--workspace",
            str(source),
            "--lifecycle-store",
            str(store._directory),  # type: ignore[attr-defined]
            "--session-id",
            session_id.value,
        ]
    )

    assert exit_code == 1
    assert "Recovery inspection failed:" in capsys.readouterr().out


@pytest.mark.parametrize(
    "error",
    [
        ConfigurationError("read-only configuration failure"),
        CompletionError("read-only operational failure"),
    ],
)
def test_recover_bounded_read_only_inspection_failures(
    tmp_path: Path,
    monkeypatch,
    capsys,
    error: Exception,
) -> None:
    """Return status 1 without traceback for bounded recover inspection failures."""

    source = create_repository(tmp_path / "source")
    store = create_store(tmp_path)

    monkeypatch.setattr(
        "agent_workbench.cli.inspect_persisted_isolated_commit_lifecycle_recovery",
        Mock(side_effect=error),
    )

    exit_code = run_main_and_capture_exit_code(
        [
            "recover",
            "--workspace",
            str(source),
            "--lifecycle-store",
            str(store._directory),  # type: ignore[attr-defined]
            "--session-id",
            "task-error",
        ]
    )

    assert exit_code == 1
    assert "Recovery inspection failed:" in capsys.readouterr().out


def test_recover_never_constructs_session_or_approval(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Recover must not create AgentSession or approval prompts."""

    source = create_repository(tmp_path / "source")
    source_head = run_git(source, "rev-parse", "HEAD").stdout.strip()
    branch_name = "agent/recover-readonly"
    run_git(source, "branch", branch_name, source_head)

    store = create_store(tmp_path)
    session_id = SessionId("task-readonly")
    persist_record(
        store,
        session_id=session_id,
        phase=IsolatedCommitLifecyclePhase.PLANNED,
        source_head=source_head,
        branch_name=branch_name,
        old_head=source_head,
        paths=("module.py",),
        commit_message="fix: readonly",
        new_head=None,
    )

    create_session_mock = Mock(side_effect=AssertionError("must not create session"))
    prompt_mock = Mock(side_effect=AssertionError("must not request approval"))

    monkeypatch.setattr("agent_workbench.cli.create_agent_session", create_session_mock)
    monkeypatch.setattr("builtins.input", prompt_mock)

    exit_code = run_main_and_capture_exit_code(
        [
            "recover",
            "--workspace",
            str(source),
            "--lifecycle-store",
            str(store._directory),  # type: ignore[attr-defined]
            "--session-id",
            session_id.value,
        ]
    )

    assert exit_code == 0
    create_session_mock.assert_not_called()
    prompt_mock.assert_not_called()


def test_recover_output_does_not_leak_sensitive_details(
    tmp_path: Path,
    capsys,
) -> None:
    """Recover output must not leak paths, messages, fingerprints, or abs paths."""

    source = create_repository(tmp_path / "source")
    source_head = run_git(source, "rev-parse", "HEAD").stdout.strip()
    branch_name = "agent/recover-redact"
    run_git(source, "branch", branch_name, source_head)

    store = create_store(tmp_path)
    session_id = SessionId("task-redact")
    commit_message = "fix: do not leak this"
    persist_record(
        store,
        session_id=session_id,
        phase=IsolatedCommitLifecyclePhase.PLANNED,
        source_head=source_head,
        branch_name=branch_name,
        old_head=source_head,
        paths=("module.py", "test_module.py"),
        commit_message=commit_message,
        new_head=None,
    )

    exit_code = run_main_and_capture_exit_code(
        [
            "recover",
            "--workspace",
            str(source),
            "--lifecycle-store",
            str(store._directory),  # type: ignore[attr-defined]
            "--session-id",
            session_id.value,
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "module.py" not in output
    assert "test_module.py" not in output
    assert commit_message not in output
    assert "0" * 64 not in output
    assert str(source) not in output
    assert str(store._directory) not in output  # type: ignore[attr-defined]


def test_recover_keyboard_interrupt_uses_outer_boundary(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    """Recover interruptions must use the existing top-level status 130 boundary."""

    source = create_repository(tmp_path / "source")
    store = create_store(tmp_path)

    monkeypatch.setattr(
        "agent_workbench.cli.inspect_persisted_isolated_commit_lifecycle_recovery",
        Mock(side_effect=KeyboardInterrupt),
    )

    with pytest.raises(SystemExit) as raised:
        main(
            [
                "recover",
                "--workspace",
                str(source),
                "--lifecycle-store",
                str(store._directory),  # type: ignore[attr-defined]
                "--session-id",
                "task-interrupt",
            ]
        )

    assert raised.value.code == 130
    captured = capsys.readouterr()
    assert captured.out == f"{CANCELLATION_MESSAGE}\n"
    assert captured.err == ""
