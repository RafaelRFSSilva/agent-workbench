"""Tests for approved allowlisted workspace validation tools."""

import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from agent_workbench.errors import CompletionError, WorkspacePathError
from agent_workbench.messages import ChatRequest, ChatResponse
from agent_workbench.tool_calling import run_tool_calling_loop
from agent_workbench.tool_registry import ToolRegistry
from agent_workbench.tools import (
    ToolApprovalDecision,
    ToolDefinition,
    ToolInvocation,
)
from agent_workbench.validation_tools import (
    MAX_VALIDATION_OUTPUT_BYTES,
    PYTEST_TIMEOUT_SECONDS,
    RUFF_TIMEOUT_SECONDS,
    RUN_PYTEST_DEFINITION,
    RUN_RUFF_CHECK_DEFINITION,
    RUN_RUFF_FORMAT_DEFINITION,
    _minimal_environment,
    _run_process,
    preview_validation,
    register_validation_tools,
    run_validation,
)
from agent_workbench.workspace import Workspace


def create_workspace(tmp_path: Path) -> tuple[Path, Workspace]:
    """Create one authorized temporary workspace."""

    root = tmp_path / "workspace"
    root.mkdir()
    return root, Workspace(root)


def existing_definition() -> ToolDefinition:
    """Create one definition that must keep its registration position."""

    return ToolDefinition(
        name="existing",
        description="Existing tool.",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    )


def test_registers_exact_approved_definitions_in_order(tmp_path: Path) -> None:
    """Append exactly the three closed-schema validation tools."""

    _, workspace = create_workspace(tmp_path)
    registry = ToolRegistry()
    existing = existing_definition()
    registry.register(existing, lambda arguments: None)

    register_validation_tools(registry, workspace)

    assert registry.definitions == (
        existing,
        RUN_RUFF_FORMAT_DEFINITION,
        RUN_RUFF_CHECK_DEFINITION,
        RUN_PYTEST_DEFINITION,
    )
    for definition in registry.definitions[1:]:
        assert definition.input_schema == {
            "type": "object",
            "properties": {
                "path": {"type": "string", "default": "."},
            },
            "additionalProperties": False,
        }
        assert registry.requires_approval(
            ToolInvocation(
                id=definition.name,
                tool_name=definition.name,
                arguments={},
            )
        )


@pytest.mark.parametrize(
    ("tool_name", "expected_command", "timeout", "modifies", "executes"),
    [
        (
            "run_ruff_format",
            ["python", "-m", "ruff", "format", "--no-cache", "--color", "never", "."],
            RUFF_TIMEOUT_SECONDS,
            True,
            False,
        ),
        (
            "run_ruff_check",
            ["python", "-m", "ruff", "check", "--no-cache", "--color", "never", "."],
            RUFF_TIMEOUT_SECONDS,
            False,
            False,
        ),
        (
            "run_pytest",
            [
                "python",
                "-m",
                "pytest",
                "-q",
                "--color=no",
                "-p",
                "no:cacheprovider",
                ".",
            ],
            PYTEST_TIMEOUT_SECONDS,
            False,
            True,
        ),
    ],
)
def test_previews_exact_fixed_commands_without_execution(
    tmp_path: Path,
    monkeypatch,
    tool_name,
    expected_command,
    timeout,
    modifies,
    executes,
) -> None:
    """Show the complete safe command boundary before approval."""

    _, workspace = create_workspace(tmp_path)
    popen = Mock(side_effect=AssertionError("preview must not execute"))
    monkeypatch.setattr("agent_workbench.validation_tools.subprocess.Popen", popen)

    preview = preview_validation(workspace, tool_name, {})

    assert preview == {
        "tool": tool_name,
        "path": ".",
        "command": expected_command,
        "cwd": ".",
        "timeout_seconds": timeout,
        "may_modify_files": modifies,
        "executes_project_code": executes,
    }
    popen.assert_not_called()


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        (".", "."),
        ("src", "src"),
        ("src/module.py", "src/module.py"),
        ("-option.py", "./-option.py"),
    ],
)
def test_targets_are_canonical_relative_and_option_safe(
    tmp_path: Path,
    requested: str,
    expected: str,
) -> None:
    """Resolve existing files or directories without producing command flags."""

    root, workspace = create_workspace(tmp_path)
    (root / "src").mkdir()
    (root / "src" / "module.py").write_text("value = 1\n", encoding="utf-8")
    (root / "-option.py").write_text("value = 1\n", encoding="utf-8")

    preview = preview_validation(
        workspace,
        "run_ruff_check",
        {"path": requested},
    )

    assert preview["path"] == ("-option.py" if requested == "-option.py" else expected)
    assert preview["command"][-1] == expected
    assert str(root) not in str(preview)


