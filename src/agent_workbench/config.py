"""Configuration helpers for Agent Workbench."""

import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from agent_workbench.agents import SUPPORTED_AGENT_NAMES
from agent_workbench.errors import ConfigurationError
from dotenv import load_dotenv

ProviderName = Literal["anthropic", "ollama", "openai"]

DEFAULT_PROVIDER_NAME: ProviderName = "ollama"
DEFAULT_MODEL_NAME = "gpt-oss:20b"

PROVIDER_ENV_VAR = "AGENT_WORKBENCH_PROVIDER"
MODEL_ENV_VAR = "AGENT_WORKBENCH_MODEL"

SUPPORTED_PROVIDERS = {"anthropic", "ollama", "openai"}
PROJECT_CONFIG_RELATIVE_PATH = Path(".agent-workbench") / "config.toml"
PROJECT_CONFIG_CONTEXT = ".agent-workbench/config.toml"
PROJECT_CONFIG_SECTIONS = frozenset({"coding"})
PROJECT_CODING_KEYS = frozenset(
    {
        "provider",
        "model",
        "agent",
        "enable_tools",
        "enable_actions",
        "max_tool_rounds",
        "temperature",
        "top_p",
        "max_output_tokens",
        "isolated",
    }
)


@dataclass(frozen=True, slots=True)
class ProjectCodingConfiguration:
    """Represent optional immutable coding defaults from one project file."""

    provider: ProviderName | None = None
    model: str | None = None
    agent: str | None = None
    enable_tools: bool | None = None
    enable_actions: bool | None = None
    max_tool_rounds: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_output_tokens: int | None = None
    isolated: bool | None = None


@dataclass(frozen=True, slots=True)
class ProjectConfiguration:
    """Bind validated project coding values to their detected project root."""

    project_root: Path
    configuration_path: Path
    configuration: ProjectCodingConfiguration


def discover_project_configuration(
    start_path: Path,
) -> ProjectConfiguration | None:
    """Find and load the nearest project configuration above one directory."""

    try:
        current = start_path.expanduser().resolve(strict=True)
    except FileNotFoundError:
        return None
    except (OSError, RuntimeError):
        raise ConfigurationError(
            "Unable to inspect the configured workspace for project configuration."
        ) from None

    if not current.is_dir():
        raise ConfigurationError(
            "Project configuration discovery requires a workspace directory."
        )

    while True:
        candidate = current / PROJECT_CONFIG_RELATIVE_PATH
        if candidate.is_file():
            try:
                configuration_path = candidate.resolve(strict=True)
                configuration_path.relative_to(current)
            except (FileNotFoundError, OSError, RuntimeError, ValueError):
                raise ConfigurationError(
                    f"Project configuration {PROJECT_CONFIG_CONTEXT} "
                    "must remain inside its detected project root."
                ) from None
            return ProjectConfiguration(
                project_root=current,
                configuration_path=configuration_path,
                configuration=load_project_configuration(configuration_path),
            )

        parent = current.parent
        if parent == current:
            return None
        current = parent


def load_project_configuration(
    configuration_path: Path,
) -> ProjectCodingConfiguration:
    """Load one strict credential-free project coding configuration."""

    try:
        with configuration_path.open("rb") as configuration_file:
            document = tomllib.load(configuration_file)
    except tomllib.TOMLDecodeError:
        raise ConfigurationError(
            f"Project configuration {PROJECT_CONFIG_CONTEXT} contains invalid TOML."
        ) from None
    except (OSError, RuntimeError):
        raise ConfigurationError(
            f"Unable to read project configuration {PROJECT_CONFIG_CONTEXT}."
        ) from None

    unknown_sections = sorted(set(document) - PROJECT_CONFIG_SECTIONS)
    if unknown_sections:
        raise ConfigurationError(
            f"Project configuration {PROJECT_CONFIG_CONTEXT} has unknown section "
            f"[{unknown_sections[0]}]."
        )

    coding = document.get("coding", {})
    if not isinstance(coding, dict):
        raise ConfigurationError(
            f"Project configuration {PROJECT_CONFIG_CONTEXT} section [coding] "
            "must be a table."
        )

    unknown_keys = sorted(set(coding) - PROJECT_CODING_KEYS)
    if unknown_keys:
        raise ConfigurationError(
            f"Project configuration {PROJECT_CONFIG_CONTEXT} has unknown key "
            f"[coding].{unknown_keys[0]}."
        )

    provider = _optional_project_provider(coding)
    model = _optional_project_non_blank_string(coding, "model")
    agent = _optional_project_agent(coding)
    enable_tools = _optional_project_boolean(coding, "enable_tools")
    enable_actions = _optional_project_boolean(coding, "enable_actions")
    max_tool_rounds = _optional_project_positive_integer(coding, "max_tool_rounds")
    temperature = _optional_project_unit_interval(coding, "temperature")
    top_p = _optional_project_unit_interval(coding, "top_p")
    max_output_tokens = _optional_project_positive_integer(
        coding,
        "max_output_tokens",
    )
    isolated = _optional_project_boolean(coding, "isolated")

    return ProjectCodingConfiguration(
        provider=provider,
        model=model,
        agent=agent,
        enable_tools=enable_tools,
        enable_actions=enable_actions,
        max_tool_rounds=max_tool_rounds,
        temperature=temperature,
        top_p=top_p,
        max_output_tokens=max_output_tokens,
        isolated=isolated,
    )


