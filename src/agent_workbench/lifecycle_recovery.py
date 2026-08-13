"""Read-only conservative classification for persisted isolated commit restart evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from agent_workbench.errors import ConfigurationError
from agent_workbench.lifecycle import (
    IsolatedCommitLifecyclePhase,
    IsolatedCommitLifecycleRecord,
)
from agent_workbench.lifecycle_restart import (
    IsolatedCommitHeadRelation,
    IsolatedCommitRestartEvidence,
    inspect_isolated_commit_restart,
)
from agent_workbench.lifecycle_store import IsolatedCommitLifecycleStore
from agent_workbench.recovery import RecoveryStatus
from agent_workbench.session import SessionId
from agent_workbench.worktree_commits import (
    IsolatedCommitRecoveryCandidateEvidence,
    inspect_isolated_commit_recovery_candidate,
)


class IsolatedCommitLifecycleRecoveryClassification(StrEnum):
    """Represent one conservative restart-time isolated commit classification."""

    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    OLD_HEAD_CLEAN_INDEX = "old_head_clean_index"
    EXPECTED_PATH_STAGING_OBSERVED = "expected_path_staging_observed"
    COMMIT_CANDIDATE_OBSERVED = "commit_candidate_observed"
    PERSISTED_VERIFIED_COMMIT_OBSERVED = "persisted_verified_commit_observed"
    DIVERGED_OR_INCONSISTENT = "diverged_or_inconsistent"


@dataclass(frozen=True, slots=True, init=False)
class IsolatedCommitLifecycleRecoveryAssessment:
    """Store immutable conservative restart classification evidence."""

    restart_evidence: IsolatedCommitRestartEvidence = field(repr=False)
    classification: IsolatedCommitLifecycleRecoveryClassification
    candidate_evidence: IsolatedCommitRecoveryCandidateEvidence | None

    def __init__(
        self,
        *,
        restart_evidence: IsolatedCommitRestartEvidence,
        classification: IsolatedCommitLifecycleRecoveryClassification,
        candidate_evidence: IsolatedCommitRecoveryCandidateEvidence | None,
    ) -> None:
        """Validate and snapshot one lifecycle recovery classification assessment."""

        if not isinstance(restart_evidence, IsolatedCommitRestartEvidence):
            raise ConfigurationError(
                "lifecycle recovery assessment requires IsolatedCommitRestartEvidence."
            )
        if not isinstance(
            classification,
            IsolatedCommitLifecycleRecoveryClassification,
        ):
            raise ConfigurationError(
                "lifecycle recovery assessment classification is invalid."
            )
        if candidate_evidence is not None and not isinstance(
            candidate_evidence,
            IsolatedCommitRecoveryCandidateEvidence,
        ):
            raise ConfigurationError(
                "lifecycle recovery assessment candidate evidence is invalid."
            )

        _validate_candidate_binding(restart_evidence, candidate_evidence)
        derived = _classify(restart_evidence, candidate_evidence)
        if classification is not derived:
            raise ConfigurationError(
                "lifecycle recovery assessment classification is inconsistent with evidence."
            )

        object.__setattr__(self, "restart_evidence", restart_evidence)
        object.__setattr__(self, "classification", classification)
        object.__setattr__(self, "candidate_evidence", candidate_evidence)


def classify_isolated_commit_lifecycle_recovery(
    restart_evidence: IsolatedCommitRestartEvidence,
    candidate_evidence: IsolatedCommitRecoveryCandidateEvidence | None = None,
) -> IsolatedCommitLifecycleRecoveryAssessment:
    """Classify isolated commit restart evidence without performing Git I/O."""

    if not isinstance(restart_evidence, IsolatedCommitRestartEvidence):
        raise ConfigurationError(
            "lifecycle recovery classification requires IsolatedCommitRestartEvidence."
        )
    if candidate_evidence is not None and not isinstance(
        candidate_evidence,
        IsolatedCommitRecoveryCandidateEvidence,
    ):
        raise ConfigurationError(
            "lifecycle recovery classification candidate evidence is invalid."
        )

    _validate_candidate_binding(restart_evidence, candidate_evidence)

    classification = _classify(restart_evidence, candidate_evidence)
    return IsolatedCommitLifecycleRecoveryAssessment(
        restart_evidence=restart_evidence,
        classification=classification,
        candidate_evidence=candidate_evidence,
    )


def inspect_isolated_commit_lifecycle_recovery(
    source_repository: Path,
    record: IsolatedCommitLifecycleRecord,
) -> IsolatedCommitLifecycleRecoveryAssessment:
    """Inspect restart evidence and classify one isolated commit lifecycle record."""

    if not isinstance(source_repository, Path):
        raise ConfigurationError(
            "isolated commit lifecycle recovery inspection requires a source Path."
        )
    if not isinstance(record, IsolatedCommitLifecycleRecord):
        raise ConfigurationError(
            "isolated commit lifecycle recovery inspection requires an IsolatedCommitLifecycleRecord."
        )

    restart_evidence = inspect_isolated_commit_restart(source_repository, record)

    candidate_evidence: IsolatedCommitRecoveryCandidateEvidence | None = None
    observed_branch_head = restart_evidence.git_observation.observed_branch_head
    if (
        record.phase is IsolatedCommitLifecyclePhase.EXECUTION_STARTED
        and restart_evidence.branch_head_relation is IsolatedCommitHeadRelation.OTHER
        and observed_branch_head is not None
    ):
        candidate_evidence = inspect_isolated_commit_recovery_candidate(
            source_repository,
            observed_branch_head,
            old_head=record.old_head,
            expected_paths=record.paths,
            commit_message_fingerprint=record.commit_message_fingerprint,
        )

    return classify_isolated_commit_lifecycle_recovery(
        restart_evidence,
        candidate_evidence,
    )


def inspect_persisted_isolated_commit_lifecycle_recovery(
    source_repository: Path,
    lifecycle_store: IsolatedCommitLifecycleStore,
    session_id: SessionId,
) -> IsolatedCommitLifecycleRecoveryAssessment | None:
    """Read one persisted lifecycle record and classify its restart evidence."""

    if not isinstance(source_repository, Path):
        raise ConfigurationError(
            "persisted isolated commit lifecycle recovery inspection requires a source Path."
        )
    if not isinstance(lifecycle_store, IsolatedCommitLifecycleStore):
        raise ConfigurationError(
            "persisted isolated commit lifecycle recovery inspection requires an IsolatedCommitLifecycleStore."
        )
    if not isinstance(session_id, SessionId):
        raise ConfigurationError(
            "persisted isolated commit lifecycle recovery inspection requires a SessionId."
        )

    record = lifecycle_store.read(session_id)
    if record is None:
        return None
    return inspect_isolated_commit_lifecycle_recovery(source_repository, record)


def _validate_candidate_binding(
    restart_evidence: IsolatedCommitRestartEvidence,
    candidate_evidence: IsolatedCommitRecoveryCandidateEvidence | None,
) -> None:
    """Require candidate evidence to bind to the exact currently observed branch head."""

    if candidate_evidence is None:
        return

    if restart_evidence.branch_head_relation is not IsolatedCommitHeadRelation.OTHER:
        raise ConfigurationError(
            "candidate evidence requires branch head relation OTHER."
        )

    observed_branch_head = restart_evidence.git_observation.observed_branch_head
    if observed_branch_head is None:
        raise ConfigurationError(
            "candidate evidence requires an observed expected-branch head."
        )
    if candidate_evidence.candidate_head != observed_branch_head:
        raise ConfigurationError(
            "candidate evidence must match the exact observed expected-branch head."
        )


def _classify(
    restart_evidence: IsolatedCommitRestartEvidence,
    candidate_evidence: IsolatedCommitRecoveryCandidateEvidence | None,
) -> IsolatedCommitLifecycleRecoveryClassification:
    """Apply conservative classification rules to factual restart observations."""

    branch_head = restart_evidence.git_observation.observed_branch_head
    if branch_head is not None:
        if (
            restart_evidence.git_observation.observed_registered_head is not None
            and restart_evidence.git_observation.observed_registered_head != branch_head
        ):
            return (
                IsolatedCommitLifecycleRecoveryClassification.DIVERGED_OR_INCONSISTENT
            )
        if (
            restart_evidence.git_observation.observed_worktree_head is not None
            and restart_evidence.git_observation.observed_worktree_head != branch_head
        ):
            return (
                IsolatedCommitLifecycleRecoveryClassification.DIVERGED_OR_INCONSISTENT
            )

    if restart_evidence.git_observation.branch_present is RecoveryStatus.NO:
        return IsolatedCommitLifecycleRecoveryClassification.DIVERGED_OR_INCONSISTENT
    if restart_evidence.git_observation.branch_present is RecoveryStatus.UNKNOWN:
        return IsolatedCommitLifecycleRecoveryClassification.INSUFFICIENT_EVIDENCE

    if restart_evidence.branch_head_relation is IsolatedCommitHeadRelation.UNKNOWN:
        return IsolatedCommitLifecycleRecoveryClassification.INSUFFICIENT_EVIDENCE

    if (
        restart_evidence.registered_branch_matches_expected is RecoveryStatus.NO
        or restart_evidence.worktree_branch_matches_expected is RecoveryStatus.NO
    ):
        return IsolatedCommitLifecycleRecoveryClassification.DIVERGED_OR_INCONSISTENT

    phase = restart_evidence.persisted_phase
    if phase is IsolatedCommitLifecyclePhase.PLANNED:
        return _classify_planned(restart_evidence)
    if phase is IsolatedCommitLifecyclePhase.EXECUTION_STARTED:
        return _classify_execution_started(restart_evidence, candidate_evidence)
    return _classify_verified(restart_evidence)


def _classify_planned(
    restart_evidence: IsolatedCommitRestartEvidence,
) -> IsolatedCommitLifecycleRecoveryClassification:
    """Classify persisted planned lifecycle restart evidence."""

    relation = restart_evidence.branch_head_relation
    if relation is IsolatedCommitHeadRelation.OTHER:
        return IsolatedCommitLifecycleRecoveryClassification.DIVERGED_OR_INCONSISTENT
    if relation is not IsolatedCommitHeadRelation.OLD_HEAD:
        return IsolatedCommitLifecycleRecoveryClassification.INSUFFICIENT_EVIDENCE

    observation = restart_evidence.git_observation
    if observation.index_dirty is RecoveryStatus.UNKNOWN:
        return IsolatedCommitLifecycleRecoveryClassification.INSUFFICIENT_EVIDENCE
    if observation.index_dirty is RecoveryStatus.YES:
        return IsolatedCommitLifecycleRecoveryClassification.DIVERGED_OR_INCONSISTENT

    if observation.staged_paths_complete is not RecoveryStatus.YES:
        return IsolatedCommitLifecycleRecoveryClassification.INSUFFICIENT_EVIDENCE
    if observation.staged_paths:
        return IsolatedCommitLifecycleRecoveryClassification.DIVERGED_OR_INCONSISTENT
    return IsolatedCommitLifecycleRecoveryClassification.OLD_HEAD_CLEAN_INDEX


def _classify_execution_started(
    restart_evidence: IsolatedCommitRestartEvidence,
    candidate_evidence: IsolatedCommitRecoveryCandidateEvidence | None,
) -> IsolatedCommitLifecycleRecoveryClassification:
    """Classify persisted execution-started lifecycle restart evidence."""

    relation = restart_evidence.branch_head_relation
    observation = restart_evidence.git_observation

    if relation is IsolatedCommitHeadRelation.OLD_HEAD:
        if observation.index_dirty is RecoveryStatus.UNKNOWN:
            return IsolatedCommitLifecycleRecoveryClassification.INSUFFICIENT_EVIDENCE
        if observation.staged_paths_complete is not RecoveryStatus.YES:
            return IsolatedCommitLifecycleRecoveryClassification.INSUFFICIENT_EVIDENCE

        if observation.index_dirty is RecoveryStatus.NO:
            if observation.staged_paths:
                return IsolatedCommitLifecycleRecoveryClassification.DIVERGED_OR_INCONSISTENT
            return IsolatedCommitLifecycleRecoveryClassification.OLD_HEAD_CLEAN_INDEX

        if observation.index_dirty is RecoveryStatus.YES:
            if restart_evidence.staged_paths_match_expected is RecoveryStatus.YES:
                return IsolatedCommitLifecycleRecoveryClassification.EXPECTED_PATH_STAGING_OBSERVED
            if restart_evidence.staged_paths_match_expected is RecoveryStatus.NO:
                return IsolatedCommitLifecycleRecoveryClassification.DIVERGED_OR_INCONSISTENT
            return IsolatedCommitLifecycleRecoveryClassification.INSUFFICIENT_EVIDENCE

        return IsolatedCommitLifecycleRecoveryClassification.INSUFFICIENT_EVIDENCE

    if relation is IsolatedCommitHeadRelation.OTHER:
        if candidate_evidence is None:
            return IsolatedCommitLifecycleRecoveryClassification.INSUFFICIENT_EVIDENCE
        aggregate = candidate_evidence.metadata_matches_expected
        if aggregate is RecoveryStatus.YES:
            return (
                IsolatedCommitLifecycleRecoveryClassification.COMMIT_CANDIDATE_OBSERVED
            )
        if aggregate is RecoveryStatus.NO:
            return (
                IsolatedCommitLifecycleRecoveryClassification.DIVERGED_OR_INCONSISTENT
            )
        return IsolatedCommitLifecycleRecoveryClassification.INSUFFICIENT_EVIDENCE

    return IsolatedCommitLifecycleRecoveryClassification.INSUFFICIENT_EVIDENCE


def _classify_verified(
    restart_evidence: IsolatedCommitRestartEvidence,
) -> IsolatedCommitLifecycleRecoveryClassification:
    """Classify persisted verified lifecycle restart evidence."""

    relation = restart_evidence.branch_head_relation
    if relation is IsolatedCommitHeadRelation.PERSISTED_NEW_HEAD:
        return IsolatedCommitLifecycleRecoveryClassification.PERSISTED_VERIFIED_COMMIT_OBSERVED
    if relation in (
        IsolatedCommitHeadRelation.OLD_HEAD,
        IsolatedCommitHeadRelation.OTHER,
    ):
        return IsolatedCommitLifecycleRecoveryClassification.DIVERGED_OR_INCONSISTENT
    return IsolatedCommitLifecycleRecoveryClassification.INSUFFICIENT_EVIDENCE
