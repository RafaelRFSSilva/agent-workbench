"""Interactive command-line interface for Agent Workbench."""

from collections.abc import Sequence
import json
from agent_workbench.arguments import (
    parse_cli_arguments,
    resolve_runtime_configuration,
)
from agent_workbench.config import (
    load_environment,
)
from agent_workbench.interactive_setup import run_interactive_setup
from agent_workbench.errors import CompletionError, ConfigurationError
from agent_workbench.messages import ToolInteractionRound
from agent_workbench.agents import AgentProfile
from agent_workbench.session import AgentSession, SessionId
from agent_workbench.session_factory import create_agent_session

EXIT_COMMANDS = {"/exit", "/quit"}


def run_cli(
    session: AgentSession,
    agent_profile: AgentProfile | None = None,
    show_tool_traces: bool = False,
) -> None:
    """Run an interactive conversation using one configured session."""

    header = (
        f"Agent Workbench | Provider: {session.provider_name} "
        f"| Model: {session.model_name}"
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

        try:
            if session.tool_registry is not None and show_tool_traces:
                assistant_response = session.send(
                    user_input,
                    tool_round_observer=_display_tool_round,
                )
            else:
                assistant_response = session.send(user_input)
        except CompletionError as exc:
            print(f"Error: {exc}\n")
            continue

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

        session = create_agent_session(
            SessionId("cli-session"),
            runtime_configuration,
        )
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}")
        return

    if session.tool_registry is not None and runtime_configuration.show_tool_traces:
        run_cli(
            session,
            agent_profile=runtime_configuration.agent_profile,
            show_tool_traces=True,
        )
    else:
        run_cli(
            session,
            agent_profile=runtime_configuration.agent_profile,
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
