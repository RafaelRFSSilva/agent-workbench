"""Orchestrate one approved autonomous coding task in an isolated worktree."""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from agent_workbench.arguments import RuntimeConfiguration
from agent_workbench.coding_loop import (
    DEFAULT_AUTONOMOUS_MAX_TOOL_ROUNDS,
    DEFAULT_CODING_ACCEPTANCE_CRITERIA,
    AutonomousCodingResult,
    run_autonomous_coding_task,
)
from agent_workbench.errors import CompletionError, ConfigurationError
from agent_workbench.isolated_sessions import create_isolated_agent_session
from agent_workbench.session import SessionId
from agent_workbench.tasks import TaskSpec
from agent_workbench.tool_calling import ToolRoundObserver
from agent_workbench.tools import ToolApprovalHandler
from agent_workbench.worktree_commits import (
    MAX_COMMIT_MESSAGE_BYTES,
    IsolatedCommitApprovalHandler,
    IsolatedCommitResult,
    create_isolated_commit,
    plan_isolated_commit,
)
from agent_workbench.worktrees import (
    WorktreeApprovalHandler,
    WorktreeHandle,
    WorktreeState,
    create_git_worktree,
    inspect_git_worktree,
    plan_git_worktree,
)


@dataclass(frozen=True, slots=True)
class IsolatedAutonomousWorkflowResult:
    """Summarize one completed isolated coding and commit workflow."""

    worktree: WorktreeHandle
    coding_result: AutonomousCodingResult
    commit_result: IsolatedCommitResult
    final_worktree_state: WorktreeState


def run_isolated_autonomous_workflow(
    session_id: SessionId,
    configuration: RuntimeConfiguration,
    branch_name: str,
    target_path: Path,
    prompt: str,
    commit_message: str,
    *,
    worktree_approval_handler: WorktreeApprovalHandler,
    tool_approval_handler: ToolApprovalHandler,
    commit_approval_handler: IsolatedCommitApprovalHandler,
    tool_round_observer: ToolRoundObserver | None = None,
    acceptance_criteria: Iterable[str] = DEFAULT_CODING_ACCEPTANCE_CRITERIA,
    max_tool_rounds: int = DEFAULT_AUTONOMOUS_MAX_TOOL_ROUNDS,
) -> IsolatedAutonomousWorkflowResult:
    """Create, run, validate, and commit one isolated autonomous coding task."""

    task_spec = _validate_workflow_inputs(
        session_id=session_id,
        configuration=configuration,
        prompt=prompt,
        commit_message=commit_message,
        worktree_approval_handler=worktree_approval_handler,
        tool_approval_handler=tool_approval_handler,
        commit_approval_handler=commit_approval_handler,
        tool_round_observer=tool_round_observer,
        acceptance_criteria=acceptance_criteria,
        max_tool_rounds=max_tool_rounds,
    )

    source = configuration.workspace_root
    assert isinstance(source, Path)

    plan = plan_git_worktree(source, branch_name, target_path)
    worktree = create_git_worktree(plan, worktree_approval_handler)

    try:
        isolated = create_isolated_agent_session(
            session_id,
            configuration,
            worktree,
            max_tool_rounds=max_tool_rounds,
        )
    except (CompletionError, ConfigurationError) as exc:
        raise _preserved_failure("Isolated session construction", exc) from None

    try:
        coding_result = run_autonomous_coding_task(
            isolated.session,
            task_spec.objective,
            tool_approval_handler=tool_approval_handler,
            tool_round_observer=tool_round_observer,
            acceptance_criteria=task_spec.acceptance_criteria,
        )
    except (CompletionError, ConfigurationError) as exc:
        raise _preserved_failure("Autonomous coding", exc) from None

    _require_commit_gates(coding_result)

    try:
        commit_plan = plan_isolated_commit(worktree, commit_message)
    except (CompletionError, ConfigurationError) as exc:
        raise _preserved_failure("Isolated commit planning", exc) from None

    try:
        commit_result = create_isolated_commit(
            commit_plan,
            commit_approval_handler,
        )
    except (CompletionError, ConfigurationError) as exc:
        raise _preserved_failure("Isolated commit creation", exc) from None

    try:
        final_state = inspect_git_worktree(worktree)
    except (CompletionError, ConfigurationError) as exc:
        raise _preserved_failure("Final worktree verification", exc) from None

    if not final_state.clean or final_state.head != commit_result.new_head:
        raise CompletionError(
            "Final isolated worktree verification did not confirm the approved "
            "commit and a clean working tree. The worktree and local branch were "
            "preserved for manual recovery."
        )

    return IsolatedAutonomousWorkflowResult(
        worktree=worktree,
        coding_result=coding_result,
        commit_result=commit_result,
        final_worktree_state=final_state,
    )


