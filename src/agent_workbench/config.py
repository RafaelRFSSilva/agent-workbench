"""Configuration helpers for Agent Workbench."""

import os
from typing import Literal, cast

from agent_workbench.errors import ConfigurationError

ProviderName = Literal["ollama", "openai"]

DEFAULT_PROVIDER_NAME: ProviderName = "ollama"
DEFAULT_MODEL_NAME = "gpt-oss:20b"

PROVIDER_ENV_VAR = "AGENT_WORKBENCH_PROVIDER"
MODEL_ENV_VAR = "AGENT_WORKBENCH_MODEL"

SUPPORTED_PROVIDERS = {"ollama", "openai"}


def get_provider_name() -> ProviderName:
    """Return the configured provider name."""

    configured_provider = (
        os.getenv(
            PROVIDER_ENV_VAR,
            DEFAULT_PROVIDER_NAME,
        )
        .strip()
        .lower()
    )

    if configured_provider not in SUPPORTED_PROVIDERS:
        supported = ", ".join(sorted(SUPPORTED_PROVIDERS))
        raise ConfigurationError(
            f"Unsupported provider '{configured_provider}'. "
            f"Supported providers: {supported}."
        )

    return cast(ProviderName, configured_provider)


def get_model_name(
    provider_name: ProviderName = DEFAULT_PROVIDER_NAME,
) -> str:
    """Return the configured model name for the selected provider."""

    configured_model = os.getenv(MODEL_ENV_VAR, "").strip()

    if configured_model:
        return configured_model

    if provider_name == "ollama":
        return DEFAULT_MODEL_NAME

    raise ConfigurationError(
        f"{MODEL_ENV_VAR} is required when {PROVIDER_ENV_VAR}={provider_name}."
    )
