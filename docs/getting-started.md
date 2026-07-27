# Getting Started

## Overview

This guide explains how to install Agent Workbench, configure a model provider,
and start an interactive conversation.

Agent Workbench currently supports:

* Local models through Ollama.
* OpenAI through the Responses API.
* Anthropic through the Messages API.
* Built-in and custom agent profiles.
* Explicit file-based context.
* Portable generation configuration.
* Portable structured outputs.
* Prompt-based interactive runtime setup.
* Opt-in provider-independent tool calling, including safe read-only workspace
  inspection.

The current application runs as a command-line interface.

Multi-agent orchestration, workspace writes, MCP integration, project
retrieval, and the VS Code interface are future milestones.

## Requirements

### Core Requirements

* Python 3.12.
* `uv`.
* Git.

Confirm the installed versions:

```bash
python3 --version
uv --version
git --version
```

### Provider Requirements

At least one model provider must be available.

For local execution with Ollama:

* Ollama installed and running.
* A compatible local model.
* Sufficient system memory or GPU memory.

For OpenAI:

* An OpenAI API key.
* Access to the Responses API.
* Available API credit.
* A compatible model name.

For Anthropic:

* An Anthropic API key.
* Access to the Messages API.
* Available API credit.
* A compatible model name.

## Clone the Repository

Clone the project:

```bash
git clone git@github.com:RafaelRFSSilva/agent-workbench.git
cd agent-workbench
```

HTTPS may be used instead of SSH when preferred.

## Install Dependencies

Install the project and development dependencies:

```bash
uv sync
```

Confirm that the CLI is available:

```bash
uv run agent-workbench --help
```

## Create the Local Environment File

Copy the public environment template:

```bash
cp .env.example .env
```

The `.env` file is ignored by Git and must not be committed.

The application does not overwrite environment variables that are already
defined in the current shell.

## Local Ollama Setup

Ollama is the default provider.

The default local model is:

```text
gpt-oss:20b
```

Download the model:

```bash
ollama pull gpt-oss:20b
```

Confirm that it is available:

```bash
ollama list
```

Confirm that Ollama is running before starting Agent Workbench.

Configure `.env`:

```dotenv
AGENT_WORKBENCH_PROVIDER=ollama
AGENT_WORKBENCH_MODEL=gpt-oss:20b
```

Start the application:

```bash
uv run agent-workbench
```

## OpenAI Setup

Configure `.env`:

```dotenv
OPENAI_API_KEY=<your-api-key>
AGENT_WORKBENCH_PROVIDER=openai
AGENT_WORKBENCH_MODEL=<openai-model>
```

Do not commit the API key.

Start the application:

```bash
uv run agent-workbench
```

A model can also be selected explicitly:

```bash
uv run agent-workbench \
  --provider openai \
  --model <openai-model>
```

When `--provider` is supplied, `--model` must also be supplied.

This prevents a model configured for another provider from being reused
accidentally.

## Anthropic Setup

Configure `.env`:

```dotenv
ANTHROPIC_API_KEY=<your-api-key>
AGENT_WORKBENCH_PROVIDER=anthropic
AGENT_WORKBENCH_MODEL=<anthropic-model>
```

Do not commit the API key.

Start the application:

```bash
uv run agent-workbench
```

A model can also be selected explicitly:

```bash
uv run agent-workbench \
  --provider anthropic \
  --model <anthropic-model>
```

## Interactive Runtime Setup

Users who do not want to provide every command-line argument manually can use:

```bash
uv run agent-workbench --setup
```

The current prompt-based setup collects:

1. Provider.
2. Model.
3. Optional built-in agent profile.
4. Optional context files.
5. Optional generation settings.
6. Optional structured output definition.

Press Enter to accept a displayed default or skip an optional value.

The setup produces the same provider-independent runtime configuration used by
the direct command-line workflow.

The current setup is functional but is not the final terminal or VS Code user
experience.

## Basic Conversation

Start Agent Workbench:

```bash
uv run agent-workbench
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

Conversation history is maintained in memory for the active process.

It is not currently persisted after the application closes.

Empty input is ignored.

Use `/exit` or `/quit` to end the session.

## Select a Provider at Runtime

Use Ollama explicitly:

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

Command-line arguments override environment configuration for the current
execution.

## Use a System Prompt

Provide temporary session instructions:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --system-prompt \
  "You are a strict software reviewer. Focus on correctness and security."
```

The system prompt remains separate from user and assistant conversation
history.

## Use a Built-In Agent

Available built-in agents are:

* `planner`
* `developer`
* `reviewer`
* `tester`

Start the Reviewer agent:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent reviewer
```

Start the Developer agent:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent developer
```

Built-in agent profiles work with Ollama, OpenAI, and Anthropic.

## Use a Custom Agent Profile

Create a TOML profile:

```toml
name = "Security Reviewer"
description = "Reviews source code for security risks."
system_prompt = """
You are a strict application security review agent.
Identify vulnerabilities and propose concrete mitigations.
"""
```

Start the custom agent:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent-file ./security-reviewer.toml
```

A custom profile must:

* Use the `.toml` extension.
* Use UTF-8 encoding.
* Define `name`, `description`, and `system_prompt`.
* Use non-empty strings.
* Avoid unsupported fields.

## Attach Context Files

Provide one or more text files as explicit conversation context:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent reviewer \
  --context-file README.md \
  --context-file pyproject.toml
```

Supported file extensions are:

* `.txt`
* `.md`
* `.py`
* `.toml`
* `.json`
* `.yaml`
* `.yml`

Each file must:

* Exist.
* Be a regular file.
* Use a supported extension.
* Contain valid UTF-8.
* Contain non-whitespace content.
* Not exceed 100 KiB.

