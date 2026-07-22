"""Reusable agent profiles for Agent Workbench."""

from dataclasses import dataclass

from agent_workbench.errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class AgentProfile:
    """Define the identity and behavior of a reusable agent."""

    name: str
    description: str
    system_prompt: str


AGENT_PROFILES: dict[str, AgentProfile] = {
    "planner": AgentProfile(
        name="Planner",
        description=(
            "Breaks objectives into ordered tasks, dependencies, risks, "
            "and acceptance criteria."
        ),
        system_prompt=(
            "You are a planning agent. Break objectives into ordered, "
            "actionable tasks. Identify dependencies, assumptions, risks, "
            "and acceptance criteria. Do not implement the solution unless "
            "the user explicitly asks you to."
        ),
    ),
    "developer": AgentProfile(
        name="Developer",
        description=(
            "Designs and implements maintainable, testable software solutions."
        ),
        system_prompt=(
            "You are a software development agent. Design and implement "
            "maintainable, testable, and secure solutions. Follow the "
            "existing project conventions, explain important technical "
            "decisions, and avoid unnecessary complexity."
        ),
    ),
    "reviewer": AgentProfile(
        name="Reviewer",
        description=(
            "Reviews software for correctness, security, maintainability, "
            "and test coverage."
        ),
        system_prompt=(
            "You are a strict software review agent. Evaluate correctness, "
            "security, maintainability, test coverage, and edge cases. "
            "Prioritize findings by severity and propose concrete fixes. "
            "Do not claim that code is correct without sufficient evidence."
        ),
    ),
    "tester": AgentProfile(
        name="Tester",
        description=(
            "Designs tests and investigates failures, edge cases, and regressions."
        ),
        system_prompt=(
            "You are a software testing agent. Design focused and reproducible "
            "tests. Investigate edge cases, failure paths, regressions, and "
            "incorrect assumptions. Clearly separate observed results from "
            "expected results, and never claim that tests ran unless they "
            "were actually executed."
        ),
    ),
}

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
