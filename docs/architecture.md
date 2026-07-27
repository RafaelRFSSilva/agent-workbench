# Architecture

## Overview

Agent Workbench uses a provider-independent application architecture for
running conversations with local and cloud language models.

The command-line interface does not communicate directly with Ollama, OpenAI,
or Anthropic.

Instead, runtime configuration is resolved into shared application objects
that are translated by provider-specific adapters.

```text
User
  ↓
Command-Line Interface
  ↓
Runtime Configuration
  ↓
Provider Factory
  ↓
ChatProvider Protocol
  ↓
Provider Adapter
  ↓
Model Provider
```

The current implementation supports:

* Ollama local models.
* OpenAI through the Responses API.
* Anthropic through the Messages API.

The same conversation layer is used by every provider.

## Architectural Goals

The current architecture is designed to provide:

* Provider independence.
* Clear separation of responsibilities.
* Testability without external API calls.
* Safe configuration handling.
* Reusable agent behavior.
* Explicit conversation context.
* Portable generation configuration.
* Portable structured output configuration.
* Provider-independent tool calling and a foundation for multiple agent
  sessions.

The command-line interface is the first application client.

Future terminal and VS Code interfaces should reuse the same application
abstractions instead of implementing separate provider logic.

## Current Runtime Flow

```text
Command-Line Input
        ↓
Argument Parser
        ↓
CLIArguments
        ↓
Environment Configuration
        ↓
RuntimeConfiguration
        ↓
Provider Factory
        ↓
Selected ChatProvider
        ↓
Interactive Conversation
        ↓
ChatRequest
        ↓
Provider Adapter
        ↓
Provider SDK
```

The direct command-line workflow and the interactive setup both produce the
same `RuntimeConfiguration`.

```text
Direct CLI Arguments ──────┐
                           ├── RuntimeConfiguration
Interactive Setup ─────────┘
```

This prevents the setup flow from becoming an independent execution path.

## Package Structure

The main application package is located under:

```text
src/agent_workbench/
```

Its responsibilities are separated across modules such as:

```text
agent_workbench/
├── arguments.py
├── cli.py
├── configuration.py
├── context.py
├── generation.py
├── interactive_setup.py
├── messages.py
├── profiles/
├── providers/
├── structured_outputs.py
├── tool_calling.py
├── tool_registry.py
└── built_in_tools.py
```

The exact package structure may evolve as tool calling, sessions, workspace
access, and orchestration are introduced.

## Configuration Layer

Runtime configuration can come from:

```text
Command-Line Arguments
        ↓
Runtime Environment Variables
        ↓
Local .env File
        ↓
Application Defaults
```

Higher entries take precedence over lower entries.

This allows users to configure permanent local defaults through `.env` while
temporarily overriding values through command-line arguments.

The private `.env` file is excluded from Git.

## CLI Arguments

Parsed command-line values are represented through `CLIArguments`.

This object contains user-supplied configuration such as:

```text
CLIArguments
├── provider
├── model
├── system_prompt
├── agent
├── agent_file
├── context_files
├── temperature
├── top_p
├── max_output_tokens
├── response_format_file
├── enable_tools
└── setup
```

`CLIArguments` represents command-line input.

It is not the final configuration passed into the conversation.

Paths and values are resolved and validated before provider construction.

## Runtime Configuration

The resolved session configuration is represented through
`RuntimeConfiguration`.

```text
RuntimeConfiguration
├── provider_name
├── model_name
├── system_prompt
├── agent_profile
├── context_documents
├── generation_config
├── response_format
└── enable_tools
```

This object provides a shared representation of one configured conversation
session.

The direct argument workflow and interactive setup both produce this same
structure.

Provider clients should not be created until runtime configuration has been
validated.

## Interactive Setup

The prompt-based setup is started through:

```bash
uv run agent-workbench --setup
```

It currently collects:

```text
Provider
    ↓
Model
    ↓
Built-In Agent Profile
    ↓
Context Files
    ↓
Generation Settings
    ↓
Structured Output
```

The setup does not contain conversation or provider SDK logic.

Its responsibility is to collect and validate configuration.