The current implementation sends the complete selected file contents with each
request.

Project-wide discovery, file tools, indexing, and RAG are not yet implemented.

## Configure Generation

Configure portable generation settings:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --temperature 0.2 \
  --top-p 0.8 \
  --max-output-tokens 512
```

Supported parameters:

| Argument              | Accepted value             |
| --------------------- | -------------------------- |
| `--temperature`       | Number from `0.0` to `1.0` |
| `--top-p`             | Number from `0.0` to `1.0` |
| `--max-output-tokens` | Positive integer           |

Parameters that are not supplied preserve provider or model defaults where
possible.

Provider and model support may still vary.

## Request Structured Output

Create a JSON response format file:

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
        "enum": [
          "low",
          "medium",
          "high"
        ]
      }
    },
    "required": [
      "summary",
      "risk_level"
    ],
    "additionalProperties": false
  }
}
```

Save it as:

```text
software-review.json
```

Start a structured output session:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --temperature 0.0 \
  --top-p 1.0 \
  --max-output-tokens 256 \
  --response-format-file ./software-review.json
```

The provider response remains a string containing JSON.

Agent Workbench does not currently deserialize or locally validate the
generated response after generation.

## Enable the Built-In Calculator

Tools are disabled by default. Enable the built-in tool registry explicitly:

```bash
uv run agent-workbench --provider ollama --model gpt-oss:20b --enable-tools
```

Ask the model to use the calculator, for example:

```text
Use the calculator tool to evaluate (17 * 23) + 5 before answering.
```

The built-in registry currently exposes only `calculator`. It accepts one
`expression` string and returns the original expression with a numeric result.
It supports integer and finite floating-point literals, parentheses, unary
`+` and `-`, and binary `+`, `-`, `*`, `/`, `//`, and `%`.

The calculator deliberately rejects names, variables, function calls,
attribute access, collections, comparisons, booleans, bitwise operators, and
all other Python syntax. It uses a restricted AST evaluator rather than
dynamic code execution, limits expression length and AST complexity, rejects
division or modulo by zero, and rejects non-finite results.

Tool execution is synchronous. During a tool-enabled CLI turn, the application
executes requested tools and displays only the final assistant text. The
internal tool rounds are not persisted across separate user turns.

## Inspect an Authorized Workspace

Workspace tools are disabled unless you explicitly authorize one root with
`--workspace`:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --workspace .
```

This exposes `list_files`, `read_file`, `search_text`, `inspect_git_status`,
and `inspect_git_diff`. `list_files` returns sorted direct children only,
including hidden entries, with file, directory, symlink, and other
classifications; it refuses directories with more than 128 entries. `read_file`
reads strict UTF-8 text up to 100 KiB and returns its canonical
workspace-relative path.

The workspace root and requested paths are resolved canonically. Absolute
paths, traversal, prefix-confusion paths, and symlinks escaping the root are
rejected. Internal symlinks remain available. `search_text` performs literal
recursive search in deterministic workspace-relative order, includes hidden
files and directories, skips invalid UTF-8, and does not follow directory
symlinks. It limits queries to 256 characters, inspects at most 512 files and
100 KiB per file, returns at most 256 matching lines of 1,000 characters, and
sets `truncated` when a limit applies.

Git status and diff inspection use fixed non-shell commands only. Diff output
separates unstaged from staged changes, disables external diff helpers, times
out after three seconds, and caps combined returned output at 100 KiB. No tool
writes, edits, deletes, globs, accesses the network, or uses MCP.

Combine workspace inspection with the calculator:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --enable-tools \
  --workspace .
```

The combined registry is deterministic: `calculator`, `list_files`,
`read_file`, `search_text`, `inspect_git_status`, then `inspect_git_diff`.

Show completed calls and results without changing conversation history:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --workspace . \
  --show-tool-traces
```

Traces are opt-in, compact deterministic JSON, and redact read content and
absolute paths. Tool execution remains synchronous, and internal tool rounds
are not persisted across separate CLI user turns. `search_symbols`, writes,
arbitrary command execution, and filesystem race protection between path
resolution and later access are not yet available.

## Configuration Precedence

Runtime configuration follows this precedence:

1. Command-line arguments.
2. Existing runtime environment variables.
3. Local `.env` values.
4. Application defaults.

This allows `.env` to provide convenient local defaults while command-line
arguments temporarily override them.

## Quality Checks

Run all automated tests:

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

Check whitespace errors in the Git diff:

```bash
git diff --check
```

## Troubleshooting

### Ollama Connection Failure

Confirm that Ollama is running:

```bash
ollama list
```

Confirm that the selected model exists locally.

### Missing Cloud API Key

Confirm that the correct environment variable exists:

```text
OPENAI_API_KEY
ANTHROPIC_API_KEY
```

Do not print real secret values into terminal output, documentation, issues, or
commits.

### Provider and Model Conflict

When selecting a provider through `--provider`, also provide `--model`.

Example:

```bash
uv run agent-workbench \
  --provider openai \
  --model <openai-model>
```

### Invalid Context File

Check that the file:

* Exists.
* Is not a directory.
* Uses a supported extension.
* Contains valid UTF-8.
* Is not empty.
* Does not exceed 100 KiB.

### Small Output Limit

Very small `--max-output-tokens` values may prevent reasoning models from
producing final response content.

Increase the limit and retry.

## Next Steps

After confirming that the basic CLI works, continue with:

* [Runtime Configuration](runtime-configuration.md)
* [Agent Profiles](agent-profiles.md)
* [Context Files](context-files.md)
* [Structured Outputs](structured-outputs.md)
* [Architecture](architecture.md)
* [Product Vision](product-vision.md)
* [Project Configuration](project-configuration.md)
* [Roadmap](roadmap.md)
