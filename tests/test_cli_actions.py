"""Tests for controlled-action approval in the CLI presentation layer."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from agent_workbench.cli import (
    _display_tool_round,
    _prompt_for_tool_approval,
    main,
    run_cli,
)
from agent_workbench.arguments import RuntimeConfiguration
from agent_workbench.messages import ChatRequest, ChatResponse, ToolInteractionRound
from agent_workbench.session import AgentSession, SessionId
from agent_workbench.tool_registry import ToolRegistry
from agent_workbench.tools import (
    ToolApprovalDecision,
    ToolApprovalRequest,
    ToolInvocation,
    ToolResult,
)
from agent_workbench.validation_tools import register_validation_tools
from agent_workbench.workspace import Workspace
from agent_workbench.workspace_actions import register_workspace_action_tools


class FakeProvider:
    """Return deterministic provider responses and retain requests."""

    name = "Fake"
    model_name = "fake-model"

    def __init__(self, responses: list[ChatResponse]) -> None:
        self._responses = iter(responses)
        self.requests: list[ChatRequest] = []

    def complete(self, request: ChatRequest) -> ChatResponse:
        """Return the next configured response."""

        self.requests.append(request)
        return next(self._responses)


def approval_request(
    tool_name: str,
    preview: dict[str, object],
) -> ToolApprovalRequest:
    """Create one CLI approval request."""

    return ToolApprovalRequest(
        ToolInvocation(id="action", tool_name=tool_name, arguments={}),
        preview,
    )


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("y", ToolApprovalDecision.APPROVE),
        ("YES", ToolApprovalDecision.APPROVE),
        ("", ToolApprovalDecision.DENY),
        ("sure", ToolApprovalDecision.DENY),
    ],
)
def test_cli_approval_is_explicit_one_prompt_and_default_deny(
    monkeypatch,
    capsys,
    answer,
    expected,
) -> None:
    """Approve only yes values after displaying the complete patch preview."""

    prompts = []
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: prompts.append(prompt) or answer,
    )
    request = approval_request(
        "apply_file_patch",
        {
            "path": "module.py",
            "operation": "update",
            "old_size_bytes": 4,
            "new_size_bytes": 4,
            "changed_lines": 2,
            "diff": "--- a/module.py\n+++ b/module.py\n-old\n+new\n",
        },
    )

    decision = _prompt_for_tool_approval(request)

    output = capsys.readouterr().out
    assert decision is expected
    assert prompts == ["Approve action? [y/N]: "]
    assert "Action approval required: apply_file_patch" in output
    assert "module.py" in output
    assert "--- a/module.py" in output
    assert "+new" in output


@pytest.mark.parametrize("failure", [EOFError(), KeyboardInterrupt()])
def test_cli_approval_input_interruption_denies(
    monkeypatch,
    failure,
) -> None:
    """Treat unavailable or interrupted approval input as denial."""

    monkeypatch.setattr("builtins.input", Mock(side_effect=failure))

    decision = _prompt_for_tool_approval(
        approval_request(
            "run_ruff_check",
            {
                "tool": "run_ruff_check",
                "path": ".",
                "command": ["python", "-m", "ruff", "check", "."],
                "cwd": ".",
                "timeout_seconds": 30,
                "may_modify_files": False,
                "executes_project_code": False,
            },
        )
    )

    assert decision is ToolApprovalDecision.DENY


@pytest.mark.parametrize(
    ("tool_name", "warning"),
    [
        ("run_ruff_format", "may modify files"),
        ("run_ruff_check", "static analysis"),
        ("run_pytest", "executes project code"),
    ],
)
def test_cli_distinguishes_validation_previews(
    monkeypatch,
    capsys,
    tool_name,
    warning,
) -> None:
    """Explain the distinct fixed command before requesting approval."""

    monkeypatch.setattr("builtins.input", lambda _: "no")
    _prompt_for_tool_approval(
        approval_request(
            tool_name,
            {
                "tool": tool_name,
                "path": ".",
                "command": ["python", "-m", tool_name, "."],
                "cwd": ".",
                "timeout_seconds": 30,
                "may_modify_files": tool_name == "run_ruff_format",
                "executes_project_code": tool_name == "run_pytest",
            },
        )
    )

    output = capsys.readouterr().out.lower()
    assert tool_name in output
    assert warning in output


def action_registry(workspace: Workspace) -> ToolRegistry:
    """Create the Stage 4 controlled-action registry."""

    registry = ToolRegistry()
    register_workspace_action_tools(registry, workspace)
    register_validation_tools(registry, workspace)
    return registry


def test_cli_denial_rolls_back_and_a_later_turn_succeeds(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    """Continue the CLI after default-deny without retaining the failed turn."""

    target = tmp_path / "module.py"
    target.write_text("old\n", encoding="utf-8")
    provider = FakeProvider(
        [
            ChatResponse(
                tool_invocations=(
                    ToolInvocation(
                        id="patch",
                        tool_name="apply_file_patch",
                        arguments={
                            "path": "module.py",
                            "expected_content": "old\n",
                            "replacement_content": "new\n",
                        },
                    ),
                )
            ),
            ChatResponse(text="Recovered."),
        ]
    )
    session = AgentSession(
        id=SessionId("cli"),
        provider=provider,
        tool_registry=action_registry(Workspace(tmp_path)),
    )
    inputs = iter(["Change it.", "", "Continue.", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))

    run_cli(session, enable_actions=True)

    output = capsys.readouterr().out
    assert "approval was denied" in output
    assert "Assistant: Recovered." in output
    assert target.read_text(encoding="utf-8") == "old\n"
    assert provider.requests[1].messages == [{"role": "user", "content": "Continue."}]


def test_cli_invalid_action_preview_is_safe_and_later_turn_succeeds(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    """Render safe validation failures without a traceback or failed-turn history."""

    provider = FakeProvider(
        [
            ChatResponse(
                tool_invocations=(
                    ToolInvocation(
                        id="patch",
                        tool_name="apply_file_patch",
                        arguments={
                            "path": "../outside.py",
                            "expected_content": "",
                            "replacement_content": "unsafe\n",
                            "create_if_missing": True,
                        },
                    ),
                )
            ),
            ChatResponse(text="Recovered."),
        ]
    )
    session = AgentSession(
        id=SessionId("cli"),
        provider=provider,
        tool_registry=action_registry(Workspace(tmp_path)),
    )
    inputs = iter(["Unsafe.", "Continue.", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))

    run_cli(session, enable_actions=True)

    output = capsys.readouterr().out
    assert "Error: apply_file_patch path must not contain traversal." in output
    assert "Traceback" not in output
    assert "Assistant: Recovered." in output
    assert provider.requests[1].messages == [{"role": "user", "content": "Continue."}]


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        (
            "apply_file_patch",
            {
                "path": "module.py",
                "expected_content": "value=1\n",
                "replacement_content": "value = 1\n",
            },
        ),
        ("run_ruff_format", {"path": "module.py"}),
        ("run_ruff_check", {"path": "."}),
        ("run_pytest", {"path": "."}),
    ],
)
def test_cli_approved_action_flows_reach_final_response(
    tmp_path: Path,
    monkeypatch,
    capsys,
    tool_name,
    arguments,
) -> None:
    """Forward one-use approval for every controlled action type."""

    (tmp_path / "module.py").write_text("value=1\n", encoding="utf-8")
    (tmp_path / "test_module.py").write_text(
        "def test_value():\n    assert True\n",
        encoding="utf-8",
    )
    provider = FakeProvider(
        [
            ChatResponse(
                tool_invocations=(
                    ToolInvocation(
                        id="action",
                        tool_name=tool_name,
                        arguments=arguments,
                    ),
                )
            ),
            ChatResponse(text="Validated."),
        ]
    )
    session = AgentSession(
        id=SessionId("cli"),
        provider=provider,
        tool_registry=action_registry(Workspace(tmp_path)),
    )
    inputs = iter(["Act.", "yes", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))

    run_cli(session, enable_actions=True)

    assert "Assistant: Validated." in capsys.readouterr().out
    result = provider.requests[1].tool_interactions[0].results[0]
    assert result.status == "success"
    if tool_name in {"run_ruff_check", "run_pytest"}:
        assert result.output["exit_code"] == 0


def test_cli_requests_new_approval_for_a_second_action(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Never cache a prior approval across tool rounds."""

    (tmp_path / "module.py").write_text("value=1\n", encoding="utf-8")
    provider = FakeProvider(
        [
            ChatResponse(
                tool_invocations=(
                    ToolInvocation(
                        id="format",
                        tool_name="run_ruff_format",
                        arguments={"path": "module.py"},
                    ),
                )
            ),
            ChatResponse(
                tool_invocations=(
                    ToolInvocation(
                        id="check",
                        tool_name="run_ruff_check",
                        arguments={"path": "."},
                    ),
                )
            ),
            ChatResponse(text="Done."),
        ]
    )
    session = AgentSession(
        id=SessionId("cli"),
        provider=provider,
        tool_registry=action_registry(Workspace(tmp_path)),
    )
    prompts = []
    answers = iter(["Act.", "y", "y", "/exit"])

    def answer(prompt):
        value = next(answers)
        if prompt.startswith("Approve"):
            prompts.append(prompt)
        return value

    monkeypatch.setattr("builtins.input", answer)

    run_cli(session, enable_actions=True)

    assert prompts == [
        "Approve action? [y/N]: ",
        "Approve action? [y/N]: ",
    ]


