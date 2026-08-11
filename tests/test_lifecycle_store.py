"""Tests for crash-safe local lifecycle record persistence."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

import pytest

import agent_workbench.lifecycle_store as lifecycle_store
from agent_workbench.errors import CompletionError, ConfigurationError
from agent_workbench.lifecycle import (
    MAX_LIFECYCLE_RECORD_BYTES,
    IsolatedCommitLifecyclePhase,
    IsolatedCommitLifecycleRecord,
    deserialize_isolated_commit_lifecycle_record,
    serialize_isolated_commit_lifecycle_record,
)
from agent_workbench.lifecycle_store import IsolatedCommitLifecycleStore
from agent_workbench.session import SessionId

SHA1_OLD = "a" * 40
SHA1_NEW = "b" * 40
SHA1_SOURCE = "c" * 40
DIFF_FP = "0" * 64
CMF = "1" * 64


def make_record(
    *,
    session_id: SessionId,
    phase: IsolatedCommitLifecyclePhase = IsolatedCommitLifecyclePhase.PLANNED,
) -> IsolatedCommitLifecycleRecord:
    """Return one valid lifecycle record for the given phase."""

    new_head = SHA1_NEW if phase is IsolatedCommitLifecyclePhase.VERIFIED else None
    return IsolatedCommitLifecycleRecord(
        session_id=session_id,
        phase=phase,
        target_display="../isolated",
        source_head=SHA1_SOURCE,
        source_branch="main",
        branch_name="agent/task",
        old_head=SHA1_OLD,
        paths=("src/foo.py",),
        diff_fingerprint=DIFF_FP,
        commit_message_fingerprint=CMF,
        new_head=new_head,
    )


def expected_filename(session_id: SessionId) -> str:
    """Return the expected deterministic lifecycle filename for one session."""

    digest = hashlib.sha256(session_id.value.encode("utf-8")).hexdigest()
    return f"isolated-commit-{digest}.json"


def test_constructor_accepts_existing_directory(tmp_path: Path) -> None:
    """Accept one existing regular directory."""

    store = IsolatedCommitLifecycleStore(tmp_path)

    assert isinstance(store, IsolatedCommitLifecycleStore)


def test_constructor_rejects_non_path_input() -> None:
    """Reject non-Path construction input."""

    with pytest.raises(ConfigurationError, match="Path"):
        IsolatedCommitLifecycleStore("not-a-path")  # type: ignore[arg-type]


def test_constructor_rejects_missing_directory(tmp_path: Path) -> None:
    """Reject a missing store directory."""

    missing = tmp_path / "missing"
    with pytest.raises(ConfigurationError, match="does not exist"):
        IsolatedCommitLifecycleStore(missing)


def test_constructor_rejects_regular_file(tmp_path: Path) -> None:
    """Reject a regular file as the store directory."""

    target = tmp_path / "not-a-directory"
    target.write_text("x", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="must be a directory"):
        IsolatedCommitLifecycleStore(target)


def test_constructor_rejects_symlink_directory(tmp_path: Path) -> None:
    """Reject a symlink as the configured store directory."""

    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(ConfigurationError, match="must not be a symlink"):
        IsolatedCommitLifecycleStore(link)


def test_constructor_repr_does_not_expose_absolute_path(tmp_path: Path) -> None:
    """Keep repr bounded and path-safe."""

    store = IsolatedCommitLifecycleStore(tmp_path)

    assert str(tmp_path.resolve()) not in repr(store)


def test_same_session_id_maps_to_same_final_record(tmp_path: Path) -> None:
    """Reuse one deterministic destination for the same SessionId."""

    session_id = SessionId("session-1")
    store = IsolatedCommitLifecycleStore(tmp_path)
    first = make_record(session_id=session_id)
    second = make_record(
        session_id=session_id,
        phase=IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
    )

    store.write(first)
    store.write(second)

    files = [path.name for path in tmp_path.iterdir()]
    assert files == [expected_filename(session_id)]
    assert store.read(session_id) == second


def test_different_session_ids_do_not_collide(tmp_path: Path) -> None:
    """Produce different deterministic filenames for different sessions."""

    first_id = SessionId("session-a")
    second_id = SessionId("session-b")
    store = IsolatedCommitLifecycleStore(tmp_path)
    store.write(make_record(session_id=first_id))
    store.write(make_record(session_id=second_id))

    files = {path.name for path in tmp_path.iterdir()}
    assert files == {expected_filename(first_id), expected_filename(second_id)}


def test_unicode_session_id_uses_hash_filename(tmp_path: Path) -> None:
    """Support Unicode session IDs without exposing them in filenames."""

    session_id = SessionId("sessao-\u00e9-\u4e2d\u6587")
    store = IsolatedCommitLifecycleStore(tmp_path)
    store.write(make_record(session_id=session_id))

    filename = expected_filename(session_id)
    assert (tmp_path / filename).exists()
    assert session_id.value not in filename


def test_traversal_like_session_value_cannot_escape_directory(tmp_path: Path) -> None:
    """Ignore traversal-like characters by hashing the raw SessionId value."""

    session_id = SessionId("../outside/..\\still-inside")
    store = IsolatedCommitLifecycleStore(tmp_path)
    store.write(make_record(session_id=session_id))

    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert files[0].name == expected_filename(session_id)


def test_raw_session_id_value_is_not_used_as_filename(tmp_path: Path) -> None:
    """Never place SessionId.value directly in a destination path."""

    session_id = SessionId("session/with/slashes-and:chars")
    store = IsolatedCommitLifecycleStore(tmp_path)
    store.write(make_record(session_id=session_id))

    only_name = next(tmp_path.iterdir()).name
    assert only_name == expected_filename(session_id)
    assert "session" not in only_name


def test_write_then_read_returns_equal_record(tmp_path: Path) -> None:
    """Round-trip one lifecycle record through filesystem storage."""

    session_id = SessionId("round-trip")
    record = make_record(session_id=session_id)
    store = IsolatedCommitLifecycleStore(tmp_path)

    store.write(record)

    assert store.read(session_id) == record


@pytest.mark.parametrize("phase", list(IsolatedCommitLifecyclePhase))
def test_every_lifecycle_phase_round_trips(
    tmp_path: Path,
    phase: IsolatedCommitLifecyclePhase,
) -> None:
    """Persist and recover each lifecycle phase exactly."""

    session_id = SessionId(f"phase-{phase}")
    record = make_record(session_id=session_id, phase=phase)
    store = IsolatedCommitLifecycleStore(tmp_path)
    store.write(record)

    assert store.read(session_id) == record


def test_write_atomically_replaces_prior_regular_record(tmp_path: Path) -> None:
    """Replace one previous regular record atomically with new bytes."""

    session_id = SessionId("replace")
    store = IsolatedCommitLifecycleStore(tmp_path)
    first = make_record(session_id=session_id)
    second = make_record(
        session_id=session_id,
        phase=IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
    )

    store.write(first)
    store.write(second)

    payload = (tmp_path / expected_filename(session_id)).read_bytes()
    assert payload == serialize_isolated_commit_lifecycle_record(second)


def test_final_file_contains_exact_serializer_bytes(tmp_path: Path) -> None:
    """Persist exactly the lifecycle serializer output bytes."""

    session_id = SessionId("exact-bytes")
    record = make_record(session_id=session_id)
    store = IsolatedCommitLifecycleStore(tmp_path)
    store.write(record)

    target = tmp_path / expected_filename(session_id)
    assert target.read_bytes() == serialize_isolated_commit_lifecycle_record(record)


def test_final_file_is_regular_and_private_on_posix(tmp_path: Path) -> None:
    """Publish a regular file with private permissions on POSIX systems."""

    session_id = SessionId("mode")
    record = make_record(session_id=session_id)
    store = IsolatedCommitLifecycleStore(tmp_path)
    store.write(record)

    target = tmp_path / expected_filename(session_id)
    status = os.lstat(target)
    assert stat.S_ISREG(status.st_mode)
    if os.name == "posix":
        assert stat.S_IMODE(status.st_mode) == 0o600


def test_successful_write_leaves_no_temporary_files(tmp_path: Path) -> None:
    """Remove the per-call temporary lifecycle file on success."""

    session_id = SessionId("no-temp")
    store = IsolatedCommitLifecycleStore(tmp_path)
    store.write(make_record(session_id=session_id))

    names = [path.name for path in tmp_path.iterdir()]
    assert not any(name.startswith(".agent-workbench-lifecycle-") for name in names)


def test_read_missing_record_returns_none(tmp_path: Path) -> None:
    """Return None only when the final record does not exist."""

    store = IsolatedCommitLifecycleStore(tmp_path)

    assert store.read(SessionId("missing")) is None


def test_read_rejects_malformed_json(tmp_path: Path) -> None:
    """Fail closed on malformed persisted JSON."""

    session_id = SessionId("bad-json")
    (tmp_path / expected_filename(session_id)).write_bytes(b"{not-json\n")
    store = IsolatedCommitLifecycleStore(tmp_path)

    with pytest.raises(ConfigurationError, match="valid JSON"):
        store.read(session_id)


def test_read_rejects_invalid_utf8(tmp_path: Path) -> None:
    """Fail closed on non-UTF-8 payload bytes."""

    session_id = SessionId("bad-utf8")
    (tmp_path / expected_filename(session_id)).write_bytes(b"\xff\xfe")
    store = IsolatedCommitLifecycleStore(tmp_path)

    with pytest.raises(ConfigurationError, match="UTF-8"):
        store.read(session_id)


def test_read_rejects_unsupported_schema(tmp_path: Path) -> None:
    """Fail closed when persisted payload schema is unsupported."""

    session_id = SessionId("bad-schema")
    record = make_record(session_id=session_id)
    payload = serialize_isolated_commit_lifecycle_record(record)
    data = json.loads(payload)
    data["schema_version"] = 999
    (tmp_path / expected_filename(session_id)).write_bytes(
        json.dumps(data).encode("utf-8") + b"\n"
    )
    store = IsolatedCommitLifecycleStore(tmp_path)

    with pytest.raises(ConfigurationError, match="unsupported schema version"):
        store.read(session_id)


def test_read_rejects_oversized_payload(tmp_path: Path) -> None:
    """Reject payloads larger than the lifecycle size limit."""

    session_id = SessionId("oversized")
    (tmp_path / expected_filename(session_id)).write_bytes(
        b"x" * (MAX_LIFECYCLE_RECORD_BYTES + 1)
    )
    store = IsolatedCommitLifecycleStore(tmp_path)

    with pytest.raises(ConfigurationError, match="exceeds the"):
        store.read(session_id)


def test_read_rejects_truncated_payload(tmp_path: Path) -> None:
    """Reject truncated JSON payloads rather than treating them as missing."""

    session_id = SessionId("truncated")
    valid = serialize_isolated_commit_lifecycle_record(
        make_record(session_id=session_id)
    )
    (tmp_path / expected_filename(session_id)).write_bytes(valid[:-2])
    store = IsolatedCommitLifecycleStore(tmp_path)

    with pytest.raises(ConfigurationError, match="valid JSON"):
        store.read(session_id)


def test_read_rejects_valid_record_for_different_session_id(tmp_path: Path) -> None:
    """Fail closed when file contents do not match the requested SessionId."""

    session_a = SessionId("session-A")
    session_b = SessionId("session-B")
    final_path = tmp_path / expected_filename(session_a)
    record_for_b = make_record(session_id=session_b)
    payload_for_b = serialize_isolated_commit_lifecycle_record(record_for_b)
    final_path.write_bytes(payload_for_b)
    store = IsolatedCommitLifecycleStore(tmp_path)

    with pytest.raises(ConfigurationError, match="does not match the requested"):
        store.read(session_a)

    assert final_path.read_bytes() == payload_for_b
    assert (
        deserialize_isolated_commit_lifecycle_record(final_path.read_bytes())
        == record_for_b
    )


def test_read_rejects_symlink_destination_without_following(tmp_path: Path) -> None:
    """Reject a symlink final record path without reading its target."""

    session_id = SessionId("symlink")
    target = tmp_path / "outside.json"
    target.write_bytes(
        serialize_isolated_commit_lifecycle_record(make_record(session_id=session_id))
    )
    final_path = tmp_path / expected_filename(session_id)
    final_path.symlink_to(target)
    store = IsolatedCommitLifecycleStore(tmp_path)

    with pytest.raises(CompletionError, match="regular file"):
        store.read(session_id)


def test_read_rejects_directory_destination(tmp_path: Path) -> None:
    """Reject a directory where a record file is expected."""

    session_id = SessionId("directory")
    (tmp_path / expected_filename(session_id)).mkdir()
    store = IsolatedCommitLifecycleStore(tmp_path)

    with pytest.raises(CompletionError, match="regular file"):
        store.read(session_id)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO is not portable")
def test_read_rejects_special_file_destination(tmp_path: Path) -> None:
    """Reject a special file where a lifecycle record is expected."""

    session_id = SessionId("special")
    os.mkfifo(tmp_path / expected_filename(session_id))
    store = IsolatedCommitLifecycleStore(tmp_path)

    with pytest.raises(CompletionError, match="regular file"):
        store.read(session_id)


def test_write_requires_lifecycle_record(tmp_path: Path) -> None:
    """Require a typed lifecycle record for write."""

    store = IsolatedCommitLifecycleStore(tmp_path)
    with pytest.raises(ConfigurationError, match="LifecycleRecord"):
        store.write("not-a-record")  # type: ignore[arg-type]


def test_read_requires_session_id(tmp_path: Path) -> None:
    """Require a SessionId for read."""

    store = IsolatedCommitLifecycleStore(tmp_path)
    with pytest.raises(ConfigurationError, match="SessionId"):
        store.read("not-a-session")  # type: ignore[arg-type]


def test_write_order_is_flush_fsync_close_replace_directory_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Perform durable publication in the required ordering."""

    events: list[str] = []
    session_id = SessionId("ordering")
    record = make_record(session_id=session_id)
    store = IsolatedCommitLifecycleStore(tmp_path)

    original_fdopen = lifecycle_store.os.fdopen
    original_fsync = lifecycle_store.os.fsync
    original_replace = lifecycle_store.os.replace

    class RecordingFile:
        def __init__(self, wrapped) -> None:
            self._wrapped = wrapped

        def write(self, content: bytes) -> int:
            events.append("write")
            return self._wrapped.write(content)

        def flush(self) -> None:
            events.append("flush")
            self._wrapped.flush()

        def fileno(self) -> int:
            return self._wrapped.fileno()

        def __enter__(self):
            self._wrapped.__enter__()
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            events.append("close")
            return self._wrapped.__exit__(exc_type, exc, tb)

    def wrapped_fdopen(*args, **kwargs):
        return RecordingFile(original_fdopen(*args, **kwargs))

    def wrapped_fsync(descriptor: int) -> None:
        mode = os.fstat(descriptor).st_mode
        events.append("fsync_dir" if stat.S_ISDIR(mode) else "fsync_temp")
        original_fsync(descriptor)

    def wrapped_replace(*args, **kwargs) -> None:
        events.append("replace")
        original_replace(*args, **kwargs)

    monkeypatch.setattr(lifecycle_store.os, "fdopen", wrapped_fdopen)
    monkeypatch.setattr(lifecycle_store.os, "fsync", wrapped_fsync)
    monkeypatch.setattr(lifecycle_store.os, "replace", wrapped_replace)

    store.write(record)

    assert events.index("write") < events.index("flush")
    assert events.index("flush") < events.index("fsync_temp")
    assert events.index("fsync_temp") < events.index("close")
    assert events.index("close") < events.index("replace")
    assert events.index("replace") < events.index("fsync_dir")


