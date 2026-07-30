"""Construct configured AgentSession instances from resolved runtime values."""

from agent_workbench.arguments import RuntimeConfiguration
from agent_workbench.built_in_tools import create_built_in_tool_registry
from agent_workbench.errors import ConfigurationError
from agent_workbench.git_tools import register_git_tools
from agent_workbench.providers.factory import create_provider
from agent_workbench.session import AgentSession, SessionId
from agent_workbench.symbol_tools import register_symbol_tools
from agent_workbench.tool_registry import ToolRegistry
from agent_workbench.validation_tools import register_validation_tools
from agent_workbench.workspace import Workspace
from agent_workbench.workspace_actions import register_workspace_action_tools
from agent_workbench.workspace_tools import register_workspace_tools

PROJECT_INSTRUCTIONS_OPENING_DELIMITER = "<project_instructions>"
PROJECT_INSTRUCTIONS_CLOSING_DELIMITER = "</project_instructions>"


def compose_system_prompt(
    system_prompt: str | None,
    project_instructions: str | None,
) -> str | None:
    """Append one delimited project-instructions section to system context."""

    if project_instructions is None:
        return system_prompt

    project_section = (
        f"{PROJECT_INSTRUCTIONS_OPENING_DELIMITER}\n"
        f"{project_instructions}\n"
        f"{PROJECT_INSTRUCTIONS_CLOSING_DELIMITER}"
    )
    if system_prompt is None:
        return project_section
    return f"{system_prompt}\n\n{project_section}"


def create_agent_session(
    session_id: SessionId,
    configuration: RuntimeConfiguration,
    *,
    max_tool_rounds: int = 8,
) -> AgentSession:
    """Create one session from already resolved runtime configuration."""

    if configuration.enable_actions and configuration.workspace_root is None:
        raise ConfigurationError("Controlled actions require a workspace.")

    provider = create_provider(
        configuration.provider_name,
        configuration.model_name,
    )
    tool_registry = (
        create_built_in_tool_registry() if configuration.enable_tools else None
    )

    if configuration.workspace_root is not None:
        workspace = Workspace(configuration.workspace_root)
        if tool_registry is None:
            tool_registry = ToolRegistry()
        register_workspace_tools(tool_registry, workspace)
        register_symbol_tools(tool_registry, workspace)
        register_git_tools(tool_registry, workspace)
        if configuration.enable_actions:
            register_workspace_action_tools(tool_registry, workspace)
            register_validation_tools(tool_registry, workspace)

    return AgentSession(
        id=session_id,
        provider=provider,
        agent_profile=configuration.agent_profile,
        system_prompt=compose_system_prompt(
            configuration.system_prompt,
            configuration.project_instructions,
        ),
        context_documents=tuple([*configuration.context_documents]),
        generation_config=configuration.generation_config,
        response_format=configuration.response_format,
        tool_registry=tool_registry,
        max_tool_rounds=max_tool_rounds,
    )