def test_cli_returns_failed_pytest_exit_to_model_as_normal_result(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    """Let the model diagnose a completed test command with failing tests."""

    (tmp_path / "test_failure.py").write_text(
        "def test_failure():\n    assert False\n",
        encoding="utf-8",
    )
    provider = FakeProvider(
        [
            ChatResponse(
                tool_invocations=(
                    ToolInvocation(
                        id="pytest",
                        tool_name="run_pytest",
                        arguments={"path": "."},
                    ),
                )
            ),
            ChatResponse(text="Tests failed; I will diagnose them."),
        ]
    )
    session = AgentSession(
        id=SessionId("cli"),
        provider=provider,
        tool_registry=action_registry(Workspace(tmp_path)),
    )
    inputs = iter(["Test.", "y", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))

    run_cli(session, enable_actions=True)

    result = provider.requests[1].tool_interactions[0].results[0]
    assert result.status == "success"
    assert result.output["exit_code"] == 1
    assert "Assistant: Tests failed; I will diagnose them." in capsys.readouterr().out


def test_patch_trace_redacts_contents_but_keeps_safe_metadata(capsys) -> None:
    """Keep complete content only in the approval diff, never normal traces."""

    invocation = ToolInvocation(
        id="patch",
        tool_name="apply_file_patch",
        arguments={
            "path": "module.py",
            "expected_content": "SECRET-OLD",
            "replacement_content": "SECRET-NEW",
            "create_if_missing": False,
        },
    )
    round_ = ToolInteractionRound(
        response=ChatResponse(tool_invocations=(invocation,)),
        results=(
            ToolResult(
                invocation_id="patch",
                status="success",
                output={
                    "path": "module.py",
                    "operation": "update",
                    "old_size_bytes": 10,
                    "new_size_bytes": 10,
                    "changed_lines": 2,
                },
            ),
        ),
    )

    _display_tool_round(round_)

    output = capsys.readouterr().out
    assert "SECRET-OLD" not in output
    assert "SECRET-NEW" not in output
    assert '"path":"module.py"' in output
    assert '"expected_content_bytes":10' in output
    assert '"replacement_content_bytes":10' in output


def test_main_forwards_action_mode_only_when_enabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Keep the factory free of prompting and authorize the CLI explicitly."""

    configuration = RuntimeConfiguration(
        provider_name="ollama",
        model_name="test-model",
        workspace_root=tmp_path,
        enable_actions=True,
    )
    session = Mock()
    session.tool_registry = Mock()
    run_cli_mock = Mock()
    monkeypatch.setattr("agent_workbench.cli.load_environment", Mock())
    monkeypatch.setattr(
        "agent_workbench.cli.resolve_runtime_configuration",
        Mock(return_value=configuration),
    )
    monkeypatch.setattr(
        "agent_workbench.cli.create_agent_session",
        Mock(return_value=session),
    )
    monkeypatch.setattr("agent_workbench.cli.run_cli", run_cli_mock)

    main(["--workspace", str(tmp_path), "--enable-actions"])

    run_cli_mock.assert_called_once_with(
        session,
        agent_profile=None,
        enable_actions=True,
    )


def transaction_preview() -> dict[str, object]:
    """Create one mixed deterministic transaction approval preview."""

    return {
        "operation_count": 2,
        "created_count": 1,
        "updated_count": 1,
        "total_old_size_bytes": 4,
        "total_new_size_bytes": 8,
        "total_changed_lines": 3,
        "changes": [
            {
                "path": "a.py",
                "operation": "create",
                "old_size_bytes": 0,
                "new_size_bytes": 4,
                "changed_lines": 1,
                "diff": "--- /dev/null\n+++ b/a.py\n+new\n",
            },
            {
                "path": "z.py",
                "operation": "update",
                "old_size_bytes": 4,
                "new_size_bytes": 4,
                "changed_lines": 2,
                "diff": "--- a/z.py\n+++ b/z.py\n-old\n+new\n",
            },
        ],
    }


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("y", ToolApprovalDecision.APPROVE),
        ("YES", ToolApprovalDecision.APPROVE),
        ("", ToolApprovalDecision.DENY),
        ("approve", ToolApprovalDecision.DENY),
    ],
)
def test_transaction_approval_renders_complete_plan_and_limits_once(
    monkeypatch,
    capsys,
    answer,
    expected,
) -> None:
    """Show every diff and the precise rollback boundary before one prompt."""

    prompts: list[str] = []
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: prompts.append(prompt) or answer,
    )

    decision = _prompt_for_tool_approval(
        approval_request(
            "apply_workspace_changes",
            transaction_preview(),
        )
    )

    output = capsys.readouterr().out
    assert decision is expected
    assert prompts == ["Approve action? [y/N]: "]
    assert "Transactional workspace change" in output
    assert "Files: 2" in output
    assert "Created: 1" in output
    assert "Updated: 1" in output
    assert "Total changed lines: 3" in output
    assert output.index("a.py") < output.index("z.py")
    assert "--- /dev/null" in output
    assert "--- a/z.py" in output
    assert "exact listed transaction only" in output
    assert "handled in-process failures" in output
    assert "power loss" in output
    assert "abrupt process termination" in output
    assert "filesystem failure" in output
    assert "rollback failure" in output


def test_transaction_trace_redacts_all_contents_and_diffs(capsys) -> None:
    """Expose only safe path, count, flag, and bounded result metadata."""

    invocation = ToolInvocation(
        id="transaction",
        tool_name="apply_workspace_changes",
        arguments={
            "changes": [
                {
                    "path": "a.py",
                    "expected_content": "SECRET-OLD",
                    "replacement_content": "SECRET-NEW",
                    "create_if_missing": False,
                },
                {
                    "path": "/absolute/b.py",
                    "expected_content": "",
                    "replacement_content": "SECOND-SECRET",
                    "create_if_missing": True,
                },
            ]
        },
    )
    round_ = ToolInteractionRound(
        response=ChatResponse(tool_invocations=(invocation,)),
        results=(
            ToolResult(
                invocation_id="transaction",
                status="success",
                output={
                    "operation_count": 2,
                    "created_count": 1,
                    "updated_count": 1,
                    "total_old_size_bytes": 10,
                    "total_new_size_bytes": 22,
                    "total_changed_lines": 3,
                    "changes": [
                        {
                            "path": "a.py",
                            "operation": "update",
                            "old_size_bytes": 10,
                            "new_size_bytes": 10,
                            "changed_lines": 2,
                        }
                    ],
                },
            ),
        ),
    )

    _display_tool_round(round_)

    output = capsys.readouterr().out
    assert "SECRET-OLD" not in output
    assert "SECRET-NEW" not in output
    assert "SECOND-SECRET" not in output
    assert '"operation_count":2' in output
    assert '"path":"a.py"' in output
    assert '"expected_content_bytes":10' in output
    assert '"replacement_content_bytes":10' in output
    assert "[redacted absolute path]" in output
    assert '"created_count":1' in output
    assert "diff" not in output.lower()


def test_cli_approved_transaction_reaches_final_response(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    """Apply one approved mixed transaction through AgentSession and the CLI."""

    target = tmp_path / "z.py"
    target.write_text("old\n", encoding="utf-8")
    provider = FakeProvider(
        [
            ChatResponse(
                tool_invocations=(
                    ToolInvocation(
                        id="transaction",
                        tool_name="apply_workspace_changes",
                        arguments={
                            "changes": [
                                {
                                    "path": "z.py",
                                    "expected_content": "old\n",
                                    "replacement_content": "new\n",
                                },
                                {
                                    "path": "a.py",
                                    "expected_content": "",
                                    "replacement_content": "new\n",
                                    "create_if_missing": True,
                                },
                            ]
                        },
                    ),
                )
            ),
            ChatResponse(text="Transaction complete."),
        ]
    )
    session = AgentSession(
        id=SessionId("transaction"),
        provider=provider,
        tool_registry=action_registry(Workspace(tmp_path)),
    )
    inputs = iter(["Change both.", "yes", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))

    run_cli(
        session,
        enable_actions=True,
        show_tool_traces=True,
    )

    output = capsys.readouterr().out
    assert output.count("Approve action?") == 0
    assert "Transaction complete." in output
    assert target.read_text(encoding="utf-8") == "new\n"
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "new\n"
    assert session.messages == (
        {"role": "user", "content": "Change both."},
        {"role": "assistant", "content": "Transaction complete."},
    )
    result = provider.requests[1].tool_interactions[0].results[0]
    assert result.status == "success"
    assert result.output["operation_count"] == 2


def test_cli_transaction_denial_writes_nothing_and_later_turn_succeeds(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    """Keep denied transaction turns out of history and continue safely."""

    target = tmp_path / "module.py"
    target.write_text("old\n", encoding="utf-8")
    provider = FakeProvider(
        [
            ChatResponse(
                tool_invocations=(
                    ToolInvocation(
                        id="transaction",
                        tool_name="apply_workspace_changes",
                        arguments={
                            "changes": [
                                {
                                    "path": "module.py",
                                    "expected_content": "old\n",
                                    "replacement_content": "new\n",
                                }
                            ]
                        },
                    ),
                )
            ),
            ChatResponse(text="Recovered."),
        ]
    )
    session = AgentSession(
        id=SessionId("transaction-denial"),
        provider=provider,
        tool_registry=action_registry(Workspace(tmp_path)),
    )
    inputs = iter(["Change.", "", "Continue.", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))

    run_cli(session, enable_actions=True)

    output = capsys.readouterr().out
    assert "approval was denied" in output
    assert "Assistant: Recovered." in output
    assert target.read_text(encoding="utf-8") == "old\n"
    assert provider.requests[1].messages == [{"role": "user", "content": "Continue."}]


def test_cli_stale_transaction_error_is_safe_and_rolls_back_turn(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    """Render a stale transaction without traceback or failed-turn history."""

    target = tmp_path / "module.py"
    target.write_text("old\n", encoding="utf-8")
    provider = FakeProvider(
        [
            ChatResponse(
                tool_invocations=(
                    ToolInvocation(
                        id="transaction",
                        tool_name="apply_workspace_changes",
                        arguments={
                            "changes": [
                                {
                                    "path": "module.py",
                                    "expected_content": "old\n",
                                    "replacement_content": "new\n",
                                }
                            ]
                        },
                    ),
                )
            ),
            ChatResponse(text="Recovered."),
        ]
    )
    session = AgentSession(
        id=SessionId("transaction-stale"),
        provider=provider,
        tool_registry=action_registry(Workspace(tmp_path)),
    )

    def approve_after_change(request):
        target.write_text("concurrent\n", encoding="utf-8")
        return ToolApprovalDecision.APPROVE

    monkeypatch.setattr(
        "agent_workbench.cli._prompt_for_tool_approval",
        approve_after_change,
    )
    inputs = iter(["Change.", "Continue.", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))

    run_cli(session, enable_actions=True)

    output = capsys.readouterr().out
    assert "transaction is stale" in output
    assert "Traceback" not in output
    assert str(tmp_path) not in output
    assert "Assistant: Recovered." in output
    assert target.read_text(encoding="utf-8") == "concurrent\n"
    assert provider.requests[1].messages == [{"role": "user", "content": "Continue."}]


def test_cli_incomplete_rollback_warns_with_relative_paths(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    """Tell the operator to inspect safe affected paths after rollback failure."""

    first = tmp_path / "a.py"
    second = tmp_path / "b.py"
    first.write_text("one\n", encoding="utf-8")
    second.write_text("two\n", encoding="utf-8")
    provider = FakeProvider(
        [
            ChatResponse(
                tool_invocations=(
                    ToolInvocation(
                        id="transaction",
                        tool_name="apply_workspace_changes",
                        arguments={
                            "changes": [
                                {
                                    "path": "a.py",
                                    "expected_content": "one\n",
                                    "replacement_content": "ONE\n",
                                },
                                {
                                    "path": "b.py",
                                    "expected_content": "two\n",
                                    "replacement_content": "TWO\n",
                                },
                            ]
                        },
                    ),
                )
            ),
            ChatResponse(text="Inspecting manually."),
        ]
    )
    session = AgentSession(
        id=SessionId("transaction-rollback"),
        provider=provider,
        tool_registry=action_registry(Workspace(tmp_path)),
    )
    from agent_workbench import workspace_actions

    original_commit = workspace_actions._commit_prepared_change
    commits = 0

    def fail_second(change):
        nonlocal commits
        commits += 1
        if commits == 2:
            raise OSError("injected")
        original_commit(change)

    monkeypatch.setattr(workspace_actions, "_commit_prepared_change", fail_second)
    monkeypatch.setattr(
        workspace_actions,
        "_rollback_applied_change",
        lambda change: (_ for _ in ()).throw(OSError("rollback injected")),
    )
    inputs = iter(["Change.", "y", "Continue.", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))

    run_cli(session, enable_actions=True)

    output = capsys.readouterr().out
    assert "rollback was incomplete" in output
    assert "inspect these paths manually: a.py" in output
    assert str(tmp_path) not in output
    assert "Traceback" not in output
    assert "Assistant: Inspecting manually." in output
    assert provider.requests[1].messages == [{"role": "user", "content": "Continue."}]
