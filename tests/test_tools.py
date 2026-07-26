"""Tests for provider-independent tool calling models."""

from typing import cast

import pytest

from agent_workbench.errors import ConfigurationError
from agent_workbench.tools import (
    ToolDefinition,
    ToolInvocation,
    ToolResult,
)


def create_valid_input_schema() -> dict[str, object]:
    """Create a valid JSON object schema for tool arguments."""

    return {
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
    }


def test_tool_definition_preserves_valid_configuration() -> None:
    """Preserve a valid provider-independent tool definition."""

    schema = create_valid_input_schema()

    definition = ToolDefinition(
        name="calculator",
        description="Evaluate a mathematical expression.",
        input_schema=schema,
    )

    assert definition.name == "calculator"
    assert definition.description == "Evaluate a mathematical expression."
    assert definition.input_schema == schema


def test_tool_definition_normalizes_text_fields() -> None:
    """Remove surrounding whitespace from tool names and descriptions."""

    definition = ToolDefinition(
        name="  calculator  ",
        description="  Evaluate a mathematical expression.  ",
        input_schema=create_valid_input_schema(),
    )

    assert definition.name == "calculator"
    assert definition.description == "Evaluate a mathematical expression."


def test_equivalent_tool_schemas_produce_equal_definitions() -> None:
    """Compare tool schemas independently of dictionary key order."""

    first_definition = ToolDefinition(
        name="calculator",
        description="Evaluate an expression.",
        input_schema={
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                }
            },
        },
    )
    second_definition = ToolDefinition(
        name="calculator",
        description="Evaluate an expression.",
        input_schema={
            "properties": {
                "expression": {
                    "type": "string",
                }
            },
            "type": "object",
        },
    )

    assert first_definition == second_definition


def test_tool_definition_defensively_copies_input_schema() -> None:
    """Prevent external mutation of a stored tool input schema."""

    schema = create_valid_input_schema()
    definition = ToolDefinition(
        name="calculator",
        description="Evaluate an expression.",
        input_schema=schema,
    )

    schema["type"] = "array"

    returned_schema = definition.input_schema
    returned_schema["type"] = "string"

    assert definition.input_schema["type"] == "object"


@pytest.mark.parametrize(
    "name",
    [
        "",
        "   ",
        "calculate value",
        "calculate.value",
        "x" * 65,
    ],
)
def test_tool_definition_rejects_invalid_name(name: str) -> None:
    """Reject tool names outside the portable provider-independent format."""

    with pytest.raises(
        ConfigurationError,
        match="tool name must contain",
    ):
        ToolDefinition(
            name=name,
            description="Evaluate an expression.",
            input_schema=create_valid_input_schema(),
        )


@pytest.mark.parametrize(
    "description",
    [
        "",
        "   ",
    ],
)
def test_tool_definition_rejects_blank_description(description: str) -> None:
    """Require a non-empty tool description."""

    with pytest.raises(
        ConfigurationError,
        match="tool description must not be blank",
    ):
        ToolDefinition(
            name="calculator",
            description=description,
            input_schema=create_valid_input_schema(),
        )


@pytest.mark.parametrize(
    "schema",
    [
        {},
        {
            "type": "array",
        },
        {
            "properties": {},
        },
    ],
)
def test_tool_definition_rejects_invalid_root_schema(
    schema: object,
) -> None:
    """Require a non-empty top-level object schema."""

    with pytest.raises(ConfigurationError):
        ToolDefinition(
            name="calculator",
            description="Evaluate an expression.",
            input_schema=cast(dict[str, object], schema),
        )


def test_tool_definition_rejects_non_string_schema_keys() -> None:
    """Reject tool schema keys that are not JSON strings."""

    schema = cast(
        dict[str, object],
        {
            "type": "object",
            1: {
                "type": "string",
            },
        },
    )

    with pytest.raises(
        ConfigurationError,
        match="object keys must be strings",
    ):
        ToolDefinition(
            name="calculator",
            description="Evaluate an expression.",
            input_schema=schema,
        )


def test_tool_definition_rejects_non_json_schema_values() -> None:
    """Reject tool schemas containing values unsupported by JSON."""

    schema = cast(
        dict[str, object],
        {
            "type": "object",
            "invalid": object(),
        },
    )

    with pytest.raises(
        ConfigurationError,
        match="JSON-compatible values",
    ):
        ToolDefinition(
            name="calculator",
            description="Evaluate an expression.",
            input_schema=schema,
        )


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
    ],
)
def test_tool_definition_rejects_non_finite_schema_numbers(
    value: float,
) -> None:
    """Reject non-finite numbers from tool schemas."""

    schema = cast(
        dict[str, object],
        {
            "type": "object",
            "invalid": value,
        },
    )

    with pytest.raises(
        ConfigurationError,
        match="finite JSON numbers",
    ):
        ToolDefinition(
            name="calculator",
            description="Evaluate an expression.",
            input_schema=schema,
        )


def test_tool_invocation_preserves_valid_data() -> None:
    """Preserve a valid provider-independent tool invocation."""

    invocation = ToolInvocation(
        id="call-123",
        tool_name="calculator",
        arguments={
            "expression": "2 + 2",
        },
    )

    assert invocation.id == "call-123"
    assert invocation.tool_name == "calculator"
    assert invocation.arguments == {
        "expression": "2 + 2",
    }


def test_tool_invocation_normalizes_identifiers() -> None:
    """Remove surrounding whitespace from invocation identifiers."""

    invocation = ToolInvocation(
        id="  call-123  ",
        tool_name="  calculator  ",
        arguments={},
    )

    assert invocation.id == "call-123"
    assert invocation.tool_name == "calculator"


