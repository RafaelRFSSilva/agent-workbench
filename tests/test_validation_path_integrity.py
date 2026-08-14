"""Regression tests for controller-owned validation path integrity."""

from collections.abc import Iterable
import hashlib
from pathlib import Path
import subprocess

import pytest

from agent_workbench import validation_tools
from agent_workbench.coding_loop import (
    CodingPhase,
    CodingProgressEvent,
    CodingProgressKind,
    run_autonomous_coding_task,
)
from agent_workbench.errors import CompletionError
from agent_workbench.git_tools import register_git_tools
from agent_workbench.messages import ChatRequest, ChatResponse
from agent_workbench.session import AgentSession, SessionId
from agent_workbench.symbol_tools import register_symbol_tools
from agent_workbench.tool_registry import ToolRegistry
from agent_workbench.tools import ToolApprovalDecision, ToolInvocation
from agent_workbench.validation_tools import register_validation_tools
from agent_workbench.workspace import Workspace
from agent_workbench.workspace_actions import register_workspace_action_tools
from agent_workbench.workspace_tools import register_workspace_tools


class ScriptedProvider:
    """Return deterministic responses for one disposable coding workflow."""

    name = "scripted"
    model_name = "validation-path-integrity-test"

    def __init__(self, responses: Iterable[ChatResponse]) -> None:
        self._responses = iter(responses)
        self.requests: list[ChatRequest] = []

    def complete(self, request: ChatRequest) -> ChatResponse:
        """Record one request and return the next scripted response."""

        self.requests.append(request)
        return next(self._responses)


def run_git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run one Git command inside a disposable repository."""

    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def commit_all(repository: Path, message: str) -> None:
    """Commit the complete disposable repository fixture."""

    run_git(repository, "add", ".")
    run_git(repository, "commit", "-m", message)


def create_repository(root: Path) -> Path:
    """Create one committed Python project containing an intentional defect."""

    root.mkdir()
    run_git(root, "init", "-b", "main")
    run_git(root, "config", "user.name", "Test User")
    run_git(root, "config", "user.email", "test@example.com")

    (root / "module.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left - right\n",
        encoding="utf-8",
    )
    (root / "test_module.py").write_text(
        "from module import add\n"
        "\n"
        "\n"
        "def test_add() -> None:\n"
        "    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    commit_all(root, "initial")
    return root


def create_registry(repository: Path) -> ToolRegistry:
    """Register the real controller tools against one disposable repository."""

    workspace = Workspace(repository)
    registry = ToolRegistry()
    register_workspace_tools(registry, workspace)
    register_symbol_tools(registry, workspace)
    register_git_tools(registry, workspace)
    register_workspace_action_tools(registry, workspace)
    register_validation_tools(registry, workspace)
    return registry


def create_session(
    repository: Path,
    provider: ScriptedProvider,
) -> AgentSession:
    """Create one scripted action-enabled session."""

    return AgentSession(
        id=SessionId("validation-path-integrity-test"),
        provider=provider,
        tool_registry=create_registry(repository),
        max_tool_rounds=8,
    )


def tool_response(
    invocation_id: str,
    tool_name: str,
    arguments: dict[str, object],
) -> ChatResponse:
    """Create one deterministic single-tool model response."""

    return ChatResponse(
        tool_invocations=(
            ToolInvocation(
                id=invocation_id,
                tool_name=tool_name,
                arguments=arguments,
            ),
        )
    )


def edit_response(original: str) -> ChatResponse:
    """Create the legitimate approved module correction."""

    return tool_response(
        "edit",
        "apply_text_replacement",
        {
            "path": "module.py",
            "expected_text": "return left - right",
            "replacement_text": "return left + right",
            "expected_file_sha256": hashlib.sha256(
                original.encode("utf-8")
            ).hexdigest(),
        },
    )


def coding_provider(
    repository: Path, *, extra_response: bool = False
) -> ScriptedProvider:
    """Create the provider sequence needed to reach controller validation."""

    original = (repository / "module.py").read_text(encoding="utf-8")
    responses = [
        ChatResponse(text="Discovery complete."),
        edit_response(original),
        ChatResponse(text="Edit complete."),
        ChatResponse(text="Edit confirmed complete."),
    ]
    if extra_response:
        responses.append(ChatResponse(text="Repair must not begin."))
    return ScriptedProvider(responses)


def approve(_request) -> ToolApprovalDecision:
    """Approve actions only inside the disposable test repository."""

    return ToolApprovalDecision.APPROVE


def test_failing_pytest_side_effect_fails_closed_before_repair(
    tmp_path: Path,
) -> None:
    """Reject a real pytest side effect before failed validation can enter REPAIR."""

    repository = create_repository(tmp_path / "project")

    sentinel = repository / "sentinel.txt"
    sentinel.write_text("original\n", encoding="utf-8")

    (repository / "conftest.py").write_text(
        "from pathlib import Path\n"
        "\n"
        "\n"
        "def pytest_sessionstart(session) -> None:\n"
        "    del session\n"
        "    Path('sentinel.txt').write_text("
        "'changed by pytest\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    (repository / "test_validation_failure.py").write_text(
        "def test_validation_failure() -> None:\n    assert False\n",
        encoding="utf-8",
    )
    commit_all(repository, "add validation side-effect fixture")

    provider = coding_provider(repository, extra_response=True)
    progress: list[CodingProgressEvent] = []

    with pytest.raises(
        CompletionError,
        match=(
            r"phase VALIDATE: unexpected changed paths after run_pytest: "
            r"sentinel\.txt"
        ),
    ):
        run_autonomous_coding_task(
            create_session(repository, provider),
            "Correct the add implementation.",
            tool_approval_handler=approve,
            progress_event_observer=progress.append,
        )

    pytest_events = [event for event in progress if event.tool_name == "run_pytest"]
    assert len(pytest_events) == 1
    assert pytest_events[0].exit_code == 1

    assert all(
        event.kind is not CodingProgressKind.REPAIR_STARTED for event in progress
    )
    assert len(provider.requests) == 4

    assert sentinel.read_text(encoding="utf-8") == "changed by pytest\n"
    status = run_git(repository, "status", "--short").stdout
    assert "sentinel.txt" in status
    assert "module.py" in status


def test_ruff_check_side_effect_fails_before_pytest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Reject a path introduced immediately after Ruff static analysis."""

    repository = create_repository(tmp_path / "project")
    unexpected = repository / "unexpected.txt"
    original_run_validation = validation_tools.run_validation
    validation_calls: list[str] = []

    def intrusive_validation(workspace, tool_name, arguments):
        result = original_run_validation(workspace, tool_name, arguments)
        validation_calls.append(tool_name)
        if tool_name == "run_ruff_check":
            unexpected.write_text("created by validation\n", encoding="utf-8")
        return result

    monkeypatch.setattr(
        validation_tools,
        "run_validation",
        intrusive_validation,
    )

    provider = coding_provider(repository, extra_response=True)
    progress: list[CodingProgressEvent] = []

    with pytest.raises(
        CompletionError,
        match=(
            r"phase VALIDATE: unexpected changed paths after run_ruff_check: "
            r"unexpected\.txt"
        ),
    ):
        run_autonomous_coding_task(
            create_session(repository, provider),
            "Correct the add implementation.",
            tool_approval_handler=approve,
            progress_event_observer=progress.append,
        )

    assert "run_ruff_check" in validation_calls
    assert "run_pytest" not in validation_calls
    assert unexpected.read_text(encoding="utf-8") == "created by validation\n"
    assert all(
        event.kind is not CodingProgressKind.REPAIR_STARTED for event in progress
    )


