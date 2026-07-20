"""Configuration helpers for Agent Workbench."""

import os

DEFAULT_MODEL_NAME = "gpt-oss:20b"
MODEL_ENV_VAR = "AGENT_WORKBENCH_MODEL"


def get_model_name() -> str:
    """Return the configured model name or the default local model."""

    configured_model = os.getenv(MODEL_ENV_VAR, "").strip()

    return configured_model or DEFAULT_MODEL_NAME
