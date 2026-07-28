"""Interactive command-line interface for Agent Workbench."""

import json
from collections.abc import Sequence

from agent_workbench.agents import AgentProfile
from agent_workbench.arguments import (
    DEFAULT_AUTONOMOUS_MAX_TOOL_ROUNDS,
    RuntimeConfiguration,
    parse_cli_arguments,
    resolve_runtime_configuration,
)
from agent_workbench.coding_loop import run_autonomous_coding_task
from agent_workbench.config import load_environment
from agent_workbench.errors import (
    CompletionError,
    ConfigurationError,
    WorkspacePathError,
)
from agent_workbench.interactive_setup import run_interactive_setup
from agent_workbench.isolated_coding import (
    IsolatedAutonomousWorkflowResult,
    run_isolated_autonomous_workflow,
)
from agent_workbench.messages import ToolInteractionRound
from agent_workbench.session import AgentSession, SessionId
from agent_workbench.session_factory import create_agent_session
from agent_workbench.tool_registry import ToolRegistry
from agent_workbench.tools import (
    ToolApprovalDecision,
    ToolApprovalRequest,
)
from agent_workbench.worktrees import (
    WorktreeAction,
    WorktreeApprovalRequest,
)

EXIT_COMMANDS = {"/exit", "/quit"}
AUTONOMOUS_MAX_TOOL_ROUNDS = DEFAULT_AUTONOMOUS_MAX_TOOL_ROUNDS


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
            if enable_actions:
                assistant_response = session.send(
                    user_input,
                    tool_round_observer=(
                        _display_tool_round
                        if show_tool_traces
                        else lambda round_: _display_approved_action_completion(
                            round_,
                            session.tool_registry,
                        )
                    ),
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
    task_prompt = getattr(arguments, "task_prompt", None)
    commit_message = getattr(arguments, "commit_message", None)

    try:
        if arguments.setup:
            runtime_configuration = run_interactive_setup()
        else:
            runtime_configuration = resolve_runtime_configuration(arguments)
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}")
        return

    if runtime_configuration.worktree_path is not None:
        _run_isolated_autonomous_task(
            runtime_configuration,
            task_prompt=task_prompt,
            commit_message=commit_message,
        )
        return

    try:
        if task_prompt is None:
            session = create_agent_session(
                SessionId("cli-session"),
                runtime_configuration,
            )
        else:
            session = create_agent_session(
                SessionId("cli-session"),
                runtime_configuration,
                max_tool_rounds=runtime_configuration.max_tool_rounds,
            )
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}")
        return

    _run_configured_cli(
        session,
        runtime_configuration,
        task_prompt=task_prompt,
    )


def _run_configured_cli(
    session: AgentSession,
    runtime_configuration: RuntimeConfiguration,
    *,
    task_prompt: str | None = None,
) -> None:
    """Run the configured interactive session or one autonomous task."""

    if task_prompt is not None:
        _run_autonomous_task(
            session,
            task_prompt,
            show_tool_traces=runtime_configuration.show_tool_traces,
        )
        return

    run_arguments = {
        "agent_profile": runtime_configuration.agent_profile,
    }
    if session.tool_registry is not None and runtime_configuration.show_tool_traces:
        run_arguments["show_tool_traces"] = True
    if runtime_configuration.enable_actions:
        run_arguments["enable_actions"] = True

    run_cli(session, **run_arguments)


def _run_autonomous_task(
    session: AgentSession,
    task_prompt: str,
    *,
    show_tool_traces: bool,
) -> None:
    """Run one supervised autonomous task and display its final result."""

    def display_approved_actions(round_: ToolInteractionRound) -> None:
        _display_approved_action_completion(
            round_,
            session.tool_registry,
        )

    observer = _display_tool_round if show_tool_traces else display_approved_actions

    try:
        result = run_autonomous_coding_task(
            session,
            task_prompt,
            tool_approval_handler=_prompt_for_tool_approval,
            tool_round_observer=observer,
        )
    except (
        CompletionError,
        ConfigurationError,
        ValueError,
        WorkspacePathError,
    ) as exc:
        print(f"Autonomous task error: {exc}")
        return

    print(f"\nAssistant: {result.assistant_summary}\n")
    print("Autonomous task result:")
    print(f"  Tool rounds: {result.tool_round_count}")
    print(f"  Validation succeeded: {'yes' if result.validation_succeeded else 'no'}")
    print(f"  Git status inspected: {'yes' if result.inspected_git_status else 'no'}")
    print(f"  Git diff inspected: {'yes' if result.inspected_git_diff else 'no'}")

    if result.validation_runs:
        print("  Validation runs:")
        for validation in result.validation_runs:
            exit_code = (
                str(validation.exit_code)
                if validation.exit_code is not None
                else "[unavailable]"
            )
            print(
                f"    - {validation.tool_name}: "
                f"{validation.result_status}, exit code {exit_code}"
            )


