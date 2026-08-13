"""Tests for explicit mutating lifecycle recovery actions."""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess

import pytest

import agent_workbench.lifecycle_recovery_actions as lifecycle_recovery_actions
from agent_workbench.errors import CompletionError, ConfigurationError
from agent_workbench.lifecycle import (
    IsolatedCommitLifecyclePhase,
    IsolatedCommitLifecycleRecord,
)
from agent_workbench.lifecycle_recovery import (
    IsolatedCommitLifecycleRecoveryClassification,
    inspect_persisted_isolated_commit_lifecycle_recovery,
)
from agent_workbench.lifecycle_recovery_actions import (
    IsolatedCommitLifecycleRecoveryActionStatus,
    adopt_isolated_commit_recovery_candidate,
)
from agent_workbench.lifecycle_store import IsolatedCommitLifecycleStore
from agent_workbench.session import SessionId
from agent_workbench.tools import ToolApprovalDecision


def run_git(repository: Path, *arguments: str, check: bool = True):
    """Run one deterministic Git command."""

    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=check,
        capture_output=True,
        text=True,
    )


def create_repository(root: Path) -> Path:
    """Create one committed repository with local identity configured."""

    root.mkdir()
    run_git(root, "init", "-b", "main")
    run_git(root, "config", "user.name", "Recovery Action Test")
    run_git(root, "config", "user.email", "recovery-action@example.invalid")
    (root / "module.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left - right\n",
        encoding="utf-8",
    )
    run_git(root, "add", "module.py")
    run_git(root, "commit", "-m", "initial")
    return root


def create_store(tmp_path: Path) -> IsolatedCommitLifecycleStore:
    """Create one existing lifecycle store."""

    directory = tmp_path / "lifecycle-store"
    directory.mkdir()
    return IsolatedCommitLifecycleStore(directory)


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
    """Persist one lifecycle record with complete required fields."""

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


def create_candidate_observed_state(
    tmp_path: Path,
    *,
    session_value: str,
    commit_message: str,
) -> tuple[Path, Path, IsolatedCommitLifecycleStore, SessionId, str, str]:
    """Build one real candidate-observed persisted EXECUTION_STARTED state."""

    source = create_repository(tmp_path / "source")
    old_head = run_git(source, "rev-parse", "HEAD").stdout.strip()
    branch_name = "agent/recovery-action"
    target = source / "isolated"
    run_git(source, "worktree", "add", "-b", branch_name, str(target), "HEAD")

    (target / "module.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left + right\n",
        encoding="utf-8",
    )
    run_git(target, "add", "module.py")
    run_git(target, "commit", "-m", commit_message)
    candidate_head = run_git(target, "rev-parse", "HEAD").stdout.strip()

    store = create_store(tmp_path)
    session_id = SessionId(session_value)
    persist_record(
        store,
        session_id=session_id,
        phase=IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
        source_head=old_head,
        branch_name=branch_name,
        old_head=old_head,
        paths=("module.py",),
        commit_message=commit_message,
        new_head=None,
    )

    assessment = inspect_persisted_isolated_commit_lifecycle_recovery(
        source,
        store,
        session_id,
    )
    assert assessment is not None
    assert (
        assessment.classification
        is IsolatedCommitLifecycleRecoveryClassification.COMMIT_CANDIDATE_OBSERVED
    )
    return source, target, store, session_id, old_head, candidate_head


