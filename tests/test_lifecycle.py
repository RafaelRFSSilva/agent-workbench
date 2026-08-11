"""Tests for the immutable isolated commit lifecycle record and serialization."""

import dataclasses
import json

import pytest

from agent_workbench.errors import ConfigurationError
from agent_workbench.lifecycle import (
    ISOLATED_COMMIT_LIFECYCLE_SCHEMA_VERSION,
    MAX_LIFECYCLE_RECORD_BYTES,
    IsolatedCommitLifecyclePhase,
    IsolatedCommitLifecycleRecord,
    deserialize_isolated_commit_lifecycle_record,
    serialize_isolated_commit_lifecycle_record,
)
from agent_workbench.session import SessionId

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

SHA1_OLD = "a" * 40
SHA1_NEW = "b" * 40
SHA1_SOURCE = "c" * 40
SHA256_OLD = "d" * 64
SHA256_NEW = "e" * 64
SHA256_SOURCE = "f" * 64
DIFF_FP = "0" * 64
CMF = "1" * 64
SESSION_VALUE = "session-001"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_record(**overrides: object) -> IsolatedCommitLifecycleRecord:
    """Return one valid planned lifecycle record with optional field overrides."""

    values: dict[str, object] = {
        "session_id": SessionId(SESSION_VALUE),
        "phase": IsolatedCommitLifecyclePhase.PLANNED,
        "target_display": "../isolated",
        "source_head": SHA1_SOURCE,
        "source_branch": "main",
        "branch_name": "agent/task",
        "old_head": SHA1_OLD,
        "paths": ("src/foo.py",),
        "diff_fingerprint": DIFF_FP,
        "commit_message_fingerprint": CMF,
        "new_head": None,
    }
    values.update(overrides)
    return IsolatedCommitLifecycleRecord(**values)  # type: ignore[arg-type]


def make_verified(**overrides: object) -> IsolatedCommitLifecycleRecord:
    """Return one valid verified lifecycle record with optional field overrides."""

    values: dict[str, object] = {
        "phase": IsolatedCommitLifecyclePhase.VERIFIED,
        "new_head": SHA1_NEW,
    }
    values.update(overrides)
    return make_record(**values)


# ---------------------------------------------------------------------------
# Valid record preservation
# ---------------------------------------------------------------------------


def test_preserves_all_fields() -> None:
    """Preserve every field of a valid planned lifecycle record."""

    sid = SessionId(SESSION_VALUE)
    record = IsolatedCommitLifecycleRecord(
        session_id=sid,
        phase=IsolatedCommitLifecyclePhase.PLANNED,
        target_display="../isolated",
        source_head=SHA1_SOURCE,
        source_branch="main",
        branch_name="agent/task",
        old_head=SHA1_OLD,
        paths=("src/foo.py", "tests/test_foo.py"),
        diff_fingerprint=DIFF_FP,
        commit_message_fingerprint=CMF,
        new_head=None,
    )

    assert record.session_id == sid
    assert record.phase is IsolatedCommitLifecyclePhase.PLANNED
    assert record.target_display == "../isolated"
    assert record.source_head == SHA1_SOURCE
    assert record.source_branch == "main"
    assert record.branch_name == "agent/task"
    assert record.old_head == SHA1_OLD
    assert record.paths == ("src/foo.py", "tests/test_foo.py")
    assert record.diff_fingerprint == DIFF_FP
    assert record.commit_message_fingerprint == CMF
    assert record.new_head is None


# ---------------------------------------------------------------------------
# Every lifecycle phase
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("phase", "new_head"),
    [
        (IsolatedCommitLifecyclePhase.PLANNED, None),
        (IsolatedCommitLifecyclePhase.EXECUTION_STARTED, None),
        (IsolatedCommitLifecyclePhase.VERIFIED, SHA1_NEW),
    ],
)
def test_accepts_every_lifecycle_phase(
    phase: IsolatedCommitLifecyclePhase,
    new_head: str | None,
) -> None:
    """Accept every valid phase with the corresponding new_head invariant."""

    record = make_record(phase=phase, new_head=new_head)

    assert record.phase is phase
    assert record.new_head == new_head