```text
Interactive Input
        ↓
Interactive Setup
        ↓
RuntimeConfiguration
        ↓
Provider Factory
        ↓
Conversation
```

Direct configuration arguments cannot be combined with `--setup`.

This avoids ambiguous configuration precedence within the same execution.

The current setup uses plain terminal prompts.

A navigable terminal workspace remains a future interface milestone.

## Provider Factory

The provider factory creates the selected provider adapter after runtime
configuration is complete.

```text
provider_name
    ↓
Provider Factory
├── "ollama"    → OllamaProvider
├── "openai"    → OpenAIProvider
└── "anthropic" → AnthropicProvider
```

The conversation layer receives only an object implementing the shared
`ChatProvider` protocol.

It does not need to know which SDK is being used.

## ChatProvider Protocol

The provider-independent boundary is represented through `ChatProvider`.

Conceptually:

```text
ChatProvider
└── complete(request: ChatRequest) -> ChatResponse
```

Each provider adapter is responsible for:

* Translating the shared request.
* Calling its native SDK.
* Extracting response text and tool invocations.
* Translating provider-specific errors.
* Preserving shared application behavior.

Current implementations:

```text
ChatProvider
├── OllamaProvider
├── OpenAIProvider
└── AnthropicProvider
```

Future providers should implement the same shared boundary.

## ChatRequest

`ChatRequest` is the provider-independent model request.

```text
ChatRequest
├── messages
├── system_prompt
├── context_documents
├── generation_config
├── response_format
├── tools
└── tool_interactions
```

The request separates different types of information instead of combining
everything into one prompt string.

This distinction is important for provider translation and future agent
execution.

`ChatResponse` contains the final or intermediate assistant `text` and ordered
`tool_invocations`. A `ToolInteractionRound` pairs one tool-requesting
`ChatResponse` with its ordered `ToolResult` values. The shared round model
validates that every result corresponds to one invocation in the same order.

## Conversation Messages

Conversation history contains user and assistant messages.

```text
messages
├── user
├── assistant
├── user
└── assistant
```

The current conversation history is stored in memory.

For each user turn:

```text
User Input
    ↓
Append User Message
    ↓
Create ChatRequest
    ↓
Provider Response
    ↓
Append Assistant Message
```

Conversation state is lost when the process ends.

Persistent sessions are not yet implemented.

## System Prompt

The system prompt contains the active assistant instructions.

It remains separate from user and assistant conversation messages.

```text
ChatRequest.system_prompt
        ↓
Provider Adapter
├── Ollama: system message
├── OpenAI: instructions
└── Anthropic: system parameter
```

The system prompt is included in every provider request in the session.

It is not inserted into conversation history as a user message.

## Agent Profiles

Agent profiles provide reusable identity and behavior.

```text
AgentProfile
├── name
├── description
└── system_prompt
```

Built-in profiles are stored as packaged TOML resources:

```text
profiles/
├── developer.toml
├── planner.toml
├── reviewer.toml
└── tester.toml
```

Custom profile files use the same shared representation.

```text
Built-In Profile ─────┐
                      ├── Profile Loader
Custom Profile ───────┘
                             ↓
                         AgentProfile
                             ↓
                    RuntimeConfiguration
```

Agent profiles do not currently contain:

* Provider selection.
* Model selection.
* Generation configuration.
* Context files.
* Response formats.
* Tools.
* Permissions.

These remain separate runtime concerns.

Future agent-session configuration may combine these concerns at a higher
application layer without changing `AgentProfile` into a provider-specific
object.

## Context Documents

Explicit context files are loaded as `ContextDocument` objects.

```text
ContextDocument
├── source
└── content
```

The loading pipeline is:

```text
--context-file
        ↓
Path Validation
        ↓
UTF-8 Content Loading
        ↓
ContextDocument
        ↓
RuntimeConfiguration.context_documents
        ↓
ChatRequest.context_documents
```

Context documents remain separate from conversation history.

Each provider translates the documents into its own instruction or system
format.

```text
Context Documents
├── Ollama: system message content
├── OpenAI: instructions content
└── Anthropic: system content
```

