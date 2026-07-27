"""Tests for AgentSession construction inside verified Git worktrees."""

from dataclasses import FrozenInstanceError
from pathlib import Path
import subprocess

import pytest

from agent_workbench.agents import AgentProfile
from agent_workbench.arguments import RuntimeConfiguration
from agent_workbench.context import ContextDocument
from agent_workbench.errors import CompletionError, ConfigurationError
from agent_workbench.isolated_sessions import (
    IsolatedAgentSession,
    create_isolated_agent_session,
)
from agent_workbench.session import SessionId, SessionStatus
from agent_workbench.tools import (
    ToolApprovalDecision,
    ToolInvocation,
)
from agent_workbench.worktrees import (
    WorktreeApprovalRequest,
    create_git_worktree,
    plan_git_worktree,
)


def run_git(repository: Path, *arguments: str, check: bool = True):
    """Run Git against one disposable repository."""

    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=check,
        capture_output=True,
        text=True,
    )


def create_repository(root: Path) -> Path:
    """Create a clean project repository suitable for isolated sessions."""

    root.mkdir()
    run_git(root, "init", "-b", "main")
    run_git(root, "config", "user.name", "Test User")
    run_git(root, "config", "user.email", "test@example.com")
    (root / "src").mkdir()
    (root / "src" / "module.py").write_text("value = 'source'\n", encoding="utf-8")
    (root / "README.md").write_text("source context\n", encoding="utf-8")
    (root / "second.md").write_text("second context\n", encoding="utf-8")
    run_git(root, "add", ".")
    run_git(root, "commit", "-m", "initial")
    return root


def approve(_request: WorktreeApprovalRequest) -> ToolApprovalDecision:
    """Approve one disposable worktree creation."""

    return ToolApprovalDecision.APPROVE


def create_handle(tmp_path: Path):
    """Create one verified clean worktree and return source plus handle."""

    source = create_repository(tmp_path / "source")
    plan = plan_git_worktree(source, "agent/session", tmp_path / "isolated")
    return source, create_git_worktree(plan, approve)


def configuration(source: Path, **overrides: object) -> RuntimeConfiguration:
    """Create one resolved test runtime configuration."""

    values: dict[str, object] = {
        "provider_name": "ollama",
        "model_name": "test-model",
        "workspace_root": source,
    }
    values.update(overrides)
    return RuntimeConfiguration(**values)  # type: ignore[arg-type]


def execute(session, name: str, arguments: dict[str, object]):
    """Execute one registered tool directly for workspace-binding assertions."""

    assert session.tool_registry is not None
    return session.tool_registry.execute(
        ToolInvocation(
            id=f"{name}-call",
            tool_name=name,
            arguments=arguments,  # type: ignore[arg-type]
        )
    )


def test_isolated_model_and_session_preserve_resolved_configuration(
    tmp_path: Path,
) -> None:
    """Return one immutable wrapper around a ready fully configured session."""

    source, handle = create_handle(tmp_path)
    profile = AgentProfile("Developer", "Develops.", "Develop safely.")
    runtime = configuration(
        source,
        model_name="gpt-oss:20b",
        agent_profile=profile,
        system_prompt="Resolved prompt.",
        enable_tools=True,
        show_tool_traces=True,
    )
    identifier = SessionId("isolated-session")

    isolated = create_isolated_agent_session(
        identifier,
        runtime,
        handle,
        max_tool_rounds=4,
    )

    assert isinstance(isolated, IsolatedAgentSession)
    assert not hasattr(isolated, "__dict__")
    with pytest.raises(FrozenInstanceError):
        isolated.worktree = handle  # type: ignore[misc]
    assert isolated.worktree is handle
    assert isolated.session.id is identifier
    assert isolated.session.status is SessionStatus.READY
    assert isolated.session.provider_name == "Ollama"
    assert isolated.session.model_name == "gpt-oss:20b"
    assert isolated.session.agent_profile is profile
    assert isolated.session.system_prompt == "Resolved prompt."
    assert isolated.session.max_tool_rounds == 4
    assert runtime.workspace_root == source
    assert runtime.enable_tools is True
    assert runtime.show_tool_traces is True
    assert str(source.resolve()) not in repr(isolated)
    assert str(handle.worktree_path) not in repr(isolated)


