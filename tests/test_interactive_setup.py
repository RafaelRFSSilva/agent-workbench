"""Tests for interactive runtime configuration setup."""

from agent_workbench.config import MODEL_ENV_VAR, PROVIDER_ENV_VAR
from agent_workbench.interactive_setup import run_interactive_setup
from agent_workbench.generation import GenerationConfig


def test_interactive_setup_accepts_environment_defaults(
    monkeypatch,
) -> None:
    """Accept the configured provider and model by pressing Enter."""

    monkeypatch.setenv(PROVIDER_ENV_VAR, "ollama")
    monkeypatch.setenv(MODEL_ENV_VAR, "gpt-oss:20b")

    user_inputs = iter(
        [
            "",  # Provider
            "",  # Model
            "",  # Agent
            "",  # Context files finished
            "",  # Temperature
            "",  # Top-p
            "",  # Maximum output tokens
        ]
    )
    output_lines: list[str] = []

    configuration = run_interactive_setup(
        input_fn=lambda _: next(user_inputs),
        output_fn=output_lines.append,
    )

    assert configuration.provider_name == "ollama"
    assert configuration.model_name == "gpt-oss:20b"
    assert configuration.agent_profile is None
    assert configuration.system_prompt is None
    assert configuration.context_documents == ()
    assert configuration.generation_config == GenerationConfig()


def test_interactive_setup_reprompts_for_invalid_provider(
    monkeypatch,
) -> None:
    """Continue prompting after an invalid provider selection."""

    monkeypatch.setenv(PROVIDER_ENV_VAR, "ollama")
    monkeypatch.setenv(MODEL_ENV_VAR, "gpt-oss:20b")

    user_inputs = iter(
        [
            "unsupported",
            "2",
            "",
            "",
            "",
            "",
            "",
            "",
        ]
    )
    output_lines: list[str] = []

    configuration = run_interactive_setup(
        input_fn=lambda _: next(user_inputs),
        output_fn=output_lines.append,
    )

    assert configuration.provider_name == "ollama"
    assert configuration.model_name == "gpt-oss:20b"
    assert "Invalid provider. Enter a provider name or its menu number." in output_lines


def test_interactive_setup_requires_model_for_new_cloud_provider(
    monkeypatch,
) -> None:
    """Require a model when the selected provider has no safe default."""

    monkeypatch.setenv(PROVIDER_ENV_VAR, "ollama")
    monkeypatch.setenv(MODEL_ENV_VAR, "gpt-oss:20b")

    user_inputs = iter(
        [
            "openai",
            "",
            "openai-test-model",
            "",
            "",
            "",
            "",
            "",
        ]
    )
    output_lines: list[str] = []

    configuration = run_interactive_setup(
        input_fn=lambda _: next(user_inputs),
        output_fn=output_lines.append,
    )

    assert configuration.provider_name == "openai"
    assert configuration.model_name == "openai-test-model"
    assert "Model name must not be blank." in output_lines


def test_interactive_setup_selects_agent_profile_by_number(
    monkeypatch,
) -> None:
    """Select a built-in agent profile through its menu number."""

    monkeypatch.setenv(PROVIDER_ENV_VAR, "ollama")
    monkeypatch.setenv(MODEL_ENV_VAR, "gpt-oss:20b")

    user_inputs = iter(
        [
            "",
            "",
            "3",
            "",
            "",
            "",
            "",
        ]
    )

    configuration = run_interactive_setup(
        input_fn=lambda _: next(user_inputs),
        output_fn=lambda _: None,
    )

    assert configuration.agent_profile is not None
    assert configuration.agent_profile.name == "Reviewer"
    assert configuration.system_prompt == (configuration.agent_profile.system_prompt)


def test_interactive_setup_reprompts_for_invalid_agent(
    monkeypatch,
) -> None:
    """Continue prompting after an invalid agent selection."""

    monkeypatch.setenv(PROVIDER_ENV_VAR, "ollama")
    monkeypatch.setenv(MODEL_ENV_VAR, "gpt-oss:20b")

    user_inputs = iter(
        [
            "",
            "",
            "unsupported",
            "tester",
            "",
            "",
            "",
            "",
        ]
    )
    output_lines: list[str] = []

    configuration = run_interactive_setup(
        input_fn=lambda _: next(user_inputs),
        output_fn=output_lines.append,
    )

    assert configuration.agent_profile is not None
    assert configuration.agent_profile.name == "Tester"
    assert (
        "Invalid agent. Enter an agent name, its menu number, or 0 for none."
        in output_lines
    )