The current implementation sends the complete contents of every selected file
with each request.

It does not currently provide:

* On-demand file tools.
* Directory scanning.
* Code symbol search.
* Embeddings.
* Vector retrieval.
* Project indexing.
* Retrieval-Augmented Generation.

These capabilities belong to future workspace and retrieval layers.

## Generation Configuration

Portable generation parameters are represented through `GenerationConfig`.

```text
GenerationConfig
├── temperature
├── top_p
└── max_output_tokens
```

All fields are optional.

When a field is not supplied, providers preserve their normal defaults where
possible.

Provider translation:

```text
GenerationConfig
├── Ollama
│   ├── temperature → options.temperature
│   ├── top_p → options.top_p
│   └── max_output_tokens → options.num_predict
│
├── OpenAI
│   ├── temperature → temperature
│   ├── top_p → top_p
│   └── max_output_tokens → max_output_tokens
│
└── Anthropic
    ├── temperature → temperature
    ├── top_p → top_p
    └── max_output_tokens → max_tokens
```

Anthropic requires a maximum output-token value for every request.

When no shared value is supplied, the Anthropic adapter uses its existing
fallback.

Provider-specific generation controls are intentionally excluded from the
shared object.

## Structured Outputs

Portable structured output requests are represented through
`JSONResponseFormat`.

```text
JSONResponseFormat
├── name
└── schema
```

The configuration path is:

```text
--response-format-file
        ↓
Response Format Loader
        ↓
JSONResponseFormat
        ↓
RuntimeConfiguration.response_format
        ↓
ChatRequest.response_format
        ↓
Provider Adapter
```

Provider translation:

```text
JSONResponseFormat
├── Ollama
│   └── format = schema
│
├── OpenAI
│   └── text.format
│       ├── type = "json_schema"
│       ├── name
│       ├── schema
│       └── strict = true
│
└── Anthropic
    └── output_config.format
        ├── type = "json_schema"
        └── schema
```

When no response format is supplied, structured output arguments are omitted.

Provider responses are currently returned as strings, including JSON
responses.

The application does not yet deserialize or locally validate generated JSON
against the configured schema.

## Provider Adapters

Each provider adapter owns its native SDK interaction.

### Ollama

```text
ChatRequest
    ↓
OllamaProvider
    ↓
ollama.chat(...)
    ↓
ChatResponse
```

The adapter translates:

* Conversation messages.
* System instructions.
* Context documents.
* Generation parameters.
* JSON Schema format configuration.
* Tool definitions, tool-call history, and tool results.

### OpenAI

```text
ChatRequest
    ↓
OpenAIProvider
    ↓
OpenAI Responses API
    ↓
ChatResponse
```

The adapter translates:

* Conversation history into Responses API input.
* System instructions into `instructions`.
* Generation configuration.
* Strict structured output configuration through `text.format`.
* Function definitions, function-call inputs, and function-call outputs.

### Anthropic

```text
ChatRequest
    ↓
AnthropicProvider
    ↓
Anthropic Messages API
    ↓
ChatResponse
```

The adapter translates:

* Conversation history into Anthropic messages.
* System instructions and context into the system parameter.
* Generation configuration.
* Structured output configuration through `output_config.format`.
* Tool definitions, `tool_use` blocks, and `tool_result` blocks.

## Error Boundaries

Provider-specific exceptions should not escape directly into the conversation
layer.

Each adapter is responsible for translating SDK failures into clear
application errors.

Examples include:

* Missing API credentials.
* Provider connection failures.
* Authentication failures.
* Unsupported models.
* Invalid provider parameters.
* Malformed provider responses.

Validation that can be performed locally should occur before provider
construction or before the request is sent.

## Testing Architecture

Automated tests do not use real paid provider APIs.

External SDK clients are replaced with deterministic test doubles.

```text
Application Code
    ↓
Fake Provider or Fake SDK Client
    ↓
Captured Request Arguments
    ↓
Assertions
```

The test suite verifies:

* CLI parsing.
* Environment configuration.
* Runtime precedence.
* Agent profile loading.
* Context loading.
* Generation validation.
* Structured output validation.
* Provider request translation.
* Error handling.
* Conversation history.
* Interactive setup behavior.
* Provider factory behavior.
* Tool translation, execution ordering, history, and CLI integration.

Real provider validation is performed separately from the automated unit
suite.

## Current Layer Boundaries

```text
Interface Layer
├── CLI argument parsing
├── Prompt-based setup
└── Interactive conversation display

Application Layer
├── Runtime configuration
├── Conversation flow
├── Shared request objects
├── Agent profiles
├── Context documents
├── Generation configuration
└── Structured output configuration

Tool Execution Layer
├── ToolRegistry
├── synchronous handlers
├── immutable approval requests
├── ToolInteractionRound
└── run_tool_calling_loop

Provider Layer
├── Ollama adapter
├── OpenAI adapter
└── Anthropic adapter

External Systems
├── Ollama
├── OpenAI API
└── Anthropic API
```

Future features should respect these boundaries.

For example, filesystem tools should belong to a workspace or tool-execution
layer rather than being implemented inside provider adapters.

## Planned Architectural Layers

The expected future architecture is:

```text
VS Code or Terminal Interface
        ↓
Workspace Application
        ↓
Agent Session Manager
        ↓
Orchestrator
        ↓
Agent Runtime
├── Conversation
├── Tools
├── Context
├── Retrieval
├── Permissions
└── State
        ↓
Provider-Independent Model Layer
        ↓
Provider Adapters
        ↓
Local or Cloud Models
```

These layers are not all implemented yet.

They describe the intended direction and should not be interpreted as current
functionality.

## Tool Calling Boundary

Tool calling is provider-independent. `ToolDefinition` describes a named tool,
its description, and JSON object input schema. `ToolInvocation` carries the
provider-native call identifier, tool name, and JSON object arguments.
`ToolResult` associates a successful JSON-compatible output or safe error with
one invocation identifier.

```text
ChatRequest.tools
        ↓
Provider-specific tool definition translation
        ↓
ChatResponse.tool_invocations
        ↓
ToolRegistry synchronous handler execution
        ↓
ToolInteractionRound
        ↓
Provider-specific interaction-history translation
        ↓
Final ChatResponse
```

`run_tool_calling_loop()` repeatedly completes a request, executes ordered
invocations through `ToolRegistry`, appends validated interaction rounds, and
stops on a response without tool invocations. Its positive maximum-round
argument protects against unbounded new tool rounds; pre-existing rounds are
forwarded without re-execution.

Registrations may mark a handler as approval-required and supply a deterministic
preview callback. `ToolApprovalRequest` snapshots the exact immutable
invocation and strict-JSON preview. Only the caller-owned
`ToolApprovalHandler` can return explicit `APPROVE` or `DENY`. An effectful
invocation must be alone in its provider response, approval is never cached,
and preview, absence, denial, invalid decision, or handler failure all prevent
execution. Approval data is not inserted into provider history or normal
session messages.

Provider adapters retain native protocol details:

* Ollama translates function definitions, assistant `tool_calls`, and ordered
  `tool` result messages. It has no native call identifier, so validated round
  ordering preserves correlation.
* OpenAI translates Responses API function tools, `function_call` items, and
  `function_call_output` items.
* Anthropic translates Messages API tools, assistant `tool_use` blocks, and
  user `tool_result` blocks.

The CLI keeps tools opt-in. `--enable-tools` registers the safe synchronous
calculator, while `--workspace PATH` authorizes the read-only `list_files`,
`read_file`, `search_text`, `search_symbols`, `inspect_git_status`, and
`inspect_git_diff` tools for one root. `--enable-actions` requires that
workspace and appends `apply_file_patch`, `run_ruff_format`, `run_ruff_check`,
and `run_pytest`. The calculator, when enabled, remains first. Without a tool
option or workspace, no registry is created.
`--show-tool-traces` adds an optional callback for completed provider-independent
rounds; traces are compact JSON, redacted, and excluded from normal CLI history.
Internal tool rounds remain inside a single loop and are not persisted in normal
CLI history across later user turns.

This separation keeps provider adapters declarative and prevents them from
directly executing application capabilities.