def test_adopt_candidate_action_completes_and_verifies_persisted_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Adopt one current candidate and persist VERIFIED with exact candidate head."""

    source, _target, store, session_id, _old_head, candidate_head = (
        create_candidate_observed_state(
            tmp_path,
            session_value="action-success",
            commit_message="fix: action candidate",
        )
    )
    persisted_before = store.read(session_id)
    assert persisted_before is not None

    recovery_write_phases: list[IsolatedCommitLifecyclePhase] = []
    original_write = IsolatedCommitLifecycleStore.write

    def counting_write(self, record: IsolatedCommitLifecycleRecord) -> None:
        if self is store:
            recovery_write_phases.append(record.phase)
        return original_write(self, record)

    monkeypatch.setattr(
        IsolatedCommitLifecycleStore,
        "write",
        counting_write,
    )

    result = adopt_isolated_commit_recovery_candidate(
        source,
        store,
        session_id,
        lambda _request: ToolApprovalDecision.APPROVE,
    )

    persisted_after = store.read(session_id)
    assert persisted_after is not None
    assert result.status is IsolatedCommitLifecycleRecoveryActionStatus.COMPLETED
    assert persisted_after.phase is IsolatedCommitLifecyclePhase.VERIFIED
    assert persisted_after.new_head == candidate_head
    assert result.approved_candidate_head == candidate_head
    assert persisted_after.session_id == persisted_before.session_id
    assert persisted_after.target_display == persisted_before.target_display
    assert persisted_after.source_head == persisted_before.source_head
    assert persisted_after.source_branch == persisted_before.source_branch
    assert persisted_after.branch_name == persisted_before.branch_name
    assert persisted_after.old_head == persisted_before.old_head
    assert persisted_after.paths == persisted_before.paths
    assert persisted_after.diff_fingerprint == persisted_before.diff_fingerprint
    assert (
        persisted_after.commit_message_fingerprint
        == persisted_before.commit_message_fingerprint
    )
    assert recovery_write_phases == [IsolatedCommitLifecyclePhase.VERIFIED]


def test_adopt_candidate_action_fails_closed_when_record_changes_after_approval(
    tmp_path: Path,
) -> None:
    """Abort adoption when persisted lifecycle record changes after approval."""

    source, _target, store, session_id, old_head, _candidate_head = (
        create_candidate_observed_state(
            tmp_path,
            session_value="action-stale-record",
            commit_message="fix: stale record",
        )
    )
    persisted_before = store.read(session_id)
    assert persisted_before is not None

    def approve_and_mutate(_request) -> ToolApprovalDecision:
        changed = IsolatedCommitLifecycleRecord(
            session_id=session_id,
            phase=IsolatedCommitLifecyclePhase.PLANNED,
            target_display=persisted_before.target_display,
            source_head=persisted_before.source_head,
            source_branch=persisted_before.source_branch,
            branch_name=persisted_before.branch_name,
            old_head=old_head,
            paths=persisted_before.paths,
            diff_fingerprint=persisted_before.diff_fingerprint,
            commit_message_fingerprint=persisted_before.commit_message_fingerprint,
            new_head=None,
        )
        store.write(changed)
        return ToolApprovalDecision.APPROVE

    with pytest.raises(CompletionError, match="record changed after approval"):
        adopt_isolated_commit_recovery_candidate(
            source,
            store,
            session_id,
            approve_and_mutate,
        )

    persisted_after = store.read(session_id)
    assert persisted_after is not None
    assert persisted_after.phase is IsolatedCommitLifecyclePhase.PLANNED


def test_adopt_candidate_action_fails_closed_when_candidate_changes_after_approval(
    tmp_path: Path,
) -> None:
    """Abort adoption when expected branch head changes after approval."""

    source, target, store, session_id, _old_head, _candidate_head = (
        create_candidate_observed_state(
            tmp_path,
            session_value="action-stale-candidate",
            commit_message="fix: stale candidate",
        )
    )
    persisted_before = store.read(session_id)
    assert persisted_before is not None

    def approve_and_move_head(_request) -> ToolApprovalDecision:
        (target / "module.py").write_text(
            "def add(left: int, right: int) -> int:\n    return left + right + 5\n",
            encoding="utf-8",
        )
        run_git(target, "add", "module.py")
        run_git(target, "commit", "-m", "fix: moved")
        return ToolApprovalDecision.APPROVE

    with pytest.raises(
        CompletionError,
        match="requires classification commit_candidate_observed",
    ):
        adopt_isolated_commit_recovery_candidate(
            source,
            store,
            session_id,
            approve_and_move_head,
        )

    persisted_after = store.read(session_id)
    assert persisted_after == persisted_before


def test_adopt_candidate_action_detects_record_change_before_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Detect store-record drift after revalidation and before intended VERIFIED write."""

    source, target, store, session_id, _old_head, _candidate_head = (
        create_candidate_observed_state(
            tmp_path,
            session_value="action-before-write-change",
            commit_message="fix: before write change",
        )
    )
    persisted_before = store.read(session_id)
    assert persisted_before is not None
    branch_before = run_git(target, "rev-parse", "HEAD").stdout.strip()

    preview_call_count = {"count": 0}
    original_preview = lifecycle_recovery_actions._build_candidate_preview

    def mutate_on_second_preview(source_repository, persisted_record, candidate_head):
        preview_call_count["count"] += 1
        preview = original_preview(source_repository, persisted_record, candidate_head)
        if preview_call_count["count"] == 2:
            changed = IsolatedCommitLifecycleRecord(
                session_id=persisted_before.session_id,
                phase=IsolatedCommitLifecyclePhase.PLANNED,
                target_display=persisted_before.target_display,
                source_head=persisted_before.source_head,
                source_branch=persisted_before.source_branch,
                branch_name=persisted_before.branch_name,
                old_head=persisted_before.old_head,
                paths=persisted_before.paths,
                diff_fingerprint=persisted_before.diff_fingerprint,
                commit_message_fingerprint=persisted_before.commit_message_fingerprint,
                new_head=None,
            )
            store.write(changed)
        return preview

    monkeypatch.setattr(
        lifecycle_recovery_actions,
        "_build_candidate_preview",
        mutate_on_second_preview,
    )

    with pytest.raises(CompletionError, match="changed before write"):
        adopt_isolated_commit_recovery_candidate(
            source,
            store,
            session_id,
            lambda _request: ToolApprovalDecision.APPROVE,
        )

    persisted_after = store.read(session_id)
    assert persisted_after is not None
    assert persisted_after.phase is IsolatedCommitLifecyclePhase.PLANNED
    assert run_git(target, "rev-parse", "HEAD").stdout.strip() == branch_before