def _run_isolated_autonomous_task(
    runtime_configuration: RuntimeConfiguration,
    *,
    task_prompt: str | None,
    commit_message: str | None,
) -> None:
    """Run one complete approved autonomous task in an isolated worktree."""

    target = runtime_configuration.worktree_path
    branch = runtime_configuration.worktree_branch
    if (
        target is None
        or branch is None
        or task_prompt is None
        or commit_message is None
    ):
        print(
            "Configuration error: Isolated autonomous workflow "
            "configuration is incomplete."
        )
        return

    observer = _display_tool_round if runtime_configuration.show_tool_traces else None
    try:
        result = run_isolated_autonomous_workflow(
            SessionId("cli-session"),
            runtime_configuration,
            branch,
            target,
            task_prompt,
            commit_message,
            worktree_approval_handler=_prompt_for_worktree_approval,
            tool_approval_handler=_prompt_for_tool_approval,
            commit_approval_handler=_prompt_for_isolated_commit_approval,
            tool_round_observer=observer,
            max_tool_rounds=runtime_configuration.max_tool_rounds,
        )
    except (
        CompletionError,
        ConfigurationError,
        ValueError,
        WorkspacePathError,
    ) as exc:
        print(f"Isolated autonomous workflow error: {exc}")
        return

    _display_isolated_autonomous_result(result)


def _display_isolated_autonomous_result(
    result: IsolatedAutonomousWorkflowResult,
) -> None:
    """Display the verified isolated commit and preserved recovery state."""

    coding_result = result.coding_result
    commit_result = result.commit_result
    print(f"\nAssistant: {coding_result.assistant_summary}\n")
    print("Isolated autonomous workflow result:")
    print(f"  Worktree: {result.worktree.target_display}")
    print(f"  Branch: {commit_result.branch_name}")
    print(f"  New isolated HEAD: {commit_result.new_head}")
    print(f"  Committed paths: {commit_result.operation_count}")
    print(f"  Tool rounds: {coding_result.tool_round_count}")
    print(
        "  Validation succeeded: "
        f"{'yes' if coding_result.validation_succeeded else 'no'}"
    )
    print(
        "  Git status inspected: "
        f"{'yes' if coding_result.inspected_git_status else 'no'}"
    )
    print(
        f"  Git diff inspected: {'yes' if coding_result.inspected_git_diff else 'no'}"
    )
    print(
        "  Final worktree clean: "
        f"{'yes' if result.final_worktree_state.clean else 'no'}"
    )
    print("  Primary working tree unchanged.")
    print("  Worktree and local branch preserved.")
    print("  No merge, push, worktree removal, or branch deletion was performed.")


