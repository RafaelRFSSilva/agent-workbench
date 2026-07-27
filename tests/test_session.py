"""Tests for provider-independent AgentSession state and completion."""

from collections.abc import Callable
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from agent_workbench.agents import AgentProfile
from agent_workbench.context import ContextDocument
from agent_workbench.errors import (
    CompletionError,
    ConfigurationError,
    SessionStateError,
)
from agent_workbench.generation import GenerationConfig
from agent_workbench.messages import (
    ChatRequest,
    ChatResponse,
    ToolInteractionRound,
)
from agent_workbench.session import AgentSession, SessionId, SessionStatus
from agent_workbench.structured_outputs import JSONResponseFormat
from agent_workbench.tool_registry import ToolRegistry
from agent_workbench.tools import ToolDefinition, ToolInvocation

type ProviderOutcome = ChatResponse | Exception | Callable[[ChatRequest], ChatResponse]


class FakeProvider:
    """Return deterministic outcomes and retain provider-independent requests."""

    name = "Fake"
    model_name = "fake-model"

    def __init__(self, outcomes: list[ProviderOutcome]) -> None:
        self._outcomes = iter(outcomes)
        self.requests: list[ChatRequest] = []

    def complete(self, request: ChatRequest) -> ChatResponse:
        """Return or raise the next configured outcome."""

        self.requests.append(request)
        outcome = next(self._outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        if callable(outcome):
            return outcome(request)
        return outcome


def calculator_definition() -> ToolDefinition:
    """Create one portable calculator definition."""

    return ToolDefinition(
        name="calculator",
        description="Evaluate an arithmetic expression.",
        input_schema={
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
            "additionalProperties": False,
        },
    )


def tool_response(invocation_id: str = "call-1") -> ChatResponse:
    """Create one calculator invocation response."""

    return ChatResponse(
        text="Calculating.",
        tool_invocations=(
            ToolInvocation(
                id=invocation_id,
                tool_name="calculator",
                arguments={"expression": "2 + 2"},
            ),
        ),
    )


def test_session_id_preserves_valid_value_and_supports_hashing() -> None:
    """Preserve identifiers exactly with value equality and hashability."""

    identifier = SessionId(" session-1 ")

    assert identifier.value == " session-1 "
    assert identifier == SessionId(" session-1 ")
    assert len({identifier, SessionId(" session-1 ")}) == 1


@pytest.mark.parametrize("value", ["", " ", "\t\n"])
def test_session_id_rejects_blank_values(value: str) -> None:
    """Reject identifiers without a non-whitespace character."""

    with pytest.raises(ConfigurationError, match="non-blank string"):
        SessionId(value)


def test_session_id_rejects_non_string_values() -> None:
    """Reject non-string identifier values."""

    with pytest.raises(ConfigurationError, match="non-blank string"):
        SessionId(42)  # type: ignore[arg-type]


def test_session_id_is_immutable() -> None:
    """Prevent replacement of a validated identifier value."""

    identifier = SessionId("session-1")

    with pytest.raises(FrozenInstanceError):
        identifier.value = "changed"  # type: ignore[misc]


def test_session_exposes_initial_read_only_configuration() -> None:
    """Expose provider-derived identity and immutable configured values."""

    provider = FakeProvider([])
    profile = AgentProfile(
        name="Reviewer",
        description="Review code.",
        system_prompt="Profile prompt.",
    )
    documents = [
        ContextDocument(source=Path("README.md"), content="Context."),
    ]
    generation = GenerationConfig(temperature=0.2)
    response_format = JSONResponseFormat(
        name="result",
        schema={"type": "object", "additionalProperties": False},
    )
    registry = ToolRegistry()

    session = AgentSession(
        id=SessionId("session-1"),
        provider=provider,
        agent_profile=profile,
        system_prompt="Explicit configured prompt.",
        context_documents=documents,
        generation_config=generation,
        response_format=response_format,
        tool_registry=registry,
        max_tool_rounds=3,
    )
    documents.append(
        ContextDocument(source=Path("other.md"), content="Other."),
    )

    assert session.id == SessionId("session-1")
    assert session.status is SessionStatus.READY
    assert session.provider is provider
    assert session.provider_name == "Fake"
    assert session.model_name == "fake-model"
    assert session.agent_profile is profile
    assert session.system_prompt == "Explicit configured prompt."
    assert session.context_documents == (
        ContextDocument(source=Path("README.md"), content="Context."),
    )
    assert session.generation_config is generation
    assert session.response_format is response_format
    assert session.tool_registry is registry
    assert session.max_tool_rounds == 3
    assert session.messages == ()
    assert "Context." not in repr(session)
    assert "Explicit configured prompt." not in repr(session)
    assert "FakeProvider" not in repr(session)


def test_session_requires_a_validated_session_identifier() -> None:
    """Reject callers that bypass the SessionId boundary."""

    with pytest.raises(ConfigurationError, match="SessionId"):
        AgentSession(
            id="session-1",  # type: ignore[arg-type]
            provider=FakeProvider([]),
        )


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_session_rejects_invalid_maximum_tool_rounds(value: object) -> None:
    """Require a positive integer maximum tool-round count."""

    with pytest.raises(ConfigurationError, match="positive integer"):
        AgentSession(
            id=SessionId("session-1"),
            provider=FakeProvider([]),
            max_tool_rounds=value,  # type: ignore[arg-type]
        )


def test_direct_send_forwards_exact_configuration_and_commits_history() -> None:
    """Complete directly and commit the user and final assistant once."""

    response = ChatResponse(text="Completed.")
    provider = FakeProvider([response])
    profile = AgentProfile("Reviewer", "Review code.", "Profile prompt.")
    context = (ContextDocument(source=Path("README.md"), content="Context."),)
    generation = GenerationConfig(temperature=0.1, max_output_tokens=128)
    response_format = JSONResponseFormat(
        name="result",
        schema={"type": "object", "additionalProperties": False},
    )
    session = AgentSession(
        id=SessionId("session-1"),
        provider=provider,
        agent_profile=profile,
        system_prompt="Configured prompt.",
        context_documents=context,
        generation_config=generation,
        response_format=response_format,
    )

    result = session.send("Review this.")

    assert result is response
    assert provider.requests == [
        ChatRequest(
            messages=[{"role": "user", "content": "Review this."}],
            system_prompt="Configured prompt.",
            context_documents=context,
            generation_config=generation,
            response_format=response_format,
        )
    ]
    assert session.messages == (
        {"role": "user", "content": "Review this."},
        {"role": "assistant", "content": "Completed."},
    )
    assert session.status is SessionStatus.READY


def test_profile_does_not_implicitly_replace_current_system_prompt_semantics() -> None:
    """Keep profile metadata separate when no configured prompt is supplied."""

    provider = FakeProvider([ChatResponse(text="Done.")])
    session = AgentSession(
        id=SessionId("session-1"),
        provider=provider,
        agent_profile=AgentProfile("Reviewer", "Review.", "Profile prompt."),
    )

    session.send("Continue.")

    assert provider.requests[0].system_prompt is None


def test_second_send_includes_only_prior_successful_messages() -> None:
    """Reuse successful conversation state in exact order."""

    provider = FakeProvider(
        [
            ChatResponse(text="First answer."),
            ChatResponse(text="Second answer."),
        ]
    )
    session = AgentSession(id=SessionId("session-1"), provider=provider)

    session.send("First question.")
    session.send("Second question.")

    assert provider.requests[1].messages == [
        {"role": "user", "content": "First question."},
        {"role": "assistant", "content": "First answer."},
        {"role": "user", "content": "Second question."},
    ]
    assert session.messages == (
        {"role": "user", "content": "First question."},
        {"role": "assistant", "content": "First answer."},
        {"role": "user", "content": "Second question."},
        {"role": "assistant", "content": "Second answer."},
    )


def test_message_snapshots_cannot_mutate_internal_history() -> None:
    """Return independent message mappings rather than the owned list."""

    session = AgentSession(
        id=SessionId("session-1"),
        provider=FakeProvider([ChatResponse(text="Answer.")]),
    )
    session.send("Question.")

    snapshot = session.messages
    snapshot[0]["content"] = "Changed."

    assert session.messages[0]["content"] == "Question."


def test_tool_send_executes_round_forwards_observer_and_commits_final_text() -> None:
    """Use the shared loop while keeping internal rounds out of history."""

    definition = calculator_definition()
    registry = ToolRegistry()
    registry.register(definition, lambda arguments: {"result": 4})
    requested = tool_response()
    final = ChatResponse(text="The answer is 4.")
    provider = FakeProvider([requested, final])
    observed: list[ToolInteractionRound] = []
    session = AgentSession(
        id=SessionId("session-1"),
        provider=provider,
        tool_registry=registry,
        max_tool_rounds=2,
    )

    result = session.send("Calculate.", tool_round_observer=observed.append)

    assert result is final
    assert provider.requests[0].tools == (definition,)
    assert provider.requests[1].tool_interactions == tuple(observed)
    assert observed[0].response is requested
    assert observed[0].results[0].output == {"result": 4}
    assert session.messages == (
        {"role": "user", "content": "Calculate."},
        {"role": "assistant", "content": "The answer is 4."},
    )
    assert session.status is SessionStatus.READY
    assert registry.definitions == (definition,)


def test_handler_error_remains_a_tool_result_and_can_complete() -> None:
    """Preserve existing safe handler-error behavior inside the shared loop."""

    registry = ToolRegistry()
    registry.register(
        calculator_definition(),
        lambda arguments: (_ for _ in ()).throw(ValueError("unsafe detail")),
    )
    provider = FakeProvider([tool_response(), ChatResponse(text="Unavailable.")])
    session = AgentSession(
        id=SessionId("session-1"),
        provider=provider,
        tool_registry=registry,
    )

    session.send("Calculate.")

    result = provider.requests[1].tool_interactions[0].results[0]
    assert result.status == "error"
    assert result.error == "Tool execution failed."
    assert session.status is SessionStatus.READY


def test_direct_failure_rolls_back_and_retry_succeeds() -> None:
    """Preserve history on provider failure and allow a successful retry."""

    failure = CompletionError("Provider unavailable.")
    provider = FakeProvider([failure, ChatResponse(text="Recovered.")])
    session = AgentSession(id=SessionId("session-1"), provider=provider)

    with pytest.raises(CompletionError, match="Provider unavailable") as exc_info:
        session.send("Failed question.")

    assert exc_info.value is failure
    assert session.messages == ()
    assert session.status is SessionStatus.FAILED

    session.send("Retry question.")

    assert provider.requests[1].messages == [
        {"role": "user", "content": "Retry question."}
    ]
    assert session.messages == (
        {"role": "user", "content": "Retry question."},
        {"role": "assistant", "content": "Recovered."},
    )
    assert session.status is SessionStatus.READY


def test_provider_failure_after_tool_round_rolls_back() -> None:
    """Discard partial tool history when the following completion fails."""

    registry = ToolRegistry()
    registry.register(calculator_definition(), lambda arguments: {"result": 4})
    provider = FakeProvider([tool_response(), CompletionError("Follow-up failed.")])
    session = AgentSession(
        id=SessionId("session-1"),
        provider=provider,
        tool_registry=registry,
    )

    with pytest.raises(CompletionError, match="Follow-up failed"):
        session.send("Calculate.")

    assert provider.requests[1].tool_interactions
    assert session.messages == ()
    assert session.status is SessionStatus.FAILED


def test_maximum_tool_round_failure_rolls_back() -> None:
    """Preserve conversation when maximum-round protection fails."""

    registry = ToolRegistry()
    registry.register(calculator_definition(), lambda arguments: {"result": 4})
    provider = FakeProvider([tool_response("call-1"), tool_response("call-2")])
    session = AgentSession(
        id=SessionId("session-1"),
        provider=provider,
        tool_registry=registry,
        max_tool_rounds=1,
    )

    with pytest.raises(CompletionError, match="maximum number"):
        session.send("Calculate.")

    assert session.messages == ()
    assert session.status is SessionStatus.FAILED


def test_observer_failure_propagates_and_rolls_back() -> None:
    """Preserve the original observer error and discard the pending turn."""

    registry = ToolRegistry()
    registry.register(calculator_definition(), lambda arguments: {"result": 4})
    provider = FakeProvider([tool_response()])
    session = AgentSession(
        id=SessionId("session-1"),
        provider=provider,
        tool_registry=registry,
    )
    failure = RuntimeError("Observer failed.")

    def fail_observer(round_: ToolInteractionRound) -> None:
        raise failure

    with pytest.raises(RuntimeError, match="Observer failed") as exc_info:
        session.send("Calculate.", tool_round_observer=fail_observer)

    assert exc_info.value is failure
    assert session.messages == ()
    assert session.status is SessionStatus.FAILED


def test_nested_send_is_rejected_and_outer_send_rolls_back() -> None:
    """Reject obvious re-entrancy without corrupting conversation state."""

    session: AgentSession

    def nested_send(request: ChatRequest) -> ChatResponse:
        session.send("Nested.")
        return ChatResponse(text="Unreachable.")

    provider = FakeProvider([nested_send, ChatResponse(text="Recovered.")])
    session = AgentSession(id=SessionId("session-1"), provider=provider)

    with pytest.raises(SessionStateError, match="already completing"):
        session.send("Outer.")

    assert session.messages == ()
    assert session.status is SessionStatus.FAILED

    session.send("Retry.")

    assert session.messages == (
        {"role": "user", "content": "Retry."},
        {"role": "assistant", "content": "Recovered."},
    )
    assert session.status is SessionStatus.READY


@pytest.mark.parametrize("content", ["", " ", "\n\t", 42])
def test_send_rejects_invalid_content_without_changing_state(content: object) -> None:
    """Reject blank or non-string content before entering completion state."""

    session = AgentSession(id=SessionId("session-1"), provider=FakeProvider([]))

    with pytest.raises(ConfigurationError, match="non-blank string"):
        session.send(content)  # type: ignore[arg-type]

    assert session.messages == ()
    assert session.status is SessionStatus.READY