## Workspace Boundary

Workspace access is explicit through `Workspace(root)`, a frozen slotted model
that stores a canonical existing directory. It resolves requested paths
strictly before containment checks, rejecting absolute paths, traversal,
prefix-confusion, and symlink escapes. Symlinks resolving inside the root are
permitted.

```text
Workspace
├── root
├── files
├── Git state
├── permissions
└── available tools
```

`list_files` returns deterministic sorted direct children, including hidden
entries, and caps a directory at 128 entries. `read_file` accepts strict UTF-8
only and caps content at 100 KiB. Both report canonical relative paths. They
are read-only. `search_text` provides bounded literal recursive search of
regular UTF-8 files in deterministic relative order, skips invalid UTF-8 and
directory symlinks, and reports truncation at its query, file, byte, match, and
line limits.

`search_symbols` parses Python with `ast.parse()` and never imports or executes
inspected code. Lexical AST scope identifies classes, top-level and nested
functions, asynchronous functions, methods, and nested classes. The portable
kinds are `class`, `function`, and `method`; `any` is the unfiltered input
value, and `is_async` independently identifies asynchronous functions and
methods. Literal name and qualified-name matching is case-insensitive by
default. Results are ordered by canonical relative path, line, and qualified
name.

Recursive symbol search includes hidden paths, skips directory symlinks, and
deduplicates canonical files. Explicit internal file symlinks resolve to their
canonical targets. Invalid UTF-8, `SyntaxError`, and oversized files are
skipped with `files_skipped` during directory search and rejected safely when
requested explicitly. Limits are 256 query characters, 512 Python files,
100 KiB per file, 256 matches, and 512 qualified-name characters;
`truncated` reports bounded file, match, or name handling.

Git inspection runs only fixed non-shell status and diff commands with external
helpers disabled, a three-second timeout, and 100 KiB output limits; it
separates unstaged and staged diff results.

The controlled write boundary is intentionally stricter than reads.
`apply_file_patch` never follows a target or parent symlink, rejects `.git`,
and supports only one complete-content compare-and-swap update or creation.
The preview contains the complete deterministic unified diff and bounded
metadata. Execution validates again after approval; existing files use a
same-directory temporary file and atomic replacement with portable permission
preservation, while new files use exclusive creation.

Validation uses the current Python interpreter with fixed Ruff or pytest
module arguments, `shell=False`, a canonical workspace cwd, minimal offline
environment, isolated process groups, timeouts, and streaming 100 KiB limits
for each output stream. Ruff and pytest findings are normal results; start,
capture, module, and timeout failures are safe errors.

The reusable factory constructs definitions and handlers but never prompts.
The CLI owns informed display and exact-invocation approval. `AgentSession`
forwards the callback transactionally, so denied or invalid turns roll back
while prior successful conversation remains.

Read access, controlled writes, fixed command execution, network access, and
Git mutation remain separate permissions. Arbitrary commands, deletion,
rename, multi-file transactions, and network/MCP capabilities remain future
work.

## Agent Session Boundary

`AgentSession` is the provider-independent application boundary for one
configured synchronous conversation. The CLI requests one session and remains
responsible for input, output, headers, exit handling, configuration
resolution, error rendering, and tool-trace formatting.

Session construction is reusable through `create_agent_session()`. Callers
supply an explicit `SessionId` and an already resolved `RuntimeConfiguration`.
Argument parsing, environment resolution, profile/context loading, and
interactive setup happen before this boundary. The factory creates the
provider, forwards the resolved profile, prompt, context, generation, and
response-format models, constructs the optional workspace and registry, and
returns one `AgentSession`. It performs no input, output, trace rendering,
completion, lifecycle management, or conversation mutation.

```text
RuntimeConfiguration resolution
        ↓
create_agent_session()
        ↓
AgentSession
        ↓
ChatProvider / run_tool_calling_loop()
        ↓
CLI rendering
```

Factory tool construction remains deterministic:

* No tool options: no registry.
* Calculator only: `calculator`.
* Workspace only: `list_files`, `read_file`, `search_text`,
  `search_symbols`, `inspect_git_status`, `inspect_git_diff`.