def _prompt_for_isolated_commit_approval(request) -> ToolApprovalDecision:
    """Render one complete immutable commit preview and prompt exactly once."""

    preview = request.preview
    if not isinstance(preview, dict):
        return ToolApprovalDecision.DENY

    print("\nIsolated commit approval required")
    print(f"  Branch: {preview.get('branch', '[unavailable]')}")
    print(f"  Old HEAD: {preview.get('old_head', '[unavailable]')}")
    print("  Commit message:")
    print(str(preview.get("commit_message", "[unavailable]")))
    print(f"  Operations: {preview.get('operation_count', '[unavailable]')}")
    print(f"  Added: {preview.get('added_count', '[unavailable]')}")
    print(f"  Modified: {preview.get('modified_count', '[unavailable]')}")
    print(f"  Changed lines: {preview.get('total_changed_lines', '[unavailable]')}")
    print("  Approved paths:")
    paths = preview.get("paths")
    if isinstance(paths, list) and all(isinstance(path, str) for path in paths):
        for path in paths:
            print(f"    - {path}")
    else:
        print("    [unavailable]")

    print("  Complete diffs:")
    changes = preview.get("changes")
    if isinstance(changes, list):
        for change in changes:
            if not isinstance(change, dict):
                continue
            path = change.get("path")
            diff = change.get("diff")
            if isinstance(path, str) and isinstance(diff, str):
                print(f"    {path}:")
                print(diff, end="" if diff.endswith("\n") else "\n")

    print("  Warning: only the isolated local branch is affected.")
    print("  Staging is limited to the exact listed paths.")
    print("  No amend, merge, push, or branch deletion occurs.")
    print(
        "  Failed staging or commit can require manual recovery of the "
        "isolated index/worktree."
    )
    print("  No destructive automatic cleanup will occur.")
    try:
        answer = input("Approve isolated commit? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return ToolApprovalDecision.DENY
    if answer in {"y", "yes"}:
        return ToolApprovalDecision.APPROVE
    return ToolApprovalDecision.DENY


def _prompt_for_worktree_approval(
    request: WorktreeApprovalRequest,
) -> ToolApprovalDecision:
    """Render one exact lifecycle preview and request explicit approval."""

    preview = request.preview
    if not isinstance(preview, dict):
        return ToolApprovalDecision.DENY

    if request.action is WorktreeAction.CREATE:
        print("\nWorktree approval required: create")
        print(f"  Source repository: {preview.get('source_repository', '.')}")
        print(f"  Pinned HEAD: {preview.get('pinned_head', '[unavailable]')}")
        print(f"  New local branch: {preview.get('branch_name', '[unavailable]')}")
        print(f"  Target: {preview.get('target', '[unavailable]')}")
        _print_worktree_command(preview)
        print("  Warning: one local branch and worktree will be created.")
        print("  No commit, merge, or push will occur.")
        print("  The primary source working tree must remain clean.")
        print("  Ambiguous partial creation is preserved for manual recovery.")
        prompt = "Approve worktree creation? [y/N]: "
    else:
        print("\nWorktree approval required: remove")
        print(f"  Local branch: {preview.get('branch_name', '[unavailable]')}")
        print(f"  Worktree HEAD: {preview.get('worktree_head', '[unavailable]')}")
        print(f"  Target: {preview.get('target', '[unavailable]')}")
        _print_worktree_command(preview)
        print("  Only the verified clean worktree will be removed.")
        print("  The local branch will remain.")
        print("  No force, branch deletion, reset, clean, or stash will occur.")
        prompt = "Remove clean isolated worktree? [y/N]: "

    try:
        answer = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return ToolApprovalDecision.DENY
    if answer in {"y", "yes"}:
        return ToolApprovalDecision.APPROVE
    return ToolApprovalDecision.DENY


def _print_worktree_command(preview: dict[str, object]) -> None:
    """Render fixed safe command tokens from one lifecycle preview."""

    command = preview.get("command")
    if isinstance(command, list) and all(isinstance(token, str) for token in command):
        print(f"  Fixed command: {' '.join(command)}")
    else:
        print("  Fixed command: [unavailable]")


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
    elif tool_name == "apply_workspace_changes" and isinstance(preview, dict):
        print("  Transactional workspace change")
        print(f"  Files: {preview.get('operation_count', '[unavailable]')}")
        print(f"  Created: {preview.get('created_count', '[unavailable]')}")
        print(f"  Updated: {preview.get('updated_count', '[unavailable]')}")
        print(
            "  Total changed lines: "
            f"{preview.get('total_changed_lines', '[unavailable]')}"
        )
        print("  One approval covers the exact listed transaction only.")
        changes = preview.get("changes")
        if isinstance(changes, list):
            for change in changes:
                if not isinstance(change, dict):
                    print("  Change: [unavailable]")
                    continue
                print(f"  Path: {change.get('path', '[unavailable]')}")
                print(f"    Operation: {change.get('operation', '[unavailable]')}")
                print(
                    "    Bytes: "
                    f"{change.get('old_size_bytes', '[unavailable]')} → "
                    f"{change.get('new_size_bytes', '[unavailable]')}"
                )
                print(
                    f"    Changed lines: {change.get('changed_lines', '[unavailable]')}"
                )
                print("    Complete diff:")
                diff = change.get("diff")
                print(diff if isinstance(diff, str) else "[unavailable]")
        else:
            print("  Changes: [unavailable]")
        print("  Rollback covers handled in-process failures when rollback succeeds.")
        print(
            "  It does not cover power loss, abrupt process termination, "
            "filesystem failure, or rollback failure."
        )
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


def _display_approved_action_completion(
    round_: ToolInteractionRound,
    registry: ToolRegistry | None,
) -> None:
    """Display a compact result for each approval-required action."""

    if registry is None:
        return

    for invocation, result in zip(
        round_.response.tool_invocations,
        round_.results,
        strict=True,
    ):
        if not registry.requires_approval(invocation):
            continue

        print(f"Action completed: {invocation.tool_name}")
        print(f"  Status: {result.status}")


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
    if invocation.tool_name == "apply_workspace_changes":
        changes = arguments.get("changes")
        safe_changes = []
        if isinstance(changes, list):
            for change in changes:
                if not isinstance(change, dict):
                    continue
                expected_content = change.get("expected_content")
                replacement_content = change.get("replacement_content")
                safe_changes.append(
                    {
                        "path": change.get("path"),
                        "create_if_missing": change.get(
                            "create_if_missing",
                            False,
                        ),
                        "expected_content_bytes": _utf8_byte_count(
                            expected_content,
                        ),
                        "replacement_content_bytes": _utf8_byte_count(
                            replacement_content,
                        ),
                    }
                )
        return {
            "operation_count": len(safe_changes),
            "changes": safe_changes,
        }
    if invocation.tool_name != "apply_file_patch":
        return arguments

    expected_content = arguments.get("expected_content")
    replacement_content = arguments.get("replacement_content")
    return {
        "path": arguments.get("path"),
        "create_if_missing": arguments.get("create_if_missing", False),
        "expected_content_bytes": _utf8_byte_count(expected_content),
        "replacement_content_bytes": _utf8_byte_count(replacement_content),
    }


def _utf8_byte_count(value: object) -> int | None:
    """Return a safe UTF-8 byte count for valid text."""

    if not isinstance(value, str):
        return None
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return None


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
        "diff",
        "expected_content",
        "password",
        "replacement_content",
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
