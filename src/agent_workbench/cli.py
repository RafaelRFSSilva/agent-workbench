"""Interactive command-line interface for Agent Workbench."""

from collections.abc import Callable
from typing import Literal, TypedDict

from ollama import chat

MODEL_NAME = "gpt-oss:20b"
EXIT_COMMANDS = {"/exit", "/quit"}


class Message(TypedDict):
    """Represent a message exchanged with a language model."""

    role: Literal["system", "user", "assistant"]
    content: str


CompletionFunction = Callable[[list[Message]], str]


def request_completion(messages: list[Message]) -> str:
    """Send the conversation history to the local language model."""

    response = chat(
        model=MODEL_NAME,
        messages=messages,
        stream=False,
    )

    return response.message.content or ""


def run_cli(completion_fn: CompletionFunction) -> None:
    """Run an interactive conversation using the provided completion function."""

    messages: list[Message] = []

    print(f"Agent Workbench | Local model: {MODEL_NAME}")
    print("Type /exit or /quit to end the session.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSession ended.")
            break

        if not user_input:
            continue

        if user_input.lower() in EXIT_COMMANDS:
            print("Session ended.")
            break

        messages.append(
            {
                "role": "user",
                "content": user_input,
            }
        )

        assistant_reply = completion_fn(messages)

        messages.append(
            {
                "role": "assistant",
                "content": assistant_reply,
            }
        )

        print(f"Assistant: {assistant_reply}\n")


def main() -> None:
    """Run the CLI using the local Ollama completion function."""

    run_cli(request_completion)


if __name__ == "__main__":
    main()
