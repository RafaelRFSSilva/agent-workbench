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
- Reusable agent profiles for planning, development, review, and testing
- Agent selection through the `--agent` command-line argument
- Clear display of the active agent identity and role
- Built-in agent profiles loaded from packaged TOML resources
- Custom agent profiles loaded through `--agent-file`
- Validation for malformed, incomplete, or unsupported TOML configuration
- File-based conversation context through the repeatable `--context-file` argument
- UTF-8 context loading with format, path, content, and size validation
- Provider-independent context delivery for Ollama, OpenAI, and Anthropic
- Provider-independent generation settings through `GenerationConfig`
- Runtime generation control through `--temperature`, `--top-p`, and
  `--max-output-tokens`
- Strict validation of portable generation parameter ranges
- Provider-specific generation translation for Ollama, OpenAI, and Anthropic
- Preservation of provider defaults when optional parameters are not supplied
- Interactive runtime configuration through the `--setup` argument
- Guided provider and model selection with safe defaults
- Optional built-in agent profile selection during setup
- Interactive loading and validation of multiple context files
- Interactive configuration of temperature, top-p, and maximum output tokens
- Repeated prompts after invalid interactive input
- Preservation of the existing non-interactive CLI workflow

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
├── context_documents
├── generation_config
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

## Agent Profiles

Agent profiles provide reusable identities and system instructions for common
software engineering responsibilities.

The initial profiles are:

| Profile | Purpose |
| --- | --- |
| `planner` | Breaks objectives into tasks, dependencies, risks, and acceptance criteria |
| `developer` | Designs and implements maintainable, testable, and secure solutions |
| `reviewer` | Reviews correctness, security, maintainability, edge cases, and test coverage |
| `tester` | Designs tests and investigates failures, regressions, and incorrect assumptions |

### Built-In Profile Files

The built-in profiles are stored as TOML resources inside the application
package:

```text
src/agent_workbench/profiles/
├── developer.toml
├── planner.toml
├── reviewer.toml
└── tester.toml
```

Each profile defines three required fields:

```toml
name = "Reviewer"
description = "Reviews software quality and risks."
system_prompt = """
You are a strict software review agent.
Evaluate correctness, security, maintainability, and test coverage.
"""
```

Keeping the profiles outside the Python implementation separates agent
behavior from profile loading and validation logic.

The TOML files are included in the source distribution and wheel, so the
built-in profiles remain available when the application is installed as a
package.


Activate a profile with `--agent`:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent reviewer
```

Example startup output:

```text
Agent Workbench | Provider: Ollama | Model: gpt-oss:20b | Agent: Reviewer
Type /exit or /quit to end the session.

Role: Reviews software for correctness, security, maintainability, and test coverage.
```

Agent profiles are represented independently from providers:

```text
AgentProfile
├── name
├── description
└── system_prompt
        ↓
ChatRequest
        ↓
Selected ChatProvider
```

The same profile can therefore be used with Ollama, OpenAI, or Anthropic.

### Custom Agent Profiles

Users can create custom agent profiles without modifying the application
source code.

Create a TOML file:

```toml
name = "Security Reviewer"
description = """
Reviews source code for security vulnerabilities and unsafe assumptions.
"""

system_prompt = """
You are a strict application security review agent.
Identify vulnerabilities, insecure defaults, input validation problems,
secret exposure, injection risks, and unsafe file operations.
Prioritize findings by severity and propose concrete mitigations.
"""
```

Start a session with the custom profile:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent-file ./security-reviewer.toml
```

Example startup output:

```text
Agent Workbench | Provider: Ollama | Model: gpt-oss:20b | Agent: Security Reviewer
Type /exit or /quit to end the session.

Role: Reviews source code for security vulnerabilities and unsafe assumptions.
```

Custom profile files:

- Must use the `.toml` extension
- Must be encoded as UTF-8
- Must contain `name`, `description`, and `system_prompt`
- Must use non-empty strings for all required fields
- Cannot contain unsupported fields
- Are validated before the provider session starts

The following combinations are intentionally rejected:

```text
--agent + --agent-file
--agent + --system-prompt
--agent-file + --system-prompt
```

This prevents ambiguous instruction precedence.

Custom profiles currently define agent identity and behavior only. Provider,
model, tools, context files, and generation parameters remain separate runtime
configuration concerns.

### Agent Profile Architecture

```text
Built-In TOML Profiles ─┐
                        ├── Agent Profile Loader
Custom TOML Profile ────┘            ↓
                              AgentProfile
                                    ↓
                         Runtime Configuration
                                    ↓
                              ChatRequest
                                    ↓
                         Selected ChatProvider
```

Both built-in and custom profiles use the same validation and runtime system
prompt pipeline.

## File-Based Conversation Context

