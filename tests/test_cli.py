"""Tests for the Agent Workbench command-line interface."""

from agent_workbench.cli import Message, run_cli


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