# ---------------------------------------------------------------------------
# SessionId preservation
# ---------------------------------------------------------------------------


def test_preserves_exact_session_id() -> None:
    """Preserve the exact SessionId instance."""

    sid = SessionId("my-unique-session-42")
    record = make_record(session_id=sid)

    assert record.session_id is sid
    assert record.session_id.value == "my-unique-session-42"


# ---------------------------------------------------------------------------
# Mutable path input snapshotting
# ---------------------------------------------------------------------------


def test_snapshots_mutable_path_input() -> None:
    """Prevent later caller mutation from changing the recorded paths."""

    paths = ["src/a.py", "src/b.py"]
    record = make_record(paths=paths)
    paths.append("src/c.py")

    assert record.paths == ("src/a.py", "src/b.py")


def test_paths_is_a_tuple() -> None:
    """Store paths as a tuple regardless of input type."""

    record = make_record(paths=["src/a.py"])

    assert isinstance(record.paths, tuple)


# ---------------------------------------------------------------------------
# Frozen / slotted / value equality / hashability
# ---------------------------------------------------------------------------


def test_is_frozen_slotted_value_comparable_and_hashable() -> None:
    """Provide immutable value semantics without an instance dictionary."""

    first = make_record()
    second = make_record()

    assert first == second
    assert hash(first) == hash(second)
    assert len({first, second}) == 1
    assert not hasattr(first, "__dict__")

    with pytest.raises(dataclasses.FrozenInstanceError):
        first.target_display = "../changed"  # type: ignore[misc]


def test_inequality_on_differing_field() -> None:
    """Two records with different phase compare unequal."""

    planned = make_record(phase=IsolatedCommitLifecyclePhase.PLANNED, new_head=None)
    started = make_record(
        phase=IsolatedCommitLifecyclePhase.EXECUTION_STARTED, new_head=None
    )

    assert planned != started


# ---------------------------------------------------------------------------
# SHA-1 and SHA-256 object IDs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source_head", "old_head"),
    [
        ("a" * 40, "b" * 40),
        ("c" * 64, "d" * 64),
        ("e" * 40, "f" * 64),
    ],
)
def test_accepts_sha1_and_sha256_object_ids(source_head: str, old_head: str) -> None:
    """Accept the complete object-ID forms supported by modern Git."""

    record = make_record(source_head=source_head, old_head=old_head)

    assert record.source_head == source_head
    assert record.old_head == old_head


# ---------------------------------------------------------------------------
# Phase / new_head invariants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phase",
    [
        IsolatedCommitLifecyclePhase.PLANNED,
        IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
    ],
)
def test_rejects_non_none_new_head_for_non_verified_phases(
    phase: IsolatedCommitLifecyclePhase,
) -> None:
    """Reject a non-None new_head when the phase is not VERIFIED."""

    with pytest.raises(ConfigurationError):
        make_record(phase=phase, new_head=SHA1_NEW)


def test_rejects_none_new_head_for_verified() -> None:
    """Reject None new_head when the phase is VERIFIED."""

    with pytest.raises(ConfigurationError):
        make_record(phase=IsolatedCommitLifecyclePhase.VERIFIED, new_head=None)


def test_rejects_new_head_equal_to_old_head_for_verified() -> None:
    """Reject new_head == old_head for the VERIFIED phase."""

    with pytest.raises(ConfigurationError):
        make_record(
            phase=IsolatedCommitLifecyclePhase.VERIFIED,
            new_head=SHA1_OLD,
        )


def test_verified_accepts_sha1_new_head() -> None:
    """Accept a valid SHA-1 new_head for the VERIFIED phase."""

    record = make_verified(new_head=SHA1_NEW)

    assert record.new_head == SHA1_NEW