def create_project_configuration(
    project_root: Path,
    configuration: ProjectCodingConfiguration,
) -> Path:
    """Create one deterministic project configuration without overwriting."""

    content = render_project_configuration(configuration)
    configuration_path = project_root / PROJECT_CONFIG_RELATIVE_PATH
    try:
        configuration_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise ConfigurationError(
            f"Unable to create directory for {PROJECT_CONFIG_CONTEXT}."
        ) from None

    created = False
    try:
        configuration_file = configuration_path.open(
            "x",
            encoding="utf-8",
            newline="\n",
        )
        created = True
        with configuration_file:
            configuration_file.write(content)
    except FileExistsError:
        raise ConfigurationError(
            f"{PROJECT_CONFIG_CONTEXT} already exists; existing file preserved."
        ) from None
    except (OSError, RuntimeError, UnicodeError):
        if created:
            try:
                configuration_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise ConfigurationError(
            f"Unable to create {PROJECT_CONFIG_CONTEXT}; no configuration was saved."
        ) from None

    return configuration_path


def render_project_configuration(
    configuration: ProjectCodingConfiguration,
) -> str:
    """Render one complete human-readable project coding configuration."""

    required_values = {
        "provider": configuration.provider,
        "model": configuration.model,
        "agent": configuration.agent,
        "enable_tools": configuration.enable_tools,
        "enable_actions": configuration.enable_actions,
        "max_tool_rounds": configuration.max_tool_rounds,
        "temperature": configuration.temperature,
        "top_p": configuration.top_p,
        "max_output_tokens": configuration.max_output_tokens,
        "isolated": configuration.isolated,
    }
    missing = [key for key, value in required_values.items() if value is None]
    if missing:
        raise ConfigurationError(
            f"Cannot create {PROJECT_CONFIG_CONTEXT}: "
            f"[coding].{missing[0]} is required."
        )

    provider = _optional_project_provider(required_values)
    model = _optional_project_non_blank_string(required_values, "model")
    agent = _optional_project_agent(required_values)
    enable_tools = _optional_project_boolean(required_values, "enable_tools")
    enable_actions = _optional_project_boolean(required_values, "enable_actions")
    max_tool_rounds = _optional_project_positive_integer(
        required_values,
        "max_tool_rounds",
    )
    temperature = _optional_project_unit_interval(required_values, "temperature")
    top_p = _optional_project_unit_interval(required_values, "top_p")
    max_output_tokens = _optional_project_positive_integer(
        required_values,
        "max_output_tokens",
    )
    isolated = _optional_project_boolean(required_values, "isolated")

    return (
        "[coding]\n"
        f"provider = {_toml_string(provider)}\n"
        f"model = {_toml_string(model)}\n"
        f"agent = {_toml_string(agent)}\n"
        f"enable_tools = {_toml_boolean(enable_tools)}\n"
        f"enable_actions = {_toml_boolean(enable_actions)}\n"
        f"max_tool_rounds = {max_tool_rounds}\n"
        f"temperature = {temperature}\n"
        f"top_p = {top_p}\n"
        f"max_output_tokens = {max_output_tokens}\n"
        f"isolated = {_toml_boolean(isolated)}\n"
    )


