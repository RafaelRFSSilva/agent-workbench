# Roadmap

## Purpose

This roadmap describes the evolution of Agent Workbench from its current
provider-independent CLI into a local-first, multi-agent software-development
workspace.

The order may change when implementation discoveries reveal missing
foundations, but the product direction should remain stable.

## Current Position

Agent Workbench currently provides:

- Local inference through Ollama.
- Cloud inference through OpenAI and Anthropic.
- Interactive multi-turn conversations.
- In-memory conversation history.
- Provider-independent requests through `ChatRequest`.
- Built-in and custom agent profiles.
- Explicit file-based context.
- Portable generation configuration.
- Prompt-based interactive setup.
- Provider-independent structured outputs.
- Provider-independent tool calling with an opt-in calculator.
- Automated tests, Ruff checks, and GitHub Actions.

The current application manages one conversation at a time.

It does not yet provide filesystem or network tools, project-wide workspace
access, RAG, asynchronous execution, user-defined tools, multiple simultaneous
agents, MCP integration, or a VS Code interface.

## Development Direction

```text
Provider-Independent Foundation
        ↓
Tool Calling
        ↓
Workspace Tools
        ↓
Agent Sessions
        ↓
Local Project Retrieval
        ↓
Multi-Agent Orchestration
        ↓
Execution Isolation
        ↓
Project Configuration and MCP
        ↓
Terminal and VS Code Experience
        ↓
Voice Input
        ↓
Evaluation and Deployment
```

## Phase 1 — Provider-Independent Foundation

### Status

Completed.

### Completed Capabilities

- [x] Ollama provider.
- [x] OpenAI Responses API provider.
- [x] Anthropic Messages API provider.
- [x] Shared `ChatProvider` protocol.
- [x] Shared `ChatRequest`.
- [x] Runtime provider and model selection.
- [x] Environment and `.env` configuration.
- [x] System prompts.
- [x] Built-in agent profiles.
- [x] Custom TOML agent profiles.
- [x] Explicit context files.
- [x] Shared `GenerationConfig`.
- [x] Prompt-based interactive setup.
- [x] Shared `JSONResponseFormat`.
- [x] Provider translation tests.
- [x] Real local Ollama validation.

## Phase 2 — Provider-Independent Tool Calling

### Status

Completed.

### Objective

Allow models to request application capabilities through shared definitions,
invocations, and results.

```text
ToolDefinition
├── name
├── description
└── input_schema

ToolInvocation
├── id
├── tool_name
└── arguments

ToolResult
├── invocation_id
├── status
├── output
└── error
```

### Completed Work

- [x] Define immutable shared `ToolDefinition`, `ToolInvocation`, and
  `ToolResult` models.
- [x] Add ordered tool definitions and interaction history to `ChatRequest`.
- [x] Add ordered tool invocations to `ChatResponse`.
- [x] Validate complete ordered `ToolInteractionRound` associations.
- [x] Translate tool definitions, calls, and history for Ollama, OpenAI, and
  Anthropic.
- [x] Add provider-independent `ToolRegistry` registration and synchronous
  execution with unknown-tool and safe handler-error results.
- [x] Add `run_tool_calling_loop()` with positive maximum-round protection.
- [x] Integrate the loop into the CLI without changing default no-tool
  behavior.
- [x] Add opt-in `--enable-tools` and the safe built-in calculator.
- [x] Add automated tests and a real local Ollama smoke test.

The initial calculator is deliberately limited to basic arithmetic. Filesystem,
shell, network, MCP, asynchronous, and user-defined tools remain outside this
completed milestone.

## Phase 3 — Workspace Tools

### Objective

Allow authorised agents to inspect project files when needed instead of
requiring every file to be attached manually.

### Read-Only Tools

- [ ] Define a workspace-root abstraction.
- [ ] Add path containment and symlink protection.
- [ ] Add `list_files`.
- [ ] Add `read_file`.
- [ ] Add `search_text`.
- [ ] Add `search_symbols`.
- [ ] Add `inspect_git_status`.
- [ ] Add `inspect_git_diff`.
- [ ] Add file and output size limits.
- [ ] Add visible tool traces.

### Controlled Write and Execution Tools

- [ ] Add patch-based file updates.
- [ ] Add approved formatter execution.
- [ ] Add approved static-analysis execution.
- [ ] Add approved test execution.
- [ ] Add confirmation for commands.
- [ ] Add timeouts and cancellation.
- [ ] Add destructive-action protection.

