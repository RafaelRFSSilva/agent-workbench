"""Provider-independent synchronous tool registration and execution."""

from collections.abc import Callable
from dataclasses import dataclass, field

from agent_workbench.errors import CompletionError, ConfigurationError
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

    def create_approval_request(
        self,
        invocation: ToolInvocation,
    ) -> ToolApprovalRequest:
        """Create a copy-safe approval request for a registered invocation."""

        registration = self._registrations.get(invocation.tool_name)

        if registration is None:
            raise ConfigurationError(f"Unknown tool '{invocation.tool_name}'.")

        preview = (
            registration.approval_preview(invocation.arguments)
            if registration.approval_preview is not None
            else None
        )
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

        try:
            output = registration.handler(invocation.arguments)

            return ToolResult(
                invocation_id=invocation.id,
                status="success",
                output=output,
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
