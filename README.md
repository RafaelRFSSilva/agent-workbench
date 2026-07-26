# Agent Workbench

Agent Workbench is a local-first AI engineering workspace for building,
configuring, and eventually orchestrating software-development agents powered
by local and cloud language models.

The current project is a provider-independent command-line application. Its
long-term direction is a multi-agent workspace inside VS Code where developers
can coordinate specialised agents, project context, tools, permissions, MCP
servers, and local or cloud models.

## Current Status

The current version supports one interactive conversation session at a time.

Implemented capabilities:

- Local inference through Ollama.
- Cloud inference through OpenAI and Anthropic.
- Provider-independent requests through `ChatProvider` and `ChatRequest`.
- Interactive multi-turn conversations with in-memory history.
- Runtime provider and model selection.
- Secure `.env` configuration.
- System prompts.
- Built-in and custom TOML agent profiles.
- Explicit file-based context.
- Provider-independent generation settings.
- Prompt-based interactive setup.
- Provider-independent structured outputs.
- Provider-independent tool calling with opt-in built-in tools.
- Automated tests, Ruff checks, and GitHub Actions.

Filesystem and network tools, project-wide access, RAG, MCP, asynchronous
execution, user-defined tools, multiple simultaneous agents, and the VS Code
interface are planned but not yet implemented.

## Product Direction

```text
VS Code Workspace
├── Planner Agent
├── Developer Agent
├── Reviewer Agent
├── Tester Agent
├── Project Files and Git State
├── Native and MCP Tools
├── Tasks and Agent Handoffs
└── Human Approval and Orchestration
```

Each future agent session may use its own profile, provider, model,
instructions, generation settings, workspace scope, tools, permissions,
conversation, task, and status.

The user remains responsible for permissions, approvals, and final decisions.

See [Product Vision](docs/product-vision.md).

## Architecture

```text
User
  ↓
CLI or Interactive Setup
  ↓
RuntimeConfiguration
  ↓
Provider Factory
  ↓
ChatProvider Protocol
  ├── OllamaProvider
  ├── OpenAIProvider
  └── AnthropicProvider
        ↓
  Local or Cloud Model
```

Provider-specific SDK calls and request translation remain inside provider
adapters. Future terminal and VS Code interfaces should reuse the same
application layer.

See [Architecture](docs/architecture.md).

## Requirements

### Core

- Python 3.12
- `uv`
- Git

### Providers

At least one provider must be available:

- Ollama with a compatible local model.
- OpenAI with an API key, model access, and available credit.
- Anthropic with an API key, model access, and available credit.

## Quick Start

Clone the repository:

```bash
git clone git@github.com:RafaelRFSSilva/agent-workbench.git
cd agent-workbench
```

Install dependencies:

```bash
uv sync
```

Create the private environment file:

```bash
cp .env.example .env
```

The `.env` file is ignored by Git and must not be committed.

Display the CLI help:

```bash
uv run agent-workbench --help
```

## Local Ollama Setup

Ollama is the default provider and `gpt-oss:20b` is the default model.

```bash
ollama pull gpt-oss:20b
ollama list
```

Configure `.env`:

```dotenv
AGENT_WORKBENCH_PROVIDER=ollama
AGENT_WORKBENCH_MODEL=gpt-oss:20b
```

Start Agent Workbench:

```bash
uv run agent-workbench
```

## Cloud Provider Setup

### OpenAI

```dotenv
OPENAI_API_KEY=<your-api-key>
AGENT_WORKBENCH_PROVIDER=openai
AGENT_WORKBENCH_MODEL=<openai-model>
```

### Anthropic

```dotenv
ANTHROPIC_API_KEY=<your-api-key>
AGENT_WORKBENCH_PROVIDER=anthropic
AGENT_WORKBENCH_MODEL=<anthropic-model>
```

API keys must remain in private environment configuration.

## Interactive Setup

Start the prompt-based setup:

```bash
uv run agent-workbench --setup
```

It collects:

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
    ↓
Interactive Conversation
```

This setup is functional but is not the final terminal or VS Code experience.

## Basic Usage

Use values from `.env`:

```bash
uv run agent-workbench
```

Select Ollama explicitly:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b
```

Select a cloud provider:

```bash
uv run agent-workbench \
  --provider openai \
  --model <openai-model>
```

```bash
uv run agent-workbench \
  --provider anthropic \
  --model <anthropic-model>
```

When `--provider` is supplied, `--model` must also be supplied.

## Agent Profiles

Built-in profiles:

| Profile | Purpose |
| --- | --- |
| `planner` | Break objectives into tasks, dependencies, risks, and acceptance criteria |
| `developer` | Design and implement maintainable, testable, and secure solutions |
| `reviewer` | Review correctness, security, maintainability, and test coverage |
| `tester` | Design tests and investigate failures and regressions |

Start the Reviewer:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent reviewer
```

Load a custom profile:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent-file ./security-reviewer.toml
```

See [Agent Profiles](docs/agent-profiles.md).

## Context Files

