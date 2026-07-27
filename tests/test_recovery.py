"""Tests for immutable provider-independent recovery evidence."""

from collections.abc import Iterable
import dataclasses

import pytest

from agent_workbench.errors import ConfigurationError
from agent_workbench.recovery import (
    IsolatedCommitRecoveryEvidence,
    IsolatedCommitRecoveryPhase,
    RecoveryStatus,
    WorktreeRecoveryEvidence,
    WorktreeRecoveryPhase,
)

EXPECTED_HEAD = "a" * 40
CHANGED_HEAD = "b" * 40
SOURCE_HEAD = "c" * 40
WORKTREE_HEAD = "d" * 40
CHANGED_SOURCE_HEAD = "e" * 40
CHANGED_WORKTREE_HEAD = "f" * 40


def recovery_evidence(
    **overrides: object,
) -> IsolatedCommitRecoveryEvidence:
    """Create one valid isolated-commit recovery evidence object."""

    values: dict[str, object] = {
        "phase": IsolatedCommitRecoveryPhase.EXACT_STAGING,
        "target_display": "../isolated",
        "expected_branch": "agent/task",
        "observed_branch": "agent/task",
        "expected_head": EXPECTED_HEAD,
        "observed_head": EXPECTED_HEAD,
        "index_dirty": RecoveryStatus.YES,
        "staged_paths": ("new.py", "tracked.txt"),
        "worktree_dirty": RecoveryStatus.YES,
    }
    values.update(overrides)
    return IsolatedCommitRecoveryEvidence(**values)  # type: ignore[arg-type]


def worktree_recovery_evidence(
    **overrides: object,
) -> WorktreeRecoveryEvidence:
    """Create one valid worktree lifecycle recovery evidence object."""

    values: dict[str, object] = {
        "phase": WorktreeRecoveryPhase.CREATION,
        "target_display": "../isolated",
        "expected_branch": "agent/task",
        "expected_source_head": SOURCE_HEAD,
        "observed_source_head": SOURCE_HEAD,
        "expected_worktree_head": WORKTREE_HEAD,
        "observed_worktree_head": WORKTREE_HEAD,
        "observed_branch": "agent/task",
        "branch_present": RecoveryStatus.YES,
        "target_present": RecoveryStatus.YES,
        "registered": RecoveryStatus.YES,
    }
    values.update(overrides)
    return WorktreeRecoveryEvidence(**values)  # type: ignore[arg-type]


def test_preserves_valid_recovery_evidence() -> None:
    """Preserve exact safe identities and ordered staged paths."""

    evidence = recovery_evidence()

    assert evidence.phase is IsolatedCommitRecoveryPhase.EXACT_STAGING
    assert evidence.target_display == "../isolated"
    assert evidence.expected_branch == "agent/task"
    assert evidence.observed_branch == "agent/task"
    assert evidence.expected_head == EXPECTED_HEAD
    assert evidence.observed_head == EXPECTED_HEAD
    assert evidence.index_dirty is RecoveryStatus.YES
    assert evidence.staged_paths == ("new.py", "tracked.txt")
    assert evidence.worktree_dirty is RecoveryStatus.YES
    assert evidence.head_changed is RecoveryStatus.NO


@pytest.mark.parametrize(
    ("observed_head", "expected"),
    [
        (None, RecoveryStatus.UNKNOWN),
        (CHANGED_HEAD, RecoveryStatus.YES),
    ],
)
def test_derives_head_change_from_observed_identity(
    observed_head: str | None,
    expected: RecoveryStatus,
) -> None:
    """Derive unknown or changed state instead of storing duplicate evidence."""

    evidence = recovery_evidence(observed_head=observed_head)

    assert evidence.head_changed is expected


def test_snapshots_mutable_staged_paths() -> None:
    """Prevent later caller mutation from changing recorded evidence."""

    paths = ["new.py", "tracked.txt"]

    evidence = recovery_evidence(staged_paths=paths)
    paths.append("later.py")

    assert evidence.staged_paths == ("new.py", "tracked.txt")