def test_replace_not_called_before_temporary_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail before replacement when temporary fsync fails."""

    session_id = SessionId("fsync-fail")
    store = IsolatedCommitLifecycleStore(tmp_path)
    record = make_record(session_id=session_id)
    replace_calls = 0
    original_fsync = lifecycle_store.os.fsync

    def failing_fsync(descriptor: int) -> None:
        if stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("fsync failure")
        original_fsync(descriptor)

    def count_replace(*args, **kwargs) -> None:
        nonlocal replace_calls
        replace_calls += 1

    monkeypatch.setattr(lifecycle_store.os, "fsync", failing_fsync)
    monkeypatch.setattr(lifecycle_store.os, "replace", count_replace)

    with pytest.raises(CompletionError, match="temporary file"):
        store.write(record)
    assert replace_calls == 0


def test_temporary_creation_failure_preserves_existing_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leave the prior destination untouched on temp creation failure."""

    session_id = SessionId("temp-create-failure")
    store = IsolatedCommitLifecycleStore(tmp_path)
    first = make_record(session_id=session_id)
    second = make_record(
        session_id=session_id,
        phase=IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
    )
    store.write(first)

    original_open = lifecycle_store.os.open

    def failing_open(path, flags, *args, **kwargs):
        if isinstance(path, str) and path.startswith(".agent-workbench-lifecycle-"):
            raise OSError("cannot create temp")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(lifecycle_store.os, "open", failing_open)
    with pytest.raises(CompletionError, match="create lifecycle temporary"):
        store.write(second)

    assert store.read(session_id) == first


