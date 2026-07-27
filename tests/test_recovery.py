"""Tests for immutable provider-independent recovery evidence."""

from collections.abc import Iterable
import dataclasses

import pytest

from agent_workbench.errors import ConfigurationError
from agent_workbench.recovery import (
    IsolatedCommitRecoveryEvidence,
    IsolatedCommitRecoveryPhase,
    RecoveryStatus,
)

EXPECTED_HEAD = "a" * 40
CHANGED_HEAD = "b" * 40


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
