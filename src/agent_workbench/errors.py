"""Application-specific exceptions for Agent Workbench."""


class CompletionError(RuntimeError):
    """Represent a failure to obtain a model completion."""


class ConfigurationError(RuntimeError):
    """Represent an invalid application configuration."""


class SessionStateError(RuntimeError):
    """Represent an invalid operation for the current session state."""


class WorkspacePathError(RuntimeError):
    """Represent an unsafe or invalid requested workspace path."""


class ToolArgumentError(ValueError):
    """Represent a safe, model-visible, correctable tool input failure."""


class WorkspaceTransactionError(CompletionError):
    """Represent a safe transactional workspace application failure."""
