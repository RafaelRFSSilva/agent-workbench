"""Crash-safe local filesystem persistence for isolated commit lifecycle records.

This store provides atomic single-record replacement for one session identifier.
It intentionally does not implement multi-writer coordination.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import secrets
import stat

from agent_workbench.errors import CompletionError, ConfigurationError
from agent_workbench.lifecycle import (
    MAX_LIFECYCLE_RECORD_BYTES,
    IsolatedCommitLifecycleRecord,
    deserialize_isolated_commit_lifecycle_record,
    serialize_isolated_commit_lifecycle_record,
)
from agent_workbench.session import SessionId

_LIFECYCLE_PREFIX = "isolated-commit-"
_LIFECYCLE_SUFFIX = ".json"
_TEMP_PREFIX = ".agent-workbench-lifecycle-"
_TEMP_SUFFIX = ".tmp"
_MAX_TEMP_NAME_ATTEMPTS = 64


class IsolatedCommitLifecycleStore:
    """Persist lifecycle records in one caller-managed directory.

    The store guarantees crash-safe single-file publication but does not provide
    locking or ordering coordination among concurrent writers.
    """

    __slots__ = ("_directory", "_directory_device", "_directory_inode")

    def __init__(self, directory: Path) -> None:
        """Validate and retain one canonical, safely-openable directory."""

        if not isinstance(directory, Path):
            raise ConfigurationError("lifecycle store directory must be a Path.")

        supplied = directory.expanduser()
        try:
            supplied_status = os.lstat(supplied)
        except FileNotFoundError:
            raise ConfigurationError(
                "lifecycle store directory does not exist."
            ) from None
        except OSError:
            raise ConfigurationError(
                "unable to inspect lifecycle store directory."
            ) from None

        if stat.S_ISLNK(supplied_status.st_mode):
            raise ConfigurationError("lifecycle store directory must not be a symlink.")
        if not stat.S_ISDIR(supplied_status.st_mode):
            raise ConfigurationError("lifecycle store directory must be a directory.")

        try:
            canonical = supplied.resolve(strict=True)
        except (FileNotFoundError, OSError, RuntimeError):
            raise ConfigurationError(
                "unable to resolve lifecycle store directory."
            ) from None

        try:
            canonical_status = os.lstat(canonical)
        except OSError:
            raise ConfigurationError(
                "unable to inspect lifecycle store directory."
            ) from None
        if stat.S_ISLNK(canonical_status.st_mode) or not stat.S_ISDIR(
            canonical_status.st_mode
        ):
            raise ConfigurationError("lifecycle store directory must be a directory.")

        descriptor = _open_directory_descriptor(canonical, for_constructor=True)
        try:
            opened_status = os.fstat(descriptor)
        except OSError:
            _close_descriptor_best_effort(descriptor)
            raise ConfigurationError(
                "unable to inspect lifecycle store directory."
            ) from None
        _close_descriptor_best_effort(descriptor)

        if not _same_directory_identity(canonical_status, opened_status):
            raise ConfigurationError(
                "lifecycle store directory changed during validation."
            )

        self._directory = canonical
        self._directory_device = canonical_status.st_dev
        self._directory_inode = canonical_status.st_ino

    def __repr__(self) -> str:
        """Return a bounded safe representation."""

        return "IsolatedCommitLifecycleStore(directory=<verified>)"

    def write(self, record: IsolatedCommitLifecycleRecord) -> None:
        """Atomically publish one serialized lifecycle record."""

        if not isinstance(record, IsolatedCommitLifecycleRecord):
            raise ConfigurationError(
                "lifecycle store write requires an IsolatedCommitLifecycleRecord."
            )

        payload = serialize_isolated_commit_lifecycle_record(record)
        final_name = _final_record_name(record.session_id)
        directory_descriptor = self._open_verified_directory()

        temporary_name: str | None = None
        replaced = False
        try:
            temporary_name, temporary_descriptor = _create_temporary_file(
                directory_descriptor
            )
            _write_temporary_payload(temporary_descriptor, payload)
            _require_replaceable_destination(directory_descriptor, final_name)
            _replace_in_directory(directory_descriptor, temporary_name, final_name)
            replaced = True
            _fsync_descriptor(
                directory_descriptor, "failed to fsync lifecycle directory."
            )
        except (CompletionError, ConfigurationError):
            raise
        except OSError:
            raise CompletionError("lifecycle store write failed.") from None
        finally:
            if not replaced and temporary_name is not None:
                _unlink_temporary_best_effort(directory_descriptor, temporary_name)
            _close_descriptor_best_effort(directory_descriptor)

    def read(self, session_id: SessionId) -> IsolatedCommitLifecycleRecord | None:
        """Read and validate one lifecycle record by session identifier."""

        if not isinstance(session_id, SessionId):
            raise ConfigurationError("lifecycle store read requires a SessionId.")

        final_name = _final_record_name(session_id)
        directory_descriptor = self._open_verified_directory()
        try:
            inspected = _inspect_read_target(directory_descriptor, final_name)
            if inspected is None:
                return None

            file_descriptor = _open_read_target(directory_descriptor, final_name)
            try:
                opened_before = os.fstat(file_descriptor)
                _require_same_read_target(inspected, opened_before)
                payload = _read_bounded_payload(file_descriptor)
                opened_after = os.fstat(file_descriptor)
                _require_same_read_target(opened_before, opened_after)
            finally:
                _close_descriptor_best_effort(file_descriptor)
        except (CompletionError, ConfigurationError):
            raise
        except OSError:
            raise CompletionError(
                "failed to inspect or read lifecycle record."
            ) from None
        finally:
            _close_descriptor_best_effort(directory_descriptor)
        record = deserialize_isolated_commit_lifecycle_record(payload)
        if record.session_id != session_id:
            raise ConfigurationError(
                "lifecycle record session identifier does not match the requested session."
            )
        return record

    def _open_verified_directory(self) -> int:
        """Open and verify the configured directory on each operation."""

        descriptor = _open_directory_descriptor(self._directory, for_constructor=False)
        try:
            opened_status = os.fstat(descriptor)
        except OSError:
            _close_descriptor_best_effort(descriptor)
            raise CompletionError(
                "unable to inspect lifecycle store directory."
            ) from None

        if not _matches_verified_directory(
            opened_status,
            device=self._directory_device,
            inode=self._directory_inode,
        ):
            _close_descriptor_best_effort(descriptor)
            raise CompletionError("lifecycle store directory identity changed.")

        return descriptor


def _final_record_name(session_id: SessionId) -> str:
    """Build one deterministic opaque lifecycle record filename."""

    digest = hashlib.sha256(session_id.value.encode("utf-8")).hexdigest()
    return f"{_LIFECYCLE_PREFIX}{digest}{_LIFECYCLE_SUFFIX}"


def _open_directory_descriptor(directory: Path, *, for_constructor: bool) -> int:
    """Open one directory without following its final path component."""

    operation = "lifecycle store construction" if for_constructor else "lifecycle store"
    flags = os.O_RDONLY
    required_flags: list[str] = ["O_DIRECTORY", "O_NOFOLLOW"]
    for flag_name in required_flags:
        if not hasattr(os, flag_name):
            if for_constructor:
                raise ConfigurationError(
                    "required filesystem safety primitives are unavailable."
                )
            raise CompletionError(
                "required filesystem safety primitives are unavailable."
            )
        flags |= getattr(os, flag_name)
    try:
        return os.open(directory, flags)
    except OSError:
        if for_constructor:
            raise ConfigurationError(
                f"unable to safely open {operation} directory."
            ) from None
        raise CompletionError(
            "unable to safely open lifecycle store directory."
        ) from None


def _same_directory_identity(
    left: os.stat_result,
    right: os.stat_result,
) -> bool:
    """Return whether both stats reference the same opened directory."""

    return (
        stat.S_ISDIR(left.st_mode)
        and stat.S_ISDIR(right.st_mode)
        and left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
    )


def _matches_verified_directory(
    status: os.stat_result,
    *,
    device: int,
    inode: int,
) -> bool:
    """Return whether one opened descriptor still identifies the trusted directory."""

    return (
        stat.S_ISDIR(status.st_mode)
        and status.st_dev == device
        and status.st_ino == inode
    )


def _create_temporary_file(directory_descriptor: int) -> tuple[str, int]:
    """Create one collision-safe temporary file in the verified directory."""

    attempts = 0
    while attempts < _MAX_TEMP_NAME_ATTEMPTS:
        attempts += 1
        candidate = f"{_TEMP_PREFIX}{secrets.token_hex(16)}{_TEMP_SUFFIX}"
        try:
            descriptor = os.open(
                candidate,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=directory_descriptor,
            )
            return candidate, descriptor
        except FileExistsError:
            continue
        except OSError:
            raise CompletionError(
                "failed to create lifecycle temporary file."
            ) from None
    raise CompletionError("failed to create lifecycle temporary file.")


def _write_temporary_payload(
    temporary_descriptor: int,
    payload: bytes,
) -> None:
    """Write and fsync a complete serialized lifecycle payload."""

    descriptor: int | None = temporary_descriptor
    try:
        with os.fdopen(temporary_descriptor, "wb") as temporary:
            descriptor = None
            temporary.write(payload)
            temporary.flush()
            _fsync_descriptor(
                temporary.fileno(),
                "failed to fsync lifecycle temporary file.",
            )
    except OSError:
        raise CompletionError("failed to write lifecycle temporary file.") from None
    finally:
        if descriptor is not None:
            _close_descriptor_best_effort(descriptor)


def _require_replaceable_destination(
    directory_descriptor: int, final_name: str
) -> None:
    """Allow replacement only when the existing destination is a regular file."""

    try:
        status = os.stat(final_name, dir_fd=directory_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError:
        raise CompletionError("failed to inspect lifecycle destination file.") from None

    if not stat.S_ISREG(status.st_mode):
        raise CompletionError("lifecycle destination must be a regular file.")


def _replace_in_directory(
    directory_descriptor: int,
    source_name: str,
    destination_name: str,
) -> None:
    """Atomically replace one destination basename using one temporary basename."""

    try:
        os.replace(
            source_name,
            destination_name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
    except OSError:
        raise CompletionError("failed to replace lifecycle destination file.") from None


def _fsync_descriptor(descriptor: int, message: str) -> None:
    """Fsync one descriptor and raise one bounded operational failure."""

    try:
        os.fsync(descriptor)
    except OSError:
        raise CompletionError(message) from None


def _unlink_temporary_best_effort(
    directory_descriptor: int, temporary_name: str
) -> None:
    """Best-effort cleanup for one temporary file created by this write invocation."""

    try:
        os.unlink(temporary_name, dir_fd=directory_descriptor)
    except OSError:
        pass


def _inspect_read_target(
    directory_descriptor: int,
    final_name: str,
) -> os.stat_result | None:
    """Inspect one destination path without following symlinks."""

    try:
        status = os.stat(final_name, dir_fd=directory_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError:
        raise CompletionError("failed to inspect lifecycle record file.") from None

    if not stat.S_ISREG(status.st_mode):
        raise CompletionError("lifecycle record must be a regular file.")
    if status.st_size > MAX_LIFECYCLE_RECORD_BYTES:
        raise ConfigurationError(
            f"lifecycle record payload exceeds the {MAX_LIFECYCLE_RECORD_BYTES}-byte limit."
        )
    return status


def _open_read_target(directory_descriptor: int, final_name: str) -> int:
    """Open one destination basename without following the final symlink."""

    if not hasattr(os, "O_NOFOLLOW"):
        raise CompletionError("required filesystem safety primitives are unavailable.")

    flags = os.O_RDONLY | os.O_NOFOLLOW
    try:
        return os.open(final_name, flags, dir_fd=directory_descriptor)
    except OSError:
        raise CompletionError("failed to open lifecycle record file.") from None


def _require_same_read_target(
    expected: os.stat_result,
    actual: os.stat_result,
) -> None:
    """Require one consistent regular-file identity across read checks."""

    if not stat.S_ISREG(expected.st_mode) or not stat.S_ISREG(actual.st_mode):
        raise CompletionError("lifecycle record changed while reading.")
    if (expected.st_dev, expected.st_ino) != (actual.st_dev, actual.st_ino):
        raise CompletionError("lifecycle record changed while reading.")


def _read_bounded_payload(file_descriptor: int) -> bytes:
    """Read at most MAX+1 bytes from one opened record descriptor."""

    chunks: list[bytes] = []
    remaining = MAX_LIFECYCLE_RECORD_BYTES + 1
    while remaining > 0:
        try:
            chunk = os.read(file_descriptor, remaining)
        except OSError:
            raise CompletionError("failed to read lifecycle record file.") from None
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)

    payload = b"".join(chunks)
    if len(payload) > MAX_LIFECYCLE_RECORD_BYTES:
        raise ConfigurationError(
            f"lifecycle record payload exceeds the {MAX_LIFECYCLE_RECORD_BYTES}-byte limit."
        )
    return payload


def _close_descriptor_best_effort(descriptor: int) -> None:
    """Close one cleanup-only descriptor without masking a primary outcome."""

    try:
        os.close(descriptor)
    except OSError:
        pass