def test_equivalent_arguments_produce_equal_invocations() -> None:
    """Compare arguments independently of dictionary key order."""

    first_invocation = ToolInvocation(
        id="call-123",
        tool_name="calculator",
        arguments={
            "left": 2,
            "right": 3,
        },
    )
    second_invocation = ToolInvocation(
        id="call-123",
        tool_name="calculator",
        arguments={
            "right": 3,
            "left": 2,
        },
    )

    assert first_invocation == second_invocation


def test_tool_invocation_defensively_copies_arguments() -> None:
    """Prevent external mutation of stored tool arguments."""

    arguments = {
        "values": [
            2,
            3,
        ]
    }
    invocation = ToolInvocation(
        id="call-123",
        tool_name="calculator",
        arguments=arguments,
    )

    arguments["values"] = []

    returned_arguments = invocation.arguments
    returned_arguments["values"] = [
        99,
    ]

    assert invocation.arguments == {
        "values": [
            2,
            3,
        ]
    }


@pytest.mark.parametrize(
    "invocation_id",
    [
        "",
        "   ",
    ],
)
def test_tool_invocation_rejects_blank_id(invocation_id: str) -> None:
    """Require a non-empty provider invocation identifier."""

    with pytest.raises(
        ConfigurationError,
        match="tool invocation id must not be blank",
    ):
        ToolInvocation(
            id=invocation_id,
            tool_name="calculator",
            arguments={},
        )


def test_tool_invocation_rejects_invalid_tool_name() -> None:
    """Apply portable tool name validation to invocations."""

    with pytest.raises(
        ConfigurationError,
        match="tool name must contain",
    ):
        ToolInvocation(
            id="call-123",
            tool_name="calculate value",
            arguments={},
        )


def test_tool_invocation_rejects_non_object_arguments() -> None:
    """Require invocation arguments to use a JSON object."""

    with pytest.raises(
        ConfigurationError,
        match="tool arguments must be a JSON object",
    ):
        ToolInvocation(
            id="call-123",
            tool_name="calculator",
            arguments=cast(dict[str, object], ["2 + 2"]),
        )


@pytest.mark.parametrize(
    "arguments",
    [
        {
            1: "invalid key",
        },
        {
            "invalid": object(),
        },
        {
            "invalid": float("nan"),
        },
    ],
)
def test_tool_invocation_rejects_invalid_json_arguments(
    arguments: object,
) -> None:
    """Reject invocation arguments that are not strict JSON."""

    with pytest.raises(ConfigurationError):
        ToolInvocation(
            id="call-123",
            tool_name="calculator",
            arguments=cast(dict[str, object], arguments),
        )


def test_successful_tool_result_preserves_output() -> None:
    """Preserve a successful tool result with JSON output."""

    result = ToolResult(
        invocation_id="call-123",
        status="success",
        output={
            "value": 4,
        },
    )

    assert result.invocation_id == "call-123"
    assert result.status == "success"
    assert result.output == {
        "value": 4,
    }
    assert result.error is None


def test_successful_tool_result_defensively_copies_output() -> None:
    """Prevent mutation of structured tool output."""

    output = {
        "values": [
            2,
            3,
        ]
    }
    result = ToolResult(
        invocation_id="call-123",
        status="success",
        output=output,
    )

    output["values"] = []

    returned_output = cast(dict[str, object], result.output)
    returned_output["values"] = [
        99,
    ]

    assert result.output == {
        "values": [
            2,
            3,
        ]
    }


def test_error_tool_result_preserves_normalized_error() -> None:
    """Preserve a failed tool result with a normalized error message."""

    result = ToolResult(
        invocation_id="  call-123  ",
        status="error",
        error="  Division by zero.  ",
    )

    assert result.invocation_id == "call-123"
    assert result.status == "error"
    assert result.output is None
    assert result.error == "Division by zero."


@pytest.mark.parametrize(
    "invocation_id",
    [
        "",
        "   ",
    ],
)
def test_tool_result_rejects_blank_invocation_id(
    invocation_id: str,
) -> None:
    """Require a non-empty invocation identifier on results."""

    with pytest.raises(
        ConfigurationError,
        match="tool invocation id must not be blank",
    ):
        ToolResult(
            invocation_id=invocation_id,
            status="success",
        )


def test_tool_result_rejects_invalid_status() -> None:
    """Allow only success and error result states."""

    with pytest.raises(
        ConfigurationError,
        match="tool result status must be 'success' or 'error'",
    ):
        ToolResult(
            invocation_id="call-123",
            status=cast(object, "pending"),
        )


@pytest.mark.parametrize(
    "error",
    [
        "",
        "   ",
    ],
)
def test_error_tool_result_requires_non_empty_error(error: str) -> None:
    """Require failed tool results to contain an error message."""

    with pytest.raises(
        ConfigurationError,
        match="failed tool result must contain a non-empty error",
    ):
        ToolResult(
            invocation_id="call-123",
            status="error",
            error=error,
        )


def test_successful_tool_result_rejects_error() -> None:
    """Prevent successful tool results from containing an error."""

    with pytest.raises(
        ConfigurationError,
        match="successful tool result must not contain an error",
    ):
        ToolResult(
            invocation_id="call-123",
            status="success",
            error="Unexpected error.",
        )


def test_error_tool_result_rejects_output() -> None:
    """Prevent failed tool results from containing normal output."""

    with pytest.raises(
        ConfigurationError,
        match="failed tool result must not contain output",
    ):
        ToolResult(
            invocation_id="call-123",
            status="error",
            output={
                "partial": True,
            },
            error="Execution failed.",
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
def test_tool_result_rejects_invalid_json_output(output: object) -> None:
    """Reject successful output that cannot be represented as strict JSON."""

    with pytest.raises(ConfigurationError):
        ToolResult(
            invocation_id="call-123",
            status="success",
            output=output,
        )
