"""Interactive command-line interface for Agent Workbench."""

from collections.abc import Sequence
from agent_workbench.arguments import (
    parse_cli_arguments,
    resolve_runtime_configuration,
)
from agent_workbench.config import (
    load_environment,
)
from agent_workbench.context import ContextDocument
from agent_workbench.errors import CompletionError, ConfigurationError
from agent_workbench.messages import ChatRequest, Message
from agent_workbench.providers.base import ChatProvider
from agent_workbench.providers.factory import create_provider
from agent_workbench.agents import AgentProfile

EXIT_COMMANDS = {"/exit", "/quit"}


def run_cli(
    provider: ChatProvider,
    system_prompt: str | None = None,
    agent_profile: AgentProfile | None = None,
    context_documents: tuple[ContextDocument, ...] = (),
) -> None:
    """Run an interactive conversation using the provided model provider."""

    messages: list[Message] = []

    header = (
        f"Agent Workbench | Provider: {provider.name} | Model: {provider.model_name}"
    )

    if agent_profile is not None:
        header += f" | Agent: {agent_profile.name}"

    print(header)
    print("Type /exit or /quit to end the session.\n")
    if agent_profile is not None:
        print(f"Role: {agent_profile.description}")

    print()

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
            assistant_reply = provider.complete(
                ChatRequest(
                    messages=request_messages,
                    system_prompt=system_prompt,
                    context_documents=context_documents,
                )
            )
        except CompletionError as exc:
            print(f"Error: {exc}\n")
            continue

        assistant_message: Message = {
            "role": "assistant",
            "content": assistant_reply,
        }
        messages = [*request_messages, assistant_message]

        print(f"Assistant: {assistant_reply}\n")


def main(
    argv: Sequence[str] | None = None,
) -> None:
    """Run the CLI using the resolved provider and model."""

    load_environment()
    arguments = parse_cli_arguments(argv)

    try:
        runtime_configuration = resolve_runtime_configuration(arguments)
        provider = create_provider(
            runtime_configuration.provider_name,
            runtime_configuration.model_name,
        )
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}")
        return

    run_cli(
        provider,
        system_prompt=runtime_configuration.system_prompt,
        agent_profile=runtime_configuration.agent_profile,
        context_documents=runtime_configuration.context_documents,
    )


if __name__ == "__main__":
    main()
