"""Tests for reusable agent profiles."""

import pytest

from agent_workbench.agents import (
    AGENT_PROFILES,
    SUPPORTED_AGENT_NAMES,
    get_agent_profile,
    load_agent_profile_file,
    parse_agent_profile,
)
from agent_workbench.errors import ConfigurationError


def test_builtin_agent_profiles_are_available() -> None:
    """Provide the initial reusable agent profile collection."""

    assert SUPPORTED_AGENT_NAMES == {
        "planner",
        "developer",
        "reviewer",
        "tester",
    }

    assert set(AGENT_PROFILES) == SUPPORTED_AGENT_NAMES

    for profile in AGENT_PROFILES.values():
        assert profile.name
        assert profile.description
        assert profile.system_prompt


def test_get_agent_profile_normalizes_the_name() -> None:
    """Resolve agent names without surrounding whitespace or casing."""

    profile = get_agent_profile("  Reviewer  ")

    assert profile.name == "Reviewer"
    assert "software review agent" in profile.system_prompt


def test_get_agent_profile_rejects_unknown_profiles() -> None:
    """Reject agent profiles that are not registered."""

    with pytest.raises(
        ConfigurationError,
        match="Unsupported agent profile 'unknown'",
    ):
        get_agent_profile("unknown")


def test_parse_agent_profile_returns_valid_profile() -> None:
    """Parse a valid profile from TOML content."""

    profile = parse_agent_profile(
        content="""
name = "Security Reviewer"
description = "Reviews application security."
system_prompt = "You are a security review agent."
""",
        source="security-reviewer.toml",
    )

    assert profile.name == "Security Reviewer"
    assert profile.description == "Reviews application security."
    assert profile.system_prompt == "You are a security review agent."


def test_parse_agent_profile_rejects_invalid_toml() -> None:
    """Reject malformed TOML profile content."""

    with pytest.raises(
        ConfigurationError,
        match="Invalid TOML",
    ):
        parse_agent_profile(
            content='name = "Broken',
            source="broken.toml",
        )


def test_parse_agent_profile_rejects_missing_fields() -> None:
    """Reject profiles without every required field."""

    with pytest.raises(
        ConfigurationError,
        match="missing required fields: system_prompt",
    ):
        parse_agent_profile(
            content="""
name = "Incomplete"
description = "Missing its system prompt."
""",
            source="incomplete.toml",
        )


def test_parse_agent_profile_rejects_blank_fields() -> None:
    """Reject required fields containing only whitespace."""

    with pytest.raises(
        ConfigurationError,
        match="field 'name' must be a non-empty string",
    ):
        parse_agent_profile(
            content="""
name = "   "
description = "Invalid profile."
system_prompt = "Some instructions."
""",
            source="blank.toml",
        )


def test_parse_agent_profile_rejects_unsupported_fields() -> None:
    """Reject unknown fields that may represent configuration mistakes."""

    with pytest.raises(
        ConfigurationError,
        match="unsupported fields: temperature",
    ):
        parse_agent_profile(
            content="""
name = "Unexpected"
description = "Contains an unsupported field."
system_prompt = "Some instructions."
temperature = 0.5
""",
            source="unexpected.toml",
        )


def test_load_agent_profile_file_returns_valid_profile(tmp_path) -> None:
    """Load a valid custom agent profile from the filesystem."""

    profile_path = tmp_path / "security-reviewer.toml"
    profile_path.write_text(
        """
name = "Security Reviewer"
description = "Reviews application security."
system_prompt = "You are a security review agent."
""",
        encoding="utf-8",
    )

    profile = load_agent_profile_file(profile_path)

    assert profile.name == "Security Reviewer"
    assert profile.description == "Reviews application security."
    assert profile.system_prompt == "You are a security review agent."


def test_load_agent_profile_file_rejects_missing_file(tmp_path) -> None:
    """Reject a custom profile file that does not exist."""

    profile_path = tmp_path / "missing.toml"

    with pytest.raises(
        ConfigurationError,
        match="does not exist",
    ):
        load_agent_profile_file(profile_path)


def test_load_agent_profile_file_rejects_directory(tmp_path) -> None:
    """Reject a directory passed as an agent profile file."""

    profile_path = tmp_path / "profile.toml"
    profile_path.mkdir()

    with pytest.raises(
        ConfigurationError,
        match="is not a file",
    ):
        load_agent_profile_file(profile_path)


def test_load_agent_profile_file_rejects_non_toml_extension(tmp_path) -> None:
    """Reject custom profile files without a TOML extension."""

    profile_path = tmp_path / "profile.json"
    profile_path.write_text("{}", encoding="utf-8")

    with pytest.raises(
        ConfigurationError,
        match=r"must use the \.toml extension",
    ):
        load_agent_profile_file(profile_path)
