"""Interactive command-line interface for Agent Workbench."""

import json
import sys
import unicodedata
from collections.abc import Sequence
from pathlib import Path

from agent_workbench.agents import AgentProfile
from agent_workbench.arguments import (
    DEFAULT_AUTONOMOUS_MAX_TOOL_ROUNDS,
    CLIArguments,
    RuntimeConfiguration,
    parse_cli_arguments,
    resolve_runtime_configuration,
)
from agent_workbench.coding_loop import (
    AutonomousCodingResult,
    CodingModelSendTrace,
    CodingProgressEvent,
    CodingProgressKind,
    CodingPhase,
    run_autonomous_coding_task,
)
from agent_workbench.config import (
    PROJECT_CONFIG_CONTEXT,
    ProjectCodingConfiguration,
    create_project_configuration,
    discover_project_configuration,
    load_environment,
    render_project_configuration,
)
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
from agent_workbench.lifecycle_recovery import (
    IsolatedCommitLifecycleRecoveryClassification,
    inspect_persisted_isolated_commit_lifecycle_recovery,
)
from agent_workbench.lifecycle_recovery_actions import (
    IsolatedCommitLifecycleRecoveryActionResult,
    IsolatedCommitLifecycleRecoveryActionStatus,
    adopt_isolated_commit_recovery_candidate,
)
from agent_workbench.lifecycle_store import IsolatedCommitLifecycleStore
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
CANCELLATION_MESSAGE = "[CANCELLED] Task cancelled by user. Workspace preserved."


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
    """Run the CLI and normalize operator cancellation."""

    try:
        _main(argv)
    except KeyboardInterrupt:
        print(CANCELLATION_MESSAGE)
        raise SystemExit(130) from None


def _main(
    argv: Sequence[str] | None = None,
) -> None:
    """Run the CLI using the resolved provider and model."""

    arguments = parse_cli_arguments(argv)
    if arguments.recover:
        _run_recovery_inspection(arguments)
        return

    load_environment()
    if arguments.init:
        _initialize_project(arguments)
        return

    task_prompt = getattr(arguments, "task_prompt", None)
    commit_message = getattr(arguments, "commit_message", None)

    try:
        if arguments.setup:
            runtime_configuration = run_interactive_setup()
        else:
            project_configuration = discover_project_configuration(
                arguments.workspace_root
                if arguments.workspace_root is not None
                else Path.cwd(),
                include_project_instructions=task_prompt is not None,
            )
            runtime_configuration = resolve_runtime_configuration(
                arguments,
                project_configuration=project_configuration,
            )
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}")
        raise SystemExit(1) from None

    if runtime_configuration.worktree_path is not None:
        _run_isolated_autonomous_task(
            runtime_configuration,
            task_prompt=task_prompt,
            commit_message=commit_message,
            lifecycle_store_directory=arguments.lifecycle_store,
            lifecycle_session_id=arguments.session_id,
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
        raise SystemExit(1) from None

    _run_configured_cli(
        session,
        runtime_configuration,
        task_prompt=task_prompt,
    )


def _initialize_project(arguments: CLIArguments) -> None:
    """Create one complete project coding configuration in the current directory."""

    provider_name = arguments.provider_name
    model_name = arguments.model_name
    agent_name = arguments.agent_name
    if provider_name is None or model_name is None or agent_name is None:
        print("Configuration error: Project initializer configuration is incomplete.")
        raise SystemExit(1)

    configuration = ProjectCodingConfiguration(
        provider=provider_name,
        model=model_name,
        agent=agent_name,
        enable_tools=arguments.enable_tools,
        enable_actions=arguments.enable_actions,
        max_tool_rounds=arguments.max_tool_rounds,
        temperature=arguments.temperature,
        top_p=arguments.top_p,
        max_output_tokens=arguments.max_output_tokens,
        isolated=arguments.isolated,
    )
    try:
        if arguments.dry_run:
            content = render_project_configuration(configuration)
        else:
            create_project_configuration(Path.cwd(), configuration)
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}")
        raise SystemExit(1) from None
    if arguments.dry_run:
        sys.stdout.write(content)
        return
    print(f"Created {PROJECT_CONFIG_CONTEXT}")


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
            show_assistant_summary=runtime_configuration.show_assistant_summary,
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
    show_assistant_summary: bool,
) -> None:
    """Run one supervised autonomous task and display its final result."""

    observer = _display_tool_round if show_tool_traces else None
    model_send_trace_arguments = (
        {"model_send_trace_observer": _display_model_send_trace}
        if show_tool_traces
        else {}
    )
    terminal_failure_displayed = False

    def display_progress(event: CodingProgressEvent) -> None:
        nonlocal terminal_failure_displayed
        _display_coding_progress(event)
        if event.kind is CodingProgressKind.TERMINAL_FAILURE:
            terminal_failure_displayed = True

    try:
        result = run_autonomous_coding_task(
            session,
            task_prompt,
            tool_approval_handler=_prompt_for_tool_approval,
            tool_round_observer=observer,
            progress_event_observer=display_progress,
            **model_send_trace_arguments,
        )
    except (
        CompletionError,
        ConfigurationError,
        ValueError,
        WorkspacePathError,
    ) as exc:
        if not terminal_failure_displayed:
            print(
                "[FAILED] Autonomous coding could not start: "
                f"{exc}; workspace preserved for manual recovery"
            )
        raise SystemExit(1) from None

    if show_assistant_summary:
        print(f"\nAssistant summary:\n{result.assistant_summary}")


