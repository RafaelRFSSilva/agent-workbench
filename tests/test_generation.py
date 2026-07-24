"""Tests for provider-independent generation configuration."""

import pytest

from agent_workbench.errors import ConfigurationError
from agent_workbench.generation import GenerationConfig


def test_generation_config_uses_provider_defaults() -> None:
    """Leave all generation parameters unset by default."""

    configuration = GenerationConfig()

    assert configuration.temperature is None
    assert configuration.top_p is None
    assert configuration.max_output_tokens is None


@pytest.mark.parametrize(
    ("temperature", "top_p"),
    [
        (0.0, 0.0),
        (0.5, 0.5),
        (1.0, 1.0),
    ],
)
def test_generation_config_accepts_unit_interval_values(
    temperature,
    top_p,
) -> None:
    """Accept temperature and top-p values from zero to one."""

    configuration = GenerationConfig(
        temperature=temperature,
        top_p=top_p,
    )

    assert configuration.temperature == temperature
    assert configuration.top_p == top_p


@pytest.mark.parametrize(
    "temperature",
    [
        -0.01,
        1.01,
        True,
        "0.5",
    ],
)
def test_generation_config_rejects_invalid_temperature(
    temperature,
) -> None:
    """Reject temperature values outside the portable range."""

    with pytest.raises(
        ConfigurationError,
        match="temperature must be a number between 0.0 and 1.0",
    ):
        GenerationConfig(temperature=temperature)


@pytest.mark.parametrize(
    "top_p",
    [
        -0.01,
        1.01,
        False,
        "0.9",
    ],
)
def test_generation_config_rejects_invalid_top_p(
    top_p,
) -> None:
    """Reject top-p values outside the portable range."""

    with pytest.raises(
        ConfigurationError,
        match="top_p must be a number between 0.0 and 1.0",
    ):
        GenerationConfig(top_p=top_p)


@pytest.mark.parametrize(
    "max_output_tokens",
    [
        1,
        256,
        4096,
    ],
)
def test_generation_config_accepts_positive_output_token_limits(
    max_output_tokens,
) -> None:
    """Accept positive maximum output token limits."""

    configuration = GenerationConfig(
        max_output_tokens=max_output_tokens,
    )

    assert configuration.max_output_tokens == max_output_tokens


@pytest.mark.parametrize(
    "max_output_tokens",
    [
        0,
        -1,
        1.5,
        True,
        "256",
    ],
)
def test_generation_config_rejects_invalid_output_token_limits(
    max_output_tokens,
) -> None:
    """Reject non-positive and non-integer token limits."""

    with pytest.raises(
        ConfigurationError,
        match="max_output_tokens must be a positive integer",
    ):
        GenerationConfig(
            max_output_tokens=max_output_tokens,
        )
