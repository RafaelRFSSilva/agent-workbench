"""Tests for conservative isolated commit lifecycle restart classification."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

import agent_workbench.lifecycle_recovery as lifecycle_recovery
from agent_workbench.errors import ConfigurationError
from agent_workbench.lifecycle import (
    IsolatedCommitLifecyclePhase,
    IsolatedCommitLifecycleRecord,
)
from agent_workbench.lifecycle_recovery import (
    IsolatedCommitLifecycleRecoveryAssessment,
    IsolatedCommitLifecycleRecoveryClassification,
    classify_isolated_commit_lifecycle_recovery,
    inspect_isolated_commit_lifecycle_recovery,
    inspect_persisted_isolated_commit_lifecycle_recovery,
)
from agent_workbench.lifecycle_restart import (
    IsolatedCommitRestartEvidence,
)
from agent_workbench.lifecycle_store import IsolatedCommitLifecycleStore
from agent_workbench.recovery import RecoveryStatus
from agent_workbench.session import SessionId
from agent_workbench.worktree_commits import IsolatedCommitRecoveryCandidateEvidence
from agent_workbench.worktrees import WorktreeRestartObservation

SHA1_OLD = "a" * 40
SHA1_NEW = "b" * 40
SHA1_OTHER = "d" * 40
SHA1_SOURCE = "c" * 40
DIFF_FP = "0" * 64
CMF = "1" * 64


def make_record(
    *,
    phase: IsolatedCommitLifecyclePhase = IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
    new_head: str | None = None,
    session_id: SessionId = SessionId("lifecycle-recovery-session"),
) -> IsolatedCommitLifecycleRecord:
    """Return one valid lifecycle record for conservative classification tests."""

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


def make_restart_evidence(
    *,
    phase: IsolatedCommitLifecyclePhase = IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
    observation: WorktreeRestartObservation | None = None,
    new_head: str | None = None,
    session_id: SessionId = SessionId("lifecycle-recovery-session"),
) -> IsolatedCommitRestartEvidence:
    """Build one restart evidence object for classification tests."""

    record = make_record(phase=phase, new_head=new_head, session_id=session_id)
    observed = observation if observation is not None else make_observation()
    return IsolatedCommitRestartEvidence(
        lifecycle_record=record,
        git_observation=observed,
    )


def make_candidate(
    *,
    candidate_head: str = SHA1_OTHER,
    parent: RecoveryStatus = RecoveryStatus.YES,
    message: RecoveryStatus = RecoveryStatus.YES,
    paths: RecoveryStatus = RecoveryStatus.YES,
) -> IsolatedCommitRecoveryCandidateEvidence:
    """Return one candidate metadata evidence object."""

    return IsolatedCommitRecoveryCandidateEvidence(
        candidate_head=candidate_head,
        parent_matches_old_head=parent,
        message_fingerprint_matches=message,
        paths_match_expected=paths,
    )


def create_store(tmp_path: Path) -> IsolatedCommitLifecycleStore:
    """Create one dedicated lifecycle store for persisted API tests."""

    directory = tmp_path / "lifecycle-store"
    directory.mkdir()
    return IsolatedCommitLifecycleStore(directory)


def test_enum_contains_exact_required_values() -> None:
    """Expose exactly the six conservative lifecycle recovery classifications."""

    assert [
        member.value for member in IsolatedCommitLifecycleRecoveryClassification
    ] == [
        "insufficient_evidence",
        "old_head_clean_index",
        "expected_path_staging_observed",
        "commit_candidate_observed",
        "persisted_verified_commit_observed",
        "diverged_or_inconsistent",
    ]


def test_assessment_is_frozen_slotted_value_comparable_and_hashable() -> None:
    """Provide immutable value semantics for recovery assessments."""

    restart = make_restart_evidence(
        observation=make_observation(
            observed_branch_head=SHA1_OTHER,
            observed_registered_head=SHA1_OTHER,
            observed_worktree_head=SHA1_OTHER,
        )
    )
    candidate = make_candidate(candidate_head=SHA1_OTHER)
    first = classify_isolated_commit_lifecycle_recovery(restart, candidate)
    second = classify_isolated_commit_lifecycle_recovery(restart, candidate)

    assert first == second
    assert hash(first) == hash(second)
    assert len({first, second}) == 1
    assert not hasattr(first, "__dict__")
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.classification = (
            IsolatedCommitLifecycleRecoveryClassification.INSUFFICIENT_EVIDENCE
        )  # type: ignore[misc]


def test_direct_assessment_construction_rejects_inconsistent_classification() -> None:
    """Reject direct construction when classification contradicts supplied evidence."""

    restart = make_restart_evidence(
        phase=IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
        observation=make_observation(
            observed_branch_head=SHA1_OLD,
            observed_registered_head=SHA1_OLD,
            observed_worktree_head=SHA1_OLD,
            index_dirty=RecoveryStatus.NO,
            staged_paths=(),
            staged_paths_complete=RecoveryStatus.YES,
        ),
    )

    with pytest.raises(ConfigurationError, match="inconsistent"):
        IsolatedCommitLifecycleRecoveryAssessment(
            restart_evidence=restart,
            classification=IsolatedCommitLifecycleRecoveryClassification.COMMIT_CANDIDATE_OBSERVED,
            candidate_evidence=None,
        )


def test_direct_assessment_construction_enforces_candidate_binding() -> None:
    """Reject direct construction when candidate evidence does not bind to OTHER head."""

    restart = make_restart_evidence(
        observation=make_observation(
            observed_branch_head=SHA1_OTHER,
            observed_registered_head=SHA1_OTHER,
            observed_worktree_head=SHA1_OTHER,
        )
    )

    with pytest.raises(ConfigurationError, match="exact observed"):
        IsolatedCommitLifecycleRecoveryAssessment(
            restart_evidence=restart,
            classification=IsolatedCommitLifecycleRecoveryClassification.INSUFFICIENT_EVIDENCE,
            candidate_evidence=make_candidate(candidate_head=SHA1_NEW),
        )


def test_direct_assessment_construction_accepts_matching_classification() -> None:
    """Allow direct immutable construction when evidence and classification agree."""

    restart = make_restart_evidence(
        phase=IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
        observation=make_observation(
            observed_branch_head=SHA1_OLD,
            observed_registered_head=SHA1_OLD,
            observed_worktree_head=SHA1_OLD,
            index_dirty=RecoveryStatus.NO,
            staged_paths=(),
            staged_paths_complete=RecoveryStatus.YES,
        ),
    )

    assessment = IsolatedCommitLifecycleRecoveryAssessment(
        restart_evidence=restart,
        classification=IsolatedCommitLifecycleRecoveryClassification.OLD_HEAD_CLEAN_INDEX,
        candidate_evidence=None,
    )

    assert (
        assessment.classification
        is IsolatedCommitLifecycleRecoveryClassification.OLD_HEAD_CLEAN_INDEX
    )


def test_assessment_repr_hides_raw_session_id_and_host_paths() -> None:
    """Keep composed assessment repr free from sensitive path/session details."""

    session_id = SessionId("raw-session-id")
    restart = make_restart_evidence(session_id=session_id)
    assessment = classify_isolated_commit_lifecycle_recovery(restart)

    text = repr(assessment)
    assert session_id.value not in text
    assert "/tmp/" not in text


def test_invalid_assessment_and_classifier_arguments_are_rejected() -> None:
    """Validate constructor/classifier argument types before classification."""

    restart = make_restart_evidence()

    with pytest.raises(
        ConfigurationError, match="requires IsolatedCommitRestartEvidence"
    ):
        classify_isolated_commit_lifecycle_recovery(object())  # type: ignore[arg-type]

    with pytest.raises(ConfigurationError, match="candidate evidence"):
        classify_isolated_commit_lifecycle_recovery(
            restart,
            object(),  # type: ignore[arg-type]
        )

    with pytest.raises(
        ConfigurationError, match="requires IsolatedCommitRestartEvidence"
    ):
        IsolatedCommitLifecycleRecoveryAssessment(
            restart_evidence=object(),  # type: ignore[arg-type]
            classification=IsolatedCommitLifecycleRecoveryClassification.INSUFFICIENT_EVIDENCE,
            candidate_evidence=None,
        )


def test_candidate_evidence_must_bind_to_exact_observed_other_head() -> None:
    """Require candidate evidence to bind to the exact observed OTHER branch head."""

    restart = make_restart_evidence(
        observation=make_observation(
            observed_branch_head=SHA1_OTHER,
            observed_registered_head=SHA1_OTHER,
            observed_worktree_head=SHA1_OTHER,
        )
    )
    candidate = make_candidate(candidate_head=SHA1_OTHER)

    assessment = classify_isolated_commit_lifecycle_recovery(restart, candidate)

    assert assessment.candidate_evidence == candidate


def test_mismatched_candidate_evidence_raises_configuration_error() -> None:
    """Reject mismatched candidate evidence rather than silently classifying."""

    restart_other = make_restart_evidence(
        observation=make_observation(
            observed_branch_head=SHA1_OTHER,
            observed_registered_head=SHA1_OTHER,
            observed_worktree_head=SHA1_OTHER,
        )
    )
    with pytest.raises(ConfigurationError, match="exact observed"):
        classify_isolated_commit_lifecycle_recovery(
            restart_other,
            make_candidate(candidate_head=SHA1_NEW),
        )

    restart_old = make_restart_evidence()
    with pytest.raises(ConfigurationError, match="relation OTHER"):
        classify_isolated_commit_lifecycle_recovery(restart_old, make_candidate())


def test_conflicting_known_head_observations_classify_diverged() -> None:
    """Detect contradictory branch/registered/worktree known head facts."""

    restart = make_restart_evidence(
        observation=make_observation(
            observed_branch_head=SHA1_OLD,
            observed_registered_head=SHA1_OTHER,
            observed_worktree_head=SHA1_OLD,
        )
    )

    assessment = classify_isolated_commit_lifecycle_recovery(restart)

    assert (
        assessment.classification
        is IsolatedCommitLifecycleRecoveryClassification.DIVERGED_OR_INCONSISTENT
    )


def test_branch_absent_or_unknown_classification_rules() -> None:
    """Classify absent branch as diverged and unknown branch presence as insufficient."""

    absent = make_restart_evidence(
        observation=make_observation(
            branch_present=RecoveryStatus.NO,
            observed_branch_head=None,
            observed_registered_branch=None,
            observed_registered_head=None,
            registered=RecoveryStatus.NO,
            registration_locked=RecoveryStatus.NO,
            registration_prunable=RecoveryStatus.NO,
            target_present=RecoveryStatus.UNKNOWN,
            target_is_directory=RecoveryStatus.UNKNOWN,
            worktree_identity_valid=RecoveryStatus.UNKNOWN,
            observed_worktree_branch=None,
            observed_worktree_head=None,
            index_dirty=RecoveryStatus.UNKNOWN,
            staged_paths=(),
            staged_paths_complete=RecoveryStatus.UNKNOWN,
            worktree_dirty=RecoveryStatus.UNKNOWN,
        )
    )
    unknown = make_restart_evidence(
        observation=make_observation(
            branch_present=RecoveryStatus.UNKNOWN,
            observed_branch_head=None,
            observed_registered_branch=None,
            observed_registered_head=None,
            registered=RecoveryStatus.UNKNOWN,
            registration_locked=RecoveryStatus.UNKNOWN,
            registration_prunable=RecoveryStatus.UNKNOWN,
            target_present=RecoveryStatus.UNKNOWN,
            target_is_directory=RecoveryStatus.UNKNOWN,
            worktree_identity_valid=RecoveryStatus.UNKNOWN,
            observed_worktree_branch=None,
            observed_worktree_head=None,
            index_dirty=RecoveryStatus.UNKNOWN,
            staged_paths=(),
            staged_paths_complete=RecoveryStatus.UNKNOWN,
            worktree_dirty=RecoveryStatus.UNKNOWN,
        )
    )

    assert (
        classify_isolated_commit_lifecycle_recovery(absent).classification
        is IsolatedCommitLifecycleRecoveryClassification.DIVERGED_OR_INCONSISTENT
    )
    assert (
        classify_isolated_commit_lifecycle_recovery(unknown).classification
        is IsolatedCommitLifecycleRecoveryClassification.INSUFFICIENT_EVIDENCE
    )


def test_registered_or_worktree_branch_mismatch_classifies_diverged() -> None:
    """Treat safely observed branch-name mismatches as diverged/inconsistent."""

    registered_mismatch = make_restart_evidence(
        observation=make_observation(observed_registered_branch="agent/other")
    )
    worktree_mismatch = make_restart_evidence(
        observation=make_observation(observed_worktree_branch="agent/other")
    )

    assert (
        classify_isolated_commit_lifecycle_recovery(registered_mismatch).classification
        is IsolatedCommitLifecycleRecoveryClassification.DIVERGED_OR_INCONSISTENT
    )
    assert (
        classify_isolated_commit_lifecycle_recovery(worktree_mismatch).classification
        is IsolatedCommitLifecycleRecoveryClassification.DIVERGED_OR_INCONSISTENT
    )


def test_planned_old_head_clean_index_classification() -> None:
    """PLANNED + old head + clean complete index classifies old-head-clean."""

    restart = make_restart_evidence(
        phase=IsolatedCommitLifecyclePhase.PLANNED,
        observation=make_observation(
            observed_branch_head=SHA1_OLD,
            observed_registered_head=SHA1_OLD,
            observed_worktree_head=SHA1_OLD,
            index_dirty=RecoveryStatus.NO,
            staged_paths=(),
            staged_paths_complete=RecoveryStatus.YES,
        ),
    )

    assert (
        classify_isolated_commit_lifecycle_recovery(restart).classification
        is IsolatedCommitLifecycleRecoveryClassification.OLD_HEAD_CLEAN_INDEX
    )


def test_planned_staging_or_other_head_or_unknown_index_rules() -> None:
    """Apply PLANNED divergence/insufficiency constraints exactly."""

    staged = make_restart_evidence(
        phase=IsolatedCommitLifecyclePhase.PLANNED,
        observation=make_observation(
            index_dirty=RecoveryStatus.YES,
            staged_paths=("module.py", "test_module.py"),
            staged_paths_complete=RecoveryStatus.YES,
            worktree_dirty=RecoveryStatus.YES,
        ),
    )
    other = make_restart_evidence(
        phase=IsolatedCommitLifecyclePhase.PLANNED,
        observation=make_observation(
            observed_branch_head=SHA1_OTHER,
            observed_registered_head=SHA1_OTHER,
            observed_worktree_head=SHA1_OTHER,
        ),
    )
    unknown = make_restart_evidence(
        phase=IsolatedCommitLifecyclePhase.PLANNED,
        observation=make_observation(
            index_dirty=RecoveryStatus.UNKNOWN,
            staged_paths=(),
            staged_paths_complete=RecoveryStatus.UNKNOWN,
            worktree_dirty=RecoveryStatus.UNKNOWN,
        ),
    )

    assert (
        classify_isolated_commit_lifecycle_recovery(staged).classification
        is IsolatedCommitLifecycleRecoveryClassification.DIVERGED_OR_INCONSISTENT
    )
    assert (
        classify_isolated_commit_lifecycle_recovery(other).classification
        is IsolatedCommitLifecycleRecoveryClassification.DIVERGED_OR_INCONSISTENT
    )
    assert (
        classify_isolated_commit_lifecycle_recovery(unknown).classification
        is IsolatedCommitLifecycleRecoveryClassification.INSUFFICIENT_EVIDENCE
    )


def test_execution_started_old_head_clean_and_expected_staging_rules() -> None:
    """Classify clean and exact staged-path states at old head for execution-started."""

    clean = make_restart_evidence(
        phase=IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
        observation=make_observation(
            index_dirty=RecoveryStatus.NO,
            staged_paths=(),
            staged_paths_complete=RecoveryStatus.YES,
        ),
    )
    exact_staging = make_restart_evidence(
        phase=IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
        observation=make_observation(
            index_dirty=RecoveryStatus.YES,
            staged_paths=("test_module.py", "module.py"),
            staged_paths_complete=RecoveryStatus.YES,
            worktree_dirty=RecoveryStatus.YES,
        ),
    )

    assert (
        classify_isolated_commit_lifecycle_recovery(clean).classification
        is IsolatedCommitLifecycleRecoveryClassification.OLD_HEAD_CLEAN_INDEX
    )
    assert (
        classify_isolated_commit_lifecycle_recovery(exact_staging).classification
        is IsolatedCommitLifecycleRecoveryClassification.EXPECTED_PATH_STAGING_OBSERVED
    )


def test_execution_started_old_head_mismatch_or_incomplete_staging_rules() -> None:
    """Classify staging mismatch as diverged and incomplete evidence as insufficient."""

    mismatch = make_restart_evidence(
        phase=IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
        observation=make_observation(
            index_dirty=RecoveryStatus.YES,
            staged_paths=("module.py",),
            staged_paths_complete=RecoveryStatus.YES,
            worktree_dirty=RecoveryStatus.YES,
        ),
    )
    incomplete = make_restart_evidence(
        phase=IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
        observation=make_observation(
            index_dirty=RecoveryStatus.YES,
            staged_paths=(),
            staged_paths_complete=RecoveryStatus.UNKNOWN,
            worktree_dirty=RecoveryStatus.YES,
        ),
    )

    assert (
        classify_isolated_commit_lifecycle_recovery(mismatch).classification
        is IsolatedCommitLifecycleRecoveryClassification.DIVERGED_OR_INCONSISTENT
    )
    assert (
        classify_isolated_commit_lifecycle_recovery(incomplete).classification
        is IsolatedCommitLifecycleRecoveryClassification.INSUFFICIENT_EVIDENCE
    )


def test_execution_started_other_head_candidate_metadata_rules() -> None:
    """Map candidate metadata aggregate YES/NO/UNKNOWN to the required classes."""

    restart = make_restart_evidence(
        phase=IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
        observation=make_observation(
            observed_branch_head=SHA1_OTHER,
            observed_registered_head=SHA1_OTHER,
            observed_worktree_head=SHA1_OTHER,
        ),
    )
    yes = make_candidate(
        candidate_head=SHA1_OTHER,
        parent=RecoveryStatus.YES,
        message=RecoveryStatus.YES,
        paths=RecoveryStatus.YES,
    )
    no = make_candidate(
        candidate_head=SHA1_OTHER,
        parent=RecoveryStatus.NO,
        message=RecoveryStatus.YES,
        paths=RecoveryStatus.YES,
    )
    unknown = make_candidate(
        candidate_head=SHA1_OTHER,
        parent=RecoveryStatus.YES,
        message=RecoveryStatus.UNKNOWN,
        paths=RecoveryStatus.YES,
    )

    assert (
        classify_isolated_commit_lifecycle_recovery(restart, yes).classification
        is IsolatedCommitLifecycleRecoveryClassification.COMMIT_CANDIDATE_OBSERVED
    )
    assert (
        classify_isolated_commit_lifecycle_recovery(restart, no).classification
        is IsolatedCommitLifecycleRecoveryClassification.DIVERGED_OR_INCONSISTENT
    )
    assert (
        classify_isolated_commit_lifecycle_recovery(restart, unknown).classification
        is IsolatedCommitLifecycleRecoveryClassification.INSUFFICIENT_EVIDENCE
    )


def test_execution_started_other_head_without_candidate_is_insufficient() -> None:
    """Classify OTHER-head execution-started evidence as insufficient without candidate."""

    restart = make_restart_evidence(
        phase=IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
        observation=make_observation(
            observed_branch_head=SHA1_OTHER,
            observed_registered_head=SHA1_OTHER,
            observed_worktree_head=SHA1_OTHER,
        ),
    )

    assessment = classify_isolated_commit_lifecycle_recovery(restart)

    assert (
        assessment.classification
        is IsolatedCommitLifecycleRecoveryClassification.INSUFFICIENT_EVIDENCE
    )


def test_verified_classification_rules() -> None:
    """Apply VERIFIED relation rules for persisted new head, old head, other, unknown."""

    persisted = make_restart_evidence(
        phase=IsolatedCommitLifecyclePhase.VERIFIED,
        new_head=SHA1_NEW,
        observation=make_observation(
            observed_branch_head=SHA1_NEW,
            observed_registered_head=SHA1_NEW,
            observed_worktree_head=SHA1_NEW,
            index_dirty=RecoveryStatus.YES,
            staged_paths=("module.py",),
            staged_paths_complete=RecoveryStatus.YES,
            worktree_dirty=RecoveryStatus.YES,
        ),
    )
    old = make_restart_evidence(
        phase=IsolatedCommitLifecyclePhase.VERIFIED,
        new_head=SHA1_NEW,
        observation=make_observation(
            observed_branch_head=SHA1_OLD,
            observed_registered_head=SHA1_OLD,
            observed_worktree_head=SHA1_OLD,
        ),
    )
    other = make_restart_evidence(
        phase=IsolatedCommitLifecyclePhase.VERIFIED,
        new_head=SHA1_NEW,
        observation=make_observation(
            observed_branch_head=SHA1_OTHER,
            observed_registered_head=SHA1_OTHER,
            observed_worktree_head=SHA1_OTHER,
        ),
    )
    unknown = make_restart_evidence(
        phase=IsolatedCommitLifecyclePhase.VERIFIED,
        new_head=SHA1_NEW,
        observation=make_observation(
            branch_present=RecoveryStatus.UNKNOWN,
            observed_branch_head=None,
            registered=RecoveryStatus.UNKNOWN,
            observed_registered_branch=None,
            observed_registered_head=None,
            registration_locked=RecoveryStatus.UNKNOWN,
            registration_prunable=RecoveryStatus.UNKNOWN,
            target_present=RecoveryStatus.UNKNOWN,
            target_is_directory=RecoveryStatus.UNKNOWN,
            worktree_identity_valid=RecoveryStatus.UNKNOWN,
            observed_worktree_branch=None,
            observed_worktree_head=None,
            index_dirty=RecoveryStatus.UNKNOWN,
            staged_paths=(),
            staged_paths_complete=RecoveryStatus.UNKNOWN,
            worktree_dirty=RecoveryStatus.UNKNOWN,
        ),
    )

    assert (
        classify_isolated_commit_lifecycle_recovery(persisted).classification
        is IsolatedCommitLifecycleRecoveryClassification.PERSISTED_VERIFIED_COMMIT_OBSERVED
    )
    assert (
        classify_isolated_commit_lifecycle_recovery(old).classification
        is IsolatedCommitLifecycleRecoveryClassification.DIVERGED_OR_INCONSISTENT
    )
    assert (
        classify_isolated_commit_lifecycle_recovery(other).classification
        is IsolatedCommitLifecycleRecoveryClassification.DIVERGED_OR_INCONSISTENT
    )
    assert (
        classify_isolated_commit_lifecycle_recovery(unknown).classification
        is IsolatedCommitLifecycleRecoveryClassification.INSUFFICIENT_EVIDENCE
    )


def test_verified_other_head_never_downgrades_to_candidate_classification() -> None:
    """Keep VERIFIED+OTHER diverged even if fabricated candidate metadata looks matching."""

    restart = make_restart_evidence(
        phase=IsolatedCommitLifecyclePhase.VERIFIED,
        new_head=SHA1_NEW,
        observation=make_observation(
            observed_branch_head=SHA1_OTHER,
            observed_registered_head=SHA1_OTHER,
            observed_worktree_head=SHA1_OTHER,
        ),
    )
    candidate = make_candidate(candidate_head=SHA1_OTHER)

    assessment = classify_isolated_commit_lifecycle_recovery(restart, candidate)

    assert (
        assessment.classification
        is IsolatedCommitLifecycleRecoveryClassification.DIVERGED_OR_INCONSISTENT
    )


def test_source_head_changed_does_not_change_commit_window_classification() -> None:
    """Keep source divergence separate from isolated commit-window classification."""

    changed_source = make_restart_evidence(
        phase=IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
        observation=make_observation(
            observed_source_head="e" * 40,
            observed_branch_head=SHA1_OTHER,
            observed_registered_head=SHA1_OTHER,
            observed_worktree_head=SHA1_OTHER,
        ),
    )

    assessment = classify_isolated_commit_lifecycle_recovery(
        changed_source,
        make_candidate(candidate_head=SHA1_OTHER),
    )

    assert (
        assessment.classification
        is IsolatedCommitLifecycleRecoveryClassification.COMMIT_CANDIDATE_OBSERVED
    )


def test_assessment_retains_exact_restart_evidence_object() -> None:
    """Preserve restart evidence immutably for later operator policy."""

    restart = make_restart_evidence()

    assessment = classify_isolated_commit_lifecycle_recovery(restart)

    assert assessment.restart_evidence is restart


def test_direct_inspection_validates_source_and_record_before_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validate direct inspection API input types before restart inspection."""

    called = False

    def unexpected(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("restart inspection should not run")

    monkeypatch.setattr(
        lifecycle_recovery, "inspect_isolated_commit_restart", unexpected
    )

    with pytest.raises(ConfigurationError, match="source Path"):
        inspect_isolated_commit_lifecycle_recovery(
            "not-a-path",  # type: ignore[arg-type]
            make_record(),
        )
    with pytest.raises(ConfigurationError, match="LifecycleRecord"):
        inspect_isolated_commit_lifecycle_recovery(
            tmp_path,
            object(),  # type: ignore[arg-type]
        )

    assert called is False


def test_direct_inspection_invokes_restart_once_and_candidate_gating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invoke restart exactly once and candidate inspection only in allowed window."""

    source = tmp_path / "source"
    source.mkdir()
    record = make_record(phase=IsolatedCommitLifecyclePhase.EXECUTION_STARTED)
    restart = make_restart_evidence(
        phase=IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
        observation=make_observation(
            observed_branch_head=SHA1_OTHER,
            observed_registered_head=SHA1_OTHER,
            observed_worktree_head=SHA1_OTHER,
        ),
    )

    restart_calls: list[tuple[Path, IsolatedCommitLifecycleRecord]] = []
    candidate_calls: list[tuple[Path, str, str, tuple[str, ...], str]] = []

    def fake_restart(path: Path, loaded_record: IsolatedCommitLifecycleRecord):
        restart_calls.append((path, loaded_record))
        return restart

    def fake_candidate(
        repository: Path,
        candidate_head: str,
        *,
        old_head: str,
        expected_paths,
        commit_message_fingerprint: str,
    ):
        candidate_calls.append(
            (
                repository,
                candidate_head,
                old_head,
                tuple(expected_paths),
                commit_message_fingerprint,
            )
        )
        return make_candidate(candidate_head=candidate_head)

    monkeypatch.setattr(
        lifecycle_recovery, "inspect_isolated_commit_restart", fake_restart
    )
    monkeypatch.setattr(
        lifecycle_recovery,
        "inspect_isolated_commit_recovery_candidate",
        fake_candidate,
    )

    assessment = inspect_isolated_commit_lifecycle_recovery(source, record)

    assert restart_calls == [(source, record)]
    assert candidate_calls == [
        (
            source,
            SHA1_OTHER,
            record.old_head,
            record.paths,
            record.commit_message_fingerprint,
        )
    ]
    assert (
        assessment.classification
        is IsolatedCommitLifecycleRecoveryClassification.COMMIT_CANDIDATE_OBSERVED
    )


def test_direct_inspection_does_not_candidate_inspect_for_disallowed_cases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not inspect candidates for OLD_HEAD, PLANNED-OTHER, or VERIFIED-OTHER cases."""

    source = tmp_path / "source"
    source.mkdir()
    candidate_called = False

    def fail_candidate(*_args, **_kwargs):
        nonlocal candidate_called
        candidate_called = True
        raise AssertionError("candidate inspection must not run")

    monkeypatch.setattr(
        lifecycle_recovery,
        "inspect_isolated_commit_recovery_candidate",
        fail_candidate,
    )

    scenarios = [
        (
            make_record(phase=IsolatedCommitLifecyclePhase.EXECUTION_STARTED),
            make_restart_evidence(phase=IsolatedCommitLifecyclePhase.EXECUTION_STARTED),
        ),
        (
            make_record(phase=IsolatedCommitLifecyclePhase.PLANNED),
            make_restart_evidence(
                phase=IsolatedCommitLifecyclePhase.PLANNED,
                observation=make_observation(
                    observed_branch_head=SHA1_OTHER,
                    observed_registered_head=SHA1_OTHER,
                    observed_worktree_head=SHA1_OTHER,
                ),
            ),
        ),
        (
            make_record(phase=IsolatedCommitLifecyclePhase.VERIFIED, new_head=SHA1_NEW),
            make_restart_evidence(
                phase=IsolatedCommitLifecyclePhase.VERIFIED,
                new_head=SHA1_NEW,
                observation=make_observation(
                    observed_branch_head=SHA1_OTHER,
                    observed_registered_head=SHA1_OTHER,
                    observed_worktree_head=SHA1_OTHER,
                ),
            ),
        ),
    ]

    for record, restart in scenarios:
        monkeypatch.setattr(
            lifecycle_recovery,
            "inspect_isolated_commit_restart",
            lambda _path, _record, restart=restart: restart,
        )
        inspect_isolated_commit_lifecycle_recovery(source, record)

    assert candidate_called is False


def test_direct_inspection_candidate_unknown_evidence_maps_to_insufficient(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UNKNOWN candidate metadata yields insufficient evidence without mutation."""

    source = tmp_path / "source"
    source.mkdir()
    record = make_record(phase=IsolatedCommitLifecyclePhase.EXECUTION_STARTED)
    restart = make_restart_evidence(
        phase=IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
        observation=make_observation(
            observed_branch_head=SHA1_OTHER,
            observed_registered_head=SHA1_OTHER,
            observed_worktree_head=SHA1_OTHER,
        ),
    )

    monkeypatch.setattr(
        lifecycle_recovery,
        "inspect_isolated_commit_restart",
        lambda _path, _record: restart,
    )
    monkeypatch.setattr(
        lifecycle_recovery,
        "inspect_isolated_commit_recovery_candidate",
        lambda *_args, **_kwargs: make_candidate(
            candidate_head=SHA1_OTHER,
            parent=RecoveryStatus.YES,
            message=RecoveryStatus.UNKNOWN,
            paths=RecoveryStatus.YES,
        ),
    )

    assessment = inspect_isolated_commit_lifecycle_recovery(source, record)

    assert (
        assessment.classification
        is IsolatedCommitLifecycleRecoveryClassification.INSUFFICIENT_EVIDENCE
    )


def test_persisted_api_validates_inputs_before_store_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validate persisted API Path/store/SessionId before reading lifecycle store."""

    store = create_store(tmp_path)
    session_id = SessionId("persisted-session")
    called = False

    def unexpected(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("store read should not run")

    monkeypatch.setattr(IsolatedCommitLifecycleStore, "read", unexpected)

    with pytest.raises(ConfigurationError, match="source Path"):
        inspect_persisted_isolated_commit_lifecycle_recovery(
            "not-a-path",  # type: ignore[arg-type]
            store,
            session_id,
        )
    with pytest.raises(ConfigurationError, match="LifecycleStore"):
        inspect_persisted_isolated_commit_lifecycle_recovery(
            tmp_path,
            object(),  # type: ignore[arg-type]
            session_id,
        )
    with pytest.raises(ConfigurationError, match="SessionId"):
        inspect_persisted_isolated_commit_lifecycle_recovery(
            tmp_path,
            store,
            object(),  # type: ignore[arg-type]
        )

    assert called is False


def test_persisted_api_reads_exact_requested_session_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read exactly one requested session record and nothing else."""

    source = tmp_path / "source"
    source.mkdir()
    store = create_store(tmp_path)
    session_id = SessionId("read-once")
    record = make_record(session_id=session_id)
    requested: list[SessionId] = []

    monkeypatch.setattr(
        IsolatedCommitLifecycleStore,
        "read",
        lambda self, value: requested.append(value) or record,
    )
    monkeypatch.setattr(
        lifecycle_recovery,
        "inspect_isolated_commit_lifecycle_recovery",
        lambda _path, _record: classify_isolated_commit_lifecycle_recovery(
            make_restart_evidence()
        ),
    )

    inspect_persisted_isolated_commit_lifecycle_recovery(source, store, session_id)

    assert requested == [session_id]


def test_persisted_api_missing_record_returns_none_without_git_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return None and skip Git inspection when no lifecycle record exists."""

    source = tmp_path / "source"
    source.mkdir()
    store = create_store(tmp_path)
    session_id = SessionId("missing")
    called = False

    monkeypatch.setattr(IsolatedCommitLifecycleStore, "read", lambda self, _sid: None)

    def unexpected(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("direct inspection should not run")

    monkeypatch.setattr(
        lifecycle_recovery,
        "inspect_isolated_commit_lifecycle_recovery",
        unexpected,
    )

    result = inspect_persisted_isolated_commit_lifecycle_recovery(
        source, store, session_id
    )

    assert result is None
    assert called is False


def test_persisted_api_store_failure_prevents_git_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Propagate bounded store failure and skip Git inspection."""

    source = tmp_path / "source"
    source.mkdir()
    store = create_store(tmp_path)
    session_id = SessionId("store-failure")
    called = False

    monkeypatch.setattr(
        IsolatedCommitLifecycleStore,
        "read",
        lambda self, _sid: (_ for _ in ()).throw(ConfigurationError("store failure")),
    )

    def unexpected(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("direct inspection should not run")

    monkeypatch.setattr(
        lifecycle_recovery,
        "inspect_isolated_commit_lifecycle_recovery",
        unexpected,
    )

    with pytest.raises(ConfigurationError, match="store failure"):
        inspect_persisted_isolated_commit_lifecycle_recovery(source, store, session_id)

    assert called is False


def test_persisted_api_valid_record_flows_to_direct_inspection_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flow one stored record into direct lifecycle recovery inspection exactly once."""

    source = tmp_path / "source"
    source.mkdir()
    store = create_store(tmp_path)
    session_id = SessionId("stored-session")
    record = make_record(session_id=session_id)
    calls: list[tuple[Path, IsolatedCommitLifecycleRecord]] = []

    monkeypatch.setattr(IsolatedCommitLifecycleStore, "read", lambda self, _sid: record)

    expected = classify_isolated_commit_lifecycle_recovery(make_restart_evidence())

    def fake_direct(path: Path, loaded_record: IsolatedCommitLifecycleRecord):
        calls.append((path, loaded_record))
        return expected

    monkeypatch.setattr(
        lifecycle_recovery,
        "inspect_isolated_commit_lifecycle_recovery",
        fake_direct,
    )

    result = inspect_persisted_isolated_commit_lifecycle_recovery(
        source, store, session_id
    )

    assert calls == [(source, record)]
    assert result == expected
