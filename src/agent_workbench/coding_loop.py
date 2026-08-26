"""Run one externally controlled deterministic coding workflow."""

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath

from agent_workbench.errors import CompletionError, ConfigurationError
from agent_workbench.messages import ChatResponse, ToolInteractionRound
from agent_workbench.session import AgentSession
from agent_workbench.tasks import TaskSpec
from agent_workbench.tool_calling import (
    MAX_TOOL_ARGUMENT_VALIDATION_RECOVERIES,
    ToolRoundObserver,
)
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
MAX_CONTROLLED_ACTION_ARGUMENT_VALIDATION_FAILURES = (
    MAX_TOOL_ARGUMENT_VALIDATION_RECOVERIES + 1
)
MAX_DISCOVERY_EVIDENCE_ITEMS = 12
MAX_DISCOVERY_EVIDENCE_ITEM_CHARACTERS = 800
MAX_DISCOVERY_EVIDENCE_CHARACTERS = 4_000
MAX_DISCOVERY_SUMMARY_CHARACTERS = 1_000
MAX_ACTION_FAILURE_EVIDENCE_ITEMS = 8
MAX_ACTION_FAILURE_EVIDENCE_ITEM_CHARACTERS = 400
MAX_ACTION_FAILURE_EVIDENCE_CHARACTERS = 2_000
MAX_REPAIR_VALIDATION_FIELD_CHARACTERS = 4_000
MAX_REPAIR_VALIDATION_EVIDENCE_CHARACTERS = 12_000
MAX_PROGRESS_REASON_CHARACTERS = 400

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
        "apply_file_rewrite",
        "apply_text_replacement",
        "apply_line_range_replacement",
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
_MAXIMUM_ROUNDS_CHANGE_SUMMARY = (
    "A successful workspace change was applied before the model-facing "
    "phase exhausted its tool-round budget."
)
_COMPLETE_FILE_REWRITE_GUIDANCE = (
    "Before apply_file_rewrite, call read_file for the complete file. Do not "
    "construct a whole-file rewrite from a partial line-range read. Use the "
    "latest file SHA from that complete latest read. replacement_content must "
    "contain the complete resulting file."
)
_CONTROLLED_EDIT_SELECTION_GUIDANCE = (
    "Controlled edit selection:\n"
    "- After a successful workspace action, when the resulting content of that "
    "same file is already known, reuse its returned resulting_file_sha256 as "
    "the next expected_file_sha256 instead of rereading solely to obtain a SHA. "
    "If an action fails, is stale, or does not apply, reread the target before "
    "retrying.\n"
    "- Use exact-content replacement (apply_text_replacement) when the exact "
    "current fragment is known and reasonably small.\n"
    "- After inspecting the current file, use apply_line_range_replacement for "
    "a known range, particularly in a large file. Lines are one-based and "
    "inclusive; the exact current range content must be known, and "
    "expected_file_sha256 may come from an appropriate current read_file or a "
    "successful prior action result when the resulting content is known.\n"
    "- Never guess a hash or uninspected line numbers. Use apply_file_patch only "
    "with complete exact current content or for creation, and apply_file_rewrite "
    "only when complete current file content is known for a true whole-file "
    "change. Normally get it from a complete read_file, or retain it after a "
    "successful action when the exact full resulting content is known. Never use "
    "a whole-file rewrite to avoid an exact-content mismatch. Use "
    "apply_workspace_changes only when changes must succeed together.\n"
    "- Never weaken tests or validation."
)
_ABSOLUTE_PATH_PATTERN = re.compile(r"(?<![\w.])/(?:[^\s\x00]+)")
_GENERIC_SENSITIVE_LINE_PATTERN = re.compile(
    r"(?i)(?:^|[^a-z])(?:api[_-]?key|password|secret|token|\.env)(?:[^a-z]|$)"
)
_VALIDATION_ASSIGNMENT_PATTERN = re.compile(
    r"""(?ix)
    (?:^|[\s{,])
    ["']?
    (?P<name>[a-z][a-z0-9_-]{1,127})
    ["']?
    \s*[:=]
    """
)
_VALIDATION_CREDENTIAL_NAME_PATTERN = re.compile(
    r"""(?ix)^
    (?:
        (?:[a-z0-9]+[_-])*api[_-]?key
        | (?:[a-z0-9]+[_-])*password
        | (?:[a-z0-9]+[_-])*(?:secret[_-]access[_-]key|secret)
        | (?:aws[_-])?access[_-]key[_-]id
        | (?:aws[_-]session|github|gitlab|openai|stripe)[_-]token
        | slack(?:[_-](?:app|bot|user))?[_-]token
        | access[_-]token
        | auth[_-]token
        | authorization
        | token
    )
    $"""
)
_VALIDATION_AUTHORIZATION_BEARER_PATTERN = re.compile(
    r"(?i)\bauthorization\s*:?\s+bearer\s+\S+"
)
_VALIDATION_BEARER_VALUE_PATTERN = re.compile(
    r"(?i)(?:^|[\s:=])bearer\s+[a-z0-9._~+/\-=]{8,}"
)
_VALIDATION_ENV_REFERENCE_PATTERN = re.compile(
    r"(?i)(?<![a-z0-9_])\.env(?:\.[a-z0-9_-]+)*(?![a-z0-9_-])"
)
_VALIDATION_OBVIOUS_SECRET_VALUE_PATTERN = re.compile(
    r"""(?ix)
    (?:^|[\s"'=:(])
    (?:
        sk-[a-z0-9_-]{12,}
        | gh[pousr]_[a-z0-9_]{12,}
        | AKIA[A-Z0-9]{12,}
    )
    (?:$|[\s"',)])
    """
)


class CodingPhase(StrEnum):
    """Represent one controller-owned deterministic workflow phase."""

    DISCOVER = "DISCOVER"
    EDIT = "EDIT"
    VALIDATE = "VALIDATE"
    REPAIR = "REPAIR"
    VERIFY = "VERIFY"
    DONE = "DONE"


class CodingProgressKind(StrEnum):
    """Identify one provider-independent controller progress event."""

    PHASE_STARTED = "phase_started"
    PHASE_COMPLETED = "phase_completed"
    WORKSPACE_CHANGED = "workspace_changed"
    ACTION_ARGUMENTS_REJECTED = "action_arguments_rejected"
    ACTION_FAILED = "action_failed"
    VALIDATION_RESULT = "validation_result"
    REPAIR_STARTED = "repair_started"
    CHANGED_PATH_COUNT = "changed_path_count"
    DONE = "done"
    TERMINAL_FAILURE = "terminal_failure"


@dataclass(frozen=True, slots=True)
class CodingModelSendTrace:
    """Store safe metadata for one model-facing coding-loop send."""

    phase: CodingPhase
    allowed_tool_names: tuple[str, ...]
    continuation: int
    decision_mode: bool = False