Text files can be supplied as reference material for the active conversation
through the repeatable `--context-file` argument.

For example, a reviewer can receive both project documentation and
configuration:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent reviewer \
  --context-file README.md \
  --context-file pyproject.toml
```

Each `--context-file` argument loads one document. Multiple documents retain
the order in which they were provided.

Supported extensions:

- `.txt`
- `.md`
- `.py`
- `.toml`
- `.json`
- `.yaml`
- `.yml`

Context files:

- Must exist and refer to regular files rather than directories
- Must use a supported extension
- Must contain valid UTF-8 text
- Must contain non-whitespace content
- Must not exceed 100 KiB individually
- Are validated before the provider session starts

Loaded files remain separate from conversation history:

```text
Context File Paths
        ↓
Context Document Loader
        ↓
ContextDocument
├── source
└── content
        ↓
RuntimeConfiguration.context_documents
        ↓
ChatRequest.context_documents
        ↓
Provider Adapter
        ├── Ollama: system message
        ├── OpenAI: instructions
        └── Anthropic: system parameter
```

Each document is identified by its source path and placed inside a clearly
delimited context block. The active system prompt remains first, followed by
instructions that identify the documents as reference data.

Context documents are not inserted into the in-memory `user` and `assistant`
conversation history. They are attached separately to every model request in
the session.

This initial implementation sends the complete contents of every selected
file. It does not use embeddings, chunk selection, a vector database, or
Retrieval-Augmented Generation. A future RAG pipeline will replace complete
document delivery with retrieval of relevant chunks.

## Generation Configuration

Generation parameters are represented through the provider-independent
`GenerationConfig` data structure.

This allows the CLI and conversation layer to configure model generation
without depending on provider-specific parameter names.

Configure generation behavior through the command line:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --temperature 0.2 \
  --top-p 0.8 \
  --max-output-tokens 512
```

The supported parameters are:

| CLI argument          | Shared field        | Validation                     |
| --------------------- | ------------------- | ------------------------------ |
| `--temperature`       | `temperature`       | Number between `0.0` and `1.0` |
| `--top-p`             | `top_p`             | Number between `0.0` and `1.0` |
| `--max-output-tokens` | `max_output_tokens` | Positive integer               |

The shared `0.0` to `1.0` sampling range provides a portable subset that can
be represented by all currently supported providers.

Invalid values are rejected before a provider request is created.

The configuration follows this provider-independent path:

```text
Command-Line Arguments
├── --temperature
├── --top-p
└── --max-output-tokens
        ↓
CLIArguments
        ↓
RuntimeConfiguration.generation_config
        ↓
Interactive CLI
        ↓
ChatRequest.generation_config
        ↓
Provider Adapter
```

Each provider translates the shared configuration into its native API
parameters:

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

Parameters that are not supplied are omitted from Ollama and OpenAI requests,
allowing those providers to use their model defaults.

The Anthropic Messages API requires `max_tokens` for every request. When
`--max-output-tokens` is omitted, `AnthropicProvider` preserves its existing
default output limit of `1024` tokens.

Provider and model capabilities can still vary. A provider may reject a
generation parameter that is not supported by the selected model.

The current shared configuration intentionally contains only parameters that
can be translated across Ollama, OpenAI, and Anthropic. Stop sequences,
provider-specific controls, reasoning settings, seeds, and generation presets
are not included in this milestone.

A real Ollama validation was performed with:

```text
Provider: Ollama
Model: gpt-oss:20b
Temperature: 0.0
Top-p: 1.0
Maximum output tokens: 256
Expected response: GENERATION-CONFIG-OK
Actual response: GENERATION-CONFIG-OK
```

A previous validation using only `32` output tokens produced an empty final
response. Increasing the limit to `256` allowed the reasoning model to produce
the expected final content. Very small output budgets may therefore be
insufficient for models that consume part of the generation budget before
producing their final answer.

## Interactive Runtime Setup

Agent Workbench provides an optional guided setup flow for users who do not
want to memorize all command-line configuration arguments.

Start the interactive setup with:

```bash
uv run agent-workbench --setup
```

The setup collects the session configuration in the following order:

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
Interactive Conversation
```

The selected values are converted into the same provider-independent
`RuntimeConfiguration` used by the normal command-line workflow:

```text
--setup
   ↓
Interactive Setup
   ↓
RuntimeConfiguration
├── provider_name
├── model_name
├── system_prompt
├── agent_profile
├── context_documents
└── generation_config
   ↓
Provider Factory
   ↓
Interactive Conversation
```

The setup does not introduce a separate provider or conversation
implementation. Provider construction, request translation, error handling,
and conversation execution continue to use the existing application
architecture.

### Provider Selection

The currently configured provider is shown as the default:

```text
Available providers:
  1. anthropic
  2. ollama
  3. openai
