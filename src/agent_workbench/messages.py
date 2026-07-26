"""Message and request types shared across Agent Workbench."""

from dataclasses import dataclass, field
from typing import Literal, TypedDict

from agent_workbench.context import ContextDocument
from agent_workbench.errors import ConfigurationError
from agent_workbench.generation import GenerationConfig
from agent_workbench.structured_outputs import JSONResponseFormat
from agent_workbench.tools import (
    ToolDefinition,
    ToolInvocation,
    ToolResult,
)


class Message(TypedDict):
    """Represent a conversation message exchanged with a language model."""

    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class ChatResponse:
    """Represent a provider-independent chat completion response."""

    text: str = ""
    tool_invocations: tuple[ToolInvocation, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolInteractionRound:
    """Represent one ordered tool request and execution-result round."""

    response: ChatResponse
    results: tuple[ToolResult, ...]

    def __post_init__(self) -> None:
        """Validate complete ordered associations between invocations and results."""

        invocation_ids = tuple(
            invocation.id for invocation in self.response.tool_invocations
        )

        if not invocation_ids:
            raise ConfigurationError(
                "tool interaction round must contain at least one tool invocation."
            )

        seen_invocation_ids: set[str] = set()

        for invocation_id in invocation_ids:
            if invocation_id in seen_invocation_ids:
                raise ConfigurationError(
                    "tool interaction round contains duplicate tool invocation ids: "
                    f"'{invocation_id}'."
                )

            seen_invocation_ids.add(invocation_id)

        result_ids = tuple(result.invocation_id for result in self.results)
        seen_result_ids: set[str] = set()

        for result_id in result_ids:
            if result_id in seen_result_ids:
                raise ConfigurationError(
                    "tool interaction round contains duplicate results for "
                    f"invocation '{result_id}'."
                )

            seen_result_ids.add(result_id)

        for result_id in result_ids:
            if result_id not in seen_invocation_ids:
                raise ConfigurationError(
                    "tool interaction round contains a result for unknown "
                    f"invocation '{result_id}'."
                )

        for invocation_id in invocation_ids:
            if invocation_id not in seen_result_ids:
                raise ConfigurationError(
                    "tool interaction round is missing a result for "
                    f"invocation '{invocation_id}'."
                )

        if result_ids != invocation_ids:
            raise ConfigurationError(
                "tool interaction round results must follow tool invocation order."
            )


@dataclass(frozen=True, slots=True)
class ChatRequest:
    """Represent a provider-independent chat completion request."""

    messages: list[Message]
    system_prompt: str | None = None
    context_documents: tuple[ContextDocument, ...] = ()
    generation_config: GenerationConfig = field(default_factory=GenerationConfig)
    response_format: JSONResponseFormat | None = None
    tools: tuple[ToolDefinition, ...] = ()
    tool_interactions: tuple[ToolInteractionRound, ...] = ()
