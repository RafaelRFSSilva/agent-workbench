"""Tests for Agent Workbench configuration."""

import os
from pathlib import Path
import pytest

from agent_workbench.config import (
    DEFAULT_MODEL_NAME,
    DEFAULT_PROVIDER_NAME,
    MODEL_ENV_VAR,
    PROJECT_CONFIG_RELATIVE_PATH,
    PROJECT_INSTRUCTIONS_RELATIVE_PATH,
    MAX_PROJECT_INSTRUCTIONS_SIZE_BYTES,
    PROVIDER_ENV_VAR,
    ProjectCodingConfiguration,
    create_project_configuration,
    discover_project_configuration,
    get_model_name,
    get_provider_name,
    load_project_configuration,
    load_project_instructions,
    load_environment,
)
from agent_workbench.errors import ConfigurationError


def test_default_model_is_used_when_variable_is_missing(monkeypatch) -> None:
    """Use the default model when no environment variable is configured."""

    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)

    assert get_model_name() == DEFAULT_MODEL_NAME


def test_model_can_be_configured_through_environment(monkeypatch) -> None:
    """Read the model name from the environment."""

    monkeypatch.setenv(MODEL_ENV_VAR, "custom-local-model")

    assert get_model_name() == "custom-local-model"


def test_blank_model_uses_default(monkeypatch) -> None:
    """Ignore an environment variable containing only whitespace."""

    monkeypatch.setenv(MODEL_ENV_VAR, "   ")

    assert get_model_name() == DEFAULT_MODEL_NAME


def test_default_provider_is_used_when_variable_is_missing(monkeypatch) -> None:
    """Use Ollama when no provider is configured."""

    monkeypatch.delenv(PROVIDER_ENV_VAR, raising=False)

    assert get_provider_name() == DEFAULT_PROVIDER_NAME


def test_provider_can_be_configured_through_environment(monkeypatch) -> None:
    """Read and normalize the provider name from the environment."""

    monkeypatch.setenv(PROVIDER_ENV_VAR, " OpenAI ")

    assert get_provider_name() == "openai"


def test_unsupported_provider_is_rejected(monkeypatch) -> None:
    """Reject provider names that the application does not support."""

    monkeypatch.setenv(PROVIDER_ENV_VAR, "unsupported")

    with pytest.raises(
        ConfigurationError,
        match="Unsupported provider 'unsupported'",
    ):
        get_provider_name()


def test_openai_requires_an_explicit_model(monkeypatch) -> None:
    """Require explicit model selection for the OpenAI provider."""

    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)

    with pytest.raises(
        ConfigurationError,
        match="AGENT_WORKBENCH_MODEL is required",
    ):
        get_model_name("openai")


def test_environment_file_is_loaded(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Load variables from a local environment file."""

    environment_file = tmp_path / ".env"
    environment_file.write_text(
        "OPENAI_API_KEY=file-api-key\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    load_environment(environment_file)

    assert os.getenv("OPENAI_API_KEY") == "file-api-key"


def test_environment_file_does_not_override_existing_variables(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Preserve variables already defined by the runtime environment."""

    environment_file = tmp_path / ".env"
    environment_file.write_text(
        "OPENAI_API_KEY=file-api-key\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("OPENAI_API_KEY", "runtime-api-key")

    load_environment(environment_file)

    assert os.getenv("OPENAI_API_KEY") == "runtime-api-key"


def test_get_provider_name_accepts_anthropic(
    monkeypatch,
) -> None:
    """Accept Anthropic as a supported provider."""

    monkeypatch.setenv(
        PROVIDER_ENV_VAR,
        " Anthropic ",
    )

    assert get_provider_name() == "anthropic"


def test_get_model_name_requires_explicit_anthropic_model(
    monkeypatch,
) -> None:
    """Require explicit model selection for Anthropic."""

    monkeypatch.delenv(
        MODEL_ENV_VAR,
        raising=False,
    )

    with pytest.raises(
        ConfigurationError,
        match=MODEL_ENV_VAR,
    ):
        get_model_name("anthropic")


def _write_project_configuration(
    project_root: Path,
    content: str,
) -> Path:
    """Write one project configuration below its fixed project-relative path."""

    configuration_path = project_root / PROJECT_CONFIG_RELATIVE_PATH
    configuration_path.parent.mkdir(parents=True)
    configuration_path.write_text(content, encoding="utf-8")
    return configuration_path


def test_project_configuration_loads_every_supported_coding_field(
    tmp_path: Path,
) -> None:
    """Load all supported immutable provider-independent coding values."""

    configuration_path = _write_project_configuration(
        tmp_path,
        """\
[coding]
provider = "ollama"
model = "qwen3-coder:30b"
agent = "developer"
enable_tools = true
enable_actions = true
max_tool_rounds = 8
temperature = 0.2
top_p = 0.9
max_output_tokens = 4096
isolated = false
""",
    )

    configuration = load_project_configuration(configuration_path)

    assert configuration == ProjectCodingConfiguration(
        provider="ollama",
        model="qwen3-coder:30b",
        agent="developer",
        enable_tools=True,
        enable_actions=True,
        max_tool_rounds=8,
        temperature=0.2,
        top_p=0.9,
        max_output_tokens=4096,
        isolated=False,
    )


def _complete_project_coding_configuration(
    **overrides: object,
) -> ProjectCodingConfiguration:
    """Build one complete project coding configuration for creation tests."""

    values: dict[str, object] = {
        "provider": "ollama",
        "model": "qwen3-coder:30b",
        "agent": "developer",
        "enable_tools": True,
        "enable_actions": True,
        "max_tool_rounds": 8,
        "temperature": 0.2,
        "top_p": 0.9,
        "max_output_tokens": 4096,
        "isolated": False,
    }
    values.update(overrides)
    return ProjectCodingConfiguration(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"model": None}, r"\[coding\]\.model is required"),
        ({"model": 3}, r"\[coding\]\.model.*string"),
        ({"enable_tools": 1}, r"\[coding\]\.enable_tools.*boolean"),
    ],
)
def test_project_configuration_rendering_failure_creates_no_target(
    tmp_path: Path,
    overrides: dict[str, object],
    match: str,
) -> None:
    """Finish strict rendering validation before touching the target path."""

    configuration_path = tmp_path / PROJECT_CONFIG_RELATIVE_PATH

    with pytest.raises(ConfigurationError, match=match):
        create_project_configuration(
            tmp_path,
            _complete_project_coding_configuration(**overrides),
        )

    assert not configuration_path.exists()
    assert not configuration_path.parent.exists()