def test_temporary_write_failure_preserves_existing_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leave the prior destination untouched on temporary write failure."""

    session_id = SessionId("temp-write-failure")
    store = IsolatedCommitLifecycleStore(tmp_path)
    first = make_record(session_id=session_id)
    second = make_record(
        session_id=session_id,
        phase=IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
    )
    store.write(first)

    original_fdopen = lifecycle_store.os.fdopen

    class FailingWriter:
        def __init__(self, wrapped) -> None:
            self._wrapped = wrapped

        def write(self, _content: bytes) -> int:
            raise OSError("write failed")

        def flush(self) -> None:
            self._wrapped.flush()

        def fileno(self) -> int:
            return self._wrapped.fileno()

        def __enter__(self):
            self._wrapped.__enter__()
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return self._wrapped.__exit__(exc_type, exc, tb)

    monkeypatch.setattr(
        lifecycle_store.os,
        "fdopen",
        lambda *args, **kwargs: FailingWriter(original_fdopen(*args, **kwargs)),
    )

    with pytest.raises(CompletionError, match="write lifecycle temporary"):
        store.write(second)
    assert store.read(session_id) == first
    assert not any(
        path.name.startswith(".agent-workbench-lifecycle-")
        for path in tmp_path.iterdir()
    )


def test_replace_failure_preserves_existing_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leave the prior destination untouched when replacement fails."""

    session_id = SessionId("replace-failure")
    store = IsolatedCommitLifecycleStore(tmp_path)
    first = make_record(session_id=session_id)
    second = make_record(
        session_id=session_id,
        phase=IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
    )
    store.write(first)

    monkeypatch.setattr(
        lifecycle_store.os,
        "replace",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("replace failed")),
    )

    with pytest.raises(CompletionError, match="replace lifecycle destination"):
        store.write(second)
    assert store.read(session_id) == first


