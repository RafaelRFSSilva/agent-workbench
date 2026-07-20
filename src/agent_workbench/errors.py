"""Application-specific exceptions for Agent Workbench."""


class CompletionError(RuntimeError):
    """Represent a failure to obtain a model completion."""


class ConfigurationError(RuntimeError):
    """Represent an invalid application configuration."""
