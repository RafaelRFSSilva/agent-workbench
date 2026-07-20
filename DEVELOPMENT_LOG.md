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