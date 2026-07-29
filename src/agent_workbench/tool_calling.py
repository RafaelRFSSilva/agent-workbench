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
    ToolResult,
)

type ToolRoundObserver = Callable[[ToolInteractionRound], None]

MAX_TOOL_INVOCATIONS_PER_RESPONSE = 8
_MAX_TOOL_BATCH_RECOVERIES = 1
_TOOL_BATCH_RECOVERY_INSTRUCTION = (
    "The previous response requested an unsafe tool-call batch. Retry with at "
    f"most {MAX_TOOL_INVOCATIONS_PER_RESPONSE} necessary tool calls, do not "
    "repeat the same tool with identical arguments in one response, and wait "
    "for the returned results before requesting more. If no tool call is "
    "needed, respond normally."
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


def _add_tool_batch_recovery_instruction(
    request: ChatRequest,
    completed_rounds: tuple[ToolInteractionRound, ...],
) -> ChatRequest:
    """Return one temporary corrective request without rejected tool data."""

    if request.system_prompt is None:
        corrected_system_prompt = _TOOL_BATCH_RECOVERY_INSTRUCTION
    else:
        corrected_system_prompt = (
            f"{request.system_prompt}\n\n{_TOOL_BATCH_RECOVERY_INSTRUCTION}"
        )

    return replace(
        request,
        system_prompt=corrected_system_prompt,
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

    while True:
        response = provider.complete(current_request)

        if response.tool_invocations and _requires_tool_batch_recovery(response):
            if tool_batch_recoveries >= _MAX_TOOL_BATCH_RECOVERIES:
                raise CompletionError(
                    "The provider repeatedly requested an unsafe tool-call batch."
                )

            tool_batch_recoveries += 1
            current_request = _add_tool_batch_recovery_instruction(
                request,
                completed_rounds,
            )
            continue

        if not response.tool_invocations:
            return response

        if executed_rounds >= max_tool_rounds:
            raise CompletionError(
                "The maximum number of tool execution rounds was exceeded."
            )

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

        current_request = replace(
            request,
            tool_interactions=completed_rounds,
        )
        executed_rounds += 1
