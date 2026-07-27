import dataclasses
import pytest
from agent_workbench.errors import ConfigurationError
from agent_workbench.tasks import TaskSpec


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
