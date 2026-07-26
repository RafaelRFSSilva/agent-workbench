"""Anthropic provider implementation."""

import json
from dataclasses import dataclass
from typing import Literal, NotRequired, Protocol, TypedDict, Unpack, cast

from anthropic import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    NotFoundError,
    RateLimitError,
)

from agent_workbench.context import build_system_instructions
from agent_workbench.errors import CompletionError, ConfigurationError
from agent_workbench.messages import ChatRequest, ChatResponse
from agent_workbench.structured_outputs import JSONSchema
from agent_workbench.tools import ToolInvocation, ToolResult


class AnthropicContentBlock(Protocol):
    """Represent a content block returned by Anthropic."""

    type: str


class AnthropicTextBlock(AnthropicContentBlock, Protocol):
    """Represent a text block returned by Anthropic."""

    text: str


class AnthropicToolUseBlock(AnthropicContentBlock, Protocol):
    """Represent a tool-use block returned by Anthropic."""

    id: str
    name: str
    input: dict[str, object]


class AnthropicResponse(Protocol):
    """Represent the response fields used by the provider."""

    content: list[AnthropicContentBlock]


class AnthropicJSONOutputFormat(TypedDict):
    """Represent an Anthropic JSON Schema output format."""

    type: Literal["json_schema"]
    schema: JSONSchema


class AnthropicOutputConfig(TypedDict):
    """Represent Anthropic output configuration."""

    format: AnthropicJSONOutputFormat


class AnthropicToolDefinition(TypedDict):
    """Represent a tool supplied to Anthropic."""

    name: str
    description: str
    input_schema: JSONSchema


class AnthropicTextInputBlock(TypedDict):
    """Represent a text block supplied to Anthropic."""

    type: Literal["text"]
    text: str


class AnthropicToolUseInputBlock(TypedDict):
    """Represent a previous tool-use block supplied to Anthropic."""

    type: Literal["tool_use"]
    id: str
    name: str
    input: dict[str, object]


class AnthropicToolResultInputBlock(TypedDict):
    """Represent a previous tool-result block supplied to Anthropic."""

    type: Literal["tool_result"]
    tool_use_id: str
    content: str
    is_error: NotRequired[bool]


type AnthropicInputContentBlock = (
    AnthropicTextInputBlock | AnthropicToolUseInputBlock | AnthropicToolResultInputBlock
)


class AnthropicInputMessage(TypedDict):
    """Represent a message supplied to the Anthropic Messages API."""

    role: Literal["user", "assistant"]
    content: str | list[AnthropicInputContentBlock]


class AnthropicMessageCreateArguments(TypedDict):
    """Represent arguments supplied to the Anthropic Messages API."""

    model: str
    max_tokens: int
    messages: list[AnthropicInputMessage]
    system: NotRequired[str]
    temperature: NotRequired[float]
    top_p: NotRequired[float]
    output_config: NotRequired[AnthropicOutputConfig]
    tools: NotRequired[list[AnthropicToolDefinition]]


class AnthropicMessagesResource(Protocol):
    """Represent the Anthropic Messages API methods used by the provider."""

    def create(
        self,
        **kwargs: Unpack[AnthropicMessageCreateArguments],
    ) -> AnthropicResponse:
        """Create a message completion."""

        ...


class AnthropicClient(Protocol):
    """Represent the Anthropic client surface used by the provider."""

    messages: AnthropicMessagesResource


