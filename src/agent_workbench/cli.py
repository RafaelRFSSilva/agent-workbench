"""Interactive command-line interface for Agent Workbench."""

from agent_workbench.config import get_model_name
from agent_workbench.errors import CompletionError
from agent_workbench.messages import Message
from agent_workbench.providers.base import ChatProvider
from agent_workbench.providers.ollama import OllamaProvider

EXIT_COMMANDS = {"/exit", "/quit"}


def run_cli(provider: ChatProvider) -> None:
    """Run an interactive conversation using the provided model provider."""

    messages: list[Message] = []

    print(f"Agent Workbench | Provider: {provider.name} | Model: {provider.model_name}")
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
            assistant_reply = provider.complete(request_messages)
        except CompletionError as exc:
            print(f"Error: {exc}\n")
            continue

        assistant_message: Message = {
            "role": "assistant",
            "content": assistant_reply,
        }
        messages = [*request_messages, assistant_message]

        print(f"Assistant: {assistant_reply}\n")


def main() -> None:
    """Run the CLI using the configured Ollama provider."""

    provider = OllamaProvider(model_name=get_model_name())

    run_cli(provider)


if __name__ == "__main__":
    main()
