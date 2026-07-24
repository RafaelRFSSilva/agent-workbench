"""Provider-independent text generation configuration."""

from dataclasses import dataclass

from agent_workbench.errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class GenerationConfig:
    """Represent optional provider-independent generation parameters."""

    temperature: float | None = None
    top_p: float | None = None
    max_output_tokens: int | None = None

    def __post_init__(self) -> None:
        """Validate configured generation parameters."""

        _validate_unit_interval(
            "temperature",
            self.temperature,
        )
        _validate_unit_interval(
            "top_p",
            self.top_p,
        )

        if self.max_output_tokens is not None:
            if (
                isinstance(self.max_output_tokens, bool)
                or not isinstance(self.max_output_tokens, int)
                or self.max_output_tokens <= 0
            ):
                raise ConfigurationError(
                    "max_output_tokens must be a positive integer."
                )


def _validate_unit_interval(
    name: str,
    value: float | None,
) -> None:
    """Validate an optional numeric value between zero and one."""

    if value is None:
        return

    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0.0 <= value <= 1.0
    ):
        raise ConfigurationError(f"{name} must be a number between 0.0 and 1.0.")
