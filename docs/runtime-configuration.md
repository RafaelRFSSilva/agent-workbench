# Runtime Configuration

## Overview

Agent Workbench resolves provider-independent runtime configuration before
creating a model provider or starting a conversation.

A configured session currently contains:

- A provider.
- A model.
- An optional system prompt.
- An optional agent profile.
- Zero or more context documents.
- Optional generation settings.
- An optional structured response format.

The same runtime configuration model is used by:

- Direct command-line arguments.
- Environment variables.
- The local `.env` file.
- Application defaults.
- The prompt-based interactive setup.

## Runtime Configuration Model

The resolved configuration is represented by `RuntimeConfiguration`.

Conceptually:

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

This is the final application configuration used before provider construction.

Provider-specific SDK arguments do not belong in `RuntimeConfiguration`.

They are created later by the selected provider adapter.

## Configuration Sources

Agent Workbench currently accepts configuration from:

1. Command-line arguments.
2. Runtime environment variables.
3. A local `.env` file.
4. Application defaults.

The prompt-based interactive setup is an explicit alternative to direct
command-line configuration.

## Configuration Precedence

The direct configuration workflow follows this precedence:

```text
Command-Line Arguments
        ↓
Runtime Environment Variables
        ↓
Local .env File
        ↓
Application Defaults
```

A value supplied through the command line overrides the equivalent environment
or `.env` value for that execution.

An environment variable already present in the current process is not
overwritten by the local `.env` file.

This allows users to keep convenient local defaults while still changing a
session temporarily.

## Local Environment File

Create the private environment file from the public template:

```bash
cp .env.example .env
```

The `.env` file is excluded from Git.

It may contain local provider and model defaults:

```dotenv
AGENT_WORKBENCH_PROVIDER=ollama
AGENT_WORKBENCH_MODEL=gpt-oss:20b
```

Cloud API credentials may also be referenced through environment variables:

```dotenv
OPENAI_API_KEY=<your-api-key>
ANTHROPIC_API_KEY=<your-api-key>
```

Real credentials must never be committed.

The public `.env.example` file should contain variable names and safe example
values only.

## Provider Configuration

The supported provider identifiers are:

- `ollama`
- `openai`
- `anthropic`

A provider can be selected through `.env`:

```dotenv
AGENT_WORKBENCH_PROVIDER=ollama
```

or through the command line:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b
```

When `--provider` is supplied, `--model` must also be supplied.

This prevents a model configured for one provider from being reused
accidentally with a different provider.

## Model Configuration

The model may be selected through:

```dotenv
AGENT_WORKBENCH_MODEL=gpt-oss:20b
```

or:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b
```

A model may also be overridden while preserving the configured provider:

```bash
uv run agent-workbench \
  --model <provider-specific-model>
```

Model identifiers are provider-specific.

Agent Workbench does not currently query providers to verify model
availability before the conversation starts.

Provider or model errors are reported when provider construction or a model
request fails.

## Ollama Defaults

The application default provider is Ollama.

The default local model is:

```text
gpt-oss:20b
```

A minimal local configuration is:

```dotenv
AGENT_WORKBENCH_PROVIDER=ollama
AGENT_WORKBENCH_MODEL=gpt-oss:20b
```

Confirm that the model is available:

```bash
ollama list
```

Start Agent Workbench:

```bash
uv run agent-workbench
```

## OpenAI Configuration

Configure OpenAI through:

```dotenv
OPENAI_API_KEY=<your-api-key>
AGENT_WORKBENCH_PROVIDER=openai
AGENT_WORKBENCH_MODEL=<openai-model>
```

Or select it directly:

```bash
uv run agent-workbench \
  --provider openai \
  --model <openai-model>
```

The provider uses the OpenAI Responses API.

API access, model availability, account permissions, and available credit are
managed by OpenAI.

## Anthropic Configuration

Configure Anthropic through:

```dotenv
ANTHROPIC_API_KEY=<your-api-key>
AGENT_WORKBENCH_PROVIDER=anthropic
AGENT_WORKBENCH_MODEL=<anthropic-model>
```

Or select it directly:

```bash
uv run agent-workbench \
  --provider anthropic \
  --model <anthropic-model>
```