def _validate_workflow_inputs(
    *,
    session_id: object,
    configuration: object,
    prompt: object,
    commit_message: object,
    worktree_approval_handler: object,
    tool_approval_handler: object,
    commit_approval_handler: object,
    tool_round_observer: object,
    acceptance_criteria: object,
    max_tool_rounds: object,
) -> TaskSpec:
    """Reject invalid workflow inputs before creating a branch or worktree."""

    if not isinstance(session_id, SessionId):
        raise ConfigurationError("isolated autonomous coding requires a SessionId.")
    if not isinstance(configuration, RuntimeConfiguration):
        raise ConfigurationError(
            "isolated autonomous coding requires a RuntimeConfiguration."
        )
    if not isinstance(configuration.workspace_root, Path):
        raise ConfigurationError(
            "isolated autonomous coding requires a source workspace."
        )
    if not configuration.enable_actions:
        raise ConfigurationError(
            "isolated autonomous coding requires controlled actions."
        )
    if not callable(worktree_approval_handler):
        raise ConfigurationError(
            "isolated autonomous coding requires a worktree approval handler."
        )
    if not callable(tool_approval_handler):
        raise ConfigurationError(
            "isolated autonomous coding requires a tool approval handler."
        )
    if not callable(commit_approval_handler):
        raise ConfigurationError(
            "isolated autonomous coding requires a commit approval handler."
        )
    if tool_round_observer is not None and not callable(tool_round_observer):
        raise ConfigurationError(
            "isolated autonomous coding tool round observer must be callable."
        )
    if (
        isinstance(max_tool_rounds, bool)
        or not isinstance(max_tool_rounds, int)
        or max_tool_rounds <= 0
    ):
        raise ConfigurationError(
            "isolated autonomous coding maximum tool rounds must be positive."
        )

    _validate_commit_message_input(commit_message)

    try:
        return TaskSpec(
            objective=prompt,
            acceptance_criteria=acceptance_criteria,
        )
    except ConfigurationError:
        raise ConfigurationError(
            "isolated autonomous coding task specification is invalid."
        ) from None


def _validate_commit_message_input(message: object) -> str:
    """Validate the exact commit message before any Git mutation."""

    if not isinstance(message, str):
        raise ConfigurationError(
            "isolated autonomous coding commit message must be a string."
        )
    if not message.strip():
        raise ConfigurationError(
            "isolated autonomous coding commit message must not be blank."
        )
    if message.startswith("-"):
        raise ConfigurationError(
            "isolated autonomous coding commit message must not begin with '-'."
        )
    if "\0" in message:
        raise ConfigurationError(
            "isolated autonomous coding commit message must not contain NUL."
        )
    try:
        encoded = message.encode("utf-8")
    except UnicodeEncodeError:
        raise ConfigurationError(
            "isolated autonomous coding commit message must be valid UTF-8."
        ) from None
    if len(encoded) > MAX_COMMIT_MESSAGE_BYTES:
        raise ConfigurationError(
            "isolated autonomous coding commit message exceeds the "
            f"{MAX_COMMIT_MESSAGE_BYTES}-byte limit."
        )
    return message


def _require_commit_gates(coding_result: AutonomousCodingResult) -> None:
    """Require successful validation and final Git inspection before planning."""

    if not coding_result.validation_succeeded:
        raise CompletionError(
            "Isolated autonomous coding did not complete successful Ruff and "
            "pytest validation. The worktree and local branch were preserved "
            "for manual recovery."
        )
    if not coding_result.inspected_git_status or not coding_result.inspected_git_diff:
        raise CompletionError(
            "Isolated autonomous coding did not inspect the final Git status "
            "and diff. The worktree and local branch were preserved for manual "
            "recovery."
        )


def _preserved_failure(stage: str, error: Exception) -> CompletionError:
    """Return one safe failure that explicitly preserves isolated state."""

    return CompletionError(
        f"{stage} failed: {error} The worktree and local branch were preserved "
        "for manual recovery."
    )
