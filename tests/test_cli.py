"""Tests for the Agent Workbench command-line interface."""

from agent_workbench.cli import run_cli
from agent_workbench.errors import CompletionError
from agent_workbench.messages import ChatRequest, Message
from agent_workbench.agents import get_agent_profile


class FakeProvider:
    """Provide deterministic responses for CLI tests."""

    name = "Fake"
    model_name = "fake-model"

    def __init__(
        self,
        outcomes: list[str | CompletionError] | None = None,
    ) -> None:
        self._outcomes = iter(outcomes or [])
        self.calls: list[list[Message]] = []
        self.system_prompts: list[str | None] = []

    def complete(self, request: ChatRequest) -> str:
        """Return the next configured response or error."""

        self.calls.append([message.copy() for message in request.messages])
        self.system_prompts.append(request.system_prompt)

        outcome = next(self._outcomes)

        if isinstance(outcome, CompletionError):
            raise outcome

        return outcome


def test_exit_command_does_not_call_provider(monkeypatch, capsys) -> None:
    """Exit immediately without contacting the provider."""

    user_inputs = iter(["/exit"])
    provider = FakeProvider()

    monkeypatch.setattr("builtins.input", lambda _: next(user_inputs))

    run_cli(provider)

    captured = capsys.readouterr()

    assert provider.calls == []
    assert "Session ended." in captured.out


def test_empty_input_is_ignored(monkeypatch) -> None:
    """Ignore empty input without contacting the provider."""

    user_inputs = iter(["", "/quit"])
    provider = FakeProvider()

    monkeypatch.setattr("builtins.input", lambda _: next(user_inputs))

    run_cli(provider)

    assert provider.calls == []


def test_conversation_history_is_preserved(monkeypatch, capsys) -> None:
    """Send previous user and assistant messages with each new request."""

    user_inputs = iter(
        [
            "Remember the code word cobalt.",
            "What is the code word?",
            "/exit",
        ]
    )
    provider = FakeProvider(["acknowledged", "cobalt"])

    monkeypatch.setattr("builtins.input", lambda _: next(user_inputs))

    run_cli(provider)

    captured = capsys.readouterr()

    assert provider.calls == [
        [
            {
                "role": "user",
                "content": "Remember the code word cobalt.",
            }
        ],
        [
            {
                "role": "user",
                "content": "Remember the code word cobalt.",
            },
            {
                "role": "assistant",
                "content": "acknowledged",
            },
            {
                "role": "user",
                "content": "What is the code word?",
            },
        ],
    ]
    assert "Assistant: acknowledged" in captured.out
    assert "Assistant: cobalt" in captured.out


def test_cli_recovers_after_provider_error(monkeypatch, capsys) -> None:
    """Continue the session without preserving a failed request."""

    user_inputs = iter(
        [
            "First request",
            "Second request",
            "/exit",
        ]
    )
    provider = FakeProvider(
        [
            CompletionError("Temporary provider failure."),
            "recovered",
        ]
    )

    monkeypatch.setattr("builtins.input", lambda _: next(user_inputs))

    run_cli(provider)

    captured = capsys.readouterr()

    assert provider.calls == [
        [
            {
                "role": "user",
                "content": "First request",
            }
        ],
        [
            {
                "role": "user",
                "content": "Second request",
            }
        ],
    ]
    assert "Error: Temporary provider failure." in captured.out
    assert "Assistant: recovered" in captured.out


def test_system_prompt_is_forwarded_without_entering_history(
    monkeypatch,
) -> None:
    """Forward the system prompt separately from conversation history."""

    user_inputs = iter(
        [
            "First request",
            "Second request",
            "/exit",
        ]
    )
    provider = FakeProvider(
        [
            "first response",
            "second response",
        ]
    )

    monkeypatch.setattr(
        "builtins.input",
        lambda _: next(user_inputs),
    )

    run_cli(
        provider,
        system_prompt="You are a strict software reviewer.",
    )

    assert provider.system_prompts == [
        "You are a strict software reviewer.",
        "You are a strict software reviewer.",
    ]

    assert all(
        message["role"] != "system"
        for request_messages in provider.calls
        for message in request_messages
    )


def test_agent_profile_is_displayed(
    monkeypatch,
    capsys,
) -> None:
    """Display the active agent identity when the session starts."""

    user_inputs = iter(["/exit"])
    provider = FakeProvider()
    agent_profile = get_agent_profile("reviewer")

    monkeypatch.setattr(
        "builtins.input",
        lambda _: next(user_inputs),
    )

    run_cli(
        provider,
        system_prompt=agent_profile.system_prompt,
        agent_profile=agent_profile,
    )

    captured = capsys.readouterr()

    assert "Agent: Reviewer" in captured.out
    assert agent_profile.description in captured.out
