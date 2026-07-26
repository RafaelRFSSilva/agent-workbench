"""Tests for provider-independent tool registration and execution."""

import pytest

from agent_workbench.errors import ConfigurationError
from agent_workbench.tool_registry import ToolRegistry
from agent_workbench.tools import ToolDefinition, ToolInvocation, ToolResult


def create_calculator_definition() -> ToolDefinition:
    """Create a calculator tool definition for registry tests."""

    return ToolDefinition(
        name="calculator",
        description="Evaluate a mathematical expression.",
        input_schema={
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                }
            },
            "required": [
                "expression",
            ],
            "additionalProperties": False,
        },
    )


def create_project_information_definition() -> ToolDefinition:
    """Create a project information tool definition for registry tests."""

    return ToolDefinition(
        name="project_information",
        description="Return project information.",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    )


def test_empty_registry_exposes_no_definitions() -> None:
    """Expose no tool definitions before registration."""

    registry = ToolRegistry()

    assert registry.definitions == ()


def test_registration_preserves_definition_order() -> None:
    """Expose registered tool definitions in registration order."""

    calculator = create_calculator_definition()
    project_information = create_project_information_definition()
    registry = ToolRegistry()

    registry.register(calculator, lambda arguments: arguments)
    registry.register(project_information, lambda arguments: arguments)

    assert registry.definitions == (
        calculator,
        project_information,
    )


def test_registration_rejects_duplicate_tool_names() -> None:
    """Reject registrations that reuse an existing tool name."""

    registry = ToolRegistry()
    registry.register(
        create_calculator_definition(),
        lambda arguments: arguments,
    )

    with pytest.raises(
        ConfigurationError,
        match="Tool 'calculator' is already registered",
    ):
        registry.register(
            create_calculator_definition(),
            lambda arguments: arguments,
        )


def test_execute_passes_invocation_arguments_to_registered_handler() -> None:
    """Execute a matching handler with the invocation arguments."""

    received_arguments = {}

    def calculate(arguments: dict[str, object]) -> dict[str, object]:
        received_arguments.update(arguments)
        return {
            "value": 4,
        }

    registry = ToolRegistry()
    registry.register(
        create_calculator_definition(),
        calculate,
    )
    invocation = ToolInvocation(
        id="call-123",
        tool_name="calculator",
        arguments={
            "expression": "2 + 2",
        },
    )

    result = registry.execute(invocation)

    assert received_arguments == {
        "expression": "2 + 2",
    }
    assert result == ToolResult(
        invocation_id="call-123",
        status="success",
        output={
            "value": 4,
        },
    )


def test_execute_returns_error_result_for_unknown_tool() -> None:
    """Return an error result instead of crashing for unknown tools."""

    result = ToolRegistry().execute(
        ToolInvocation(
            id="call-123",
            tool_name="unknown",
            arguments={},
        )
    )

    assert result == ToolResult(
        invocation_id="call-123",
        status="error",
        error="Unknown tool 'unknown'.",
    )


def test_execute_returns_safe_error_result_for_handler_exception() -> None:
    """Do not expose handler exception details through tool results."""

    def fail(arguments: dict[str, object]) -> None:
        raise RuntimeError("sensitive internal detail")

    registry = ToolRegistry()
    registry.register(
        create_calculator_definition(),
        fail,
    )

    result = registry.execute(
        ToolInvocation(
            id="call-123",
            tool_name="calculator",
            arguments={},
        )
    )

    assert result == ToolResult(
        invocation_id="call-123",
        status="error",
        error="Tool execution failed.",
    )


@pytest.mark.parametrize(
    "output",
    [
        object(),
        float("nan"),
        {
            1: "invalid key",
        },
    ],
)
def test_execute_returns_error_result_for_non_json_handler_output(
    output: object,
) -> None:
    """Convert non-JSON handler output into a safe error result."""

    registry = ToolRegistry()
    registry.register(
        create_calculator_definition(),
        lambda arguments: output,
    )

    result = registry.execute(
        ToolInvocation(
            id="call-123",
            tool_name="calculator",
            arguments={},
        )
    )

    assert result == ToolResult(
        invocation_id="call-123",
        status="error",
        error="Tool execution failed.",
    )


@pytest.mark.parametrize(
    "exception_type",
    [
        KeyboardInterrupt,
        SystemExit,
    ],
)
def test_execute_does_not_swallow_control_flow_exceptions(
    exception_type: type[BaseException],
) -> None:
    """Allow control-flow exceptions to propagate from handlers."""

    def interrupt(arguments: dict[str, object]) -> None:
        raise exception_type()

    registry = ToolRegistry()
    registry.register(
        create_calculator_definition(),
        interrupt,
    )

    with pytest.raises(exception_type):
        registry.execute(
            ToolInvocation(
                id="call-123",
                tool_name="calculator",
                arguments={},
            )
        )


def test_execute_does_not_mutate_shared_tool_data() -> None:
    """Keep definitions, invocation arguments, and handler output independent."""

    definition = create_calculator_definition()
    handler_output = {
        "values": [
            4,
        ]
    }

    def calculate(arguments: dict[str, object]) -> dict[str, object]:
        arguments["values"] = [
            99,
        ]
        return handler_output

    registry = ToolRegistry()
    registry.register(definition, calculate)
    invocation = ToolInvocation(
        id="call-123",
        tool_name="calculator",
        arguments={
            "values": [
                2,
                3,
            ],
        },
    )

    result = registry.execute(invocation)

    assert registry.definitions == (definition,)
    assert invocation.arguments == {
        "values": [
            2,
            3,
        ],
    }
    assert handler_output == {
        "values": [
            4,
        ]
    }
    assert result.output == handler_output