def test_is_frozen_slotted_value_comparable_and_hashable() -> None:
    """Provide immutable value semantics without an instance dictionary."""

    first = recovery_evidence()
    second = recovery_evidence()

    assert first == second
    assert hash(first) == hash(second)
    assert len({first, second}) == 1
    assert not hasattr(first, "__dict__")

    with pytest.raises(dataclasses.FrozenInstanceError):
        first.target_display = "../changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "head",
    [
        "a" * 40,
        "b" * 64,
    ],
)
def test_accepts_complete_sha1_and_sha256_object_ids(head: str) -> None:
    """Accept the complete object-ID forms supported by modern Git."""

    evidence = recovery_evidence(
        expected_head=head,
        observed_head=head,
    )

    assert evidence.expected_head == head
    assert evidence.observed_head == head
    assert evidence.head_changed is RecoveryStatus.NO


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("phase", "exact_staging"),
        ("index_dirty", "yes"),
        ("worktree_dirty", "unknown"),
    ],
)
def test_rejects_non_enum_state_values(field: str, value: object) -> None:
    """Require explicit closed enum values for every recovery state."""

    with pytest.raises(ConfigurationError):
        recovery_evidence(**{field: value})


@pytest.mark.parametrize(
    "target_display",
    [
        "",
        "   ",
        "/tmp/isolated",
        "C:\\isolated",
        "line\nbreak",
        "invalid\0target",
    ],
)
def test_rejects_unsafe_target_displays(target_display: str) -> None:
    """Prevent blank, absolute, multiline, or NUL-containing target displays."""

    with pytest.raises(ConfigurationError, match="target display"):
        recovery_evidence(target_display=target_display)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expected_branch", ""),
        ("expected_branch", " "),
        ("expected_branch", "agent/task\nother"),
        ("observed_branch", ""),
        ("observed_branch", "agent/task\0other"),
    ],
)
def test_rejects_invalid_branch_evidence(field: str, value: str) -> None:
    """Require bounded single-line branch identity when it is observable."""

    with pytest.raises(ConfigurationError, match="branch"):
        recovery_evidence(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expected_head", ""),
        ("expected_head", "a" * 39),
        ("expected_head", "z" * 40),
        ("observed_head", "b" * 41),
        ("observed_head", "not-an-object-id"),
    ],
)
def test_rejects_invalid_git_object_ids(field: str, value: str) -> None:
    """Reject incomplete or non-hexadecimal Git identities."""

    with pytest.raises(ConfigurationError, match="HEAD"):
        recovery_evidence(**{field: value})


def test_allows_unobserved_branch_and_head() -> None:
    """Represent unavailable Git identity without inventing observed values."""

    evidence = recovery_evidence(
        observed_branch=None,
        observed_head=None,
    )

    assert evidence.observed_branch is None
    assert evidence.observed_head is None
    assert evidence.head_changed is RecoveryStatus.UNKNOWN


def test_rejects_bare_string_staged_path_collection() -> None:
    """Prevent one path string from being interpreted character by character."""

    with pytest.raises(ConfigurationError, match="staged paths"):
        recovery_evidence(staged_paths="new.py")


@pytest.mark.parametrize(
    "path",
    [
        "",
        " ",
        "/tmp/secret.py",
        "C:\\secret.py",
        "../escape.py",
        "nested/../escape.py",
        "./tracked.py",
        "tracked.py\nsecret",
        "tracked.py\0secret",
    ],
)
def test_rejects_unsafe_staged_paths(path: str) -> None:
    """Require canonical portable repository-relative staged paths."""

    with pytest.raises(ConfigurationError, match="staged path"):
        recovery_evidence(staged_paths=[path])


def test_rejects_duplicate_staged_paths() -> None:
    """Prevent ambiguous repeated path evidence."""

    with pytest.raises(ConfigurationError, match="duplicate"):
        recovery_evidence(staged_paths=["tracked.py", "tracked.py"])


@pytest.mark.parametrize(
    ("index_dirty", "staged_paths"),
    [
        (RecoveryStatus.YES, ()),
        (RecoveryStatus.NO, ("tracked.py",)),
        (RecoveryStatus.UNKNOWN, ("tracked.py",)),
    ],
)
def test_rejects_inconsistent_index_and_path_evidence(
    index_dirty: RecoveryStatus,
    staged_paths: Iterable[str],
) -> None:
    """Keep index dirtiness consistent with the observable staged path set."""

    with pytest.raises(ConfigurationError, match="index"):
        recovery_evidence(
            index_dirty=index_dirty,
            staged_paths=staged_paths,
        )


def test_preserves_valid_worktree_recovery_evidence() -> None:
    """Preserve exact safe lifecycle identities and observations."""

    evidence = worktree_recovery_evidence()

    assert evidence.phase is WorktreeRecoveryPhase.CREATION
    assert evidence.target_display == "../isolated"
    assert evidence.expected_branch == "agent/task"
    assert evidence.expected_source_head == SOURCE_HEAD
    assert evidence.observed_source_head == SOURCE_HEAD
    assert evidence.expected_worktree_head == WORKTREE_HEAD
    assert evidence.observed_worktree_head == WORKTREE_HEAD
    assert evidence.observed_branch == "agent/task"
    assert evidence.branch_present is RecoveryStatus.YES
    assert evidence.target_present is RecoveryStatus.YES
    assert evidence.registered is RecoveryStatus.YES
    assert evidence.source_head_changed is RecoveryStatus.NO
    assert evidence.worktree_head_changed is RecoveryStatus.NO


