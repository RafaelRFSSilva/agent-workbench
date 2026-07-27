"""Tests for AgentSession construction from resolved runtime configuration."""

from inspect import signature
from pathlib import Path

import pytest

from agent_workbench.agents import AgentProfile
from agent_workbench.arguments import RuntimeConfiguration
from agent_workbench.context import ContextDocument
from agent_workbench.errors import ConfigurationError
from agent_workbench.generation import GenerationConfig
from agent_workbench.providers.ollama import OllamaProvider
from agent_workbench.session import SessionId, SessionStatus
from agent_workbench.session_factory import create_agent_session
from agent_workbench.structured_outputs import JSONResponseFormat


def configuration(**overrides: object) -> RuntimeConfiguration:
    """Create one resolved test runtime configuration."""

    values: dict[str, object] = {
        "provider_name": "ollama",
        "model_name": "test-model",
    }
    values.update(overrides)
    return RuntimeConfiguration(**values)  # type: ignore[arg-type]


def test_factory_has_the_exact_public_signature() -> None:
    """Expose one small function with a keyword-only round limit."""

    assert str(signature(create_agent_session)) == (
        "(session_id: agent_workbench.session.SessionId, "
        "configuration: agent_workbench.arguments.RuntimeConfiguration, *, "
        "max_tool_rounds: int = 8) -> agent_workbench.session.AgentSession"
    )


def test_factory_creates_ready_ollama_session_without_tools() -> None:
    """Construct the configured provider and preserve the supplied identity."""

    runtime = configuration(model_name="gpt-oss:20b")
    identifier = SessionId("factory-session")

    session = create_agent_session(identifier, runtime)

    assert session.id is identifier
    assert session.status is SessionStatus.READY
    assert session.messages == ()
    assert session.provider_name == "Ollama"
    assert session.model_name == "gpt-oss:20b"
    assert isinstance(session.provider, OllamaProvider)
    assert session.tool_registry is None
    assert session.max_tool_rounds == 8
    assert runtime == configuration(model_name="gpt-oss:20b")


def test_factory_forwards_resolved_configuration_without_rebuilding(
    monkeypatch,
) -> None:
    """Preserve loaded profile, context, generation, and response models."""

    provider = OllamaProvider("provided-model")
    monkeypatch.setattr(
        "agent_workbench.session_factory.create_provider",
        lambda provider_name, model_name: provider,
    )
    profile = AgentProfile("Reviewer", "Reviews.", "Profile prompt.")
    documents = (
        ContextDocument(Path("first.md"), "First."),
        ContextDocument(Path("second.md"), "Second."),
    )
    generation = GenerationConfig(temperature=0.2, max_output_tokens=128)
    response_format = JSONResponseFormat(
        name="result",
        schema={"type": "object", "additionalProperties": False},
    )
    runtime = configuration(
        system_prompt="Resolved prompt.",
        agent_profile=profile,
        context_documents=documents,
        generation_config=generation,
        response_format=response_format,
        show_tool_traces=True,
    )

    session = create_agent_session(
        SessionId("configured"),
        runtime,
        max_tool_rounds=3,
    )

    assert session.provider is provider
    assert session.agent_profile is profile
    assert session.system_prompt == "Resolved prompt."
    assert session.context_documents == documents
    assert session.context_documents is not documents
    assert session.generation_config is generation
    assert session.response_format is response_format
    assert session.max_tool_rounds == 3
    assert session.tool_registry is None


