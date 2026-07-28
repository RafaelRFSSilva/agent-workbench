"""Run one bounded supervised autonomous coding task."""

from collections.abc import Iterable
from dataclasses import dataclass

from agent_workbench.errors import ConfigurationError
from agent_workbench.messages import ToolInteractionRound
from agent_workbench.session import AgentSession
from agent_workbench.tasks import TaskSpec
from agent_workbench.tools import ToolApprovalHandler, ToolApprovalDecision
from agent_workbench.tool_calling import ToolRoundObserver


DEFAULT_CODING_ACCEPTANCE_CRITERIA = (
    "Implement the requested behavior with bounded workspace changes.",
    "Run Ruff static analysis and resolve introduced issues.",
    "Run pytest and resolve introduced regressions.",
    "Inspect the final Git status and diff before reporting completion.",
)

DEFAULT_AUTONOMOUS_MAX_TOOL_ROUNDS = 16

_REQUIRED_CODING_TOOLS = frozenset(
    {
        "list_files",
        "read_file",
        "inspect_git_status",
        "inspect_git_diff",
        "apply_workspace_changes",
        "run_ruff_check",
        "run_pytest",
    }
)

_VALIDATION_TOOL_NAMES = frozenset(
    {
        "run_ruff_format",
        "run_ruff_check",
        "run_pytest",
    }
)


@dataclass(frozen=True, slots=True)
class ValidationRun:
    """Record one validation command observed during an autonomous task."""

    tool_name: str
    result_status: str
    exit_code: int | None


@dataclass(frozen=True, slots=True)
class AutonomousCodingResult:
    """Summarize one completed supervised autonomous coding task."""

    task_spec: TaskSpec
    assistant_summary: str
    tool_round_count: int
    executed_tool_names: tuple[str, ...]
    approved_action_names: tuple[str, ...]
    validation_runs: tuple[ValidationRun, ...]
    inspected_git_status: bool
    inspected_git_diff: bool

    @property
    def validation_succeeded(self) -> bool:
        """Return whether the latest required validations completed successfully."""

        latest_runs = {
            validation.tool_name: validation for validation in self.validation_runs
        }
        required = {
            "run_ruff_check",
            "run_pytest",
        }

        if not required.issubset(latest_runs):
            return False

        return all(
            latest_runs[tool_name].result_status == "success"
            and latest_runs[tool_name].exit_code == 0
            for tool_name in required
        )


def run_autonomous_coding_task(
    session: AgentSession,
    prompt: str,
    *,
    tool_approval_handler: ToolApprovalHandler,
    tool_round_observer: ToolRoundObserver | None = None,
    acceptance_criteria: Iterable[str] = DEFAULT_CODING_ACCEPTANCE_CRITERIA,
) -> AutonomousCodingResult:
    """Run one prompt through the complete bounded coding tool loop."""

    if not isinstance(session, AgentSession):
        raise ConfigurationError("autonomous coding requires an AgentSession.")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ConfigurationError("autonomous coding prompt must be a non-blank string.")
    if not callable(tool_approval_handler):
        raise ConfigurationError("autonomous coding requires a tool approval handler.")
    if tool_round_observer is not None and not callable(tool_round_observer):
        raise ConfigurationError(
            "autonomous coding tool round observer must be callable."
        )

    registry = session.tool_registry
    if registry is None:
        raise ConfigurationError(
            "autonomous coding requires an action-enabled tool registry."
        )

    available_tools = {definition.name for definition in registry.definitions}
    missing_tools = sorted(_REQUIRED_CODING_TOOLS - available_tools)
    if missing_tools:
        raise ConfigurationError(
            "autonomous coding session is missing required tools: "
            + ", ".join(missing_tools)
            + "."
        )

    task_spec = TaskSpec(
        objective=prompt,
        acceptance_criteria=acceptance_criteria,
    )
    observed_rounds: list[ToolInteractionRound] = []
    approved_action_names: list[str] = []

    def observe_tool_round(round_: ToolInteractionRound) -> None:
        observed_rounds.append(round_)
        if tool_round_observer is not None:
            tool_round_observer(round_)

    def handle_tool_approval(request):
        decision = tool_approval_handler(request)

        if decision is ToolApprovalDecision.APPROVE:
            approved_action_names.append(request.invocation.tool_name)

        return decision

    response = session.send(
        _build_coding_prompt(task_spec),
        tool_round_observer=observe_tool_round,
        tool_approval_handler=handle_tool_approval,
        recover_approval_preview_errors=True,
    )

    executed_tool_names: list[str] = []
    validation_runs: list[ValidationRun] = []

    for round_ in observed_rounds:
        for invocation, result in zip(
            round_.response.tool_invocations,
            round_.results,
            strict=True,
        ):
            executed_tool_names.append(invocation.tool_name)

            if invocation.tool_name in _VALIDATION_TOOL_NAMES:
                validation_runs.append(
                    ValidationRun(
                        tool_name=invocation.tool_name,
                        result_status=str(result.status),
                        exit_code=_validation_exit_code(result.output),
                    )
                )

    return AutonomousCodingResult(
        task_spec=task_spec,
        assistant_summary=response.text,
        tool_round_count=len(observed_rounds),
        executed_tool_names=tuple(executed_tool_names),
        approved_action_names=tuple(approved_action_names),
        validation_runs=tuple(validation_runs),
        inspected_git_status="inspect_git_status" in executed_tool_names,
        inspected_git_diff="inspect_git_diff" in executed_tool_names,
    )


def _build_coding_prompt(task_spec: TaskSpec) -> str:
    """Create one deterministic task and execution-protocol prompt."""

    criteria = "\n".join(
        f"{index}. {criterion}"
        for index, criterion in enumerate(
            task_spec.acceptance_criteria,
            start=1,
        )
    )

    return (
        "Complete one supervised autonomous coding task.\n\n"
        f"Objective:\n{task_spec.objective}\n\n"
        f"Acceptance criteria:\n{criteria}\n\n"
        "Execution protocol:\n"
        "1. Inspect the workspace and relevant files before editing.\n"
        "2. Use the available read-only tools to understand existing behavior.\n"
        "3. Apply only bounded changes required by the objective.\n"
        "4. Request each approval-required action separately.\n"
        "5. Run run_ruff_check and run_pytest after making changes.\n"
        "6. If validation fails, inspect the output, correct the implementation, "
        "and rerun validation within the available tool-round limit.\n"
        "7. Call inspect_git_status with {} and inspect_git_diff with {} "
        "before the final response.\n"
        "8. Summarize the changes, validation results, and unresolved issues.\n"
        "Do not claim success unless the observed validation results support it."
    )


def _validation_exit_code(output: object) -> int | None:
    """Extract one safe non-boolean validation exit code."""

    if not isinstance(output, dict):
        return None

    exit_code = output.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        return None

    return exit_code