def test_adopt_candidate_action_rejects_content_and_mode_change_candidate(
    tmp_path: Path,
) -> None:
    """Fail before approval when candidate modifies content and executable mode."""

    source = create_repository(tmp_path / "source-mode-content")
    branch_name = "agent/recovery-action-mode-content"
    target = source / "isolated"
    run_git(source, "worktree", "add", "-b", branch_name, str(target), "HEAD")
    old_head = run_git(target, "rev-parse", "HEAD").stdout.strip()

    tracked = target / "module.py"
    tracked.write_text(
        "def add(left: int, right: int) -> int:\n    return left + right\n",
        encoding="utf-8",
    )
    tracked.chmod(0o755)
    run_git(target, "add", "module.py")
    commit_message = "candidate mode+content"
    run_git(target, "commit", "-m", commit_message)

    store = create_store(tmp_path)
    session_id = SessionId("action-mode-content")
    persist_record(
        store,
        session_id=session_id,
        phase=IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
        source_head=old_head,
        branch_name=branch_name,
        old_head=old_head,
        paths=("module.py",),
        commit_message=commit_message,
        new_head=None,
    )

    assessment = inspect_persisted_isolated_commit_lifecycle_recovery(
        source,
        store,
        session_id,
    )
    assert assessment is not None
    assert (
        assessment.classification
        is IsolatedCommitLifecycleRecoveryClassification.COMMIT_CANDIDATE_OBSERVED
    )

    called = {"approval": False}

    def should_not_prompt(_request) -> ToolApprovalDecision:
        called["approval"] = True
        return ToolApprovalDecision.APPROVE

    with pytest.raises(
        ConfigurationError,
        match="candidate preview could not be constructed safely",
    ):
        adopt_isolated_commit_recovery_candidate(
            source,
            store,
            session_id,
            should_not_prompt,
        )

    persisted_after = store.read(session_id)
    assert persisted_after is not None
    assert persisted_after.phase is IsolatedCommitLifecyclePhase.EXECUTION_STARTED
    assert called["approval"] is False


