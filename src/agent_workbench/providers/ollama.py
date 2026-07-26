"""Ollama provider implementation."""

from dataclasses import dataclass
from typing import Literal, NotRequired, TypedDict

from ollama import ResponseError, chat

from agent_workbench.context import build_system_instructions
from agent_workbench.errors import CompletionError
from agent_workbench.messages import ChatRequest, ChatResponse
from agent_workbench.structured_outputs import JSONSchema
from agent_workbench.tools import ToolInvocation


class OllamaMessage(TypedDict):
    """Represent a message accepted by the Ollama chat API."""

    role: Literal["system", "user", "assistant"]
    content: str


class OllamaFunctionDefinition(TypedDict):
    """Represent a function supplied to Ollama."""

    name: str
    description: str
    parameters: JSONSchema


class OllamaToolDefinition(TypedDict):
    """Represent a tool supplied to Ollama."""

    type: Literal["function"]
    function: OllamaFunctionDefinition


class OllamaChatArguments(TypedDict):
    """Represent arguments supplied to the Ollama chat API."""

    model: str
    messages: list[OllamaMessage]
    stream: bool
    options: NotRequired[dict[str, float | int]]
    format: NotRequired[JSONSchema]
    tools: NotRequired[list[OllamaToolDefinition]]


@dataclass(frozen=True, slots=True)
class OllamaProvider:
    """Generate chat completions through a local Ollama server."""

    model_name: str

    @property
    def name(self) -> str:
        """Return the provider name."""

        return "Ollama"

    def complete(self, request: ChatRequest) -> ChatResponse:
        """Generate a response using the configured Ollama model."""

        request_messages: list[OllamaMessage] = []

        system_instructions = build_system_instructions(
            request.system_prompt,
            request.context_documents,
        )

        if system_instructions is not None:
            request_messages.append(
                {
                    "role": "system",
                    "content": system_instructions,
                }
            )

        for message in request.messages:
            request_messages.append(
                {
                    "role": message["role"],
                    "content": message["content"],
                }
            )

        generation_options: dict[str, float | int] = {}

        if request.generation_config.temperature is not None:
            generation_options["temperature"] = request.generation_config.temperature

        if request.generation_config.top_p is not None:
            generation_options["top_p"] = request.generation_config.top_p

        if request.generation_config.max_output_tokens is not None:
            generation_options["num_predict"] = (
                request.generation_config.max_output_tokens
            )

        chat_arguments: OllamaChatArguments = {
            "model": self.model_name,
            "messages": request_messages,
            "stream": False,
        }

        if generation_options:
            chat_arguments["options"] = generation_options

        if request.response_format is not None:
            chat_arguments["format"] = request.response_format.schema

        if request.tools:
            chat_arguments["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in request.tools
            ]

        try:
            response = chat(**chat_arguments)
        except ConnectionError as exc:
            raise CompletionError(
                "Unable to connect to Ollama. "
                "Confirm that the Ollama service is running."
            ) from exc
        except ResponseError as exc:
            if exc.status_code == 404:
                raise CompletionError(
                    f"Model '{self.model_name}' is not available in Ollama."
                ) from exc

            raise CompletionError(f"Ollama request failed: {exc.error}") from exc

        tool_calls = getattr(response.message, "tool_calls", None) or ()

        tool_invocations = tuple(
            ToolInvocation(
                id=f"ollama-tool-call-{index}",
                tool_name=tool_call.function.name,
                arguments=dict(tool_call.function.arguments),
            )
            for index, tool_call in enumerate(tool_calls, start=1)
        )

        return ChatResponse(
            text=response.message.content or "",
            tool_invocations=tool_invocations,
        )