Write and execution tools should be introduced only after read-only access and
permissions are stable.

## Phase 4 — Agent Sessions

### Objective

Represent one configured agent as a reusable application object.

```text
AgentSession
├── id
├── profile
├── provider
├── model
├── conversation
├── task
├── workspace_scope
├── tools
├── permissions
├── generation_config
├── response_format
└── status
```

### Planned Work

- [ ] Define session identifiers.
- [ ] Separate session state from CLI presentation.
- [ ] Represent tasks and session status.
- [ ] Attach workspace scope and tools.
- [ ] Add lifecycle, cancellation, failure, and completion states.
- [ ] Preserve session conversation state.
- [ ] Add deterministic session tests.

The existing CLI should become one client of the session layer.

## Phase 5 — Local Project Retrieval

### Objective

Find relevant project information without inserting the complete repository
into every model request.

```text
Project Files
        ↓
Filtering and Parsing
        ↓
Chunking
        ↓
Embeddings
        ↓
Local Vector Store
        ↓
Relevant Chunks
        ↓
Agent Session
```

### Planned Work

- [ ] Define project indexing boundaries.
- [ ] Add include and exclude rules.
- [ ] Add deterministic chunking.
- [ ] Preserve source metadata.
- [ ] Add a local embedding provider.
- [ ] Add a local vector store.
- [ ] Add semantic retrieval.
- [ ] Add context-window budgeting.
- [ ] Add retrieval traces and evaluations.

Workspace tools provide exact access; RAG helps discover relevant content.

## Phase 6 — Multi-Agent Orchestration

### Objective

Allow the user to coordinate several specialised agent sessions.

```text
User Objective
        ↓
Planner
        ↓
Approved Tasks
        ↓
Developer
        ↓
Tester
        ↓
Reviewer
        ↓
User Approval
```

### Planned Work

- [ ] Define tasks, dependencies, and acceptance criteria.
- [ ] Add structured agent handoffs.
- [ ] Allow manual task assignment.
- [ ] Allow a Planner to propose tasks.
- [ ] Require approval before execution.
- [ ] Track progress and blocked work.
- [ ] Pass focused context between agents.
- [ ] Request reviews.
- [ ] Limit recursive delegation.
- [ ] Record complete orchestration traces.

The user remains responsible for permissions, approvals, and final decisions.

## Phase 7 — Isolated Agent Execution

### Objective

Prevent several writing agents from modifying the same working directory
without coordination.

### Planned Work

- [ ] Support read-only sessions.
- [ ] Support patch-only results.
- [ ] Add Git branch isolation.
- [ ] Add Git worktree isolation.
- [ ] Add controlled patch application.
- [ ] Add conflict detection.
- [ ] Add review-before-apply workflows.
- [ ] Add cleanup and failure recovery.

A possible layout is:

```text
Main Workspace
├── Planner: read-only
├── Developer A: worktree A
├── Developer B: worktree B
├── Tester: test worktree
└── Reviewer: read-only diff access
```

## Phase 8 — Project Configuration

### Objective

Allow repositories to define reusable Agent Workbench behavior.

Proposed structure:

```text
.agent-workbench/
├── project.md
├── project.local.md
├── settings.toml
├── settings.local.toml
├── agents/
├── rules/
├── skills/
├── commands/
└── mcp.toml
```

### Planned Work

- [ ] Add project-root discovery.
- [ ] Define validated configuration schemas.
- [ ] Add shared and local settings.
- [ ] Discover project agents and rules.
- [ ] Add skills and named commands.
- [ ] Add clear precedence rules.
- [ ] Add repository trust.
- [ ] Add effective-configuration inspection.

This structure remains proposed until the runtime models it depends on are
stable.

## Phase 9 — MCP Integration

### Objective

Expose Model Context Protocol capabilities through the shared Agent Workbench
tool and context layers.

### Planned Work

- [ ] Define MCP server configuration.
- [ ] Add MCP client management.
- [ ] Require approval for unfamiliar servers.
- [ ] Discover MCP tools and resources.
- [ ] Convert MCP tools into shared tool definitions.
- [ ] Apply agent and workspace permissions.
- [ ] Add timeouts, cancellation, traces, and clear errors.
- [ ] Add secret and environment-variable handling.

MCP extends Agent Workbench capabilities; it does not replace the shared tool
abstraction, permission model, or native workspace tools.

## Phase 10 — Terminal Workspace

### Objective

Evolve the current prompt-based `--setup` into a navigable terminal workspace.

### Planned Experience

