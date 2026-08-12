"""Tests for read-only isolated commit lifecycle restart inspection."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import get_type_hints

import pytest

import agent_workbench.lifecycle_restart as lifecycle_restart
from agent_workbench.errors import ConfigurationError
from agent_workbench.lifecycle import (
    IsolatedCommitLifecyclePhase,
    IsolatedCommitLifecycleRecord,
)
from agent_workbench.lifecycle_restart import (
    IsolatedCommitHeadRelation,
    IsolatedCommitRestartEvidence,
    inspect_isolated_commit_restart,
    inspect_persisted_isolated_commit_restart,
)
from agent_workbench.lifecycle_store import IsolatedCommitLifecycleStore
from agent_workbench.recovery import RecoveryStatus
from agent_workbench.session import SessionId
from agent_workbench.worktrees import WorktreeRestartObservation

SHA1_OLD = "a" * 40
SHA1_NEW = "b" * 40
SHA1_SOURCE = "c" * 40
DIFF_FP = "0" * 64
CMF = "1" * 64


def make_record(
    *,
    session_id: SessionId = SessionId("restart-session"),
    phase: IsolatedCommitLifecyclePhase = IsolatedCommitLifecyclePhase.PLANNED,
    new_head: str | None = None,
) -> IsolatedCommitLifecycleRecord:
    """Return one valid lifecycle record for restart inspection tests."""

    effective_new_head = (
        SHA1_NEW if phase is IsolatedCommitLifecyclePhase.VERIFIED else new_head
    )
    return IsolatedCommitLifecycleRecord(
        session_id=session_id,
        phase=phase,
        target_display="../isolated",
        source_head=SHA1_SOURCE,
        source_branch="main",
        branch_name="agent/task",
        old_head=SHA1_OLD,
        paths=("module.py", "test_module.py"),
        diff_fingerprint=DIFF_FP,
        commit_message_fingerprint=CMF,
        new_head=effective_new_head,
    )


def make_observation(**overrides: object) -> WorktreeRestartObservation:
    """Return one valid restart observation with optional overrides."""

    values: dict[str, object] = {
        "observed_source_head": SHA1_SOURCE,
        "observed_source_branch": "main",
        "branch_present": RecoveryStatus.YES,
        "observed_branch_head": SHA1_OLD,
        "registered": RecoveryStatus.YES,
        "observed_registered_branch": "agent/task",
        "observed_registered_head": SHA1_OLD,
        "registration_locked": RecoveryStatus.NO,
        "registration_prunable": RecoveryStatus.NO,
        "target_present": RecoveryStatus.YES,
        "target_is_directory": RecoveryStatus.YES,
        "worktree_identity_valid": RecoveryStatus.YES,
        "observed_worktree_branch": "agent/task",
        "observed_worktree_head": SHA1_OLD,
        "index_dirty": RecoveryStatus.NO,
        "staged_paths": (),
        "staged_paths_complete": RecoveryStatus.YES,
        "worktree_dirty": RecoveryStatus.NO,
    }
    values.update(overrides)
    return WorktreeRestartObservation(**values)  # type: ignore[arg-type]


def create_store(tmp_path: Path) -> IsolatedCommitLifecycleStore:
    """Create one dedicated lifecycle store for restart inspection tests."""

    directory = tmp_path / "lifecycle-store"
    directory.mkdir()
    return IsolatedCommitLifecycleStore(directory)


def test_evidence_is_frozen_slotted_value_comparable_and_hashable() -> None:
    """Provide immutable value semantics for composed restart evidence."""

    record = make_record()
    observation = make_observation()
    first = IsolatedCommitRestartEvidence(
        lifecycle_record=record,
        git_observation=observation,
    )
    second = IsolatedCommitRestartEvidence(
        lifecycle_record=record,
        git_observation=observation,
    )

    assert first == second
    assert hash(first) == hash(second)
    assert len({first, second}) == 1
    assert not hasattr(first, "__dict__")
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.git_observation = observation  # type: ignore[misc]


def test_evidence_preserves_record_and_observation_without_mutation() -> None:
    """Retain the exact immutable lifecycle record and observation objects."""

    record = make_record()
    observation = make_observation()
    evidence = IsolatedCommitRestartEvidence(
        lifecycle_record=record,
        git_observation=observation,
    )

    assert evidence.lifecycle_record is record
    assert evidence.git_observation is observation
    assert evidence.persisted_phase is record.phase
    assert evidence.target_display == record.target_display
    assert evidence.expected_source_head == record.source_head
    assert evidence.expected_source_branch == record.source_branch
    assert evidence.expected_branch == record.branch_name
    assert evidence.old_head == record.old_head
    assert evidence.persisted_new_head == record.new_head
    assert evidence.expected_paths == record.paths


def test_evidence_repr_avoids_host_paths_and_raw_session_identifier() -> None:
    """Keep repr focused on safe observational state."""

    session_id = SessionId("raw-session-id")
    evidence = IsolatedCommitRestartEvidence(
        lifecycle_record=make_record(session_id=session_id),
        git_observation=make_observation(),
    )

    representation = repr(evidence)
    assert "/tmp/" not in representation
    assert session_id.value not in representation


@pytest.mark.parametrize("phase", list(IsolatedCommitLifecyclePhase))
def test_all_lifecycle_phases_are_accepted(
    phase: IsolatedCommitLifecyclePhase,
) -> None:
    """Treat every persisted lifecycle phase as factual historical evidence."""

    new_head = SHA1_NEW if phase is IsolatedCommitLifecyclePhase.VERIFIED else None
    evidence = IsolatedCommitRestartEvidence(
        lifecycle_record=make_record(phase=phase, new_head=new_head),
        git_observation=make_observation(),
    )

    assert evidence.persisted_phase is phase


def test_direct_inspection_rejects_invalid_record_before_git_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validate record input before invoking any Git observation."""

    source = tmp_path / "source"
    called = False

    def unexpected(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("Git inspection must not run")

    monkeypatch.setattr(lifecycle_restart, "inspect_worktree_restart_state", unexpected)

    with pytest.raises(ConfigurationError, match="LifecycleRecord"):
        inspect_isolated_commit_restart(source, object())  # type: ignore[arg-type]

    assert called is False


def test_direct_inspection_rejects_invalid_source_before_git_inspection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validate source input before invoking any Git observation."""

    record = make_record()
    called = False

    def unexpected(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("Git inspection must not run")

    monkeypatch.setattr(lifecycle_restart, "inspect_worktree_restart_state", unexpected)

    with pytest.raises(ConfigurationError, match="source Path"):
        inspect_isolated_commit_restart("not-a-path", record)  # type: ignore[arg-type]

    assert called is False


def test_direct_inspection_composes_record_with_actual_git_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compose the lifecycle record with the observation returned by the worktree inspector."""

    source = tmp_path / "source"
    record = make_record()
    observation = make_observation(observed_worktree_head=SHA1_NEW)
    calls: list[tuple[Path, str, str]] = []

    def fake_inspect(path: Path, target_display: str, branch_name: str):
        calls.append((path, target_display, branch_name))
        return observation

    monkeypatch.setattr(
        lifecycle_restart, "inspect_worktree_restart_state", fake_inspect
    )

    evidence = inspect_isolated_commit_restart(source, record)

    assert calls == [(source, record.target_display, record.branch_name)]
    assert evidence.lifecycle_record is record
    assert evidence.git_observation is observation


def test_source_head_changed_derives_yes_no_and_unknown() -> None:
    """Derive source HEAD relation purely from current observation."""

    record = make_record()

    assert (
        IsolatedCommitRestartEvidence(
            lifecycle_record=record,
            git_observation=make_observation(observed_source_head=record.source_head),
        ).source_head_changed
        is RecoveryStatus.NO
    )
    assert (
        IsolatedCommitRestartEvidence(
            lifecycle_record=record,
            git_observation=make_observation(observed_source_head="d" * 40),
        ).source_head_changed
        is RecoveryStatus.YES
    )
    assert (
        IsolatedCommitRestartEvidence(
            lifecycle_record=record,
            git_observation=make_observation(observed_source_head=None),
        ).source_head_changed
        is RecoveryStatus.UNKNOWN
    )


def test_head_relations_derive_unknown_old_new_and_other() -> None:
    """Classify observed heads factually against persisted old and new heads only."""

    verified = make_record(
        phase=IsolatedCommitLifecyclePhase.VERIFIED,
        new_head=SHA1_NEW,
    )

    unknown = IsolatedCommitRestartEvidence(
        lifecycle_record=verified,
        git_observation=make_observation(
            observed_branch_head=None,
            observed_registered_head=None,
            observed_worktree_head=None,
        ),
    )
    assert unknown.branch_head_relation is IsolatedCommitHeadRelation.UNKNOWN
    assert unknown.registered_head_relation is IsolatedCommitHeadRelation.UNKNOWN
    assert unknown.worktree_head_relation is IsolatedCommitHeadRelation.UNKNOWN

    old = IsolatedCommitRestartEvidence(
        lifecycle_record=verified,
        git_observation=make_observation(
            observed_branch_head=SHA1_OLD,
            observed_registered_head=SHA1_OLD,
            observed_worktree_head=SHA1_OLD,
        ),
    )
    assert old.branch_head_relation is IsolatedCommitHeadRelation.OLD_HEAD
    assert old.registered_head_relation is IsolatedCommitHeadRelation.OLD_HEAD
    assert old.worktree_head_relation is IsolatedCommitHeadRelation.OLD_HEAD

    new = IsolatedCommitRestartEvidence(
        lifecycle_record=verified,
        git_observation=make_observation(
            observed_branch_head=SHA1_NEW,
            observed_registered_head=SHA1_NEW,
            observed_worktree_head=SHA1_NEW,
        ),
    )
    assert new.branch_head_relation is IsolatedCommitHeadRelation.PERSISTED_NEW_HEAD
    assert new.registered_head_relation is IsolatedCommitHeadRelation.PERSISTED_NEW_HEAD
    assert new.worktree_head_relation is IsolatedCommitHeadRelation.PERSISTED_NEW_HEAD

    other = IsolatedCommitRestartEvidence(
        lifecycle_record=verified,
        git_observation=make_observation(
            observed_branch_head="d" * 40,
            observed_registered_head="d" * 40,
            observed_worktree_head="d" * 40,
        ),
    )
    assert other.branch_head_relation is IsolatedCommitHeadRelation.OTHER
    assert other.registered_head_relation is IsolatedCommitHeadRelation.OTHER
    assert other.worktree_head_relation is IsolatedCommitHeadRelation.OTHER


def test_non_verified_persisted_phases_never_report_persisted_new_head_relation() -> (
    None
):
    """Treat unrelated current heads as OTHER when no persisted new head exists."""

    for phase in (
        IsolatedCommitLifecyclePhase.PLANNED,
        IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
    ):
        evidence = IsolatedCommitRestartEvidence(
            lifecycle_record=make_record(phase=phase, new_head=None),
            git_observation=make_observation(observed_branch_head="d" * 40),
        )
        assert evidence.branch_head_relation is IsolatedCommitHeadRelation.OTHER


def test_branch_match_properties_are_factual_yes_no_unknown() -> None:
    """Derive branch matching facts without inferring actions."""

    record = make_record()
    yes = IsolatedCommitRestartEvidence(
        lifecycle_record=record,
        git_observation=make_observation(
            observed_registered_branch=record.branch_name,
            observed_worktree_branch=record.branch_name,
        ),
    )
    assert yes.registered_branch_matches_expected is RecoveryStatus.YES
    assert yes.worktree_branch_matches_expected is RecoveryStatus.YES

    no = IsolatedCommitRestartEvidence(
        lifecycle_record=record,
        git_observation=make_observation(
            observed_registered_branch="agent/other",
            observed_worktree_branch="agent/other",
        ),
    )
    assert no.registered_branch_matches_expected is RecoveryStatus.NO
    assert no.worktree_branch_matches_expected is RecoveryStatus.NO

    unknown = IsolatedCommitRestartEvidence(
        lifecycle_record=record,
        git_observation=make_observation(
            observed_registered_branch=None,
            observed_worktree_branch=None,
        ),
    )
    assert unknown.registered_branch_matches_expected is RecoveryStatus.UNKNOWN
    assert unknown.worktree_branch_matches_expected is RecoveryStatus.UNKNOWN


def test_staged_paths_match_expected_is_yes_no_and_unknown() -> None:
    """Compare only complete staged-path sets against persisted expected paths."""

    record = make_record()
    yes = IsolatedCommitRestartEvidence(
        lifecycle_record=record,
        git_observation=make_observation(
            index_dirty=RecoveryStatus.YES,
            staged_paths=("test_module.py", "module.py"),
            staged_paths_complete=RecoveryStatus.YES,
            worktree_dirty=RecoveryStatus.YES,
        ),
    )
    assert yes.staged_paths_match_expected is RecoveryStatus.YES

    no = IsolatedCommitRestartEvidence(
        lifecycle_record=record,
        git_observation=make_observation(
            index_dirty=RecoveryStatus.YES,
            staged_paths=("module.py",),
            staged_paths_complete=RecoveryStatus.YES,
            worktree_dirty=RecoveryStatus.YES,
        ),
    )
    assert no.staged_paths_match_expected is RecoveryStatus.NO

    unknown = IsolatedCommitRestartEvidence(
        lifecycle_record=record,
        git_observation=make_observation(
            index_dirty=RecoveryStatus.YES,
            staged_paths=(),
            staged_paths_complete=RecoveryStatus.UNKNOWN,
            worktree_dirty=RecoveryStatus.YES,
        ),
    )
    assert unknown.staged_paths_match_expected is RecoveryStatus.UNKNOWN


def test_known_session_api_returns_none_without_git_inspection_when_record_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return None and perform no Git inspection when no record exists."""

    source = tmp_path / "source"
    store = create_store(tmp_path)
    session_id = SessionId("missing")
    called = False

    def unexpected(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("Git inspection must not run")

    monkeypatch.setattr(
        lifecycle_restart, "inspect_isolated_commit_restart", unexpected
    )

    assert inspect_persisted_isolated_commit_restart(source, store, session_id) is None
    assert called is False


def test_known_session_api_reads_exact_requested_session_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read only the requested SessionId from the lifecycle store."""

    source = tmp_path / "source"
    store = create_store(tmp_path)
    session_id = SessionId("requested")
    requested: list[SessionId] = []
    record = make_record(session_id=session_id)

    monkeypatch.setattr(
        IsolatedCommitLifecycleStore,
        "read",
        lambda self, value: requested.append(value) or record,
    )
    monkeypatch.setattr(
        lifecycle_restart,
        "inspect_isolated_commit_restart",
        lambda path, loaded_record: IsolatedCommitRestartEvidence(
            lifecycle_record=loaded_record,
            git_observation=make_observation(),
        ),
    )

    evidence = inspect_persisted_isolated_commit_restart(source, store, session_id)

    assert requested == [session_id]
    assert evidence is not None
    assert evidence.lifecycle_record is record


def test_store_read_failure_prevents_git_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Propagate store failures without attempting Git observation."""

    source = tmp_path / "source"
    store = create_store(tmp_path)
    session_id = SessionId("failed-read")
    called = False

    def unexpected(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("Git inspection must not run")

    monkeypatch.setattr(
        IsolatedCommitLifecycleStore,
        "read",
        lambda self, _session_id: (_ for _ in ()).throw(
            ConfigurationError("invalid persisted data")
        ),
    )
    monkeypatch.setattr(
        lifecycle_restart, "inspect_isolated_commit_restart", unexpected
    )

    with pytest.raises(ConfigurationError, match="invalid persisted data"):
        inspect_persisted_isolated_commit_restart(source, store, session_id)

    assert called is False


def test_valid_persisted_record_is_passed_to_actual_restart_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compose store read and direct inspection for one known persisted session."""

    source = tmp_path / "source"
    store = create_store(tmp_path)
    session_id = SessionId("stored")
    record = make_record(session_id=session_id)
    calls: list[tuple[Path, IsolatedCommitLifecycleRecord]] = []

    store.write(record)

    def fake_inspect(path: Path, loaded_record: IsolatedCommitLifecycleRecord):
        calls.append((path, loaded_record))
        return IsolatedCommitRestartEvidence(
            lifecycle_record=loaded_record,
            git_observation=make_observation(),
        )

    monkeypatch.setattr(
        lifecycle_restart, "inspect_isolated_commit_restart", fake_inspect
    )

    evidence = inspect_persisted_isolated_commit_restart(source, store, session_id)

    assert calls == [(source, record)]
    assert evidence is not None
    assert evidence.lifecycle_record == record


def test_persisted_phase_does_not_imply_actionable_restart_state() -> None:
    """Treat persisted phases as historical evidence only."""

    planned = IsolatedCommitRestartEvidence(
        lifecycle_record=make_record(phase=IsolatedCommitLifecyclePhase.PLANNED),
        git_observation=make_observation(observed_branch_head="d" * 40),
    )
    started = IsolatedCommitRestartEvidence(
        lifecycle_record=make_record(
            phase=IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
        ),
        git_observation=make_observation(observed_branch_head=SHA1_OLD),
    )
    verified = IsolatedCommitRestartEvidence(
        lifecycle_record=make_record(
            phase=IsolatedCommitLifecyclePhase.VERIFIED,
            new_head=SHA1_NEW,
        ),
        git_observation=make_observation(observed_branch_head="d" * 40),
    )

    assert planned.branch_head_relation is IsolatedCommitHeadRelation.OTHER
    assert started.branch_head_relation is IsolatedCommitHeadRelation.OLD_HEAD
    assert verified.branch_head_relation is IsolatedCommitHeadRelation.OTHER


def test_lifecycle_restart_module_exposes_no_recovery_action_api() -> None:
    """Keep the module limited to evidence-only inspection APIs."""

    for forbidden_name in (
        "recover",
        "resume",
        "retry",
        "rollback",
        "reset",
        "restore",
        "clean",
        "stash",
        "commit",
        "stage",
    ):
        assert not hasattr(lifecycle_restart, forbidden_name)


def test_public_restart_api_annotations_are_explicit() -> None:
    """Keep the two public restart inspection APIs explicitly annotated."""

    assert get_type_hints(lifecycle_restart.inspect_isolated_commit_restart) == {
        "source_repository": Path,
        "record": IsolatedCommitLifecycleRecord,
        "return": IsolatedCommitRestartEvidence,
    }
    assert get_type_hints(
        lifecycle_restart.inspect_persisted_isolated_commit_restart
    ) == {
        "source_repository": Path,
        "lifecycle_store": IsolatedCommitLifecycleStore,
        "session_id": SessionId,
        "return": IsolatedCommitRestartEvidence | None,
    }
