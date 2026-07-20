"""Ollama provider implementation."""

from dataclasses import dataclass

from ollama import ResponseError, chat

from agent_workbench.errors import CompletionError
from agent_workbench.messages import Message


@dataclass(frozen=True, slots=True)
class OllamaProvider:
    """Generate chat completions through a local Ollama server."""

    model_name: str

    @property
    def name(self) -> str:
        """Return the provider name."""

        return "Ollama"

    def complete(self, messages: list[Message]) -> str:
        """Generate a response using the configured Ollama model."""

        try:
            response = chat(
                model=self.model_name,
                messages=messages,
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
