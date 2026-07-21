"""OpenAI provider implementation."""

from dataclasses import dataclass
from typing import Protocol

from openai import APIConnectionError, APIStatusError

from agent_workbench.errors import CompletionError
from agent_workbench.messages import Message


class OpenAIResponse(Protocol):
    """Define the response data required from the OpenAI SDK."""

    output_text: str


class OpenAIResponsesResource(Protocol):
    """Define the Responses API operation required by the provider."""

    def create(
        self,
        *,
        model: str,
        input: list[Message],
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

    def complete(self, messages: list[Message]) -> str:
        """Generate a response using the configured OpenAI model."""

        input_messages: list[Message] = [
            {
                "role": message["role"],
                "content": message["content"],
            }
            for message in messages
        ]

        try:
            response = self.client.responses.create(
                model=self.model_name,
                input=input_messages,
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
