"""Approved fixed Ruff and pytest execution inside an authorized workspace."""

import importlib.util
import os
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from agent_workbench.tool_registry import ToolRegistry
from agent_workbench.tools import JSONObject, ToolDefinition
from agent_workbench.workspace import Workspace

RUFF_TIMEOUT_SECONDS = 30
"""Maximum duration for one fixed Ruff command."""

PYTEST_TIMEOUT_SECONDS = 120
"""Maximum duration for one fixed pytest command."""

MAX_VALIDATION_OUTPUT_BYTES = 100 * 1024
"""Maximum captured bytes returned independently for stdout and stderr."""

_PATH_SCHEMA: JSONObject = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "default": ".",
        }
    },
    "additionalProperties": False,
}

RUN_RUFF_FORMAT_DEFINITION = ToolDefinition(
    name="run_ruff_format",
    description="Run approved Ruff formatting inside the authorized workspace.",
    input_schema=_PATH_SCHEMA,
)

RUN_RUFF_CHECK_DEFINITION = ToolDefinition(
    name="run_ruff_check",
    description="Run approved Ruff static analysis inside the authorized workspace.",
    input_schema=_PATH_SCHEMA,
)

RUN_PYTEST_DEFINITION = ToolDefinition(
    name="run_pytest",
    description="Run approved pytest tests inside the authorized workspace.",
    input_schema=_PATH_SCHEMA,
)


@dataclass(frozen=True, slots=True)
class _ValidationSpec:
    """Define one fixed allowlisted validation command."""

    module: str
    arguments: tuple[str, ...]
    timeout_seconds: int
    may_modify_files: bool
    executes_project_code: bool


_VALIDATION_SPECS = {
    "run_ruff_format": _ValidationSpec(
        module="ruff",
        arguments=("format", "--no-cache", "--color", "never"),
        timeout_seconds=RUFF_TIMEOUT_SECONDS,
        may_modify_files=True,
        executes_project_code=False,
    ),
    "run_ruff_check": _ValidationSpec(
        module="ruff",
        arguments=("check", "--no-cache", "--color", "never"),
        timeout_seconds=RUFF_TIMEOUT_SECONDS,
        may_modify_files=False,
        executes_project_code=False,
    ),
    "run_pytest": _ValidationSpec(
        module="pytest",
        arguments=("-q", "--color=no", "-p", "no:cacheprovider"),
        timeout_seconds=PYTEST_TIMEOUT_SECONDS,
        may_modify_files=False,
        executes_project_code=True,
    ),
}

_ABSOLUTE_PATH_PATTERN = re.compile(r"(?<![\w.])/(?:[^\s\x00]+)")


def register_validation_tools(
    registry: ToolRegistry,
    workspace: Workspace,
) -> None:
    """Register fixed approved validation tools in deterministic order."""

    for definition in (
        RUN_RUFF_FORMAT_DEFINITION,
        RUN_RUFF_CHECK_DEFINITION,
        RUN_PYTEST_DEFINITION,
    ):
        registry.register(
            definition,
            lambda arguments, name=definition.name: run_validation(
                workspace,
                name,
                arguments,
            ),
            requires_approval=True,
            approval_preview=lambda arguments, name=definition.name: preview_validation(
                workspace,
                name,
                arguments,
            ),
        )


def preview_validation(
    workspace: Workspace,
    tool_name: str,
    arguments: object,
) -> JSONObject:
    """Return a deterministic preview without starting a process."""

    spec = _get_spec(tool_name)
    relative_path, display_target = _resolve_target(workspace, arguments)
    display_command = _display_command(spec, display_target)
    return {
        "tool": tool_name,
        "path": relative_path,
        "command": display_command,
        "cwd": ".",
        "timeout_seconds": spec.timeout_seconds,
        "may_modify_files": spec.may_modify_files,
        "executes_project_code": spec.executes_project_code,
    }


def run_validation(
    workspace: Workspace,
    tool_name: str,
    arguments: object,
) -> JSONObject:
    """Execute one fixed validation command and return bounded safe output."""

    spec = _get_spec(tool_name)
    relative_path, display_target = _resolve_target(workspace, arguments)
    if not _module_available(spec.module):
        raise ValueError(f"{spec.module} module is unavailable.")

    command = [
        sys.executable,
        "-m",
        spec.module,
        *spec.arguments,
        display_target,
    ]
    (
        exit_code,
        stdout_bytes,
        stderr_bytes,
        stdout_truncated,
        stderr_truncated,
        duration_ms,
    ) = _run_process(
        command,
        workspace.root,
        _minimal_environment(),
        spec.timeout_seconds,
    )
    stdout, stdout_was_limited = _decode_and_sanitize(
        stdout_bytes,
        workspace,
    )
    stderr, stderr_was_limited = _decode_and_sanitize(
        stderr_bytes,
        workspace,
    )

    return {
        "tool": tool_name,
        "path": relative_path,
        "command": _display_command(spec, display_target),
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": stdout_truncated or stdout_was_limited,
        "stderr_truncated": stderr_truncated or stderr_was_limited,
        "duration_ms": duration_ms,
    }


