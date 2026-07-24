# Development Log

This document records the incremental development of Agent Workbench,
including architecture decisions, implementation milestones, validation,
and known limitations.

## 2026-07-20 — Initial Local Model Integration

### Objective

Establish the minimum application architecture required for a Python service
to communicate with a locally hosted language model.

### Implemented

- Initialized a packaged Python project using uv.
- Added the Ollama Python client as the first model provider dependency.
- Integrated the application with a locally hosted `gpt-oss:20b` model.
- Validated communication through both the Ollama HTTP API and Python client.
- Confirmed GPU-backed local inference.
- Added project documentation, licensing, and an incremental Git history.

### Architecture

```text
Python Application
        ↓
Ollama Python Client
        ↓
Ollama Local API
        ↓
Local Language Model
```

### Validation

The application successfully connected to the local model and returned the
expected response:

```text
Python connected to the local model
```

### Technical Decisions

- Used WSL 2 with Ubuntu 24.04 to align the local development environment
  with common Linux-based deployment targets.
- Selected Python 3.12 and uv to provide reproducible dependency and
  interpreter management.
- Used Ollama as an independent local inference server, keeping the Python
  application decoupled from the model runtime.
- Accessed the model through the Ollama client instead of embedding inference
  logic directly into the application.
- Kept the initial implementation intentionally small to validate connectivity,
  model availability, and GPU-backed inference before introducing additional
  abstractions.

### Current Limitations

- The prompt is currently hard-coded.
- Only one local model provider is supported.
- There is no interactive command-line interface.
- Error handling, configuration, logging, and automated tests are not yet
  implemented.

### Next Milestone

Build an interactive command-line interface and move the Ollama integration
behind a dedicated provider abstraction.

## 2026-07-20 — Interactive CLI and Runtime Configuration

### Objective

Replace the fixed model connectivity check with a testable interactive
application capable of maintaining multi-turn conversations and handling
runtime failures clearly.

### Implemented

- Added an interactive command-line conversation loop.
- Added support for `/exit` and `/quit` session commands.
- Ignored empty input without contacting the model.
- Preserved user and assistant messages across multiple requests.
- Moved the command-line implementation into a dedicated `cli` module.
- Added dependency injection for the completion function.
- Added model selection through the `AGENT_WORKBENCH_MODEL` environment
  variable.
- Added application-specific error translation for Ollama connection and
  response failures.
- Prevented failed requests from being stored in conversation history.
- Added Ruff for formatting and static analysis.
- Added pytest-based automated tests.

### Architecture

```text
Application Entry Point
        ↓
Runtime Configuration
        ↓
Interactive CLI
        ↓
Injected Completion Function
        ↓
Ollama Client
        ↓
Local Model
```

During automated testing, the real completion function is replaced by a
deterministic test implementation:

```text
Interactive CLI
        ↓
Fake Completion Function
        ↓
Deterministic Test Response
```

### Validation

The implementation was validated through:

- A real multi-turn conversation with the local model.
- A conversation-history test using a remembered code word.
- Verification that empty input does not trigger a model request.
- Verification that `/exit` and `/quit` terminate the session.
- Verification that the configured model name is read from the environment.
- Verification that blank configuration falls back to the default model.
- Verification that unavailable models produce a clear application error.
- Verification that the CLI continues running after a completion failure.
- Nine passing automated tests.
- Successful Ruff formatting and static-analysis checks.

### Technical Decisions

- Separated the CLI from the package initializer to keep the package structure
  explicit and maintainable.
- Injected the completion function into the CLI so conversation behavior can be
  tested independently of Ollama, network availability, and GPU resources.
- Used `functools.partial` to bind runtime model configuration while preserving
  the single-argument completion interface expected by the CLI.
- Stored conversation history in the application rather than relying on
  provider-side state.
- Added user messages to the in-memory conversation history only after a
  successful model response.
- Translated provider-specific exceptions into an application-level
  `CompletionError` to prevent implementation details from leaking into the
  user interface.
- Used environment-based configuration to change local models without editing
  source code.

### Current Limitations

- Ollama is still accessed directly from the CLI module.
- Only the Ollama provider is supported.
- Conversation history exists only for the current process.
- Responses are not streamed.
- Logging and structured observability are not implemented.
- Model generation parameters are not externally configurable.
- The test suite does not currently include integration tests against a live
  Ollama server.

### Next Milestone

Extract the Ollama integration behind a provider-independent interface while
preserving the existing CLI behavior and automated tests.

## 2026-07-20 — Provider-Independent Chat Architecture

### Objective

Decouple the command-line interface from the Ollama implementation and define
a reusable contract for future language model providers.

### Implemented

- Extracted the shared message structure into a dedicated module.
- Introduced the `ChatProvider` protocol as the provider-independent interface.
- Created an `OllamaProvider` implementation for local model inference.
- Moved Ollama API calls and provider-specific error translation out of the CLI.
- Replaced completion-function injection with provider-object injection.
- Updated the CLI to display both the active provider and model.
- Added a deterministic `FakeProvider` for isolated CLI testing.
- Added dedicated automated tests for the Ollama provider.
- Preserved existing conversation, configuration, and error-handling behavior.