def test_verified_accepts_sha256_new_head() -> None:
    """Accept a valid SHA-256 new_head for the VERIFIED phase."""

    record = make_verified(old_head=SHA256_OLD, new_head=SHA256_NEW)

    assert record.new_head == SHA256_NEW


# ---------------------------------------------------------------------------
# Invalid targets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    [
        "",
        "   ",
        "/absolute/path",
        "C:\\windows\\path",
        "multi\nline",
        "has\rnull",
        "has\x00nul",
    ],
)
def test_rejects_invalid_target_display(target: str) -> None:
    """Reject blank, absolute, multiline, and NUL-containing targets."""

    with pytest.raises(ConfigurationError):
        make_record(target_display=target)


def test_accepts_sibling_target_display() -> None:
    """Allow a sibling worktree path as the target display."""

    record = make_record(target_display="../isolated")

    assert record.target_display == "../isolated"


def test_accepts_simple_target_display() -> None:
    """Allow a simple relative path as the target display."""

    record = make_record(target_display="worktrees/agent-task")

    assert record.target_display == "worktrees/agent-task"


# ---------------------------------------------------------------------------
# Invalid branches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_branch", ""),
        ("source_branch", "   "),
        ("source_branch", "line\nbreak"),
        ("source_branch", "has\x00nul"),
        ("branch_name", ""),
        ("branch_name", "  "),
        ("branch_name", "cr\r"),
        ("branch_name", "nul\x00"),
    ],
)
def test_rejects_invalid_branch_names(field: str, value: str) -> None:
    """Reject blank or control-character-containing branch names."""

    with pytest.raises(ConfigurationError):
        make_record(**{field: value})


def test_preserves_branch_names_exactly() -> None:
    """Preserve branch names without normalization."""

    record = make_record(source_branch="  padded  ", branch_name="agent/task/1")

    assert record.source_branch == "  padded  "
    assert record.branch_name == "agent/task/1"


# ---------------------------------------------------------------------------
# Invalid object IDs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "oid",
    [
        "",
        "abc",
        "g" * 40,
        "a" * 39,
        "a" * 41,
        "a" * 63,
        "a" * 65,
        "ABCDEF" + "a" * 34 + "!",
    ],
)
def test_rejects_invalid_object_ids(oid: str) -> None:
    """Reject object IDs that are not complete SHA-1 or SHA-256 digests."""

    with pytest.raises(ConfigurationError):
        make_record(old_head=oid)


def test_rejects_invalid_source_head_object_id() -> None:
    """Reject an invalid source_head object ID."""

    with pytest.raises(ConfigurationError):
        make_record(source_head="notanoid")


def test_accepts_mixed_case_object_id() -> None:
    """Accept mixed-case hex object IDs."""

    oid = "AaBbCcDd" + "0" * 32
    record = make_record(old_head=oid)

    assert record.old_head == oid


# ---------------------------------------------------------------------------
# Invalid and duplicate paths
# ---------------------------------------------------------------------------


def test_rejects_bare_string_paths() -> None:
    """Reject a bare string as the paths argument."""

    with pytest.raises(ConfigurationError):
        make_record(paths="src/foo.py")  # type: ignore[arg-type]


def test_rejects_empty_paths_iterable() -> None:
    """Require at least one path."""

    with pytest.raises(ConfigurationError):
        make_record(paths=[])


def test_rejects_absolute_path() -> None:
    """Reject an absolute path in the paths list."""

    with pytest.raises(ConfigurationError):
        make_record(paths=["/etc/passwd"])


def test_rejects_path_with_dotdot() -> None:
    """Reject a path that traverses above the repository root."""

    with pytest.raises(ConfigurationError):
        make_record(paths=["../outside.py"])