def _get_spec(tool_name: str) -> _ValidationSpec:
    """Return one fixed validation specification."""

    try:
        return _VALIDATION_SPECS[tool_name]
    except KeyError:
        raise ValueError("Unknown validation tool.") from None


def _resolve_target(
    workspace: Workspace,
    arguments: object,
) -> tuple[str, str]:
    """Resolve an existing regular file or directory into safe command syntax."""

    if not isinstance(arguments, dict) or set(arguments) - {"path"}:
        raise ValueError("validation tool requires an optional path string.")

    requested_path = arguments.get("path", ".")
    if not isinstance(requested_path, str):
        raise ValueError("validation tool requires an optional path string.")

    canonical_path = workspace.resolve(Path(requested_path))
    if not canonical_path.is_file() and not canonical_path.is_dir():
        raise ValueError("validation target must be a regular file or directory.")

    relative = canonical_path.relative_to(workspace.root)
    if ".git" in relative.parts:
        raise ValueError("validation tools cannot target .git paths.")

    relative_path = "." if relative == Path(".") else relative.as_posix()
    display_target = (
        f"./{relative_path}"
        if relative_path != "." and relative.parts[0].startswith("-")
        else relative_path
    )
    return relative_path, display_target


def _display_command(
    spec: _ValidationSpec,
    display_target: str,
) -> list[str]:
    """Return safe portable display tokens without a host interpreter path."""

    return [
        "python",
        "-m",
        spec.module,
        *spec.arguments,
        display_target,
    ]


def _minimal_environment() -> dict[str, str]:
    """Return a fixed offline environment without parent-process secrets."""

    return {
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
        "NO_COLOR": "1",
        "PATH": "/usr/bin:/bin",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INDEX": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "UV_OFFLINE": "1",
    }


def _module_available(module: str) -> bool:
    """Return whether an allowlisted module is installed locally."""

    return importlib.util.find_spec(module) is not None


def _run_process(
    command: list[str],
    cwd: Path,
    environment: dict[str, str],
    timeout_seconds: int,
) -> tuple[int, bytes, bytes, bool, bool, int]:
    """Run a fixed command with bounded streaming output and group timeout."""

    started_at = time.monotonic()
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except FileNotFoundError:
        raise ValueError("Python interpreter is unavailable.") from None
    except OSError:
        raise ValueError("Unable to start validation command.") from None

    if process.stdout is None or process.stderr is None:
        _terminate_process_group(process)
        raise ValueError("Unable to capture validation output.")

    stdout_capture = _BoundedCapture()
    stderr_capture = _BoundedCapture()
    stdout_thread = threading.Thread(
        target=stdout_capture.read,
        args=(process.stdout,),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=stderr_capture.read,
        args=(process.stderr,),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    timed_out = False
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_process_group(process)

    stdout_thread.join(timeout=1)
    stderr_thread.join(timeout=1)
    if stdout_thread.is_alive() or stderr_thread.is_alive():
        _terminate_process_group(process)
        process.stdout.close()
        process.stderr.close()
        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)

    if timed_out:
        raise ValueError("Validation command timed out.")
    if stdout_capture.failure is not None or stderr_capture.failure is not None:
        raise ValueError("Unable to capture validation output.")
    if stdout_thread.is_alive() or stderr_thread.is_alive():
        raise ValueError("Unable to capture validation output.")

    duration_ms = max(0, int((time.monotonic() - started_at) * 1000))
    return (
        process.returncode,
        bytes(stdout_capture.content),
        bytes(stderr_capture.content),
        stdout_capture.truncated,
        stderr_capture.truncated,
        duration_ms,
    )


@dataclass(slots=True)
class _BoundedCapture:
    """Drain one process stream while retaining only a fixed byte prefix."""

    content: bytearray = field(default_factory=bytearray)
    truncated: bool = False
    failure: Exception | None = None

    def read(self, stream) -> None:
        """Drain the stream without accumulating beyond the configured limit."""

        try:
            while chunk := stream.read(8192):
                remaining = MAX_VALIDATION_OUTPUT_BYTES - len(self.content)
                if remaining > 0:
                    self.content.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    self.truncated = True
        except Exception as error:
            self.failure = error
        finally:
            try:
                stream.close()
            except Exception:
                pass


def _terminate_process_group(process) -> None:
    """Terminate and reap one isolated validation process group."""

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass

    try:
        process.wait(timeout=1)
        return
    except subprocess.TimeoutExpired:
        pass

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass

    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        raise ValueError("Unable to terminate validation command.") from None


def _decode_and_sanitize(
    content: bytes,
    workspace: Workspace,
) -> tuple[str, bool]:
    """Decode output safely, redact host paths, and enforce the byte limit."""

    decoded = content.decode("utf-8", errors="replace")
    sanitized = decoded.replace(str(workspace.root), ".")
    sanitized = _ABSOLUTE_PATH_PATTERN.sub("[absolute-path]", sanitized)
    encoded = sanitized.encode("utf-8")
    if len(encoded) <= MAX_VALIDATION_OUTPUT_BYTES:
        return sanitized, False

    limited = encoded[:MAX_VALIDATION_OUTPUT_BYTES].decode(
        "utf-8",
        errors="ignore",
    )
    return limited, True