Attach supported text files:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent reviewer \
  --context-file README.md \
  --context-file pyproject.toml
```

Supported extensions:

- `.txt`
- `.md`
- `.py`
- `.toml`
- `.json`
- `.yaml`
- `.yml`

Each file must contain valid UTF-8 and must not exceed 100 KiB.

The current implementation sends complete selected files with every request.
Workspace tools and RAG are future milestones.

See [Context Files](docs/context-files.md).

## Generation Configuration

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --temperature 0.2 \
  --top-p 0.8 \
  --max-output-tokens 512
```

| Argument | Validation |
| --- | --- |
| `--temperature` | Number from `0.0` to `1.0` |
| `--top-p` | Number from `0.0` to `1.0` |
| `--max-output-tokens` | Positive integer |

See [Runtime Configuration](docs/runtime-configuration.md).

## Structured Outputs

Create a response format:

```json
{
  "name": "software_review",
  "schema": {
    "type": "object",
    "properties": {
      "summary": {
        "type": "string"
      },
      "risk_level": {
        "type": "string",
        "enum": ["low", "medium", "high"]
      }
    },
    "required": ["summary", "risk_level"],
    "additionalProperties": false
  }
}
```

Use it:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --temperature 0.0 \
  --max-output-tokens 256 \
  --response-format-file ./software-review.json
```

Provider responses currently remain strings containing JSON.

See [Structured Outputs](docs/structured-outputs.md).

## Tool Calling

Tools are disabled by default. Enable the available built-in tools explicitly:

```bash
uv run agent-workbench --provider ollama --model gpt-oss:20b --enable-tools
```

The initial built-in tool is `calculator`. It evaluates basic arithmetic with
integer and finite floating-point literals, parentheses, unary `+` and `-`,
and binary `+`, `-`, `*`, `/`, `//`, and `%`. It rejects names, function calls,
collections, comparisons, booleans, bitwise operators, non-finite values, and
other Python syntax. Expressions are limited in length and AST complexity.

Tool calling is synchronous. A model may request tools during one CLI user
turn; the CLI displays and retains only the final assistant text. Internal tool
rounds are not persisted across separate user turns.

See [Architecture](docs/architecture.md) for the shared tool models and
provider translations.

## Configuration Precedence

```text
Command-Line Arguments
        ↓
Runtime Environment Variables
        ↓
Local .env File
        ↓
Application Defaults
```

Existing environment variables are not overwritten by `.env`.

## Documentation

| Document | Purpose |
| --- | --- |
| [Getting Started](docs/getting-started.md) | Installation, examples, and troubleshooting |
| [Architecture](docs/architecture.md) | Current layers and planned architecture |
| [Runtime Configuration](docs/runtime-configuration.md) | Providers, models, generation, and setup |
| [Agent Profiles](docs/agent-profiles.md) | Built-in and custom roles |
| [Context Files](docs/context-files.md) | Attachments and future workspace context |
| [Structured Outputs](docs/structured-outputs.md) | JSON Schema response configuration |
| [Product Vision](docs/product-vision.md) | Multi-agent VS Code workspace and voice input |
| [Project Configuration](docs/project-configuration.md) | `.agent-workbench/`, skills, commands, and MCP |
| [Roadmap](docs/roadmap.md) | Completed and planned milestones |
| [Development Log](DEVELOPMENT_LOG.md) | Implementation history and decisions |

## Roadmap Summary

Completed foundations:

- [x] Local and cloud providers.
- [x] Provider-independent requests.
- [x] Agent profiles and file context.
- [x] Generation configuration.
- [x] Prompt-based setup.
- [x] Structured outputs.
- [x] Provider-independent tool calling and opt-in calculator.

Next milestones:

- [ ] Safe workspace tools.
- [ ] Agent sessions.
- [ ] Local project retrieval and RAG.
- [ ] Multi-agent orchestration.
- [ ] Git worktree isolation.
- [ ] Project configuration and MCP.
- [ ] Terminal and VS Code interfaces.
- [ ] Voice prompt input.
- [ ] Evaluation, persistence, and AWS deployment.

See the complete [Roadmap](docs/roadmap.md).

## Quality Checks

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
git diff --check
```

## Security

Current protections include:

- API keys remain outside source code.
- `.env` is ignored by Git.
- Existing environment variables are preserved.
- Context and response-format files are validated before provider creation.
- Automated tests do not call paid APIs.

Future workspace execution will require explicit permissions, path containment,
command confirmation, repository and MCP trust, and visible audit traces.

## Current Limitations

Agent Workbench does not yet provide:

- Filesystem or network tools.
- Shell command execution.
- User-defined tools.
- Asynchronous tool execution.
- Project indexing or RAG.
- Persistent conversations.
- Multiple simultaneous agents.
- Multi-agent orchestration.
- MCP integration.
- Git worktree management.
- A navigable terminal workspace.
- A VS Code extension.
- Voice prompt input.
- Cloud deployment.

## Author

Developed and maintained by Rafael Silva.

## License

Copyright © 2026 Rafael Silva.

Licensed under the Apache License 2.0.