def test_cleanup_failure_does_not_mask_primary_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report the original write failure even if temp cleanup also fails."""

    session_id = SessionId("cleanup")
    store = IsolatedCommitLifecycleStore(tmp_path)
    original_fdopen = lifecycle_store.os.fdopen

    class FailingWriter:
        def __init__(self, wrapped) -> None:
            self._wrapped = wrapped

        def write(self, _content: bytes) -> int:
            raise OSError("write failure")

        def flush(self) -> None:
            self._wrapped.flush()

        def fileno(self) -> int:
            return self._wrapped.fileno()

        def __enter__(self):
            self._wrapped.__enter__()
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return self._wrapped.__exit__(exc_type, exc, tb)

    monkeypatch.setattr(
        lifecycle_store.os,
        "fdopen",
        lambda *args, **kwargs: FailingWriter(original_fdopen(*args, **kwargs)),
    )
    monkeypatch.setattr(
        lifecycle_store.os,
        "unlink",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("cleanup failed")),
    )

    with pytest.raises(CompletionError, match="write lifecycle temporary"):
        store.write(make_record(session_id=session_id))


def test_directory_close_failure_does_not_mask_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the original write failure when cleanup close for directory fd fails."""

    session_id = SessionId("close-write")
    store = IsolatedCommitLifecycleStore(tmp_path)
    original_close = lifecycle_store.os.close

    def failing_close(descriptor: int) -> None:
        mode = os.fstat(descriptor).st_mode
        if stat.S_ISDIR(mode):
            raise OSError("close dir failed")
        original_close(descriptor)

    monkeypatch.setattr(lifecycle_store.os, "close", failing_close)
    monkeypatch.setattr(
        lifecycle_store,
        "_create_temporary_file",
        lambda _directory_descriptor: (_ for _ in ()).throw(
            CompletionError("failed to create lifecycle temporary file.")
        ),
    )

    with pytest.raises(CompletionError, match="create lifecycle temporary"):
        store.write(make_record(session_id=session_id))


