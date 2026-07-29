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
MAX_AUTONOMOUS_COMPLETION_CONTINUATIONS = 1

_REQUIRED_CODING_TOOLS = frozenset(
    {
        "list_files",
        "read_file",
        "inspect_git_status",
        "inspect_git_diff",
        "apply_text_replacement",
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

_WORKSPACE_CHANGE_TOOL_NAMES = frozenset(
    {
        "apply_file_patch",
        "apply_text_replacement",
        "apply_workspace_changes",
    }
)


@dataclass(frozen=True, slots=True)
class ValidationRun:
    """Record one validation command observed during an autonomous task."""

    tool_name: str
    result_status: str
    exit_code: int | None
    sequence_index: int = 0


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
    last_workspace_change_sequence_index: int | None = None
    latest_git_status_sequence_index: int | None = None
    latest_git_diff_sequence_index: int | None = None

    @property
    def workspace_change_applied(self) -> bool:
        """Return whether one approved workspace mutation completed successfully."""

        return self.last_workspace_change_sequence_index is not None

    @property
    def validation_succeeded(self) -> bool:
        """Return whether required validations passed after the latest change."""

        return all(
            _validation_succeeded_after_latest_change(self, tool_name)
            for tool_name in ("run_ruff_check", "run_pytest")
        )

    @property
    def inspected_git_status_after_change(self) -> bool:
        """Return whether Git status succeeded after the latest change."""

        return _evidence_follows_latest_change(
            self,
            self.latest_git_status_sequence_index,
        )

    @property
    def inspected_git_diff_after_change(self) -> bool:
        """Return whether Git diff succeeded after the latest change."""

        return _evidence_follows_latest_change(
            self,
            self.latest_git_diff_sequence_index,
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
    approved_action_ids: set[str] = set()

    def observe_tool_round(round_: ToolInteractionRound) -> None:
        observed_rounds.append(round_)
        if tool_round_observer is not None:
            tool_round_observer(round_)

    def handle_tool_approval(request):
        decision = tool_approval_handler(request)

        if decision is ToolApprovalDecision.APPROVE:
            approved_action_names.append(request.invocation.tool_name)
            approved_action_ids.add(request.invocation.id)

        return decision

    response = session.send(
        _build_coding_prompt(task_spec),
        tool_round_observer=observe_tool_round,
        tool_approval_handler=handle_tool_approval,
        recover_approval_preview_errors=True,
    )

    for _ in range(MAX_AUTONOMOUS_COMPLETION_CONTINUATIONS):
        result = _build_autonomous_coding_result(
            task_spec=task_spec,
            assistant_summary=response.text,
            observed_rounds=observed_rounds,
            approved_action_names=approved_action_names,
            approved_action_ids=approved_action_ids,
        )
        missing_gates = _missing_completion_gates(result)
        if not missing_gates:
            return result

        response = session.send(
            _build_completion_continuation_prompt(missing_gates),
            tool_round_observer=observe_tool_round,
            tool_approval_handler=handle_tool_approval,
            recover_approval_preview_errors=True,
        )

    return _build_autonomous_coding_result(
        task_spec=task_spec,
        assistant_summary=response.text,
        observed_rounds=observed_rounds,
        approved_action_names=approved_action_names,
        approved_action_ids=approved_action_ids,
    )


def _build_autonomous_coding_result(
    *,
    task_spec: TaskSpec,
    assistant_summary: str,
    observed_rounds: Iterable[ToolInteractionRound],
    approved_action_names: Iterable[str],
    approved_action_ids: Iterable[str],
) -> AutonomousCodingResult:
    """Build one aggregate result from every observed coding tool round."""

    rounds = tuple(observed_rounds)
    approved_ids = frozenset(approved_action_ids)
    executed_tool_names: list[str] = []
    validation_runs: list[ValidationRun] = []
    sequence_index = 0
    last_workspace_change_sequence_index: int | None = None
    latest_git_status_sequence_index: int | None = None
    latest_git_diff_sequence_index: int | None = None

    for round_ in rounds:
        for invocation, result in zip(
            round_.response.tool_invocations,
            round_.results,
            strict=True,
        ):
            sequence_index += 1
            executed_tool_names.append(invocation.tool_name)
            result_status = str(result.status)

            if (
                invocation.tool_name in _WORKSPACE_CHANGE_TOOL_NAMES
                and invocation.id in approved_ids
                and result_status == "success"
                and _workspace_change_result_applied(
                    invocation.tool_name,
                    invocation.arguments,
                    result.output,
                )
            ):
                last_workspace_change_sequence_index = sequence_index

            if invocation.tool_name in _VALIDATION_TOOL_NAMES:
                validation_runs.append(
                    ValidationRun(
                        tool_name=invocation.tool_name,
                        result_status=result_status,
                        exit_code=_validation_exit_code(result.output),
                        sequence_index=sequence_index,
                    )
                )

            if (
                invocation.tool_name == "inspect_git_status"
                and result_status == "success"
            ):
                latest_git_status_sequence_index = sequence_index
            if (
                invocation.tool_name == "inspect_git_diff"
                and result_status == "success"
            ):
                latest_git_diff_sequence_index = sequence_index

    return AutonomousCodingResult(
        task_spec=task_spec,
        assistant_summary=assistant_summary,
        tool_round_count=len(rounds),
        executed_tool_names=tuple(executed_tool_names),
        approved_action_names=tuple(approved_action_names),
        validation_runs=tuple(validation_runs),
        inspected_git_status="inspect_git_status" in executed_tool_names,
        inspected_git_diff="inspect_git_diff" in executed_tool_names,
        last_workspace_change_sequence_index=(last_workspace_change_sequence_index),
        latest_git_status_sequence_index=latest_git_status_sequence_index,
        latest_git_diff_sequence_index=latest_git_diff_sequence_index,
    )


def _missing_completion_gates(
    result: AutonomousCodingResult,
) -> tuple[str, ...]:
    """Return incomplete mandatory gates in deterministic order."""

    missing: list[str] = []

    if not result.workspace_change_applied:
        missing.append("successful approved workspace change")

    for tool_name, gate_name in (
        (
            "run_ruff_check",
            "successful run_ruff_check after the latest workspace change",
        ),
        (
            "run_pytest",
            "successful run_pytest after the latest workspace change",
        ),
    ):
        if not _validation_succeeded_after_latest_change(result, tool_name):
            missing.append(gate_name)

    if not result.inspected_git_status_after_change:
        missing.append("inspect_git_status after the latest workspace change")
    if not result.inspected_git_diff_after_change:
        missing.append("inspect_git_diff after the latest workspace change")

    return tuple(missing)


def _workspace_change_result_applied(
    tool_name: str,
    arguments: object,
    output: object,
) -> bool:
    """Return whether approved inputs and result metadata prove a change."""

    if not isinstance(arguments, dict) or not isinstance(output, dict):
        return False

    if tool_name == "apply_workspace_changes":
        changes = arguments.get("changes")
        if isinstance(changes, list) and any(
            isinstance(change, dict) and change.get("create_if_missing") is True
            for change in changes
        ):
            return True

        changed_lines = output.get("total_changed_lines")
    else:
        if (
            tool_name == "apply_file_patch"
            and arguments.get("create_if_missing") is True
        ):
            return True

        changed_lines = output.get("changed_lines")

    return (
        isinstance(changed_lines, int)
        and not isinstance(changed_lines, bool)
        and changed_lines > 0
    )


def _validation_succeeded_after_latest_change(
    result: AutonomousCodingResult,
    tool_name: str,
) -> bool:
    """Return whether the latest post-change validation succeeded."""

    change_index = result.last_workspace_change_sequence_index
    if change_index is None:
        return False

    latest: ValidationRun | None = None
    for validation in result.validation_runs:
        if (
            validation.tool_name == tool_name
            and validation.sequence_index > change_index
        ):
            latest = validation

    return (
        latest is not None
        and latest.result_status == "success"
        and latest.exit_code == 0
    )


def _evidence_follows_latest_change(
    result: AutonomousCodingResult,
    evidence_sequence_index: int | None,
) -> bool:
    """Return whether successful evidence follows the latest workspace change."""

    change_index = result.last_workspace_change_sequence_index
    return (
        change_index is not None
        and evidence_sequence_index is not None
        and evidence_sequence_index > change_index
    )


def _build_completion_continuation_prompt(
    missing_gates: Iterable[str],
) -> str:
    """Build one deterministic bounded completion-continuation prompt."""

    gates = tuple(missing_gates)
    formatted_gates = "\n".join(
        f"{index}. {gate}" for index, gate in enumerate(gates, start=1)
    )
    return (
        "Continue the same supervised autonomous coding task.\n"
        "The previous response ended before mandatory completion evidence "
        "was gathered.\n"
        f"Missing completion gates:\n{formatted_gates}\n"
        "Use the available tools now. Validation and Git inspection evidence "
        "must follow the latest completed approved workspace write. Do not "
        "repeat already completed work unnecessarily. Do not provide a final "
        "response until the missing gates have been attempted."
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
        "3. Complete at least one approved workspace change required by the "
        "objective. Prefer apply_text_replacement for small exact edits to "
        "existing files, using the sha256 value from the most recent read_file "
        "result; use complete-content actions only when necessary. Inspection "
        "and validation without a successful approved change do not complete "
        "a coding task.\n"
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
