"""Interactive runtime configuration setup."""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

from agent_workbench.agents import SUPPORTED_AGENT_NAMES
from agent_workbench.arguments import (
    CLIArguments,
    RuntimeConfiguration,
    resolve_runtime_configuration,
)
from agent_workbench.config import (
    DEFAULT_MODEL_NAME,
    DEFAULT_PROVIDER_NAME,
    SUPPORTED_PROVIDERS,
    ProviderName,
    get_model_name,
    get_provider_name,
)
from agent_workbench.context import ContextDocument, load_context_document
from agent_workbench.errors import ConfigurationError
from agent_workbench.generation import GenerationConfig

InputFunction = Callable[[str], str]
OutputFunction = Callable[[str], None]


def run_interactive_setup(
    *,
    input_fn: InputFunction = input,
    output_fn: OutputFunction = print,
) -> RuntimeConfiguration:
    """Collect runtime configuration interactively."""

    output_fn("Agent Workbench Interactive Setup")
    output_fn("Press Enter to accept a displayed default or skip an optional value.")
    output_fn("")

    configured_provider = get_provider_name()

    provider_name = _prompt_provider(
        default_provider=configured_provider,
        input_fn=input_fn,
        output_fn=output_fn,
    )

    default_model = _resolve_default_model(
        provider_name=provider_name,
        configured_provider=configured_provider,
    )

    model_name = _prompt_model(
        default_model=default_model,
        input_fn=input_fn,
        output_fn=output_fn,
    )

    agent_name = _prompt_agent_profile(
        input_fn=input_fn,
        output_fn=output_fn,
    )

    context_documents = _prompt_context_documents(
        input_fn=input_fn,
        output_fn=output_fn,
    )

    generation_config = _prompt_generation_config(
        input_fn=input_fn,
        output_fn=output_fn,
    )

    runtime_configuration = resolve_runtime_configuration(
        CLIArguments(
            provider_name=provider_name,
            model_name=model_name,
            agent_name=agent_name,
            temperature=generation_config.temperature,
            top_p=generation_config.top_p,
            max_output_tokens=generation_config.max_output_tokens,
        )
    )

    return replace(
        runtime_configuration,
        context_documents=context_documents,
    )


def _prompt_provider(
    *,
    default_provider: ProviderName,
    input_fn: InputFunction,
    output_fn: OutputFunction,
) -> ProviderName:
    """Prompt until the user selects a supported provider."""

    providers = tuple(sorted(SUPPORTED_PROVIDERS))

    output_fn("Available providers:")

    for index, provider_name in enumerate(providers, start=1):
        output_fn(f"  {index}. {provider_name}")

    while True:
        selected_value = input_fn(f"Provider [{default_provider}]: ").strip().lower()

        if not selected_value:
            return default_provider

        if selected_value.isdigit():
            selected_index = int(selected_value) - 1

            if 0 <= selected_index < len(providers):
                return cast(
                    ProviderName,
                    providers[selected_index],
                )

        if selected_value in SUPPORTED_PROVIDERS:
            return cast(
                ProviderName,
                selected_value,
            )

        output_fn("Invalid provider. Enter a provider name or its menu number.")


def _resolve_default_model(
    *,
    provider_name: ProviderName,
    configured_provider: ProviderName,
) -> str | None:
    """Return a safe model default for the selected provider."""

    if provider_name == configured_provider:
        try:
            return get_model_name(provider_name)
        except ConfigurationError:
            return None

    if provider_name == DEFAULT_PROVIDER_NAME:
        return DEFAULT_MODEL_NAME

    return None


def _prompt_model(
    *,
    default_model: str | None,
    input_fn: InputFunction,
    output_fn: OutputFunction,
) -> str:
    """Prompt until the user supplies a non-empty model name."""

    while True:
        prompt = (
            f"Model [{default_model}]: " if default_model is not None else "Model: "
        )

        selected_model = input_fn(prompt).strip()

        if selected_model:
            return selected_model

        if default_model is not None:
            return default_model

        output_fn("Model name must not be blank.")


