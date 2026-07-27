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
from agent_workbench.errors import (
    CompletionError,
    ConfigurationError,
    WorkspacePathError,
)
from agent_workbench.messages import ToolInteractionRound
from agent_workbench.agents import AgentProfile
from agent_workbench.session import AgentSession, SessionId
from agent_workbench.session_factory import create_agent_session
from agent_workbench.tools import (
    ToolApprovalDecision,
    ToolApprovalRequest,
)

EXIT_COMMANDS = {"/exit", "/quit"}


def run_cli(
    session: AgentSession,
    agent_profile: AgentProfile | None = None,
    show_tool_traces: bool = False,
    enable_actions: bool = False,
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
            if enable_actions and show_tool_traces:
                assistant_response = session.send(
                    user_input,
                    tool_round_observer=_display_tool_round,
                    tool_approval_handler=_prompt_for_tool_approval,
                )
            elif enable_actions:
                assistant_response = session.send(
                    user_input,
                    tool_approval_handler=_prompt_for_tool_approval,
                )
            elif session.tool_registry is not None and show_tool_traces:
                assistant_response = session.send(
                    user_input,
                    tool_round_observer=_display_tool_round,
                )
            else:
                assistant_response = session.send(user_input)
        except CompletionError as exc:
            print(f"Error: {exc}\n")
            continue
        except (ValueError, WorkspacePathError) as exc:
            if not enable_actions:
                raise
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

    run_arguments = {
        "agent_profile": runtime_configuration.agent_profile,
    }
    if session.tool_registry is not None and runtime_configuration.show_tool_traces:
        run_arguments["show_tool_traces"] = True
    if runtime_configuration.enable_actions:
        run_arguments["enable_actions"] = True
    run_cli(session, **run_arguments)


def _prompt_for_tool_approval(
    request: ToolApprovalRequest,
) -> ToolApprovalDecision:
    """Display one complete action preview and prompt once with default deny."""

    tool_name = request.invocation.tool_name
    preview = request.preview
    print(f"\nAction approval required: {tool_name}")

    if tool_name == "apply_file_patch" and isinstance(preview, dict):
        print(f"  Path: {preview.get('path', '[unavailable]')}")
        print(f"  Operation: {preview.get('operation', '[unavailable]')}")
        print(
            "  Bytes: "
            f"{preview.get('old_size_bytes', '[unavailable]')} → "
            f"{preview.get('new_size_bytes', '[unavailable]')}"
        )
        print(f"  Changed lines: {preview.get('changed_lines', '[unavailable]')}")
        print("  Complete diff:")
        diff = preview.get("diff")
        print(diff if isinstance(diff, str) else "[unavailable]")
    elif tool_name in {
        "run_ruff_format",
        "run_ruff_check",
        "run_pytest",
    } and isinstance(preview, dict):
        print(f"  Target: {preview.get('path', '[unavailable]')}")
        command = preview.get("command")
        if isinstance(command, list) and all(
            isinstance(token, str) for token in command
        ):
            print(f"  Fixed command: {' '.join(command)}")
        else:
            print("  Fixed command: [unavailable]")
        print(f"  Working directory: {preview.get('cwd', '[unavailable]')}")
        print(f"  Timeout: {preview.get('timeout_seconds', '[unavailable]')} seconds")
        if tool_name == "run_ruff_format":
            print("  Warning: Ruff format may modify files.")
        elif tool_name == "run_ruff_check":
            print("  Note: Ruff check performs static analysis.")
        else:
            print("  Warning: pytest executes project code.")
    else:
        print(f"  Preview: {_serialize_trace_data(preview)}")

    try:
        answer = input("Approve action? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return ToolApprovalDecision.DENY

    if answer in {"y", "yes"}:
        return ToolApprovalDecision.APPROVE

    return ToolApprovalDecision.DENY


def _display_tool_round(round_: ToolInteractionRound) -> None:
    """Display compact, safe trace records for one completed tool round."""

    for invocation, result in zip(
        round_.response.tool_invocations,
        round_.results,
        strict=True,
    ):
        print(f"Tool trace: {invocation.tool_name} ({invocation.id})")
        print(f"  arguments={_serialize_trace_data(_trace_arguments(invocation))}")

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


def _trace_arguments(invocation) -> object:
    """Return safe trace arguments with patch contents replaced by byte counts."""

    arguments = invocation.arguments
    if invocation.tool_name != "apply_file_patch":
        return arguments

    expected_content = arguments.get("expected_content")
    replacement_content = arguments.get("replacement_content")
    return {
        "path": arguments.get("path"),
        "create_if_missing": arguments.get("create_if_missing", False),
        "expected_content_bytes": (
            len(expected_content.encode("utf-8"))
            if isinstance(expected_content, str)
            else None
        ),
        "replacement_content_bytes": (
            len(replacement_content.encode("utf-8"))
            if isinstance(replacement_content, str)
            else None
        ),
    }


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
