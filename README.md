# Agent Workbench

An incremental AI engineering project for learning how to build applications
that interact with local and cloud-based language models.

## Current Status

The current version connects a Python application to a local language model
running through Ollama.

```text
Python Application
        ↓
Ollama Python Client
        ↓
Ollama Local API
        ↓
Local Language Model
```

## Requirements

- Python 3.12
- uv
- Ollama
- The `gpt-oss:20b` model
- Sufficient system memory or a compatible GPU

## Setup

Install the project dependencies:

```bash
uv sync
```

Download the local model:

```bash
ollama pull gpt-oss:20b
```

## Usage

Run the application:

```bash
uv run agent-workbench
```

Expected output:

```text
Python connected to the local model
```

## Roadmap

- [x] Connect Python to a local Ollama model
- [ ] Add an interactive command-line interface
- [ ] Create a common interface for multiple model providers
- [ ] Add OpenAI integration
- [ ] Add Anthropic integration
- [ ] Add structured outputs
- [ ] Implement tool calling
- [ ] Build a local RAG pipeline
- [ ] Add evaluations and logging
- [ ] Explore MCP integrations
- [ ] Build workflows with LangGraph
- [ ] Containerize the application
- [ ] Deploy a cloud version to AWS

## Author

Developed by Rafael Silva as part of a practical AI engineering learning path.

## License

Copyright © 2026 Rafael Silva.

Licensed under the Apache License 2.0.