The provider uses the Anthropic Messages API.

API access, model availability, account permissions, and available credit are
managed by Anthropic.

## System Prompts

A temporary system prompt can be supplied through:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --system-prompt \
  "You are a strict software reviewer."
```

The system prompt defines instructions for the complete session.

It remains separate from user and assistant messages:

```text
ChatRequest
├── system_prompt
└── messages
    ├── user
    └── assistant
```

Provider adapters translate it into their native API representation:

```text
ChatRequest.system_prompt
├── Ollama: system message
├── OpenAI: instructions
└── Anthropic: system parameter
```

The system prompt is included with every request in the session.

## Agent Profiles

A built-in agent may be selected through:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent reviewer
```

Available built-in profiles are:

- `developer`
- `planner`
- `reviewer`
- `tester`

A custom profile may be supplied through:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent-file ./security-reviewer.toml
```

The following combinations are rejected:

```text
--agent + --agent-file
--agent + --system-prompt
--agent-file + --system-prompt
```

This avoids ambiguous instruction precedence.

Agent profiles are documented in more detail in
[Agent Profiles](agent-profiles.md).

## Context Documents

One or more explicit context files may be supplied through repeated
`--context-file` arguments:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --context-file README.md \
  --context-file pyproject.toml
```

The resolved files are stored separately from conversation history:

```text
RuntimeConfiguration.context_documents
        ↓
ChatRequest.context_documents
        ↓
Provider Adapter
```

Context file loading is documented in
[Context Files](context-files.md).

## Generation Configuration

Portable model-generation settings are represented through
`GenerationConfig`.

Current fields:

```text
GenerationConfig
├── temperature
├── top_p
└── max_output_tokens
```

Configure them through:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --temperature 0.2 \
  --top-p 0.8 \
  --max-output-tokens 512
```

## Temperature

`--temperature` accepts a number between `0.0` and `1.0`.

Example:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --temperature 0.2
```

Lower values generally request more focused generation.

Higher values allow more variation.

Exact behavior remains model-dependent.

## Top-p

`--top-p` accepts a number between `0.0` and `1.0`.

Example:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --top-p 0.8
```

The shared range is intentionally limited to a portable subset supported by
the current providers.

## Maximum Output Tokens

`--max-output-tokens` accepts a positive integer.

Example:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --max-output-tokens 512
```

Very small output limits may prevent reasoning models from producing final
response content.

The required budget depends on the selected model and task.

## Generation Provider Translation

Each provider translates the shared fields:

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

Unset optional values are omitted from Ollama and OpenAI requests.

The Anthropic Messages API requires `max_tokens`.

When no shared maximum is supplied, `AnthropicProvider` uses its existing
fallback of `1024`.

## Structured Response Format

A JSON response format may be selected through:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --response-format-file ./software-review.json
```

The validated file becomes:

```text
RuntimeConfiguration.response_format
        ↓
ChatRequest.response_format
        ↓
Provider Adapter
```

When no response format is supplied, providers preserve their normal
unstructured text behavior.

Structured output configuration is documented in
[Structured Outputs](structured-outputs.md).

## Interactive Setup

Start the prompt-based runtime setup with:

```bash
uv run agent-workbench --setup
```

The setup collects:

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

It then produces the same `RuntimeConfiguration` used by direct command-line
configuration.

The setup does not contain separate provider or conversation logic.

## Setup Provider Selection

The configured provider is shown as the default:

```text
Available providers:
  1. anthropic
  2. ollama
  3. openai
Provider [ollama]:
```

A provider may be selected by name or menu number.

Invalid values repeat the provider question.

## Setup Model Selection

When the selected provider matches the configured provider, the configured
model may be offered as a safe default.

When switching providers, a model from the previous provider is not reused.

For Ollama, the application may offer:

```text
Model [gpt-oss:20b]:
```

For a cloud provider without a matching configured model, the user must enter a
non-empty model identifier.

## Setup Agent Selection

The setup displays:

```text
Available agent profiles:
  0. none
  1. developer
  2. planner
  3. reviewer
  4. tester