def test_normal_validation_without_side_effect_reaches_done(
    tmp_path: Path,
) -> None:
    """Preserve successful controller validation when no unexpected path appears."""

    repository = create_repository(tmp_path / "project")
    provider = coding_provider(repository)

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    assert result.final_phase is CodingPhase.DONE
    assert result.validation_succeeded is True
    assert result.approved_workspace_paths == ("module.py",)
    assert (
        repository.joinpath("module.py").read_text(encoding="utf-8")
        == "def add(left: int, right: int) -> int:\n"
        "    return left + right\n"
    )


def test_baseline_dirty_path_remains_allowed_when_pytest_changes_it(
    tmp_path: Path,
) -> None:
    """Allow validation to touch a path already present in baseline evidence."""

    repository = create_repository(tmp_path / "project")

    baseline = repository / "baseline.txt"
    baseline.write_text("committed\n", encoding="utf-8")

    (repository / "conftest.py").write_text(
        "from pathlib import Path\n"
        "\n"
        "\n"
        "def pytest_sessionstart(session) -> None:\n"
        "    del session\n"
        "    Path('baseline.txt').write_text("
        "'changed again by pytest\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    commit_all(repository, "add allowed baseline fixture")

    baseline.write_text("operator baseline change\n", encoding="utf-8")

    provider = coding_provider(repository)

    result = run_autonomous_coding_task(
        create_session(repository, provider),
        "Correct the add implementation.",
        tool_approval_handler=approve,
    )

    assert result.final_phase is CodingPhase.DONE
    assert result.validation_succeeded is True
    assert result.baseline_changed_paths == ("baseline.txt",)
    assert baseline.read_text(encoding="utf-8") == "changed again by pytest\n"
    assert run_git(repository, "status", "--short").stdout == (
        " M baseline.txt\n M module.py\n"
    )
