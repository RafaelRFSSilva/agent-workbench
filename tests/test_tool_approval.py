"""Tests for provider-independent approval of effectful tool invocations."""

from dataclasses import FrozenInstanceError

import pytest

from agent_workbench.errors import CompletionError
from agent_workbench.messages import ChatRequest, ChatResponse, ToolInteractionRound
from agent_workbench.session import AgentSession, SessionId, SessionStatus
from agent_workbench.tool_calling import run_tool_calling_loop
from agent_workbench.tool_registry import ToolRegistry
from agent_workbench.tools import (
    ToolApprovalDecision,
    ToolApprovalRequest,
    ToolDefinition,
    ToolInvocation,
)


class FakeProvider:
    """Return configured responses and retain provider requests."""

    name = "Fake"
    model_name = "fake-model"

    def __init__(self, responses: list[ChatResponse]) -> None:
        self._responses = iter(responses)
        self.requests: list[ChatRequest] = []

    def complete(self, request: ChatRequest) -> ChatResponse:
        """Return the next configured response."""

        self.requests.append(request)
        return next(self._responses)


def definition(name: str = "write_file") -> ToolDefinition:
    """Create one portable test definition."""

    return ToolDefinition(
        name=name,
        description=f"Run {name}.",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    )


def invocation(
    identifier: str = "call-1",
    name: str = "write_file",
    path: str = "file.py",
) -> ToolInvocation:
    """Create one portable test invocation."""

    return ToolInvocation(
        id=identifier,
        tool_name=name,
        arguments={"path": path},
    )


def tool_response(*invocations: ToolInvocation) -> ChatResponse:
    """Create a response containing ordered tool invocations."""

    return ChatResponse(text="Working.", tool_invocations=invocations)


def approval_registry(
    *,
    executions: list[dict[str, object]] | None = None,
    previews: list[dict[str, object]] | None = None,
) -> ToolRegistry:
    """Create one registry containing an approval-required tool."""

    registry = ToolRegistry()

    def preview(arguments):
        if previews is not None:
            previews.append(arguments)
        return {"path": arguments["path"], "operation": "update"}

    def execute(arguments):
        if executions is not None:
            executions.append(arguments)
        return {"path": arguments["path"]}

    registry.register(
        definition(),
        execute,
        requires_approval=True,
        approval_preview=preview,
    )
    return registry


def test_approval_models_are_explicit_immutable_snapshots() -> None:
    """Preserve exact invocation data and isolate mutable preview input."""

    source_preview = {"changes": [{"path": "file.py"}]}
    request = ToolApprovalRequest(invocation("native-call"), source_preview)
    source_preview["changes"][0]["path"] = "changed.py"

    assert request.invocation.id == "native-call"
    assert request.invocation.tool_name == "write_file"
    assert request.invocation.arguments == {"path": "file.py"}
    assert request.preview == {"changes": [{"path": "file.py"}]}
    assert ToolApprovalDecision("approve") is ToolApprovalDecision.APPROVE
    assert ToolApprovalDecision("deny") is ToolApprovalDecision.DENY
    with pytest.raises(ValueError):
        ToolApprovalDecision("yes")
    with pytest.raises(FrozenInstanceError):
        request.invocation = invocation("changed")  # type: ignore[misc]

    changed_preview = request.preview
    assert isinstance(changed_preview, dict)
    changed_preview["changes"] = []
    assert request.preview == {"changes": [{"path": "file.py"}]}


def test_registry_preserves_read_only_behavior_and_approval_metadata() -> None:
    """Keep registration order while isolating metadata between registries."""

    read_only = definition("read_file")
    action = definition()
    first = ToolRegistry()
    second = ToolRegistry()
    first.register(read_only, lambda arguments: arguments)
    first.register(
        action,
        lambda arguments: arguments,
        requires_approval=True,
        approval_preview=lambda arguments: {"path": arguments["path"]},
    )
    second.register(action, lambda arguments: arguments)

    assert first.definitions == (read_only, action)
    assert first.requires_approval(invocation(name="read_file")) is False
    assert first.requires_approval(invocation()) is True
    assert second.requires_approval(invocation()) is False


def test_registry_creates_copy_safe_preview_before_execution() -> None:
    """Generate a deterministic request without exposing invocation state."""

    preview_arguments = []
    registry = approval_registry(previews=preview_arguments)
    requested = invocation()

    approval = registry.create_approval_request(requested)
    preview_arguments[0]["path"] = "mutated.py"

    assert approval.invocation is requested
    assert approval.preview == {"operation": "update", "path": "file.py"}
    assert requested.arguments == {"path": "file.py"}


def test_registry_preview_failure_propagates_without_execution() -> None:
    """Reject invalid actions before an approval handler can run."""

    executions = []
    registry = ToolRegistry()
    registry.register(
        definition(),
        lambda arguments: executions.append(arguments),
        requires_approval=True,
        approval_preview=lambda arguments: (_ for _ in ()).throw(
            CompletionError("Invalid action.")
        ),
    )

    with pytest.raises(CompletionError, match="Invalid action"):
        registry.create_approval_request(invocation())

    assert executions == []