def test_rendering_failure_preserves_an_existing_project_configuration(
    tmp_path: Path,
) -> None:
    """Leave an existing configuration byte-for-byte unchanged on render failure."""

    configuration_path = _write_project_configuration(
        tmp_path,
        '[coding]\nmodel = "keep-me"\n',
    )
    original = configuration_path.read_bytes()

    with pytest.raises(
        ConfigurationError,
        match=r"\[coding\]\.enable_actions.*boolean",
    ):
        create_project_configuration(
            tmp_path,
            _complete_project_coding_configuration(enable_actions="yes"),
        )

    assert configuration_path.read_bytes() == original
    assert list(configuration_path.parent.iterdir()) == [configuration_path]


def test_project_configuration_is_discovered_from_root_and_nested_directory(
    tmp_path: Path,
) -> None:
    """Walk from either the root or a nested directory to the first config."""

    configuration_path = _write_project_configuration(
        tmp_path,
        '[coding]\nprovider = "ollama"\n',
    )
    nested = tmp_path / "src" / "package"
    nested.mkdir(parents=True)

    root_result = discover_project_configuration(tmp_path)
    nested_result = discover_project_configuration(nested)

    assert root_result is not None
    assert nested_result is not None
    assert root_result.project_root == tmp_path.resolve()
    assert nested_result.project_root == tmp_path.resolve()
    assert root_result.configuration_path == configuration_path.resolve()
    assert nested_result.configuration.provider == "ollama"


