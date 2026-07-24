"""Message and request types shared across Agent Workbench."""

from dataclasses import dataclass
from typing import Literal, TypedDict

from agent_workbench.context import ContextDocument


class Message(TypedDict):
    """Represent a conversation message exchanged with a language model."""

    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class ChatRequest:
    """Represent a provider-independent chat completion request."""

    messages: list[Message]
    system_prompt: str | None = None
    context_documents: tuple[ContextDocument, ...] = ()
