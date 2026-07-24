"""Ollama provider implementation."""

from dataclasses import dataclass
from typing import Literal, TypedDict

from ollama import ResponseError, chat

from agent_workbench.context import build_system_instructions
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

        try:
            if generation_options:
                response = chat(
                    model=self.model_name,
                    messages=request_messages,
                    options=generation_options,
                    stream=False,
                )
            else:
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