def test_rejects_path_with_backslash() -> None:
    """Reject a non-portable backslash path."""

    with pytest.raises(ConfigurationError):
        make_record(paths=["src\\foo.py"])


def test_rejects_blank_path() -> None:
    """Reject an empty or whitespace-only path string."""

    with pytest.raises(ConfigurationError):
        make_record(paths=[""])


def test_rejects_duplicate_paths() -> None:
    """Reject duplicate entries in the paths list."""

    with pytest.raises(ConfigurationError):
        make_record(paths=["src/a.py", "src/a.py"])


def test_preserves_path_order() -> None:
    """Preserve the original path order without sorting."""

    paths = ["z.py", "a.py", "m.py"]
    record = make_record(paths=paths)

    assert record.paths == ("z.py", "a.py", "m.py")


# ---------------------------------------------------------------------------
# Invalid fingerprints
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fp",
    [
        "",
        "a" * 63,
        "a" * 65,
        "A" * 64,
        "g" * 64,
        "0" * 32,
    ],
)
def test_rejects_invalid_diff_fingerprint(fp: str) -> None:
    """Reject diff_fingerprint that is not a lowercase 64-char hex string."""

    with pytest.raises(ConfigurationError):
        make_record(diff_fingerprint=fp)


@pytest.mark.parametrize(
    "fp",
    [
        "",
        "a" * 63,
        "a" * 65,
        "Z" * 64,
    ],
)
def test_rejects_invalid_commit_message_fingerprint(fp: str) -> None:
    """Reject commit_message_fingerprint that is not a lowercase SHA-256 hex string."""

    with pytest.raises(ConfigurationError):
        make_record(commit_message_fingerprint=fp)


def test_accepts_valid_lowercase_sha256_fingerprints() -> None:
    """Accept lowercase 64-char hex fingerprints."""

    fp = "0123456789abcdef" * 4
    record = make_record(diff_fingerprint=fp, commit_message_fingerprint=fp)

    assert record.diff_fingerprint == fp
    assert record.commit_message_fingerprint == fp


# ---------------------------------------------------------------------------
# Deterministic serialization
# ---------------------------------------------------------------------------


def test_serialization_is_deterministic() -> None:
    """Produce exactly the same bytes for the same record on repeated calls."""

    record = make_record()

    first = serialize_isolated_commit_lifecycle_record(record)
    second = serialize_isolated_commit_lifecycle_record(record)

    assert first == second


def test_serialized_bytes_contains_expected_fields() -> None:
    """Include all required schema fields in the serialized payload."""

    record = make_record()
    payload = serialize_isolated_commit_lifecycle_record(record)
    data = json.loads(payload)

    assert data["schema_version"] == ISOLATED_COMMIT_LIFECYCLE_SCHEMA_VERSION
    assert data["kind"] == "isolated_commit_lifecycle"
    assert data["session_id"] == SESSION_VALUE
    assert data["phase"] == "planned"
    assert data["target_display"] == "../isolated"
    assert data["source_head"] == SHA1_SOURCE
    assert data["source_branch"] == "main"
    assert data["branch_name"] == "agent/task"
    assert data["old_head"] == SHA1_OLD
    assert data["paths"] == ["src/foo.py"]
    assert data["diff_fingerprint"] == DIFF_FP
    assert data["commit_message_fingerprint"] == CMF
    assert data["new_head"] is None


def test_serialized_bytes_end_with_exactly_one_lf() -> None:
    """End with exactly one LF byte and no extra whitespace."""

    record = make_record()
    payload = serialize_isolated_commit_lifecycle_record(record)

    assert payload.endswith(b"\n")
    assert not payload.endswith(b"\n\n")


def test_serialized_bytes_are_valid_utf8() -> None:
    """Produce valid UTF-8 without ASCII escaping of non-ASCII characters."""

    sid = SessionId("session-\u00e9-\u4e2d\u6587")
    record = make_record(session_id=sid)
    payload = serialize_isolated_commit_lifecycle_record(record)

    text = payload.decode("utf-8")
    assert "\u00e9" in text


