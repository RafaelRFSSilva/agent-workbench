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

    while True:
        response = provider.complete(current_request)

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