@pytest.mark.parametrize(
    ("provider_name", "model_name"),
    [
        ("openai", "openai-model"),
        ("anthropic", "anthropic-model"),
    ],
)
def test_factory_preserves_cloud_provider_construction_without_network(
    monkeypatch,
    provider_name,
    model_name,
) -> None:
    """Use the existing provider path without concrete-provider inspection."""

    class FakeCloudProvider:
        name = provider_name.title()

        def __init__(self, configured_model: str) -> None:
            self.model_name = configured_model

        def complete(self, request):
            raise AssertionError("Factory must not complete requests.")

    provider = FakeCloudProvider(model_name)
    create_provider_calls = []

    def fake_create_provider(selected_provider, selected_model):
        create_provider_calls.append((selected_provider, selected_model))
        return provider

    monkeypatch.setattr(
        "agent_workbench.session_factory.create_provider",
        fake_create_provider,
    )

    session = create_agent_session(
        SessionId("cloud"),
        configuration(provider_name=provider_name, model_name=model_name),
    )

    assert create_provider_calls == [(provider_name, model_name)]
    assert session.provider is provider
    assert session.model_name == model_name


def test_factory_propagates_provider_failure_unchanged(monkeypatch) -> None:
    """Do not wrap safe existing provider construction errors."""

    failure = ConfigurationError("Provider configuration failed.")

    def fail_provider(provider_name, model_name):
        raise failure

    monkeypatch.setattr(
        "agent_workbench.session_factory.create_provider",
        fail_provider,
    )

    with pytest.raises(ConfigurationError, match="Provider configuration") as exc_info:
        create_agent_session(SessionId("failed"), configuration())

    assert exc_info.value is failure


@pytest.mark.parametrize(
    ("enable_tools", "workspace", "expected_names"),
    [
        (False, None, None),
        (True, None, ["calculator"]),
        (
            False,
            "workspace",
            [
                "list_files",
                "read_file",
                "search_text",
                "search_symbols",
                "inspect_git_status",
                "inspect_git_diff",
            ],
        ),
        (
            True,
            "workspace",
            [
                "calculator",
                "list_files",
                "read_file",
                "search_text",
                "search_symbols",
                "inspect_git_status",
                "inspect_git_diff",
            ],
        ),
    ],
)
def test_factory_builds_exact_deterministic_tool_registry(
    tmp_path,
    enable_tools,
    workspace,
    expected_names,
) -> None:
    """Create only explicitly authorized tools in stable order."""

    workspace_root = tmp_path if workspace is not None else None

    session = create_agent_session(
        SessionId("tools"),
        configuration(
            enable_tools=enable_tools,
            workspace_root=workspace_root,
        ),
    )

    if expected_names is None:
        assert session.tool_registry is None
    else:
        assert session.tool_registry is not None
        assert [
            definition.name for definition in session.tool_registry.definitions
        ] == expected_names


@pytest.mark.parametrize(
    ("enable_tools", "expected_names"),
    [
        (
            False,
            [
                "list_files",
                "read_file",
                "search_text",
                "search_symbols",
                "inspect_git_status",
                "inspect_git_diff",
                "apply_file_patch",
                "run_ruff_format",
                "run_ruff_check",
                "run_pytest",
            ],
        ),
        (
            True,
            [
                "calculator",
                "list_files",
                "read_file",
                "search_text",
                "search_symbols",
                "inspect_git_status",
                "inspect_git_diff",
                "apply_file_patch",
                "run_ruff_format",
                "run_ruff_check",
                "run_pytest",
            ],
        ),
    ],
)
def test_factory_registers_actions_only_when_explicitly_authorized(
    tmp_path,
    enable_tools,
    expected_names,
) -> None:
    """Append action tools in exact order after read-only workspace tools."""

    session = create_agent_session(
        SessionId("actions"),
        configuration(
            enable_tools=enable_tools,
            enable_actions=True,
            workspace_root=tmp_path,
        ),
    )

    assert [item.name for item in session.tool_registry.definitions] == expected_names


def test_factory_rejects_actions_without_workspace() -> None:
    """Defend the reusable factory boundary from invalid direct configuration."""

    with pytest.raises(ConfigurationError, match="require a workspace"):
        create_agent_session(
            SessionId("invalid-actions"),
            configuration(enable_actions=True),
        )


