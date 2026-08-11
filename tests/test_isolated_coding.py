"""Tests for the complete isolated autonomous coding workflow."""

from dataclasses import FrozenInstanceError, replace
import hashlib
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_workbench.arguments import (
    CLIArguments,
    RuntimeConfiguration,
    resolve_runtime_configuration,
)
from agent_workbench.coding_loop import (
    AutonomousCodingResult,
    CodingPhase,
    ValidationRun,
)
from agent_workbench.errors import CompletionError, ConfigurationError
from agent_workbench.config import discover_project_configuration
from agent_workbench.isolated_coding import (
    IsolatedAutonomousWorkflowResult,
    run_isolated_autonomous_workflow,
)
from agent_workbench.lifecycle import (
    IsolatedCommitLifecyclePhase,
    IsolatedCommitLifecycleRecord,
)
from agent_workbench.lifecycle_store import IsolatedCommitLifecycleStore
from agent_workbench.session import SessionId
from agent_workbench.messages import ChatRequest, ChatResponse
from agent_workbench.tasks import TaskSpec
from agent_workbench.tools import ToolApprovalDecision
from agent_workbench.worktree_commits import MAX_COMMIT_MESSAGE_BYTES

SHA1_OLD = "a" * 40
SHA1_NEW = "b" * 40
SHA1_SOURCE = "c" * 40
DIFF_FP = "0" * 64
CMF = "1" * 64


