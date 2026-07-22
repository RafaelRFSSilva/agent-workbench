"""Provider construction for Agent Workbench."""

import os

from anthropic import Anthropic
from openai import OpenAI

from agent_workbench.config import ProviderName
from agent_workbench.errors import ConfigurationError
from agent_workbench.providers.anthropic import AnthropicProvider
from agent_workbench.providers.base import ChatProvider
from agent_workbench.providers.ollama import OllamaProvider
from agent_workbench.providers.openai import OpenAIProvider

ANTHROPIC_API_KEY_ENV_VAR = "ANTHROPIC_API_KEY"
OPENAI_API_KEY_ENV_VAR = "OPENAI_API_KEY"


def create_provider(
    provider_name: ProviderName,
    model_name: str,
) -> ChatProvider:
    """Create the provider selected through runtime configuration."""

    if provider_name == "ollama":
        return OllamaProvider(model_name=model_name)

    if provider_name == "openai":
        api_key = os.getenv(OPENAI_API_KEY_ENV_VAR, "").strip()

        if not api_key:
            raise ConfigurationError(
                f"{OPENAI_API_KEY_ENV_VAR} is required when using OpenAI."
            )

        return OpenAIProvider(
            model_name=model_name,
            client=OpenAI(),
        )

    if provider_name == "anthropic":
        api_key = os.getenv(ANTHROPIC_API_KEY_ENV_VAR, "").strip()

        if not api_key:
            raise ConfigurationError(
                f"{ANTHROPIC_API_KEY_ENV_VAR} is required when using Anthropic."
            )

        return AnthropicProvider(
            model_name=model_name,
            client=Anthropic(),
        )

    raise ConfigurationError(f"Provider '{provider_name}' cannot be created.")