@dataclass(frozen=True, slots=True)
class CodingProgressEvent:
    """Store one typed safe autonomous coding progress update."""

    phase: CodingPhase
    kind: CodingProgressKind
    path: str | None = None
    reason: str | None = None
    tool_name: str | None = None
    result_status: str | None = None
    exit_code: int | None = None
    validation_summary: str | None = None
    repair_attempt: int = 0
    max_repair_attempts: int = 0
    changed_path_count: int | None = None
    skipped: bool = False
    workspace_preserved: bool = False
    later_action_rejected: bool = False


type CodingProgressObserver = Callable[[CodingProgressEvent], None]
type CodingModelSendTraceObserver = Callable[[CodingModelSendTrace], None]


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
    target_paths: tuple[str, ...] = ()
    skipped: bool = False


@dataclass(frozen=True, slots=True)
class _DiscoveryEvidence:
    """Store one bounded safe summary of a successful discovery result."""

    tool_name: str
    paths: tuple[str, ...]
    metadata: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ActionFailureEvidence:
    """Store one bounded safe controlled-action failure summary."""

    tool_name: str
    path: str | None
    error_message: str
    phase: CodingPhase
    attempt_number: int


@dataclass(frozen=True, slots=True)
class _ValidationFailureEvidence:
    """Store one explicit bounded sanitized validation failure."""

    tool_name: str
    result_status: str
    command: tuple[str, ...] | None
    exit_code: int | None
    stdout_excerpt: str
    stderr_excerpt: str


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
    approved_workspace_paths: tuple[str, ...] = ()
    baseline_changed_paths: tuple[str, ...] = ()

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
    discovery_evidence: list[_DiscoveryEvidence] = field(default_factory=list)
    action_failure_evidence: list[_ActionFailureEvidence] = field(default_factory=list)
    discovery_summary: str = ""
    assistant_summary: str = ""
    repair_attempt_count: int = 0
    completion_continuation_count: int = 0
    controller_invocation_count: int = 0
    current_action_attempt_number: int = 0
    controlled_action_argument_validation_failures: int = 0
    last_argument_validation_tool_name: str | None = None
    baseline_changed_paths: tuple[str, ...] = ()
    baseline_unsafe_changed_path_count: int = 0
    skipped_validation_runs: list[ValidationRun] = field(default_factory=list)
    progress_event_observer: CodingProgressObserver | None = None
    model_send_trace_observer: CodingModelSendTraceObserver | None = None


