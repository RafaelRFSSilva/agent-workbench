"""Message types shared across Agent Workbench."""

from typing import Literal, TypedDict


class Message(TypedDict):
    """Represent a message exchanged with a language model."""

    role: Literal["system", "user", "assistant"]
    content: str