# ---------------------------------------------------------------------------
# Exact round trip
# ---------------------------------------------------------------------------


def test_round_trip_planned() -> None:
    """Serialize and deserialize a PLANNED record and recover an equal record."""

    record = make_record()
    payload = serialize_isolated_commit_lifecycle_record(record)
    recovered = deserialize_isolated_commit_lifecycle_record(payload)

    assert recovered == record


def test_round_trip_execution_started() -> None:
    """Round-trip an EXECUTION_STARTED record."""

    record = make_record(
        phase=IsolatedCommitLifecyclePhase.EXECUTION_STARTED, new_head=None
    )
    payload = serialize_isolated_commit_lifecycle_record(record)
    recovered = deserialize_isolated_commit_lifecycle_record(payload)

    assert recovered == record


def test_round_trip_verified() -> None:
    """Round-trip a VERIFIED record with a non-None new_head."""

    record = make_verified()
    payload = serialize_isolated_commit_lifecycle_record(record)
    recovered = deserialize_isolated_commit_lifecycle_record(payload)

    assert recovered == record


def test_round_trip_multiple_paths() -> None:
    """Round-trip a record with multiple paths in original order."""

    record = make_record(paths=["z.py", "a.py", "m.py"])
    payload = serialize_isolated_commit_lifecycle_record(record)
    recovered = deserialize_isolated_commit_lifecycle_record(payload)

    assert recovered.paths == ("z.py", "a.py", "m.py")


# ---------------------------------------------------------------------------
# Unicode SessionId round trip
# ---------------------------------------------------------------------------


def test_unicode_session_id_round_trip() -> None:
    """Preserve a Unicode session ID through a full serialize/deserialize cycle."""

    sid = SessionId("session-\u00e9-\u4e2d\u6587")
    record = make_record(session_id=sid)
    payload = serialize_isolated_commit_lifecycle_record(record)
    recovered = deserialize_isolated_commit_lifecycle_record(payload)

    assert recovered.session_id.value == "session-\u00e9-\u4e2d\u6587"


# ---------------------------------------------------------------------------
# Deserialization error cases
# ---------------------------------------------------------------------------


def test_rejects_non_bytes_input() -> None:
    """Reject deserialization of non-bytes input."""

    with pytest.raises(ConfigurationError):
        deserialize_isolated_commit_lifecycle_record("not bytes")  # type: ignore[arg-type]


def test_rejects_non_bytes_none_input() -> None:
    """Reject None as deserialization input."""

    with pytest.raises(ConfigurationError):
        deserialize_isolated_commit_lifecycle_record(None)  # type: ignore[arg-type]


def test_rejects_invalid_utf8() -> None:
    """Reject payloads that are not valid UTF-8."""

    with pytest.raises(ConfigurationError):
        deserialize_isolated_commit_lifecycle_record(b"\xff\xfe invalid utf-8")


def test_rejects_malformed_json() -> None:
    """Reject payloads that are not valid JSON."""

    with pytest.raises(ConfigurationError):
        deserialize_isolated_commit_lifecycle_record(b"not json at all\n")


def test_rejects_json_array_at_top_level() -> None:
    """Reject a JSON array as the top-level value."""

    with pytest.raises(ConfigurationError):
        deserialize_isolated_commit_lifecycle_record(b"[1, 2, 3]\n")


def test_rejects_duplicate_json_keys() -> None:
    """Reject JSON objects with duplicate keys."""

    payload = b'{"schema_version":1,"kind":"x","kind":"y"}\n'
    with pytest.raises(ConfigurationError):
        deserialize_isolated_commit_lifecycle_record(payload)