- [ ] Arrow-key navigation.
- [ ] Provider and model menus.
- [ ] Agent session list.
- [ ] Task assignment.
- [ ] File attachment selection.
- [ ] Permission summaries.
- [ ] Tool activity display.
- [ ] Confirmation screens.
- [ ] Switching between sessions.
- [ ] Editing and cancelling sessions.

The current setup remains a functional foundation, not the final interface.

## Phase 11 — VS Code Experience

### Objective

Provide a VS Code extension or workspace panel for creating, observing, and
orchestrating agents.

```text
Agent Workbench
├── Sessions
├── Tasks
├── Context
├── Tools
├── Activity
├── Project Configuration
├── MCP Servers
└── Controls
```

### Planned Work

- [ ] Create and close agent sessions.
- [ ] Display session and task status.
- [ ] Attach the current file, selection, or Git diff.
- [ ] Display tool calls, file changes, commands, and tests.
- [ ] Approve or reject actions.
- [ ] Navigate between agents.
- [ ] Display project configuration and MCP capabilities.
- [ ] Present structured agent results.
- [ ] Preserve a visible audit trail.

## Phase 12 — Voice Input

### Objective

Allow users to create prompts through speech while preserving review and
confirmation.

```text
Microphone
        ↓
Speech-to-Text
        ↓
Editable Transcript
        ↓
User Confirmation
        ↓
Selected Agent Session
```

### Planned Work

- [ ] Add a provider-independent transcription boundary.
- [ ] Add local speech-to-text.
- [ ] Support optional cloud transcription.
- [ ] Support English and language selection.
- [ ] Add push-to-talk and cancellation.
- [ ] Add transcript preview and editing.
- [ ] Avoid retaining audio by default.
- [ ] Require confirmation before sending.
- [ ] Prevent unconfirmed speech from triggering tools.

Text input remains fully supported.

## Phase 13 — Evaluation, Observability, and Persistence

### Planned Work

- [ ] Record provider latency and token usage.
- [ ] Record tool invocations and failures.
- [ ] Measure structured output validity.
- [ ] Evaluate retrieval relevance.
- [ ] Measure task and handoff success.
- [ ] Add reproducible benchmark scenarios.
- [ ] Persist conversations and agent sessions.
- [ ] Persist tasks, traces, indexes, and evaluation results.
- [ ] Add schema versioning and migrations.

Persistence should follow stable session, task, and trace models.

## Phase 14 — Packaging and Deployment

### Planned Work

- [ ] Improve Python packaging and releases.
- [ ] Add versioning and changelog automation.
- [ ] Add container support.
- [ ] Add reproducible development environments.
- [ ] Deploy an optional cloud version to AWS.
- [ ] Add operational monitoring and recovery documentation.

Local-only operation remains a core requirement.

## Cross-Cutting Security

Security must be implemented throughout the roadmap.

- [ ] Secret isolation.
- [ ] Workspace path containment.
- [ ] Symlink escape protection.
- [ ] Tool permissions.
- [ ] Command confirmation.
- [ ] Destructive-action protection.
- [ ] Git operation safety.
- [ ] Network controls.
- [ ] MCP and repository trust.
- [ ] Prompt-injection awareness.
- [ ] Tool-result validation.
- [ ] Audit traces.
- [ ] Timeouts, cancellation, and output limits.

## Documentation Policy

- `README.md` is the short project entry point.
- `docs/` contains detailed user and architecture documentation.
- `DEVELOPMENT_LOG.md` records implementation history and decisions.
- Feature milestones update only the relevant documents.
- Validation details should not be duplicated across every file.
- Future development-log entries should remain focused.

## Immediate Next Milestone

The next implementation milestone is:

> Safe read-only workspace tools.

The completed provider-independent tool-calling loop provides the execution
boundary; the next phase must add explicit workspace scope, permissions, path
containment, and visible activity before exposing project files.

## Near-Term Sequence

1. Add read-only workspace tools.
2. Introduce agent sessions.
3. Build local project retrieval.
4. Add initial multi-agent orchestration.
5. Add project configuration and MCP.
6. Build the terminal and VS Code experiences.

## Related Documentation

- [Product Vision](product-vision.md)
- [Architecture](architecture.md)
- [Getting Started](getting-started.md)
- [Runtime Configuration](runtime-configuration.md)
- [Agent Profiles](agent-profiles.md)
- [Context Files](context-files.md)
- [Structured Outputs](structured-outputs.md)
- [Project Configuration](project-configuration.md)
