"""Reusable agent profiles for Agent Workbench."""

from dataclasses import dataclass
from importlib.resources import files
import tomllib
from pathlib import Path

from agent_workbench.errors import ConfigurationError

PROFILE_RESOURCE_PACKAGE = "agent_workbench.profiles"
PROFILE_FIELDS = frozenset(
    {
        "name",
        "description",
        "system_prompt",
    }
)


@dataclass(frozen=True, slots=True)
class AgentProfile:
    """Define the identity and behavior of a reusable agent."""

    name: str
    description: str
    system_prompt: str


def _require_non_empty_string(
    profile_data: dict[str, object],
    field_name: str,
    source: str,
) -> str:
    """Return a required non-empty string field."""

    value = profile_data.get(field_name)

    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(
            f"Agent profile '{source}' field '{field_name}' must be a non-empty string."
        )

    return value.strip()


def parse_agent_profile(
    content: str,
    source: str,
) -> AgentProfile:
    """Parse and validate an agent profile from TOML content."""

    try:
        profile_data = tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(
            f"Invalid TOML in agent profile '{source}': {exc}"
        ) from exc

    fields = set(profile_data)
    missing_fields = PROFILE_FIELDS - fields
    unsupported_fields = fields - PROFILE_FIELDS

    if missing_fields:
        missing = ", ".join(sorted(missing_fields))

        raise ConfigurationError(
            f"Agent profile '{source}' is missing required fields: {missing}."
        )

    if unsupported_fields:
        unsupported = ", ".join(sorted(unsupported_fields))

        raise ConfigurationError(
            f"Agent profile '{source}' contains unsupported fields: {unsupported}."
        )

    return AgentProfile(
        name=_require_non_empty_string(
            profile_data,
            "name",
            source,
        ),
        description=_require_non_empty_string(
            profile_data,
            "description",
            source,
        ),
        system_prompt=_require_non_empty_string(
            profile_data,
            "system_prompt",
            source,
        ),
    )


def load_agent_profile_file(path: Path) -> AgentProfile:
    """Load and validate an agent profile from an external TOML file."""

    profile_path = path.expanduser()

    if profile_path.suffix.lower() != ".toml":
        raise ConfigurationError(
            f"Agent profile file '{profile_path}' must use the .toml extension."
        )

    if not profile_path.exists():
        raise ConfigurationError(f"Agent profile file '{profile_path}' does not exist.")

    if not profile_path.is_file():
        raise ConfigurationError(f"Agent profile path '{profile_path}' is not a file.")

    try:
        content = profile_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConfigurationError(
            f"Could not read agent profile file '{profile_path}': {exc}"
        ) from exc

    return parse_agent_profile(
        content=content,
        source=str(profile_path),
    )


def load_builtin_agent_profiles() -> dict[str, AgentProfile]:
    """Load the agent profiles distributed with the application."""

    profile_resources = files(PROFILE_RESOURCE_PACKAGE)
    loaded_profiles: dict[str, AgentProfile] = {}

    for resource in sorted(
        profile_resources.iterdir(),
        key=lambda item: item.name,
    ):
        if not resource.is_file() or not resource.name.endswith(".toml"):
            continue

        profile_name = resource.name.removesuffix(".toml")
        source = f"{PROFILE_RESOURCE_PACKAGE}/{resource.name}"

        loaded_profiles[profile_name] = parse_agent_profile(
            resource.read_text(encoding="utf-8"),
            source,
        )

    if not loaded_profiles:
        raise ConfigurationError("No built-in agent profiles were found.")

    return loaded_profiles


AGENT_PROFILES = load_builtin_agent_profiles()
SUPPORTED_AGENT_NAMES = frozenset(AGENT_PROFILES)


def get_agent_profile(agent_name: str) -> AgentProfile:
    """Return a reusable agent profile by its normalized name."""

    normalized_name = agent_name.strip().lower()

    try:
        return AGENT_PROFILES[normalized_name]
    except KeyError as exc:
        supported_names = ", ".join(sorted(SUPPORTED_AGENT_NAMES))

        raise ConfigurationError(
            f"Unsupported agent profile '{agent_name}'. "
            f"Supported profiles: {supported_names}."
        ) from exc
