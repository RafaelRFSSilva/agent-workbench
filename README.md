# Agent Workbench

An incremental AI engineering workbench for building and evaluating
applications powered by local and cloud-based language models.

The project begins with a direct Ollama integration and evolves through
provider abstraction, structured outputs, tool calling, Retrieval-Augmented
Generation, agent workflows, evaluation, observability, and cloud deployment.

## Current Status

The current version provides an interactive command-line interface built on a
provider-independent chat abstraction.

Implemented capabilities:

- Interactive multi-turn conversations
- In-memory conversation history
- Provider-independent CLI integration through the `ChatProvider` protocol
- Local model inference through the `OllamaProvider`
- Configurable local model selection
- Empty-input and session-exit handling
- Clear handling of Ollama connection and model errors
- Automated tests for CLI, configuration, and provider behavior
- Continuous integration with GitHub Actions
- Static analysis and formatting with Ruff

## Architecture

```text
User
  ↓
Interactive CLI
  ↓
ChatProvider Protocol
  ↓
OllamaProvider
  ↓
Ollama Python Client
  ↓
Ollama Local API
  ↓
Local Language Model

The CLI depends on an injected completion function, allowing the conversation
logic to be tested without contacting a real model server.

## Requirements

- Python 3.12
- uv
- Ollama
- A locally available Ollama model
- Sufficient system memory or a compatible GPU

The default model is:

```text
gpt-oss:20b
```

## Setup

Install the project dependencies:

```bash
uv sync
```

Download the default local model:

```bash
ollama pull gpt-oss:20b
```

Confirm that Ollama is running and that the model is available:

```bash
ollama list
```

## Usage

Start the interactive CLI:

```bash
uv run agent-workbench
```

Example session:

```text
Agent Workbench | Local model: gpt-oss:20b
Type /exit or /quit to end the session.

You: Remember the code word cobalt.
Assistant: Understood.

You: What was the code word?
Assistant: cobalt

You: /exit
Session ended.
```

Empty input is ignored. Use `/exit` or `/quit` to end the session.

## Model Configuration

Set `AGENT_WORKBENCH_MODEL` to use another model already available in Ollama:

```bash
AGENT_WORKBENCH_MODEL=<model-name> uv run agent-workbench
```

Example:

```bash
AGENT_WORKBENCH_MODEL=<model-name> uv run agent-workbench
```

When the variable is missing or blank, the application uses `gpt-oss:20b`.

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

## Roadmap

- [x] Connect Python to a local Ollama model
- [x] Add an interactive command-line interface
- [x] Preserve multi-turn conversation history
- [x] Add configurable local model selection
- [x] Add automated tests and error handling
- [x] Create a common interface for multiple model providers
- [x] Move the Ollama integration behind a dedicated provider
- [ ] Add OpenAI integration
- [ ] Add Anthropic integration
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