### Architecture

```text
Application Entry Point
        ↓
Runtime Configuration
        ↓
OllamaProvider
        ↓
ChatProvider Contract
        ↓
Interactive CLI
```

At runtime, the CLI receives an object compatible with the provider contract:

```text
Interactive CLI
        ↓
ChatProvider
        ↓
OllamaProvider
        ↓
Ollama API
```

During automated testing:

```text
Interactive CLI
        ↓
FakeProvider
        ↓
Deterministic Outcomes
```

### Validation

The refactoring was validated through:

- Successful Ruff formatting and static-analysis checks.
- Ten passing automated tests.
- Dedicated tests for successful Ollama responses.
- Dedicated tests for connection and missing-model failures.
- CLI tests using a deterministic provider implementation.
- A successful real completion through the local Ollama server.
- Verification that multi-turn conversation history remained unchanged.

### Technical Decisions

- Used a Python `Protocol` to define behavior without requiring providers to
  inherit from a concrete base class.
- Kept provider-specific dependencies and exceptions outside the CLI layer.
- Assigned model configuration to the provider instance rather than passing it
  with every completion request.
- Used an immutable, slotted dataclass for `OllamaProvider` because its runtime
  configuration should not change after initialization.
- Retained application-level `CompletionError` handling so the CLI remains
  independent of provider-specific exception types.
- Replaced function-level test doubles with a fake provider that follows the
  same interface as production implementations.

### Current Limitations

- Ollama remains the only implemented provider.
- Provider selection is not yet configurable independently of model selection.
- All providers currently use the same internal message representation.
- Responses are not streamed.
- Generation parameters are not exposed through the provider interface.
- Provider capabilities are not yet represented explicitly.
- Integration tests against a live Ollama server are still manual.

### Next Milestone

Add the first cloud-based provider while preserving the provider-independent
CLI and shared message contract.


## 2026-07-21 — OpenAI Provider and Secure Runtime Selection

### Objective

Add the first cloud-based language model provider while preserving the existing
provider-independent CLI, shared message contract, and local Ollama workflow.

### Implemented

- Added the official OpenAI Python SDK.
- Implemented `OpenAIProvider` using the OpenAI Responses API.
- Added runtime selection between Ollama and OpenAI.
- Added a provider factory responsible for constructing configured providers.
- Added explicit provider and model validation.
- Required an explicit model when using the OpenAI provider.
- Added validation for the `OPENAI_API_KEY` environment variable.
- Added application-level translation for OpenAI connection, authentication,
  unavailable-model, rate-limit, and API status errors.
- Added local `.env` loading through `python-dotenv`.
- Preserved existing runtime variables by disabling `.env` overrides.
- Added a public `.env.example` without credentials.
- Updated `.gitignore` to exclude local environment files.
- Added automated tests for OpenAI behavior, provider construction,
  configuration validation, and environment loading.

### Architecture

```text
Runtime Configuration
        ↓
Provider Factory
        ↓
ChatProvider
        ├── OllamaProvider
        │       ↓
        │   Ollama Local API
        │
        └── OpenAIProvider
                ↓
        OpenAI Responses API
```

The provider factory creates only the provider selected at runtime. OpenAI
credentials are therefore required only when the OpenAI provider is selected.

### Configuration

```text
AGENT_WORKBENCH_PROVIDER
        ↓
Select Ollama or OpenAI

AGENT_WORKBENCH_MODEL
        ↓
Select the provider-specific model

OPENAI_API_KEY
        ↓
Authenticate OpenAI requests
```

Configuration is loaded in this order:

```text
Runtime Environment
        ↓
Local .env File
        ↓
Application Defaults
```

Values already present in the runtime environment are not overwritten by the
local `.env` file.

### Validation

The implementation was validated through:

- Successful Ruff formatting and static-analysis checks.
- Twenty-four passing automated tests.
- Simulated OpenAI Responses API success and failure scenarios.
- Verification of authentication, connection, unavailable-model, and
  rate-limit error translation.
- Verification that unsupported providers are rejected.
- Verification that OpenAI requires an explicit model and API key.
- Verification that Ollama remains the default provider.
- Verification that `.env` does not override runtime environment variables.
- Verification that the private `.env` file is ignored by Git.
- A successful real completion through the OpenAI Responses API.
- Confirmation that the existing Ollama workflow remains available.

### Technical Decisions

- Used the OpenAI Responses API rather than introducing a separate
  conversation implementation.
- Injected the OpenAI SDK client into `OpenAIProvider` so provider behavior can
  be tested without network access or paid requests.
- Centralized provider construction in a factory to keep the CLI independent
  of provider-specific initialization.
- Required explicit OpenAI model selection instead of silently choosing a
  cloud model with potentially different availability or cost.
