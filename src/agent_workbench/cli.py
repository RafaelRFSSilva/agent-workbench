"""Interactive command-line interface for Agent Workbench."""

from collections.abc import Sequence
import json
from agent_workbench.arguments import (
    parse_cli_arguments,
    resolve_runtime_configuration,
)
from agent_workbench.built_in_tools import create_built_in_tool_registry
from agent_workbench.config import (
    load_environment,
)
from agent_workbench.context import ContextDocument
from agent_workbench.interactive_setup import run_interactive_setup
from agent_workbench.errors import CompletionError, ConfigurationError
from agent_workbench.messages import ChatRequest, Message, ToolInteractionRound
from agent_workbench.providers.base import ChatProvider
from agent_workbench.providers.factory import create_provider
from agent_workbench.agents import AgentProfile
from agent_workbench.generation import GenerationConfig
from agent_workbench.structured_outputs import JSONResponseFormat
from agent_workbench.symbol_tools import register_symbol_tools
from agent_workbench.tool_calling import run_tool_calling_loop
from agent_workbench.tool_registry import ToolRegistry
from agent_workbench.workspace import Workspace
from agent_workbench.workspace_tools import register_workspace_tools
from agent_workbench.git_tools import register_git_tools

EXIT_COMMANDS = {"/exit", "/quit"}
DEFAULT_MAX_TOOL_ROUNDS = 8


def run_cli(
    provider: ChatProvider,
    system_prompt: str | None = None,
    agent_profile: AgentProfile | None = None,
    context_documents: tuple[ContextDocument, ...] = (),
    generation_config: GenerationConfig | None = None,
    response_format: JSONResponseFormat | None = None,
    tool_registry: ToolRegistry | None = None,
    max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
    show_tool_traces: bool = False,
) -> None:
    """Run an interactive conversation using the provided model provider."""

    active_generation_config = generation_config or GenerationConfig()
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
            request = ChatRequest(
                messages=request_messages,
                system_prompt=system_prompt,
                context_documents=context_documents,
                generation_config=active_generation_config,
                response_format=response_format,
                tools=tool_registry.definitions if tool_registry is not None else (),
            )

            if tool_registry is None:
                assistant_response = provider.complete(request)
            elif show_tool_traces:
                assistant_response = run_tool_calling_loop(
                    provider,
                    request,
                    tool_registry,
                    max_tool_rounds,
                    tool_round_observer=_display_tool_round,
                )
            else:
                assistant_response = run_tool_calling_loop(
                    provider,
                    request,
                    tool_registry,
                    max_tool_rounds,
                )
        except CompletionError as exc:
            print(f"Error: {exc}\n")
            continue

        assistant_message: Message = {
            "role": "assistant",
            "content": assistant_response.text,
        }
        messages = [*request_messages, assistant_message]

        print(f"Assistant: {assistant_response.text}\n")


def main(
    argv: Sequence[str] | None = None,
) -> None:
    """Run the CLI using the resolved provider and model."""

    load_environment()
    arguments = parse_cli_arguments(argv)

    try:
        if arguments.setup:
            runtime_configuration = run_interactive_setup()
        else:
            runtime_configuration = resolve_runtime_configuration(arguments)

        provider = create_provider(
            runtime_configuration.provider_name,
            runtime_configuration.model_name,
        )

        tool_registry = (
            create_built_in_tool_registry()
            if runtime_configuration.enable_tools
            else None
        )

        if runtime_configuration.workspace_root is not None:
            workspace = Workspace(runtime_configuration.workspace_root)
            if tool_registry is None:
                tool_registry = ToolRegistry()
            register_workspace_tools(tool_registry, workspace)
            register_symbol_tools(tool_registry, workspace)
            register_git_tools(tool_registry, workspace)
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}")
        return

    if tool_registry is not None and runtime_configuration.show_tool_traces:
        run_cli(
            provider,
            system_prompt=runtime_configuration.system_prompt,
            agent_profile=runtime_configuration.agent_profile,
            context_documents=runtime_configuration.context_documents,
            generation_config=runtime_configuration.generation_config,
            response_format=runtime_configuration.response_format,
            tool_registry=tool_registry,
            show_tool_traces=True,
        )
    elif tool_registry is not None:
        run_cli(
            provider,
            system_prompt=runtime_configuration.system_prompt,
            agent_profile=runtime_configuration.agent_profile,
            context_documents=runtime_configuration.context_documents,
            generation_config=runtime_configuration.generation_config,
            response_format=runtime_configuration.response_format,
            tool_registry=tool_registry,
        )
    else:
        run_cli(
            provider,
            system_prompt=runtime_configuration.system_prompt,
            agent_profile=runtime_configuration.agent_profile,
            context_documents=runtime_configuration.context_documents,
            generation_config=runtime_configuration.generation_config,
            response_format=runtime_configuration.response_format,
        )


def _display_tool_round(round_: ToolInteractionRound) -> None:
    """Display compact, safe trace records for one completed tool round."""

    for invocation, result in zip(
        round_.response.tool_invocations,
        round_.results,
        strict=True,
    ):
        print(f"Tool trace: {invocation.tool_name} ({invocation.id})")
        print(f"  arguments={_serialize_trace_data(invocation.arguments)}")

        if result.status == "success":
            result_data = {
                "status": "success",
                "output": _redact_trace_data(result.output),
            }
        else:
            result_data = {
                "status": "error",
                "error": _redact_trace_data(result.error),
            }

        print(f"  result={_serialize_trace_data(result_data)}")


def _serialize_trace_data(value: object) -> str:
    """Serialize trace data as compact deterministic safe JSON."""

    return json.dumps(
        _redact_trace_data(value),
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _redact_trace_data(value: object, *, key: str | None = None) -> object:
    """Redact sensitive content and absolute paths from visible tool traces."""

    if key is not None and key.lower() in {
        "content",
        "password",
        "secret",
        "token",
        "api_key",
    }:
        return "[redacted]"

    if isinstance(value, dict):
        return {
            str(item_key): _redact_trace_data(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }

    if isinstance(value, list):
        return [_redact_trace_data(item) for item in value]

    if isinstance(value, str):
        if value.startswith("/"):
            return "[redacted absolute path]"
        if value == ".env" or value.endswith("/.env"):
            return "[redacted path]"

    return value


if __name__ == "__main__":
    main()
