"""Provider-independent tool-calling execution loop."""

from dataclasses import replace

from agent_workbench.errors import CompletionError, ConfigurationError
from agent_workbench.messages import ChatRequest, ChatResponse, ToolInteractionRound
from agent_workbench.providers.base import ChatProvider
from agent_workbench.tool_registry import ToolRegistry


def run_tool_calling_loop(
    provider: ChatProvider,
    request: ChatRequest,
    registry: ToolRegistry,
    max_tool_rounds: int,
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
        completed_rounds = (
            *completed_rounds,
            ToolInteractionRound(response=response, results=results),
        )
        current_request = replace(request, tool_interactions=completed_rounds)
        executed_rounds += 1