- Kept Ollama as the default provider so the application remains usable
  locally without cloud credentials.
- Stored API credentials only in environment variables and ignored local
  environment files through Git.
- Used `.env.example` to document required configuration without publishing
  secrets.
- Preserved runtime environment precedence to support CI, containers, and
  future cloud deployment.

### Current Limitations

- Only Ollama and OpenAI are implemented.
- The provider is selected only at application startup.
- Responses are not streamed.
- Generation parameters are not yet configurable.
- Usage metadata and token consumption are not exposed by the provider
  abstraction.
- Provider capabilities are not represented explicitly.
- Conversation history exists only in memory for the current process.
- OpenAI integration tests use simulated clients; live API validation remains
  a manual operation.
- Logging and structured observability are not implemented.

### Next Milestone

Add Anthropic as a second cloud provider and evaluate whether the shared
message contract requires provider-specific normalization.

## 2026-07-22 — Anthropic Provider Integration

### Objective

Add Anthropic as a second cloud-based language model provider while preserving
the provider-independent CLI, the local Ollama workflow, and the existing
OpenAI integration.

### Implemented

- Added the official Anthropic Python SDK.
- Implemented `AnthropicProvider` using the Anthropic Messages API.
- Added Anthropic to runtime provider selection.
- Added Anthropic provider construction through the shared provider factory.
- Required explicit Anthropic model configuration.
- Added validation for the `ANTHROPIC_API_KEY` environment variable.
- Added application-level translation for connection, authentication,
  unavailable-model, rate-limit, and generic API status errors.
- Added support for extracting and concatenating text content blocks from
  Anthropic responses.
- Added configurable `max_tokens` to the provider implementation.
- Updated `.env.example` with the Anthropic API key variable.
- Added automated tests for provider behavior, configuration, and factory
  construction.

### Architecture

```text
Runtime Configuration
        ↓
Provider Factory
        ↓
ChatProvider
        ├── OllamaProvider
        │       ↓
        │   Ollama Local API
        │
        ├── OpenAIProvider
        │       ↓
        │   OpenAI Responses API
        │
        └── AnthropicProvider
                ↓
        Anthropic Messages API
```

### Validation

The implementation was validated through:

- Successful Ruff formatting and static-analysis checks.
- Thirty-three passing automated tests.
- Simulated Anthropic success and failure scenarios.
- Verification that text response blocks are concatenated correctly.
- Verification that non-text content blocks are ignored.
- Verification of connection, authentication, unavailable-model, and
  rate-limit error translation.
- Verification that Anthropic requires an explicit model and API key.
- Verification that no API request is made when the API key is missing.
- A successful direct request through the Anthropic Python SDK.
- A successful real completion through `AnthropicProvider`.
- Confirmation that the private `.env` file remains excluded from Git.
- Confirmation that the existing Ollama and OpenAI providers remain available.

### Technical Decisions

- Used the Anthropic Messages API as the provider-specific completion
  interface.
- Injected the Anthropic SDK client into `AnthropicProvider` so automated tests
  remain deterministic and do not make paid network requests.
- Kept Anthropic response parsing inside the provider implementation because
  the Messages API returns structured content blocks.
- Ignored non-text blocks until tool calling is represented in the shared
  provider contract.
- Made `max_tokens` a provider property because it is required by the
  Anthropic Messages API.
- Required explicit model selection instead of choosing a cloud model
  automatically.
- Kept Ollama as the default provider so the project remains usable without
  cloud credentials or API costs.

### Current Limitations

- Provider and model selection rely on environment variables rather than
  command-line arguments.
- Responses are not streamed.
- System prompts are not represented separately.
- Usage metadata and token consumption are not exposed.
- Generation parameters are not managed through a shared configuration model.
- Tool-use content blocks are currently ignored.
- Conversation history exists only in memory for the current process.
- Logging and structured observability are not implemented.
- Multiple agents are not yet coordinated by an orchestrator.

### Next Milestone

Add command-line arguments for provider and model selection so users can switch
between Ollama, OpenAI, and Anthropic without editing `.env`.

## 2026-07-22 — Command-Line Runtime Configuration

### Objective

Allow users to select a provider and model for each application execution
without editing the local `.env` file.

### Implemented

- Added command-line parsing through Python's `argparse`.
- Added the optional `--provider` argument.
- Added the optional `--model` argument.
- Restricted provider values to Ollama, OpenAI, and Anthropic.
- Added validation that rejects blank model arguments.
- Added immutable data structures for parsed arguments and resolved runtime
  configuration.
- Added explicit configuration precedence between command-line arguments,
  environment variables, `.env`, and application defaults.
- Required `--model` whenever `--provider` is supplied.
- Updated the CLI entry point to resolve configuration before constructing the
  selected provider.
- Added automated tests for parsing, validation, environment fallback, and CLI
  precedence.

### Configuration Precedence

```text
Command-Line Arguments
        ↓
Runtime Environment Variables
        ↓
Local .env File
        ↓
Application Defaults
```

