"""Provider-independent tool-calling execution loop."""

import json
from collections.abc import Callable
from dataclasses import replace

from agent_workbench.errors import CompletionError, ConfigurationError
from agent_workbench.messages import ChatRequest, ChatResponse, ToolInteractionRound
from agent_workbench.providers.base import ChatProvider
from agent_workbench.tool_registry import ToolRegistry
from agent_workbench.tools import (
    ToolApprovalDecision,
    ToolApprovalHandler,
    ToolDefinition,
    ToolInvocation,
    ToolResult,
)

type ToolRoundObserver = Callable[[ToolInteractionRound], None]

MAX_TOOL_INVOCATIONS_PER_RESPONSE = 8
_MAX_TOOL_BATCH_RECOVERIES = 1
_MAX_WITHHELD_INSPECTION_RECOVERIES = 1
_MAX_CONSECUTIVE_INSPECTION_ROUNDS = 16
_REPEATED_INSPECTION_TOOL_NAMES = frozenset(
    {
        "inspect_git_diff",
        "inspect_git_status",
        "list_files",
        "read_file",
        "search_symbols",
        "search_text",
    }
)
_TOOL_BATCH_RECOVERY_INSTRUCTION = (
    "The previous response requested an unsafe tool-call batch. Retry with at "
    f"most {MAX_TOOL_INVOCATIONS_PER_RESPONSE} necessary tool calls, do not "
    "repeat the same tool with identical arguments in one response, and wait "
    "for the returned results before requesting more. If no tool call is "
    "needed, respond normally."
)
_INSPECTION_STREAK_RECOVERY_INSTRUCTION = (
    "The previous completed rounds used only successful read-only inspection "
    "tools without transitioning to another operation. Use the inspection "
    "information already returned to perform the next necessary non-inspection "
    "operation, or respond normally if no tool call is needed."
)
_WITHHELD_INSPECTION_RECOVERY_INSTRUCTION = (
    "The previous response requested a read-only inspection tool that was not "
    "available during recovery. Do not request any read-only inspection tool. "
    "Use the inspection information already returned to perform an available "
    "non-inspection operation, or respond normally if no operation is needed."
)
_DUPLICATE_INSPECTION_ERROR = (
    "Duplicate inspection rejected: the same read-only invocation already "
    "completed successfully. Use the existing result, choose a different "
    "inspection, or produce the next controlled edit."
)


def _requires_tool_batch_recovery(response: ChatResponse) -> bool:
    """Return whether one provider response contains an unsafe tool batch."""

    invocations = response.tool_invocations

    if len(invocations) > MAX_TOOL_INVOCATIONS_PER_RESPONSE:
        return True

    for index, invocation in enumerate(invocations):
        if any(
            invocation.tool_name == previous.tool_name
            and invocation.arguments == previous.arguments
            for previous in invocations[:index]
        ):
            return True

    return False


