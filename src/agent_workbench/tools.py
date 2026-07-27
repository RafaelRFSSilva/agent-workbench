"""Provider-independent tool calling models."""

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, cast

from agent_workbench.errors import ConfigurationError

type JSONValue = (
    None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]
)
type JSONObject = dict[str, JSONValue]
type ToolResultStatus = Literal["success", "error"]

_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True, slots=True, init=False)
class ToolDefinition:
    """Represent a provider-independent tool definition."""

    name: str
    description: str
    _input_schema_json: str = field(repr=False)

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: JSONObject,
    ) -> None:
        """Validate and store an immutable tool definition."""

        normalized_name = _normalize_tool_name(name)
        normalized_description = _normalize_description(description)

        if not isinstance(input_schema, dict) or not input_schema:
            raise ConfigurationError(
                "tool input schema must be a non-empty JSON object."
            )

        if input_schema.get("type") != "object":
            raise ConfigurationError(
                "tool input schema must use 'object' as its top-level type."
            )

        input_schema_json = _serialize_json_value(
            input_schema,
            context="tool input schema",
        )

        object.__setattr__(self, "name", normalized_name)
        object.__setattr__(self, "description", normalized_description)
        object.__setattr__(self, "_input_schema_json", input_schema_json)

    @property
    def input_schema(self) -> JSONObject:
        """Return an independent copy of the tool input schema."""

        return cast(
            JSONObject,
            json.loads(self._input_schema_json),
        )


@dataclass(frozen=True, slots=True, init=False)
class ToolInvocation:
    """Represent a provider-independent request to execute a tool."""

    id: str
    tool_name: str
    _arguments_json: str = field(repr=False)

    def __init__(
        self,
        id: str,
        tool_name: str,
        arguments: JSONObject,
    ) -> None:
        """Validate and store an immutable tool invocation."""

        normalized_id = _normalize_invocation_id(id)
        normalized_tool_name = _normalize_tool_name(tool_name)

        if not isinstance(arguments, dict):
            raise ConfigurationError("tool arguments must be a JSON object.")

        arguments_json = _serialize_json_value(
            arguments,
            context="tool arguments",
        )

        object.__setattr__(self, "id", normalized_id)
        object.__setattr__(self, "tool_name", normalized_tool_name)
        object.__setattr__(self, "_arguments_json", arguments_json)

    @property
    def arguments(self) -> JSONObject:
        """Return an independent copy of the tool arguments."""

        return cast(
            JSONObject,
            json.loads(self._arguments_json),
        )


class ToolApprovalDecision(StrEnum):
    """Represent an explicit caller-owned action approval decision."""

    APPROVE = "approve"
    DENY = "deny"


@dataclass(frozen=True, slots=True, init=False)
class ToolApprovalRequest:
    """Represent one immutable provider-independent approval request."""

    invocation: ToolInvocation
    _preview_json: str = field(repr=False)

    def __init__(
        self,
        invocation: ToolInvocation,
        preview: JSONValue = None,
    ) -> None:
        """Store an invocation and an immutable strict-JSON preview snapshot."""

        preview_json = _serialize_json_value(
            preview,
            context="tool approval preview",
        )

        object.__setattr__(self, "invocation", invocation)
        object.__setattr__(self, "_preview_json", preview_json)

    @property
    def preview(self) -> JSONValue:
        """Return an independent copy of the approval preview."""

        return cast(JSONValue, json.loads(self._preview_json))


type ToolApprovalHandler = Callable[
    [ToolApprovalRequest],
    ToolApprovalDecision,
]


@dataclass(frozen=True, slots=True, init=False)
class ToolResult:
    """Represent the provider-independent result of a tool invocation."""

    invocation_id: str
    status: ToolResultStatus
    _output_json: str | None = field(
        default=None,
        repr=False,
    )
    error: str | None = None

    def __init__(
        self,
        invocation_id: str,
        status: ToolResultStatus,
        output: JSONValue = None,
        error: str | None = None,
    ) -> None:
        """Validate and store an immutable tool execution result."""

        normalized_invocation_id = _normalize_invocation_id(invocation_id)

        if status not in ("success", "error"):
            raise ConfigurationError("tool result status must be 'success' or 'error'.")

        output_json: str | None = None
        normalized_error: str | None = None

        if status == "success":
            if error is not None:
                raise ConfigurationError(
                    "successful tool result must not contain an error."
                )

            output_json = _serialize_json_value(
                output,
                context="tool result output",
            )
        else:
            if output is not None:
                raise ConfigurationError("failed tool result must not contain output.")

            if not isinstance(error, str) or not error.strip():
                raise ConfigurationError(
                    "failed tool result must contain a non-empty error."
                )

            normalized_error = error.strip()

        object.__setattr__(
            self,
            "invocation_id",
            normalized_invocation_id,
        )
        object.__setattr__(
            self,
            "status",
            cast(ToolResultStatus, status),
        )
        object.__setattr__(
            self,
            "_output_json",
            output_json,
        )
        object.__setattr__(
            self,
            "error",
            normalized_error,
        )

    @property
    def output(self) -> JSONValue:
        """Return an independent copy of the successful tool output."""

        if self._output_json is None:
            return None

        return cast(
            JSONValue,
            json.loads(self._output_json),
        )


def _normalize_tool_name(name: object) -> str:
    """Validate and normalize a portable tool name."""

    if not isinstance(name, str):
        raise ConfigurationError("tool name must be a string.")

    normalized_name = name.strip()

    if not _TOOL_NAME_PATTERN.fullmatch(normalized_name):
        raise ConfigurationError(
            "tool name must contain 1 to 64 letters, numbers, underscores, or hyphens."
        )

    return normalized_name


def _normalize_description(description: object) -> str:
    """Validate and normalize a tool description."""

    if not isinstance(description, str):
        raise ConfigurationError("tool description must be a string.")

    normalized_description = description.strip()

    if not normalized_description:
        raise ConfigurationError("tool description must not be blank.")

    return normalized_description


def _normalize_invocation_id(invocation_id: object) -> str:
    """Validate and normalize a provider invocation identifier."""

    if not isinstance(invocation_id, str):
        raise ConfigurationError("tool invocation id must be a string.")

    normalized_invocation_id = invocation_id.strip()

    if not normalized_invocation_id:
        raise ConfigurationError("tool invocation id must not be blank.")

    return normalized_invocation_id


def _serialize_json_value(
    value: object,
    *,
    context: str,
) -> str:
    """Validate and serialize a strict JSON-compatible value."""

    _validate_json_value(
        value,
        context=context,
    )

    return json.dumps(
        value,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _validate_json_value(
    value: object,
    *,
    context: str,
) -> None:
    """Reject values that cannot be represented safely as strict JSON."""

    if value is None or isinstance(
        value,
        (bool, int, str),
    ):
        return

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ConfigurationError(
                f"{context} must contain only finite JSON numbers."
            )

        return

    if isinstance(value, list):
        for item in value:
            _validate_json_value(
                item,
                context=context,
            )

        return

    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ConfigurationError(f"{context} object keys must be strings.")

            _validate_json_value(
                item,
                context=context,
            )

        return

    raise ConfigurationError(f"{context} must contain only JSON-compatible values.")
