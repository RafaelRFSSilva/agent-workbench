"""Tests for reusable agent profiles."""

import pytest

from agent_workbench.agents import (
    AGENT_PROFILES,
    SUPPORTED_AGENT_NAMES,
    get_agent_profile,
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