def run_autonomous_coding_task(
    session: AgentSession,
    prompt: str,
    *,
    tool_approval_handler: ToolApprovalHandler,
    tool_round_observer: ToolRoundObserver | None = None,
    progress_event_observer: CodingProgressObserver | None = None,
    model_send_trace_observer: CodingModelSendTraceObserver | None = None,
    acceptance_criteria: Iterable[str] = DEFAULT_CODING_ACCEPTANCE_CRITERIA,
    limits: CodingWorkflowLimits = DEFAULT_CODING_WORKFLOW_LIMITS,
) -> AutonomousCodingResult:
    """Run one deterministic discover, edit, validate, repair, and verify flow."""

    task_spec, registry, available_tools = _validate_inputs(
        session=session,
        prompt=prompt,
        tool_approval_handler=tool_approval_handler,
        tool_round_observer=tool_round_observer,
        progress_event_observer=progress_event_observer,
        model_send_trace_observer=model_send_trace_observer,
        acceptance_criteria=acceptance_criteria,
        limits=limits,
    )
    state = _WorkflowState(
        task_spec=task_spec,
        limits=limits,
        progress_event_observer=progress_event_observer,
        model_send_trace_observer=model_send_trace_observer,
    )
    try:
        (
            state.baseline_changed_paths,
            state.baseline_unsafe_changed_path_count,
        ) = _inspect_changed_paths(registry, state)
    except CompletionError as exc:
        raise _workflow_failure(
            state,
            CodingPhase.DISCOVER,
            f"baseline changed-path inspection failed: {exc}",
        ) from None

    def observe_tool_round(round_: ToolInteractionRound) -> None:
        state.rounds.append(round_)
        _capture_argument_validation_failures(state, round_)
        _capture_action_failure_evidence(state, round_)
        _emit_action_progress(state, round_)
        if tool_round_observer is not None:
            tool_round_observer(round_)

    def handle_tool_approval(request):
        decision = tool_approval_handler(request)
        if decision is ToolApprovalDecision.APPROVE:
            state.approved_action_names.append(request.invocation.tool_name)
            state.approved_action_ids.add(request.invocation.id)
        return decision

    _emit_progress(
        state,
        CodingProgressEvent(
            phase=CodingPhase.DISCOVER,
            kind=CodingProgressKind.PHASE_STARTED,
        ),
    )
    _run_discover_phase(
        session,
        state,
        available_tools,
        observe_tool_round,
        handle_tool_approval,
    )
    _emit_progress(
        state,
        CodingProgressEvent(
            phase=CodingPhase.DISCOVER,
            kind=CodingProgressKind.PHASE_COMPLETED,
        ),
    )
    _emit_progress(
        state,
        CodingProgressEvent(
            phase=CodingPhase.EDIT,
            kind=CodingProgressKind.PHASE_STARTED,
        ),
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
    _emit_validation_start(state)
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
        _emit_progress(
            state,
            CodingProgressEvent(
                phase=CodingPhase.REPAIR,
                kind=CodingProgressKind.REPAIR_STARTED,
                repair_attempt=state.repair_attempt_count,
                max_repair_attempts=limits.repair_attempts,
            ),
        )
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
        _emit_validation_start(state)
        validation_results = _run_validation_phase(
            registry,
            state,
            observe_tool_round,
            handle_tool_approval,
        )
        failed_validations = _failed_validations(validation_results)

    state.phase = CodingPhase.VERIFY
    _emit_progress(
        state,
        CodingProgressEvent(
            phase=CodingPhase.VERIFY,
            kind=CodingProgressKind.PHASE_STARTED,
        ),
    )
    verification_results = _run_verify_phase(
        registry,
        state,
        observe_tool_round,
        handle_tool_approval,
    )
    _require_verification_success(state, verification_results)
    status_result = dict(verification_results)["inspect_git_status"]
    _require_no_unexpected_changed_paths(
        state,
        status_result,
        phase=CodingPhase.VERIFY,
        context="before DONE",
    )
    final_changed_paths, _ = _changed_path_evidence(status_result)
    task_changed_path_count = len(
        set(final_changed_paths).intersection(_approved_workspace_paths(state))
    )
    _emit_progress(
        state,
        CodingProgressEvent(
            phase=CodingPhase.VERIFY,
            kind=CodingProgressKind.CHANGED_PATH_COUNT,
            changed_path_count=task_changed_path_count,
        ),
    )

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
    _emit_progress(
        state,
        CodingProgressEvent(
            phase=CodingPhase.DONE,
            kind=CodingProgressKind.DONE,
        ),
    )
    return result


def _validate_inputs(
    *,
    session: object,
    prompt: object,
    tool_approval_handler: object,
    tool_round_observer: object,
    progress_event_observer: object,
    model_send_trace_observer: object,
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
    if progress_event_observer is not None and not callable(progress_event_observer):
        raise ConfigurationError(
            "autonomous coding progress event observer must be callable."
        )
    if model_send_trace_observer is not None and not callable(
        model_send_trace_observer
    ):
        raise ConfigurationError(
            "autonomous coding model-send trace observer must be callable."
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
    phase_start = len(state.rounds)
    response: ChatResponse | None = None
    try:
        allowed_tool_names = available_tools.intersection(_READ_ONLY_TOOL_NAMES)
        _emit_model_send_trace(
            state,
            phase=CodingPhase.DISCOVER,
            allowed_tool_names=allowed_tool_names,
            continuation=1,
            decision_mode=False,
        )
        response = session.send(
            _build_phase_prompt(
                state,
                outstanding=(
                    "Inspect only the repository information needed for the objective.",
                    "Do not edit files or run validation.",
                    "Return a concise discovery completion when inspection is sufficient.",
                ),
            ),
            allowed_tool_names=allowed_tool_names,
            max_tool_rounds=state.limits.discover_tool_rounds,
            tool_round_observer=observer,
            tool_approval_handler=approval_handler,
            recover_approval_preview_errors=True,
        )
    except CompletionError as exc:
        if not str(exc).startswith(_MAXIMUM_ROUNDS_ERROR):
            raise _workflow_failure(
                state,
                CodingPhase.DISCOVER,
                f"discovery completion failed: {exc}",
            ) from None
    finally:
        _capture_discovery_evidence(state, state.rounds[phase_start:])

    if response is not None:
        state.discovery_summary = _bounded_prompt_summary(response.text)


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
    state.controlled_action_argument_validation_failures = 0
    state.last_argument_validation_tool_name = None
    local_continuations = 0
    # True whenever a successful workspace change (from a normal completion or
    # from tool-round exhaustion) is preserved and still needs completion
    # confirmation; a failed/denied mutation attempt always clears it, but pure
    # read-only exhaustion never clears it on its own.
    awaiting_change_confirmation = False
    # True only while the pending confirmation was preserved across a
    # tool-round exhaustion, used solely to word the next outstanding prompt.
    change_confirmation_via_exhaustion = False
    decision_mode = False
    outstanding = (
        "Apply at least one successful controlled workspace change.",
        "Use repository evidence already gathered instead of restarting discovery.",
        "Do not run validation or Git verification; the controller owns those phases.",
    )

    while True:
        state.current_action_attempt_number = (
            state.repair_attempt_count
            if phase is CodingPhase.REPAIR
            else local_continuations + 1
        )
        call_start = len(state.rounds)
        prompt = (
            _build_repair_prompt(state, failed_validations, outstanding)
            if phase is CodingPhase.REPAIR
            else _build_phase_prompt(state, outstanding=outstanding)
        )
        try:
            allowed_tool_names = (
                _WORKSPACE_CHANGE_TOOL_NAMES
                if phase is CodingPhase.EDIT and decision_mode
                else _READ_ONLY_TOOL_NAMES | _WORKSPACE_CHANGE_TOOL_NAMES
            )
            _emit_model_send_trace(
                state,
                phase=phase,
                allowed_tool_names=available_tools.intersection(allowed_tool_names),
                continuation=local_continuations + 1,
                decision_mode=phase is CodingPhase.EDIT and decision_mode,
            )
            response = session.send(
                prompt,
                allowed_tool_names=available_tools.intersection(allowed_tool_names),
                tool_round_observer=observer,
                tool_approval_handler=approval_handler,
                recover_approval_preview_errors=True,
                recover_multiple_approval_actions=True,
            )
        except CompletionError as exc:
            if str(exc).startswith("Tool argument validation recovery limit reached;"):
                raise _argument_validation_workflow_failure(
                    state,
                    phase,
                    session,
                    len(state.rounds) - call_start,
                ) from None
            if str(exc) != _MAXIMUM_ROUNDS_ERROR:
                raise _workflow_failure(
                    state,
                    phase,
                    f"model-facing phase failed: {exc}",
                ) from None
            current_call_rounds = state.rounds[call_start:]
            if _rounds_contain_successful_change(
                current_call_rounds,
                state.approved_action_ids,
            ):
                # Preserve the change and its evidence, but exhaustion alone is
                # never proof of completion; require a bounded continuation.
                state.assistant_summary = _MAXIMUM_ROUNDS_CHANGE_SUMMARY
                awaiting_change_confirmation = True
                decision_mode = False
                change_confirmation_via_exhaustion = True
                incomplete_reason = (
                    "the model-facing call exhausted its tool-round budget; a "
                    "successful workspace change from that call was preserved "
                    "but exhaustion is not evidence that editing is complete"
                )
            elif _rounds_contain_workspace_change_attempt(current_call_rounds):
                # An attempted mutation failed or was denied before exhaustion;
                # never let that stand in for the pending confirmation.
                awaiting_change_confirmation = False
                decision_mode = False
                incomplete_reason = (
                    "the model-facing call exhausted its tool-round budget without "
                    "completing the required workspace change"
                )
            else:
                # Pure read-only exhaustion never clears a confirmation already
                # pending from an earlier preserved change.
                if phase is CodingPhase.EDIT and not awaiting_change_confirmation:
                    decision_mode = bool(current_call_rounds)
                incomplete_reason = (
                    "the model-facing call exhausted its tool-round budget while "
                    "only performing read-only inspection"
                )
        else:
            state.assistant_summary = response.text
            current_call_rounds = state.rounds[call_start:]
            if _rounds_contain_successful_change(
                current_call_rounds,
                state.approved_action_ids,
            ):
                # A successful mutation proves work happened, not that all
                # requested editing work is complete; require confirmation.
                awaiting_change_confirmation = True
                decision_mode = False
                change_confirmation_via_exhaustion = False
                incomplete_reason = (
                    "a successful workspace change was applied but has not yet "
                    "been confirmed complete without another workspace-change "
                    "attempt"
                )
            elif _rounds_contain_workspace_change_attempt(current_call_rounds):
                # A mutation was attempted and failed/denied; do not let prose
                # from this response stand in for confirmation.
                awaiting_change_confirmation = False
                decision_mode = False
                incomplete_reason = "no successful new workspace change was observed"
            elif awaiting_change_confirmation:
                # The continuation confirmed completion without inventing a new
                # mutation; the previously preserved change may proceed.
                return True
            else:
                if phase is CodingPhase.EDIT:
                    decision_mode = decision_mode or bool(current_call_rounds)
                incomplete_reason = "no successful new workspace change was observed"

        if (
            state.controlled_action_argument_validation_failures
            >= MAX_CONTROLLED_ACTION_ARGUMENT_VALIDATION_FAILURES
        ):
            raise _argument_validation_workflow_failure(
                state,
                phase,
                session,
                len(current_call_rounds),
            )

        if local_continuations >= continuation_limit:
            if phase is CodingPhase.EDIT:
                raise _workflow_failure(
                    state,
                    phase,
                    f"completion continuation limit reached after {incomplete_reason}",
                )
            return False

        local_continuations += 1
        state.completion_continuation_count += 1
        if awaiting_change_confirmation:
            outstanding = (
                (
                    f"The previous {phase.value} call exhausted its tool-round budget."
                    if change_confirmation_via_exhaustion
                    else "A successful workspace change was already applied."
                ),
                "That change was preserved; do not discard or roll it back.",
                (
                    "Tool-round exhaustion is not evidence that editing is complete."
                    if change_confirmation_via_exhaustion
                    else (
                        "A successful workspace change alone is not evidence "
                        "that editing is complete."
                    )
                ),
                "Review the remaining original acceptance criteria below.",
                "If work remains, make another controlled workspace change now.",
                "If the requested editing work is already complete, finish this "
                "response without inventing another workspace change.",
            )
        else:
            if phase is CodingPhase.EDIT and decision_mode:
                outstanding = (
                    f"{phase.value} is incomplete because {incomplete_reason}.",
                    "Repository evidence has already been gathered; further read-only inspection is intentionally unavailable.",
                    "Use the evidence already gathered to make a controlled workspace change now if you can do so safely.",
                    "If you cannot safely make a change from that evidence, finish without a tool call.",
                    "Assistant prose is not evidence of a workspace change.",
                )
            else:
                outstanding = (
                    f"{phase.value} is incomplete because {incomplete_reason}.",
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

    results: list[tuple[str, ToolResult]] = []
    format_targets = tuple(
        path for path in _approved_workspace_paths(state) if path.endswith(".py")
    )
    if not format_targets:
        sequence_index = _tool_result_count(state.rounds) + 1
        skipped = ToolResult(
            invocation_id=f"controller-skipped-{sequence_index}-run_ruff_format",
            status="success",
            output={
                "tool": "run_ruff_format",
                "path": ".",
                "exit_code": 0,
                "skipped": True,
            },
        )
        state.skipped_validation_runs.append(
            ValidationRun(
                tool_name="run_ruff_format",
                result_status="success",
                exit_code=None,
                sequence_index=sequence_index,
                target_paths=(),
                skipped=True,
            )
        )
        results.append(("run_ruff_format", skipped))
        _emit_validation_result(state, "run_ruff_format", skipped, skipped=True)
    else:
        for path in format_targets:
            result = _run_validation_tool(
                registry,
                state,
                "run_ruff_format",
                {"path": path},
                observer,
                approval_handler,
            )
            results.append(("run_ruff_format", result))
            _emit_validation_result(state, "run_ruff_format", result)
            _require_no_unexpected_changed_paths(
                state,
                _inspect_changed_paths_result(registry, state),
                phase=CodingPhase.VALIDATE,
                context="after run_ruff_format",
            )

    for tool_name in ("run_ruff_check", "run_pytest"):
        result = _run_validation_tool(
            registry,
            state,
            tool_name,
            {"path": "."},
            observer,
            approval_handler,
        )
        results.append((tool_name, result))
        _emit_validation_result(state, tool_name, result)
        _require_no_unexpected_changed_paths(
            state,
            _inspect_changed_paths_result(registry, state),
            phase=CodingPhase.VALIDATE,
            context=f"after {tool_name}",
        )
    return tuple(results)


def _emit_validation_start(state: _WorkflowState) -> None:
    """Emit one stable validation phase transition."""

    _emit_progress(
        state,
        CodingProgressEvent(
            phase=CodingPhase.VALIDATE,
            kind=CodingProgressKind.PHASE_STARTED,
            repair_attempt=state.repair_attempt_count,
            max_repair_attempts=state.limits.repair_attempts,
        ),
    )


def _emit_validation_result(
    state: _WorkflowState,
    tool_name: str,
    result: ToolResult,
    *,
    skipped: bool = False,
) -> None:
    """Emit one typed validation outcome from structured tool evidence."""

    _emit_progress(
        state,
        CodingProgressEvent(
            phase=CodingPhase.VALIDATE,
            kind=CodingProgressKind.VALIDATION_RESULT,
            tool_name=tool_name,
            result_status=str(result.status),
            exit_code=None if skipped else _validation_exit_code(result.output),
            validation_summary=(
                _pytest_progress_summary(result.output)
                if tool_name == "run_pytest"
                else None
            ),
            repair_attempt=state.repair_attempt_count,
            max_repair_attempts=state.limits.repair_attempts,
            skipped=skipped,
        ),
    )


def _pytest_progress_summary(output: object) -> str | None:
    """Extract only fixed pytest outcome counts from bounded command output."""

    if not isinstance(output, dict):
        return None
    stdout = output.get("stdout")
    if not isinstance(stdout, str):
        return None
    summary_pattern = re.compile(
        r"(?<!\w)(\d+) "
        r"(failed|passed|skipped|xfailed|xpassed|error|errors|deselected|warnings?)"
        r"(?!\w)"
    )
    for line in reversed(stdout.splitlines()):
        counts = summary_pattern.findall(line)
        if counts:
            return ", ".join(f"{count} {label}" for count, label in counts)[:200]
    return None


def _emit_action_progress(
    state: _WorkflowState,
    round_: ToolInteractionRound,
) -> None:
    """Emit safe successful and failed controlled-action evidence."""

    if state.phase not in {CodingPhase.EDIT, CodingPhase.REPAIR}:
        return
    for invocation, result in zip(
        round_.response.tool_invocations,
        round_.results,
        strict=True,
    ):
        if invocation.tool_name not in _WORKSPACE_CHANGE_TOOL_NAMES:
            continue
        event_fields = {
            "phase": state.phase,
            "repair_attempt": (
                state.repair_attempt_count if state.phase is CodingPhase.REPAIR else 0
            ),
            "max_repair_attempts": state.limits.repair_attempts,
        }
        if (
            invocation.id in state.approved_action_ids
            and result.status == "success"
            and _workspace_change_result_applied(
                invocation.tool_name,
                invocation.arguments,
                result.output,
            )
        ):
            for path in sorted(_changed_paths_from_workspace_result(result.output)):
                if _is_safe_prompt_path(path):
                    _emit_progress(
                        state,
                        CodingProgressEvent(
                            kind=CodingProgressKind.WORKSPACE_CHANGED,
                            path=path,
                            **event_fields,
                        ),
                    )
            continue
        if result.status != "error":
            continue
        evidence = _bounded_action_failure_evidence(
            invocation.tool_name,
            invocation.arguments,
            result.error,
            phase=state.phase,
            attempt_number=state.current_action_attempt_number,
        )
        _emit_progress(
            state,
            CodingProgressEvent(
                kind=(
                    CodingProgressKind.ACTION_ARGUMENTS_REJECTED
                    if _is_argument_validation_error(
                        invocation.tool_name,
                        result.error,
                    )
                    else CodingProgressKind.ACTION_FAILED
                ),
                path=evidence.path,
                reason=evidence.error_message,
                later_action_rejected=(
                    evidence.error_message.startswith("Approval preview failed for ")
                    and _rounds_contain_successful_change(
                        state.rounds[:-1],
                        state.approved_action_ids,
                    )
                ),
                **event_fields,
            ),
        )


def _capture_argument_validation_failures(
    state: _WorkflowState,
    round_: ToolInteractionRound,
) -> None:
    """Count only model-correctable schema failures for controlled actions."""

    if state.phase not in {CodingPhase.EDIT, CodingPhase.REPAIR}:
        return
    for invocation, result in zip(
        round_.response.tool_invocations,
        round_.results,
        strict=True,
    ):
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
            state.controlled_action_argument_validation_failures = 0
            state.last_argument_validation_tool_name = None
            continue
        if (
            invocation.tool_name in _WORKSPACE_CHANGE_TOOL_NAMES
            and result.status == "error"
            and _is_argument_validation_error(invocation.tool_name, result.error)
        ):
            state.controlled_action_argument_validation_failures += 1
            state.last_argument_validation_tool_name = invocation.tool_name


def _is_argument_validation_error(tool_name: str, error: str | None) -> bool:
    """Identify one deterministic registry-produced schema failure."""

    return error is not None and error.startswith(
        f"Tool '{tool_name}' argument validation failed:"
    )


def _argument_validation_workflow_failure(
    state: _WorkflowState,
    phase: CodingPhase,
    session: AgentSession,
    tool_round_count: int,
) -> CompletionError:
    """Return one sanitized terminal failure for bounded argument recovery."""

    tool_name = state.last_argument_validation_tool_name or "unavailable"
    return _workflow_failure(
        state,
        phase,
        "controlled action argument recovery limit reached; "
        f"phase={phase.value}; "
        f"tool={tool_name}; "
        "argument_validation_failures="
        f"{state.controlled_action_argument_validation_failures}; "
        f"tool_round_count={tool_round_count}/{session.max_tool_rounds}; "
        "correction_opportunity_provided=true",
    )


def _emit_progress(
    state: _WorkflowState,
    event: CodingProgressEvent,
) -> None:
    """Notify the optional application-owned progress observer."""

    if state.progress_event_observer is not None:
        state.progress_event_observer(event)


def _emit_model_send_trace(
    state: _WorkflowState,
    *,
    phase: CodingPhase,
    allowed_tool_names: Iterable[str],
    continuation: int,
    decision_mode: bool,
) -> None:
    """Publish bounded metadata immediately before one model-facing send."""

    if state.model_send_trace_observer is not None:
        state.model_send_trace_observer(
            CodingModelSendTrace(
                phase=phase,
                allowed_tool_names=tuple(sorted(allowed_tool_names)),
                continuation=continuation,
                decision_mode=decision_mode,
            )
        )


def _run_validation_tool(
    registry: ToolRegistry,
    state: _WorkflowState,
    tool_name: str,
    arguments: JSONObject,
    observer: ToolRoundObserver,
    approval_handler: ToolApprovalHandler,
) -> ToolResult:
    """Invoke one fixed validation and preserve phase-specific failures."""

    try:
        return _execute_controller_invocation(
            registry,
            state,
            tool_name,
            arguments,
            observer,
            approval_handler,
        )
    except CompletionError as exc:
        raise _workflow_failure(
            state,
            CodingPhase.VALIDATE,
            f"{tool_name} could not execute: {exc}",
        ) from None


def _inspect_changed_paths_result(
    registry: ToolRegistry,
    state: _WorkflowState,
) -> ToolResult:
    """Run one hidden read-only status inspection for controller safety."""

    state.controller_invocation_count += 1
    invocation = ToolInvocation(
        id=(
            f"controller-{state.controller_invocation_count}-inspect_git_status-safety"
        ),
        tool_name="inspect_git_status",
        arguments={},
    )
    return registry.execute(invocation)


def _inspect_changed_paths(
    registry: ToolRegistry,
    state: _WorkflowState,
) -> tuple[tuple[str, ...], int]:
    """Return validated typed changed-path evidence from read-only Git status."""

    return _changed_path_evidence(_inspect_changed_paths_result(registry, state))


def _changed_path_evidence(result: ToolResult) -> tuple[tuple[str, ...], int]:
    """Validate one Git status result without accepting unsafe path values."""

    if result.status != "success" or not isinstance(result.output, dict):
        raise CompletionError("Git status inspection did not succeed.")
    raw_paths = result.output.get("changed_paths")
    unsafe_count = result.output.get("unsafe_changed_path_count")
    if (
        not isinstance(raw_paths, list)
        or any(
            not isinstance(path, str) or not _is_safe_prompt_path(path)
            for path in raw_paths
        )
        or isinstance(unsafe_count, bool)
        or not isinstance(unsafe_count, int)
        or unsafe_count < 0
    ):
        raise CompletionError("Git status returned invalid changed-path evidence.")
    paths = tuple(sorted(set(raw_paths)))
    if len(paths) != len(raw_paths):
        raise CompletionError("Git status returned invalid changed-path evidence.")
    return paths, unsafe_count


def _require_no_unexpected_changed_paths(
    state: _WorkflowState,
    status_result: ToolResult,
    *,
    phase: CodingPhase,
    context: str,
) -> None:
    """Reject paths outside the baseline and successful approved actions."""

    try:
        current_paths, unsafe_count = _changed_path_evidence(status_result)
    except CompletionError as exc:
        raise _workflow_failure(
            state,
            phase,
            f"changed-path inspection {context} failed: {exc}",
        ) from None

    allowed_paths = set(state.baseline_changed_paths)
    allowed_paths.update(_approved_workspace_paths(state))
    unexpected = sorted(set(current_paths) - allowed_paths)
    if unexpected:
        raise _workflow_failure(
            state,
            phase,
            f"unexpected changed paths {context}: {', '.join(unexpected)}",
        )
    if unsafe_count > state.baseline_unsafe_changed_path_count:
        raise _workflow_failure(
            state,
            phase,
            f"unexpected unsafe changed path {context}",
        )


def _approved_workspace_paths(state: _WorkflowState) -> tuple[str, ...]:
    """Return exact safe paths changed by successful approved task actions."""

    paths: set[str] = set()
    for round_ in state.rounds:
        for invocation, result in zip(
            round_.response.tool_invocations,
            round_.results,
            strict=True,
        ):
            if (
                invocation.tool_name not in _WORKSPACE_CHANGE_TOOL_NAMES
                or invocation.id not in state.approved_action_ids
                or result.status != "success"
                or not _workspace_change_result_applied(
                    invocation.tool_name,
                    invocation.arguments,
                    result.output,
                )
            ):
                continue
            paths.update(_changed_paths_from_workspace_result(result.output))
    return tuple(sorted(paths))


def _tool_result_count(rounds: Iterable[ToolInteractionRound]) -> int:
    """Count completed tool results across interaction rounds."""

    return sum(len(round_.results) for round_ in rounds)


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
    untracked = diff_output.get("untracked", "")
    if (
        not isinstance(unstaged, str)
        or not isinstance(staged, str)
        or not isinstance(untracked, str)
    ):
        raise _workflow_failure(
            state,
            CodingPhase.VERIFY,
            "inspect_git_diff returned invalid evidence",
        )
    if not unstaged.strip() and not staged.strip() and not untracked.strip():
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


def _rounds_contain_workspace_change_attempt(
    rounds: Iterable[ToolInteractionRound],
) -> bool:
    """Return whether rounds requested any workspace-change tool, applied or not."""

    return any(
        invocation.tool_name in _WORKSPACE_CHANGE_TOOL_NAMES
        for round_ in rounds
        for invocation in round_.response.tool_invocations
    )


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
    approved_workspace_paths: set[str] = set()

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
                approved_workspace_paths.update(
                    _changed_paths_from_workspace_result(result.output)
                )

            if invocation.tool_name in _VALIDATION_TOOL_NAMES:
                validation_runs.append(
                    ValidationRun(
                        tool_name=invocation.tool_name,
                        result_status=str(result.status),
                        exit_code=_validation_exit_code(result.output),
                        sequence_index=sequence_index,
                        target_paths=_validation_target_paths(result.output),
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
    validation_runs.extend(state.skipped_validation_runs)
    validation_runs.sort(
        key=lambda run: (
            run.sequence_index,
            _VALIDATION_TOOL_NAMES.index(run.tool_name),
        )
    )
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
        approved_workspace_paths=tuple(sorted(approved_workspace_paths)),
        baseline_changed_paths=state.baseline_changed_paths,
    )


def _validation_target_paths(output: object) -> tuple[str, ...]:
    """Extract one safe validation target path from structured evidence."""

    if not isinstance(output, dict):
        return ()
    path = output.get("path")
    if not isinstance(path, str) or path == "." or not _is_safe_prompt_path(path):
        return ()
    return (path,)


def _changed_paths_from_workspace_result(output: object) -> tuple[str, ...]:
    """Extract effective canonical paths from successful approved action output."""

    if not isinstance(output, dict):
        return ()
    candidates: list[dict[str, object]] = [output]
    changes = output.get("changes")
    if isinstance(changes, list):
        candidates = [change for change in changes if isinstance(change, dict)]

    paths = []
    for candidate in candidates:
        path = candidate.get("path")
        operation = candidate.get("operation")
        changed_lines = candidate.get("changed_lines")
        if isinstance(path, str) and (
            operation == "create"
            or (
                isinstance(changed_lines, int)
                and not isinstance(changed_lines, bool)
                and changed_lines > 0
            )
        ):
            paths.append(path)
    return tuple(paths)


def _build_phase_prompt(
    state: _WorkflowState,
    *,
    outstanding: Iterable[str],
) -> str:
    """Build an explicit bounded model-facing phase prompt."""

    completed = _completed_evidence_lines(state)
    acceptance_criteria = _numbered_lines(
        _sanitize_prompt_text(criterion)
        for criterion in state.task_spec.acceptance_criteria
    )
    action_failure_section = ""
    if state.action_failure_evidence:
        action_failure_section = (
            "\n\nAction failure evidence:\n"
            f"{_numbered_lines(_format_action_failure_evidence(item) for item in state.action_failure_evidence)}\n\n"
            "Action failure recovery:\n"
            "The previous action did not change the workspace. The model must "
            "reread the target before trying again. "
            f"{_COMPLETE_FILE_REWRITE_GUIDANCE} For a small exact change, you "
            "may use apply_text_replacement with a short literal snippet copied "
            "exactly from the latest file."
        )
    return (
        "Continue one externally controlled deterministic coding workflow.\n\n"
        f"Original objective:\n{_sanitize_prompt_text(state.task_spec.objective)}\n\n"
        "Acceptance criteria:\n"
        f"{acceptance_criteria}\n\n"
        f"Current phase: {state.phase.value}\n\n"
        "Completed phase evidence:\n"
        f"{_numbered_lines(completed)}"
        f"{action_failure_section}\n\n"
        "Outstanding requirements:\n"
        f"{_numbered_lines(outstanding)}\n\n"
        f"{_CONTROLLED_EDIT_SELECTION_GUIDANCE}\n\n"
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

    failure_evidence = _bounded_validation_failure_evidence(failed_validations)
    formatted_failures = _format_validation_failure_evidence(failure_evidence)

    changed_paths = _safe_changed_paths(state.rounds)
    path_evidence = ", ".join(changed_paths) if changed_paths else "unavailable"
    base = _build_phase_prompt(state, outstanding=outstanding)
    return (
        f"{base}\n\n"
        f"Repair attempt: {state.repair_attempt_count}/"
        f"{state.limits.repair_attempts}\n"
        "Failed validation evidence:\n"
        f"{formatted_failures}\n"
        f"Current changed-file paths: {path_evidence}\n"
        "Repair requirements:\n"
        "- You must resolve every listed validation failure.\n"
        "- Do not ignore dynamic runtime requirements in validation output.\n"
        "- Apply another successful controlled workspace change.\n"
        "- Do not call Ruff or pytest directly because the controller owns "
        "validation.\n"
        f"- {_COMPLETE_FILE_REWRITE_GUIDANCE}\n"
        "- The controller will run the full validation sequence afterward.\n"
        "This repair attempt requires another successful controlled workspace "
        "change before validation can run again."
    )


def _bounded_validation_failure_evidence(
    failed_validations: tuple[tuple[str, ToolResult], ...],
) -> tuple[_ValidationFailureEvidence, ...]:
    """Build fair per-stream excerpts within one combined prompt budget."""

    evidence = []
    raw_commands: list[tuple[int, tuple[str, ...]]] = []
    raw_streams: list[tuple[int, str, str]] = []
    for index, (tool_name, result) in enumerate(failed_validations):
        stdout = ""
        stderr = ""
        command: tuple[str, ...] | None = None
        if isinstance(result.output, dict):
            raw_stdout = result.output.get("stdout")
            raw_stderr = result.output.get("stderr")
            if isinstance(raw_stdout, str):
                stdout = _sanitize_validation_output(raw_stdout)
            if isinstance(raw_stderr, str):
                stderr = _sanitize_validation_output(raw_stderr)
            raw_command = result.output.get("command")
            if (
                isinstance(raw_command, list)
                and raw_command
                and all(isinstance(argument, str) for argument in raw_command)
            ):
                command = tuple(raw_command)
        if not stderr and result.error:
            stderr = _sanitize_validation_output(result.error)

        evidence.append(
            _ValidationFailureEvidence(
                tool_name=tool_name,
                result_status=str(result.status),
                command=None,
                exit_code=_validation_exit_code(result.output),
                stdout_excerpt="",
                stderr_excerpt="",
            )
        )
        if command is not None:
            raw_commands.append((index, command))
        if stdout:
            raw_streams.append((index, "stdout", stdout))
        if stderr:
            raw_streams.append((index, "stderr", stderr))

    for evidence_index, command in raw_commands:
        current = evidence[evidence_index]
        candidate = _ValidationFailureEvidence(
            tool_name=current.tool_name,
            result_status=current.result_status,
            command=command,
            exit_code=current.exit_code,
            stdout_excerpt=current.stdout_excerpt,
            stderr_excerpt=current.stderr_excerpt,
        )
        candidate_evidence = list(evidence)
        candidate_evidence[evidence_index] = candidate
        if (
            len(_format_validation_failure_evidence(tuple(candidate_evidence)))
            <= MAX_REPAIR_VALIDATION_EVIDENCE_CHARACTERS
        ):
            evidence[evidence_index] = candidate

    base_length = len(_format_validation_failure_evidence(tuple(evidence)))
    remaining = max(
        0,
        MAX_REPAIR_VALIDATION_EVIDENCE_CHARACTERS - base_length,
    )
    for stream_index, (evidence_index, stream_name, stream) in enumerate(raw_streams):
        remaining_streams = len(raw_streams) - stream_index
        limit = min(
            MAX_REPAIR_VALIDATION_FIELD_CHARACTERS,
            remaining // remaining_streams,
        )
        excerpt = _truncate_repair_field(stream, limit)
        current = evidence[evidence_index]
        evidence[evidence_index] = _ValidationFailureEvidence(
            tool_name=current.tool_name,
            result_status=current.result_status,
            command=current.command,
            exit_code=current.exit_code,
            stdout_excerpt=(
                excerpt if stream_name == "stdout" else current.stdout_excerpt
            ),
            stderr_excerpt=(
                excerpt if stream_name == "stderr" else current.stderr_excerpt
            ),
        )
        remaining -= len(excerpt)
    return tuple(evidence)


def _truncate_repair_field(value: str, limit: int) -> str:
    """Truncate one sanitized validation stream with a deterministic marker."""

    if len(value) <= limit:
        return value
    marker = "\n[truncated]"
    if limit <= len(marker):
        return marker[:limit]
    return value[: limit - len(marker)] + marker


def _format_validation_failure_evidence(
    evidence: tuple[_ValidationFailureEvidence, ...],
) -> str:
    """Render every validation failure with all required explicit fields."""

    if not evidence:
        return "None."
    records = []
    for index, item in enumerate(evidence, start=1):
        exit_code = str(item.exit_code) if item.exit_code is not None else "unavailable"
        command = (
            json.dumps(
                item.command,
                ensure_ascii=True,
                separators=(",", ":"),
            )
            if item.command is not None
            else "[unavailable]"
        )
        records.append(
            f"Validation failure {index}:\n"
            f"tool_name={item.tool_name}\n"
            f"result_status={item.result_status}\n"
            f"command={command}\n"
            f"exit_code={exit_code}\n"
            "stdout_excerpt:\n"
            f"{item.stdout_excerpt or '[empty]'}\n"
            "stderr_excerpt:\n"
            f"{item.stderr_excerpt or '[empty]'}"
        )
    return "\n\n".join(records)


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
    lines.extend(
        f"Discovery evidence: {_format_discovery_evidence(evidence)}"
        for evidence in state.discovery_evidence
    )
    if state.discovery_summary:
        lines.append(f"Discovery summary: {state.discovery_summary}")
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


def _capture_discovery_evidence(
    state: _WorkflowState,
    rounds: Iterable[ToolInteractionRound],
) -> None:
    """Retain bounded path and metadata evidence even for rolled-back turns."""

    combined_characters = sum(
        len(_format_discovery_evidence(evidence))
        for evidence in state.discovery_evidence
    )
    for round_ in rounds:
        for invocation, result in zip(
            round_.response.tool_invocations,
            round_.results,
            strict=True,
        ):
            if (
                len(state.discovery_evidence) >= MAX_DISCOVERY_EVIDENCE_ITEMS
                or result.status != "success"
                or not isinstance(result.output, dict)
            ):
                continue
            evidence = _bounded_discovery_evidence(
                invocation.tool_name,
                result.output,
            )
            formatted = _format_discovery_evidence(evidence)
            if (
                not evidence.paths
                and not evidence.metadata
                or combined_characters + len(formatted)
                > MAX_DISCOVERY_EVIDENCE_CHARACTERS
            ):
                continue
            state.discovery_evidence.append(evidence)
            combined_characters += len(formatted)


def _capture_action_failure_evidence(
    state: _WorkflowState,
    round_: ToolInteractionRound,
) -> None:
    """Retain only bounded safe failures for controlled workspace actions."""

    if state.phase not in {CodingPhase.EDIT, CodingPhase.REPAIR}:
        return

    for invocation, result in zip(
        round_.response.tool_invocations,
        round_.results,
        strict=True,
    ):
        if invocation.tool_name not in _WORKSPACE_CHANGE_TOOL_NAMES:
            continue
        if result.status == "success" and _workspace_change_result_applied(
            invocation.tool_name,
            invocation.arguments,
            result.output,
        ):
            _clear_obsolete_action_failure_evidence(state, result.output)
            continue
        if result.status != "error":
            continue

        evidence = _bounded_action_failure_evidence(
            invocation.tool_name,
            invocation.arguments,
            result.error,
            phase=state.phase,
            attempt_number=state.current_action_attempt_number,
        )
        state.action_failure_evidence.append(evidence)
        while (
            len(state.action_failure_evidence) > MAX_ACTION_FAILURE_EVIDENCE_ITEMS
            or sum(
                len(_format_action_failure_evidence(item))
                for item in state.action_failure_evidence
            )
            > MAX_ACTION_FAILURE_EVIDENCE_CHARACTERS
        ):
            state.action_failure_evidence.pop(0)


def _bounded_action_failure_evidence(
    tool_name: str,
    arguments: object,
    error: str | None,
    *,
    phase: CodingPhase,
    attempt_number: int,
) -> _ActionFailureEvidence:
    """Build one sanitized failure item within its exact character budget."""

    path = _safe_action_failure_path(arguments)
    message = " ".join(
        _sanitize_prompt_text(error or "Controlled workspace action failed.").split()
    )
    placeholder = _ActionFailureEvidence(
        tool_name=tool_name,
        path=path,
        error_message="",
        phase=phase,
        attempt_number=attempt_number,
    )
    available = max(
        0,
        MAX_ACTION_FAILURE_EVIDENCE_ITEM_CHARACTERS
        - len(_format_action_failure_evidence(placeholder)),
    )
    return _ActionFailureEvidence(
        tool_name=tool_name,
        path=path,
        error_message=message[:available],
        phase=phase,
        attempt_number=attempt_number,
    )


def _safe_action_failure_path(arguments: object) -> str | None:
    """Extract one bounded workspace-relative action path when unambiguous."""

    if not isinstance(arguments, dict):
        return None
    candidate = arguments.get("path")
    if candidate is None:
        changes = arguments.get("changes")
        if isinstance(changes, list) and len(changes) == 1:
            change = changes[0]
            if isinstance(change, dict):
                candidate = change.get("path")
    if (
        not isinstance(candidate, str)
        or not _is_safe_prompt_path(candidate)
        or len(candidate) > MAX_ACTION_FAILURE_EVIDENCE_ITEM_CHARACTERS // 2
    ):
        return None
    return candidate


def _clear_obsolete_action_failure_evidence(
    state: _WorkflowState,
    output: object,
) -> None:
    """Clear current guidance for paths changed successfully afterward."""

    changed_paths = set(_changed_paths_from_workspace_result(output))
    if not changed_paths:
        return
    state.action_failure_evidence = [
        evidence
        for evidence in state.action_failure_evidence
        if evidence.path not in changed_paths
    ]


def _format_action_failure_evidence(evidence: _ActionFailureEvidence) -> str:
    """Format one already bounded controlled-action failure item."""

    path = evidence.path if evidence.path is not None else "unavailable"
    return (
        f"tool={evidence.tool_name}, path={path}, phase={evidence.phase.value}, "
        f"attempt={evidence.attempt_number}, error={evidence.error_message}"
    )


def _bounded_discovery_evidence(
    tool_name: str,
    output: JSONObject,
) -> _DiscoveryEvidence:
    """Extract only safe paths and scalar metadata within one item limit."""

    paths = _discovery_output_paths(output)
    metadata = _discovery_output_metadata(output)
    bounded_paths: list[str] = []
    bounded_metadata: list[str] = []

    for path in paths:
        candidate = _DiscoveryEvidence(
            tool_name=tool_name,
            paths=tuple([*bounded_paths, path]),
            metadata=tuple(bounded_metadata),
        )
        if len(_format_discovery_evidence(candidate)) > (
            MAX_DISCOVERY_EVIDENCE_ITEM_CHARACTERS
        ):
            break
        bounded_paths.append(path)

    for item in metadata:
        candidate = _DiscoveryEvidence(
            tool_name=tool_name,
            paths=tuple(bounded_paths),
            metadata=tuple([*bounded_metadata, item]),
        )
        if len(_format_discovery_evidence(candidate)) > (
            MAX_DISCOVERY_EVIDENCE_ITEM_CHARACTERS
        ):
            break
        bounded_metadata.append(item)

    return _DiscoveryEvidence(
        tool_name=tool_name,
        paths=tuple(bounded_paths),
        metadata=tuple(bounded_metadata),
    )


def _discovery_output_paths(output: JSONObject) -> tuple[str, ...]:
    """Return deterministic safe workspace paths without result body text."""

    candidates: list[str] = []
    direct_path = output.get("path")
    if isinstance(direct_path, str):
        candidates.append(direct_path)
    for collection_name in ("entries", "matches"):
        collection = output.get(collection_name)
        if not isinstance(collection, list):
            continue
        for item in collection:
            if isinstance(item, dict):
                candidate = item.get("path")
                if isinstance(candidate, str):
                    candidates.append(candidate)

    return tuple(
        dict.fromkeys(
            path
            for path in candidates
            if _is_safe_prompt_path(path) and _sanitize_prompt_text(path) == path
        )
    )


def _discovery_output_metadata(output: JSONObject) -> tuple[str, ...]:
    """Return allowlisted scalar metadata and bounded collection counts."""

    values: list[str] = []
    for key in (
        "size_bytes",
        "line_start",
        "line_end",
        "total_lines",
        "files_inspected",
        "files_skipped",
        "truncated",
    ):
        value = output.get(key)
        if isinstance(value, bool) or isinstance(value, int):
            values.append(f"{key}={value}")
    for key in ("entries", "matches"):
        value = output.get(key)
        if isinstance(value, list):
            values.append(f"{key}={len(value)}")
    return tuple(values)


def _format_discovery_evidence(evidence: _DiscoveryEvidence) -> str:
    """Format one already bounded discovery evidence item."""

    paths = ", ".join(evidence.paths) if evidence.paths else "none"
    value = f"{evidence.tool_name} | paths: {paths}"
    if evidence.metadata:
        value += " | metadata: " + ", ".join(evidence.metadata)
    return value


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


def _bounded_prompt_summary(text: str) -> str:
    """Return one sanitized single-line bounded model summary."""

    sanitized = _sanitize_prompt_text(text)
    return " ".join(sanitized.split())[:MAX_DISCOVERY_SUMMARY_CHARACTERS]


def _sanitize_prompt_text(text: str) -> str:
    """Conservatively redact generic non-validation model-facing text."""

    return _sanitize_model_facing_lines(
        text,
        _generic_prompt_line_is_sensitive,
    )


def _sanitize_validation_output(text: str) -> str:
    """Redact validation credentials while preserving safe runtime identifiers."""

    return _sanitize_model_facing_lines(
        text,
        _validation_output_line_is_sensitive,
    )


def _sanitize_model_facing_lines(
    text: str,
    sensitive_line: Callable[[str], bool],
) -> str:
    """Apply one explicit line policy and the shared absolute-path boundary."""

    safe_lines = []
    for line in text.splitlines():
        if sensitive_line(line):
            safe_lines.append("[redacted sensitive content]")
        else:
            safe_lines.append(_ABSOLUTE_PATH_PATTERN.sub("[absolute-path]", line))
    return "\n".join(safe_lines)


def _generic_prompt_line_is_sensitive(line: str) -> bool:
    """Preserve the conservative generic sensitive-line policy."""

    return _GENERIC_SENSITIVE_LINE_PATTERN.search(line) is not None


def _validation_output_line_is_sensitive(line: str) -> bool:
    """Identify credentials without treating every token identifier as secret."""

    if (
        _VALIDATION_ENV_REFERENCE_PATTERN.search(line)
        or _VALIDATION_AUTHORIZATION_BEARER_PATTERN.search(line)
        or _VALIDATION_BEARER_VALUE_PATTERN.search(line)
        or _VALIDATION_OBVIOUS_SECRET_VALUE_PATTERN.search(line)
    ):
        return True
    return any(
        _VALIDATION_CREDENTIAL_NAME_PATTERN.fullmatch(assignment.group("name"))
        is not None
        for assignment in _VALIDATION_ASSIGNMENT_PATTERN.finditer(line)
    )


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
        and (latest.skipped or latest.exit_code == 0)
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

    progress_reason = " ".join(_sanitize_prompt_text(reason).split())[
        :MAX_PROGRESS_REASON_CHARACTERS
    ]
    _emit_progress(
        state,
        CodingProgressEvent(
            phase=phase,
            kind=CodingProgressKind.TERMINAL_FAILURE,
            reason=progress_reason.rstrip("."),
            repair_attempt=state.repair_attempt_count,
            max_repair_attempts=state.limits.repair_attempts,
            workspace_preserved=True,
        ),
    )
    return CompletionError(
        f"Deterministic coding failed in phase {phase.value}: {reason}. "
        f"repair_attempts={state.repair_attempt_count}, "
        f"completion_continuations={state.completion_continuation_count}."
    )