For example, a local `.env` can keep Anthropic as its configured provider while
a single execution temporarily selects Ollama:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b
```

The `.env` file is not modified by this command.

### Validation

The implementation was validated through:

- Successful Ruff formatting and static-analysis checks.
- Thirty-nine passing automated tests.
- Verification that supported provider names are displayed in `--help`.
- Verification that unsupported providers are rejected by `argparse`.
- Verification that blank model values are rejected.
- Verification that command-line arguments override environment configuration.
- Verification that environment configuration is used when CLI arguments are
  absent.
- Verification that a model can be overridden while retaining the configured
  provider.
- Verification that `--provider` without `--model` produces a clear
  configuration error.
- A successful Ollama execution while Anthropic remained configured in
  `.env`.

### Technical Decisions

- Used the Python standard-library `argparse` module instead of introducing an
  additional CLI dependency.
- Separated argument parsing from runtime configuration resolution so each
  responsibility can be tested independently.
- Represented parsed and resolved configuration using immutable, slotted
  dataclasses.
- Required a model whenever the provider is overridden to prevent
  provider-model mismatches.
- Preserved environment configuration as the fallback so existing `.env`, CI,
  container, and cloud workflows continue to work.
- Kept provider construction inside the existing provider factory.

### Current Limitations

- The CLI supports only provider and model overrides.
- Provider-specific generation parameters are not exposed.
- API keys cannot be supplied through command-line arguments.
- Responses are not streamed.
- Configuration profiles are not implemented.
- Conversation history exists only in memory.
- Multiple agents are not yet coordinated by an orchestrator.
- Logging and structured observability are not implemented.

### Next Milestone

Introduce structured system prompts and conversation roles before adding tool
calling and multi-agent orchestration.

## 2026-07-22 — Provider-Independent Chat Requests and System Prompts

### Objective

Introduce a provider-independent request model and allow users to define the
assistant's role and behavior through a system prompt.

### Implemented

- Added the immutable `ChatRequest` data structure.
- Separated system instructions from conversation messages.
- Updated the `ChatProvider` protocol to accept `ChatRequest`.
- Updated Ollama, OpenAI, and Anthropic providers to translate shared requests
  into their provider-specific formats.
- Added Ollama system instructions through a `system` message.
- Added OpenAI system instructions through the Responses API `instructions`
  parameter.
- Added Anthropic system instructions through the Messages API `system`
  parameter.
- Updated the interactive CLI to construct provider-independent chat requests.
- Added the `--system-prompt` command-line argument.
- Added validation that rejects blank system prompts.
- Forwarded the system prompt with every completion request in the session.
- Kept system instructions outside user and assistant conversation history.
- Updated automated provider and CLI tests for the new request contract.

### Architecture

```text
Command-Line Configuration
        ↓
System Prompt
        ↓
Interactive CLI
        ↓
ChatRequest
├── system_prompt
└── messages
        ↓
ChatProvider
        ├── OllamaProvider
        │       ↓
        │   system message
        │
        ├── OpenAIProvider
        │       ↓
        │   instructions parameter
        │
        └── AnthropicProvider
                ↓
            system parameter
```

### Conversation State

The system prompt is configuration rather than conversation history:

```text
System Prompt
    └── Defines identity and behavior

Conversation History
    ├── User message
    ├── Assistant message
    ├── User message
    └── Assistant message
```

The same system prompt accompanies each request, while only successful user
and assistant messages are preserved in the session history.

### Validation

The implementation was validated through:

- Successful Ruff formatting and static-analysis checks.
- Forty-two passing automated tests.
- Verification that all providers accept the shared `ChatRequest`.
- Verification that Ollama receives a system message before conversation
  messages.
- Verification that OpenAI receives system instructions separately from input
  messages.
- Verification that Anthropic receives the dedicated system parameter.
- Verification that provider errors continue to use `CompletionError`.
- Verification that blank system prompt arguments are rejected.
- Verification that the system prompt is forwarded with every request.
- Verification that system instructions do not enter conversation history.
- A successful real Ollama completion using a software reviewer identity.
- Confirmation that the existing provider and model CLI arguments remain
  functional.

### Technical Decisions

- Represented system instructions separately from the `Message` type because
  they describe agent configuration rather than conversation history.
- Removed the `system` role from the shared conversation message contract.
- Used a provider-independent `ChatRequest` instead of adding provider-specific
  parameters to the CLI.
- Kept provider translation inside each provider implementation.
- Used an immutable, slotted dataclass for `ChatRequest`.
- Avoided storing the system prompt repeatedly in the in-memory message list.
- Added system prompts as runtime configuration before introducing persistent
  agent profiles.

### Current Limitations

- System prompts are supplied as raw command-line text.
- Reusable agent profiles are not yet implemented.
- System prompts cannot yet be loaded from dedicated files.
- Provider-specific generation parameters are not exposed.
- Responses are not streamed.
- Conversation history exists only in memory.
- Tool calling is not implemented.
- Multiple agents are not yet coordinated by an orchestrator.
- Logging and structured observability are not implemented.

### Next Milestone

Introduce reusable agent profiles so named roles such as `Planner`,
`Developer`, `Reviewer`, and `Tester` can define their own system prompts and
later receive provider, model, and tool configuration.

## 2026-07-22 — Reusable Agent Profiles

### Objective

Introduce reusable agent identities that define consistent responsibilities,
descriptions, and system instructions independently of the selected model
provider.

### Implemented

- Added the immutable `AgentProfile` data structure.
- Added built-in `planner`, `developer`, `reviewer`, and `tester` profiles.
- Added a centralized agent profile registry.
- Added normalized profile lookup.
- Added clear validation for unsupported agent names.
- Added the `--agent` command-line argument.
- Restricted the argument to registered agent profiles.
- Resolved agent profiles into system prompts during runtime configuration.
- Prevented simultaneous use of `--agent` and `--system-prompt`.
- Added the active agent name to the CLI session header.
- Added the agent role description to the session startup output.
- Kept agent identity independent from provider and model selection.
- Added automated tests for profile registration, lookup, configuration, and
  CLI presentation.

### Built-In Profiles

```text
planner
    └── Plans tasks, dependencies, risks, assumptions, and acceptance criteria

