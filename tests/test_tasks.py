import dataclasses
import pytest
from agent_workbench.errors import ConfigurationError
from agent_workbench.tasks import TaskSpec, TaskId


def test_task_id_value_preservation() -> None:
    tid = TaskId("  id123  ")
    assert tid.value == "  id123  "


def test_task_id_equality_and_hashability() -> None:
    a = TaskId("id")
    b = TaskId("id")
    c = TaskId("other")
    assert a == b
    assert hash(a) == hash(b)
    s = {a, b, c}
    assert len(s) == 2


def test_task_id_frozen_and_slotted() -> None:
    tid = TaskId("id")
    with pytest.raises(dataclasses.FrozenInstanceError):
        # type: ignore[assignment]
        tid.value = "new"
    assert not hasattr(tid, "__dict__")


def test_task_id_rejection_of_invalid_values() -> None:
    for invalid in ["", "   ", 123, None]:
        with pytest.raises(ConfigurationError):
            TaskId(invalid)  # type: ignore[arg-type]


def test_task_spec_without_task_id_defaults_to_none() -> None:
    spec = TaskSpec(objective="obj", acceptance_criteria=["c1"])
    assert spec.task_id is None


def test_task_spec_with_valid_task_id() -> None:
    tid = TaskId("id")
    spec = TaskSpec(objective="obj", acceptance_criteria=["c1"], task_id=tid)
    assert spec.task_id == tid


def test_task_spec_rejects_invalid_task_id() -> None:
    for invalid in [123, "id"]:
        with pytest.raises(ConfigurationError):
            TaskSpec(objective="obj", acceptance_criteria=["c1"], task_id=invalid)  # type: ignore[arg-type]


# Existing tests below remain unchanged.


def test_preserves_valid_text() -> None:
    spec = TaskSpec(
        objective="  leading and trailing  ", acceptance_criteria=["  crit1  "]
    )
    assert spec.objective == "  leading and trailing  "
    assert spec.acceptance_criteria[0] == "  crit1  "


def test_preserves_criteria_order_and_tuple() -> None:
    order = ["first", "second", "third"]
    spec = TaskSpec(objective="obj", acceptance_criteria=order)
    assert isinstance(spec.acceptance_criteria, tuple)
    assert list(spec.acceptance_criteria) == order


def test_snapshots_mutable_criteria() -> None:
    original = ["one", "two"]
    spec = TaskSpec(objective="obj", acceptance_criteria=original)
    original.append("three")
    assert list(spec.acceptance_criteria) == ["one", "two"]


# The following test ensures that the dataclass is frozen and slotted.
def test_is_frozen_and_slotted() -> None:
    spec = TaskSpec(objective="obj", acceptance_criteria=["c1"])
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.objective = "new"
    assert not hasattr(spec, "__dict__")


# Value equality and hashability for equivalent inputs.
def test_value_equality_and_hashability() -> None:
    list_input = ["c1", "c2"]
    tuple_input = tuple(list_input)
    spec_list = TaskSpec(objective="obj", acceptance_criteria=list_input)
    spec_tuple = TaskSpec(objective="obj", acceptance_criteria=tuple_input)
    assert spec_list == spec_tuple
    assert hash(spec_list) == hash(spec_tuple)
    s = {spec_list, spec_tuple}
    assert len(s) == 1


# Parameterize invalid objectives.
@pytest.mark.parametrize(
    "invalid_objective",
    ["", "   ", 123, None],
    ids=[str(v) for v in ["empty", "whitespace", "int", "none"]],
)
def test_rejects_invalid_objective(invalid_objective: object) -> None:
    with pytest.raises(ConfigurationError):
        TaskSpec(objective=invalid_objective, acceptance_criteria=["c1"])


# Empty criteria collections.
def test_rejects_empty_criteria() -> None:
    for empty in [[], tuple()]:
        with pytest.raises(ConfigurationError):
            TaskSpec(objective="obj", acceptance_criteria=empty)


# Parameterize invalid criterion items.
@pytest.mark.parametrize(
    "invalid_criterion",
    ["", "   ", 123, None],
    ids=[str(v) for v in ["empty", "whitespace", "int", "none"]],
)
def test_rejects_invalid_criterion(invalid_criterion: object) -> None:
    with pytest.raises(ConfigurationError):
        TaskSpec(objective="obj", acceptance_criteria=[invalid_criterion, "c2"])


# Bare string criteria collection.
def test_rejects_bare_string_criteria_collection() -> None:
    with pytest.raises(ConfigurationError):
        TaskSpec(objective="obj", acceptance_criteria="not a list")
