"""Ollama provider implementation."""

from dataclasses import dataclass
from typing import Literal, TypedDict

from ollama import ResponseError, chat

from agent_workbench.errors import CompletionError
from agent_workbench.messages import ChatRequest


class OllamaMessage(TypedDict):
    """Represent a message accepted by the Ollama chat API."""

    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class OllamaProvider:
    """Generate chat completions through a local Ollama server."""

    model_name: str

    @property
    def name(self) -> str:
        """Return the provider name."""

        return "Ollama"

    def complete(self, request: ChatRequest) -> str:
        """Generate a response using the configured Ollama model."""

        request_messages: list[OllamaMessage] = []

        if request.system_prompt is not None:
            request_messages.append(
                {
                    "role": "system",
                    "content": request.system_prompt,
                }
            )

        for message in request.messages:
            request_messages.append(
                {
                    "role": message["role"],
                    "content": message["content"],
                }
            )

        try:
            response = chat(
                model=self.model_name,
                messages=request_messages,
                stream=False,
            )
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

        return response.message.content or ""
