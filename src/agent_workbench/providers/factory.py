"""Provider construction for Agent Workbench."""

import os

from openai import OpenAI

from agent_workbench.config import ProviderName
from agent_workbench.errors import ConfigurationError
from agent_workbench.providers.base import ChatProvider
from agent_workbench.providers.ollama import OllamaProvider
from agent_workbench.providers.openai import OpenAIProvider

OPENAI_API_KEY_ENV_VAR = "OPENAI_API_KEY"


def create_provider(
    provider_name: ProviderName,
    model_name: str,
) -> ChatProvider:
    """Create the configured chat provider."""

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

    raise ConfigurationError(f"Provider '{provider_name}' cannot be created.")