def run_git(
    repository: Path,
    *arguments: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run Git against one disposable repository."""

    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=check,
        capture_output=True,
        text=True,
    )


def create_repository(root: Path) -> Path:
    """Create one clean Python repository with local commit identity."""

    root.mkdir()
    run_git(root, "init", "-b", "main")
    run_git(root, "config", "user.name", "Workflow Test User")
    run_git(root, "config", "user.email", "workflow-test@example.invalid")
    (root / "module.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left - right\n",
        encoding="utf-8",
    )
    (root / "test_module.py").write_text(
        "from module import add\n"
        "\n"
        "\n"
        "def test_add() -> None:\n"
        "    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    run_git(root, "add", ".")
    run_git(root, "commit", "-m", "initial")
    return root


def configuration(source: Path) -> RuntimeConfiguration:
    """Create one minimal source-bound runtime configuration."""

    return RuntimeConfiguration(
        provider_name="ollama",
        model_name="test-model",
        workspace_root=source,
        enable_actions=True,
    )


def approve(_request) -> ToolApprovalDecision:
    """Approve one disposable supervised action."""

    return ToolApprovalDecision.APPROVE


def coding_result(
    *,
    final_phase: CodingPhase = CodingPhase.DONE,
    workspace_change_applied: bool = True,
    validation_succeeded: bool = True,
    validation_after_change: bool = True,
    inspected_git_status: bool = True,
    inspected_git_diff: bool = True,
    git_inspection_after_change: bool = True,
    approved_workspace_paths: tuple[str, ...] = ("module.py",),
) -> AutonomousCodingResult:
    """Create one deterministic autonomous coding outcome."""

    change_index = 2 if workspace_change_applied else None
    validation_offset = 3 if validation_after_change else 0
    git_offset = 5 if git_inspection_after_change else 1
    pytest_exit_code = 0 if validation_succeeded else 1
    return AutonomousCodingResult(
        task_spec=TaskSpec(
            objective="Correct the add implementation.",
            acceptance_criteria=("Run validation.",),
        ),
        assistant_summary="Corrected the implementation.",
        final_phase=final_phase,
        workspace_change_applied=workspace_change_applied,
        repair_attempt_count=0,
        completion_continuation_count=0,
        tool_round_count=6,
        executed_tool_names=(
            "read_file",
            "apply_workspace_changes",
            "run_ruff_format",
            "run_ruff_check",
            "run_pytest",
            "inspect_git_status",
            "inspect_git_diff",
        ),
        approved_action_names=(
            "apply_workspace_changes",
            "run_ruff_format",
            "run_ruff_check",
            "run_pytest",
        ),
        validation_runs=(
            ValidationRun(
                tool_name="run_ruff_format",
                result_status="success",
                exit_code=0,
                sequence_index=validation_offset,
            ),
            ValidationRun(
                tool_name="run_ruff_check",
                result_status="success",
                exit_code=0,
                sequence_index=validation_offset + 1,
            ),
            ValidationRun(
                tool_name="run_pytest",
                result_status="success",
                exit_code=pytest_exit_code,
                sequence_index=validation_offset + 2,
            ),
        ),
        tool_results=(),
        inspected_git_status=inspected_git_status,
        inspected_git_diff=inspected_git_diff,
        last_workspace_change_sequence_index=change_index,
        latest_git_status_sequence_index=(git_offset if inspected_git_status else None),
        latest_git_diff_sequence_index=(git_offset + 1 if inspected_git_diff else None),
        approved_workspace_paths=approved_workspace_paths,
    )


class RecordingLifecycleStore(IsolatedCommitLifecycleStore):
    """Capture lifecycle writes while delegating to the real crash-safe store."""

    __slots__ = ("writes", "fail_on_call", "failure", "delegate_before_failure")

    def __init__(
        self,
        directory: Path,
        *,
        fail_on_call: int | None = None,
        failure: Exception | None = None,
        delegate_before_failure: bool = False,
    ) -> None:
        super().__init__(directory)
        self.writes: list[IsolatedCommitLifecycleRecord] = []
        self.fail_on_call = fail_on_call
        self.failure = failure or CompletionError("injected lifecycle store failure")
        self.delegate_before_failure = delegate_before_failure

    def write(self, record: IsolatedCommitLifecycleRecord) -> None:
        self.writes.append(record)
        should_fail = self.fail_on_call == len(self.writes)
        if should_fail and not self.delegate_before_failure:
            raise self.failure
        super().write(record)
        if should_fail and self.delegate_before_failure:
            raise self.failure


def create_lifecycle_store(
    tmp_path: Path,
    *,
    fail_on_call: int | None = None,
    failure: Exception | None = None,
    delegate_before_failure: bool = False,
) -> RecordingLifecycleStore:
    """Create one dedicated existing lifecycle store directory for testing."""

    directory = tmp_path / "lifecycle-store"
    directory.mkdir()
    return RecordingLifecycleStore(
        directory,
        fail_on_call=fail_on_call,
        failure=failure,
        delegate_before_failure=delegate_before_failure,
    )


def expected_lifecycle_filename(session_id: SessionId) -> str:
    """Return one deterministic lifecycle filename."""

    digest = hashlib.sha256(session_id.value.encode("utf-8")).hexdigest()
    return f"isolated-commit-{digest}.json"


def make_existing_lifecycle_record(
    session_id: SessionId,
    *,
    phase: IsolatedCommitLifecyclePhase = IsolatedCommitLifecyclePhase.PLANNED,
) -> IsolatedCommitLifecycleRecord:
    """Return one valid pre-existing lifecycle record for a session."""

    new_head = SHA1_NEW if phase is IsolatedCommitLifecyclePhase.VERIFIED else None
    return IsolatedCommitLifecycleRecord(
        session_id=session_id,
        phase=phase,
        target_display="../isolated",
        source_head=SHA1_SOURCE,
        source_branch="main",
        branch_name="agent/task",
        old_head=SHA1_OLD,
        paths=("module.py",),
        diff_fingerprint=DIFF_FP,
        commit_message_fingerprint=CMF,
        new_head=new_head,
    )


def install_successful_coding_stub(
    monkeypatch: pytest.MonkeyPatch,
    *,
    approved_workspace_paths: tuple[str, ...] = ("module.py",),
) -> dict[str, object]:
    """Patch isolated session creation and successful coding for workflow tests."""

    captured: dict[str, object] = {}

    def create_isolated(_session_id, _runtime, worktree, *, max_tool_rounds):
        assert max_tool_rounds == 16
        captured["worktree"] = worktree
        return SimpleNamespace(worktree=worktree, session=object())

    def run_coding(_session, _prompt, **_kwargs):
        worktree = captured["worktree"]
        worktree_path = worktree.worktree_path
        (worktree_path / "module.py").write_text(
            "def add(left: int, right: int) -> int:\n    return left + right\n",
            encoding="utf-8",
        )
        return coding_result(approved_workspace_paths=approved_workspace_paths)

    monkeypatch.setattr(
        "agent_workbench.isolated_coding.create_isolated_agent_session",
        create_isolated,
    )
    monkeypatch.setattr(
        "agent_workbench.isolated_coding.run_autonomous_coding_task",
        run_coding,
    )
    return captured


def assert_no_worktree_mutation(
    source: Path,
    target: Path,
    branch_name: str,
) -> None:
    """Require complete absence of branch, target, and source changes."""

    assert not target.exists()
    assert run_git(source, "branch", "--list", branch_name).stdout == ""
    assert run_git(source, "status", "--short").stdout == ""


def test_runs_isolated_task_and_creates_verified_local_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Keep the source unchanged while committing the isolated task result."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"
    original_head = run_git(source, "rev-parse", "HEAD").stdout.strip()
    captured = {}

    def create_isolated(session_id, runtime, worktree, *, max_tool_rounds):
        captured["session_id"] = session_id
        captured["runtime"] = runtime
        captured["worktree"] = worktree
        captured["max_tool_rounds"] = max_tool_rounds
        return SimpleNamespace(
            worktree=worktree,
            session=object(),
        )

    def run_coding(
        session,
        prompt,
        *,
        tool_approval_handler,
        tool_round_observer,
        progress_event_observer,
        acceptance_criteria,
    ):
        assert session is not None
        assert prompt == "Correct the add implementation."
        assert callable(tool_approval_handler)
        assert tool_round_observer is None
        assert progress_event_observer is None
        assert tuple(acceptance_criteria) == (
            "Implement the requested behavior with bounded workspace changes.",
            "Run Ruff formatting and static analysis and resolve introduced issues.",
            "Run pytest and resolve introduced regressions.",
            "Inspect the final Git status and diff before reporting completion.",
        )
        worktree = captured["worktree"]
        (worktree.worktree_path / "module.py").write_text(
            "def add(left: int, right: int) -> int:\n    return left + right\n",
            encoding="utf-8",
        )
        return coding_result()

    monkeypatch.setattr(
        "agent_workbench.isolated_coding.create_isolated_agent_session",
        create_isolated,
    )
    monkeypatch.setattr(
        "agent_workbench.isolated_coding.run_autonomous_coding_task",
        run_coding,
    )

    result = run_isolated_autonomous_workflow(
        SessionId("isolated-workflow"),
        configuration(source),
        "agent/fix-add",
        target,
        "Correct the add implementation.",
        "fix: correct add implementation",
        worktree_approval_handler=approve,
        tool_approval_handler=approve,
        commit_approval_handler=approve,
    )

    assert isinstance(result, IsolatedAutonomousWorkflowResult)
    assert not hasattr(result, "__dict__")
    with pytest.raises(FrozenInstanceError):
        result.coding_result = coding_result()  # type: ignore[misc]

    assert captured["session_id"] == SessionId("isolated-workflow")
    assert captured["runtime"] == configuration(source)
    assert captured["max_tool_rounds"] == 16
    assert result.worktree is captured["worktree"]
    assert result.commit_result.branch_name == "agent/fix-add"
    assert result.commit_result.commit_message == "fix: correct add implementation"
    assert result.commit_result.paths == ("module.py",)
    assert result.final_worktree_state.clean is True
    assert result.final_worktree_state.head == result.commit_result.new_head

    assert run_git(source, "rev-parse", "HEAD").stdout.strip() == original_head
    assert (
        (source / "module.py")
        .read_text(encoding="utf-8")
        .endswith("return left - right\n")
    )
    assert (
        (target / "module.py")
        .read_text(encoding="utf-8")
        .endswith("return left + right\n")
    )
    assert run_git(target, "status", "--short").stdout == ""
    assert (
        run_git(target, "log", "-1", "--pretty=%s").stdout.strip()
        == "fix: correct add implementation"
    )


def test_isolated_coding_uses_discovered_project_instructions_in_provider_request(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Cover exact-root discovery through an isolated provider request and task."""

    source = create_repository(tmp_path / "source")
    configuration_directory = source / ".agent-workbench"
    configuration_directory.mkdir()
    (configuration_directory / "config.toml").write_text(
        '[coding]\nprovider = "ollama"\nmodel = "test-model"\nenable_actions = true\n',
        encoding="utf-8",
    )
    project_instructions = "# Isolated Project\n\n- Preserve isolation.\n"
    (configuration_directory / "instructions.md").write_text(
        project_instructions,
        encoding="utf-8",
    )
    run_git(source, "add", ".agent-workbench")
    run_git(source, "commit", "-m", "add project configuration")
    nested = source / "src" / "package"
    nested.mkdir(parents=True)
    discovered = discover_project_configuration(
        nested,
        include_project_instructions=True,
    )
    assert discovered is not None
    task = "Correct the add implementation."
    runtime = resolve_runtime_configuration(
        CLIArguments(
            provider_name=None,
            model_name=None,
            task_prompt=task,
            system_prompt="Existing system instructions.",
        ),
        project_configuration=discovered,
    )
    target = tmp_path / "isolated"
    requests: list[ChatRequest] = []

    def complete(request: ChatRequest) -> ChatResponse:
        requests.append(request)
        return ChatResponse(text="Captured.")

    provider = SimpleNamespace(
        name="Fake",
        model_name="test-model",
        complete=complete,
    )
    monkeypatch.setattr(
        "agent_workbench.session_factory.create_provider",
        lambda _provider_name, _model_name: provider,
    )

    def run_coding(session, prompt, **_kwargs):
        assert prompt == task
        session.send(prompt, allowed_tool_names=())
        (target / "module.py").write_text(
            "def add(left: int, right: int) -> int:\n    return left + right\n",
            encoding="utf-8",
        )
        return coding_result()

    monkeypatch.setattr(
        "agent_workbench.isolated_coding.run_autonomous_coding_task",
        run_coding,
    )

    run_isolated_autonomous_workflow(
        SessionId("isolated-project-instructions"),
        runtime,
        "agent/project-instructions",
        target,
        task,
        "fix: use project instructions",
        worktree_approval_handler=approve,
        tool_approval_handler=approve,
        commit_approval_handler=approve,
    )

    expected_system_prompt = (
        "Existing system instructions.\n\n"
        "<project_instructions>\n"
        f"{project_instructions}\n"
        "</project_instructions>"
    )
    assert requests[0].system_prompt == expected_system_prompt
    assert requests[0].system_prompt.count(project_instructions) == 1
    assert requests[0].messages == [{"role": "user", "content": task}]
    assert nested.is_dir()
    assert run_git(source, "status", "--short").stdout == ""


def test_commits_exact_new_files_after_untracked_verification(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Commit only the two safe created files accepted by deterministic gates."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"
    captured = {}

    def create_isolated(_session_id, _runtime, worktree, *, max_tool_rounds):
        assert max_tool_rounds == 16
        captured["worktree"] = worktree
        return SimpleNamespace(worktree=worktree, session=object())

    def run_coding(_session, _prompt, **_kwargs):
        worktree = captured["worktree"]
        (worktree.worktree_path / "created_module.py").write_text(
            "def multiply(left: int, right: int) -> int:\n    return left * right\n",
            encoding="utf-8",
        )
        (worktree.worktree_path / "test_created_module.py").write_text(
            "from created_module import multiply\n"
            "\n"
            "\n"
            "def test_multiply() -> None:\n"
            "    assert multiply(2, 3) == 6\n",
            encoding="utf-8",
        )
        return coding_result(
            approved_workspace_paths=(
                "created_module.py",
                "test_created_module.py",
            )
        )

    monkeypatch.setattr(
        "agent_workbench.isolated_coding.create_isolated_agent_session",
        create_isolated,
    )
    monkeypatch.setattr(
        "agent_workbench.isolated_coding.run_autonomous_coding_task",
        run_coding,
    )

    result = run_isolated_autonomous_workflow(
        SessionId("isolated-new-files"),
        configuration(source),
        "agent/new-files",
        target,
        "Create a multiplication module and focused test.",
        "feat: add multiplication module",
        worktree_approval_handler=approve,
        tool_approval_handler=approve,
        commit_approval_handler=approve,
    )

    assert result.commit_result.paths == (
        "created_module.py",
        "test_created_module.py",
    )
    assert run_git(target, "show", "--format=", "--name-only", "HEAD").stdout == (
        "created_module.py\ntest_created_module.py\n"
    )
    assert run_git(target, "status", "--short").stdout == ""
    assert run_git(target, "ls-files", "--others", "--exclude-standard").stdout == ""
    assert run_git(source, "status", "--short").stdout == ""
    assert not (source / "created_module.py").exists()
    assert not (source / "test_created_module.py").exists()


def test_lifecycle_store_none_preserves_existing_successful_behavior(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Keep the workflow unchanged when no lifecycle store is injected."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"
    install_successful_coding_stub(monkeypatch)
    monkeypatch.setattr(
        "agent_workbench.isolated_coding._build_lifecycle_record",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("lifecycle records must not be constructed")
        ),
    )

    result = run_isolated_autonomous_workflow(
        SessionId("no-store"),
        configuration(source),
        "agent/no-store",
        target,
        "Correct the add implementation.",
        "fix: correct add implementation",
        worktree_approval_handler=approve,
        tool_approval_handler=approve,
        commit_approval_handler=approve,
        lifecycle_store=None,
    )

    assert result.commit_result.branch_name == "agent/no-store"


def test_invalid_lifecycle_store_is_rejected_before_worktree_creation(
    tmp_path: Path,
) -> None:
    """Reject invalid lifecycle store values before creating any worktree."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"

    with pytest.raises(ConfigurationError, match="lifecycle store"):
        run_isolated_autonomous_workflow(
            SessionId("invalid-store"),
            configuration(source),
            "agent/invalid-store",
            target,
            "Correct the add implementation.",
            "fix: valid",
            worktree_approval_handler=approve,
            tool_approval_handler=approve,
            commit_approval_handler=approve,
            lifecycle_store=object(),  # type: ignore[arg-type]
        )

    assert_no_worktree_mutation(source, target, "agent/invalid-store")


def test_existing_lifecycle_record_blocks_session_reuse_before_worktree_creation(
    tmp_path: Path,
) -> None:
    """Refuse to overwrite unresolved persisted lifecycle state for a session."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"
    session_id = SessionId("existing-lifecycle")
    store = create_lifecycle_store(tmp_path)
    existing = make_existing_lifecycle_record(session_id)
    store.write(existing)

    with pytest.raises(CompletionError, match="persisted lifecycle state"):
        run_isolated_autonomous_workflow(
            session_id,
            configuration(source),
            "agent/existing-lifecycle",
            target,
            "Correct the add implementation.",
            "fix: valid",
            worktree_approval_handler=approve,
            tool_approval_handler=approve,
            commit_approval_handler=approve,
            lifecycle_store=store,
        )

    assert store.read(session_id) == existing
    assert_no_worktree_mutation(source, target, "agent/existing-lifecycle")


def test_corrupt_existing_lifecycle_state_fails_before_worktree_creation(
    tmp_path: Path,
) -> None:
    """Propagate invalid persisted lifecycle data before creating any worktree."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"
    session_id = SessionId("corrupt-lifecycle")
    store = create_lifecycle_store(tmp_path)
    payload_path = store._directory / expected_lifecycle_filename(session_id)  # type: ignore[attr-defined]
    payload_path.write_bytes(b"{not-json\n")

    with pytest.raises(ConfigurationError, match="valid JSON"):
        run_isolated_autonomous_workflow(
            session_id,
            configuration(source),
            "agent/corrupt-lifecycle",
            target,
            "Correct the add implementation.",
            "fix: valid",
            worktree_approval_handler=approve,
            tool_approval_handler=approve,
            commit_approval_handler=approve,
            lifecycle_store=store,
        )

    assert_no_worktree_mutation(source, target, "agent/corrupt-lifecycle")


def test_successful_workflow_persists_planned_execution_started_and_verified(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Persist lifecycle checkpoints in order with stable fields across phases."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"
    session_id = SessionId("checkpoint-order")
    commit_message = "fix: correct add implementation"
    store = create_lifecycle_store(tmp_path)
    install_successful_coding_stub(monkeypatch)
    approval_calls = 0

    def commit_approval_handler(_request):
        nonlocal approval_calls
        approval_calls += 1
        return ToolApprovalDecision.APPROVE

    result = run_isolated_autonomous_workflow(
        session_id,
        configuration(source),
        "agent/checkpoint-order",
        target,
        "Correct the add implementation.",
        commit_message,
        worktree_approval_handler=approve,
        tool_approval_handler=approve,
        commit_approval_handler=commit_approval_handler,
        lifecycle_store=store,
    )

    assert approval_calls == 1
    assert [record.phase for record in store.writes] == [
        IsolatedCommitLifecyclePhase.PLANNED,
        IsolatedCommitLifecyclePhase.EXECUTION_STARTED,
        IsolatedCommitLifecyclePhase.VERIFIED,
    ]
    persisted = store.read(session_id)
    assert persisted is not None
    assert persisted.phase is IsolatedCommitLifecyclePhase.VERIFIED
    assert persisted.new_head == result.commit_result.new_head

    first = store.writes[0]
    expected_message_fingerprint = hashlib.sha256(
        commit_message.encode("utf-8")
    ).hexdigest()
    for record in store.writes:
        assert record.session_id == session_id
        assert (
            record.target_display
            == first.target_display
            == result.worktree.target_display
        )
        assert record.source_head == first.source_head == result.worktree.source_head
        assert record.source_branch == first.source_branch == "main"
        assert record.branch_name == first.branch_name == result.worktree.branch_name
        assert record.old_head == first.old_head == result.commit_result.old_head
        assert record.paths == first.paths == result.commit_result.paths
        assert record.diff_fingerprint == first.diff_fingerprint
        assert record.commit_message_fingerprint == expected_message_fingerprint
    assert store.writes[0].new_head is None
    assert store.writes[1].new_head is None
    assert store.writes[2].new_head == result.commit_result.new_head

    stored_bytes = (
        tmp_path / "lifecycle-store" / expected_lifecycle_filename(session_id)
    ).read_text(encoding="utf-8")
    assert commit_message not in stored_bytes


def test_planned_persistence_failure_prevents_approval_and_commit_mutation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Abort before commit approval or Git mutation when PLANNED persistence fails."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"
    session_id = SessionId("planned-failure")
    store = create_lifecycle_store(
        tmp_path,
        fail_on_call=1,
        failure=CompletionError("injected planned failure"),
    )
    install_successful_coding_stub(monkeypatch)
    commit_approval = Mock(
        side_effect=AssertionError("commit approval must not be requested")
    )

    with pytest.raises(
        CompletionError, match="PLANNED lifecycle checkpoint persistence"
    ):
        run_isolated_autonomous_workflow(
            session_id,
            configuration(source),
            "agent/planned-failure",
            target,
            "Correct the add implementation.",
            "fix: valid",
            worktree_approval_handler=approve,
            tool_approval_handler=approve,
            commit_approval_handler=commit_approval,
            lifecycle_store=store,
        )

    assert not commit_approval.called
    assert store.read(session_id) is None
    assert target.exists()
    assert run_git(target, "diff", "--cached", "--quiet").returncode == 0
    assert run_git(target, "log", "-1", "--pretty=%s").stdout.strip() == "initial"


def test_approval_denial_leaves_planned_as_latest_lifecycle_record(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Keep PLANNED persisted when commit approval is denied."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"
    session_id = SessionId("approval-denied")
    store = create_lifecycle_store(tmp_path)
    install_successful_coding_stub(monkeypatch)

    with pytest.raises(CompletionError, match="approval was denied"):
        run_isolated_autonomous_workflow(
            session_id,
            configuration(source),
            "agent/approval-denied",
            target,
            "Correct the add implementation.",
            "fix: valid",
            worktree_approval_handler=approve,
            tool_approval_handler=approve,
            commit_approval_handler=lambda _request: ToolApprovalDecision.DENY,
            lifecycle_store=store,
        )

    persisted = store.read(session_id)
    assert persisted is not None
    assert persisted.phase is IsolatedCommitLifecyclePhase.PLANNED
    assert [record.phase for record in store.writes] == [
        IsolatedCommitLifecyclePhase.PLANNED
    ]


def test_post_approval_stale_plan_leaves_planned_and_never_writes_execution_started(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Do not advance lifecycle state when post-approval revalidation is stale."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"
    session_id = SessionId("stale-plan")
    store = create_lifecycle_store(tmp_path)
    captured = install_successful_coding_stub(monkeypatch)

    def stale_after_approval(_request):
        worktree = captured["worktree"]
        (worktree.worktree_path / "module.py").write_text(
            "def add(left: int, right: int) -> int:\n    return left * right\n",
            encoding="utf-8",
        )
        return ToolApprovalDecision.APPROVE

    with pytest.raises(CompletionError, match="stale"):
        run_isolated_autonomous_workflow(
            session_id,
            configuration(source),
            "agent/stale-plan",
            target,
            "Correct the add implementation.",
            "fix: valid",
            worktree_approval_handler=approve,
            tool_approval_handler=approve,
            commit_approval_handler=stale_after_approval,
            lifecycle_store=store,
        )

    persisted = store.read(session_id)
    assert persisted is not None
    assert persisted.phase is IsolatedCommitLifecyclePhase.PLANNED
    assert [record.phase for record in store.writes] == [
        IsolatedCommitLifecyclePhase.PLANNED
    ]


def test_execution_started_persistence_failure_occurs_before_git_add(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Abort before staging when EXECUTION_STARTED persistence fails."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"
    session_id = SessionId("execution-started-failure")
    store = create_lifecycle_store(
        tmp_path,
        fail_on_call=2,
        failure=CompletionError("injected execution-started failure"),
    )
    install_successful_coding_stub(monkeypatch)

    with pytest.raises(CompletionError, match="Pre-mutation checkpoint failed"):
        run_isolated_autonomous_workflow(
            session_id,
            configuration(source),
            "agent/execution-started-failure",
            target,
            "Correct the add implementation.",
            "fix: valid",
            worktree_approval_handler=approve,
            tool_approval_handler=approve,
            commit_approval_handler=approve,
            lifecycle_store=store,
        )

    persisted = store.read(session_id)
    assert persisted is not None
    assert persisted.phase is IsolatedCommitLifecyclePhase.PLANNED
    assert target.exists()
    assert run_git(target, "diff", "--cached", "--quiet").returncode == 0
    assert run_git(target, "log", "-1", "--pretty=%s").stdout.strip() == "initial"


def test_staging_failure_after_execution_started_leaves_that_phase_persisted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Preserve EXECUTION_STARTED when staging fails after the checkpoint."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"
    session_id = SessionId("stage-failure")
    store = create_lifecycle_store(tmp_path)
    install_successful_coding_stub(monkeypatch)
    module = pytest.importorskip("agent_workbench.worktree_commits")
    original_run_git = module._run_git

    def fail_stage(repository, arguments, *, input_bytes=None):
        arguments = tuple(arguments)
        if arguments[:2] == ("add", "--"):
            original_run_git(repository, ("add", "--", "module.py"))
            return module._GitOutput(1, b"", b"injected")
        return original_run_git(repository, arguments, input_bytes=input_bytes)

    monkeypatch.setattr(module, "_run_git", fail_stage)

    with pytest.raises(CompletionError, match="Isolated commit creation failed"):
        run_isolated_autonomous_workflow(
            session_id,
            configuration(source),
            "agent/stage-failure",
            target,
            "Correct the add implementation.",
            "fix: valid",
            worktree_approval_handler=approve,
            tool_approval_handler=approve,
            commit_approval_handler=approve,
            lifecycle_store=store,
        )

    persisted = store.read(session_id)
    assert persisted is not None
    assert persisted.phase is IsolatedCommitLifecyclePhase.EXECUTION_STARTED


def test_commit_failure_after_execution_started_leaves_that_phase_persisted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Preserve EXECUTION_STARTED when commit creation fails after staging."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"
    session_id = SessionId("commit-failure")
    store = create_lifecycle_store(tmp_path)
    install_successful_coding_stub(monkeypatch)
    module = pytest.importorskip("agent_workbench.worktree_commits")
    original_run_git = module._run_git

    def fail_commit(repository, arguments, *, input_bytes=None):
        arguments = tuple(arguments)
        if "commit" in arguments:
            return module._GitOutput(1, b"", b"injected")
        return original_run_git(repository, arguments, input_bytes=input_bytes)

    monkeypatch.setattr(module, "_run_git", fail_commit)

    with pytest.raises(CompletionError, match="Isolated commit creation failed"):
        run_isolated_autonomous_workflow(
            session_id,
            configuration(source),
            "agent/commit-failure",
            target,
            "Correct the add implementation.",
            "fix: valid",
            worktree_approval_handler=approve,
            tool_approval_handler=approve,
            commit_approval_handler=approve,
            lifecycle_store=store,
        )

    persisted = store.read(session_id)
    assert persisted is not None
    assert persisted.phase is IsolatedCommitLifecyclePhase.EXECUTION_STARTED


def test_verified_persistence_failure_reports_failure_without_rolling_back_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Keep the created commit and preserved worktree when VERIFIED persistence fails."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"
    session_id = SessionId("verified-failure")
    store = create_lifecycle_store(
        tmp_path,
        fail_on_call=3,
        failure=CompletionError("injected verified failure"),
    )
    install_successful_coding_stub(monkeypatch)

    with pytest.raises(
        CompletionError, match="VERIFIED lifecycle checkpoint persistence"
    ):
        run_isolated_autonomous_workflow(
            session_id,
            configuration(source),
            "agent/verified-failure",
            target,
            "Correct the add implementation.",
            "fix: verified failure",
            worktree_approval_handler=approve,
            tool_approval_handler=approve,
            commit_approval_handler=approve,
            lifecycle_store=store,
        )

    persisted = store.read(session_id)
    assert persisted is not None
    assert persisted.phase is IsolatedCommitLifecyclePhase.EXECUTION_STARTED
    assert (
        run_git(target, "log", "-1", "--pretty=%s").stdout.strip()
        == "fix: verified failure"
    )
    assert (
        run_git(target, "branch", "--show-current").stdout.strip()
        == "agent/verified-failure"
    )


def test_final_worktree_verification_failure_leaves_verified_persisted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Keep VERIFIED persisted even if the final workflow inspection later fails."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"
    session_id = SessionId("final-verification-failure")
    store = create_lifecycle_store(tmp_path)
    install_successful_coding_stub(monkeypatch)
    monkeypatch.setattr(
        "agent_workbench.isolated_coding.inspect_git_worktree",
        lambda _worktree: (_ for _ in ()).throw(
            CompletionError("injected final failure")
        ),
    )

    with pytest.raises(CompletionError, match="Final worktree verification failed"):
        run_isolated_autonomous_workflow(
            session_id,
            configuration(source),
            "agent/final-verification-failure",
            target,
            "Correct the add implementation.",
            "fix: final verification failure",
            worktree_approval_handler=approve,
            tool_approval_handler=approve,
            commit_approval_handler=approve,
            lifecycle_store=store,
        )

    persisted = store.read(session_id)
    assert persisted is not None
    assert persisted.phase is IsolatedCommitLifecyclePhase.VERIFIED


def test_rejects_unrelated_untracked_file_before_commit_mutation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Preserve every path when worktree changes exceed approved action paths."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"
    original_head = run_git(source, "rev-parse", "HEAD").stdout.strip()
    captured = {}

    def create_isolated(_session_id, _runtime, worktree, *, max_tool_rounds):
        assert max_tool_rounds == 16
        captured["worktree"] = worktree
        return SimpleNamespace(worktree=worktree, session=object())

    def run_coding(_session, _prompt, **_kwargs):
        worktree = captured["worktree"]
        for path in (
            "created_module.py",
            "test_created_module.py",
            "unrelated-notes.txt",
        ):
            (worktree.worktree_path / path).write_text(
                f"{path}\n",
                encoding="utf-8",
            )
        return coding_result(
            approved_workspace_paths=(
                "created_module.py",
                "test_created_module.py",
            )
        )

    commit_approval = Mock(
        side_effect=AssertionError("commit approval must not be requested")
    )
    monkeypatch.setattr(
        "agent_workbench.isolated_coding.create_isolated_agent_session",
        create_isolated,
    )
    monkeypatch.setattr(
        "agent_workbench.isolated_coding.run_autonomous_coding_task",
        run_coding,
    )

    with pytest.raises(
        CompletionError,
        match=(
            r"Isolated commit planning failed: isolated commit contains a "
            r"changed path outside the successful approved workspace actions"
        ),
    ):
        run_isolated_autonomous_workflow(
            SessionId("isolated-unrelated-file"),
            configuration(source),
            "agent/unrelated-file",
            target,
            "Create a multiplication module and focused test.",
            "feat: add multiplication module",
            worktree_approval_handler=approve,
            tool_approval_handler=approve,
            commit_approval_handler=commit_approval,
        )

    assert not commit_approval.called
    assert run_git(target, "diff", "--cached", "--name-only").stdout == ""
    assert run_git(target, "status", "--short").stdout == (
        "?? created_module.py\n?? test_created_module.py\n?? unrelated-notes.txt\n"
    )
    assert (target / "unrelated-notes.txt").read_text(encoding="utf-8") == (
        "unrelated-notes.txt\n"
    )
    assert run_git(target, "log", "-1", "--pretty=%s").stdout.strip() == "initial"
    assert run_git(source, "rev-parse", "HEAD").stdout.strip() == original_head
    assert run_git(source, "status", "--short").stdout == ""


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (
            coding_result(final_phase=CodingPhase.VERIFY),
            "deterministic DONE phase",
        ),
        (
            coding_result(workspace_change_applied=False),
            "successful approved workspace change",
        ),
        (
            coding_result(validation_succeeded=False),
            "successful Ruff and pytest validation after the latest workspace change",
        ),
        (
            coding_result(validation_after_change=False),
            "successful Ruff and pytest validation after the latest workspace change",
        ),
        (
            coding_result(inspected_git_status=False),
            "final Git status and diff after the latest workspace change",
        ),
        (
            coding_result(inspected_git_diff=False),
            "final Git status and diff after the latest workspace change",
        ),
        (
            coding_result(git_inspection_after_change=False),
            "final Git status and diff after the latest workspace change",
        ),
    ],
)
def test_failed_commit_gate_preserves_dirty_worktree_without_commit(
    tmp_path: Path,
    monkeypatch,
    result: AutonomousCodingResult,
    message: str,
) -> None:
    """Preserve isolated changes when validation or inspection is incomplete."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"
    original_head = run_git(source, "rev-parse", "HEAD").stdout.strip()
    captured = {}

    def create_isolated(_session_id, _runtime, worktree, *, max_tool_rounds):
        assert max_tool_rounds == 16
        captured["worktree"] = worktree
        return SimpleNamespace(
            worktree=worktree,
            session=object(),
        )

    def run_coding(_session, _prompt, **_kwargs):
        worktree = captured["worktree"]
        (worktree.worktree_path / "module.py").write_text(
            "def add(left: int, right: int) -> int:\n    return left + right\n",
            encoding="utf-8",
        )
        return result

    def reject_commit_planning(*_args, **_kwargs):
        raise AssertionError("commit planning must not run")

    monkeypatch.setattr(
        "agent_workbench.isolated_coding.create_isolated_agent_session",
        create_isolated,
    )
    monkeypatch.setattr(
        "agent_workbench.isolated_coding.run_autonomous_coding_task",
        run_coding,
    )
    monkeypatch.setattr(
        "agent_workbench.isolated_coding.plan_isolated_commit",
        reject_commit_planning,
    )

    with pytest.raises(CompletionError, match=message):
        run_isolated_autonomous_workflow(
            SessionId("failed-gate"),
            configuration(source),
            "agent/failed-gate",
            target,
            "Correct the add implementation.",
            "fix: should not be created",
            worktree_approval_handler=approve,
            tool_approval_handler=approve,
            commit_approval_handler=approve,
        )

    assert target.exists()
    assert run_git(target, "status", "--short").stdout == " M module.py\n"
    assert run_git(source, "rev-parse", "HEAD").stdout.strip() == original_head
    assert run_git(source, "branch", "--list", "agent/failed-gate").stdout.strip()
    assert run_git(target, "log", "-1", "--pretty=%s").stdout.strip() == "initial"


def test_deterministic_coding_failure_never_plans_a_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Preserve the isolated worktree before any commit planning on failure."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"
    captured = {}

    def create_isolated(_session_id, _runtime, worktree, *, max_tool_rounds):
        assert max_tool_rounds == 16
        captured["worktree"] = worktree
        return SimpleNamespace(worktree=worktree, session=object())

    def fail_coding(_session, _prompt, **_kwargs):
        worktree = captured["worktree"]
        (worktree.worktree_path / "module.py").write_text(
            "def add(left: int, right: int) -> int:\n    return left * right\n",
            encoding="utf-8",
        )
        raise CompletionError(
            "Deterministic coding failed in phase VALIDATE: pytest failed. "
            "repair_attempts=2, completion_continuations=0."
        )

    commit_planning = Mock(side_effect=AssertionError("commit planning must not run"))
    monkeypatch.setattr(
        "agent_workbench.isolated_coding.create_isolated_agent_session",
        create_isolated,
    )
    monkeypatch.setattr(
        "agent_workbench.isolated_coding.run_autonomous_coding_task",
        fail_coding,
    )
    monkeypatch.setattr(
        "agent_workbench.isolated_coding.plan_isolated_commit",
        commit_planning,
    )

    with pytest.raises(
        CompletionError,
        match=r"Autonomous coding failed: Deterministic coding failed in phase VALIDATE",
    ):
        run_isolated_autonomous_workflow(
            SessionId("deterministic-failure"),
            configuration(source),
            "agent/deterministic-failure",
            target,
            "Correct the add implementation.",
            "fix: must not be created",
            worktree_approval_handler=approve,
            tool_approval_handler=approve,
            commit_approval_handler=approve,
        )

    commit_planning.assert_not_called()
    assert target.exists()
    assert run_git(target, "status", "--short").stdout == " M module.py\n"
    assert run_git(target, "log", "-1", "--pretty=%s").stdout.strip() == "initial"


@pytest.mark.parametrize(
    ("session_id", "disable_actions", "criteria", "message", "error"),
    [
        (
            "invalid-session",
            False,
            ("criterion",),
            "fix: valid",
            "requires a SessionId",
        ),
        (
            SessionId("disabled-actions"),
            True,
            ("criterion",),
            "fix: valid",
            "requires controlled actions",
        ),
        (
            SessionId("invalid-criteria"),
            False,
            "not a criteria collection",
            "fix: valid",
            "task specification is invalid",
        ),
        (
            SessionId("invalid-message"),
            False,
            ("criterion",),
            "-invalid",
            "must not begin",
        ),
        (
            SessionId("oversized-message"),
            False,
            ("criterion",),
            "x" * (MAX_COMMIT_MESSAGE_BYTES + 1),
            "byte limit",
        ),
    ],
)
def test_invalid_inputs_are_rejected_before_worktree_creation(
    tmp_path: Path,
    session_id: object,
    disable_actions: bool,
    criteria: object,
    message: str,
    error: str,
) -> None:
    """Perform no Git mutation when any workflow preflight input is invalid."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"
    runtime = configuration(source)
    if disable_actions:
        runtime = replace(runtime, enable_actions=False)

    with pytest.raises(ConfigurationError, match=error):
        run_isolated_autonomous_workflow(
            session_id,  # type: ignore[arg-type]
            runtime,
            "agent/invalid",
            target,
            "Correct the add implementation.",
            message,
            worktree_approval_handler=approve,
            tool_approval_handler=approve,
            commit_approval_handler=approve,
            acceptance_criteria=criteria,  # type: ignore[arg-type]
        )

    assert_no_worktree_mutation(source, target, "agent/invalid")


def test_invalid_handler_is_rejected_before_worktree_creation(
    tmp_path: Path,
) -> None:
    """Reject an invalid approval callback before any Git mutation."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"

    with pytest.raises(ConfigurationError, match="tool approval handler"):
        run_isolated_autonomous_workflow(
            SessionId("invalid-handler"),
            configuration(source),
            "agent/invalid-handler",
            target,
            "Correct the add implementation.",
            "fix: valid",
            worktree_approval_handler=approve,
            tool_approval_handler=None,  # type: ignore[arg-type]
            commit_approval_handler=approve,
        )

    assert_no_worktree_mutation(source, target, "agent/invalid-handler")


def test_isolated_session_failure_preserves_created_worktree(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Preserve the approved worktree when isolated session construction fails."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"

    def fail_session(*_args, **_kwargs):
        raise ConfigurationError("provider construction failed.")

    monkeypatch.setattr(
        "agent_workbench.isolated_coding.create_isolated_agent_session",
        fail_session,
    )

    with pytest.raises(
        CompletionError,
        match="Isolated session construction failed",
    ):
        run_isolated_autonomous_workflow(
            SessionId("session-failure"),
            configuration(source),
            "agent/session-failure",
            target,
            "Correct the add implementation.",
            "fix: valid",
            worktree_approval_handler=approve,
            tool_approval_handler=approve,
            commit_approval_handler=approve,
        )

    assert target.exists()
    assert run_git(target, "status", "--short").stdout == ""
    assert run_git(source, "branch", "--list", "agent/session-failure").stdout.strip()
    assert run_git(source, "status", "--short").stdout == ""


def create_repository_without_local_identity(root: Path) -> Path:
    """Create a repository with no local user.name or user.email configured."""

    root.mkdir()
    run_git(root, "init", "-b", "main")
    run_git(
        root,
        "-c",
        "user.name=Temp Identity",
        "-c",
        "user.email=temp@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "initial",
    )
    return root


def test_preflight_fails_before_worktree_when_both_identity_fields_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Fail before worktree creation when both local user.name and user.email are absent."""

    source = create_repository_without_local_identity(tmp_path / "source")
    target = tmp_path / "isolated"

    provider_called = []
    monkeypatch.setattr(
        "agent_workbench.isolated_coding.create_isolated_agent_session",
        lambda *_a, **_kw: provider_called.append(True),
    )

    with pytest.raises(ConfigurationError, match="user.name") as raised:
        run_isolated_autonomous_workflow(
            SessionId("preflight-both-missing"),
            configuration(source),
            "agent/preflight",
            target,
            "Fix it.",
            "fix: preflight",
            worktree_approval_handler=approve,
            tool_approval_handler=approve,
            commit_approval_handler=approve,
        )

    assert "user.email" in str(raised.value)
    assert not provider_called
    assert_no_worktree_mutation(source, target, "agent/preflight")


def test_preflight_fails_before_worktree_when_local_name_missing(
    tmp_path: Path,
) -> None:
    """Report missing user.name alone before any worktree or branch is created."""

    source = create_repository_without_local_identity(tmp_path / "source")
    run_git(source, "config", "user.email", "only-email@example.invalid")
    target = tmp_path / "isolated"

    with pytest.raises(ConfigurationError, match="user.name") as raised:
        run_isolated_autonomous_workflow(
            SessionId("preflight-name-missing"),
            configuration(source),
            "agent/preflight-name",
            target,
            "Fix it.",
            "fix: preflight",
            worktree_approval_handler=approve,
            tool_approval_handler=approve,
            commit_approval_handler=approve,
        )

    assert "user.email" not in str(raised.value)
    assert_no_worktree_mutation(source, target, "agent/preflight-name")


def test_preflight_fails_before_worktree_when_local_email_missing(
    tmp_path: Path,
) -> None:
    """Report missing user.email alone before any worktree or branch is created."""

    source = create_repository_without_local_identity(tmp_path / "source")
    run_git(source, "config", "user.name", "Only Name")
    target = tmp_path / "isolated"

    with pytest.raises(ConfigurationError, match="user.email") as raised:
        run_isolated_autonomous_workflow(
            SessionId("preflight-email-missing"),
            configuration(source),
            "agent/preflight-email",
            target,
            "Fix it.",
            "fix: preflight",
            worktree_approval_handler=approve,
            tool_approval_handler=approve,
            commit_approval_handler=approve,
        )

    assert "user.name" not in str(raised.value)
    assert_no_worktree_mutation(source, target, "agent/preflight-email")


def test_preflight_rejects_global_only_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Global identity alone must not satisfy the repository-local requirement.

    A temporary global gitconfig is created with placeholder identity values
    and exposed via GIT_CONFIG_GLOBAL so git would normally see it. The
    preflight must still fail because _run_git always overrides
    GIT_CONFIG_GLOBAL with os.devnull, excluding global config entirely.
    """

    source = create_repository_without_local_identity(tmp_path / "source")
    target = tmp_path / "isolated"

    global_config = tmp_path / "global_gitconfig"
    global_config.write_text(
        "[user]\n    name = Global Only User\n    email = global@example.invalid\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))

    with pytest.raises(ConfigurationError, match="repository-local") as raised:
        run_isolated_autonomous_workflow(
            SessionId("preflight-global-only"),
            configuration(source),
            "agent/preflight-global",
            target,
            "Fix it.",
            "fix: preflight",
            worktree_approval_handler=approve,
            tool_approval_handler=approve,
            commit_approval_handler=approve,
        )

    assert "user.name" in str(raised.value) or "user.email" in str(raised.value)
    assert_no_worktree_mutation(source, target, "agent/preflight-global")


def test_preflight_message_contains_actionable_git_config_guidance(
    tmp_path: Path,
) -> None:
    """Include shell-quoted git config --local commands for each missing field."""

    source_with_spaces = tmp_path / "my source repo"
    source = create_repository_without_local_identity(source_with_spaces)
    target = tmp_path / "isolated"

    with pytest.raises(ConfigurationError) as raised:
        run_isolated_autonomous_workflow(
            SessionId("preflight-guidance"),
            configuration(source),
            "agent/preflight-guidance",
            target,
            "Fix it.",
            "fix: preflight",
            worktree_approval_handler=approve,
            tool_approval_handler=approve,
            commit_approval_handler=approve,
        )

    message = str(raised.value)
    assert "config --local" in message
    assert 'user.name "Your Name"' in message
    assert 'user.email "you@example.com"' in message
    assert "my source repo" in message
    # Path containing spaces must be shell-quoted as a single argument.
    import shlex

    assert shlex.quote(str(source)) in message


def test_preflight_propagates_git_operational_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A non-not-found Git failure propagates and is not rewritten as missing or blank.

    Patches _run_git so that the user.name query returns exit code 128
    (a fatal Git error), simulating a corrupt config or permission failure.
    Verifies the error propagates unchanged without aggregating any field.
    """

    source = create_repository_without_local_identity(tmp_path / "source")

    import agent_workbench.worktree_commits as wc

    original_run_git = wc._run_git

    def patched_run_git(repository, arguments, **kwargs):
        if "user.name" in arguments:
            return wc._GitOutput(
                returncode=128, stdout=b"", stderr=b"fatal: bad config"
            )
        return original_run_git(repository, arguments, **kwargs)

    monkeypatch.setattr(wc, "_run_git", patched_run_git)

    with pytest.raises(ConfigurationError) as raised:
        wc.require_local_author_identity(source)

    assert "missing or blank" not in str(raised.value)
    assert "unexpected error" in str(raised.value)


def test_preflight_succeeds_when_both_local_fields_are_configured(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Allow the workflow to proceed when local user.name and user.email are set."""

    source = create_repository(tmp_path / "source")
    target = tmp_path / "isolated"
    captured = {}

    def create_isolated(_session_id, _runtime, worktree, *, max_tool_rounds):
        captured["worktree"] = worktree
        return SimpleNamespace(worktree=worktree, session=object())

    def run_coding(_session, _prompt, **_kwargs):
        worktree = captured["worktree"]
        (worktree.worktree_path / "module.py").write_text(
            "def add(left: int, right: int) -> int:\n    return left + right\n",
            encoding="utf-8",
        )
        return coding_result()

    monkeypatch.setattr(
        "agent_workbench.isolated_coding.create_isolated_agent_session",
        create_isolated,
    )
    monkeypatch.setattr(
        "agent_workbench.isolated_coding.run_autonomous_coding_task",
        run_coding,
    )

    result = run_isolated_autonomous_workflow(
        SessionId("preflight-success"),
        configuration(source),
        "agent/preflight-success",
        target,
        "Fix it.",
        "fix: preflight success",
        worktree_approval_handler=approve,
        tool_approval_handler=approve,
        commit_approval_handler=approve,
    )

    assert isinstance(result, IsolatedAutonomousWorkflowResult)
    assert result.commit_result.branch_name == "agent/preflight-success"
