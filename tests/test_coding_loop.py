"""Vertical tests for the supervised autonomous coding loop."""

from collections.abc import Iterable
from pathlib import Path
import subprocess

from agent_workbench.coding_loop import run_autonomous_coding_task
from agent_workbench.git_tools import register_git_tools
from agent_workbench.messages import ChatRequest, ChatResponse
from agent_workbench.session import AgentSession, SessionId
from agent_workbench.tool_registry import ToolRegistry
from agent_workbench.tools import (
    ToolApprovalDecision,
    ToolInvocation,
)
from agent_workbench.validation_tools import register_validation_tools
from agent_workbench.workspace import Workspace
from agent_workbench.workspace_actions import register_workspace_action_tools
from agent_workbench.workspace_tools import register_workspace_tools


class ScriptedProvider:
    """Return deterministic responses for one complete coding workflow."""

    name = "scripted"
    model_name = "scripted-coding-model"

    def __init__(self, responses: Iterable[ChatResponse]) -> None:
        self._responses = iter(responses)
        self.requests: list[ChatRequest] = []

    def complete(self, request: ChatRequest) -> ChatResponse:
        """Record each request and return the next scripted response."""

        self.requests.append(request)
        return next(self._responses)


def run_git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run one Git command inside a disposable test repository."""

    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def create_coding_repository(root: Path) -> Path:
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

    run_git(root, "add", "module.py", "test_module.py")
    run_git(root, "commit", "-m", "initial")
    return root


def create_coding_registry(workspace: Workspace) -> ToolRegistry:
    """Register the real tools needed by one bounded coding task."""

    registry = ToolRegistry()
    register_workspace_tools(registry, workspace)
    register_git_tools(registry, workspace)
    register_workspace_action_tools(registry, workspace)
    register_validation_tools(registry, workspace)
    return registry


def tool_response(
    invocation_id: str,
    tool_name: str,
    arguments: dict[str, object],
) -> ChatResponse:
    """Create one deterministic single-tool provider response."""

    return ChatResponse(
        tool_invocations=(
            ToolInvocation(
                id=invocation_id,
                tool_name=tool_name,
                arguments=arguments,
            ),
        )
    )


def test_runs_complete_inspect_edit_validate_and_diff_cycle(
    tmp_path: Path,
) -> None:
    """Complete one real bounded coding workflow from a single prompt."""

    repository = create_coding_repository(tmp_path / "project")
    workspace = Workspace(repository)
    registry = create_coding_registry(workspace)

    original_content = (
        "def add(left: int, right: int) -> int:\n    return left - right\n"
    )
    corrected_content = (
        "def add(left: int, right: int) -> int:\n    return left + right\n"
    )

    provider = ScriptedProvider(
        [
            tool_response(
                "list",
                "list_files",
                {"path": "."},
            ),
            tool_response(
                "read",
                "read_file",
                {"path": "module.py"},
            ),
            tool_response(
                "patch",
                "apply_file_patch",
                {
                    "path": "module.py",
                    "expected_content": original_content,
                    "replacement_content": corrected_content,
                    "create_if_missing": False,
                },
            ),
            tool_response(
                "ruff",
                "run_ruff_check",
                {"path": "."},
            ),
            tool_response(
                "pytest",
                "run_pytest",
                {"path": "."},
            ),
            tool_response(
                "status",
                "inspect_git_status",
                {},
            ),
            tool_response(
                "diff",
                "inspect_git_diff",
                {},
            ),
            ChatResponse(
                text=(
                    "Corrected the add implementation. "
                    "Ruff and pytest completed successfully, and the final "
                    "Git status and diff were inspected."
                )
            ),
        ]
    )

    session = AgentSession(
        id=SessionId("autonomous-coding-test"),
        provider=provider,
        tool_registry=registry,
        max_tool_rounds=8,
    )

    approval_requests = []

    def approve(request):
        approval_requests.append(request)
        return ToolApprovalDecision.APPROVE

    result = run_autonomous_coding_task(
        session,
        "Correct the add function so that the existing test passes.",
        tool_approval_handler=approve,
    )

    assert (repository / "module.py").read_text(encoding="utf-8") == corrected_content

    assert result.task_spec.objective == (
        "Correct the add function so that the existing test passes."
    )
    assert result.tool_round_count == 7
    assert result.executed_tool_names == (
        "list_files",
        "read_file",
        "apply_file_patch",
        "run_ruff_check",
        "run_pytest",
        "inspect_git_status",
        "inspect_git_diff",
    )
    assert result.approved_action_names == (
        "apply_file_patch",
        "run_ruff_check",
        "run_pytest",
    )
    assert tuple(run.tool_name for run in result.validation_runs) == (
        "run_ruff_check",
        "run_pytest",
    )
    assert all(run.result_status == "success" for run in result.validation_runs)
    assert all(run.exit_code == 0 for run in result.validation_runs)
    assert result.validation_succeeded is True
    assert result.inspected_git_status is True
    assert result.inspected_git_diff is True
    assert "Corrected the add implementation" in result.assistant_summary

    assert len(approval_requests) == 3

    assert len(provider.requests) == 8
    first_prompt = provider.requests[0].messages[0]["content"]
    assert "Correct the add function" in first_prompt
    assert "Run run_ruff_check and run_pytest" in first_prompt

    assert run_git(repository, "status", "--short").stdout == " M module.py\n"
    assert "return left + right" in run_git(repository, "diff").stdout
