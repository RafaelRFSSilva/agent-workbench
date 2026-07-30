"""Run one externally controlled deterministic coding workflow."""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath

from agent_workbench.errors import CompletionError, ConfigurationError
from agent_workbench.messages import ChatResponse, ToolInteractionRound
from agent_workbench.session import AgentSession
from agent_workbench.tasks import TaskSpec
from agent_workbench.tool_calling import ToolRoundObserver
from agent_workbench.tool_registry import ToolRegistry
from agent_workbench.tools import (
    JSONObject,
    ToolApprovalDecision,
    ToolApprovalHandler,
    ToolInvocation,
    ToolResult,
)


DEFAULT_CODING_ACCEPTANCE_CRITERIA = (
    "Implement the requested behavior with bounded workspace changes.",
    "Run Ruff formatting and static analysis and resolve introduced issues.",
    "Run pytest and resolve introduced regressions.",
    "Inspect the final Git status and diff before reporting completion.",
)

DEFAULT_AUTONOMOUS_MAX_TOOL_ROUNDS = 16
DEFAULT_DISCOVER_MAX_TOOL_ROUNDS = 4
DEFAULT_EDIT_COMPLETION_CONTINUATIONS = 2
DEFAULT_MAX_REPAIR_ATTEMPTS = 2
DEFAULT_REPAIR_COMPLETION_CONTINUATIONS = 2
MAX_REPAIR_OUTPUT_CHARACTERS = 4_000

_READ_ONLY_TOOL_NAMES = frozenset(
    {
        "list_files",
        "read_file",
        "search_text",
        "search_symbols",
        "inspect_git_status",
        "inspect_git_diff",
    }
)
_VALIDATION_TOOL_NAMES = (
    "run_ruff_format",
    "run_ruff_check",
    "run_pytest",
)
_WORKSPACE_CHANGE_TOOL_NAMES = frozenset(
    {
        "apply_file_patch",
        "apply_text_replacement",
        "apply_workspace_changes",
    }
)
_VERIFY_TOOL_NAMES = (
    "inspect_git_status",
    "inspect_git_diff",
)
_REQUIRED_CODING_TOOLS = frozenset(
    {
        "list_files",
        "read_file",
        *_VALIDATION_TOOL_NAMES,
        *_VERIFY_TOOL_NAMES,
    }
)
_MAXIMUM_ROUNDS_ERROR = "The maximum number of tool execution rounds was exceeded."
_ABSOLUTE_PATH_PATTERN = re.compile(r"(?<![\w.])/(?:[^\s\x00]+)")
_SENSITIVE_LINE_PATTERN = re.compile(
    r"(?i)(?:^|[^a-z])(?:api[_-]?key|password|secret|token|\.env)(?:[^a-z]|$)"
)


class CodingPhase(StrEnum):
    """Represent one controller-owned deterministic workflow phase."""

    DISCOVER = "DISCOVER"
    EDIT = "EDIT"
    VALIDATE = "VALIDATE"
    REPAIR = "REPAIR"
    VERIFY = "VERIFY"
    DONE = "DONE"