@pytest.mark.parametrize(
    "arguments",
    [
        {"path": "../outside.py"},
        {"path": "/tmp/outside.py"},
        {"path": "missing.py"},
        {"path": 1},
        {"path": ".", "flags": "--fix"},
        {"path": "\0"},
    ],
)
def test_rejects_unsafe_invalid_or_extended_arguments(
    tmp_path: Path,
    arguments,
) -> None:
    """Accept no paths outside Workspace and no caller-controlled options."""

    _, workspace = create_workspace(tmp_path)

    with pytest.raises((ValueError, WorkspacePathError)):
        preview_validation(workspace, "run_ruff_check", arguments)


def test_external_symlink_is_rejected_and_internal_target_is_canonical(
    tmp_path: Path,
) -> None:
    """Use the existing Workspace containment boundary for validation targets."""

    root, workspace = create_workspace(tmp_path)
    internal = root / "module.py"
    internal.write_text("value = 1\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("secret\n", encoding="utf-8")
    (root / "internal.py").symlink_to(internal)
    (root / "external.py").symlink_to(outside)

    assert (
        preview_validation(
            workspace,
            "run_ruff_check",
            {"path": "internal.py"},
        )["path"]
        == "module.py"
    )
    with pytest.raises(WorkspacePathError, match="outside"):
        preview_validation(
            workspace,
            "run_ruff_check",
            {"path": "external.py"},
        )


def test_minimal_environment_is_offline_and_excludes_parent_secrets(
    monkeypatch,
) -> None:
    """Never forward API keys, credentials, or arbitrary parent values."""

    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("ARBITRARY_PARENT_VALUE", "secret")

    environment = _minimal_environment()

    assert environment == {
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
    assert "OPENAI_API_KEY" not in environment
    assert "AWS_SECRET_ACCESS_KEY" not in environment


def test_real_ruff_format_and_check_are_fixed_and_scoped(tmp_path: Path) -> None:
    """Format only the target and return non-zero lint findings normally."""

    root, workspace = create_workspace(tmp_path)
    target = root / "module.py"
    other = root / "other.py"
    target.write_text("value=1\n", encoding="utf-8")
    other.write_text("other=2\n", encoding="utf-8")
    original_cwd = Path.cwd()

    formatted = run_validation(workspace, "run_ruff_format", {"path": "module.py"})
    checked = run_validation(workspace, "run_ruff_check", {"path": "."})

    assert formatted["exit_code"] == 0
    assert target.read_text(encoding="utf-8") == "value = 1\n"
    assert other.read_text(encoding="utf-8") == "other=2\n"
    assert checked["exit_code"] == 0
    assert Path.cwd() == original_cwd

    target.write_text("import os\n", encoding="utf-8")
    failure = run_validation(workspace, "run_ruff_check", {"path": "module.py"})
    assert failure["exit_code"] != 0
    assert "F401" in failure["stdout"]


def test_real_pytest_success_and_failure_are_normal_results(tmp_path: Path) -> None:
    """Execute project code only through the fixed pytest command."""

    root, workspace = create_workspace(tmp_path)
    test_file = root / "test_demo.py"
    test_file.write_text("def test_demo():\n    assert True\n", encoding="utf-8")

    success = run_validation(workspace, "run_pytest", {"path": "."})
    test_file.write_text("def test_demo():\n    assert False\n", encoding="utf-8")
    failure = run_validation(workspace, "run_pytest", {"path": "."})

    assert success["exit_code"] == 0
    assert "1 passed" in success["stdout"]
    assert failure["exit_code"] == 1
    assert "1 failed" in failure["stdout"]
    assert not (root / ".pytest_cache").exists()


def test_result_shape_sanitizes_paths_and_tracks_truncation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Return bounded independently truncated UTF-8 output and a duration."""

    root, workspace = create_workspace(tmp_path)
    monkeypatch.setattr(
        "agent_workbench.validation_tools._run_process",
        lambda command, cwd, environment, timeout: (
            7,
            (str(root) + "/module.py\n").encode()
            + b"x" * (MAX_VALIDATION_OUTPUT_BYTES + 1),
            b"/outside/private\n" + b"y" * (MAX_VALIDATION_OUTPUT_BYTES + 1),
            True,
            True,
            12,
        ),
    )

    result = run_validation(workspace, "run_ruff_check", {})

    assert result["tool"] == "run_ruff_check"
    assert result["path"] == "."
    assert result["command"][0] == "python"
    assert result["exit_code"] == 7
    assert len(result["stdout"].encode()) <= MAX_VALIDATION_OUTPUT_BYTES
    assert len(result["stderr"].encode()) <= MAX_VALIDATION_OUTPUT_BYTES
    assert result["stdout_truncated"] is True
    assert result["stderr_truncated"] is True
    assert result["duration_ms"] == 12
    assert str(root) not in result["stdout"]
    assert "/outside/private" not in result["stderr"]


class FakePipe:
    """Provide bounded bytes to the streaming capture threads."""

    def __init__(self, content: bytes) -> None:
        self._content = content
        self._read = False

    def read(self, size: int) -> bytes:
        """Return configured content once."""

        if self._read:
            return b""
        self._read = True
        return self._content

    def close(self) -> None:
        """Support process stream cleanup."""


class FakeProcess:
    """Record fixed Popen behavior for process-boundary tests."""

    pid = 4321
    returncode = 0

    def __init__(self, stdout: bytes = b"ok", stderr: bytes = b"") -> None:
        self.stdout = FakePipe(stdout)
        self.stderr = FakePipe(stderr)

    def wait(self, timeout=None):
        """Complete immediately."""

        return self.returncode


def test_process_boundary_uses_no_shell_fixed_cwd_and_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Start the exact command without stdin, shell, or inherited secrets."""

    root, _ = create_workspace(tmp_path)
    process = FakeProcess()
    popen = Mock(return_value=process)
    monkeypatch.setattr("agent_workbench.validation_tools.subprocess.Popen", popen)

    result = _run_process(
        ["/fixed/python", "-m", "ruff", "check", "."],
        root,
        {"SAFE": "1"},
        30,
    )

    assert result[0] == 0
    popen.assert_called_once_with(
        ["/fixed/python", "-m", "ruff", "check", "."],
        cwd=root,
        env={"SAFE": "1"},
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def test_timeout_terminates_process_group_safely(tmp_path: Path, monkeypatch) -> None:
    """Terminate and reap a timed-out validation process."""

    root, _ = create_workspace(tmp_path)
    process = FakeProcess()
    waits = iter(
        [
            subprocess.TimeoutExpired(["python"], 30),
            0,
        ]
    )

    def wait(timeout=None):
        outcome = next(waits)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    process.wait = Mock(side_effect=wait)
    monkeypatch.setattr(
        "agent_workbench.validation_tools.subprocess.Popen",
        Mock(return_value=process),
    )
    killpg = Mock()
    monkeypatch.setattr("agent_workbench.validation_tools.os.killpg", killpg)

    with pytest.raises(ValueError, match="timed out"):
        _run_process(["python"], root, {}, 30)

    assert killpg.called
    assert process.wait.call_count >= 2


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (FileNotFoundError(), "interpreter is unavailable"),
        (OSError(), "Unable to start"),
    ],
)
def test_process_start_failures_are_safe(
    tmp_path: Path,
    monkeypatch,
    failure,
    message,
) -> None:
    """Convert process-start failures without exposing exception details."""

    root, _ = create_workspace(tmp_path)
    monkeypatch.setattr(
        "agent_workbench.validation_tools.subprocess.Popen",
        Mock(side_effect=failure),
    )

    with pytest.raises(ValueError, match=message):
        _run_process(["python"], root, {}, 30)


def test_missing_module_is_rejected_before_process_start(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Fail safely if an installed allowlisted module becomes unavailable."""

    _, workspace = create_workspace(tmp_path)
    popen = Mock()
    monkeypatch.setattr(
        "agent_workbench.validation_tools._module_available", lambda _: False
    )
    monkeypatch.setattr("agent_workbench.validation_tools.subprocess.Popen", popen)

    with pytest.raises(ValueError, match="module is unavailable"):
        run_validation(workspace, "run_ruff_check", {})

    popen.assert_not_called()


@pytest.mark.parametrize("decision", [None, ToolApprovalDecision.DENY])
def test_absent_or_denied_approval_starts_no_process(
    tmp_path: Path,
    monkeypatch,
    decision,
) -> None:
    """Keep every validation command behind exact caller approval."""

    _, workspace = create_workspace(tmp_path)
    registry = ToolRegistry()
    register_validation_tools(registry, workspace)
    popen = Mock(side_effect=AssertionError("must not execute"))
    monkeypatch.setattr("agent_workbench.validation_tools.subprocess.Popen", popen)
    provider = iter(
        [
            ChatResponse(
                tool_invocations=(
                    ToolInvocation(
                        id="validation",
                        tool_name="run_ruff_check",
                        arguments={},
                    ),
                )
            )
        ]
    )

    class Provider:
        name = "Fake"
        model_name = "fake"

        def complete(self, request):
            return next(provider)

    handler = None if decision is None else lambda request: decision
    with pytest.raises(CompletionError):
        run_tool_calling_loop(
            Provider(),
            ChatRequest(messages=[]),
            registry,
            max_tool_rounds=1,
            tool_approval_handler=handler,
        )

    popen.assert_not_called()


def test_calls_do_not_mutate_arguments_or_registry_state(tmp_path: Path) -> None:
    """Keep caller inputs and separate registries independent."""

    root, workspace = create_workspace(tmp_path)
    (root / "module.py").write_text("value = 1\n", encoding="utf-8")
    arguments = {"path": "module.py"}
    first = ToolRegistry()
    second = ToolRegistry()
    register_validation_tools(first, workspace)

    first_result = run_validation(workspace, "run_ruff_check", arguments)
    first_result["path"] = "changed.py"
    second_result = run_validation(workspace, "run_ruff_check", arguments)

    assert arguments == {"path": "module.py"}
    assert second_result["path"] == "module.py"
    assert second.definitions == ()