def test_adopt_candidate_action_rejects_mode_only_candidate(
    tmp_path: Path,
) -> None:
    """Fail before approval when candidate contains one mode-only tracked change."""

    source = create_repository(tmp_path / "source-mode-only")
    branch_name = "agent/recovery-action-mode-only"
    target = source / "isolated"
    run_git(source, "worktree", "add", "-b", branch_name, str(target), "HEAD")
    old_head = run_git(target, "rev-parse", "HEAD").stdout.strip()

    tracked = target / "module.py"
    tracked.chmod(0o755)
    run_git(target, "add", "module.py")
    commit_message = "candidate mode only"
    run_git(target, "commit", "-m", commit_message)

    store = create_store(tmp_path)
    session_id = SessionId("action-mode-only")
    persist_record(
        store,
        session_id=session_id,
        phase=IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
        source_head=old_head,
        branch_name=branch_name,
        old_head=old_head,
        paths=("module.py",),
        commit_message=commit_message,
        new_head=None,
    )

    assessment = inspect_persisted_isolated_commit_lifecycle_recovery(
        source,
        store,
        session_id,
    )
    assert assessment is not None
    assert (
        assessment.classification
        is IsolatedCommitLifecycleRecoveryClassification.COMMIT_CANDIDATE_OBSERVED
    )

    with pytest.raises(
        ConfigurationError,
        match="candidate preview could not be constructed safely",
    ):
        adopt_isolated_commit_recovery_candidate(
            source,
            store,
            session_id,
            lambda _request: ToolApprovalDecision.APPROVE,
        )

    persisted_after = store.read(session_id)
    assert persisted_after is not None
    assert persisted_after.phase is IsolatedCommitLifecyclePhase.EXECUTION_STARTED


def test_adopt_candidate_action_rejects_added_symlink_candidate(
    tmp_path: Path,
) -> None:
    """Fail before approval when candidate adds one symlink path."""

    source = create_repository(tmp_path / "source-added-symlink")
    branch_name = "agent/recovery-action-symlink"
    target = source / "isolated"
    run_git(source, "worktree", "add", "-b", branch_name, str(target), "HEAD")
    old_head = run_git(target, "rev-parse", "HEAD").stdout.strip()

    symlink_path = target / "link.py"
    symlink_path.symlink_to("module.py")
    run_git(target, "add", "link.py")
    commit_message = "candidate symlink"
    run_git(target, "commit", "-m", commit_message)

    store = create_store(tmp_path)
    session_id = SessionId("action-added-symlink")
    persist_record(
        store,
        session_id=session_id,
        phase=IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
        source_head=old_head,
        branch_name=branch_name,
        old_head=old_head,
        paths=("link.py",),
        commit_message=commit_message,
        new_head=None,
    )

    assessment = inspect_persisted_isolated_commit_lifecycle_recovery(
        source,
        store,
        session_id,
    )
    assert assessment is not None
    assert (
        assessment.classification
        is IsolatedCommitLifecycleRecoveryClassification.COMMIT_CANDIDATE_OBSERVED
    )

    with pytest.raises(
        ConfigurationError,
        match="candidate preview could not be constructed safely",
    ):
        adopt_isolated_commit_recovery_candidate(
            source,
            store,
            session_id,
            lambda _request: ToolApprovalDecision.APPROVE,
        )

    persisted_after = store.read(session_id)
    assert persisted_after is not None
    assert persisted_after.phase is IsolatedCommitLifecyclePhase.EXECUTION_STARTED
