"""Tests for provider-independent built-in tools."""

import pytest

from agent_workbench.built_in_tools import (
    MAX_AST_NODES,
    MAX_EXPRESSION_LENGTH,
    create_built_in_tool_registry,
    evaluate_calculator,
)


def test_built_in_registry_exposes_only_the_calculator_definition() -> None:
    """Register exactly the documented calculator tool."""

    registry = create_built_in_tool_registry()

    assert len(registry.definitions) == 1
    assert registry.definitions[0].name == "calculator"
    assert (
        registry.definitions[0].description == "Evaluate a basic arithmetic expression."
    )
    assert registry.definitions[0].input_schema == {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
            }
        },
        "required": ["expression"],
        "additionalProperties": False,
    }


def test_calculator_evaluates_integer_arithmetic() -> None:
    """Evaluate the supported integer binary operators."""

    assert evaluate_calculator({"expression": "17 // 3 + 17 % 3"}) == {
        "expression": "17 // 3 + 17 % 3",
        "result": 7,
    }


def test_calculator_evaluates_finite_floating_point_arithmetic() -> None:
    """Evaluate finite floating-point literals and division."""

    assert evaluate_calculator({"expression": "5.5 / 2"}) == {
        "expression": "5.5 / 2",
        "result": 2.75,
    }


def test_calculator_supports_parentheses_and_unary_operators() -> None:
    """Evaluate the supported grouping and unary operators."""

    assert evaluate_calculator({"expression": "-(2 + 3) * +4"}) == {
        "expression": "-(2 + 3) * +4",
        "result": -20,
    }


@pytest.mark.parametrize(
    "expression",
    [
        "",
        "1 +",
        "value + 1",
        "abs(1)",
        "value.attribute",
        "[1, 2]",
        "1 < 2",
        "True",
        "1 & 2",
        "1 ** 2",
    ],
)
def test_calculator_rejects_invalid_or_unsupported_syntax(
    expression: str,
) -> None:
    """Reject syntax outside the restricted arithmetic grammar."""

    with pytest.raises(ValueError):
        evaluate_calculator({"expression": expression})


@pytest.mark.parametrize(
    "expression",
    [
        "1 / 0",
        "1 % 0",
    ],
)
def test_calculator_rejects_division_or_modulo_by_zero(
    expression: str,
) -> None:
    """Reject arithmetic with a zero divisor."""

    with pytest.raises(ValueError, match="division or modulo by zero"):
        evaluate_calculator({"expression": expression})


def test_calculator_rejects_expression_length_limit() -> None:
    """Reject expressions larger than the documented length limit."""

    with pytest.raises(ValueError, match="expression is too long"):
        evaluate_calculator({"expression": "1" * (MAX_EXPRESSION_LENGTH + 1)})


def test_calculator_rejects_ast_complexity_limit() -> None:
    """Reject expressions with more than the documented AST node limit."""

    expression = "+".join(["1"] * MAX_AST_NODES)

    with pytest.raises(ValueError, match="expression is too complex"):
        evaluate_calculator({"expression": expression})


@pytest.mark.parametrize(
    "expression",
    [
        "1e309",
        "1e308 * 1e308",
    ],
)
def test_calculator_rejects_non_finite_literals_and_results(
    expression: str,
) -> None:
    """Reject non-finite numeric literals and calculation results."""

    with pytest.raises(ValueError, match="finite"):
        evaluate_calculator({"expression": expression})


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"expression": 1},
        {"expression": "1 + 1", "unexpected": True},
    ],
)
def test_calculator_rejects_invalid_input_shape(arguments: object) -> None:
    """Require exactly one string expression argument."""

    with pytest.raises(ValueError, match="expression string"):
        evaluate_calculator(arguments)