def _run_isolated_autonomous_task(
    runtime_configuration: RuntimeConfiguration,
    *,
    task_prompt: str | None,
    commit_message: str | None,
    lifecycle_store_directory: Path | None,
    lifecycle_session_id: SessionId | None,
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
        raise SystemExit(1)

    observer = _display_tool_round if runtime_configuration.show_tool_traces else None
    model_send_trace_arguments = (
        {"model_send_trace_observer": _display_model_send_trace}
        if runtime_configuration.show_tool_traces
        else {}
    )
    terminal_failure_displayed = False

    def display_progress(event: CodingProgressEvent) -> None:
        nonlocal terminal_failure_displayed
        _display_coding_progress(event)
        if event.kind is CodingProgressKind.TERMINAL_FAILURE:
            terminal_failure_displayed = True

    lifecycle_store = None
    session_id = SessionId("cli-session")

    try:
        if lifecycle_store_directory is not None:
            if lifecycle_session_id is None:
                raise ConfigurationError(
                    "--lifecycle-store and --session-id must be supplied together."
                )
            lifecycle_store = IsolatedCommitLifecycleStore(lifecycle_store_directory)
            session_id = lifecycle_session_id

        result = run_isolated_autonomous_workflow(
            session_id,
            runtime_configuration,
            branch,
            target,
            task_prompt,
            commit_message,
            worktree_approval_handler=_prompt_for_worktree_approval,
            tool_approval_handler=_prompt_for_tool_approval,
            commit_approval_handler=_prompt_for_isolated_commit_approval,
            lifecycle_store=lifecycle_store,
            tool_round_observer=observer,
            progress_event_observer=display_progress,
            **model_send_trace_arguments,
            max_tool_rounds=runtime_configuration.max_tool_rounds,
        )
    except (
        CompletionError,
        ConfigurationError,
        ValueError,
        WorkspacePathError,
    ) as exc:
        if not terminal_failure_displayed:
            print(
                "[FAILED] Isolated autonomous workflow failed: "
                f"{exc}; workspace preserved for manual recovery"
            )
        raise SystemExit(1) from None

    _display_isolated_autonomous_result(
        result,
        show_assistant_summary=runtime_configuration.show_assistant_summary,
    )


def _run_recovery_inspection(arguments: CLIArguments) -> None:
    """Run recover inspection and optional explicit candidate adoption action."""

    source_repository = arguments.workspace_root
    lifecycle_store_path = arguments.lifecycle_store
    session_id = arguments.session_id
    if source_repository is None or lifecycle_store_path is None or session_id is None:
        print("Configuration error: Recovery command arguments are incomplete.")
        raise SystemExit(1)

    try:
        lifecycle_store = IsolatedCommitLifecycleStore(lifecycle_store_path)
        assessment = inspect_persisted_isolated_commit_lifecycle_recovery(
            source_repository,
            lifecycle_store,
            session_id,
        )
    except (CompletionError, ConfigurationError) as exc:
        print(f"Recovery inspection failed: {exc}")
        raise SystemExit(1) from None

    if assessment is None:
        print("Recovery inspection failed: requested lifecycle record was not found.")
        raise SystemExit(1)

    _print_recovery_assessment(assessment)
    guidance = _recovery_guidance_message(assessment.classification)
    print(f"[RECOVERY] Guidance: {guidance}")

    if not arguments.adopt_candidate:
        print("[RECOVERY] No recovery action was performed.")
        print("[RECOVERY] Any future mutating recovery action requires fresh approval.")
        return

    if (
        assessment.classification
        is not IsolatedCommitLifecycleRecoveryClassification.COMMIT_CANDIDATE_OBSERVED
    ):
        print(
            "Recovery adoption failed: --adopt-candidate requires "
            "classification commit_candidate_observed."
        )
        raise SystemExit(1)

    try:
        action_result = adopt_isolated_commit_recovery_candidate(
            source_repository,
            lifecycle_store,
            session_id,
            _prompt_for_candidate_recovery_approval,
        )
    except (CompletionError, ConfigurationError) as exc:
        print(f"Recovery adoption failed: {exc}")
        raise SystemExit(1) from None

    _display_candidate_adoption_result(action_result)


def _print_recovery_assessment(assessment) -> None:
    """Print one bounded conservative recovery assessment."""

    print(
        f"[RECOVERY] Persisted phase: {assessment.restart_evidence.persisted_phase.value}"
    )
    print(f"[RECOVERY] Classification: {assessment.classification.value}")
    if assessment.candidate_evidence is not None:
        print(
            "[RECOVERY] Candidate parent matches old HEAD: "
            f"{assessment.candidate_evidence.parent_matches_old_head.value}"
        )
        print(
            "[RECOVERY] Candidate commit message matches: "
            f"{assessment.candidate_evidence.message_fingerprint_matches.value}"
        )
        print(
            "[RECOVERY] Candidate committed paths match expected: "
            f"{assessment.candidate_evidence.paths_match_expected.value}"
        )


def _display_candidate_adoption_result(
    result: IsolatedCommitLifecycleRecoveryActionResult,
) -> None:
    """Display one bounded candidate-adoption outcome."""

    if result.status is IsolatedCommitLifecycleRecoveryActionStatus.DENIED:
        print("[RECOVERY] Candidate adoption denied.")
        print("[RECOVERY] No lifecycle mutation was performed.")
        raise SystemExit(1)

    print("[RECOVERY] Candidate adoption completed.")
    print(f"[RECOVERY] Persisted phase: {result.persisted_record.phase.value}")
    print(f"[RECOVERY] Classification: {result.assessment.classification.value}")
    print("[RECOVERY] Git was not modified.")
    print(
        "[RECOVERY] The approved candidate is now the exact persisted verified commit."
    )


def _prompt_for_candidate_recovery_approval(request) -> ToolApprovalDecision:
    """Render one complete exact candidate preview and ask for fresh approval."""

    preview = request.preview
    if not isinstance(preview, dict):
        return ToolApprovalDecision.DENY

    print("\nCandidate recovery approval required")
    print("  This candidate was not proven to be the exact originally approved commit.")
    print("  The complete candidate shown below is what is being freshly approved now.")
    print("  Git will not be modified by this recovery action.")
    print(
        "  Successful approval will only replace the persisted lifecycle "
        "checkpoint with VERIFIED for the exact displayed candidate commit."
    )
    print(f"  Recovery action: {preview.get('action', '[unavailable]')}")
    print(f"  Expected branch: {preview.get('branch', '[unavailable]')}")
    print(f"  Candidate commit: {preview.get('candidate_head', '[unavailable]')}")
    print(f"  Parent / old HEAD: {preview.get('old_head', '[unavailable]')}")
    print("  Commit message:")
    message, _ = _render_terminal_safe_text(
        preview.get("commit_message"),
        allow_newlines=True,
        allow_tabs=True,
    )
    print(message)
    print(f"  Operations: {preview.get('operation_count', '[unavailable]')}")
    print(f"  Added: {preview.get('added_count', '[unavailable]')}")
    print(f"  Modified: {preview.get('modified_count', '[unavailable]')}")
    print(f"  Changed lines: {preview.get('total_changed_lines', '[unavailable]')}")
    print("  Changed paths:")
    paths = preview.get("paths")
    if isinstance(paths, list) and all(isinstance(path, str) for path in paths):
        for path in paths:
            safe_path, _ = _render_terminal_safe_text(
                path,
                allow_newlines=False,
                allow_tabs=False,
            )
            print(f"    - {safe_path}")
    else:
        print("    [unavailable]")

    print("  Complete current candidate diff:")
    changes = preview.get("changes")
    if isinstance(changes, list):
        for change in changes:
            if not isinstance(change, dict):
                continue
            path, _ = _render_terminal_safe_text(
                change.get("path"),
                allow_newlines=False,
                allow_tabs=False,
            )
            diff, _ = _render_terminal_safe_text(
                change.get("diff"),
                allow_newlines=True,
                allow_tabs=True,
            )
            print(f"    {path}:")
            print(diff, end="" if diff.endswith("\n") else "\n")

    try:
        answer = input("Approve candidate adoption? [y/N]: ").strip().lower()
    except EOFError:
        print()
        return ToolApprovalDecision.DENY
    if answer in {"y", "yes"}:
        return ToolApprovalDecision.APPROVE
    return ToolApprovalDecision.DENY


def _recovery_guidance_message(
    classification: IsolatedCommitLifecycleRecoveryClassification,
) -> str:
    """Return one stable operator guidance message per classification."""

    if (
        classification
        is IsolatedCommitLifecycleRecoveryClassification.INSUFFICIENT_EVIDENCE
    ):
        return "Current evidence is insufficient for a safe recovery decision."
    if (
        classification
        is IsolatedCommitLifecycleRecoveryClassification.OLD_HEAD_CLEAN_INDEX
    ):
        return (
            "The expected branch remains at the old HEAD with no staged "
            "changes observed."
        )
    if (
        classification
        is IsolatedCommitLifecycleRecoveryClassification.EXPECTED_PATH_STAGING_OBSERVED
    ):
        return (
            "The expected persisted path set is currently staged; this does not "
            "prove the staged contents are the exact originally approved contents."
        )
    if (
        classification
        is IsolatedCommitLifecycleRecoveryClassification.COMMIT_CANDIDATE_OBSERVED
    ):
        return (
            "A compatible candidate commit is currently observed; this does not "
            "prove it is the exact originally approved commit."
        )
    if (
        classification
        is IsolatedCommitLifecycleRecoveryClassification.PERSISTED_VERIFIED_COMMIT_OBSERVED
    ):
        return "The exact persisted verified commit is currently observed on the expected branch."
    return (
        "Current Git state conflicts with persisted lifecycle evidence and "
        "requires operator/manual review."
    )


def _display_isolated_autonomous_result(
    result: IsolatedAutonomousWorkflowResult,
    *,
    show_assistant_summary: bool = False,
) -> None:
    """Display the verified isolated commit and preserved recovery state."""

    coding_result = result.coding_result
    commit_result = result.commit_result
    if show_assistant_summary:
        print(f"\nAssistant summary:\n{coding_result.assistant_summary}")
    print(
        f"[ISOLATED] Created local commit on {commit_result.branch_name} "
        f"with {commit_result.operation_count} changed "
        f"{'file' if commit_result.operation_count == 1 else 'files'}"
    )
    print(
        "[ISOLATED] Worktree clean; primary workspace unchanged; "
        "worktree and local branch preserved"
    )


def _display_coding_progress(event: CodingProgressEvent) -> None:
    """Render one concise stable controller-owned progress line."""

    label = event.phase.value
    if event.phase is CodingPhase.REPAIR and event.repair_attempt:
        label = f"REPAIR {event.repair_attempt}/{event.max_repair_attempts}"

    if event.kind is CodingProgressKind.PHASE_STARTED:
        messages = {
            CodingPhase.DISCOVER: "Inspecting workspace",
            CodingPhase.EDIT: "Applying controlled workspace changes",
            CodingPhase.VALIDATE: "Running controller-owned validation",
            CodingPhase.VERIFY: "Inspecting final workspace changes",
        }
        message = messages.get(event.phase)
    elif event.kind is CodingProgressKind.PHASE_COMPLETED:
        message = (
            "Inspection complete"
            if event.phase is CodingPhase.DISCOVER
            else "Phase complete"
        )
    elif event.kind is CodingProgressKind.WORKSPACE_CHANGED:
        message = f"Changed {event.path or '[unavailable]'}"
    elif event.kind is CodingProgressKind.ACTION_ARGUMENTS_REJECTED:
        target = f" for {event.path}" if event.path is not None else ""
        message = (
            f"Controlled action arguments rejected{target}: "
            f"{event.reason or 'unavailable'}"
        )
    elif event.kind is CodingProgressKind.ACTION_FAILED:
        target = f" for {event.path}" if event.path is not None else ""
        if event.later_action_rejected:
            message = (
                f"Later controlled action rejected{target}: "
                f"{event.reason or 'unavailable'}"
            )
        else:
            message = (
                f"Controlled action failed{target}: {event.reason or 'unavailable'}"
            )
    elif event.kind is CodingProgressKind.VALIDATION_RESULT:
        message = _format_validation_progress(event)
    elif event.kind is CodingProgressKind.REPAIR_STARTED:
        message = "Resolving validation failures"
    elif event.kind is CodingProgressKind.CHANGED_PATH_COUNT:
        count = event.changed_path_count or 0
        message = f"{count} changed {'file' if count == 1 else 'files'}"
    elif event.kind is CodingProgressKind.DONE:
        message = "Task completed successfully"
    elif event.kind is CodingProgressKind.TERMINAL_FAILURE:
        attempts = (
            f"; validation repair attempts "
            f"{event.repair_attempt}/{event.max_repair_attempts}"
            if event.max_repair_attempts
            else ""
        )
        preserved = (
            "; workspace preserved for manual recovery"
            if event.workspace_preserved
            else ""
        )
        print(
            f"[FAILED] {event.phase.value}: {event.reason or 'unavailable'}"
            f"{attempts}{preserved}"
        )
        return
    else:
        message = None

    if message is not None:
        print(f"[{label}] {message}")


def _display_model_send_trace(trace: CodingModelSendTrace) -> None:
    """Display safe metadata for one controller model send."""

    print("Model send:")
    print(f"  Phase: {trace.phase.value}")
    print(f"  Continuation: {trace.continuation}")
    print(f"  Decision mode: {'yes' if trace.decision_mode else 'no'}")
    print(f"  Tools: {', '.join(trace.allowed_tool_names)}")


def _format_validation_progress(event: CodingProgressEvent) -> str:
    """Render one validation result without exposing command output."""

    names = {
        "run_ruff_format": "Ruff format",
        "run_ruff_check": "Ruff check",
        "run_pytest": "Pytest",
    }
    name = names.get(event.tool_name or "", "Validation")
    if event.skipped:
        return f"{name} skipped: no approved changed Python files"
    passed = event.result_status == "success" and event.exit_code == 0
    status = "passed" if passed else "failed"
    if event.validation_summary:
        return f"{name} {status}: {event.validation_summary}"
    if event.exit_code is not None and not passed:
        return f"{name} {status}: exit code {event.exit_code}"
    return f"{name} {status}"


def _display_coding_evidence(result: AutonomousCodingResult) -> None:
    """Display deterministic workflow evidence in one stable shared order."""

    print(f"  Final phase: {result.final_phase.value}")
    print(f"  Tool rounds: {result.tool_round_count}")
    print(
        "  Workspace change applied: "
        f"{'yes' if result.workspace_change_applied else 'no'}"
    )
    print(f"  Repair attempts: {result.repair_attempt_count}")
    print(f"  Completion continuations: {result.completion_continuation_count}")
    print(f"  Validation succeeded: {'yes' if result.validation_succeeded else 'no'}")
    print(f"  Git status inspected: {'yes' if result.inspected_git_status else 'no'}")
    print(f"  Git diff inspected: {'yes' if result.inspected_git_diff else 'no'}")


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
    except EOFError:
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
    except EOFError:
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

    if tool_name in {
        "apply_file_patch",
        "apply_file_rewrite",
        "apply_text_replacement",
        "apply_line_range_replacement",
    } and isinstance(preview, dict):
        path, path_was_escaped = _render_terminal_safe_text(
            preview.get("path"),
            allow_newlines=False,
            allow_tabs=False,
        )
        diff, diff_was_escaped = _render_terminal_safe_text(
            preview.get("diff"),
            allow_newlines=True,
            allow_tabs=True,
        )
        if path_was_escaped or diff_was_escaped:
            print("  Note: Terminal control characters are shown as escaped text.")
        print(f"  Path: {path}")
        print(f"  Operation: {preview.get('operation', '[unavailable]')}")
        print(
            "  Bytes: "
            f"{preview.get('old_size_bytes', '[unavailable]')} → "
            f"{preview.get('new_size_bytes', '[unavailable]')}"
        )
        print(f"  Changed lines: {preview.get('changed_lines', '[unavailable]')}")
        if tool_name == "apply_text_replacement":
            print(
                "  Literal occurrences: "
                f"{preview.get('occurrences_replaced', '[unavailable]')}"
            )
        elif tool_name == "apply_line_range_replacement":
            print(
                "  Selected line range: "
                f"{preview.get('start_line', '[unavailable]')}–"
                f"{preview.get('end_line', '[unavailable]')}"
            )
        print("  Complete diff:")
        print(diff)
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
                path, path_was_escaped = _render_terminal_safe_text(
                    change.get("path"),
                    allow_newlines=False,
                    allow_tabs=False,
                )
                diff, diff_was_escaped = _render_terminal_safe_text(
                    change.get("diff"),
                    allow_newlines=True,
                    allow_tabs=True,
                )
                if path_was_escaped or diff_was_escaped:
                    print(
                        "  Note: Terminal control characters are shown as escaped text."
                    )
                print(f"  Path: {path}")
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
                print(diff)
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
    except EOFError:
        print()
        return ToolApprovalDecision.DENY

    if answer in {"y", "yes"}:
        return ToolApprovalDecision.APPROVE

    return ToolApprovalDecision.DENY


def _render_terminal_safe_text(
    value: object,
    *,
    allow_newlines: bool,
    allow_tabs: bool,
) -> tuple[str, bool]:
    """Render untrusted text without executable terminal control characters."""

    if not isinstance(value, str):
        return "[unavailable]", False

    rendered: list[str] = []
    was_escaped = False
    index = 0
    while index < len(value):
        character = value[index]
        codepoint = ord(character)
        if (
            character == "\r"
            and allow_newlines
            and index + 1 < len(value)
            and value[index + 1] == "\n"
        ):
            rendered.append("\n")
            was_escaped = True
            index += 2
            continue
        if character == "\n" and allow_newlines:
            rendered.append(character)
        elif character == "\t" and allow_tabs:
            rendered.append(character)
        elif codepoint < 0x20 or codepoint == 0x7F:
            rendered.append(f"\\x{codepoint:02x}")
            was_escaped = True
        elif 0x80 <= codepoint <= 0x9F:
            rendered.append(f"\\u{codepoint:04x}")
            was_escaped = True
        elif unicodedata.category(character) in {"Cf", "Zl", "Zp"}:
            rendered.append(
                f"\\u{codepoint:04x}" if codepoint <= 0xFFFF else f"\\U{codepoint:08x}"
            )
            was_escaped = True
        else:
            rendered.append(character)
        index += 1

    return "".join(rendered), was_escaped


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
    if invocation.tool_name == "apply_file_rewrite":
        return {
            "path": arguments.get("path"),
            "replacement_content_bytes": _utf8_byte_count(
                arguments.get("replacement_content"),
            ),
            "expected_file_sha256_present": isinstance(
                arguments.get("expected_file_sha256"),
                str,
            ),
        }
    if invocation.tool_name == "apply_text_replacement":
        return {
            "path": arguments.get("path"),
            "expected_occurrences": arguments.get("expected_occurrences", 1),
            "expected_text_bytes": _utf8_byte_count(
                arguments.get("expected_text"),
            ),
            "replacement_text_bytes": _utf8_byte_count(
                arguments.get("replacement_text"),
            ),
            "expected_file_sha256_present": isinstance(
                arguments.get("expected_file_sha256"),
                str,
            ),
        }
    if invocation.tool_name == "apply_line_range_replacement":
        return {
            "path": arguments.get("path"),
            "start_line": arguments.get("start_line"),
            "end_line": arguments.get("end_line"),
            "replacement_content_bytes": _utf8_byte_count(
                arguments.get("replacement_content"),
            ),
            "expected_file_sha256_present": isinstance(
                arguments.get("expected_file_sha256"),
                str,
            ),
        }
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
        "expected_text",
        "password",
        "replacement_content",
        "replacement_text",
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