def test_non_coding_discovery_does_not_inspect_project_instructions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Load project configuration without touching coding-only instructions."""

    _write_project_configuration(tmp_path, '[coding]\nprovider = "ollama"\n')

    def fail_if_called(_project_root):
        raise AssertionError("project instructions loader must not be called")

    monkeypatch.setattr(
        "agent_workbench.config.load_project_instructions",
        fail_if_called,
    )

    result = discover_project_configuration(tmp_path)

    assert result is not None
    assert result.configuration.provider == "ollama"
    assert result.project_instructions is None


def test_discovery_loads_instructions_from_the_exact_configuration_root(
    tmp_path: Path,
) -> None:
    """Associate nested discovery only with the winning configuration root."""

    _write_project_configuration(tmp_path, '[coding]\nmodel = "outer"\n')
    (tmp_path / PROJECT_INSTRUCTIONS_RELATIVE_PATH).write_text(
        "Outer instructions.",
        encoding="utf-8",
    )
    project = tmp_path / "packages" / "inner"
    _write_project_configuration(project, '[coding]\nmodel = "inner"\n')
    instructions = "# Inner\n\n- Preserve this Markdown.\n"
    (project / PROJECT_INSTRUCTIONS_RELATIVE_PATH).write_text(
        instructions,
        encoding="utf-8",
    )
    nested = project / "src" / "package"
    nested.mkdir(parents=True)

    result = discover_project_configuration(
        nested,
        include_project_instructions=True,
    )

    assert result is not None
    assert result.project_root == project.resolve()
    assert result.project_instructions == instructions


def test_discovery_does_not_load_parent_instructions_or_agents_file(
    tmp_path: Path,
) -> None:
    """Ignore unrelated instruction sources outside the configured root."""

    parent_instructions = tmp_path / PROJECT_INSTRUCTIONS_RELATIVE_PATH
    parent_instructions.parent.mkdir()
    parent_instructions.write_text("Do not load parent.", encoding="utf-8")
    project = tmp_path / "project"
    _write_project_configuration(project, '[coding]\nmodel = "inner"\n')
    (project / "AGENTS.md").write_text("Do not load AGENTS.", encoding="utf-8")

    result = discover_project_configuration(
        project,
        include_project_instructions=True,
    )

    assert result is not None
    assert result.project_instructions is None


@pytest.mark.parametrize("content", [b"", b" \n\t"])
def test_empty_project_instructions_contribute_nothing(
    tmp_path: Path,
    content: bytes,
) -> None:
    """Treat empty and whitespace-only project instruction files as absent."""

    instructions_path = tmp_path / PROJECT_INSTRUCTIONS_RELATIVE_PATH
    instructions_path.parent.mkdir()
    instructions_path.write_bytes(content)

    assert load_project_instructions(tmp_path) is None


def test_project_instructions_accept_exact_size_limit(tmp_path: Path) -> None:
    """Accept strict UTF-8 instructions exactly at the 100 KiB limit."""

    instructions_path = tmp_path / PROJECT_INSTRUCTIONS_RELATIVE_PATH
    instructions_path.parent.mkdir()
    content = "a" * MAX_PROJECT_INSTRUCTIONS_SIZE_BYTES
    instructions_path.write_text(content, encoding="utf-8")

    assert load_project_instructions(tmp_path) == content


@pytest.mark.parametrize(
    ("contents", "expected"),
    [
        (b"a" * (100 * 1024 + 1), "exceeds the 102400-byte limit"),
        (b"valid-prefix\xff", "not valid UTF-8"),
    ],
)
def test_invalid_project_instruction_contents_are_rejected_without_paths(
    tmp_path: Path,
    contents: bytes,
    expected: str,
) -> None:
    """Reject oversized and invalid UTF-8 instructions with portable errors."""

    instructions_path = tmp_path / PROJECT_INSTRUCTIONS_RELATIVE_PATH
    instructions_path.parent.mkdir()
    instructions_path.write_bytes(contents)

    with pytest.raises(ConfigurationError, match=expected) as raised:
        load_project_instructions(tmp_path)

    assert str(tmp_path) not in str(raised.value)


def test_project_instructions_require_a_regular_file(tmp_path: Path) -> None:
    """Reject a directory at the fixed instructions location without paths."""

    instructions_path = tmp_path / PROJECT_INSTRUCTIONS_RELATIVE_PATH
    instructions_path.mkdir(parents=True)

    with pytest.raises(ConfigurationError, match="regular readable file") as raised:
        load_project_instructions(tmp_path)

    assert str(tmp_path) not in str(raised.value)


def test_nearest_project_configuration_wins(tmp_path: Path) -> None:
    """Stop upward traversal at the first matching project configuration."""

    _write_project_configuration(
        tmp_path,
        '[coding]\nmodel = "outer-model"\n',
    )
    nested_project = tmp_path / "packages" / "inner"
    _write_project_configuration(
        nested_project,
        '[coding]\nmodel = "inner-model"\n',
    )
    source = nested_project / "src"
    source.mkdir()

    result = discover_project_configuration(source)

    assert result is not None
    assert result.project_root == nested_project.resolve()
    assert result.configuration.model == "inner-model"


def test_missing_project_configuration_returns_none(tmp_path: Path) -> None:
    """Preserve application defaults when traversal reaches the root."""

    assert discover_project_configuration(tmp_path) is None


@pytest.mark.parametrize(
    ("content", "match"),
    [
        ("[coding\nprovider = 'ollama'\n", "invalid TOML"),
        ("[other]\nvalue = true\n", r"unknown section \[other\]"),
        ("[coding]\nunsupported = true\n", r"unknown key \[coding\]\.unsupported"),
        ("[coding]\nprovider = 3\n", r"\[coding\]\.provider.*string"),
        ("[coding]\ntemperature = 1.1\n", r"\[coding\]\.temperature.*0.0 and 1.0"),
        ("[coding]\nmax_tool_rounds = 0\n", r"\[coding\]\.max_tool_rounds.*positive"),
        ("[coding]\napi_key = 'secret'\n", r"\[coding\]\.api_key"),
    ],
)
def test_invalid_project_configuration_is_rejected_safely(
    tmp_path: Path,
    content: str,
    match: str,
) -> None:
    """Reject malformed, unknown, mistyped, unsafe, and out-of-range values."""

    configuration_path = _write_project_configuration(tmp_path, content)

    with pytest.raises(ConfigurationError, match=match) as raised:
        load_project_configuration(configuration_path)

    message = str(raised.value)
    assert str(tmp_path) not in message
    assert content not in message
    assert "secret" not in message


def test_discovery_treats_windows_style_name_as_a_safe_posix_component(
    tmp_path: Path,
) -> None:
    """Do not reinterpret a backslash-containing POSIX directory as traversal."""

    project = tmp_path / r"safe\windows-style"
    _write_project_configuration(project, '[coding]\nprovider = "ollama"\n')
    nested = project / "src"
    nested.mkdir()

    result = discover_project_configuration(nested)

    assert result is not None
    assert result.project_root == project.resolve()