def _prompt_agent_profile(
    *,
    input_fn: InputFunction,
    output_fn: OutputFunction,
) -> str | None:
    """Prompt until the user selects an agent profile or none."""

    agent_names = tuple(sorted(SUPPORTED_AGENT_NAMES))

    output_fn("")
    output_fn("Available agent profiles:")
    output_fn("  0. none")

    for index, agent_name in enumerate(agent_names, start=1):
        output_fn(f"  {index}. {agent_name}")

    while True:
        selected_value = input_fn("Agent [none]: ").strip().lower()

        if not selected_value or selected_value in {"0", "none"}:
            return None

        if selected_value.isdigit():
            selected_index = int(selected_value) - 1

            if 0 <= selected_index < len(agent_names):
                return agent_names[selected_index]

        if selected_value in SUPPORTED_AGENT_NAMES:
            return selected_value

        output_fn("Invalid agent. Enter an agent name, its menu number, or 0 for none.")


def _prompt_context_documents(
    *,
    input_fn: InputFunction,
    output_fn: OutputFunction,
) -> tuple[ContextDocument, ...]:
    """Prompt for zero or more validated context documents."""

    context_documents: list[ContextDocument] = []

    output_fn("")
    output_fn("Context files:")
    output_fn("Enter one file path at a time. Press Enter when finished.")

    while True:
        selected_value = input_fn("Context file [done]: ").strip()

        if not selected_value:
            return tuple(context_documents)

        context_path = Path(selected_value).expanduser()

        try:
            context_document = load_context_document(context_path)
        except ConfigurationError as exc:
            output_fn(f"Invalid context file: {exc}")
            continue

        context_documents.append(context_document)
        output_fn(f"Added context file: {context_document.source}")


def _prompt_generation_config(
    *,
    input_fn: InputFunction,
    output_fn: OutputFunction,
) -> GenerationConfig:
    """Prompt for optional provider-independent generation settings."""

    output_fn("")
    output_fn("Generation settings:")
    output_fn("Press Enter to use the provider or model default.")

    temperature = _prompt_optional_unit_interval(
        prompt="Temperature [provider default]: ",
        display_name="Temperature",
        input_fn=input_fn,
        output_fn=output_fn,
    )
    top_p = _prompt_optional_unit_interval(
        prompt="Top-p [provider default]: ",
        display_name="Top-p",
        input_fn=input_fn,
        output_fn=output_fn,
    )
    max_output_tokens = _prompt_optional_positive_integer(
        prompt="Maximum output tokens [provider default]: ",
        input_fn=input_fn,
        output_fn=output_fn,
    )

    return GenerationConfig(
        temperature=temperature,
        top_p=top_p,
        max_output_tokens=max_output_tokens,
    )


def _prompt_optional_unit_interval(
    *,
    prompt: str,
    display_name: str,
    input_fn: InputFunction,
    output_fn: OutputFunction,
) -> float | None:
    """Prompt for an optional numeric value between zero and one."""

    while True:
        selected_value = input_fn(prompt).strip()

        if not selected_value:
            return None

        try:
            parsed_value = float(selected_value)
        except ValueError:
            output_fn(f"{display_name} must be a number between 0.0 and 1.0.")
            continue

        if not 0.0 <= parsed_value <= 1.0:
            output_fn(f"{display_name} must be a number between 0.0 and 1.0.")
            continue

        return parsed_value


def _prompt_optional_positive_integer(
    *,
    prompt: str,
    input_fn: InputFunction,
    output_fn: OutputFunction,
) -> int | None:
    """Prompt for an optional positive integer."""

    while True:
        selected_value = input_fn(prompt).strip()

        if not selected_value:
            return None

        try:
            parsed_value = int(selected_value)
        except ValueError:
            output_fn("Maximum output tokens must be a positive integer.")
            continue

        if parsed_value <= 0:
            output_fn("Maximum output tokens must be a positive integer.")
            continue

        return parsed_value