@pytest.mark.parametrize("enable_tools", [False, True])
@pytest.mark.parametrize("enable_actions", [False, True])
def test_isolated_factory_preserves_exact_registry_order_and_freshness(
    tmp_path: Path,
    enable_tools: bool,
    enable_actions: bool,
) -> None:
    """Build a fresh registry with every workspace handler bound to the worktree."""

    source, handle = create_handle(tmp_path)
    runtime = configuration(
        source,
        enable_tools=enable_tools,
        enable_actions=enable_actions,
    )

    first = create_isolated_agent_session(SessionId("first"), runtime, handle)
    second = create_isolated_agent_session(SessionId("second"), runtime, handle)

    expected = []
    if enable_tools:
        expected.append("calculator")
    expected.extend(
        [
            "list_files",
            "read_file",
            "search_text",
            "search_symbols",
            "inspect_git_status",
            "inspect_git_diff",
        ]
    )
    if enable_actions:
        expected.extend(
            [
                "apply_file_patch",
                "apply_workspace_changes",
                "run_ruff_format",
                "run_ruff_check",
                "run_pytest",
            ]
        )

    assert [item.name for item in first.session.tool_registry.definitions] == expected
    assert [item.name for item in second.session.tool_registry.definitions] == expected
    assert first.session.tool_registry is not second.session.tool_registry
    if enable_tools:
        result = execute(
            first.session,
            "calculator",
            {"expression": "(2 + 3) * 4"},
        )
        assert result.status == "success"
        assert result.output == {"expression": "(2 + 3) * 4", "result": 20}


def test_read_write_and_git_tools_are_bound_only_to_isolated_worktree(
    tmp_path: Path,
) -> None:
    """Read, patch, and inspect only the isolated checkout, never the source."""

    source, handle = create_handle(tmp_path)
    isolated = create_isolated_agent_session(
        SessionId("actions"),
        configuration(source, enable_actions=True),
        handle,
    )
    isolated_path = handle.worktree_path / "src" / "module.py"
    isolated_path.write_text("value = 'isolated'\n", encoding="utf-8")
    (source / "source-only.txt").write_text("source secret\n", encoding="utf-8")

    read = execute(
        isolated.session,
        "read_file",
        {"path": "src/module.py"},
    )
    assert read.status == "success"
    assert read.output["content"] == "value = 'isolated'\n"
    source_only = execute(
        isolated.session,
        "read_file",
        {"path": "source-only.txt"},
    )
    assert source_only.status == "error"

    patch = execute(
        isolated.session,
        "apply_file_patch",
        {
            "path": "src/module.py",
            "expected_content": "value = 'isolated'\n",
            "replacement_content": "value = 'changed'\n",
        },
    )
    assert patch.status == "success"
    assert isolated_path.read_text(encoding="utf-8") == "value = 'changed'\n"
    assert (source / "src" / "module.py").read_text(encoding="utf-8") == (
        "value = 'source'\n"
    )

    status = execute(isolated.session, "inspect_git_status", {})
    diff = execute(isolated.session, "inspect_git_diff", {})
    assert status.status == "success"
    assert "src/module.py" in status.output["status"]
    assert diff.status == "success"
    assert "value = 'changed'" in diff.output["unstaged"]
    assert "source-only.txt" not in status.output["status"]


