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
- Provider and model selection through command-line arguments
- Configuration precedence between CLI arguments, environment variables, and defaults
- Provider-independent requests through the `ChatRequest` abstraction
- System prompt configuration through the `--system-prompt` argument
- Provider-specific translation of system instructions

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

## Runtime Configuration

Provider and model configuration can be supplied through command-line
arguments or environment variables.

The application uses the following precedence:

```text
Command-Line Arguments
        ↓
Runtime Environment Variables
        ↓
Local .env File
        ↓
Application Defaults
```

Command-line arguments therefore allow temporary provider changes without
editing `.env`.

When `--provider` is specified, `--model` must also be supplied. This prevents
a model configured for one provider from being reused accidentally with
another provider.

## System Prompts

A system prompt defines the assistant's role, behavior, and operating
instructions for the entire conversation.

Provide one through the command line:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --system-prompt "You are a strict software reviewer."
```

System prompts are represented separately from conversation history:

```text
ChatRequest
├── system_prompt
└── messages
    ├── user
    └── assistant
```

Each provider translates the shared request into its native API format:

```text
ChatRequest.system_prompt
        ↓
Provider Adapter
        ├── Ollama: system message
        ├── OpenAI: instructions
        └── Anthropic: system parameter
```

The system prompt is included with every model request in the session but is
not stored as a user or assistant conversation message.

This separation provides the foundation for reusable agent identities such as
`Planner`, `Developer`, `Reviewer`, and `Tester`.

## Usage

Start the CLI using the configuration stored in `.env`:

```bash
uv run agent-workbench
```

Display the available command-line options:

```bash
uv run agent-workbench --help
```

Use Ollama without changing `.env`:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b
```

Use OpenAI:

```bash
uv run agent-workbench \
  --provider openai \
  --model <openai-model>
```

Use Anthropic:

```bash
uv run agent-workbench \
  --provider anthropic \
  --model <anthropic-model>
```

A model can also be overridden while keeping the provider configured through
the environment:

```bash
uv run agent-workbench --model <provider-specific-model>
```

Example session:

```text
Agent Workbench | Provider: Ollama | Model: gpt-oss:20b
Type /exit or /quit to end the session.

You: Remember the code word cobalt.
Assistant: Understood.

You: What was the code word?
Assistant: cobalt

You: /exit
Session ended.
```

Empty input is ignored. Use `/exit` or `/quit` to end the session.

Use a temporary system prompt without modifying `.env`:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --system-prompt \
  "You are a software reviewer. Focus on correctness, security, and maintainability."
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
- System prompts are runtime instructions and are not persisted automatically.

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
- [x] Add command-line provider and model selection
- [x] Add a provider-independent chat request abstraction
- [x] Add system prompt configuration
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