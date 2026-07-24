"""Anthropic provider implementation."""

from dataclasses import dataclass
from typing import NotRequired, Protocol, TypedDict, Unpack

from anthropic import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    NotFoundError,
    RateLimitError,
)

from agent_workbench.context import build_system_instructions
from agent_workbench.errors import CompletionError
from agent_workbench.messages import ChatRequest, Message


class AnthropicContentBlock(Protocol):
    """Represent a content block returned by Anthropic."""

    type: str
    text: str


class AnthropicResponse(Protocol):
    """Represent the response fields used by the provider."""

    content: list[AnthropicContentBlock]


class AnthropicMessageCreateArguments(TypedDict):
    """Represent arguments supplied to the Anthropic Messages API."""

    model: str
    max_tokens: int
    messages: list[Message]
    system: NotRequired[str]
    temperature: NotRequired[float]
    top_p: NotRequired[float]


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

    def complete(self, request: ChatRequest) -> str:
        """Return an assistant reply for the supplied chat request."""

        request_messages: list[Message] = [
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

        return "".join(block.text for block in response.content if block.type == "text")