def test_source_relative_context_is_reloaded_from_isolated_worktree(
    tmp_path: Path,
) -> None:
    """Map ordered context paths and reload their isolated contents safely."""

    source, handle = create_handle(tmp_path)
    (handle.worktree_path / "README.md").write_text(
        "isolated context\n",
        encoding="utf-8",
    )
    (handle.worktree_path / "second.md").write_text(
        "isolated second\n",
        encoding="utf-8",
    )
    documents = (
        ContextDocument(source=Path("README.md"), content="source context\n"),
        ContextDocument(
            source=(source / "second.md").resolve(),
            content="second context\n",
        ),
    )
    runtime = configuration(source, context_documents=documents)

    isolated = create_isolated_agent_session(
        SessionId("context"),
        runtime,
        handle,
    )

    assert isolated.session.context_documents == (
        ContextDocument(source=Path("README.md"), content="isolated context\n"),
        ContextDocument(source=Path("second.md"), content="isolated second\n"),
    )
    assert runtime.context_documents is documents
    assert runtime.context_documents[0].content == "source context\n"


@pytest.mark.parametrize("failure", ["missing", "external", "escaping"])
def test_isolated_context_rejects_unavailable_or_external_paths(
    tmp_path: Path,
    failure: str,
) -> None:
    """Reject context that cannot be safely mapped into the isolated checkout."""

    source, handle = create_handle(tmp_path)
    if failure == "missing":
        (handle.worktree_path / "README.md").unlink()
        context_source = Path("README.md")
    elif failure == "external":
        external = tmp_path / "external.md"
        external.write_text("external secret\n", encoding="utf-8")
        context_source = external.resolve()
    else:
        context_source = Path("../external.md")
    runtime = configuration(
        source,
        context_documents=(
            ContextDocument(source=context_source, content="original\n"),
        ),
    )

    with pytest.raises(ConfigurationError) as raised:
        create_isolated_agent_session(SessionId("invalid-context"), runtime, handle)

    message = str(raised.value)
    assert str(source.resolve()) not in message
    assert str(handle.worktree_path) not in message
    assert handle.worktree_path.exists()


@pytest.mark.parametrize(
    "failure",
    ["invalid_handle", "unregistered", "branch", "source_mismatch"],
)
def test_isolated_factory_revalidates_handle_and_source_identity(
    tmp_path: Path,
    failure: str,
) -> None:
    """Reject untrusted or changed identity without constructing a session."""

    source, handle = create_handle(tmp_path)
    supplied_handle: object = handle
    runtime_source = source
    if failure == "invalid_handle":
        supplied_handle = object()
    elif failure == "unregistered":
        run_git(source, "worktree", "remove", str(handle.worktree_path))
    elif failure == "branch":
        run_git(handle.worktree_path, "switch", "-c", "other")
    else:
        other = create_repository(tmp_path / "other-source")
        runtime_source = other

    with pytest.raises((ConfigurationError, CompletionError)) as raised:
        create_isolated_agent_session(
            SessionId("invalid"),
            configuration(runtime_source),
            supplied_handle,  # type: ignore[arg-type]
        )

    assert str(source.resolve()) not in str(raised.value)
    assert str(handle.worktree_path) not in str(raised.value)


def test_construction_failure_preserves_worktree_and_process_cwd(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Return no partial session and never clean up a created worktree implicitly."""

    source, handle = create_handle(tmp_path)
    original_cwd = Path.cwd()

    def fail_provider(provider_name, model_name):
        raise ConfigurationError("Provider configuration failed.")

    monkeypatch.setattr(
        "agent_workbench.session_factory.create_provider",
        fail_provider,
    )

    with pytest.raises(ConfigurationError, match="preserved") as raised:
        create_isolated_agent_session(
            SessionId("failed"),
            configuration(source),
            handle,
        )

    assert str(source.resolve()) not in str(raised.value)
    assert str(handle.worktree_path) not in str(raised.value)
    assert handle.worktree_path.exists()
    assert (
        "agent/session"
        in run_git(
            source,
            "branch",
            "--list",
            "agent/session",
        ).stdout
    )
    assert Path.cwd() == original_cwd
