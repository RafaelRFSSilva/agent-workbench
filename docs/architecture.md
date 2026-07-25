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
* A foundation for tool calling and multiple agent sessions.

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
└── structured_outputs.py
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
└── response_format
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
└── chat(request: ChatRequest) -> str
```

Each provider adapter is responsible for:

* Translating the shared request.
* Calling its native SDK.
* Extracting the final response text.
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
└── response_format
```

The request separates different types of information instead of combining
everything into one prompt string.

This distinction is important for provider translation and future agent
execution.

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
Response Text
```

The adapter translates:

* Conversation messages.
* System instructions.
* Context documents.
* Generation parameters.
* JSON Schema format configuration.

### OpenAI

```text
ChatRequest
    ↓
OpenAIProvider
    ↓
OpenAI Responses API
    ↓
Response Text
```

The adapter translates:

* Conversation history into Responses API input.
* System instructions into `instructions`.
* Generation configuration.
* Strict structured output configuration through `text.format`.

### Anthropic

```text
ChatRequest
    ↓
AnthropicProvider
    ↓
Anthropic Messages API
    ↓
Response Text
```

The adapter translates:

* Conversation history into Anthropic messages.
* System instructions and context into the system parameter.
* Generation configuration.
* Structured output configuration through `output_config.format`.

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

The next major provider-independent abstraction is tool calling.

A future request may contain:

```text
ChatRequest
├── messages
├── system_prompt
├── context_documents
├── generation_config
├── response_format
└── tools
```

A shared tool architecture may use:

```text
ToolDefinition
ToolInvocation
ToolResult
```

Provider adapters will translate tool definitions and invocations.

Tool execution itself should remain outside provider adapters.

```text
Model Requests Tool
        ↓
Provider Adapter Translation
        ↓
Application Tool Executor
        ↓
Tool Result
        ↓
Next Model Request
```

This distinction prevents providers from controlling application execution
directly.

## Workspace Boundary

Future workspace access should be explicit.

```text
Workspace
├── root
├── files
├── Git state
├── permissions
└── available tools
```

Agents should not receive unrestricted access automatically.

Read access, write access, command execution, network access, and Git
operations should be represented separately.

## Agent Session Boundary

The current CLI manages one conversation at a time.

A future `AgentSession` should combine:

```text
AgentSession
├── identity
├── provider configuration
├── model configuration
├── conversation
├── task
├── workspace scope
├── tools
├── permissions
└── status
```

An agent session should use the current provider-independent request and
provider layers rather than replacing them.

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
* Tool execution.
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
