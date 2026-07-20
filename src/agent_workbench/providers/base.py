"""Provider interfaces for Agent Workbench."""

from typing import Protocol

from agent_workbench.messages import Message


class ChatProvider(Protocol):
    """Define the interface required from a chat model provider."""

    @property
    def name(self) -> str:
        """Return a human-readable provider name."""

        ...

    @property
    def model_name(self) -> str:
        """Return the configured model name."""

        ...

    def complete(self, messages: list[Message]) -> str:
        """Generate a response for the supplied conversation."""

        ...
