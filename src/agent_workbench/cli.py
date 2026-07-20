"""Interactive command-line interface for Agent Workbench."""

from collections.abc import Callable
from functools import partial
from typing import Literal, TypedDict

from ollama import ResponseError, chat

from agent_workbench.config import DEFAULT_MODEL_NAME, get_model_name
from agent_workbench.errors import CompletionError

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

    try:
        response = chat(
            model=model_name,
            messages=messages,
            stream=False,
        )
    except ConnectionError as exc:
        raise CompletionError(
            "Unable to connect to Ollama. Confirm that the Ollama service is running."
        ) from exc
    except ResponseError as exc:
        if exc.status_code == 404:
            raise CompletionError(
                f"Model '{model_name}' is not available in Ollama."
            ) from exc

        raise CompletionError(f"Ollama request failed: {exc.error}") from exc

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

        user_message: Message = {
            "role": "user",
            "content": user_input,
        }
        request_messages = [*messages, user_message]

        try:
            assistant_reply = completion_fn(request_messages)
        except CompletionError as exc:
            print(f"Error: {exc}\n")
            continue

        messages = request_messages
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