* Combined: `calculator`, followed by the workspace-only order.

All workspace registrations share one canonical `Workspace`. Trace enablement
stays in runtime presentation configuration and is never applied by the
factory. The CLI now owns only configuration resolution, its explicit
CLI-scoped session identifier, terminal presentation, trace formatting, and
error presentation; it does not construct providers, workspaces, registries,
or session fields.

```text
AgentSession
├── SessionId
├── ChatProvider
│   ├── provider name
│   └── provider-derived model name
├── optional AgentProfile metadata
├── resolved system prompt
├── ContextDocument tuple
├── GenerationConfig
├── optional JSONResponseFormat
├── optional ToolRegistry
├── maximum tool rounds
├── owned successful Message history
└── SessionStatus
    ├── ready
    ├── completing
    └── failed
```

`SessionId` is immutable and caller-supplied. Model identity is read from the
configured provider rather than duplicated. Context, generation, response
format, and ordered tool definitions are forwarded through the existing
`ChatRequest` contract on every turn.

The synchronous `send()` operation rejects blank content and re-entrant sends,
sets the status to `completing`, and selects direct provider completion or the
existing `run_tool_calling_loop()`. It forwards the maximum-round limit and an
optional observer without implementing another tool loop.

Conversation updates are transactional. A successful send appends the user
message and final assistant text exactly once and returns to `ready`; internal
tool rounds are not persisted in normal conversation history. Provider,
tool-loop, maximum-round, or observer exceptions leave prior history unchanged,
set `failed`, and propagate unchanged. A later successful send is allowed and
returns the session to `ready`.

The first boundary does not add task models, task assignment, multiple
simultaneous sessions, orchestration, persistence, serialization, concurrency,
cancellation, asynchronous APIs, RAG, MCP, worktrees, VS Code, or voice input.

## Orchestration Boundary

The orchestrator should coordinate sessions and tasks.

It should not:

* Contain provider-specific API calls.
* Directly manipulate provider SDK request formats.
* Bypass workspace permissions.
* Modify files without a tool executor.
* Hide agent activity from the user.

The orchestrator may:

* Assign tasks.
* Track dependencies.
* Request agent reviews.
* Pass approved results between sessions.
* Detect blocked work.
* Collect final outputs.

## Persistence

Current state is process-local and in memory.

The project does not yet persist:

* Conversation history.
* Agent sessions.
* Setup selections.
* Task state.
* Tool traces.
* Project indexes.
* Evaluation results.

Persistence should be added only after the session and task models are stable.

## Security Principles

The architecture follows these current security rules:

* Secrets remain outside source code.
* Local `.env` files are ignored by Git.
* Context files are validated before provider creation.
* Unsupported paths and formats are rejected.
* Provider-specific errors are translated.
* Automated tests do not call paid APIs.
* Existing environment variables are not overwritten by `.env`.

Future workspace execution must add:

* Explicit tool permissions.
* Path containment.
* Command allowlists or confirmation.
* Write isolation.
* Destructive-action confirmation.
* Audit traces.
* Secret redaction.
* Network-access controls.

## Architectural Non-Goals

The current architecture does not yet provide:

* Fully autonomous agents.
* Multiple simultaneous agent sessions.
* Filesystem exploration.
* Source-code modification.
* Shell execution.
* Project indexing.
* Retrieval-Augmented Generation.
* Persistent task state.
* Git worktree management.
* A VS Code extension.
* Background execution.
* Cloud deployment.

The current tool implementation is synchronous and contains the opt-in
calculator plus explicitly authorized read-only workspace inspection. It does
not include writes, network, MCP, asynchronous execution, or user-defined
tools.

The presence of future-oriented abstractions in documentation does not imply
that these capabilities are already implemented.

## Related Documentation

* [Product Vision](product-vision.md)
* [Getting Started](getting-started.md)
* [Runtime Configuration](runtime-configuration.md)
* [Agent Profiles](agent-profiles.md)
* [Context Files](context-files.md)
* [Structured Outputs](structured-outputs.md)
* [Roadmap](roadmap.md)