def test_factory_uses_same_workspace_for_read_and_action_tools(
    monkeypatch,
    tmp_path,
) -> None:
    """Bind all workspace registrations to one canonical Workspace instance."""

    from agent_workbench import session_factory

    seen_workspaces = []
    registrations = (
        "register_workspace_tools",
        "register_symbol_tools",
        "register_git_tools",
        "register_workspace_action_tools",
        "register_validation_tools",
    )
    for name in registrations:
        original = getattr(session_factory, name)

        def record(registry, workspace, original=original):
            seen_workspaces.append(workspace)
            original(registry, workspace)

        monkeypatch.setattr(session_factory, name, record)

    create_agent_session(
        SessionId("actions"),
        configuration(enable_actions=True, workspace_root=tmp_path),
    )

    assert len(seen_workspaces) == 5
    assert all(workspace is seen_workspaces[0] for workspace in seen_workspaces)


def test_factory_uses_one_workspace_for_all_registration(
    monkeypatch,
    tmp_path,
) -> None:
    """Bind file, symbol, and Git tools to the same canonical boundary."""

    from agent_workbench import session_factory

    seen_workspaces = []
    original_workspace = session_factory.register_workspace_tools
    original_symbols = session_factory.register_symbol_tools
    original_git = session_factory.register_git_tools

    def record_workspace(registry, workspace):
        seen_workspaces.append(workspace)
        original_workspace(registry, workspace)

    def record_symbols(registry, workspace):
        seen_workspaces.append(workspace)
        original_symbols(registry, workspace)

    def record_git(registry, workspace):
        seen_workspaces.append(workspace)
        original_git(registry, workspace)

    monkeypatch.setattr(session_factory, "register_workspace_tools", record_workspace)
    monkeypatch.setattr(session_factory, "register_symbol_tools", record_symbols)
    monkeypatch.setattr(session_factory, "register_git_tools", record_git)

    create_agent_session(
        SessionId("workspace"),
        configuration(workspace_root=tmp_path),
    )

    assert len(seen_workspaces) == 3
    assert seen_workspaces[0] is seen_workspaces[1] is seen_workspaces[2]
    assert seen_workspaces[0].root == tmp_path.resolve()


def test_factory_constructs_workspace_only_when_authorized(monkeypatch) -> None:
    """Avoid workspace validation when no root is configured."""

    workspace = pytest.importorskip("agent_workbench.session_factory")

    def fail_if_called(root):
        raise AssertionError("Workspace must not be constructed.")

    monkeypatch.setattr(workspace, "Workspace", fail_if_called)

    session = create_agent_session(SessionId("plain"), configuration())

    assert session.tool_registry is None


def test_factory_propagates_invalid_workspace_and_later_call_succeeds(
    tmp_path,
) -> None:
    """Leave no global partial registry after one failed construction."""

    with pytest.raises(ConfigurationError, match="Workspace root does not exist"):
        create_agent_session(
            SessionId("invalid"),
            configuration(workspace_root=tmp_path / "missing"),
        )

    session = create_agent_session(
        SessionId("valid"),
        configuration(enable_tools=True),
    )

    assert session.tool_registry is not None
    assert [item.name for item in session.tool_registry.definitions] == ["calculator"]


def test_factory_calls_are_isolated_and_preserve_working_directory(tmp_path) -> None:
    """Create independent registries without changing process location."""

    original_directory = Path.cwd()
    runtime = configuration(enable_tools=True)

    first = create_agent_session(SessionId("first"), runtime)
    second = create_agent_session(SessionId("second"), runtime)

    assert first.tool_registry is not second.tool_registry
    assert Path.cwd() == original_directory


def test_factory_uses_agent_session_round_validation() -> None:
    """Forward invalid round counts to the existing session invariant."""

    with pytest.raises(ConfigurationError, match="positive integer"):
        create_agent_session(
            SessionId("invalid-rounds"),
            configuration(),
            max_tool_rounds=0,
        )
