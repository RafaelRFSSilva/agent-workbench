"""Tests for the Agent Workbench command-line interface."""

import pytest
from ollama import ResponseError

from agent_workbench.cli import Message, request_completion, run_cli
from agent_workbench.errors import CompletionError


def test_exit_command_does_not_call_completion(monkeypatch, capsys) -> None:
    """Exit immediately without contacting the model."""

    user_inputs = iter(["/exit"])
    completion_calls: list[list[Message]] = []

    monkeypatch.setattr("builtins.input", lambda _: next(user_inputs))

    def fake_completion(messages: list[Message]) -> str:
        completion_calls.append(messages)
        return "unused"

    run_cli(fake_completion)

    captured = capsys.readouterr()

    assert completion_calls == []
    assert "Session ended." in captured.out


def test_empty_input_is_ignored(monkeypatch) -> None:
    """Ignore empty input without contacting the model."""

    user_inputs = iter(["", "/quit"])
    completion_calls: list[list[Message]] = []

    monkeypatch.setattr("builtins.input", lambda _: next(user_inputs))

    def fake_completion(messages: list[Message]) -> str:
        completion_calls.append(messages)
        return "unused"

    run_cli(fake_completion)

    assert completion_calls == []


def test_conversation_history_is_preserved(monkeypatch, capsys) -> None:
    """Send previous user and assistant messages with each new request."""

    user_inputs = iter(
        [
            "Remember the code word cobalt.",
            "What is the code word?",
            "/exit",
        ]
    )
    responses = iter(["acknowledged", "cobalt"])
    captured_histories: list[list[Message]] = []

    monkeypatch.setattr("builtins.input", lambda _: next(user_inputs))

    def fake_completion(messages: list[Message]) -> str:
        captured_histories.append([message.copy() for message in messages])
        return next(responses)

    run_cli(fake_completion)

    captured = capsys.readouterr()

    assert captured_histories == [
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


def test_connection_error_is_translated(monkeypatch) -> None:
    """Translate an Ollama connection failure into an application error."""

    def fake_chat(**kwargs) -> None:
        raise ConnectionError

    monkeypatch.setattr("agent_workbench.cli.chat", fake_chat)

    with pytest.raises(
        CompletionError,
        match="Unable to connect to Ollama",
    ):
        request_completion([], model_name="test-model")


def test_missing_model_error_is_translated(monkeypatch) -> None:
    """Provide a clear error when the configured model is unavailable."""

    def fake_chat(**kwargs) -> None:
        raise ResponseError("model not found", 404)

    monkeypatch.setattr("agent_workbench.cli.chat", fake_chat)

    with pytest.raises(
        CompletionError,
        match="Model 'missing-model' is not available",
    ):
        request_completion([], model_name="missing-model")


def test_cli_recovers_after_completion_error(monkeypatch, capsys) -> None:
    """Continue the session without preserving a failed request."""

    user_inputs = iter(
        [
            "First request",
            "Second request",
            "/exit",
        ]
    )
    captured_histories: list[list[Message]] = []

    monkeypatch.setattr("builtins.input", lambda _: next(user_inputs))

    def fake_completion(messages: list[Message]) -> str:
        captured_histories.append([message.copy() for message in messages])

        if len(captured_histories) == 1:
            raise CompletionError("Temporary model failure.")

        return "recovered"

    run_cli(fake_completion)

    captured = capsys.readouterr()

    assert captured_histories == [
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
    assert "Error: Temporary model failure." in captured.out
    assert "Assistant: recovered" in captured.out
