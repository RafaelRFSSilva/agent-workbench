# Agent Profiles

## Overview

Agent profiles provide reusable identities and instructions for specialised
software-engineering roles.

A profile describes how an agent should behave. It remains independent from:

- The selected provider.
- The selected model.
- Generation settings.
- Context files.
- Structured output configuration.
- Future tools and permissions.

The same profile can therefore be used with Ollama, OpenAI, or Anthropic.

## Built-In Profiles

Agent Workbench currently provides four built-in profiles:

| Profile | Purpose |
| --- | --- |
| `planner` | Break objectives into tasks, dependencies, risks, and acceptance criteria |
| `developer` | Design and implement maintainable, testable, and secure solutions |
| `reviewer` | Review correctness, security, maintainability, edge cases, and test coverage |
| `tester` | Design tests and investigate failures, regressions, and incorrect assumptions |

Select a built-in profile with `--agent`.

Example:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent reviewer
```

## Profile Structure

An agent profile contains three required fields:

```text
AgentProfile
├── name
├── description
└── system_prompt
```

- `name` is the displayed agent identity.
- `description` explains the role to the user.
- `system_prompt` defines the instructions sent to the model.

The profile does not contain provider-specific API configuration.

## Built-In Profile Files

Built-in profiles are stored as TOML resources inside the application package:

```text
src/agent_workbench/profiles/
├── developer.toml
├── planner.toml
├── reviewer.toml
└── tester.toml
```

Example profile:

```toml
name = "Reviewer"
description = "Reviews software quality and risks."
system_prompt = """
You are a strict software review agent.
Evaluate correctness, security, maintainability, and test coverage.
"""
```

These files are included in the project package so the profiles remain
available after installation.

## Runtime Flow

```text
Built-In TOML Profile
        ↓
Profile Loader
        ↓
AgentProfile
        ↓
RuntimeConfiguration
        ↓
ChatRequest.system_prompt
        ↓
Selected Provider Adapter
```

Provider adapters receive the resulting system prompt through the shared
`ChatRequest`.

The profile loader does not call model providers directly.

## Startup Display

When an agent profile is active, the CLI displays its name and role.

Example:

```text
Agent Workbench | Provider: Ollama | Model: gpt-oss:20b | Agent: Reviewer
Type /exit or /quit to end the session.

Role: Reviews software for correctness, security, maintainability, and test coverage.
```

This makes the active role visible before the conversation begins.

## Custom Agent Profiles

Users can create custom profiles without modifying the application source
code.

Create a TOML file such as `security-reviewer.toml`:

```toml
name = "Security Reviewer"
description = """
Reviews source code for security vulnerabilities and unsafe assumptions.
"""

system_prompt = """
You are a strict application security review agent.
Identify vulnerabilities, insecure defaults, input validation problems,
secret exposure, injection risks, and unsafe file operations.
Prioritise findings by severity and propose concrete mitigations.
"""
```

Start the custom agent:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent-file ./security-reviewer.toml
```

## Custom Profile Validation

Custom profile files:

- Must exist.
- Must refer to a regular file.
- Must use the `.toml` extension.
- Must use UTF-8 encoding.
- Must contain valid TOML.
- Must define `name`.
- Must define `description`.
- Must define `system_prompt`.
- Must use non-empty strings for every required field.
- Must not contain unsupported top-level fields.

Validation occurs before provider construction.

Invalid profiles prevent the session from starting and produce a descriptive
configuration error.

## Instruction Precedence

Only one direct source of session identity or system instructions may be
selected.

The following combinations are rejected:

```text
--agent + --agent-file
--agent + --system-prompt
--agent-file + --system-prompt
```

This avoids ambiguous instruction precedence.

Use:

- `--agent` for a packaged built-in profile.
- `--agent-file` for a custom TOML profile.
- `--system-prompt` for one-off direct instructions.

## Interactive Setup

The prompt-based setup supports built-in profiles:

```bash
uv run agent-workbench --setup
```

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

The user may select a profile by:

- Name.
- Menu number.
- Pressing Enter to use no profile.
- Entering `0`.
- Entering `none`.

Invalid values repeat the agent question.

Custom agent files and direct system prompts are not currently selected through
the setup.

## Provider Independence

Agent profiles are independent from the selected provider.

```text
AgentProfile
        ↓
ChatRequest.system_prompt
        ↓
ChatProvider
├── OllamaProvider
├── OpenAIProvider
└── AnthropicProvider
```

This allows the same Reviewer profile, for example, to run with a local Ollama
model or a cloud model.

A profile should describe role and behaviour rather than native SDK arguments.

## Profiles and Runtime Configuration

The current runtime keeps these concerns separate:

```text
RuntimeConfiguration
├── agent_profile
├── provider_name
├── model_name
├── context_documents
├── generation_config
└── response_format
```

This prevents a reusable role from becoming tied to one provider or model.

It also allows the user to combine the same profile with different context and
generation settings.

Example:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent reviewer \
  --context-file README.md \
  --temperature 0.0 \
  --max-output-tokens 512
```

## Profiles and Future Agent Sessions

The current `AgentProfile` is one component of the future multi-agent session
model.

A future session may include:

```text
AgentSession
├── profile
├── provider
├── model
├── task
├── conversation
├── workspace scope
├── tools
├── permissions
├── generation configuration
└── status
```

`AgentProfile` should remain focused on identity and general behaviour.

Provider settings, workspace access, tools, and task state belong to higher
runtime layers.

## Project-Specific Agents

A future project configuration directory may discover profiles from:

```text
.agent-workbench/agents/
```

For example:

```text
.agent-workbench/agents/
├── backend-developer.toml
├── security-reviewer.toml
└── release-tester.toml
```

This is not implemented yet.

The current `--agent-file` argument is the explicit foundation for that future
discovery mechanism.

Project-specific agent configuration is described in
[Project Configuration](project-configuration.md).

## Rules and Skills

Future agents may reference modular project rules and reusable skills.

The distinction should remain:

- A profile defines identity and general behaviour.
- A rule defines a focused project constraint.
- A skill defines a reusable workflow.
- A command starts an explicit project workflow.
- A task defines the current objective.

These concepts should not all be merged into one large system prompt file.

Relevant instructions should be selected according to the active task and
workspace context.

## Security

Agent profile files are instructions and must be treated as untrusted input.

Future project profile discovery should protect against:

- Excessive file sizes.
- Invalid encodings.
- Unsupported fields.
- Hidden provider credentials.
- Unsafe tool permissions.
- Instructions that attempt to bypass project restrictions.
- Paths outside the trusted workspace.

A profile must not grant itself tools, write access, command execution, network
access, or MCP access without approval from the application permission model.

## Current Limitations

Agent profiles currently define only:

- Name.
- Description.
- System prompt.

They do not currently define:

- Provider.
- Model.
- Context documents.
- Generation settings.
- Structured response formats.
- Tools.
- Permissions.
- Workspace scope.
- Skills.
- Rules.
- Persistent memory.
- Task state.

Other current limitations include:

- No automatic discovery of project profiles.
- No user-level profile directory.
- No profile composition.
- No profile inheritance.
- No setup selection for custom profiles.
- No profile editing interface.
- No VS Code profile panel.
- No persistent agent sessions.

## Related Documentation

- [Getting Started](getting-started.md)
- [Runtime Configuration](runtime-configuration.md)
- [Architecture](architecture.md)
- [Project Configuration](project-configuration.md)
- [Product Vision](product-vision.md)
- [Roadmap](roadmap.md)
