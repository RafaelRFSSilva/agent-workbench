"""OpenAI provider implementation."""

from dataclasses import dataclass
from typing import NotRequired, Protocol, TypedDict, Unpack

from openai import APIConnectionError, APIStatusError

from agent_workbench.context import build_system_instructions
from agent_workbench.errors import CompletionError
from agent_workbench.messages import ChatRequest, Message


class OpenAIResponse(Protocol):
    """Define the response data required from the OpenAI SDK."""

    output_text: str


class OpenAIResponseCreateArguments(TypedDict):
    """Represent arguments supplied to the OpenAI Responses API."""

    model: str
    input: list[Message]
    instructions: NotRequired[str]
    temperature: NotRequired[float]
    top_p: NotRequired[float]
    max_output_tokens: NotRequired[int]


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


@dataclass(frozen=True, slots=True)
class OpenAIProvider:
    """Generate chat completions through the OpenAI Responses API."""

    model_name: str
    client: OpenAIClient

    @property
    def name(self) -> str:
        """Return the provider name."""

        return "OpenAI"

    def complete(self, request: ChatRequest) -> str:
        """Generate a response using the configured OpenAI model."""

        input_messages: list[Message] = [
            {
                "role": message["role"],
                "content": message["content"],
            }
            for message in request.messages
        ]

        system_instructions = build_system_instructions(
            request.system_prompt,
            request.context_documents,
        )

        response_arguments: OpenAIResponseCreateArguments = {
            "model": self.model_name,
            "input": input_messages,
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

        return response.output_text or ""