def _inspection_signature(invocation: ToolInvocation) -> tuple[str, str]:
    """Return one deterministic read-only invocation signature."""

    return (
        invocation.tool_name,
        json.dumps(
            invocation.arguments,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
    )


def _completed_inspection_signatures(
    completed_rounds: tuple[ToolInteractionRound, ...],
) -> frozenset[tuple[str, str]]:
    """Return successful inspection signatures since non-inspection progress."""

    signatures: set[tuple[str, str]] = set()

    for round_ in reversed(completed_rounds):
        if any(
            invocation.tool_name not in _REPEATED_INSPECTION_TOOL_NAMES
            for invocation in round_.response.tool_invocations
        ):
            break

        for invocation, result in zip(
            round_.response.tool_invocations,
            round_.results,
            strict=True,
        ):
            if result.status == "success":
                signatures.add(_inspection_signature(invocation))

    return frozenset(signatures)


def _matching_completed_inspection_ids(
    response: ChatResponse,
    completed_rounds: tuple[ToolInteractionRound, ...],
) -> frozenset[str]:
    """Return inspections matching successful signatures since progress."""

    completed_signatures = _completed_inspection_signatures(completed_rounds)

    return frozenset(
        invocation.id
        for invocation in response.tool_invocations
        if invocation.tool_name in _REPEATED_INSPECTION_TOOL_NAMES
        and _inspection_signature(invocation) in completed_signatures
    )


def _duplicate_inspection_ids(
    response: ChatResponse,
    completed_rounds: tuple[ToolInteractionRound, ...],
) -> frozenset[str]:
    """Return matching inspections that should be rejected this round."""

    if response.response_repair_attempt_count > 0:
        return frozenset()

    return _matching_completed_inspection_ids(response, completed_rounds)


def _inspection_failure_diagnostics(
    response: ChatResponse,
    request: ChatRequest,
    *,
    allowed_tool_names: frozenset[str] | None,
    executed_rounds: int,
    max_tool_rounds: int,
    duplicate_count: int,
    inspection_streak_count: int,
) -> str:
    """Return sanitized lifecycle fields for one repeated-inspection failure."""

    invocation = next(
        invocation
        for invocation in response.tool_invocations
        if invocation.tool_name in _REPEATED_INSPECTION_TOOL_NAMES
    )
    requested_inspection_names = frozenset(
        invocation.tool_name
        for invocation in response.tool_invocations
        if invocation.tool_name in _REPEATED_INSPECTION_TOOL_NAMES
    )
    permitted_inspection_names = frozenset(
        definition.name
        for definition in request.tools
        if definition.name in _REPEATED_INSPECTION_TOOL_NAMES
        and (allowed_tool_names is None or definition.name in allowed_tool_names)
    )
    alternatives_available = bool(
        permitted_inspection_names - requested_inspection_names
    )

    return (
        f"requested_inspection={invocation.tool_name}; "
        f"tool_round_count={executed_rounds}/{max_tool_rounds}; "
        f"duplicate_count={duplicate_count}; "
        f"inspection_streak_count={inspection_streak_count}; "
        "response_repair_attempt_count="
        f"{response.response_repair_attempt_count}; "
        "alternative_inspection_tools_available="
        f"{str(alternatives_available).lower()}"
    )


def _is_successful_inspection_round(round_: ToolInteractionRound) -> bool:
    """Return whether one completed round only inspected successfully."""

    return (
        bool(round_.response.tool_invocations)
        and all(
            invocation.tool_name in _REPEATED_INSPECTION_TOOL_NAMES
            for invocation in round_.response.tool_invocations
        )
        and all(result.status == "success" for result in round_.results)
    )


def _count_trailing_successful_inspection_rounds(
    completed_rounds: tuple[ToolInteractionRound, ...],
) -> int:
    """Count consecutive successful inspection-only rounds at history end."""

    count = 0

    for round_ in reversed(completed_rounds):
        if not _is_successful_inspection_round(round_):
            break
        count += 1

    return count


def _without_inspection_tools(
    tools: tuple[ToolDefinition, ...],
) -> tuple[ToolDefinition, ...]:
    """Return tool definitions without read-only inspection operations."""

    return tuple(
        definition
        for definition in tools
        if definition.name not in _REPEATED_INSPECTION_TOOL_NAMES
    )


def _add_temporary_recovery_instruction(
    request: ChatRequest,
    completed_rounds: tuple[ToolInteractionRound, ...],
    instruction: str,
    *,
    withhold_inspection_tools: bool = False,
) -> ChatRequest:
    """Return one temporary corrective request without rejected tool data."""

    if request.system_prompt is None:
        corrected_system_prompt = instruction
    else:
        corrected_system_prompt = f"{request.system_prompt}\n\n{instruction}"

    corrected_tools = (
        _without_inspection_tools(request.tools)
        if withhold_inspection_tools
        else request.tools
    )

    return replace(
        request,
        system_prompt=corrected_system_prompt,
        tools=corrected_tools,
        tool_interactions=completed_rounds,
    )


def run_tool_calling_loop(
    provider: ChatProvider,
    request: ChatRequest,
    registry: ToolRegistry,
    max_tool_rounds: int,
    allowed_tool_names: frozenset[str] | None = None,
    tool_round_observer: ToolRoundObserver | None = None,
    tool_approval_handler: ToolApprovalHandler | None = None,
    recover_approval_preview_errors: bool = False,
) -> ChatResponse:
    """Complete a request, executing requested tools until text is returned."""

    if (
        isinstance(max_tool_rounds, bool)
        or not isinstance(max_tool_rounds, int)
        or max_tool_rounds <= 0
    ):
        raise ConfigurationError("maximum tool rounds must be a positive integer.")

    if not isinstance(recover_approval_preview_errors, bool):
        raise ConfigurationError("approval preview recovery must be a boolean.")

    completed_rounds = request.tool_interactions
    current_request = request
    executed_rounds = 0
    tool_batch_recoveries = 0
    duplicate_inspection_rejections = 0
    withheld_inspection_recoveries = 0
    consecutive_inspection_rounds = _count_trailing_successful_inspection_rounds(
        completed_rounds
    )
    withheld_inspection_streak_count = 0
    inspection_tools_withheld = (
        consecutive_inspection_rounds >= _MAX_CONSECUTIVE_INSPECTION_ROUNDS
    )

    if inspection_tools_withheld:
        withheld_inspection_streak_count = consecutive_inspection_rounds
        withheld_inspection_recoveries = 0
        current_request = _add_temporary_recovery_instruction(
            request,
            completed_rounds,
            _INSPECTION_STREAK_RECOVERY_INSTRUCTION,
            withhold_inspection_tools=True,
        )
        consecutive_inspection_rounds = 0

    while True:
        response = provider.complete(current_request)

        if inspection_tools_withheld and any(
            invocation.tool_name in _REPEATED_INSPECTION_TOOL_NAMES
            for invocation in response.tool_invocations
        ):
            if withheld_inspection_recoveries >= _MAX_WITHHELD_INSPECTION_RECOVERIES:
                raise CompletionError(
                    "The provider repeatedly requested a read-only inspection "
                    "tool while inspection tools were withheld during recovery; "
                    + _inspection_failure_diagnostics(
                        response,
                        current_request,
                        allowed_tool_names=allowed_tool_names,
                        executed_rounds=executed_rounds,
                        max_tool_rounds=max_tool_rounds,
                        duplicate_count=len(
                            _duplicate_inspection_ids(response, completed_rounds)
                        ),
                        inspection_streak_count=withheld_inspection_streak_count,
                    )
                )

            withheld_inspection_recoveries += 1
            current_request = _add_temporary_recovery_instruction(
                request,
                completed_rounds,
                _WITHHELD_INSPECTION_RECOVERY_INSTRUCTION,
                withhold_inspection_tools=True,
            )
            continue

        if response.tool_invocations and _requires_tool_batch_recovery(response):
            if tool_batch_recoveries >= _MAX_TOOL_BATCH_RECOVERIES:
                raise CompletionError(
                    "The provider repeatedly requested an unsafe tool-call batch."
                )

            tool_batch_recoveries += 1
            current_request = _add_temporary_recovery_instruction(
                request,
                completed_rounds,
                _TOOL_BATCH_RECOVERY_INSTRUCTION,
                withhold_inspection_tools=(inspection_tools_withheld),
            )
            continue

        if not response.tool_invocations:
            return response

        duplicate_inspection_ids = _duplicate_inspection_ids(
            response,
            completed_rounds,
        )
        matching_inspection_ids = _matching_completed_inspection_ids(
            response,
            completed_rounds,
        )

        if executed_rounds >= max_tool_rounds:
            if matching_inspection_ids:
                raise CompletionError(
                    "The maximum number of tool execution rounds was exceeded. "
                    + _inspection_failure_diagnostics(
                        response,
                        current_request,
                        allowed_tool_names=allowed_tool_names,
                        executed_rounds=executed_rounds,
                        max_tool_rounds=max_tool_rounds,
                        duplicate_count=(
                            duplicate_inspection_rejections
                            + len(matching_inspection_ids)
                        ),
                        inspection_streak_count=consecutive_inspection_rounds,
                    )
                )
            raise CompletionError(
                "The maximum number of tool execution rounds was exceeded."
            )

        inspection_tools_withheld = False
        withheld_inspection_recoveries = 0

        approval_required = tuple(
            invocation
            for invocation in response.tool_invocations
            if (
                allowed_tool_names is None or invocation.tool_name in allowed_tool_names
            )
            and registry.requires_approval(invocation)
        )

        if approval_required:
            if len(response.tool_invocations) != 1:
                raise CompletionError(
                    "Approval-required tool actions must be requested one at a time."
                )

            invocation = approval_required[0]

            try:
                approval_request = registry.create_approval_request(invocation)
            except CompletionError as exc:
                if not recover_approval_preview_errors:
                    raise

                results = (
                    ToolResult(
                        invocation_id=invocation.id,
                        status="error",
                        error=str(exc),
                    ),
                )
                completed_round = ToolInteractionRound(
                    response=response,
                    results=results,
                )
                completed_rounds = (
                    *completed_rounds,
                    completed_round,
                )

                if tool_round_observer is not None:
                    tool_round_observer(completed_round)

                current_request = replace(
                    request,
                    tool_interactions=completed_rounds,
                )
                consecutive_inspection_rounds = 0
                executed_rounds += 1
                continue

            if tool_approval_handler is None:
                raise CompletionError("Tool action approval is required.")

            try:
                decision = tool_approval_handler(approval_request)
            except Exception:
                raise CompletionError("Tool approval handler failed.") from None

            if decision is ToolApprovalDecision.DENY:
                raise CompletionError("Tool action approval was denied.")

            if decision is not ToolApprovalDecision.APPROVE:
                raise CompletionError("Tool approval decision is invalid.")

        results = tuple(
            (
                ToolResult(
                    invocation_id=invocation.id,
                    status="error",
                    error=_DUPLICATE_INSPECTION_ERROR,
                )
                if invocation.id in duplicate_inspection_ids
                else (
                    registry.execute(invocation)
                    if (
                        allowed_tool_names is None
                        or invocation.tool_name in allowed_tool_names
                    )
                    else ToolResult(
                        invocation_id=invocation.id,
                        status="error",
                        error=(
                            f"Tool '{invocation.tool_name}' is not allowed for this send."
                        ),
                    )
                )
            )
            for invocation in response.tool_invocations
        )
        duplicate_inspection_rejections += len(duplicate_inspection_ids)
        completed_round = ToolInteractionRound(
            response=response,
            results=results,
        )
        completed_rounds = (
            *completed_rounds,
            completed_round,
        )

        if tool_round_observer is not None:
            tool_round_observer(completed_round)

        if _is_successful_inspection_round(completed_round):
            consecutive_inspection_rounds += 1
        else:
            consecutive_inspection_rounds = 0

        if consecutive_inspection_rounds >= _MAX_CONSECUTIVE_INSPECTION_ROUNDS:
            withheld_inspection_streak_count = consecutive_inspection_rounds
            current_request = _add_temporary_recovery_instruction(
                request,
                completed_rounds,
                _INSPECTION_STREAK_RECOVERY_INSTRUCTION,
                withhold_inspection_tools=True,
            )
            inspection_tools_withheld = True
            withheld_inspection_recoveries = 0
            consecutive_inspection_rounds = 0
        else:
            current_request = replace(
                request,
                tool_interactions=completed_rounds,
            )
        executed_rounds += 1
