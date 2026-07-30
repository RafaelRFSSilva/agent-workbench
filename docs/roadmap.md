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
- Provider-independent tool calling with an opt-in calculator, safe workspace
  inspection, approved structured file changes, and fixed validation commands.
- Reusable provider-independent `AgentSession` construction.
- Supervised local Git worktree creation, isolated session construction,
  complete approved local commits, dirty-state preservation, and clean-only
  approved removal.
- Deterministic autonomous coding with concise typed progress, formatter scope
  limited to successful approved Python changes, project-wide lint/tests, and
  unexpected-path rejection before DONE.
- Automated tests, Ruff checks, and GitHub Actions.

The current application manages one conversation at a time.

It does not yet provide deletion or rename transactions, arbitrary shell or
network tools, RAG, asynchronous execution, user-defined tools, multiple
simultaneous agents, MCP integration, or a VS Code interface.

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

### Status

Safe read-only inspection and the initial controlled local single-agent coding
workflow are completed. Broader write profiles and Git operations remain
future work.

### Objective

Allow authorised agents to inspect project files when needed instead of
requiring every file to be attached manually.

### Read-Only Tools

- [x] Define a canonical workspace-root abstraction.
- [x] Add absolute-path, containment, traversal, prefix-confusion, and symlink
  escape protection.
- [x] Add direct-child `list_files` with deterministic sorting, hidden-entry
  visibility, classifications, and a 128-entry limit.
- [x] Add strict UTF-8 `read_file` with canonical relative paths and a 100 KiB
  limit.
- [x] Authorize workspace tools explicitly with `--workspace PATH`; preserve
  deterministic combined order with `--enable-tools`.
- [x] Add bounded literal `search_text` with deterministic recursive order,
  hidden entries, symlink boundaries, UTF-8 skipping, and truncation reporting.
- [x] Add bounded Python `search_symbols` using non-executing standard-library
  AST parsing, lexical symbol kinds, async metadata, and deterministic results.
- [x] Add fixed-command `inspect_git_status` and `inspect_git_diff`.
- [x] Centralize generated-directory filtering across recursive file, text, and
  symbol traversal.
- [x] Add bounded read-only safe untracked-file evidence to Git diff
  inspection.
- [x] Add file and output size limits.
- [x] Add opt-in visible tool traces outside conversation history.

### Controlled Write and Execution Tools

- [x] Add patch-based single-file updates.
- [x] Add approved formatter execution.
- [x] Constrain controller-owned formatting to sorted successful approved
  changed Python paths.
- [x] Add approved static-analysis execution.
- [x] Add approved test execution.
- [x] Add explicit confirmation for fixed commands.
- [x] Add fixed command allowlists.
- [x] Add timeouts and bounded output.
- [x] Add initial destructive-action protection.
- [x] Add approved deterministic multi-file write transactions.
- [x] Add complete combined previews and post-approval full-plan revalidation.
- [x] Add handled-failure reverse-order rollback with explicit incomplete
  rollback reporting.
- [ ] Add command cancellation UI.
- [ ] Add file deletion and rename.
- [ ] Add crash-safe transaction journaling and recovery.
- [x] Add supervised Git worktree isolation for one local session.
- [ ] Add broader permission profiles.

The available controlled workflow is inspect → patch one file or apply one
approved multi-file transaction → format → lint → test → inspect diff →
report. It can run inside a separately approved local worktree while keeping
the clean primary tree unchanged. Each effectful invocation requires informed
one-use approval.
Multi-file rollback covers handled in-process failures only when rollback
succeeds; it is not crash-safe or globally filesystem-atomic. The workflow
does not provide arbitrary commands, deletion, rename, cancellation UI,
persistence, concurrency, automatic planning, or orchestration.

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

- [x] Define immutable validated session identifiers.
- [x] Separate transactional conversation state from CLI presentation.
- [x] Add initial ready, completing, and failed session states with retry after
  failure and re-entrant-send rejection.
- [x] Attach existing profile/system-prompt, context, generation, structured
  response, and optional tool-registry configuration.
- [x] Preserve successful session conversation state while excluding internal
  tool rounds.
- [x] Add deterministic session and CLI integration tests.
- [x] Add reusable `AgentSession` construction from resolved runtime
  configuration.
- [x] Separate provider, resolved profile/context forwarding, workspace, and
  deterministic registry construction from the CLI.
- [x] Make the CLI use the runtime factory as its single session-construction
  boundary.
- [x] Add deterministic factory tests for providers, configuration, tool
  ordering, failure isolation, and workspace safety.
- [x] Add immutable provider-independent task specifications with exact
  objectives and ordered acceptance criteria.
- [x] Attach optional read-only task metadata to `AgentSession`.
- [ ] Add task assignment, lifecycle, dependencies, and handoff semantics.
- [ ] Add cancellation and later completion lifecycle semantics.
- [ ] Add persistence, serialization, and multiple-session coordination.

The existing CLI is now a presentation client of the session layer.

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

- [x] Define bounded immutable task specifications with objectives and
  acceptance criteria.
- [ ] Define task identifiers, dependencies, lifecycle, assignment, and
  handoff semantics.
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

### Status