developer
    └── Designs and implements maintainable, testable, and secure software

reviewer
    └── Reviews correctness, security, maintainability, tests, and edge cases

tester
    └── Designs tests and investigates failures, regressions, and assumptions
```

### Architecture

```text
Command-Line Arguments
        ↓
Agent Profile Registry
        ↓
AgentProfile
├── name
├── description
└── system_prompt
        ↓
Runtime Configuration
        ↓
Interactive CLI
        ↓
ChatRequest
        ↓
Selected Provider
```

Agent profiles do not contain provider-specific behavior. The same profile can
be combined with Ollama, OpenAI, or Anthropic.

### Validation

The implementation was validated through:

- Successful Ruff formatting and static-analysis checks.
- Fifty passing automated tests.
- Verification that all four built-in profiles are registered.
- Verification that every profile contains a name, description, and system
  prompt.
- Verification that profile names are normalized before lookup.
- Verification that unknown profile names are rejected.
- Verification that `--help` lists all supported agent profiles.
- Verification that the selected profile provides the runtime system prompt.
- Verification that `--agent` and `--system-prompt` cannot be combined.
- Verification that the active profile name and description appear in the CLI.
- A successful real Ollama conversation using the `reviewer` profile.
- Confirmation that the reviewer identified correctness, edge-case, and
  testing concerns in a Python division expression.

### Technical Decisions

- Stored built-in profiles in a centralized registry so discovery, validation,
  and lookup use the same source of truth.
- Used immutable, slotted dataclasses for agent profile definitions.
- Kept provider and model configuration outside `AgentProfile` in this first
  version so roles remain reusable across providers.
- Reused the existing system prompt pipeline instead of adding agent-specific
  logic to providers.
- Rejected simultaneous custom prompts and profiles to avoid unclear
  instruction precedence.
- Displayed the agent identity at session startup without changing the generic
  session termination message.
- Started with software engineering roles that can later participate in a
  coordinated multi-agent workflow.

### Current Limitations

- Only built-in profiles defined in Python are available.
- Profiles cannot yet be loaded from YAML, TOML, or JSON files.
- Profiles do not define provider, model, tools, or generation parameters.
- Only one agent profile can be active in each CLI process.
- Separate CLI processes are not coordinated.
- Agents do not share state, tasks, or conversation history.
- Tool calling is not implemented.
- Responses are not streamed.
- Logging and structured observability are not implemented.

### Next Milestone

Move agent definitions into external configuration files so users can create
and modify profiles without editing Python source code.

## 2026-07-23 — External Agent Profile Configuration

### Objective

Separate agent definitions from Python source code and allow users to load
custom agent identities and instructions from external TOML files.

### Implemented

- Moved the built-in `planner`, `developer`, `reviewer`, and `tester` profiles
  into packaged TOML resources.
- Added a TOML parser for agent profile definitions.
- Added validation for malformed TOML content.
- Added validation for missing required fields.
- Added validation for blank field values.
- Added validation for unsupported fields.
- Added loading of built-in resources through `importlib.resources`.
- Verified that packaged profiles are included in the generated wheel.
- Added loading of external UTF-8 TOML profile files.
- Added validation for missing files, directories, and unsupported file
  extensions.
- Added the `--agent-file` command-line argument.
- Resolved custom profile system instructions into runtime configuration.
- Prevented ambiguous combinations between built-in profiles, external
  profiles, and direct system prompts.
- Preserved compatibility with Ollama, OpenAI, and Anthropic providers.
- Added automated tests for profile parsing, filesystem loading, CLI parsing,
  and runtime configuration.

### Built-In Profile Resources

```text
src/agent_workbench/profiles/
├── __init__.py
├── developer.toml
├── planner.toml
├── reviewer.toml
└── tester.toml
```

The profile filename provides the built-in CLI identifier:

```text
reviewer.toml
      ↓
