"""Provider-independent synchronous tool registration and execution."""

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from agent_workbench.errors import (
    CompletionError,
    ConfigurationError,
    ToolArgumentError,
    WorkspacePathError,
)
from agent_workbench.tools import (
    JSONValue,
    JSONObject,
    ToolApprovalRequest,
    ToolDefinition,
    ToolInvocation,
    ToolResult,
)

type ToolHandler = Callable[[JSONObject], object]
type ToolApprovalPreview = Callable[[JSONObject], JSONValue]

_MAX_ARGUMENT_VALIDATION_ERROR_CHARACTERS = 800


@dataclass(frozen=True, slots=True)
class _ToolRegistration:
    """Store one private immutable tool registration."""

    definition: ToolDefinition
    handler: ToolHandler = field(repr=False)
    requires_approval: bool
    approval_preview: ToolApprovalPreview | None = field(repr=False)
    propagates_completion_errors: bool


@dataclass(slots=True)
class ToolRegistry:
    """Register provider-independent tools and execute their handlers."""

    _registrations: dict[str, _ToolRegistration] = field(
        default_factory=dict,
    )

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        """Return registered tool definitions in registration order."""

        return tuple(
            registration.definition for registration in self._registrations.values()
        )

    def register(
        self,
        definition: ToolDefinition,
        handler: ToolHandler,
        *,
        requires_approval: bool = False,
        approval_preview: ToolApprovalPreview | None = None,
        propagates_completion_errors: bool = False,
    ) -> None:
        """Register a synchronous handler for a tool definition."""

        if definition.name in self._registrations:
            raise ConfigurationError(f"Tool '{definition.name}' is already registered.")

        self._registrations[definition.name] = _ToolRegistration(
            definition=definition,
            handler=handler,
            requires_approval=requires_approval,
            approval_preview=approval_preview,
            propagates_completion_errors=propagates_completion_errors,
        )

    def requires_approval(self, invocation: ToolInvocation) -> bool:
        """Return whether a registered invocation requires caller approval."""

        registration = self._registrations.get(invocation.tool_name)
        return registration is not None and registration.requires_approval

    def argument_validation_error(self, invocation: ToolInvocation) -> str | None:
        """Return bounded schema guidance for one invalid registered invocation."""

        registration = self._registrations.get(invocation.tool_name)
        if registration is None:
            return None

        issue = _schema_validation_issue(
            invocation.arguments,
            registration.definition.input_schema,
        )
        if issue is None:
            return None

        shape = _describe_schema(registration.definition.input_schema)
        message = (
            f"Tool '{invocation.tool_name}' argument validation failed: {issue}. "
            f"Required structured shape: {shape}. Issue a corrected "
            f"{invocation.tool_name} tool call matching the advertised schema."
        )
        return message[:_MAX_ARGUMENT_VALIDATION_ERROR_CHARACTERS]

    def create_approval_request(
        self,
        invocation: ToolInvocation,
    ) -> ToolApprovalRequest:
        """Create a copy-safe approval request for a registered invocation."""

        registration = self._registrations.get(invocation.tool_name)

        if registration is None:
            raise ConfigurationError(f"Unknown tool '{invocation.tool_name}'.")

        validation_error = self.argument_validation_error(invocation)
        if validation_error is not None:
            raise CompletionError(validation_error)

        try:
            preview = (
                registration.approval_preview(invocation.arguments)
                if registration.approval_preview is not None
                else None
            )
        except (
            CompletionError,
            ConfigurationError,
            ValueError,
            WorkspacePathError,
        ) as exc:
            raise CompletionError(
                f"Approval preview failed for {invocation.tool_name}: {exc}"
            ) from None
        return ToolApprovalRequest(
            invocation=invocation,
            preview=preview,
        )

    def execute(self, invocation: ToolInvocation) -> ToolResult:
        """Execute a tool invocation and return a provider-independent result."""

        registration = self._registrations.get(invocation.tool_name)

        if registration is None:
            return ToolResult(
                invocation_id=invocation.id,
                status="error",
                error=f"Unknown tool '{invocation.tool_name}'.",
            )

        if registration.requires_approval:
            validation_error = self.argument_validation_error(invocation)
            if validation_error is not None:
                return ToolResult(
                    invocation_id=invocation.id,
                    status="error",
                    error=validation_error,
                )

        try:
            output = registration.handler(invocation.arguments)

            return ToolResult(
                invocation_id=invocation.id,
                status="success",
                output=output,
            )
        except ToolArgumentError as exc:
            return ToolResult(
                invocation_id=invocation.id,
                status="error",
                error=f"Invalid tool arguments: {exc}",
            )
        except WorkspacePathError:
            return ToolResult(
                invocation_id=invocation.id,
                status="error",
                error=(
                    "Invalid tool arguments: workspace path is unavailable "
                    "or outside the authorized workspace."
                ),
            )
        except CompletionError:
            if registration.propagates_completion_errors:
                raise
            return ToolResult(
                invocation_id=invocation.id,
                status="error",
                error="Tool execution failed.",
            )
        except Exception:
            return ToolResult(
                invocation_id=invocation.id,
                status="error",
                error="Tool execution failed.",
            )