def _toml_string(value: object) -> str:
    """Encode a validated string as one TOML basic string."""

    if not isinstance(value, str):
        raise ConfigurationError(
            f"Cannot create {PROJECT_CONFIG_CONTEXT}: expected a string."
        )
    return json.dumps(value, ensure_ascii=False)


def _toml_boolean(value: object) -> str:
    """Encode a validated strict boolean for TOML."""

    if not isinstance(value, bool):
        raise ConfigurationError(
            f"Cannot create {PROJECT_CONFIG_CONTEXT}: expected a boolean."
        )
    return "true" if value else "false"


def _optional_project_provider(
    coding: dict[str, object],
) -> ProviderName | None:
    """Return one supported optional project provider."""

    value = _optional_project_non_blank_string(coding, "provider")
    if value is None:
        return None
    if value not in SUPPORTED_PROVIDERS:
        supported = ", ".join(sorted(SUPPORTED_PROVIDERS))
        raise ConfigurationError(
            f"Project configuration {PROJECT_CONFIG_CONTEXT} [coding].provider "
            f"must be one of: {supported}."
        )
    return cast(ProviderName, value)


def _optional_project_agent(coding: dict[str, object]) -> str | None:
    """Return one supported optional built-in agent name."""

    value = _optional_project_non_blank_string(coding, "agent")
    if value is None:
        return None
    if value not in SUPPORTED_AGENT_NAMES:
        supported = ", ".join(sorted(SUPPORTED_AGENT_NAMES))
        raise ConfigurationError(
            f"Project configuration {PROJECT_CONFIG_CONTEXT} [coding].agent "
            f"must be one of: {supported}."
        )
    return value


def _optional_project_non_blank_string(
    coding: dict[str, object],
    key: str,
) -> str | None:
    """Return one optional non-blank project string."""

    if key not in coding:
        return None
    value = coding[key]
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(
            f"Project configuration {PROJECT_CONFIG_CONTEXT} [coding].{key} "
            "must be a non-empty string."
        )
    return value.strip()


def _optional_project_boolean(
    coding: dict[str, object],
    key: str,
) -> bool | None:
    """Return one optional strict project boolean."""

    if key not in coding:
        return None
    value = coding[key]
    if not isinstance(value, bool):
        raise ConfigurationError(
            f"Project configuration {PROJECT_CONFIG_CONTEXT} [coding].{key} "
            "must be a boolean."
        )
    return value


def _optional_project_positive_integer(
    coding: dict[str, object],
    key: str,
) -> int | None:
    """Return one optional strict positive project integer."""

    if key not in coding:
        return None
    value = coding[key]
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigurationError(
            f"Project configuration {PROJECT_CONFIG_CONTEXT} [coding].{key} "
            "must be a positive integer."
        )
    return value


def _optional_project_unit_interval(
    coding: dict[str, object],
    key: str,
) -> float | None:
    """Return one optional project number in the portable unit interval."""

    if key not in coding:
        return None
    value = coding[key]
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0.0 <= value <= 1.0
    ):
        raise ConfigurationError(
            f"Project configuration {PROJECT_CONFIG_CONTEXT} [coding].{key} "
            "must be a number between 0.0 and 1.0."
        )
    return float(value)


def load_environment(
    dotenv_path: str | os.PathLike[str] | None = None,
) -> None:
    """Load local environment variables without overriding existing values."""

    load_dotenv(
        dotenv_path=dotenv_path,
        override=False,
    )


def get_provider_name() -> ProviderName:
    """Return the configured provider name."""

    configured_provider = (
        os.getenv(
            PROVIDER_ENV_VAR,
            DEFAULT_PROVIDER_NAME,
        )
        .strip()
        .lower()
    )

    if configured_provider not in SUPPORTED_PROVIDERS:
        supported = ", ".join(sorted(SUPPORTED_PROVIDERS))
        raise ConfigurationError(
            f"Unsupported provider '{configured_provider}'. "
            f"Supported providers: {supported}."
        )

    return cast(ProviderName, configured_provider)


def get_model_name(
    provider_name: ProviderName = DEFAULT_PROVIDER_NAME,
) -> str:
    """Return the configured model name for the selected provider."""

    configured_model = os.getenv(MODEL_ENV_VAR, "").strip()

    if configured_model:
        return configured_model

    if provider_name == "ollama":
        return DEFAULT_MODEL_NAME

    raise ConfigurationError(
        f"{MODEL_ENV_VAR} is required when {PROVIDER_ENV_VAR}={provider_name}."
    )