Agent [none]:
```

Pressing Enter, entering `0`, or entering `none` starts without an agent
profile.

Custom agent profile files are not currently selected through the setup.

## Setup Context Selection

The setup accepts context file paths one at a time:

```text
Context files:
Enter one file path at a time. Press Enter when finished.
Context file [done]:
```

Each file is validated immediately.

Invalid files do not restart the complete setup.

## Setup Generation Selection

The setup asks:

```text
Generation settings:
Press Enter to use the provider or model default.
Temperature [provider default]:
Top-p [provider default]:
Maximum output tokens [provider default]:
```

Blank values preserve defaults.

Invalid values repeat only the affected question.

## Setup Structured Output Selection

The setup asks:

```text
Structured output:
Press Enter to use the normal unstructured text response.
Response format file [none]:
```

Blank input preserves normal text responses.

A valid file is loaded and displayed by name.

An invalid file repeats the response format question.

## Setup Argument Compatibility

`--setup` cannot be combined with direct configuration arguments:

```text
--provider
--model
--system-prompt
--agent
--agent-file
--context-file
--temperature
--top-p
--max-output-tokens
--response-format-file
```

This keeps direct and interactive configuration as separate, unambiguous entry
points.

## CLI Argument Summary

| Argument | Purpose |
| --- | --- |
| `--provider` | Select a provider |
| `--model` | Select a provider-specific model |
| `--system-prompt` | Provide session instructions |
| `--agent` | Select a built-in agent profile |
| `--agent-file` | Load a custom TOML agent profile |
| `--context-file` | Attach a context file; repeatable |
| `--temperature` | Configure portable temperature |
| `--top-p` | Configure portable top-p |
| `--max-output-tokens` | Configure the output-token limit |
| `--response-format-file` | Load a JSON response format |
| `--setup` | Start prompt-based interactive setup |

Display the current CLI help:

```bash
uv run agent-workbench --help
```

## Example: Local Reviewer

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent reviewer \
  --context-file README.md \
  --temperature 0.0 \
  --top-p 1.0 \
  --max-output-tokens 512
```

## Example: Cloud Provider Override

```bash
uv run agent-workbench \
  --provider openai \
  --model <openai-model> \
  --agent developer
```

This override applies only to the current execution.

It does not modify `.env`.

## Example: Structured Review

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent reviewer \
  --context-file src/agent_workbench/providers/openai.py \
  --temperature 0.0 \
  --max-output-tokens 512 \
  --response-format-file ./software-review.json
```

## Validation Behavior

Configuration that can be validated locally is rejected before a provider
request is made.

Examples include:

- Unsupported provider names.
- Missing models after explicit provider selection.
- Conflicting instruction arguments.
- Invalid generation ranges.
- Invalid context files.
- Invalid response format files.
- Direct configuration combined with `--setup`.

Provider or model capabilities may still differ.

Unsupported provider-specific behavior is reported by the provider adapter.

## Security

Runtime configuration follows these security principles:

- API keys remain outside source code.
- `.env` is excluded from Git.
- Existing environment variables are not overwritten.
- Context files are validated before provider construction.
- Response format files are validated before provider construction.
- Unsupported argument combinations are rejected.
- Automated tests use simulated provider clients.
- Project files are not modified by runtime configuration.

Future workspace tools will require a separate permission model.

## Current Limitations

Runtime configuration is currently process-local.

The application does not persist:

- Setup selections.
- Conversation history.
- Agent sessions.
- Generation presets.
- Response format selections.
- Per-project configuration.
- Tool permissions.
- MCP configuration.

The current prompt-based setup does not support:

- Arrow-key navigation.
- Going back to a previous question.
- Custom agent profile selection.
- Direct system prompt entry.
- Removing a context file after adding it.
- Provider credential entry.
- Provider model discovery.
- Saving configuration.

These are future interface and configuration milestones.

## Related Documentation

- [Getting Started](getting-started.md)
- [Architecture](architecture.md)
- [Agent Profiles](agent-profiles.md)
- [Context Files](context-files.md)
- [Structured Outputs](structured-outputs.md)
- [Project Configuration](project-configuration.md)
- [Product Vision](product-vision.md)
- [Roadmap](roadmap.md)