def _schema_validation_issue(
    value: JSONValue,
    schema: JSONObject,
    *,
    path: str = "",
) -> str | None:
    """Return the first deterministic structural mismatch without input values."""

    expected_type = schema.get("type")
    if isinstance(expected_type, str) and not _matches_json_type(value, expected_type):
        location = path or "arguments"
        return f"{location} must be {_type_phrase(expected_type)}"

    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        location = path or "arguments"
        return f"{location} must use one of the advertised values"

    if isinstance(value, dict):
        properties = schema.get("properties")
        property_schemas = properties if isinstance(properties, dict) else {}
        required = schema.get("required")
        required_names = (
            [name for name in required if isinstance(name, str)]
            if isinstance(required, list)
            else []
        )
        missing = sorted(name for name in required_names if name not in value)
        if missing:
            return f"missing required fields: {', '.join(missing)}"

        if schema.get("additionalProperties") is False:
            unsupported = sorted(set(value) - set(property_schemas))
            if unsupported:
                return f"unsupported fields: {', '.join(unsupported)}"

        for name, property_schema in property_schemas.items():
            if name not in value or not isinstance(property_schema, dict):
                continue
            issue = _schema_validation_issue(
                value[name],
                property_schema,
                path=f"{path}.{name}" if path else name,
            )
            if issue is not None:
                return issue

    if isinstance(value, list):
        minimum_items = schema.get("minItems")
        maximum_items = schema.get("maxItems")
        location = path or "arguments"
        if (
            isinstance(minimum_items, int)
            and not isinstance(minimum_items, bool)
            and len(value) < minimum_items
        ):
            return f"{location} must contain at least {minimum_items} item(s)"
        if (
            isinstance(maximum_items, int)
            and not isinstance(maximum_items, bool)
            and len(value) > maximum_items
        ):
            return f"{location} must contain at most {maximum_items} item(s)"
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                issue = _schema_validation_issue(
                    item,
                    item_schema,
                    path=f"{location}[{index}]",
                )
                if issue is not None:
                    return issue

    if isinstance(value, str):
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            try:
                matches = re.search(pattern, value) is not None
            except re.error:
                matches = False
            if not matches:
                location = path or "arguments"
                return f"{location} must match its advertised pattern"

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        location = path or "arguments"
        if isinstance(minimum, (int, float)) and value < minimum:
            return f"{location} must be at least {minimum}"
        if isinstance(maximum, (int, float)) and value > maximum:
            return f"{location} must be at most {maximum}"

    return None


def _matches_json_type(value: JSONValue, expected_type: str) -> bool:
    """Match strict JSON types without treating booleans as integers."""

    match expected_type:
        case "object":
            return isinstance(value, dict)
        case "array":
            return isinstance(value, list)
        case "string":
            return isinstance(value, str)
        case "boolean":
            return isinstance(value, bool)
        case "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        case "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        case "null":
            return value is None
        case _:
            return True


def _type_phrase(expected_type: str) -> str:
    """Return one concise human-readable JSON type phrase."""

    phrases = {
        "object": "an object",
        "array": "an array",
        "string": "a string",
        "boolean": "a boolean",
        "integer": "an integer",
        "number": "a number",
        "null": "null",
    }
    return phrases.get(expected_type, f"a value of type {expected_type}")


def _describe_schema(schema: JSONObject) -> str:
    """Render one bounded deterministic structural summary from a tool schema."""

    schema_type = schema.get("type")
    if schema_type == "object":
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return "{}"
        required = schema.get("required")
        required_names = (
            {name for name in required if isinstance(name, str)}
            if isinstance(required, list)
            else set()
        )
        required_order = (
            [name for name in required if isinstance(name, str)]
            if isinstance(required, list)
            else []
        )
        ordered_names = [
            *(name for name in required_order if name in properties),
            *(name for name in sorted(properties) if name not in required_names),
        ]
        fields = []
        for name in ordered_names:
            property_schema = properties[name]
            if not isinstance(property_schema, dict):
                continue
            marker = "" if name in required_names else "?"
            fields.append(f"{name}{marker}: {_describe_schema(property_schema)}")
        return "{" + ", ".join(fields) + "}"
    if schema_type == "array":
        items = schema.get("items")
        item_shape = _describe_schema(items) if isinstance(items, dict) else "value"
        return f"array<{item_shape}>"
    if isinstance(schema_type, str):
        return schema_type
    return "JSON value"