def test_rejects_missing_key() -> None:
    """Reject a payload that is missing a required key."""

    record = make_record()
    payload = serialize_isolated_commit_lifecycle_record(record)
    data = json.loads(payload)
    del data["phase"]
    broken = json.dumps(data).encode("utf-8") + b"\n"

    with pytest.raises(ConfigurationError):
        deserialize_isolated_commit_lifecycle_record(broken)


def test_rejects_unknown_key() -> None:
    """Reject a payload that contains an unknown key."""

    record = make_record()
    payload = serialize_isolated_commit_lifecycle_record(record)
    data = json.loads(payload)
    data["unexpected_field"] = "surprise"
    broken = json.dumps(data).encode("utf-8") + b"\n"

    with pytest.raises(ConfigurationError):
        deserialize_isolated_commit_lifecycle_record(broken)


def test_rejects_unsupported_schema_version() -> None:
    """Reject a payload with an unsupported schema_version."""

    record = make_record()
    payload = serialize_isolated_commit_lifecycle_record(record)
    data = json.loads(payload)
    data["schema_version"] = 99
    broken = json.dumps(data).encode("utf-8") + b"\n"

    with pytest.raises(ConfigurationError):
        deserialize_isolated_commit_lifecycle_record(broken)


def test_rejects_wrong_kind() -> None:
    """Reject a payload with an incorrect kind value."""

    record = make_record()
    payload = serialize_isolated_commit_lifecycle_record(record)
    data = json.loads(payload)
    data["kind"] = "something_else"
    broken = json.dumps(data).encode("utf-8") + b"\n"

    with pytest.raises(ConfigurationError):
        deserialize_isolated_commit_lifecycle_record(broken)


def test_rejects_invalid_phase_value() -> None:
    """Reject a payload with an unrecognized phase string."""

    record = make_record()
    payload = serialize_isolated_commit_lifecycle_record(record)
    data = json.loads(payload)
    data["phase"] = "unknown_phase"
    broken = json.dumps(data).encode("utf-8") + b"\n"

    with pytest.raises(ConfigurationError):
        deserialize_isolated_commit_lifecycle_record(broken)


def test_rejects_oversized_payload() -> None:
    """Reject payloads that exceed the maximum lifecycle-record size."""

    oversized = b"x" * (MAX_LIFECYCLE_RECORD_BYTES + 1)
    with pytest.raises(ConfigurationError):
        deserialize_isolated_commit_lifecycle_record(oversized)


def test_rejects_invalid_field_type_paths_not_list() -> None:
    """Reject a payload where paths is not a JSON array."""

    record = make_record()
    payload = serialize_isolated_commit_lifecycle_record(record)
    data = json.loads(payload)
    data["paths"] = "not-a-list"
    broken = json.dumps(data).encode("utf-8") + b"\n"

    with pytest.raises(ConfigurationError):
        deserialize_isolated_commit_lifecycle_record(broken)


def test_rejects_invalid_field_type_session_id_not_string() -> None:
    """Reject a payload where session_id is not a string."""

    record = make_record()
    payload = serialize_isolated_commit_lifecycle_record(record)
    data = json.loads(payload)
    data["session_id"] = 42
    broken = json.dumps(data).encode("utf-8") + b"\n"

    with pytest.raises(ConfigurationError):
        deserialize_isolated_commit_lifecycle_record(broken)


def test_rejects_invalid_record_invariants_via_deserialize() -> None:
    """Reject a payload that fails field-level record validation."""

    record = make_record()
    payload = serialize_isolated_commit_lifecycle_record(record)
    data = json.loads(payload)
    # Supply an invalid object ID for old_head.
    data["old_head"] = "not-an-oid"
    broken = json.dumps(data).encode("utf-8") + b"\n"

    with pytest.raises(ConfigurationError):
        deserialize_isolated_commit_lifecycle_record(broken)


# ---------------------------------------------------------------------------
# SessionId validation
# ---------------------------------------------------------------------------


def test_rejects_non_session_id_for_session_id_field() -> None:
    """Reject a plain string passed as session_id."""

    with pytest.raises(ConfigurationError):
        make_record(session_id="raw-string")  # type: ignore[arg-type]


