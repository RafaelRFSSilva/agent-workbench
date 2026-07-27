"""Provider-independent tool-calling execution loop."""

from collections.abc import Callable
from dataclasses import replace

from agent_workbench.errors import CompletionError, ConfigurationError
from agent_workbench.messages import ChatRequest, ChatResponse, ToolInteractionRound
from agent_workbench.providers.base import ChatProvider
from agent_workbench.tool_registry import ToolRegistry

type ToolRoundObserver = Callable[[ToolInteractionRound], None]


def run_tool_calling_loop(
    provider: ChatProvider,
    request: ChatRequest,
    registry: ToolRegistry,
    max_tool_rounds: int,
    tool_round_observer: ToolRoundObserver | None = None,
) -> ChatResponse:
    """Complete a request, executing requested tools until text is returned."""

    if (
        isinstance(max_tool_rounds, bool)
        or not isinstance(max_tool_rounds, int)
        or max_tool_rounds <= 0
    ):
        raise ConfigurationError("maximum tool rounds must be a positive integer.")

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

        results = tuple(
            registry.execute(invocation) for invocation in response.tool_invocations
        )
        completed_round = ToolInteractionRound(response=response, results=results)
        completed_rounds = (
            *completed_rounds,
            completed_round,
        )
        if tool_round_observer is not None:
            tool_round_observer(completed_round)
        current_request = replace(request, tool_interactions=completed_rounds)
        executed_rounds += 1
