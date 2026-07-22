# Agent Workbench

An incremental AI engineering workbench for building and evaluating
applications powered by local and cloud-based language models.

The project evolves through provider abstraction, structured outputs, tool
calling, Retrieval-Augmented Generation, agent workflows, evaluation,
observability, and cloud deployment.

## Current Status

The current version provides an interactive command-line interface built on a
provider-independent chat architecture.

Implemented capabilities:

- Interactive multi-turn conversations
- In-memory conversation history
- Provider-independent integration through the `ChatProvider` protocol
- Local inference through `OllamaProvider`
- Cloud inference through `OpenAIProvider`
- Cloud inference through `AnthropicProvider`
- Runtime provider and model selection
- Local configuration through environment variables
- Secure `.env` loading without overriding runtime variables
- Clear provider-specific error handling
- Automated tests for CLI, configuration, provider construction, and API behavior
- Continuous integration with GitHub Actions
- Static analysis and formatting with Ruff

## Architecture

```text
User
  ↓
Interactive CLI
  ↓
Provider Factory
  ↓
ChatProvider Protocol
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

The CLI depends only on the `ChatProvider` protocol. Provider-specific clients,
authentication, API calls, and error translation remain outside the
conversation layer.

During automated testing, real providers and external APIs are replaced by
deterministic test doubles.

## Requirements

### Core

- Python 3.12
- uv

### Ollama provider

- Ollama
- A locally available Ollama model
- Sufficient system memory or a compatible GPU

### OpenAI provider

- An OpenAI API project
- An API key with access to the Responses API
- Available API credit
- An explicitly configured OpenAI model

### Anthropic provider

- An Anthropic Console workspace
- An API key with access to the Messages API
- Available API credit
- An explicitly configured Anthropic model

## Setup

Install the project dependencies:

```bash
uv sync
```

Create a local environment file from the public template:

```bash
cp .env.example .env
```

The `.env` file is excluded from Git and must never be committed.

## Ollama Configuration

The default configuration uses Ollama with `gpt-oss:20b`.

Download the model:

```bash
ollama pull gpt-oss:20b
```

Confirm that Ollama is running and the model is available:

```bash
ollama list
```

Example `.env` configuration:

```dotenv
AGENT_WORKBENCH_PROVIDER=ollama
AGENT_WORKBENCH_MODEL=gpt-oss:20b
```

## OpenAI Configuration

Configure the OpenAI provider in `.env`:

```dotenv
OPENAI_API_KEY=<your-api-key>
AGENT_WORKBENCH_PROVIDER=openai
AGENT_WORKBENCH_MODEL=<openai-model>
```

The API key must remain only in the private `.env` file or another secure
runtime environment.

Runtime environment variables take precedence over values loaded from `.env`.

## Anthropic Configuration

Configure the Anthropic provider in `.env`:

```dotenv
ANTHROPIC_API_KEY=<your-api-key>
AGENT_WORKBENCH_PROVIDER=anthropic
AGENT_WORKBENCH_MODEL=<anthropic-model>
```

The API key must remain only in the private `.env` file or another secure
runtime environment.

## Usage

Start the interactive CLI:

```bash
uv run agent-workbench
```

Example:

```text
Agent Workbench | Provider: OpenAI | Model: <openai-model>
Type /exit or /quit to end the session.

You: Remember the code word cobalt.
Assistant: Understood.

You: What was the code word?
Assistant: cobalt

You: /exit
Session ended.
```

Empty input is ignored. Use `/exit` or `/quit` to end the session.

Configuration can also be supplied for a single command:

```bash
AGENT_WORKBENCH_PROVIDER=ollama \
AGENT_WORKBENCH_MODEL=gpt-oss:20b \
uv run agent-workbench
```

## Quality Checks

Run the automated tests:

```bash
uv run pytest -q
```

Run static analysis:

```bash
uv run ruff check .
```

Verify formatting:

```bash
uv run ruff format --check .
```

## Security

- API keys are never stored in source code.
- `.env` and related local environment files are ignored by Git.
- `.env.example` contains variable names only and no secrets.
- Existing runtime environment variables are not overwritten by `.env`.
- Automated tests use simulated SDK clients and do not make paid API requests.

## Roadmap

- [x] Connect Python to a local Ollama model
- [x] Add an interactive command-line interface
- [x] Preserve multi-turn conversation history
- [x] Add automated tests and error handling
- [x] Create a common interface for multiple model providers
- [x] Move the Ollama integration behind a dedicated provider
- [x] Add runtime provider and model selection
- [x] Add OpenAI Responses API integration
- [x] Add secure local environment configuration
- [x] Add Anthropic Messages API integration
- [ ] Add structured outputs
- [ ] Implement tool calling
- [ ] Build a local RAG pipeline
- [ ] Add evaluations, logging, and observability
- [ ] Explore MCP integrations
- [ ] Build workflows with LangGraph
- [ ] Containerize the application
- [ ] Deploy a cloud version to AWS

## Author

Developed and maintained by Rafael Silva.

## License

Copyright © 2026 Rafael Silva.

Licensed under the Apache License 2.0.