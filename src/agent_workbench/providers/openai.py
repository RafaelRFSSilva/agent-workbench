"""OpenAI provider implementation."""

import json
from dataclasses import dataclass
from typing import Literal, NotRequired, Protocol, TypedDict, Unpack

from openai import APIConnectionError, APIStatusError

from agent_workbench.context import build_system_instructions
from agent_workbench.errors import CompletionError, ConfigurationError
from agent_workbench.messages import ChatRequest, ChatResponse, Message
from agent_workbench.structured_outputs import JSONSchema
from agent_workbench.tools import ToolInvocation, ToolResult


class OpenAIResponseOutputItem(Protocol):
    """Define an output item returned by the OpenAI Responses API."""

    type: str
    call_id: str
    name: str
    arguments: str


class OpenAIResponse(Protocol):
    """Define the response data required from the OpenAI SDK."""

    output_text: str
    output: list[OpenAIResponseOutputItem]


class OpenAIJSONSchemaFormat(TypedDict):
    """Represent an OpenAI strict JSON Schema response format."""

    type: Literal["json_schema"]
    name: str
    schema: JSONSchema
    strict: bool


class OpenAITextConfig(TypedDict):
    """Represent OpenAI text response configuration."""

    format: OpenAIJSONSchemaFormat


class OpenAIFunctionToolDefinition(TypedDict):
    """Represent a function tool supplied to OpenAI."""

    type: Literal["function"]
    name: str
    description: str
    parameters: JSONSchema
    strict: bool


class OpenAIFunctionCallInput(TypedDict):
    """Represent a previous function call supplied to OpenAI."""

    type: Literal["function_call"]
    call_id: str
    name: str
    arguments: str


class OpenAIFunctionCallOutputInput(TypedDict):
    """Represent a previous function-call output supplied to OpenAI."""

    type: Literal["function_call_output"]
    call_id: str
    output: str


type OpenAIInputItem = Message | OpenAIFunctionCallInput | OpenAIFunctionCallOutputInput


class OpenAIResponseCreateArguments(TypedDict):
    """Represent arguments supplied to the OpenAI Responses API."""

    model: str
    input: list[OpenAIInputItem]
    instructions: NotRequired[str]
    temperature: NotRequired[float]
    top_p: NotRequired[float]
    max_output_tokens: NotRequired[int]
    text: NotRequired[OpenAITextConfig]
    tools: NotRequired[list[OpenAIFunctionToolDefinition]]


class OpenAIResponsesResource(Protocol):
    """Define the Responses API operation required by the provider."""

    def create(
        self,
        **kwargs: Unpack[OpenAIResponseCreateArguments],
    ) -> OpenAIResponse:
        """Create a model response."""

        ...


class OpenAIClient(Protocol):
    """Define the OpenAI client behavior required by the provider."""

    responses: OpenAIResponsesResource


def _serialize_tool_result(result: ToolResult) -> str:
    """Serialize a provider-independent tool result for OpenAI input."""

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
class OpenAIProvider:
    """Generate chat completions through the OpenAI Responses API."""

    model_name: str
    client: OpenAIClient

    @property
    def name(self) -> str:
        """Return the provider name."""

        return "OpenAI"

    def complete(self, request: ChatRequest) -> ChatResponse:
        """Generate a response using the configured OpenAI model."""

        input_items: list[OpenAIInputItem] = [
            {
                "role": message["role"],
                "content": message["content"],
            }
            for message in request.messages
        ]

        for interaction in request.tool_interactions:
            if interaction.response.text:
                input_items.append(
                    {
                        "role": "assistant",
                        "content": interaction.response.text,
                    }
                )

            for invocation in interaction.response.tool_invocations:
                input_items.append(
                    {
                        "type": "function_call",
                        "call_id": invocation.id,
                        "name": invocation.tool_name,
                        "arguments": json.dumps(
                            invocation.arguments,
                            allow_nan=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    }
                )

            for result in interaction.results:
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": result.invocation_id,
                        "output": _serialize_tool_result(result),
                    }
                )

        system_instructions = build_system_instructions(
            request.system_prompt,
            request.context_documents,
        )

        response_arguments: OpenAIResponseCreateArguments = {
            "model": self.model_name,
            "input": input_items,
        }

        if system_instructions is not None:
            response_arguments["instructions"] = system_instructions

        if request.generation_config.temperature is not None:
            response_arguments["temperature"] = request.generation_config.temperature

        if request.generation_config.top_p is not None:
            response_arguments["top_p"] = request.generation_config.top_p

        if request.generation_config.max_output_tokens is not None:
            response_arguments["max_output_tokens"] = (
                request.generation_config.max_output_tokens
            )

        if request.response_format is not None:
            response_arguments["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": request.response_format.name,
                    "schema": request.response_format.schema,
                    "strict": True,
                }
            }

        if request.tools:
            response_arguments["tools"] = [
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                    "strict": True,
                }
                for tool in request.tools
            ]

        try:
            response = self.client.responses.create(
                **response_arguments,
            )
        except APIConnectionError as exc:
            raise CompletionError(
                "Unable to connect to OpenAI. Check the network connection."
            ) from exc
        except APIStatusError as exc:
            if exc.status_code == 401:
                raise CompletionError(
                    "OpenAI authentication failed. "
                    "Confirm that OPENAI_API_KEY is valid."
                ) from exc

            if exc.status_code == 404:
                raise CompletionError(
                    f"Model '{self.model_name}' is not available through OpenAI."
                ) from exc

            if exc.status_code == 429:
                raise CompletionError(
                    "OpenAI rate limit or account quota was exceeded."
                ) from exc

            raise CompletionError(
                f"OpenAI request failed with status code {exc.status_code}."
            ) from exc

        tool_invocations: list[ToolInvocation] = []

        for output_item in response.output:
            if output_item.type != "function_call":
                continue

            try:
                arguments = json.loads(output_item.arguments)
            except (json.JSONDecodeError, TypeError) as exc:
                raise CompletionError(
                    "OpenAI returned a malformed tool invocation."
                ) from exc

            if not isinstance(arguments, dict):
                raise CompletionError("OpenAI returned a malformed tool invocation.")

            try:
                tool_invocation = ToolInvocation(
                    id=output_item.call_id,
                    tool_name=output_item.name,
                    arguments=arguments,
                )
            except ConfigurationError as exc:
                raise CompletionError(
                    "OpenAI returned a malformed tool invocation."
                ) from exc

            tool_invocations.append(tool_invocation)

        return ChatResponse(
            text=response.output_text or "",
            tool_invocations=tuple(tool_invocations),
        )