@pytest.mark.parametrize(
    "phase",
    [
        WorktreeRecoveryPhase.CREATION,
        WorktreeRecoveryPhase.REMOVAL,
        WorktreeRecoveryPhase.REMOVAL_VERIFICATION,
    ],
)
def test_accepts_every_worktree_recovery_phase(
    phase: WorktreeRecoveryPhase,
) -> None:
    """Represent each current mutating worktree lifecycle boundary."""

    evidence = worktree_recovery_evidence(phase=phase)

    assert evidence.phase is phase


def test_derives_changed_worktree_recovery_heads() -> None:
    """Derive changed source and worktree identities from observations."""

    evidence = worktree_recovery_evidence(
        observed_source_head=CHANGED_SOURCE_HEAD,
        observed_worktree_head=CHANGED_WORKTREE_HEAD,
    )

    assert evidence.source_head_changed is RecoveryStatus.YES
    assert evidence.worktree_head_changed is RecoveryStatus.YES


def test_derives_unknown_worktree_recovery_heads() -> None:
    """Represent unavailable identities without inventing Git state."""

    evidence = worktree_recovery_evidence(
        observed_source_head=None,
        observed_worktree_head=None,
        observed_branch=None,
        branch_present=RecoveryStatus.UNKNOWN,
        target_present=RecoveryStatus.UNKNOWN,
        registered=RecoveryStatus.UNKNOWN,
    )

    assert evidence.observed_source_head is None
    assert evidence.observed_worktree_head is None
    assert evidence.observed_branch is None
    assert evidence.source_head_changed is RecoveryStatus.UNKNOWN
    assert evidence.worktree_head_changed is RecoveryStatus.UNKNOWN


def test_worktree_recovery_evidence_is_frozen_slotted_and_hashable() -> None:
    """Provide immutable value semantics without exposing instance storage."""

    first = worktree_recovery_evidence()
    second = worktree_recovery_evidence()

    assert first == second
    assert hash(first) == hash(second)
    assert len({first, second}) == 1
    assert not hasattr(first, "__dict__")

    with pytest.raises(dataclasses.FrozenInstanceError):
        first.target_display = "../changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("phase", "creation"),
        ("branch_present", "yes"),
        ("target_present", "no"),
        ("registered", "unknown"),
    ],
)
def test_rejects_non_enum_worktree_recovery_values(
    field: str,
    value: object,
) -> None:
    """Require closed enum values for lifecycle phase and observations."""

    with pytest.raises(ConfigurationError):
        worktree_recovery_evidence(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expected_source_head", ""),
        ("expected_source_head", "a" * 39),
        ("expected_worktree_head", "z" * 40),
        ("observed_source_head", "b" * 41),
        ("observed_worktree_head", "not-an-object-id"),
    ],
)
def test_rejects_invalid_worktree_recovery_heads(
    field: str,
    value: str,
) -> None:
    """Require complete SHA-1 or SHA-256 lifecycle identities."""

    with pytest.raises(ConfigurationError, match="HEAD"):
        worktree_recovery_evidence(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("target_display", "/tmp/isolated"),
        ("target_display", "C:\\isolated"),
        ("target_display", "target\nother"),
        ("expected_branch", ""),
        ("expected_branch", "agent/task\nother"),
        ("observed_branch", "agent/task\0other"),
    ],
)
def test_rejects_unsafe_worktree_recovery_identity(
    field: str,
    value: str,
) -> None:
    """Reject unsafe target or branch identity evidence."""

    with pytest.raises(ConfigurationError):
        worktree_recovery_evidence(**{field: value})


def test_accepts_sha256_worktree_recovery_heads() -> None:
    """Support repositories using complete SHA-256 object identifiers."""

    source_head = "1" * 64
    worktree_head = "2" * 64

    evidence = worktree_recovery_evidence(
        expected_source_head=source_head,
        observed_source_head=source_head,
        expected_worktree_head=worktree_head,
        observed_worktree_head=worktree_head,
    )

    assert evidence.source_head_changed is RecoveryStatus.NO
    assert evidence.worktree_head_changed is RecoveryStatus.NO