def test_rejects_blank_session_id() -> None:
    """Reject a SessionId constructed from a blank string."""

    with pytest.raises(ConfigurationError):
        SessionId("   ")


# ---------------------------------------------------------------------------
# Phase enum validation
# ---------------------------------------------------------------------------


def test_rejects_non_enum_phase() -> None:
    """Reject a plain string passed as phase."""

    with pytest.raises(ConfigurationError):
        make_record(phase="planned")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Schema constant
# ---------------------------------------------------------------------------


def test_schema_version_constant_is_one() -> None:
    """Expose schema version 1 as the current constant."""

    assert ISOLATED_COMMIT_LIFECYCLE_SCHEMA_VERSION == 1


def test_serialized_schema_version_matches_constant() -> None:
    """Embed the schema version constant in every serialized record."""

    record = make_record()
    payload = serialize_isolated_commit_lifecycle_record(record)
    data = json.loads(payload)

    assert data["schema_version"] == ISOLATED_COMMIT_LIFECYCLE_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Strict schema_version type validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "version",
    [
        True,  # bool satisfies == 1 in Python but must be rejected
        1.0,  # float satisfies == 1 in Python but must be rejected
        "1",  # string
        None,  # null
    ],
)
def test_rejects_non_integer_schema_version(version: object) -> None:
    """Reject schema_version values that are not a plain Python int."""

    record = make_record()
    payload = serialize_isolated_commit_lifecycle_record(record)
    data = json.loads(payload)
    data["schema_version"] = version
    broken = json.dumps(data).encode("utf-8") + b"\n"

    with pytest.raises(ConfigurationError):
        deserialize_isolated_commit_lifecycle_record(broken)


def test_rejects_unsupported_integer_schema_version() -> None:
    """Reject a schema_version integer that is not the supported version."""

    record = make_record()
    payload = serialize_isolated_commit_lifecycle_record(record)
    data = json.loads(payload)
    data["schema_version"] = 99
    broken = json.dumps(data).encode("utf-8") + b"\n"

    with pytest.raises(ConfigurationError):
        deserialize_isolated_commit_lifecycle_record(broken)


# ---------------------------------------------------------------------------
# Explicit wire-type validation for kind and phase
# ---------------------------------------------------------------------------


def test_rejects_non_string_kind() -> None:
    """Reject a kind field that is not a JSON string."""

    record = make_record()
    payload = serialize_isolated_commit_lifecycle_record(record)
    data = json.loads(payload)
    data["kind"] = 1
    broken = json.dumps(data).encode("utf-8") + b"\n"

    with pytest.raises(ConfigurationError):
        deserialize_isolated_commit_lifecycle_record(broken)


def test_rejects_non_string_phase() -> None:
    """Reject a phase field that is not a JSON string."""

    record = make_record()
    payload = serialize_isolated_commit_lifecycle_record(record)
    data = json.loads(payload)
    data["phase"] = 0
    broken = json.dumps(data).encode("utf-8") + b"\n"

    with pytest.raises(ConfigurationError):
        deserialize_isolated_commit_lifecycle_record(broken)


# ---------------------------------------------------------------------------
# Serializer size enforcement
# ---------------------------------------------------------------------------


def test_serializer_rejects_record_exceeding_size_limit() -> None:
    """Reject serialization of a valid record whose output exceeds the size limit."""

    # Each path: 4 + 190 + 1 + 4 + 3 = 202 chars; 100 unique paths produce
    # a payload well above MAX_LIFECYCLE_RECORD_BYTES.
    prefix = "src/" + "x" * 190 + "_"
    paths = [f"{prefix}{i:04d}.py" for i in range(100)]
    record = make_record(paths=paths)

    with pytest.raises(ConfigurationError):
        serialize_isolated_commit_lifecycle_record(record)