def _serialize_tool_result(result: ToolResult) -> str:
    """Serialize a provider-independent tool result for Anthropic input."""

    if result.status == "success":
        result_data = {
            "status": "success",
            "output": result.output,
        }
    else:
        result_data = {
            "status": "error",
            "error": result.error,
        }

    return json.dumps(
        result_data,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


@dataclass(frozen=True, slots=True)
class AnthropicProvider:
    """Generate chat completions through the Anthropic Messages API."""

    model_name: str
    client: AnthropicClient
    max_tokens: int = 1024

    @property
    def name(self) -> str:
        """Return the provider display name."""

        return "Anthropic"

    def complete(self, request: ChatRequest) -> ChatResponse:
        """Return an assistant reply for the supplied chat request."""

        request_messages: list[AnthropicInputMessage] = [
            {
                "role": message["role"],
                "content": message["content"],
            }
            for message in request.messages
        ]

        for interaction in request.tool_interactions:
            assistant_content: list[AnthropicInputContentBlock] = []

            if interaction.response.text:
                assistant_content.append(
                    {
                        "type": "text",
                        "text": interaction.response.text,
                    }
                )

            for invocation in interaction.response.tool_invocations:
                assistant_content.append(
                    {
                        "type": "tool_use",
                        "id": invocation.id,
                        "name": invocation.tool_name,
                        "input": invocation.arguments,
                    }
                )

            request_messages.append(
                {
                    "role": "assistant",
                    "content": assistant_content,
                }
            )

            result_content: list[AnthropicInputContentBlock] = []

            for result in interaction.results:
                tool_result: AnthropicToolResultInputBlock = {
                    "type": "tool_result",
                    "tool_use_id": result.invocation_id,
                    "content": _serialize_tool_result(result),
                }

                if result.status == "error":
                    tool_result["is_error"] = True

                result_content.append(tool_result)

            request_messages.append(
                {
                    "role": "user",
                    "content": result_content,
                }
            )

        system_instructions = build_system_instructions(
            request.system_prompt,
            request.context_documents,
        )

        max_tokens = (
            request.generation_config.max_output_tokens
            if request.generation_config.max_output_tokens is not None
            else self.max_tokens
        )

        request_arguments: AnthropicMessageCreateArguments = {
            "model": self.model_name,
            "max_tokens": max_tokens,
            "messages": request_messages,
        }

        if system_instructions is not None:
            request_arguments["system"] = system_instructions

        if request.generation_config.temperature is not None:
            request_arguments["temperature"] = request.generation_config.temperature

        if request.generation_config.top_p is not None:
            request_arguments["top_p"] = request.generation_config.top_p

        if request.response_format is not None:
            request_arguments["output_config"] = {
                "format": {
                    "type": "json_schema",
                    "schema": request.response_format.schema,
                }
            }

        if request.tools:
            request_arguments["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
                for tool in request.tools
            ]

        try:
            response = self.client.messages.create(
                **request_arguments,
            )
        except APIConnectionError as exc:
            raise CompletionError(
                "Unable to connect to Anthropic. Check the network connection."
            ) from exc
        except AuthenticationError as exc:
            raise CompletionError(
                "Anthropic authentication failed. Check ANTHROPIC_API_KEY."
            ) from exc
        except NotFoundError as exc:
            raise CompletionError(
                f"Model '{self.model_name}' is not available through Anthropic."
            ) from exc
        except RateLimitError as exc:
            raise CompletionError(
                "Anthropic rate limit or account quota was exceeded."
            ) from exc
        except APIStatusError as exc:
            raise CompletionError(
                f"Anthropic API request failed with status {exc.status_code}."
            ) from exc

        text_blocks: list[str] = []
        tool_invocations: list[ToolInvocation] = []

        for block in response.content:
            if block.type == "text":
                text_blocks.append(cast(AnthropicTextBlock, block).text)
                continue

            if block.type != "tool_use":
                continue

            tool_use_block = cast(AnthropicToolUseBlock, block)

            try:
                tool_invocation = ToolInvocation(
                    id=tool_use_block.id,
                    tool_name=tool_use_block.name,
                    arguments=tool_use_block.input,
                )
            except (AttributeError, ConfigurationError) as exc:
                raise CompletionError(
                    "Anthropic returned a malformed tool invocation."
                ) from exc

            tool_invocations.append(tool_invocation)

        return ChatResponse(
            text="".join(text_blocks),
            tool_invocations=tuple(tool_invocations),
        )
