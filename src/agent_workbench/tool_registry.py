"""Provider-independent synchronous tool registration and execution."""

from collections.abc import Callable
from dataclasses import dataclass, field

from agent_workbench.errors import ConfigurationError
from agent_workbench.tools import (
    JSONObject,
    ToolDefinition,
    ToolInvocation,
    ToolResult,
)

type ToolHandler = Callable[[JSONObject], object]


@dataclass(slots=True)
class ToolRegistry:
    """Register provider-independent tools and execute their handlers."""

    _registrations: dict[str, tuple[ToolDefinition, ToolHandler]] = field(
        default_factory=dict,
    )

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        """Return registered tool definitions in registration order."""

        return tuple(definition for definition, _ in self._registrations.values())

    def register(
        self,
        definition: ToolDefinition,
        handler: ToolHandler,
    ) -> None:
        """Register a synchronous handler for a tool definition."""

        if definition.name in self._registrations:
            raise ConfigurationError(f"Tool '{definition.name}' is already registered.")

        self._registrations[definition.name] = (
            definition,
            handler,
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

        _, handler = registration

        try:
            output = handler(invocation.arguments)

            return ToolResult(
                invocation_id=invocation.id,
                status="success",
                output=output,
            )
        except Exception:
            return ToolResult(
                invocation_id=invocation.id,
                status="error",
                error="Tool execution failed.",
            )
