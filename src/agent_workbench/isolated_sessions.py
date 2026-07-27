"""Construct AgentSession instances inside verified local Git worktrees."""

from dataclasses import dataclass, replace
from pathlib import Path

from agent_workbench.arguments import RuntimeConfiguration
from agent_workbench.context import ContextDocument, load_context_document
from agent_workbench.errors import ConfigurationError
from agent_workbench.session import AgentSession, SessionId
from agent_workbench.session_factory import create_agent_session
from agent_workbench.worktrees import (
    WorktreeHandle,
    inspect_git_worktree,
)


@dataclass(frozen=True, slots=True)
class IsolatedAgentSession:
    """Pair one verified worktree with its dedicated configured session."""

    worktree: WorktreeHandle
    session: AgentSession


def create_isolated_agent_session(
    session_id: SessionId,
    configuration: RuntimeConfiguration,
    worktree: WorktreeHandle,
    *,
    max_tool_rounds: int = 8,
) -> IsolatedAgentSession:
    """Construct one session whose workspace capabilities use only a worktree."""

    if not isinstance(worktree, WorktreeHandle):
        raise ConfigurationError("isolated session requires a verified WorktreeHandle.")
    if not isinstance(configuration, RuntimeConfiguration):
        raise ConfigurationError("isolated session requires a RuntimeConfiguration.")

    inspect_git_worktree(worktree)
    source = _configuration_source(configuration)
    if source != worktree.source_repository:
        raise ConfigurationError(
            "isolated session workspace does not match the worktree source."
        )
    if worktree.worktree_path == source:
        raise ConfigurationError(
            "isolated session worktree must differ from its source."
        )

    isolated_context = _map_context_documents(
        configuration.context_documents,
        source=source,
        worktree=worktree.worktree_path,
    )
    isolated_configuration = replace(
        configuration,
        workspace_root=worktree.worktree_path,
        context_documents=isolated_context,
    )

    try:
        session = create_agent_session(
            session_id,
            isolated_configuration,
            max_tool_rounds=max_tool_rounds,
        )
    except ConfigurationError:
        raise ConfigurationError(
            "Isolated session construction failed; worktree "
            f"{worktree.target_display} on branch {worktree.branch_name} "
            "was preserved for manual recovery."
        ) from None

    return IsolatedAgentSession(
        worktree=worktree,
        session=session,
    )


def _configuration_source(configuration: RuntimeConfiguration) -> Path:
    """Return the canonical explicitly configured source repository."""

    supplied = configuration.workspace_root
    if not isinstance(supplied, Path):
        raise ConfigurationError(
            "isolated session requires the source workspace configuration."
        )
    try:
        return supplied.expanduser().resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError):
        raise ConfigurationError(
            "isolated session source workspace is unavailable."
        ) from None


def _map_context_documents(
    documents: tuple[ContextDocument, ...],
    *,
    source: Path,
    worktree: Path,
) -> tuple[ContextDocument, ...]:
    """Reload ordered source-relative context from the isolated worktree."""

    mapped_documents = []
    for document in documents:
        relative_path = _source_relative_context_path(document, source)
        mapped_path = worktree / relative_path
        try:
            canonical_mapped = mapped_path.resolve(strict=True)
            canonical_mapped.relative_to(worktree)
            loaded = load_context_document(canonical_mapped)
        except (
            ConfigurationError,
            FileNotFoundError,
            OSError,
            RuntimeError,
            ValueError,
        ):
            raise ConfigurationError(
                "Isolated context file is unavailable, external, or invalid."
            ) from None
        mapped_documents.append(
            ContextDocument(
                source=relative_path,
                content=loaded.content,
            )
        )
    return tuple(mapped_documents)


def _source_relative_context_path(
    document: ContextDocument,
    source: Path,
) -> Path:
    """Return a canonical source-relative context path or reject it."""

    supplied = document.source
    if not isinstance(supplied, Path):
        raise ConfigurationError("Isolated context source must be a repository path.")

    try:
        if supplied.is_absolute():
            canonical_source = supplied.expanduser().resolve(strict=True)
        else:
            canonical_source = (source / supplied).resolve(strict=True)
        return canonical_source.relative_to(source)
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        raise ConfigurationError(
            "Isolated context files must be contained by the source repository."
        ) from None
