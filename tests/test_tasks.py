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
    assert spec.task_id is tid
    assert spec.task_id == tid


def test_task_spec_rejects_invalid_task_id() -> None:
    for invalid in [123, "id"]:
        with pytest.raises(ConfigurationError):
            TaskSpec(objective="obj", acceptance_criteria=["c1"], task_id=invalid)  # type: ignore[arg-type]


def test_task_spec_equality_and_hashability_with_distinct_equal_task_ids() -> None:
    task_id_a = TaskId("task-1")
    task_id_b = TaskId("task-1")

    spec_a = TaskSpec(
        objective="obj",
        acceptance_criteria=["c1", "c2"],
        task_id=task_id_a,
    )
    spec_b = TaskSpec(
        objective="obj",
        acceptance_criteria=["c1", "c2"],
        task_id=task_id_b,
    )

    assert task_id_a is not task_id_b
    assert spec_a == spec_b
    assert hash(spec_a) == hash(spec_b)


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


# Tests for TaskSpec dependencies feature


def test_default_empty_dependencies() -> None:
    spec = TaskSpec(objective="obj", acceptance_criteria=["c1"])
    assert spec.dependencies == ()


def test_explicit_tuple_dependencies() -> None:
    dep1 = TaskId("dep1")
    dep2 = TaskId("dep2")
    spec = TaskSpec(
        objective="obj", acceptance_criteria=["c1"], dependencies=(dep1, dep2)
    )
    assert spec.dependencies == (dep1, dep2)


def test_list_dependencies_snapshotted_to_tuple() -> None:
    dep1 = TaskId("dep1")
    dep2 = TaskId("dep2")
    original_deps = [dep1, dep2]
    spec = TaskSpec(
        objective="obj", acceptance_criteria=["c1"], dependencies=original_deps
    )
    assert isinstance(spec.dependencies, tuple)
    assert spec.dependencies == (dep1, dep2)


def test_original_list_mutation_does_not_alter_taskspec() -> None:
    dep1 = TaskId("dep1")
    original_deps = [dep1]
    spec = TaskSpec(
        objective="obj", acceptance_criteria=["c1"], dependencies=original_deps
    )
    original_deps.append(TaskId("dep2"))
    assert len(spec.dependencies) == 1
    assert spec.dependencies[0] == dep1


def test_dependency_order_preserved() -> None:
    dep1 = TaskId("first")
    dep2 = TaskId("second")
    dep3 = TaskId("third")
    spec = TaskSpec(
        objective="obj", acceptance_criteria=["c1"], dependencies=[dep1, dep2, dep3]
    )
    assert list(spec.dependencies) == [dep1, dep2, dep3]


def test_duplicate_dependencies_preserved() -> None:
    dep = TaskId("dup")
    spec = TaskSpec(
        objective="obj", acceptance_criteria=["c1"], dependencies=[dep, dep]
    )
    assert list(spec.dependencies) == [dep, dep]


def test_invalid_non_taskid_dependency_raises_error() -> None:
    for invalid in [123, "not-a-task-id", None]:
        with pytest.raises(ConfigurationError):
            TaskSpec(
                objective="obj", acceptance_criteria=["c1"], dependencies=[invalid]
            )  # type: ignore[list-item]


def test_equality_with_equivalent_dependencies() -> None:
    dep1 = TaskId("dep1")
    dep2 = TaskId("dep2")
    spec_a = TaskSpec(
        objective="obj", acceptance_criteria=["c1"], dependencies=(dep1, dep2)
    )
    spec_b = TaskSpec(
        objective="obj", acceptance_criteria=["c1"], dependencies=(dep1, dep2)
    )
    assert spec_a == spec_b


def test_inequality_when_dependencies_differ() -> None:
    dep1 = TaskId("dep1")
    dep2 = TaskId("dep2")
    dep3 = TaskId("dep3")
    spec_a = TaskSpec(
        objective="obj", acceptance_criteria=["c1"], dependencies=(dep1, dep2)
    )
    spec_b = TaskSpec(
        objective="obj", acceptance_criteria=["c1"], dependencies=(dep1, dep3)
    )
    assert spec_a != spec_b


def test_hashing_consistent_with_equality() -> None:
    dep1 = TaskId("dep1")
    dep2 = TaskId("dep2")
    spec_a = TaskSpec(
        objective="obj", acceptance_criteria=["c1"], dependencies=(dep1, dep2)
    )
    spec_b = TaskSpec(
        objective="obj", acceptance_criteria=["c1"], dependencies=(dep1, dep2)
    )
    assert hash(spec_a) == hash(spec_b)
    s = {spec_a, spec_b}
    assert len(s) == 1


def test_existing_taskspec_construction_still_supported() -> None:
    # Ensure backwards compatibility - no dependencies parameter
    spec = TaskSpec(objective="obj", acceptance_criteria=["c1"])
    assert spec.objective == "obj"
    assert spec.acceptance_criteria == ("c1",)
    assert spec.dependencies == ()


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