def test_directory_close_failure_does_not_mask_read_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the original read failure when cleanup close for directory fd fails."""

    session_id = SessionId("close-read")
    final_path = tmp_path / expected_filename(session_id)
    final_path.write_bytes(b"{not-json\n")
    store = IsolatedCommitLifecycleStore(tmp_path)
    original_close = lifecycle_store.os.close

    def failing_close(descriptor: int) -> None:
        mode = os.fstat(descriptor).st_mode
        if stat.S_ISDIR(mode):
            raise OSError("close dir failed")
        original_close(descriptor)

    monkeypatch.setattr(lifecycle_store.os, "close", failing_close)

    with pytest.raises(ConfigurationError, match="valid JSON"):
        store.read(session_id)


def test_directory_fsync_failure_after_replace_reports_failure_without_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Treat post-replace directory fsync failure as ambiguous durability."""

    session_id = SessionId("dir-fsync")
    store = IsolatedCommitLifecycleStore(tmp_path)
    old_record = make_record(session_id=session_id)
    new_record = make_record(
        session_id=session_id,
        phase=IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
    )
    store.write(old_record)

    replace_calls = 0
    original_replace = lifecycle_store.os.replace
    original_fsync = lifecycle_store.os.fsync

    def wrapped_replace(*args, **kwargs) -> None:
        nonlocal replace_calls
        replace_calls += 1
        original_replace(*args, **kwargs)

    def failing_directory_fsync(descriptor: int) -> None:
        mode = os.fstat(descriptor).st_mode
        if stat.S_ISDIR(mode):
            raise OSError("directory fsync failed")
        original_fsync(descriptor)

    monkeypatch.setattr(lifecycle_store.os, "replace", wrapped_replace)
    monkeypatch.setattr(lifecycle_store.os, "fsync", failing_directory_fsync)

    with pytest.raises(CompletionError, match="fsync lifecycle directory"):
        store.write(new_record)

    assert replace_calls == 1
    final_payload = (tmp_path / expected_filename(session_id)).read_bytes()
    assert final_payload == serialize_isolated_commit_lifecycle_record(new_record)
    assert store.read(session_id) == new_record


