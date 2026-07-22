"""Anthropic provider implementation."""

from dataclasses import dataclass
from typing import Protocol

from anthropic import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    NotFoundError,
    RateLimitError,
)

from agent_workbench.errors import CompletionError
from agent_workbench.messages import ChatRequest, Message


class AnthropicContentBlock(Protocol):
    """Represent a content block returned by Anthropic."""

    type: str
    text: str


class AnthropicResponse(Protocol):
    """Represent the response fields used by the provider."""

    content: list[AnthropicContentBlock]


class AnthropicMessagesResource(Protocol):
    """Represent the Anthropic Messages API methods used by the provider."""

    def create(
        self,
        *,
        model: str,
        max_tokens: int,
        messages: list[Message],
        system: str | None = None,
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

        try:
            if request.system_prompt is None:
                response = self.client.messages.create(
                    model=self.model_name,
                    max_tokens=self.max_tokens,
                    messages=request_messages,
                )
            else:
                response = self.client.messages.create(
                    model=self.model_name,
                    max_tokens=self.max_tokens,
                    messages=request_messages,
                    system=request.system_prompt,
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