--agent reviewer
```

The file content provides the agent identity and behavior:

```toml
name = "Reviewer"
description = "Reviews software quality and risks."
system_prompt = "You are a strict software review agent."
```

### Custom Profile Flow

```text
--agent-file ./custom-agent.toml
                 ↓
        Validate file path
                 ↓
          Read UTF-8 TOML
                 ↓
       Parse and validate fields
                 ↓
            AgentProfile
                 ↓
       Runtime system prompt
                 ↓
         Selected provider
```

### Required Fields

Every profile must contain exactly:

```text
name
description
system_prompt
```

All fields must contain non-empty strings.

Unknown fields are rejected so spelling mistakes and unsupported configuration
do not pass silently.

### Configuration Precedence

The application accepts one agent instruction source per session:

```text
Built-in profile
    OR
External profile file
    OR
Direct system prompt
```

The following combinations are rejected:

```text
--agent + --agent-file
--agent + --system-prompt
--agent-file + --system-prompt
```

### Validation

The implementation was validated through:

- Successful Ruff formatting and static-analysis checks.
- Sixty-four passing automated tests.
- Verification that all four packaged TOML profiles are discovered.
- Verification that valid TOML content creates an `AgentProfile`.
- Verification that malformed TOML is rejected.
- Verification that missing, blank, and unsupported fields are rejected.
- Verification that missing files are rejected.
- Verification that directories cannot be used as profile files.
- Verification that custom profiles require the `.toml` extension.
- Verification that `--agent-file` appears in CLI help.
- Verification that custom profiles provide the runtime system prompt.
- Verification that conflicting profile arguments are rejected.
- Verification that the built-in TOML files are included in the generated
  wheel.
- A successful real Ollama session using an external `Security Reviewer`
  profile.
- Confirmation that the external system prompt changed the model behavior.

### Technical Decisions

- Used `tomllib` from the Python 3.12 standard library, avoiding another
  runtime dependency.
- Used `importlib.resources` instead of filesystem-relative paths for packaged
  resources.
- Kept the immutable `AgentProfile` data structure as the provider-independent
  representation.
- Used strict field validation to detect configuration mistakes early.
- Kept provider and model configuration outside the profile format.
- Added `--agent-file` rather than automatically scanning directories in this
  milestone, keeping profile selection explicit and predictable.
- Preserved the same system prompt pipeline for built-in profiles, custom
  profiles, and direct instructions.
- Prepared the profile loader for a future interactive configuration mode.

### Current Limitations

- Custom profiles must be selected through an explicit file path.
- The application does not yet discover profiles from a default user
  directory.
- Profiles cannot yet configure provider, model, temperature, tools, or
  context sources.
- File-based project context is not implemented.
- Only one profile can be active in each CLI session.
- The application does not yet provide an interactive selection wizard.
- Agents are not coordinated through an orchestrator.
- Conversation state is not persisted between sessions.

### Next Milestone

Add file-based context so users can provide source code, documentation, and
other text files to the active agent.

After context support is stable, add an interactive setup flow for selecting
the provider, model, agent profile, and context sources without requiring the
user to memorize command-line arguments.

## 2026-07-24 — File-Based Conversation Context

### Objective

Allow users to supply source code, documentation, configuration, and other
text files as provider-independent reference material for an interactive
conversation.

### Implemented

- Added the immutable `ContextDocument` data structure.
- Added local context file loading through the repeatable `--context-file`
  command-line argument.
- Preserved the order in which multiple context files are supplied.
- Added support for `.txt`, `.md`, `.py`, `.toml`, `.json`, `.yaml`, and `.yml`
  files.
- Added validation for missing files and directory paths.
- Added validation for unsupported file extensions.
- Added UTF-8 decoding validation.
- Rejected empty and whitespace-only context files.
- Added a 100 KiB individual file-size limit.
- Added context documents to `RuntimeConfiguration`.
- Added provider-independent context documents to `ChatRequest`.
- Kept context documents separate from user and assistant conversation history.
- Added shared formatting for context documents and their source paths.
- Combined system prompts and context documents through a common instruction
  builder.
- Added provider-specific context translation for Ollama, OpenAI, and
  Anthropic.
- Added automated tests for file loading, validation, formatting, runtime
  configuration, CLI forwarding, request behavior, and provider translation.

### Architecture

```text
--context-file
      ↓
CLIArguments.context_files
      ↓
Context Document Loader
      ↓
ContextDocument
├── source
└── content
      ↓
RuntimeConfiguration.context_documents
      ↓
Interactive CLI
      ↓
ChatRequest
├── system_prompt
├── context_documents
└── messages
      ↓