def test_read_rejects_inspect_open_identity_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed when the inspected target is swapped before open."""

    session_id = SessionId("read-race")
    final_name = expected_filename(session_id)
    initial = make_record(session_id=session_id)
    alternate = make_record(
        session_id=session_id,
        phase=IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
    )
    (tmp_path / final_name).write_bytes(
        serialize_isolated_commit_lifecycle_record(initial)
    )
    (tmp_path / "alternate.json").write_bytes(
        serialize_isolated_commit_lifecycle_record(alternate)
    )
    store = IsolatedCommitLifecycleStore(tmp_path)
    original_open = lifecycle_store.os.open

    def swapping_open(path, flags, *args, **kwargs):
        if path == final_name and kwargs.get("dir_fd") is not None:
            dir_fd = kwargs["dir_fd"]
            os.replace(
                "alternate.json", final_name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd
            )
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(lifecycle_store.os, "open", swapping_open)

    with pytest.raises(CompletionError, match="changed while reading"):
        store.read(session_id)


def test_read_returns_exact_deserialized_bytes(tmp_path: Path) -> None:
    """Pass exact bytes from disk to lifecycle deserialization."""

    session_id = SessionId("deserialize-exact")
    record = make_record(session_id=session_id)
    payload = serialize_isolated_commit_lifecycle_record(record)
    (tmp_path / expected_filename(session_id)).write_bytes(payload)
    store = IsolatedCommitLifecycleStore(tmp_path)

    recovered = store.read(session_id)
    assert recovered == deserialize_isolated_commit_lifecycle_record(payload)