@dataclass(frozen=True, slots=True)
class CodingWorkflowLimits:
    """Store typed limits for bounded model-facing workflow phases."""

    discover_tool_rounds: int = DEFAULT_DISCOVER_MAX_TOOL_ROUNDS
    edit_completion_continuations: int = DEFAULT_EDIT_COMPLETION_CONTINUATIONS
    repair_attempts: int = DEFAULT_MAX_REPAIR_ATTEMPTS
    repair_completion_continuations: int = DEFAULT_REPAIR_COMPLETION_CONTINUATIONS

    def __post_init__(self) -> None:
        """Require positive limits without accepting booleans."""

        for value in (
            self.discover_tool_rounds,
            self.edit_completion_continuations,
            self.repair_attempts,
            self.repair_completion_continuations,
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ConfigurationError(
                    "coding workflow limits must be positive integers."
                )


DEFAULT_CODING_WORKFLOW_LIMITS = CodingWorkflowLimits()


@dataclass(frozen=True, slots=True)
class ValidationRun:
    """Record one controller-invoked validation command."""

    tool_name: str
    result_status: str
    exit_code: int | None
    sequence_index: int = 0


@dataclass(frozen=True, slots=True)
class AutonomousCodingResult:
    """Summarize one completed deterministic coding workflow."""

    task_spec: TaskSpec
    assistant_summary: str
    final_phase: CodingPhase
    workspace_change_applied: bool
    repair_attempt_count: int
    completion_continuation_count: int
    tool_round_count: int
    executed_tool_names: tuple[str, ...]
    approved_action_names: tuple[str, ...]
    validation_runs: tuple[ValidationRun, ...]
    tool_results: tuple[ToolResult, ...]
    inspected_git_status: bool
    inspected_git_diff: bool
    last_workspace_change_sequence_index: int | None = None
    latest_git_status_sequence_index: int | None = None
    latest_git_diff_sequence_index: int | None = None

    @property
    def validation_succeeded(self) -> bool:
        """Return whether every latest required validation passed."""

        return all(
            _validation_succeeded_after_latest_change(self, tool_name)
            for tool_name in _VALIDATION_TOOL_NAMES
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


@dataclass(slots=True)
class _WorkflowState:
    """Own mutable evidence while the deterministic controller advances."""

    task_spec: TaskSpec
    limits: CodingWorkflowLimits
    phase: CodingPhase = CodingPhase.DISCOVER
    rounds: list[ToolInteractionRound] = field(default_factory=list)
    approved_action_names: list[str] = field(default_factory=list)
    approved_action_ids: set[str] = field(default_factory=set)
    assistant_summary: str = ""
    repair_attempt_count: int = 0
    completion_continuation_count: int = 0
    controller_invocation_count: int = 0


def run_autonomous_coding_task(
    session: AgentSession,
    prompt: str,
    *,
    tool_approval_handler: ToolApprovalHandler,
    tool_round_observer: ToolRoundObserver | None = None,
    acceptance_criteria: Iterable[str] = DEFAULT_CODING_ACCEPTANCE_CRITERIA,
    limits: CodingWorkflowLimits = DEFAULT_CODING_WORKFLOW_LIMITS,
) -> AutonomousCodingResult:
    """Run one deterministic discover, edit, validate, repair, and verify flow."""

    task_spec, registry, available_tools = _validate_inputs(
        session=session,
        prompt=prompt,
        tool_approval_handler=tool_approval_handler,
        tool_round_observer=tool_round_observer,
        acceptance_criteria=acceptance_criteria,
        limits=limits,
    )
    state = _WorkflowState(task_spec=task_spec, limits=limits)

    def observe_tool_round(round_: ToolInteractionRound) -> None:
        state.rounds.append(round_)
        if tool_round_observer is not None:
            tool_round_observer(round_)

    def handle_tool_approval(request):
        decision = tool_approval_handler(request)
        if decision is ToolApprovalDecision.APPROVE:
            state.approved_action_names.append(request.invocation.tool_name)
            state.approved_action_ids.add(request.invocation.id)
        return decision

    _run_discover_phase(
        session,
        state,
        available_tools,
        observe_tool_round,
        handle_tool_approval,
    )
    _run_model_change_phase(
        session,
        state,
        available_tools,
        observe_tool_round,
        handle_tool_approval,
        phase=CodingPhase.EDIT,
        continuation_limit=limits.edit_completion_continuations,
    )

    state.phase = CodingPhase.VALIDATE
    validation_results = _run_validation_phase(
        registry,
        state,
        observe_tool_round,
        handle_tool_approval,
    )
    failed_validations = _failed_validations(validation_results)
    while failed_validations:
        if state.repair_attempt_count >= limits.repair_attempts:
            raise _workflow_failure(
                state,
                CodingPhase.REPAIR,
                "validation still failed after the configured repair limit",
            )

        state.repair_attempt_count += 1
        changed = _run_model_change_phase(
            session,
            state,
            available_tools,
            observe_tool_round,
            handle_tool_approval,
            phase=CodingPhase.REPAIR,
            continuation_limit=limits.repair_completion_continuations,
            failed_validations=failed_validations,
        )
        if not changed and state.repair_attempt_count >= limits.repair_attempts:
            raise _workflow_failure(
                state,
                CodingPhase.REPAIR,
                "repair completed without a successful new workspace change",
            )
        if not changed:
            continue

        state.phase = CodingPhase.VALIDATE
        validation_results = _run_validation_phase(
            registry,
            state,
            observe_tool_round,
            handle_tool_approval,
        )
        failed_validations = _failed_validations(validation_results)

    state.phase = CodingPhase.VERIFY
    verification_results = _run_verify_phase(
        registry,
        state,
        observe_tool_round,
        handle_tool_approval,
    )
    _require_verification_success(state, verification_results)

    result = _build_result(state, final_phase=CodingPhase.DONE)
    if not result.workspace_change_applied:
        raise _workflow_failure(
            state,
            CodingPhase.VERIFY,
            "no successful workspace change was observed",
        )
    if not result.validation_succeeded:
        raise _workflow_failure(
            state,
            CodingPhase.VALIDATE,
            "latest required validation evidence is incomplete or unsuccessful",
        )
    if (
        not result.inspected_git_status_after_change
        or not result.inspected_git_diff_after_change
    ):
        raise _workflow_failure(
            state,
            CodingPhase.VERIFY,
            "final Git inspection evidence is incomplete or unsuccessful",
        )
    return result


def _validate_inputs(
    *,
    session: object,
    prompt: object,
    tool_approval_handler: object,
    tool_round_observer: object,
    acceptance_criteria: object,
    limits: object,
) -> tuple[TaskSpec, ToolRegistry, frozenset[str]]:
    """Validate controller inputs and return immutable available tool names."""

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
    if not isinstance(limits, CodingWorkflowLimits):
        raise ConfigurationError(
            "autonomous coding requires validated workflow limits."
        )

    registry = session.tool_registry
    if registry is None:
        raise ConfigurationError(
            "autonomous coding requires an action-enabled tool registry."
        )

    available_tools = frozenset(definition.name for definition in registry.definitions)
    missing_tools = sorted(_REQUIRED_CODING_TOOLS - available_tools)
    if missing_tools:
        raise ConfigurationError(
            "autonomous coding session is missing required tools: "
            + ", ".join(missing_tools)
            + "."
        )
    if not available_tools.intersection(_WORKSPACE_CHANGE_TOOL_NAMES):
        raise ConfigurationError(
            "autonomous coding session is missing a workspace action tool."
        )

    try:
        task_spec = TaskSpec(
            objective=prompt,
            acceptance_criteria=acceptance_criteria,
        )
    except ConfigurationError:
        raise ConfigurationError(
            "autonomous coding task specification is invalid."
        ) from None

    return task_spec, registry, available_tools


def _run_discover_phase(
    session: AgentSession,
    state: _WorkflowState,
    available_tools: frozenset[str],
    observer: ToolRoundObserver,
    approval_handler: ToolApprovalHandler,
) -> None:
    """Run bounded read-only discovery and always advance to EDIT."""

    state.phase = CodingPhase.DISCOVER
    try:
        session.send(
            _build_phase_prompt(
                state,
                outstanding=(
                    "Inspect only the repository information needed for the objective.",
                    "Do not edit files or run validation.",
                    "Return a concise discovery completion when inspection is sufficient.",
                ),
            ),
            allowed_tool_names=available_tools.intersection(_READ_ONLY_TOOL_NAMES),
            max_tool_rounds=state.limits.discover_tool_rounds,
            tool_round_observer=observer,
            tool_approval_handler=approval_handler,
            recover_approval_preview_errors=True,
        )
    except CompletionError as exc:
        if str(exc) != _MAXIMUM_ROUNDS_ERROR:
            raise _workflow_failure(
                state,
                CodingPhase.DISCOVER,
                f"discovery completion failed: {exc}",
            ) from None


def _run_model_change_phase(
    session: AgentSession,
    state: _WorkflowState,
    available_tools: frozenset[str],
    observer: ToolRoundObserver,
    approval_handler: ToolApprovalHandler,
    *,
    phase: CodingPhase,
    continuation_limit: int,
    failed_validations: tuple[tuple[str, ToolResult], ...] = (),
) -> bool:
    """Require a successful workspace action during EDIT or one REPAIR attempt."""

    state.phase = phase
    phase_start = len(state.rounds)
    local_continuations = 0
    outstanding = (
        "Apply at least one successful controlled workspace change.",
        "Use repository evidence already gathered instead of restarting discovery.",
        "Do not run validation or Git verification; the controller owns those phases.",
    )

    while True:
        prompt = (
            _build_repair_prompt(state, failed_validations, outstanding)
            if phase is CodingPhase.REPAIR
            else _build_phase_prompt(state, outstanding=outstanding)
        )
        try:
            response = session.send(
                prompt,
                allowed_tool_names=available_tools.intersection(
                    _READ_ONLY_TOOL_NAMES | _WORKSPACE_CHANGE_TOOL_NAMES
                ),
                tool_round_observer=observer,
                tool_approval_handler=approval_handler,
                recover_approval_preview_errors=True,
            )
        except CompletionError as exc:
            raise _workflow_failure(
                state,
                phase,
                f"model-facing phase failed: {exc}",
            ) from None

        state.assistant_summary = response.text
        if _rounds_contain_successful_change(
            state.rounds[phase_start:],
            state.approved_action_ids,
        ):
            return True

        if local_continuations >= continuation_limit:
            if phase is CodingPhase.EDIT:
                raise _workflow_failure(
                    state,
                    phase,
                    "completion continuation limit reached without a successful "
                    "workspace change",
                )
            return False

        local_continuations += 1
        state.completion_continuation_count += 1
        outstanding = (
            f"{phase.value} is incomplete because no successful new workspace "
            "change was observed.",
            "Apply a controlled workspace change now.",
            "Assistant prose is not evidence of a workspace change.",
        )


def _run_validation_phase(
    registry: ToolRegistry,
    state: _WorkflowState,
    observer: ToolRoundObserver,
    approval_handler: ToolApprovalHandler,
) -> tuple[tuple[str, ToolResult], ...]:
    """Invoke the complete fixed Python validation sequence."""

    results = []
    for tool_name in _VALIDATION_TOOL_NAMES:
        try:
            result = _execute_controller_invocation(
                registry,
                state,
                tool_name,
                {"path": "."},
                observer,
                approval_handler,
            )
        except CompletionError as exc:
            raise _workflow_failure(
                state,
                CodingPhase.VALIDATE,
                f"{tool_name} could not execute: {exc}",
            ) from None
        results.append((tool_name, result))
    return tuple(results)


def _run_verify_phase(
    registry: ToolRegistry,
    state: _WorkflowState,
    observer: ToolRoundObserver,
    approval_handler: ToolApprovalHandler,
) -> tuple[tuple[str, ToolResult], ...]:
    """Invoke final Git status and diff inspection in fixed order."""

    results = []
    for tool_name in _VERIFY_TOOL_NAMES:
        try:
            result = _execute_controller_invocation(
                registry,
                state,
                tool_name,
                {},
                observer,
                approval_handler,
            )
        except CompletionError as exc:
            raise _workflow_failure(
                state,
                CodingPhase.VERIFY,
                f"{tool_name} could not execute: {exc}",
            ) from None
        results.append((tool_name, result))
    return tuple(results)


def _execute_controller_invocation(
    registry: ToolRegistry,
    state: _WorkflowState,
    tool_name: str,
    arguments: JSONObject,
    observer: ToolRoundObserver,
    approval_handler: ToolApprovalHandler,
) -> ToolResult:
    """Use the registered preview, approval, and execution path directly."""

    state.controller_invocation_count += 1
    invocation = ToolInvocation(
        id=f"controller-{state.controller_invocation_count}-{tool_name}",
        tool_name=tool_name,
        arguments=arguments,
    )
    if registry.requires_approval(invocation):
        approval_request = registry.create_approval_request(invocation)
        try:
            decision = approval_handler(approval_request)
        except Exception:
            raise CompletionError("Tool approval handler failed.") from None
        if decision is ToolApprovalDecision.DENY:
            raise CompletionError("Tool action approval was denied.")
        if decision is not ToolApprovalDecision.APPROVE:
            raise CompletionError("Tool approval decision is invalid.")

    result = registry.execute(invocation)
    round_ = ToolInteractionRound(
        response=ChatResponse(tool_invocations=(invocation,)),
        results=(result,),
    )
    observer(round_)
    return result


def _failed_validations(
    results: Iterable[tuple[str, ToolResult]],
) -> tuple[tuple[str, ToolResult], ...]:
    """Return required validations whose actual latest result did not pass."""

    return tuple(
        (tool_name, result)
        for tool_name, result in results
        if result.status != "success" or _validation_exit_code(result.output) != 0
    )


def _require_verification_success(
    state: _WorkflowState,
    results: tuple[tuple[str, ToolResult], ...],
) -> None:
    """Require successful Git inspection and a non-empty final diff."""

    result_by_name = dict(results)
    for tool_name in _VERIFY_TOOL_NAMES:
        result = result_by_name[tool_name]
        if result.status != "success":
            raise _workflow_failure(
                state,
                CodingPhase.VERIFY,
                f"{tool_name} returned an error",
            )

    diff_output = result_by_name["inspect_git_diff"].output
    if not isinstance(diff_output, dict):
        raise _workflow_failure(
            state,
            CodingPhase.VERIFY,
            "inspect_git_diff returned invalid evidence",
        )
    unstaged = diff_output.get("unstaged")
    staged = diff_output.get("staged")
    if not isinstance(unstaged, str) or not isinstance(staged, str):
        raise _workflow_failure(
            state,
            CodingPhase.VERIFY,
            "inspect_git_diff returned invalid evidence",
        )
    if not unstaged.strip() and not staged.strip():
        raise _workflow_failure(
            state,
            CodingPhase.VERIFY,
            "final Git diff is empty",
        )


def _rounds_contain_successful_change(
    rounds: Iterable[ToolInteractionRound],
    approved_action_ids: set[str],
) -> bool:
    """Return whether rounds prove one approved effective workspace mutation."""

    for round_ in rounds:
        for invocation, result in zip(
            round_.response.tool_invocations,
            round_.results,
            strict=True,
        ):
            if (
                invocation.tool_name in _WORKSPACE_CHANGE_TOOL_NAMES
                and invocation.id in approved_action_ids
                and result.status == "success"
                and _workspace_change_result_applied(
                    invocation.tool_name,
                    invocation.arguments,
                    result.output,
                )
            ):
                return True
    return False


def _build_result(
    state: _WorkflowState,
    *,
    final_phase: CodingPhase,
) -> AutonomousCodingResult:
    """Aggregate immutable evidence from every model and controller tool round."""

    executed_tool_names: list[str] = []
    validation_runs: list[ValidationRun] = []
    tool_results: list[ToolResult] = []
    sequence_index = 0
    last_workspace_change_sequence_index: int | None = None
    latest_git_status_sequence_index: int | None = None
    latest_git_diff_sequence_index: int | None = None

    for round_ in state.rounds:
        for invocation, result in zip(
            round_.response.tool_invocations,
            round_.results,
            strict=True,
        ):
            sequence_index += 1
            executed_tool_names.append(invocation.tool_name)
            tool_results.append(result)

            if (
                invocation.tool_name in _WORKSPACE_CHANGE_TOOL_NAMES
                and invocation.id in state.approved_action_ids
                and result.status == "success"
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
                        result_status=str(result.status),
                        exit_code=_validation_exit_code(result.output),
                        sequence_index=sequence_index,
                    )
                )

            if (
                invocation.tool_name == "inspect_git_status"
                and result.status == "success"
            ):
                latest_git_status_sequence_index = sequence_index
            if (
                invocation.tool_name == "inspect_git_diff"
                and result.status == "success"
            ):
                latest_git_diff_sequence_index = sequence_index

    workspace_change_applied = last_workspace_change_sequence_index is not None
    return AutonomousCodingResult(
        task_spec=state.task_spec,
        assistant_summary=state.assistant_summary,
        final_phase=final_phase,
        workspace_change_applied=workspace_change_applied,
        repair_attempt_count=state.repair_attempt_count,
        completion_continuation_count=state.completion_continuation_count,
        tool_round_count=len(state.rounds),
        executed_tool_names=tuple(executed_tool_names),
        approved_action_names=tuple(state.approved_action_names),
        validation_runs=tuple(validation_runs),
        tool_results=tuple(tool_results),
        inspected_git_status="inspect_git_status" in executed_tool_names,
        inspected_git_diff="inspect_git_diff" in executed_tool_names,
        last_workspace_change_sequence_index=last_workspace_change_sequence_index,
        latest_git_status_sequence_index=latest_git_status_sequence_index,
        latest_git_diff_sequence_index=latest_git_diff_sequence_index,
    )


def _build_phase_prompt(
    state: _WorkflowState,
    *,
    outstanding: Iterable[str],
) -> str:
    """Build an explicit bounded model-facing phase prompt."""

    completed = _completed_evidence_lines(state)
    return (
        "Continue one externally controlled deterministic coding workflow.\n\n"
        f"Original objective:\n{_sanitize_prompt_text(state.task_spec.objective)}\n\n"
        f"Current phase: {state.phase.value}\n\n"
        "Completed phase evidence:\n"
        f"{_numbered_lines(completed)}\n\n"
        "Outstanding requirements:\n"
        f"{_numbered_lines(outstanding)}\n\n"
        "Current attempt counters:\n"
        f"- Repair attempts: {state.repair_attempt_count}/"
        f"{state.limits.repair_attempts}\n"
        f"- Completion continuations: {state.completion_continuation_count}\n"
        "Only the controller can advance phases or declare DONE."
    )


def _build_repair_prompt(
    state: _WorkflowState,
    failed_validations: tuple[tuple[str, ToolResult], ...],
    outstanding: Iterable[str],
) -> str:
    """Build one bounded repair prompt with fresh explicit failure evidence."""

    failure_lines = []
    for tool_name, result in failed_validations:
        exit_code = _validation_exit_code(result.output)
        failure_lines.append(
            f"{tool_name}: status={result.status}, "
            f"exit_code={exit_code if exit_code is not None else 'unavailable'}"
        )
        output = result.output
        if isinstance(output, dict):
            for stream_name in ("stdout", "stderr"):
                stream = output.get(stream_name)
                if isinstance(stream, str) and stream:
                    failure_lines.append(
                        f"{tool_name} {stream_name}: {_sanitize_repair_output(stream)}"
                    )

    changed_paths = _safe_changed_paths(state.rounds)
    path_evidence = ", ".join(changed_paths) if changed_paths else "unavailable"
    base = _build_phase_prompt(state, outstanding=outstanding)
    return (
        f"{base}\n\n"
        f"Repair attempt: {state.repair_attempt_count}/"
        f"{state.limits.repair_attempts}\n"
        "Failed validation evidence:\n"
        f"{_numbered_lines(failure_lines)}\n"
        f"Current changed-file paths: {path_evidence}\n"
        "This repair attempt requires another successful controlled workspace "
        "change before validation can run again."
    )


def _completed_evidence_lines(state: _WorkflowState) -> tuple[str, ...]:
    """Return bounded accumulated phase evidence without tool output bodies."""

    result = _build_result(state, final_phase=state.phase)
    lines = [
        f"Observed tool rounds: {result.tool_round_count}",
        "Workspace change applied: "
        f"{'yes' if result.workspace_change_applied else 'no'}",
    ]
    if result.executed_tool_names:
        lines.append("Executed tools: " + ", ".join(result.executed_tool_names[-16:]))
    if result.validation_runs:
        latest = result.validation_runs[-len(_VALIDATION_TOOL_NAMES) :]
        lines.append(
            "Latest validations: "
            + ", ".join(
                f"{run.tool_name}={run.result_status}/"
                f"{run.exit_code if run.exit_code is not None else 'unavailable'}"
                for run in latest
            )
        )
    return tuple(lines)


def _numbered_lines(lines: Iterable[str]) -> str:
    """Format bounded evidence or requirements in deterministic order."""

    values = tuple(lines)
    if not values:
        return "1. None yet."
    return "\n".join(f"{index}. {value}" for index, value in enumerate(values, start=1))


def _safe_changed_paths(
    rounds: Iterable[ToolInteractionRound],
) -> tuple[str, ...]:
    """Extract only safe relative paths from successful workspace results."""

    paths: set[str] = set()
    for round_ in rounds:
        for invocation, result in zip(
            round_.response.tool_invocations,
            round_.results,
            strict=True,
        ):
            if (
                invocation.tool_name not in _WORKSPACE_CHANGE_TOOL_NAMES
                or result.status != "success"
                or not isinstance(result.output, dict)
            ):
                continue
            candidate = result.output.get("path")
            if isinstance(candidate, str) and _is_safe_prompt_path(candidate):
                paths.add(candidate)
            changes = result.output.get("changes")
            if isinstance(changes, list):
                for change in changes:
                    if not isinstance(change, dict):
                        continue
                    candidate = change.get("path")
                    if isinstance(candidate, str) and _is_safe_prompt_path(candidate):
                        paths.add(candidate)
    return tuple(sorted(paths))


def _is_safe_prompt_path(path: str) -> bool:
    """Return whether one workspace-relative path is safe for a model prompt."""

    if not path or path.startswith("/") or "\\" in path or "\x00" in path:
        return False
    pure_path = PurePosixPath(path)
    return (
        ".." not in pure_path.parts
        and ".env" not in pure_path.parts
        and not pure_path.is_absolute()
    )


def _sanitize_repair_output(output: str) -> str:
    """Bound and redact validation output before placing it in a repair prompt."""

    return _sanitize_prompt_text(output)[:MAX_REPAIR_OUTPUT_CHARACTERS]


def _sanitize_prompt_text(text: str) -> str:
    """Redact sensitive lines and absolute paths from model-facing text."""

    safe_lines = []
    for line in text.splitlines():
        if _SENSITIVE_LINE_PATTERN.search(line):
            safe_lines.append("[redacted sensitive content]")
        else:
            safe_lines.append(_ABSOLUTE_PATH_PATTERN.sub("[absolute-path]", line))
    return "\n".join(safe_lines)


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


def _validation_exit_code(output: object) -> int | None:
    """Extract one safe non-boolean validation exit code."""

    if not isinstance(output, dict):
        return None
    exit_code = output.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        return None
    return exit_code


def _workflow_failure(
    state: _WorkflowState,
    phase: CodingPhase,
    reason: str,
) -> CompletionError:
    """Return one phase-specific terminal error with current attempt counts."""

    return CompletionError(
        f"Deterministic coding failed in phase {phase.value}: {reason}. "
        f"repair_attempts={state.repair_attempt_count}, "
        f"completion_continuations={state.completion_continuation_count}."
    )
