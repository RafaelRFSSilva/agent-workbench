"""Effectful lifecycle recovery actions with fresh approval and fail-closed checks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
import json
from pathlib import Path
from typing import cast

from agent_workbench.errors import CompletionError, ConfigurationError
from agent_workbench.lifecycle import (
    IsolatedCommitLifecyclePhase,
    IsolatedCommitLifecycleRecord,
)
from agent_workbench.lifecycle_recovery import (
    IsolatedCommitLifecycleRecoveryAssessment,
    IsolatedCommitLifecycleRecoveryClassification,
    inspect_isolated_commit_lifecycle_recovery,
)
from agent_workbench.lifecycle_store import IsolatedCommitLifecycleStore
from agent_workbench.recovery import RecoveryStatus
from agent_workbench.session import SessionId
from agent_workbench.tools import JSONObject, JSONValue, ToolApprovalDecision
from agent_workbench.worktree_commits import (
    IsolatedCommitRecoveryCandidatePreview,
    build_isolated_commit_recovery_candidate_preview,
)


class IsolatedCommitLifecycleRecoveryAction(StrEnum):
    """Identify one bounded mutating lifecycle recovery action."""

    ADOPT_CANDIDATE = "adopt_candidate"


class IsolatedCommitLifecycleRecoveryActionStatus(StrEnum):
    """Represent one bounded action result without carrying side effects."""

    COMPLETED = "completed"
    DENIED = "denied"


@dataclass(frozen=True, slots=True, init=False)
class IsolatedCommitLifecycleRecoveryApprovalRequest:
    """Store one immutable exact approval request for lifecycle recovery."""

    action: IsolatedCommitLifecycleRecoveryAction
    _preview_json: str = field(repr=False)

    def __init__(
        self,
        action: IsolatedCommitLifecycleRecoveryAction,
        preview: JSONValue,
    ) -> None:
        """Validate and snapshot one strict-JSON approval preview."""

        if not isinstance(action, IsolatedCommitLifecycleRecoveryAction):
            raise ConfigurationError(
                "recovery approval action must be an IsolatedCommitLifecycleRecoveryAction."
            )
        try:
            preview_json = json.dumps(
                preview,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError):
            raise ConfigurationError(
                "recovery approval preview must be strict JSON."
            ) from None
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "_preview_json", preview_json)

    @property
    def preview(self) -> JSONObject:
        """Return one independent copy of the exact approval preview."""

        return cast(JSONObject, json.loads(self._preview_json))


@dataclass(frozen=True, slots=True)
class IsolatedCommitLifecycleRecoveryActionResult:
    """Return one bounded result for candidate adoption."""

    action: IsolatedCommitLifecycleRecoveryAction
    status: IsolatedCommitLifecycleRecoveryActionStatus
    approved_candidate_head: str | None
    persisted_record: IsolatedCommitLifecycleRecord
    assessment: IsolatedCommitLifecycleRecoveryAssessment


type IsolatedCommitLifecycleRecoveryApprovalHandler = Callable[
    [IsolatedCommitLifecycleRecoveryApprovalRequest],
    ToolApprovalDecision,
]


def adopt_isolated_commit_recovery_candidate(
    source_repository: Path,
    lifecycle_store: IsolatedCommitLifecycleStore,
    session_id: SessionId,
    approval_handler: IsolatedCommitLifecycleRecoveryApprovalHandler,
) -> IsolatedCommitLifecycleRecoveryActionResult:
    """Adopt one currently observed compatible candidate as persisted VERIFIED."""

    if not isinstance(source_repository, Path):
        raise ConfigurationError(
            "candidate adoption requires a source repository Path."
        )
    if not isinstance(lifecycle_store, IsolatedCommitLifecycleStore):
        raise ConfigurationError(
            "candidate adoption requires an IsolatedCommitLifecycleStore."
        )
    if not isinstance(session_id, SessionId):
        raise ConfigurationError("candidate adoption requires a SessionId.")
    if not callable(approval_handler):
        raise ConfigurationError("candidate adoption requires an approval handler.")

    persisted_before = lifecycle_store.read(session_id)
    if persisted_before is None:
        raise CompletionError("requested lifecycle record was not found.")

    assessment_before = inspect_isolated_commit_lifecycle_recovery(
        source_repository,
        persisted_before,
    )

    _require_candidate_observed(assessment_before)
    candidate_before = assessment_before.candidate_evidence
    assert candidate_before is not None

    preview_before = _build_candidate_preview(
        source_repository,
        persisted_before,
        candidate_before.candidate_head,
    )
    request = IsolatedCommitLifecycleRecoveryApprovalRequest(
        IsolatedCommitLifecycleRecoveryAction.ADOPT_CANDIDATE,
        preview_before.preview,
    )

    decision = approval_handler(request)
    if decision is not ToolApprovalDecision.APPROVE:
        return IsolatedCommitLifecycleRecoveryActionResult(
            action=IsolatedCommitLifecycleRecoveryAction.ADOPT_CANDIDATE,
            status=IsolatedCommitLifecycleRecoveryActionStatus.DENIED,
            approved_candidate_head=None,
            persisted_record=persisted_before,
            assessment=assessment_before,
        )

    persisted_after_approval = lifecycle_store.read(session_id)
    if persisted_after_approval is None:
        raise CompletionError(
            "persisted lifecycle record disappeared after approval; no lifecycle mutation was performed."
        )
    if persisted_after_approval != persisted_before:
        raise CompletionError(
            "persisted lifecycle record changed after approval; no lifecycle mutation was performed."
        )

    assessment_after_approval = inspect_isolated_commit_lifecycle_recovery(
        source_repository,
        persisted_after_approval,
    )
    _require_candidate_observed(assessment_after_approval)
    candidate_after_approval = assessment_after_approval.candidate_evidence
    assert candidate_after_approval is not None
    if candidate_after_approval.candidate_head != candidate_before.candidate_head:
        raise CompletionError(
            "candidate head changed after approval; no lifecycle mutation was performed."
        )

    preview_after_approval = _build_candidate_preview(
        source_repository,
        persisted_after_approval,
        candidate_after_approval.candidate_head,
    )
    if preview_after_approval.preview != preview_before.preview:
        raise CompletionError(
            "candidate preview changed after approval; no lifecycle mutation was performed."
        )

    persisted_immediately_before_write = lifecycle_store.read(session_id)
    if persisted_immediately_before_write is None:
        raise CompletionError(
            "persisted lifecycle record disappeared before write; no lifecycle mutation was performed."
        )
    if persisted_immediately_before_write != persisted_before:
        raise CompletionError(
            "persisted lifecycle record changed before write; no lifecycle mutation was performed."
        )

    verified_record = IsolatedCommitLifecycleRecord(
        session_id=persisted_before.session_id,
        phase=IsolatedCommitLifecyclePhase.VERIFIED,
        target_display=persisted_before.target_display,
        source_head=persisted_before.source_head,
        source_branch=persisted_before.source_branch,
        branch_name=persisted_before.branch_name,
        old_head=persisted_before.old_head,
        paths=persisted_before.paths,
        diff_fingerprint=persisted_before.diff_fingerprint,
        commit_message_fingerprint=persisted_before.commit_message_fingerprint,
        new_head=candidate_before.candidate_head,
    )

    try:
        lifecycle_store.write(verified_record)
    except (CompletionError, ConfigurationError) as exc:
        raise CompletionError(
            "persisted lifecycle write failed; inspect persisted recovery state again: "
            f"{exc}"
        ) from None

    persisted_after_write = lifecycle_store.read(session_id)
    if persisted_after_write != verified_record:
        raise CompletionError(
            "persisted lifecycle verification failed after write; inspect persisted recovery state again."
        )

    assessment_after_write = inspect_isolated_commit_lifecycle_recovery(
        source_repository,
        persisted_after_write,
    )
    if (
        assessment_after_write.classification
        is not IsolatedCommitLifecycleRecoveryClassification.PERSISTED_VERIFIED_COMMIT_OBSERVED
    ):
        raise CompletionError(
            "post-write recovery verification failed; inspect persisted recovery state again."
        )
    if (
        assessment_after_write.restart_evidence.persisted_new_head
        != candidate_before.candidate_head
    ):
        raise CompletionError(
            "persisted verified head differs from approved candidate; inspect persisted recovery state again."
        )

    return IsolatedCommitLifecycleRecoveryActionResult(
        action=IsolatedCommitLifecycleRecoveryAction.ADOPT_CANDIDATE,
        status=IsolatedCommitLifecycleRecoveryActionStatus.COMPLETED,
        approved_candidate_head=candidate_before.candidate_head,
        persisted_record=verified_record,
        assessment=assessment_after_write,
    )


def _require_candidate_observed(
    assessment: IsolatedCommitLifecycleRecoveryAssessment,
) -> None:
    """Require exact candidate-observed classification and compatible metadata."""

    if (
        assessment.classification
        is not IsolatedCommitLifecycleRecoveryClassification.COMMIT_CANDIDATE_OBSERVED
    ):
        raise CompletionError(
            "candidate adoption requires classification commit_candidate_observed."
        )
    candidate = assessment.candidate_evidence
    if candidate is None:
        raise CompletionError("candidate adoption requires candidate evidence.")
    if candidate.metadata_matches_expected is not RecoveryStatus.YES:
        raise CompletionError(
            "candidate metadata must remain fully compatible for adoption."
        )


def _build_candidate_preview(
    source_repository: Path,
    persisted_record: IsolatedCommitLifecycleRecord,
    candidate_head: str,
) -> IsolatedCommitRecoveryCandidatePreview:
    """Build one complete exact preview for the current candidate commit."""

    try:
        return build_isolated_commit_recovery_candidate_preview(
            source_repository,
            expected_branch=persisted_record.branch_name,
            candidate_head=candidate_head,
            old_head=persisted_record.old_head,
        )
    except (CompletionError, ConfigurationError):
        raise ConfigurationError(
            "candidate preview could not be constructed safely."
        ) from None
