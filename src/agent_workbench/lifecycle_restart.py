"""Read-only restart inspection for persisted isolated commit lifecycle records."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from agent_workbench.errors import ConfigurationError
from agent_workbench.lifecycle import (
    IsolatedCommitLifecyclePhase,
    IsolatedCommitLifecycleRecord,
)
from agent_workbench.lifecycle_store import IsolatedCommitLifecycleStore
from agent_workbench.recovery import RecoveryStatus
from agent_workbench.session import SessionId
from agent_workbench.worktrees import (
    WorktreeRestartObservation,
    inspect_worktree_restart_state,
)


class IsolatedCommitHeadRelation(StrEnum):
    """Represent a factual relation between an observed HEAD and persisted state."""

    UNKNOWN = "unknown"
    OLD_HEAD = "old_head"
    PERSISTED_NEW_HEAD = "persisted_new_head"
    OTHER = "other"


@dataclass(frozen=True, slots=True, init=False)
class IsolatedCommitRestartEvidence:
    """Compose one persisted lifecycle record with current read-only Git observation."""

    lifecycle_record: IsolatedCommitLifecycleRecord = field(repr=False)
    git_observation: WorktreeRestartObservation

    def __init__(
        self,
        *,
        lifecycle_record: IsolatedCommitLifecycleRecord,
        git_observation: WorktreeRestartObservation,
    ) -> None:
        """Validate and retain immutable restart evidence."""

        if not isinstance(lifecycle_record, IsolatedCommitLifecycleRecord):
            raise ConfigurationError(
                "isolated commit restart evidence requires an IsolatedCommitLifecycleRecord."
            )
        if not isinstance(git_observation, WorktreeRestartObservation):
            raise ConfigurationError(
                "isolated commit restart evidence requires a WorktreeRestartObservation."
            )
        object.__setattr__(self, "lifecycle_record", lifecycle_record)
        object.__setattr__(self, "git_observation", git_observation)

    @property
    def persisted_phase(self) -> IsolatedCommitLifecyclePhase:
        """Return the persisted lifecycle phase."""

        return self.lifecycle_record.phase

    @property
    def target_display(self) -> str:
        """Return the persisted target display."""

        return self.lifecycle_record.target_display

    @property
    def expected_source_head(self) -> str:
        """Return the persisted source head."""

        return self.lifecycle_record.source_head

    @property
    def expected_source_branch(self) -> str:
        """Return the persisted source branch."""

        return self.lifecycle_record.source_branch

    @property
    def expected_branch(self) -> str:
        """Return the persisted isolated branch name."""

        return self.lifecycle_record.branch_name

    @property
    def old_head(self) -> str:
        """Return the persisted old head."""

        return self.lifecycle_record.old_head

    @property
    def persisted_new_head(self) -> str | None:
        """Return the persisted verified new head, if any."""

        return self.lifecycle_record.new_head

    @property
    def expected_paths(self) -> tuple[str, ...]:
        """Return the persisted repository-relative path set."""

        return self.lifecycle_record.paths

    @property
    def source_head_changed(self) -> RecoveryStatus:
        """Derive whether the current source HEAD differs from persisted state."""

        observed = self.git_observation.observed_source_head
        if observed is None:
            return RecoveryStatus.UNKNOWN
        if observed == self.lifecycle_record.source_head:
            return RecoveryStatus.NO
        return RecoveryStatus.YES

    @property
    def registered_branch_matches_expected(self) -> RecoveryStatus:
        """Derive whether the registered branch matches the persisted branch."""

        observed = self.git_observation.observed_registered_branch
        if observed is None:
            return RecoveryStatus.UNKNOWN
        if observed == self.lifecycle_record.branch_name:
            return RecoveryStatus.YES
        return RecoveryStatus.NO

    @property
    def worktree_branch_matches_expected(self) -> RecoveryStatus:
        """Derive whether the current worktree branch matches the persisted branch."""

        observed = self.git_observation.observed_worktree_branch
        if observed is None:
            return RecoveryStatus.UNKNOWN
        if observed == self.lifecycle_record.branch_name:
            return RecoveryStatus.YES
        return RecoveryStatus.NO

    @property
    def staged_paths_match_expected(self) -> RecoveryStatus:
        """Derive whether the complete staged-path set matches persisted paths."""

        if self.git_observation.staged_paths_complete is not RecoveryStatus.YES:
            return RecoveryStatus.UNKNOWN
        if set(self.git_observation.staged_paths) == set(self.lifecycle_record.paths):
            return RecoveryStatus.YES
        return RecoveryStatus.NO

    @property
    def branch_head_relation(self) -> IsolatedCommitHeadRelation:
        """Relate the observed branch head to persisted commit identities."""

        return _head_relation(
            self.lifecycle_record,
            self.git_observation.observed_branch_head,
        )

    @property
    def registered_head_relation(self) -> IsolatedCommitHeadRelation:
        """Relate the observed registered head to persisted commit identities."""

        return _head_relation(
            self.lifecycle_record,
            self.git_observation.observed_registered_head,
        )

    @property
    def worktree_head_relation(self) -> IsolatedCommitHeadRelation:
        """Relate the observed worktree head to persisted commit identities."""

        return _head_relation(
            self.lifecycle_record,
            self.git_observation.observed_worktree_head,
        )


def inspect_isolated_commit_restart(
    source_repository: Path,
    record: IsolatedCommitLifecycleRecord,
) -> IsolatedCommitRestartEvidence:
    """Inspect current Git state for one already-loaded persisted lifecycle record."""

    if not isinstance(source_repository, Path):
        raise ConfigurationError(
            "isolated commit restart inspection requires a source Path."
        )
    if not isinstance(record, IsolatedCommitLifecycleRecord):
        raise ConfigurationError(
            "isolated commit restart inspection requires an IsolatedCommitLifecycleRecord."
        )

    observation = inspect_worktree_restart_state(
        source_repository,
        record.target_display,
        record.branch_name,
    )
    return IsolatedCommitRestartEvidence(
        lifecycle_record=record,
        git_observation=observation,
    )


def inspect_persisted_isolated_commit_restart(
    source_repository: Path,
    lifecycle_store: IsolatedCommitLifecycleStore,
    session_id: SessionId,
) -> IsolatedCommitRestartEvidence | None:
    """Inspect current Git state for one known persisted lifecycle session."""

    if not isinstance(source_repository, Path):
        raise ConfigurationError(
            "persisted isolated commit restart inspection requires a source Path."
        )
    if not isinstance(lifecycle_store, IsolatedCommitLifecycleStore):
        raise ConfigurationError(
            "persisted isolated commit restart inspection requires an IsolatedCommitLifecycleStore."
        )
    if not isinstance(session_id, SessionId):
        raise ConfigurationError(
            "persisted isolated commit restart inspection requires a SessionId."
        )

    record = lifecycle_store.read(session_id)
    if record is None:
        return None
    return inspect_isolated_commit_restart(source_repository, record)


def _head_relation(
    record: IsolatedCommitLifecycleRecord,
    observed_head: str | None,
) -> IsolatedCommitHeadRelation:
    """Compare one observed head to the persisted old and new commit identities."""

    if observed_head is None:
        return IsolatedCommitHeadRelation.UNKNOWN
    if observed_head == record.old_head:
        return IsolatedCommitHeadRelation.OLD_HEAD
    if record.new_head is not None and observed_head == record.new_head:
        return IsolatedCommitHeadRelation.PERSISTED_NEW_HEAD
    return IsolatedCommitHeadRelation.OTHER