The first supervised single-worktree boundary and its explicitly approved
local commit completion flow are complete. Concurrent worktrees, push/PR/merge
workflows, deletion/rename support, and crash recovery remain planned.

### Objective

Prevent several writing agents from modifying the same working directory
without coordination.

### Planned Work

- [x] Support one read-only or action-enabled isolated session.
- [ ] Support patch-only results.
- [x] Add validated immutable worktree plans pinned to a clean primary HEAD.
- [x] Add approved local branch and worktree creation through fixed Git
  commands.
- [x] Construct `AgentSession` workspace capabilities only inside the verified
  worktree.
- [x] Add clean-only separately approved removal while preserving the branch.
- [x] Preserve dirty, partial, failed, and ambiguous worktrees for manual
  recovery.
- [x] Validate a real isolated local `gpt-oss:20b` coding workflow.
- [x] Add immutable validated isolated commit plans with a clean-index
  requirement and complete message, path, and unified-diff previews.
- [x] Revalidate after exact one-use approval, stage only approved paths, and
  verify the complete staged path set and diff.
- [x] Create fixed hookless, editorless, unsigned local commits and verify
  parent, message, paths, diff, index, worktree, and unchanged primary state.
- [x] Preserve partial index and ambiguous ref state for manual recovery.
- [x] Add immutable provider-independent recovery evidence for isolated commit
  and worktree lifecycle failures.
- [x] Re-inspect branch, target, registration, source HEAD, worktree HEAD,
  index state, and staged paths after handled failures without automatic
  reset, restore, clean, stash, forced removal, or branch deletion.
- [x] Integrate commit message and complete commit approval into the CLI.
- [x] Validate the complete local flow with `COMMIT-842`.
- [x] Add a self-hosting operator guide.
- [x] Add provider-independent per-send tool allowlists and round limits.
- [x] Enforce deterministic DISCOVER, EDIT, VALIDATE, REPAIR, VERIFY, and DONE
  phases outside the model.
- [x] Run Ruff format, Ruff check, pytest, Git status, and Git diff from the
  controller through existing approval and execution boundaries.
- [x] Reject false completion, failed validation, exhausted repair limits,
  failed Git inspection, and empty final diffs before commit planning.
- [x] Add scripted-provider regression scenarios backed by real temporary Git
  repositories.
- [x] Add controlled patch application.
- [x] Add an existing-file-only SHA-guarded whole-file rewrite action.
- [x] Preserve bounded sanitized workspace-action failure evidence across EDIT
  and REPAIR sends.
- [x] Present every failed validation with explicit bounded stdout and stderr
  evidence and dynamic runtime requirements during REPAIR.
- [ ] Add conflict detection.
- [ ] Add review-before-apply workflows.
- [ ] Add deletion, rename, copy, and mode-changing commits.
- [ ] Add optional commit signing.
- [ ] Add automatic push and Pull Request creation.
- [ ] Add merge workflows.
- [ ] Add branch deletion.
- [ ] Add several concurrent worktrees and orchestration.
- [ ] Add persistent lifecycle records and crash-safe staging/commit recovery.

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
- [x] Add a deterministic scripted-provider coding workflow regression battery.
- [ ] Add reproducible benchmark scenarios.
- [ ] Benchmark the deterministic workflow manually with real local models.
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
- [x] Workspace path containment.
- [x] Symlink escape protection.
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

The provider-independent deterministic coding workflow is complete.

The application now owns phase progression, invokes fixed Python validation and
Git verification itself, requires a real workspace action and non-empty final
diff, and bounds discovery, action failures, edit continuations, repair
evidence, and repair attempts. Existing-file whole rewrites use a latest-read
SHA guard, and repair prompts preserve every safe validation failure and
dynamic runtime requirement. Mutable formatting is restricted to successful
approved changed Python paths; read-only status checks reject unexpected paths
after formatting and before DONE. Typed controller events provide concise
direct and isolated CLI progress while tool traces and complete assistant prose
remain opt-in. Scripted providers and real temporary Git repositories cover
representative success, repair, and preserved-state failure paths without
calling Ollama or paid/cloud providers.

The next implementation milestone is:

> Persistent lifecycle records and crash-safe restart recovery.

This work must preserve the existing conservative rule: unexpected staged
state, partial staging, failed commits, changed HEAD, and ambiguous refs remain
available for manual recovery. It must not introduce automatic reset, restore,
clean, stash, force removal, push, merge, or branch deletion.

## Near-Term Sequence

1. Add persistent lifecycle records and crash-safe restart recovery.
2. Add deletion and rename transactions with explicit conflict handling.
3. Build local project retrieval and project configuration.
4. Add task lifecycle, assignment, dependencies, and multi-agent foundations.
5. Build terminal and VS Code experiences.
6. Add reproducible manual benchmarks with real local models.

## Related Documentation

- [Product Vision](product-vision.md)
- [Architecture](architecture.md)
- [Task Specifications](task-specifications.md)
- [Getting Started](getting-started.md)
- [Runtime Configuration](runtime-configuration.md)
- [Agent Profiles](agent-profiles.md)
- [Self-Hosting](self-hosting.md)
- [Context Files](context-files.md)
- [Structured Outputs](structured-outputs.md)
- [Project Configuration](project-configuration.md)