def test_interactive_setup_loads_multiple_context_files(
    monkeypatch,
    tmp_path,
) -> None:
    """Load multiple context documents while preserving their order."""

    monkeypatch.setenv(PROVIDER_ENV_VAR, "ollama")
    monkeypatch.setenv(MODEL_ENV_VAR, "gpt-oss:20b")

    first_path = tmp_path / "README.md"
    second_path = tmp_path / "example.py"

    first_path.write_text(
        "Project documentation.",
        encoding="utf-8",
    )
    second_path.write_text(
        'print("Hello")',
        encoding="utf-8",
    )

    user_inputs = iter(
        [
            "",
            "",
            "",
            str(first_path),
            str(second_path),
            "",
            "",
            "",
            "",
        ]
    )

    configuration = run_interactive_setup(
        input_fn=lambda _: next(user_inputs),
        output_fn=lambda _: None,
    )

    assert [document.source for document in configuration.context_documents] == [
        first_path,
        second_path,
    ]
    assert [document.content for document in configuration.context_documents] == [
        "Project documentation.",
        'print("Hello")',
    ]


def test_interactive_setup_reprompts_for_invalid_context_file(
    monkeypatch,
    tmp_path,
) -> None:
    """Continue prompting after an invalid context file."""

    monkeypatch.setenv(PROVIDER_ENV_VAR, "ollama")
    monkeypatch.setenv(MODEL_ENV_VAR, "gpt-oss:20b")

    missing_path = tmp_path / "missing.md"
    valid_path = tmp_path / "valid.md"

    valid_path.write_text(
        "Valid context.",
        encoding="utf-8",
    )

    user_inputs = iter(
        [
            "",
            "",
            "",
            str(missing_path),
            str(valid_path),
            "",
            "",
            "",
            "",
        ]
    )
    output_lines: list[str] = []

    configuration = run_interactive_setup(
        input_fn=lambda _: next(user_inputs),
        output_fn=output_lines.append,
    )

    assert len(configuration.context_documents) == 1
    assert configuration.context_documents[0].source == valid_path
    assert configuration.context_documents[0].content == "Valid context."
    assert any(line.startswith("Invalid context file:") for line in output_lines)


def test_interactive_setup_collects_generation_settings(
    monkeypatch,
) -> None:
    """Collect provider-independent generation settings."""

    monkeypatch.setenv(PROVIDER_ENV_VAR, "ollama")
    monkeypatch.setenv(MODEL_ENV_VAR, "gpt-oss:20b")

    user_inputs = iter(
        [
            "",
            "",
            "",
            "",
            "0.2",
            "0.8",
            "256",
        ]
    )

    configuration = run_interactive_setup(
        input_fn=lambda _: next(user_inputs),
        output_fn=lambda _: None,
    )

    assert configuration.generation_config == GenerationConfig(
        temperature=0.2,
        top_p=0.8,
        max_output_tokens=256,
    )


def test_interactive_setup_reprompts_for_invalid_generation_settings(
    monkeypatch,
) -> None:
    """Continue prompting after invalid generation settings."""

    monkeypatch.setenv(PROVIDER_ENV_VAR, "ollama")
    monkeypatch.setenv(MODEL_ENV_VAR, "gpt-oss:20b")

    user_inputs = iter(
        [
            "",
            "",
            "",
            "",
            "invalid",
            "-0.1",
            "0.3",
            "1.1",
            "0.7",
            "0",
            "1.5",
            "128",
        ]
    )
    output_lines: list[str] = []

    configuration = run_interactive_setup(
        input_fn=lambda _: next(user_inputs),
        output_fn=output_lines.append,
    )

    assert configuration.generation_config == GenerationConfig(
        temperature=0.3,
        top_p=0.7,
        max_output_tokens=128,
    )
    assert output_lines.count("Temperature must be a number between 0.0 and 1.0.") == 2
    assert "Top-p must be a number between 0.0 and 1.0." in output_lines
    assert output_lines.count("Maximum output tokens must be a positive integer.") == 2
