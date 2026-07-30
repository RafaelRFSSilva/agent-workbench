"""Provider-independent tool-calling execution loop."""

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
    ToolResult,
)

type ToolRoundObserver = Callable[[ToolInteractionRound], None]

MAX_TOOL_INVOCATIONS_PER_RESPONSE = 8
_MAX_TOOL_BATCH_RECOVERIES = 1
_MAX_REPEATED_TOOL_BATCH_RECOVERIES = 1
_MAX_CONSECUTIVE_INSPECTION_ROUNDS = 6
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
_REPEATED_TOOL_BATCH_RECOVERY_INSTRUCTION = (
    "The previous response repeated the same read-only inspection tool-call batch "
    "as the immediately preceding completed round. Choose a different next "
    "operation based on the returned tool result, or respond normally if no "
    "additional tool call is needed. Do not immediately repeat an identical "
    "inspection tool name and arguments."
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


def _repeats_previous_inspection_batch(
    response: ChatResponse,
    completed_rounds: tuple[ToolInteractionRound, ...],
) -> bool:
    """Return whether a response repeats the previous read-only inspection batch."""

    if not response.tool_invocations or not completed_rounds:
        return False

    if any(
        invocation.tool_name not in _REPEATED_INSPECTION_TOOL_NAMES
        for invocation in response.tool_invocations
    ):
        return False

    previous_round = completed_rounds[-1]

    if any(result.status != "success" for result in previous_round.results):
        return False

    previous_invocations = previous_round.response.tool_invocations

    return len(response.tool_invocations) == len(previous_invocations) and all(
        current.tool_name == previous.tool_name
        and current.arguments == previous.arguments
        for current, previous in zip(
            response.tool_invocations,
            previous_invocations,
            strict=True,
        )
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
    repeated_tool_batch_recoveries = 0
    consecutive_inspection_rounds = _count_trailing_successful_inspection_rounds(
        completed_rounds
    )
    inspection_streak_recovery_pending = (
        consecutive_inspection_rounds >= _MAX_CONSECUTIVE_INSPECTION_ROUNDS
    )

    if inspection_streak_recovery_pending:
        current_request = _add_temporary_recovery_instruction(
            request,
            completed_rounds,
            _INSPECTION_STREAK_RECOVERY_INSTRUCTION,
            withhold_inspection_tools=True,
        )
        consecutive_inspection_rounds = 0

    while True:
        response = provider.complete(current_request)

        if inspection_streak_recovery_pending and any(
            invocation.tool_name in _REPEATED_INSPECTION_TOOL_NAMES
            for invocation in response.tool_invocations
        ):
            raise CompletionError(
                "The provider requested another read-only inspection tool "
                "while inspection tools were withheld during recovery."
            )

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
                withhold_inspection_tools=(inspection_streak_recovery_pending),
            )
            continue

        if _repeats_previous_inspection_batch(response, completed_rounds):
            if repeated_tool_batch_recoveries >= _MAX_REPEATED_TOOL_BATCH_RECOVERIES:
                raise CompletionError(
                    "The provider repeatedly requested the same read-only inspection tool-call batch."
                )

            repeated_tool_batch_recoveries += 1
            current_request = _add_temporary_recovery_instruction(
                request,
                completed_rounds,
                _REPEATED_TOOL_BATCH_RECOVERY_INSTRUCTION,
                withhold_inspection_tools=True,
            )
            continue

        if not response.tool_invocations:
            return response

        if executed_rounds >= max_tool_rounds:
            raise CompletionError(
                "The maximum number of tool execution rounds was exceeded."
            )

        inspection_streak_recovery_pending = False

        approval_required = tuple(
            invocation
            for invocation in response.tool_invocations
            if registry.requires_approval(invocation)
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
            registry.execute(invocation) for invocation in response.tool_invocations
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

        if _is_successful_inspection_round(completed_round):
            consecutive_inspection_rounds += 1
        else:
            consecutive_inspection_rounds = 0

        if consecutive_inspection_rounds >= _MAX_CONSECUTIVE_INSPECTION_ROUNDS:
            current_request = _add_temporary_recovery_instruction(
                request,
                completed_rounds,
                _INSPECTION_STREAK_RECOVERY_INSTRUCTION,
                withhold_inspection_tools=True,
            )
            inspection_streak_recovery_pending = True
            consecutive_inspection_rounds = 0
        else:
            current_request = replace(
                request,
                tool_interactions=completed_rounds,
            )
        executed_rounds += 1