Provider Adapter
      ├── OllamaProvider
      │       ↓
      │   system message
      │
      ├── OpenAIProvider
      │       ↓
      │   instructions parameter
      │
      └── AnthropicProvider
              ↓
          system parameter
```

### Context Instruction Structure

The active system prompt remains separate from the loaded documents until the
provider request is constructed.

When context documents are present, the shared instruction builder produces a
structure similar to:

```text
Active system prompt

Reference-data instruction

<context_document source="README.md">
Document contents
</context_document>

<context_document source="pyproject.toml">
Document contents
</context_document>
```

Each document is identified by its source path. Characters that could break
the source attribute are escaped, while the original document contents are
preserved.

Context documents are labelled as reference data and are not added to the
`user` and `assistant` conversation history.

### Validation

The implementation was validated through:

- Successful Ruff formatting and static-analysis checks.
- Ninety-five passing automated tests.
- Verification of every supported context file extension.
- Verification that uppercase supported extensions are accepted.
- Verification that original whitespace and line breaks are preserved.
- Verification that missing files and directories are rejected.
- Verification that unsupported file extensions are rejected.
- Verification that invalid UTF-8 content is rejected.
- Verification that blank and whitespace-only context files are rejected.
- Verification that files larger than 100 KiB are rejected.
- Verification that a file exactly at the 100 KiB limit is accepted.
- Verification that repeated `--context-file` arguments preserve order.
- Verification that context documents remain outside conversation history.
- Verification that Ollama receives context through a system message.
- Verification that OpenAI receives context through the `instructions`
  parameter.
- Verification that Anthropic receives context through the `system` parameter.
- Verification that automated provider tests use simulated clients and make no
  paid API requests.
- A successful real Ollama conversation using one context file and a built-in
  reviewer profile.
- A successful real Ollama conversation using two ordered context files.
- Verification that the model recovered distinct values from both supplied
  documents.
- Verification that missing and unsupported files produce clear CLI
  configuration errors.
- Confirmation that no temporary validation files remained in the repository.

### Technical Decisions

- Used `pathlib.Path` for filesystem-independent path handling.
- Used immutable, slotted dataclasses for context documents and shared
  requests.
- Used tuples to preserve document order and prevent accidental collection
  mutation.
- Used Python standard-library functionality for file loading and context
  formatting.
- Applied the individual file-size limit before reading the complete file into
  memory.
- Preserved original document content and used whitespace normalization only
  to detect empty files.
- Centralized context formatting so all providers receive the same logical
  representation.
- Kept provider-specific translation inside each provider adapter.
- Reused provider-native system instruction channels instead of adding context
  to user messages.
- Kept context documents separate from conversation history because they are
  runtime reference material rather than conversational turns.
- Sent complete documents in this milestone to establish a simple and testable
  baseline before introducing retrieval.
- Avoided embeddings and vector database dependencies until the RAG milestone.

### Current Limitations

- Every selected file is sent in full with every model request.
- The 100 KiB limit applies independently rather than through a total context
  budget.
- Token usage is not estimated before provider requests.
- Files are selected only through explicit command-line paths.
- Directories cannot be scanned recursively.
- Binary documents, PDFs, and office formats are not supported.
- Context documents are loaded only when the application starts.
- Context cannot be added or removed during an active session.
- Embeddings, chunking, semantic retrieval, and vector storage are not
  implemented.
- Provider context-window limits are not represented through the shared
  configuration model.
- Responses are not streamed.
- Conversation history remains in memory only.
- Logging and structured observability are not implemented.

### Next Milestone

Introduce a provider-independent generation configuration model for parameters
such as temperature, top-p, maximum output tokens, stop sequences, and
provider-supported options.

After generation configuration is stable, add an interactive setup wizard so
users can select providers, models, agents, context files, and inference
presets without memorizing command-line arguments.


## 2026-07-24 — Provider-Independent Generation Configuration

### Objective

Allow users to control common text-generation parameters without exposing the
interactive CLI and conversation layer to provider-specific API names.

### Implemented

* Added the immutable `GenerationConfig` data structure.
* Added optional provider-independent `temperature`, `top_p`, and
  `max_output_tokens` fields.
* Added validation for portable sampling values between `0.0` and `1.0`.
* Added validation requiring maximum output tokens to be a positive integer.
* Explicitly rejected boolean values from numeric generation fields.
* Added `GenerationConfig` to `ChatRequest`.
* Added generation configuration to `RuntimeConfiguration`.
* Forwarded generation settings separately from conversation history.
* Added the `--temperature` command-line argument.
* Added the `--top-p` command-line argument.
* Added the `--max-output-tokens` command-line argument.
* Added CLI parsing and early validation for generation values.
* Preserved provider defaults when optional parameters are not supplied.
* Added generation translation for Ollama.
* Added generation translation for the OpenAI Responses API.
* Added generation translation for the Anthropic Messages API.
* Added typed provider request argument structures.
* Added automated tests for validation, CLI parsing, runtime resolution,
  request forwarding, and provider translation.

### Architecture

```text
--temperature
--top-p
--max-output-tokens
        ↓
