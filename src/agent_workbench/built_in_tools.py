"""Safe provider-independent built-in tools."""

import ast
import math

from agent_workbench.tool_registry import ToolRegistry
from agent_workbench.tools import JSONObject, ToolDefinition

MAX_EXPRESSION_LENGTH = 256
"""Maximum number of characters accepted by the calculator."""

MAX_AST_NODES = 64
"""Maximum AST nodes accepted by the calculator."""

CALCULATOR_DEFINITION = ToolDefinition(
    name="calculator",
    description="Evaluate a basic arithmetic expression.",
    input_schema={
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
            }
        },
        "required": ["expression"],
        "additionalProperties": False,
    },
)


def create_built_in_tool_registry() -> ToolRegistry:
    """Create a registry containing the available built-in tools."""

    registry = ToolRegistry()
    registry.register(CALCULATOR_DEFINITION, evaluate_calculator)

    return registry


def evaluate_calculator(arguments: object) -> JSONObject:
    """Evaluate a restricted arithmetic expression."""

    expression = _get_expression(arguments)
    normalized_expression = expression.strip()

    if not normalized_expression:
        raise ValueError("calculator requires an expression string.")

    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise ValueError("calculator expression is too long.")

    try:
        parsed_expression = ast.parse(normalized_expression, mode="eval")
    except (SyntaxError, ValueError):
        raise ValueError("calculator expression is invalid.") from None

    if sum(1 for _ in ast.walk(parsed_expression)) > MAX_AST_NODES:
        raise ValueError("calculator expression is too complex.")

    result = _evaluate_node(parsed_expression.body)

    return {
        "expression": expression,
        "result": result,
    }


def _get_expression(arguments: object) -> str:
    """Extract the one required calculator argument."""

    if not isinstance(arguments, dict) or set(arguments) != {"expression"}:
        raise ValueError("calculator requires an expression string.")

    expression = arguments["expression"]

    if not isinstance(expression, str):
        raise ValueError("calculator requires an expression string.")

    return expression


def _evaluate_node(node: ast.expr) -> int | float:
    """Evaluate one explicitly supported expression node."""

    if isinstance(node, ast.Constant):
        return _validate_number_literal(node.value)

    if isinstance(node, ast.UnaryOp):
        operand = _evaluate_node(node.operand)

        if isinstance(node.op, ast.UAdd):
            return _validate_result(+operand)

        if isinstance(node.op, ast.USub):
            return _validate_result(-operand)

        raise ValueError("calculator supports only basic arithmetic expressions.")

    if isinstance(node, ast.BinOp):
        left = _evaluate_node(node.left)
        right = _evaluate_node(node.right)

        try:
            if isinstance(node.op, ast.Add):
                return _validate_result(left + right)

            if isinstance(node.op, ast.Sub):
                return _validate_result(left - right)

            if isinstance(node.op, ast.Mult):
                return _validate_result(left * right)

            if isinstance(node.op, ast.Div):
                return _validate_result(left / right)

            if isinstance(node.op, ast.FloorDiv):
                return _validate_result(left // right)

            if isinstance(node.op, ast.Mod):
                return _validate_result(left % right)
        except ZeroDivisionError:
            raise ValueError(
                "calculator division or modulo by zero is not allowed."
            ) from None
        except OverflowError:
            raise ValueError("calculator result must be finite.") from None

    raise ValueError("calculator supports only basic arithmetic expressions.")


def _validate_number_literal(value: object) -> int | float:
    """Accept finite numeric literals and reject all other constants."""

    if type(value) is int:
        return value

    if type(value) is float:
        return _validate_result(value)

    raise ValueError("calculator supports only finite numeric literals.")


def _validate_result(value: int | float) -> int | float:
    """Reject non-finite floating-point calculation results."""

    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("calculator result must be finite.")

    return value