Provider [ollama]:
```

Users may select a provider by entering either its name or menu number.

Pressing Enter accepts the displayed default.

Invalid provider values are rejected and the question is repeated without
terminating the application.

### Model Selection

When the selected provider matches the configured provider, the configured
model is offered as the default.

When switching to Ollama, the application can safely offer the default local
model:

```text
Model [gpt-oss:20b]:
```

A model configured for one provider is not reused automatically with another
provider.

For example, an Ollama model is not suggested after selecting OpenAI or
Anthropic. When no safe default exists, the user must provide a non-empty
provider-specific model name.

### Agent Profile Selection

The setup supports the packaged built-in agent profiles:

```text
Available agent profiles:
  0. none
  1. developer
  2. planner
  3. reviewer
  4. tester
Agent [none]:
```

Pressing Enter, entering `0`, or entering `none` starts the session without an
agent profile.

An agent can be selected through its name or menu number.

The selected profile is resolved through the same existing agent-profile
pipeline and provides the active system prompt, display name, and role
description.

Custom `--agent-file` profiles and direct `--system-prompt` values are not
currently collected by the interactive setup.

### Context File Selection

The setup accepts zero or more context files:

```text
Context files:
Enter one file path at a time. Press Enter when finished.
Context file [done]: README.md
Added context file: README.md
Context file [done]: pyproject.toml
Added context file: pyproject.toml
Context file [done]:
```

Every path is validated immediately through the existing context document
loader.

The same file requirements apply as in the normal CLI workflow:

- The path must exist.
- The path must point to a regular file.
- The extension must be supported.
- The contents must be valid UTF-8.
- The file must not be empty or whitespace-only.
- The file must not exceed the individual 100 KiB limit.

Invalid files are reported and the setup continues asking for another path.

Multiple valid files preserve the order in which they were entered.

### Generation Settings

The setup collects the provider-independent generation parameters:

```text
Generation settings:
Press Enter to use the provider or model default.
Temperature [provider default]:
Top-p [provider default]:
Maximum output tokens [provider default]:
```

The supported values are:

| Setting               | Validation                              |
| --------------------- | --------------------------------------- |
| Temperature           | Optional number between `0.0` and `1.0` |
| Top-p                 | Optional number between `0.0` and `1.0` |
| Maximum output tokens | Optional positive integer               |

Pressing Enter leaves a parameter unset and preserves the provider or model
default.

Invalid values are rejected and the individual question is repeated.

The collected values are stored in `GenerationConfig` and translated by the
selected provider adapter exactly as they are when supplied through direct
command-line arguments.

### Argument Compatibility

The interactive setup is an explicit alternative to direct configuration
arguments.

Therefore, `--setup` cannot be combined with:

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
```

This prevents ambiguous configuration sources inside the same session.

Running Agent Workbench without `--setup` preserves the existing behavior and
continues to resolve configuration through command-line arguments,
environment variables, the local `.env` file, and application defaults.

### Example

```text
Agent Workbench Interactive Setup
Press Enter to accept a displayed default or skip an optional value.

Available providers:
  1. anthropic
  2. ollama
  3. openai
Provider [anthropic]: 2
Model [gpt-oss:20b]:

Available agent profiles:
  0. none
  1. developer
  2. planner
  3. reviewer
  4. tester
Agent [none]: 3

Context files:
Enter one file path at a time. Press Enter when finished.
Context file [done]: README.md
Added context file: README.md
Context file [done]:

Generation settings:
Press Enter to use the provider or model default.
Temperature [provider default]: 0.0
Top-p [provider default]: 1.0
Maximum output tokens [provider default]: 256
```

## Usage

Start the CLI using the configuration stored in `.env`:

```bash
uv run agent-workbench
```

Start the guided interactive setup:

```bash
uv run agent-workbench --setup
```

Display the available command-line options:

```bash
uv run agent-workbench --help
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

Use the built-in planner:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent planner
```

Use the built-in developer:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent developer
```

Use the built-in reviewer:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent reviewer
```

Use the built-in tester:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent tester
```

Load a custom agent profile:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent-file ./security-reviewer.toml
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
- Context files are read locally and are not persisted by the application.
- Context contents are clearly delimited and identified as reference data.
- Context file validation occurs before the selected provider is constructed.

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
- [x] Add reusable agent profiles
- [x] Load built-in agent profiles from TOML resources
- [x] Support custom agent profile files
- [x] Add file-based conversation context
- [x] Add provider-independent generation configuration
- [x] Add prompt-based interactive runtime setup
- [ ] Add a navigable terminal setup wizard
- [ ] Discover custom profiles from a user profile directory
- [ ] Coordinate multiple agents through an orchestrator
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