CLIArguments
        ↓
GenerationConfig
├── temperature
├── top_p
└── max_output_tokens
        ↓
RuntimeConfiguration.generation_config
        ↓
Interactive CLI
        ↓
ChatRequest.generation_config
        ↓
Provider Adapter
```

Generation configuration remains separate from:

```text
ChatRequest
├── system_prompt
├── context_documents
├── generation_config
└── messages
```

Generation parameters are runtime request configuration rather than
conversation messages or model instructions.

### Provider Translation

```text
GenerationConfig
├── OllamaProvider
│   ├── temperature → options["temperature"]
│   ├── top_p → options["top_p"]
│   └── max_output_tokens → options["num_predict"]
│
├── OpenAIProvider
│   ├── temperature → temperature
│   ├── top_p → top_p
│   └── max_output_tokens → max_output_tokens
│
└── AnthropicProvider
    ├── temperature → temperature
    ├── top_p → top_p
    └── max_output_tokens → max_tokens
```

Ollama and OpenAI generation parameters are omitted completely when they are
not configured, preserving provider and model defaults.

Anthropic requires `max_tokens` in every Messages API request. When the shared
maximum output value is absent, the provider continues to use its existing
default of `1024`.

### Validation

The implementation was validated through:

* Successful Ruff formatting and static-analysis checks.
* One hundred and thirty-four passing automated tests.
* Verification that all generation parameters remain optional.
* Verification that default `GenerationConfig` values are `None`.
* Verification that temperature accepts boundary values `0.0` and `1.0`.
* Verification that top-p accepts boundary values `0.0` and `1.0`.
* Verification that values outside the portable interval are rejected.
* Verification that non-numeric sampling values are rejected by the CLI.
* Verification that maximum output tokens must be a positive integer.
* Verification that floating-point, zero, negative, string, and boolean token
  limits are rejected.
* Verification that CLI arguments are preserved in runtime configuration.
* Verification that generation settings are forwarded separately from
  conversation history.
* Verification that omitted generation parameters do not alter Ollama or
  OpenAI provider calls.
* Verification that Ollama receives generation parameters through `options`.
* Verification that OpenAI receives native Responses API arguments.
* Verification that Anthropic receives native Messages API arguments.
* Verification that Anthropic preserves its provider output-token default when
  no shared limit is supplied.
* Verification that provider tests use simulated clients and make no paid API
  calls.
* A successful real Ollama session using `temperature=0.0`, `top_p=1.0`, and
  `max_output_tokens=256`.
* Confirmation that `gpt-oss:20b` returned the exact expected text
  `GENERATION-CONFIG-OK`.
* Observation that a `32`-token output budget produced empty final content for
  the same reasoning model.
* Confirmation that increasing the output budget to `256` allowed the model to
  produce its final answer.

### Technical Decisions

* Used a dedicated provider-independent configuration object instead of
  adding provider-specific parameters directly to `ChatRequest`.
* Used an immutable, slotted dataclass to prevent configuration mutation during
  a session.
* Kept every shared generation field optional so existing provider behavior
  remains unchanged.
* Used a portable `0.0` to `1.0` interval for temperature and top-p.
* Validated values both at the CLI boundary and inside `GenerationConfig`.
* Explicitly rejected booleans because Python treats `bool` as a subclass of
  `int`.
* Used `max_output_tokens` as the shared semantic name even though providers
  use different native names.
* Kept provider-specific translation inside each provider adapter.
* Used typed dictionaries and `Unpack` for dynamically constructed OpenAI and
  Anthropic keyword arguments.
* Omitted optional keyword arguments instead of sending explicit `None`
  values.
* Preserved the existing Anthropic provider fallback because its API requires
  a maximum output-token value.
* Limited the first implementation to common provider-independent parameters.
* Deferred stop sequences and provider-specific generation controls until a
  later milestone.

### Current Limitations

* Only temperature, top-p, and maximum output tokens are represented.
* Stop sequences are not supported by the shared configuration.
* Reasoning effort and thinking-budget controls are not represented.
* Seeds and deterministic generation controls are not represented.
* Provider-specific parameters cannot be supplied through the shared CLI.
* Model-specific parameter compatibility is not detected before the provider
  request.
* The application does not estimate whether the requested output limit is
  appropriate for the selected model.
* Very small output-token limits may prevent reasoning models from producing
  final response content.
* Generation presets cannot yet be stored in agent profile files.
* Generation settings cannot be changed during an active conversation.
* Responses are not streamed.
* Token usage and generation metadata are not displayed.
* Conversation state remains in memory only.
* Logging and structured observability are not implemented.

### Next Milestone

Add an interactive runtime setup flow so users can select a provider, model,
agent profile, context files, and generation settings without memorizing
command-line arguments.

After the interactive setup flow is stable, continue toward structured model
outputs, tool calling, Retrieval-Augmented Generation, agent orchestration,
evaluation, observability, and deployment.