def test_read_only_tool_executes_without_approval_handler() -> None:
    """Preserve the existing tool-loop behavior for read-only tools."""

    executions = []
    registry = ToolRegistry()
    registry.register(
        definition("read_file"),
        lambda arguments: executions.append(arguments) or {"content": "ok"},
    )
    provider = FakeProvider(
        [
            tool_response(invocation(name="read_file")),
            ChatResponse(text="Done."),
        ]
    )

    run_tool_calling_loop(
        provider,
        ChatRequest(messages=[]),
        registry,
        max_tool_rounds=1,
    )

    assert executions == [{"path": "file.py"}]


def test_approved_effectful_tool_executes_once_and_enters_history() -> None:
    """Execute only after preview and approval, then record a normal result."""

    events = []
    registry = approval_registry(executions=events, previews=events)
    requested = tool_response(invocation())
    observed: list[ToolInteractionRound] = []
    provider = FakeProvider([requested, ChatResponse(text="Done.")])

    result = run_tool_calling_loop(
        provider,
        ChatRequest(messages=[]),
        registry,
        max_tool_rounds=1,
        tool_round_observer=observed.append,
        tool_approval_handler=lambda request: (
            events.append({"approved": request.invocation.id})
            or ToolApprovalDecision.APPROVE
        ),
    )

    assert result.text == "Done."
    assert events == [
        {"path": "file.py"},
        {"approved": "call-1"},
        {"path": "file.py"},
    ]
    assert observed == list(provider.requests[1].tool_interactions)
    assert observed[0].results[0].status == "success"
    assert observed[0].results[0].output == {"path": "file.py"}
    assert not hasattr(provider.requests[1], "tool_approvals")


@pytest.mark.parametrize(
    ("handler", "message"),
    [
        (None, "approval is required"),
        (lambda request: ToolApprovalDecision.DENY, "approval was denied"),
        (
            lambda request: (_ for _ in ()).throw(RuntimeError("private")),
            "approval handler failed",
        ),
        (lambda request: "approve", "approval decision is invalid"),
    ],
)
def test_unapproved_effectful_tool_never_executes(handler, message) -> None:
    """Reject absent, denied, failed, and malformed approval decisions."""

    executions = []
    registry = approval_registry(executions=executions)
    provider = FakeProvider([tool_response(invocation())])

    with pytest.raises(CompletionError, match=message):
        run_tool_calling_loop(
            provider,
            ChatRequest(messages=[]),
            registry,
            max_tool_rounds=1,
            tool_approval_handler=handler,
        )

    assert executions == []


def test_approval_is_specific_to_each_exact_invocation() -> None:
    """Request a fresh approval for every action round and invocation id."""

    approvals = []
    executions = []
    registry = approval_registry(executions=executions)
    provider = FakeProvider(
        [
            tool_response(invocation("call-1", path="first.py")),
            tool_response(invocation("call-2", path="second.py")),
            ChatResponse(text="Done."),
        ]
    )

    run_tool_calling_loop(
        provider,
        ChatRequest(messages=[]),
        registry,
        max_tool_rounds=2,
        tool_approval_handler=lambda request: (
            approvals.append(
                (
                    request.invocation.id,
                    request.invocation.arguments["path"],
                )
            )
            or ToolApprovalDecision.APPROVE
        ),
    )

    assert approvals == [("call-1", "first.py"), ("call-2", "second.py")]
    assert executions == [{"path": "first.py"}, {"path": "second.py"}]


@pytest.mark.parametrize(
    "requested",
    [
        (
            invocation("action", path="first.py"),
            invocation("read", name="read_file", path="second.py"),
        ),
        (
            invocation("action-1", path="first.py"),
            invocation("action-2", path="second.py"),
        ),
    ],
)
def test_action_mixed_with_any_other_invocation_rejects_entire_round(
    requested,
) -> None:
    """Prevent partial execution when an action shares a provider round."""

    executions = []
    registry = approval_registry(executions=executions)
    registry.register(
        definition("read_file"),
        lambda arguments: executions.append(arguments),
    )
    provider = FakeProvider([tool_response(*requested)])

    with pytest.raises(CompletionError, match="one at a time"):
        run_tool_calling_loop(
            provider,
            ChatRequest(messages=[]),
            registry,
            max_tool_rounds=1,
            tool_approval_handler=lambda request: ToolApprovalDecision.APPROVE,
        )

    assert executions == []


def test_session_forwards_approval_and_rolls_back_denial_then_retries() -> None:
    """Keep failed turns out of history and allow a newly approved retry."""

    executions = []
    registry = approval_registry(executions=executions)
    provider = FakeProvider(
        [
            ChatResponse(text="Previous."),
            tool_response(invocation("denied")),
            tool_response(invocation("approved")),
            ChatResponse(text="Changed."),
        ]
    )
    session = AgentSession(
        id=SessionId("session"),
        provider=provider,
        tool_registry=registry,
    )
    session.send("First.")

    with pytest.raises(CompletionError, match="denied"):
        session.send(
            "Denied.",
            tool_approval_handler=lambda request: ToolApprovalDecision.DENY,
        )

    assert executions == []
    assert session.status is SessionStatus.FAILED
    assert session.messages == (
        {"role": "user", "content": "First."},
        {"role": "assistant", "content": "Previous."},
    )

    session.send(
        "Retry.",
        tool_approval_handler=lambda request: ToolApprovalDecision.APPROVE,
    )

    assert executions == [{"path": "file.py"}]
    assert session.status is SessionStatus.READY
    assert session.messages[-2:] == (
        {"role": "user", "content": "Retry."},
        {"role": "assistant", "content": "Changed."},
    )
    assert all("approval" not in message for message in session.messages)
