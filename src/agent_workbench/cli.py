"""Interactive command-line interface for Agent Workbench."""

from collections.abc import Callable
from functools import partial
from typing import Literal, TypedDict

from ollama import chat

from agent_workbench.config import DEFAULT_MODEL_NAME, get_model_name

EXIT_COMMANDS = {"/exit", "/quit"}


class Message(TypedDict):
    """Represent a message exchanged with a language model."""

    role: Literal["system", "user", "assistant"]
    content: str


CompletionFunction = Callable[[list[Message]], str]


def request_completion(
    messages: list[Message],
    *,
    model_name: str,
) -> str:
    """Send the conversation history to the selected local model."""

    response = chat(
        model=model_name,
        messages=messages,
        stream=False,
    )

    return response.message.content or ""


def run_cli(
    completion_fn: CompletionFunction,
    *,
    model_name: str = DEFAULT_MODEL_NAME,
) -> None:
    """Run an interactive conversation using the provided completion function."""

    messages: list[Message] = []

    print(f"Agent Workbench | Local model: {model_name}")
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
    """Run the CLI using the configured local Ollama model."""

    model_name = get_model_name()
    completion_fn = partial(request_completion, model_name=model_name)

    run_cli(completion_fn, model_name=model_name)


if __name__ == "__main__":
    main()
