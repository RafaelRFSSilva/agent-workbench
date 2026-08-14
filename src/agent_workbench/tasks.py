from collections.abc import Iterable
from dataclasses import dataclass

from agent_workbench.errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class TaskId:
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value.strip():
            raise ConfigurationError("task id must be a non-blank string.")


@dataclass(frozen=True, slots=True, init=False)
class TaskSpec:
    objective: str
    acceptance_criteria: tuple[str, ...]
    dependencies: tuple["TaskId", ...]
    task_id: "TaskId | None"

    def __init__(
        self,
        objective: str,
        acceptance_criteria: Iterable[str],
        *,
        dependencies: Iterable["TaskId"] = (),
        task_id: "TaskId | None" = None,
    ) -> None:
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

        try:
            deps = tuple(dependencies)
        except TypeError as exc:
            raise ConfigurationError(
                "dependencies must be an iterable of TaskId"
            ) from exc
        for dep in deps:
            if not isinstance(dep, TaskId):
                raise ConfigurationError(f"dependency '{dep}' must be a TaskId")

        if task_id is not None and not isinstance(task_id, TaskId):
            raise ConfigurationError("task id must be a TaskId or None.")

        object.__setattr__(self, "objective", objective)
        object.__setattr__(self, "acceptance_criteria", criteria)
        object.__setattr__(self, "dependencies", deps)
        object.__setattr__(self, "task_id", task_id)
