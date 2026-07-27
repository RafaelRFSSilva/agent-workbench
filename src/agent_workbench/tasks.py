from collections.abc import Iterable
from dataclasses import dataclass

from agent_workbench.errors import ConfigurationError


@dataclass(frozen=True, slots=True, init=False)
class TaskSpec:
    objective: str
    acceptance_criteria: tuple[str, ...]

    def __init__(self, objective: str, acceptance_criteria: Iterable[str]) -> None:
        if not isinstance(objective, str) or not objective.strip():
            raise ConfigurationError("objective must be a non-empty string")
        if isinstance(acceptance_criteria, str):
            raise ConfigurationError(
                "acceptance criteria must be an iterable of strings"
            )
        try:
            criteria = tuple(acceptance_criteria)
        except TypeError as exc:
            raise ConfigurationError("acceptance criteria must be an iterable") from exc
        if not criteria:
            raise ConfigurationError("at least one acceptance criterion is required")
        for c in criteria:
            if not isinstance(c, str) or not c.strip():
                raise ConfigurationError(f"criterion '{c}' must be a non-empty string")
        object.__setattr__(self, "objective", objective)
        object.__setattr__(self, "acceptance_criteria", criteria)
