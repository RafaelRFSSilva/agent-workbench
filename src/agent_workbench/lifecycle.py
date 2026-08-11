"""Immutable lifecycle record for one isolated commit's intent and checkpoint state."""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
import json
from pathlib import PurePosixPath, PureWindowsPath
import re

from agent_workbench.errors import ConfigurationError
from agent_workbench.session import SessionId

ISOLATED_COMMIT_LIFECYCLE_SCHEMA_VERSION = 1
"""Schema version embedded in every serialized isolated commit lifecycle record."""

MAX_LIFECYCLE_RECORD_BYTES = 16 * 1024
"""Maximum encoded lifecycle-record size; appropriate for metadata only."""

_GIT_OBJECT_ID_PATTERN = re.compile(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}")
_SHA256_HEX_PATTERN = re.compile(r"[0-9a-f]{64}")
_LIFECYCLE_KIND = "isolated_commit_lifecycle"

_SCHEMA_KEYS = frozenset(
    {
        "branch_name",
        "commit_message_fingerprint",
        "diff_fingerprint",
        "kind",
        "new_head",
        "old_head",
        "paths",
        "phase",
        "schema_version",
        "session_id",
        "source_branch",
        "source_head",
        "target_display",
    }
)


class IsolatedCommitLifecyclePhase(StrEnum):
    """Identify the lifecycle phase of one isolated commit."""

    PLANNED = "planned"
    EXECUTION_STARTED = "execution_started"
    VERIFIED = "verified"


@dataclass(frozen=True, slots=True, init=False)
class IsolatedCommitLifecycleRecord:
    """Store the immutable intent and checkpoint state of one isolated commit."""

    session_id: SessionId
    phase: IsolatedCommitLifecyclePhase
    target_display: str
    source_head: str
    source_branch: str
    branch_name: str
    old_head: str
    paths: tuple[str, ...]
    diff_fingerprint: str
    commit_message_fingerprint: str
    new_head: str | None

    def __init__(
        self,
        *,
        session_id: SessionId,
        phase: IsolatedCommitLifecyclePhase,
        target_display: str,
        source_head: str,
        source_branch: str,
        branch_name: str,
        old_head: str,
        paths: Iterable[str],
        diff_fingerprint: str,
        commit_message_fingerprint: str,
        new_head: str | None,
    ) -> None:
        """Validate and snapshot one isolated commit lifecycle record."""

        if not isinstance(session_id, SessionId):
            raise ConfigurationError("lifecycle session_id must be a SessionId.")
        if not isinstance(phase, IsolatedCommitLifecyclePhase):
            raise ConfigurationError(
                "lifecycle phase must be an IsolatedCommitLifecyclePhase."
            )

        safe_target = _validate_target_display(target_display)
        safe_source_branch = _validate_branch(source_branch, field_name="source_branch")
        safe_branch_name = _validate_branch(branch_name, field_name="branch_name")
        safe_source_head = _validate_object_id(source_head, field_name="source_head")
        safe_old_head = _validate_object_id(old_head, field_name="old_head")
        safe_paths = _snapshot_paths(paths)
        safe_diff_fp = _validate_sha256_hex(
            diff_fingerprint, field_name="diff_fingerprint"
        )
        safe_cmf = _validate_sha256_hex(
            commit_message_fingerprint, field_name="commit_message_fingerprint"
        )
        safe_new_head = _validate_new_head(new_head, phase, old_head=safe_old_head)

        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "phase", phase)
        object.__setattr__(self, "target_display", safe_target)
        object.__setattr__(self, "source_head", safe_source_head)
        object.__setattr__(self, "source_branch", safe_source_branch)
        object.__setattr__(self, "branch_name", safe_branch_name)
        object.__setattr__(self, "old_head", safe_old_head)
        object.__setattr__(self, "paths", safe_paths)
        object.__setattr__(self, "diff_fingerprint", safe_diff_fp)
        object.__setattr__(self, "commit_message_fingerprint", safe_cmf)
        object.__setattr__(self, "new_head", safe_new_head)


