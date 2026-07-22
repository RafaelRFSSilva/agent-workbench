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