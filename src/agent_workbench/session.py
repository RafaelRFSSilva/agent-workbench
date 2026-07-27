"""Provider-independent synchronous agent session state."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from agent_workbench.agents import AgentProfile
from agent_workbench.context import ContextDocument
from agent_workbench.errors import ConfigurationError, SessionStateError
from agent_workbench.generation import GenerationConfig
from agent_workbench.messages import ChatRequest, ChatResponse, Message
from agent_workbench.providers.base import ChatProvider
from agent_workbench.structured_outputs import JSONResponseFormat
from agent_workbench.tool_calling import ToolRoundObserver, run_tool_calling_loop
from agent_workbench.tool_registry import ToolRegistry

DEFAULT_MAX_TOOL_ROUNDS = 8


@dataclass(frozen=True, slots=True)
class SessionId:
    """Represent one immutable caller-supplied session identifier."""

    value: str

    def __post_init__(self) -> None:
        """Require a non-blank string without normalizing it."""

        if not isinstance(self.value, str) or not self.value.strip():
            raise ConfigurationError("session identifier must be a non-blank string.")


class SessionStatus(StrEnum):
    """Represent the current synchronous session lifecycle state."""

    READY = "ready"
    COMPLETING = "completing"
    FAILED = "failed"


@dataclass(slots=True, init=False, repr=False)
class AgentSession:
    """Own one configured provider-independent conversation."""

    _id: SessionId
    _provider: ChatProvider = field(repr=False)
    _agent_profile: AgentProfile | None
    _system_prompt: str | None = field(repr=False)
    _context_documents: tuple[ContextDocument, ...] = field(repr=False)
    _generation_config: GenerationConfig
    _response_format: JSONResponseFormat | None = field(repr=False)
    _tool_registry: ToolRegistry | None = field(repr=False)
    _max_tool_rounds: int
    _messages: list[Message] = field(repr=False)
    _status: SessionStatus

    def __init__(
        self,
        *,
        id: SessionId,
        provider: ChatProvider,
        agent_profile: AgentProfile | None = None,
        system_prompt: str | None = None,
        context_documents: Iterable[ContextDocument] = (),
        generation_config: GenerationConfig | None = None,
        response_format: JSONResponseFormat | None = None,
        tool_registry: ToolRegistry | None = None,
        max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
    ) -> None:
        """Store configured values and initialize an empty ready conversation."""

        if not isinstance(id, SessionId):
            raise ConfigurationError("session id must be a SessionId.")

        if (
            isinstance(max_tool_rounds, bool)
            or not isinstance(max_tool_rounds, int)
            or max_tool_rounds <= 0
        ):
            raise ConfigurationError("maximum tool rounds must be a positive integer.")

        self._id = id
        self._provider = provider
        self._agent_profile = agent_profile
        self._system_prompt = system_prompt
        self._context_documents = tuple(context_documents)
        self._generation_config = generation_config or GenerationConfig()
        self._response_format = response_format
        self._tool_registry = tool_registry
        self._max_tool_rounds = max_tool_rounds
        self._messages = []
        self._status = SessionStatus.READY

    @property
    def id(self) -> SessionId:
        """Return the immutable session identifier."""

        return self._id

    @property
    def status(self) -> SessionStatus:
        """Return the current lifecycle state."""

        return self._status

    @property
    def provider(self) -> ChatProvider:
        """Return the configured provider."""

        return self._provider

    @property
    def provider_name(self) -> str:
        """Return provider identity from the configured provider."""

        return self._provider.name

    @property
    def model_name(self) -> str:
        """Return model identity from the configured provider."""

        return self._provider.model_name

    @property
    def agent_profile(self) -> AgentProfile | None:
        """Return optional configured profile metadata."""

        return self._agent_profile

    @property
    def system_prompt(self) -> str | None:
        """Return the already-resolved explicit system prompt."""

        return self._system_prompt

    @property
    def context_documents(self) -> tuple[ContextDocument, ...]:
        """Return the immutable configured context collection."""

        return self._context_documents

    @property
    def generation_config(self) -> GenerationConfig:
        """Return immutable provider-independent generation configuration."""

        return self._generation_config

    @property
    def response_format(self) -> JSONResponseFormat | None:
        """Return optional immutable structured response configuration."""

        return self._response_format

    @property
    def tool_registry(self) -> ToolRegistry | None:
        """Return the optional configured provider-independent registry."""

        return self._tool_registry

    @property
    def max_tool_rounds(self) -> int:
        """Return the positive tool-round limit."""

        return self._max_tool_rounds

    @property
    def messages(self) -> tuple[Message, ...]:
        """Return independent snapshots of successful conversation messages."""

        return tuple(message.copy() for message in self._messages)

    def send(
        self,
        content: str,
        *,
        tool_round_observer: ToolRoundObserver | None = None,
    ) -> ChatResponse:
        """Complete and transactionally commit one conversation turn."""

        if not isinstance(content, str) or not content.strip():
            raise ConfigurationError("session content must be a non-blank string.")

        if self._status is SessionStatus.COMPLETING:
            raise SessionStateError("session is already completing a request.")

        self._status = SessionStatus.COMPLETING
        user_message: Message = {
            "role": "user",
            "content": content,
        }
        request_messages = [
            *(message.copy() for message in self._messages),
            user_message,
        ]
        request = ChatRequest(
            messages=request_messages,
            system_prompt=self._system_prompt,
            context_documents=self._context_documents,
            generation_config=self._generation_config,
            response_format=self._response_format,
            tools=(
                self._tool_registry.definitions
                if self._tool_registry is not None
                else ()
            ),
        )

        try:
            if self._tool_registry is None:
                response = self._provider.complete(request)
            else:
                response = run_tool_calling_loop(
                    self._provider,
                    request,
                    self._tool_registry,
                    self._max_tool_rounds,
                    tool_round_observer=tool_round_observer,
                )
        except Exception:
            self._status = SessionStatus.FAILED
            raise

        assistant_message: Message = {
            "role": "assistant",
            "content": response.text,
        }
        self._messages = [
            *request_messages,
            assistant_message,
        ]
        self._status = SessionStatus.READY
        return response

    def __repr__(self) -> str:
        """Return safe session metadata without configured content or clients."""

        return (
            "AgentSession("
            f"id={self._id!r}, "
            f"status={self._status!r}, "
            f"provider_name={self.provider_name!r}, "
            f"model_name={self.model_name!r}, "
            f"message_count={len(self._messages)}"
            ")"
        )