def serialize_isolated_commit_lifecycle_record(
    record: IsolatedCommitLifecycleRecord,
) -> bytes:
    """Serialize one lifecycle record to canonical deterministic UTF-8 JSON bytes."""

    if not isinstance(record, IsolatedCommitLifecycleRecord):
        raise ConfigurationError(
            "lifecycle record serialization requires an IsolatedCommitLifecycleRecord."
        )

    data = {
        "branch_name": record.branch_name,
        "commit_message_fingerprint": record.commit_message_fingerprint,
        "diff_fingerprint": record.diff_fingerprint,
        "kind": _LIFECYCLE_KIND,
        "new_head": record.new_head,
        "old_head": record.old_head,
        "paths": list(record.paths),
        "phase": str(record.phase),
        "schema_version": ISOLATED_COMMIT_LIFECYCLE_SCHEMA_VERSION,
        "session_id": record.session_id.value,
        "source_branch": record.source_branch,
        "source_head": record.source_head,
        "target_display": record.target_display,
    }
    encoded = json.dumps(
        data,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    payload = encoded + b"\n"
    if len(payload) > MAX_LIFECYCLE_RECORD_BYTES:
        raise ConfigurationError(
            f"lifecycle record exceeds the {MAX_LIFECYCLE_RECORD_BYTES}-byte serialization limit."
        )
    return payload


def deserialize_isolated_commit_lifecycle_record(
    payload: object,
) -> IsolatedCommitLifecycleRecord:
    """Parse and validate a serialized lifecycle record from UTF-8 JSON bytes."""

    if not isinstance(payload, bytes):
        raise ConfigurationError(
            "lifecycle record deserialization requires bytes input."
        )
    if len(payload) > MAX_LIFECYCLE_RECORD_BYTES:
        raise ConfigurationError(
            f"lifecycle record payload exceeds the {MAX_LIFECYCLE_RECORD_BYTES}-byte limit."
        )

    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        raise ConfigurationError(
            "lifecycle record payload is not valid UTF-8."
        ) from None

    try:
        parsed = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except ConfigurationError:
        raise
    except json.JSONDecodeError:
        raise ConfigurationError(
            "lifecycle record payload is not valid JSON."
        ) from None

    if not isinstance(parsed, dict):
        raise ConfigurationError("lifecycle record payload must be a JSON object.")

    present_keys = set(parsed.keys())
    missing = _SCHEMA_KEYS - present_keys
    unknown = present_keys - _SCHEMA_KEYS
    if missing:
        raise ConfigurationError(
            f"lifecycle record is missing required keys: {sorted(missing)}."
        )
    if unknown:
        raise ConfigurationError(
            f"lifecycle record contains unknown keys: {sorted(unknown)}."
        )

    schema_version = parsed["schema_version"]
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise ConfigurationError(
            f"lifecycle record schema_version must be an integer: {schema_version!r}."
        )
    if schema_version != ISOLATED_COMMIT_LIFECYCLE_SCHEMA_VERSION:
        raise ConfigurationError(
            f"lifecycle record has unsupported schema version: {schema_version!r}."
        )

    kind = parsed["kind"]
    if not isinstance(kind, str):
        raise ConfigurationError(f"lifecycle record kind must be a string: {kind!r}.")
    if kind != _LIFECYCLE_KIND:
        raise ConfigurationError(f"lifecycle record has unexpected kind: {kind!r}.")

    session_id_value = parsed["session_id"]
    if not isinstance(session_id_value, str):
        raise ConfigurationError("lifecycle record session_id must be a string.")
    try:
        session_id = SessionId(value=session_id_value)
    except ConfigurationError:
        raise ConfigurationError("lifecycle record session_id is invalid.") from None

    phase_value = parsed["phase"]
    if not isinstance(phase_value, str):
        raise ConfigurationError(
            f"lifecycle record phase must be a string: {phase_value!r}."
        )
    try:
        phase = IsolatedCommitLifecyclePhase(phase_value)
    except (ValueError, KeyError):
        raise ConfigurationError(
            f"lifecycle record phase is invalid: {phase_value!r}."
        ) from None

    for field_name in (
        "target_display",
        "source_head",
        "source_branch",
        "branch_name",
        "old_head",
        "diff_fingerprint",
        "commit_message_fingerprint",
    ):
        if not isinstance(parsed[field_name], str):
            raise ConfigurationError(f"lifecycle record {field_name} must be a string.")

    paths_raw = parsed["paths"]
    if not isinstance(paths_raw, list):
        raise ConfigurationError("lifecycle record paths must be a JSON array.")
    for path in paths_raw:
        if not isinstance(path, str):
            raise ConfigurationError("each lifecycle record path must be a string.")

    new_head_raw = parsed["new_head"]
    if new_head_raw is not None and not isinstance(new_head_raw, str):
        raise ConfigurationError("lifecycle record new_head must be a string or null.")

    return IsolatedCommitLifecycleRecord(
        session_id=session_id,
        phase=phase,
        target_display=parsed["target_display"],
        source_head=parsed["source_head"],
        source_branch=parsed["source_branch"],
        branch_name=parsed["branch_name"],
        old_head=parsed["old_head"],
        paths=paths_raw,
        diff_fingerprint=parsed["diff_fingerprint"],
        commit_message_fingerprint=parsed["commit_message_fingerprint"],
        new_head=new_head_raw,
    )


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    """Reject JSON objects containing duplicate keys."""

    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ConfigurationError(
                f"lifecycle record contains duplicate JSON key: {key!r}."
            )
        result[key] = value
    return result


def _validate_target_display(value: object) -> str:
    """Require one safe relative single-line worktree display value."""

    if (
        not isinstance(value, str)
        or not value.strip()
        or "\0" in value
        or "\n" in value
        or "\r" in value
        or PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
    ):
        raise ConfigurationError(
            "lifecycle target_display must be a safe relative single-line string."
        )
    return value


def _validate_branch(value: object, *, field_name: str) -> str:
    """Require one exact non-blank single-line branch identity."""

    if (
        not isinstance(value, str)
        or not value.strip()
        or "\0" in value
        or "\n" in value
        or "\r" in value
    ):
        raise ConfigurationError(
            f"lifecycle {field_name} must be a non-blank single-line string."
        )
    return value


def _validate_object_id(value: object, *, field_name: str) -> str:
    """Require one complete SHA-1 or SHA-256 Git object identifier."""

    if not isinstance(value, str) or _GIT_OBJECT_ID_PATTERN.fullmatch(value) is None:
        raise ConfigurationError(
            f"lifecycle {field_name} must be a complete Git object identifier."
        )
    return value


def _validate_sha256_hex(value: object, *, field_name: str) -> str:
    """Require one lowercase SHA-256 hexadecimal digest."""

    if not isinstance(value, str) or _SHA256_HEX_PATTERN.fullmatch(value) is None:
        raise ConfigurationError(
            f"lifecycle {field_name} must be a lowercase SHA-256 hexadecimal digest."
        )
    return value


def _snapshot_paths(paths: object) -> tuple[str, ...]:
    """Validate and snapshot canonical portable repository-relative paths."""

    if isinstance(paths, str):
        raise ConfigurationError(
            "lifecycle paths must be an iterable of path strings, not a bare string."
        )

    try:
        path_tuple = tuple(paths)  # type: ignore[call-overload]
    except TypeError as exc:
        raise ConfigurationError("lifecycle paths must be an iterable.") from exc

    if not path_tuple:
        raise ConfigurationError("lifecycle paths must contain at least one path.")

    seen: set[str] = set()
    for path in path_tuple:
        if not isinstance(path, str) or not path.strip():
            raise ConfigurationError("each lifecycle path must be a non-blank string.")
        if "\0" in path or "\n" in path or "\r" in path or "\\" in path:
            raise ConfigurationError(
                "each lifecycle path must be a canonical portable relative path."
            )
        pure = PurePosixPath(path)
        if (
            pure.is_absolute()
            or str(pure) != path
            or path in {".", ".."}
            or ".." in pure.parts
        ):
            raise ConfigurationError(
                "each lifecycle path must be a canonical portable relative path."
            )
        if path in seen:
            raise ConfigurationError(
                "lifecycle paths cannot contain duplicate entries."
            )
        seen.add(path)

    return path_tuple


def _validate_new_head(
    value: object,
    phase: IsolatedCommitLifecyclePhase,
    *,
    old_head: str,
) -> str | None:
    """Validate new_head according to the phase invariants."""

    if phase in (
        IsolatedCommitLifecyclePhase.PLANNED,
        IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
    ):
        if value is not None:
            raise ConfigurationError(
                f"lifecycle new_head must be None for phase {str(phase)!r}."
            )
        return None

    # VERIFIED phase requires a valid OID that differs from old_head.
    if not isinstance(value, str) or _GIT_OBJECT_ID_PATTERN.fullmatch(value) is None:
        raise ConfigurationError(
            "lifecycle new_head must be a complete Git object identifier "
            "for phase 'verified'."
        )
    if value == old_head:
        raise ConfigurationError(
            "lifecycle new_head must differ from old_head for phase 'verified'."
        )
    return value
