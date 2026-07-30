# Architecture

## Overview

Agent Workbench uses a provider-independent application architecture for
running conversations with local and cloud language models.

The command-line interface does not communicate directly with Ollama, OpenAI,
or Anthropic.

Instead, runtime configuration is resolved into shared application objects
that are translated by provider-specific adapters.

```text
User
  ↓
Command-Line Interface
  ↓
Runtime Configuration
  ↓
Provider Factory
  ↓
ChatProvider Protocol
  ↓
Provider Adapter
  ↓
Model Provider
```

The current implementation supports:

* Ollama local models.
* OpenAI through the Responses API.
* Anthropic through the Messages API.

The same conversation layer is used by every provider.

## Architectural Goals

The current architecture is designed to provide:

* Provider independence.
* Clear separation of responsibilities.
* Testability without external API calls.
* Safe configuration handling.
* Reusable agent behavior.
* Explicit conversation context.
* Portable generation configuration.
* Portable structured output configuration.
* Provider-independent tool calling and a foundation for multiple agent
  sessions.

The command-line interface is the first application client.

Future terminal and VS Code interfaces should reuse the same application
abstractions instead of implementing separate provider logic.

## Current Runtime Flow

```text
Command-Line Input
        ↓
Argument Parser
        ↓
CLIArguments
        ↓
Environment Configuration
        ↓
RuntimeConfiguration
        ↓
Provider Factory
        ↓
Selected ChatProvider
        ↓
Interactive Conversation
        ↓
ChatRequest
        ↓
Provider Adapter
        ↓
Provider SDK
```

The direct command-line workflow and the interactive setup both produce the
same `RuntimeConfiguration`.

```text
Direct CLI Arguments ──────┐
                           ├── RuntimeConfiguration
Interactive Setup ─────────┘
```

This prevents the setup flow from becoming an independent execution path.

The `code` command uses the same provider-independent session and registry, but
an application controller owns phase progression:

```text
TaskSpec
   ↓
DISCOVER (model, read-only, at most four tool rounds)
   ↓
EDIT (model, read-only plus controlled workspace actions)
   ↓
VALIDATE (controller: Ruff format → Ruff check → pytest)
   ↓ failed
REPAIR (model, read-only plus controlled workspace actions)
   └──────────────→ VALIDATE
   ↓ passed
VERIFY (controller: Git status → Git diff)
   ↓
DONE
```

Only successful tool results, validation exit codes, and final Git inspection
can advance this workflow. Assistant text is never completion evidence.

## Package Structure

The main application package is located under:

```text
src/agent_workbench/
```

Its responsibilities are separated across modules such as:

```text
agent_workbench/
├── arguments.py
├── cli.py
├── configuration.py
├── context.py
├── generation.py
├── interactive_setup.py
├── messages.py
├── profiles/
├── providers/
├── structured_outputs.py
├── tool_calling.py
├── tool_registry.py
└── built_in_tools.py
```

The exact package structure may evolve as tool calling, sessions, workspace
access, and orchestration are introduced.

## Configuration Layer

Runtime configuration can come from:

```text
Command-Line Arguments
        ↓
Runtime Environment Variables
        ↓
Local .env File
        ↓
Application Defaults
```

Higher entries take precedence over lower entries.

This allows users to configure permanent local defaults through `.env` while
temporarily overriding values through command-line arguments.

The private `.env` file is excluded from Git.

## CLI Arguments

Parsed command-line values are represented through `CLIArguments`.

This object contains user-supplied configuration such as:

```text
CLIArguments
├── provider
├── model
├── system_prompt
├── agent
├── agent_file
├── context_files
├── temperature
├── top_p
├── max_output_tokens
├── response_format_file
├── enable_tools
└── setup
```

`CLIArguments` represents command-line input.

It is not the final configuration passed into the conversation.

Paths and values are resolved and validated before provider construction.

## Runtime Configuration

The resolved session configuration is represented through
`RuntimeConfiguration`.

```text
RuntimeConfiguration
├── provider_name
├── model_name
├── system_prompt
├── agent_profile
├── context_documents
├── generation_config
├── response_format
└── enable_tools
```

This object provides a shared representation of one configured conversation
session.

The direct argument workflow and interactive setup both produce this same
structure.

Provider clients should not be created until runtime configuration has been
validated.

## Interactive Setup

The prompt-based setup is started through:

```bash
uv run agent-workbench --setup
```

It currently collects:

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

The setup does not contain conversation or provider SDK logic.

Its responsibility is to collect and validate configuration.

```text
Interactive Input
        ↓
Interactive Setup
        ↓
RuntimeConfiguration
        ↓
Provider Factory
        ↓
Conversation
```

Direct configuration arguments cannot be combined with `--setup`.

This avoids ambiguous configuration precedence within the same execution.

The current setup uses plain terminal prompts.

A navigable terminal workspace remains a future interface milestone.

## Provider Factory

The provider factory creates the selected provider adapter after runtime
configuration is complete.

```text
provider_name
    ↓
Provider Factory
├── "ollama"    → OllamaProvider
├── "openai"    → OpenAIProvider
└── "anthropic" → AnthropicProvider
```

The conversation layer receives only an object implementing the shared
`ChatProvider` protocol.

It does not need to know which SDK is being used.

## ChatProvider Protocol

The provider-independent boundary is represented through `ChatProvider`.

Conceptually:

```text
ChatProvider
└── complete(request: ChatRequest) -> ChatResponse
```

Each provider adapter is responsible for:

* Translating the shared request.
* Calling its native SDK.
* Extracting response text and tool invocations.
* Translating provider-specific errors.
* Preserving shared application behavior.

Current implementations:

```text
ChatProvider
├── OllamaProvider
├── OpenAIProvider
└── AnthropicProvider
```

Future providers should implement the same shared boundary.

## ChatRequest

`ChatRequest` is the provider-independent model request.

```text
ChatRequest
├── messages
├── system_prompt
├── context_documents
├── generation_config
├── response_format
├── tools
└── tool_interactions
```

The request separates different types of information instead of combining
everything into one prompt string.

This distinction is important for provider translation and future agent
execution.

`ChatResponse` contains the final or intermediate assistant `text` and ordered
`tool_invocations`. A `ToolInteractionRound` pairs one tool-requesting
`ChatResponse` with its ordered `ToolResult` values. The shared round model
validates that every result corresponds to one invocation in the same order.

## Conversation Messages

Conversation history contains user and assistant messages.

```text
messages
├── user
├── assistant
├── user
└── assistant
```

The current conversation history is stored in memory.

For each user turn:

```text
User Input
    ↓
Append User Message
    ↓
Create ChatRequest
    ↓
Provider Response
    ↓
Append Assistant Message
```

Conversation state is lost when the process ends.

Persistent sessions are not yet implemented.

## System Prompt

The system prompt contains the active assistant instructions.

It remains separate from user and assistant conversation messages.

```text
ChatRequest.system_prompt
        ↓
Provider Adapter
├── Ollama: system message
├── OpenAI: instructions
└── Anthropic: system parameter
```

The system prompt is included in every provider request in the session.

It is not inserted into conversation history as a user message.

## Agent Profiles

Agent profiles provide reusable identity and behavior.

```text
AgentProfile
├── name
├── description
└── system_prompt
```

Built-in profiles are stored as packaged TOML resources:

```text
profiles/
├── developer.toml
├── planner.toml
├── reviewer.toml
└── tester.toml
```

Custom profile files use the same shared representation.

```text
Built-In Profile ─────┐
                      ├── Profile Loader
Custom Profile ───────┘
                             ↓
                         AgentProfile
                             ↓
                    RuntimeConfiguration
```

Agent profiles do not currently contain:

* Provider selection.
* Model selection.
* Generation configuration.
* Context files.
* Response formats.
* Tools.
* Permissions.

These remain separate runtime concerns.

Future agent-session configuration may combine these concerns at a higher
application layer without changing `AgentProfile` into a provider-specific
object.

## Context Documents

Explicit context files are loaded as `ContextDocument` objects.

```text
ContextDocument
├── source
└── content
```

The loading pipeline is:

```text
--context-file
        ↓
Path Validation
        ↓
UTF-8 Content Loading
        ↓
ContextDocument
        ↓
RuntimeConfiguration.context_documents
        ↓
ChatRequest.context_documents
```

Context documents remain separate from conversation history.

Each provider translates the documents into its own instruction or system
format.

```text
Context Documents
├── Ollama: system message content
├── OpenAI: instructions content
└── Anthropic: system content
```

The current implementation sends the complete contents of every selected file
with each request.

It does not currently provide:

* On-demand file tools.
* Directory scanning.
* Code symbol search.
* Embeddings.
* Vector retrieval.
* Project indexing.
* Retrieval-Augmented Generation.

These capabilities belong to future workspace and retrieval layers.

## Generation Configuration

Portable generation parameters are represented through `GenerationConfig`.

```text
GenerationConfig
├── temperature
├── top_p
└── max_output_tokens
```

All fields are optional.

When a field is not supplied, providers preserve their normal defaults where
possible.

Provider translation:

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

Anthropic requires a maximum output-token value for every request.

When no shared value is supplied, the Anthropic adapter uses its existing
fallback.

Provider-specific generation controls are intentionally excluded from the
shared object.

## Structured Outputs

Portable structured output requests are represented through
`JSONResponseFormat`.

```text
JSONResponseFormat
├── name
└── schema
```

The configuration path is:

```text
--response-format-file
        ↓
Response Format Loader
        ↓
JSONResponseFormat
        ↓
RuntimeConfiguration.response_format
        ↓
ChatRequest.response_format
        ↓
Provider Adapter
```

Provider translation:

```text
JSONResponseFormat
├── Ollama
│   └── format = schema
│
├── OpenAI
│   └── text.format
│       ├── type = "json_schema"
│       ├── name
│       ├── schema
│       └── strict = true
│
└── Anthropic
    └── output_config.format
        ├── type = "json_schema"
        └── schema
```

When no response format is supplied, structured output arguments are omitted.

Provider responses are currently returned as strings, including JSON
responses.

The application does not yet deserialize or locally validate generated JSON
against the configured schema.

## Provider Adapters

Each provider adapter owns its native SDK interaction.

### Ollama

```text
ChatRequest
    ↓
OllamaProvider
    ↓
ollama.chat(...)
    ↓
ChatResponse
```

The adapter translates:

* Conversation messages.
* System instructions.
* Context documents.
* Generation parameters.
* JSON Schema format configuration.
* Tool definitions, tool-call history, and tool results.

### OpenAI

```text
ChatRequest
    ↓
OpenAIProvider
    ↓
OpenAI Responses API
    ↓
ChatResponse
```

The adapter translates:

* Conversation history into Responses API input.
* System instructions into `instructions`.
* Generation configuration.
* Strict structured output configuration through `text.format`.
* Function definitions, function-call inputs, and function-call outputs.

### Anthropic

```text
ChatRequest
    ↓
AnthropicProvider
    ↓
Anthropic Messages API
    ↓
ChatResponse
```

The adapter translates:

* Conversation history into Anthropic messages.
* System instructions and context into the system parameter.
* Generation configuration.
* Structured output configuration through `output_config.format`.
* Tool definitions, `tool_use` blocks, and `tool_result` blocks.

## Error Boundaries

Provider-specific exceptions should not escape directly into the conversation
layer.

Each adapter is responsible for translating SDK failures into clear
application errors.

Examples include:

* Missing API credentials.
* Provider connection failures.
* Authentication failures.
* Unsupported models.
* Invalid provider parameters.
* Malformed provider responses.

Validation that can be performed locally should occur before provider
construction or before the request is sent.

## Testing Architecture

Automated tests do not use real paid provider APIs.

External SDK clients are replaced with deterministic test doubles.

```text
Application Code
    ↓
Fake Provider or Fake SDK Client
    ↓
Captured Request Arguments
    ↓
Assertions
```

The test suite verifies:

* CLI parsing.
* Environment configuration.
* Runtime precedence.
* Agent profile loading.
* Context loading.
* Generation validation.
* Structured output validation.
* Provider request translation.
* Error handling.
* Conversation history.
* Interactive setup behavior.
* Provider factory behavior.
* Tool translation, execution ordering, history, and CLI integration.
* Deterministic coding phases with scripted providers and real temporary Git
  repositories.

Automated workflow tests never call Ollama or a paid/cloud provider. Manual
benchmarks with real local models are separate future evaluation work and must
not be confused with the deterministic scripted-provider regression battery.

## Current Layer Boundaries

```text
Interface Layer
├── CLI argument parsing
├── Prompt-based setup
└── Interactive conversation display

Application Layer
├── Runtime configuration
├── Conversation flow
├── Shared request objects
├── Agent profiles
├── Context documents
├── Generation configuration
└── Structured output configuration

Tool Execution Layer
├── ToolRegistry
├── synchronous handlers
├── immutable approval requests
├── ToolInteractionRound
└── run_tool_calling_loop

Provider Layer
├── Ollama adapter
├── OpenAI adapter
└── Anthropic adapter

External Systems
├── Ollama
├── OpenAI API
└── Anthropic API
```

Future features should respect these boundaries.

For example, filesystem tools should belong to a workspace or tool-execution
layer rather than being implemented inside provider adapters.

## Planned Architectural Layers

The expected future architecture is:

```text
VS Code or Terminal Interface
        ↓
Workspace Application
        ↓
Agent Session Manager
        ↓
Orchestrator
        ↓
Agent Runtime
├── Conversation
├── Tools
├── Context
├── Retrieval
├── Permissions
└── State
        ↓
Provider-Independent Model Layer
        ↓
Provider Adapters
        ↓
Local or Cloud Models
```

These layers are not all implemented yet.

They describe the intended direction and should not be interpreted as current
functionality.

## Tool Calling Boundary

Tool calling is provider-independent. `ToolDefinition` describes a named tool,
its description, and JSON object input schema. `ToolInvocation` carries the
provider-native call identifier, tool name, and JSON object arguments.
`ToolResult` associates a successful JSON-compatible output or safe error with
one invocation identifier.

```text
ChatRequest.tools
        ↓
Provider-specific tool definition translation
        ↓
ChatResponse.tool_invocations
        ↓
ToolRegistry synchronous handler execution
        ↓
ToolInteractionRound
        ↓
Provider-specific interaction-history translation
        ↓
Final ChatResponse
```

`run_tool_calling_loop()` repeatedly completes a request, executes ordered
invocations through `ToolRegistry`, appends validated interaction rounds, and
stops on a response without tool invocations. Its positive maximum-round
argument protects against unbounded new tool rounds; pre-existing rounds are
forwarded without re-execution.

`AgentSession.send()` may additionally receive an allowlist and maximum tool
rounds for one call. The allowlist filters provider-visible definitions and is
enforced again before execution. A withheld invocation produces a deterministic
error `ToolResult` and cannot reach preview, approval, or execution. Omitting
both options preserves the configured session behavior and default round limit.

Registrations may mark a handler as approval-required and supply a deterministic
preview callback. `ToolApprovalRequest` snapshots the exact immutable
invocation and strict-JSON preview. Only the caller-owned
`ToolApprovalHandler` can return explicit `APPROVE` or `DENY`. An effectful
invocation must be alone in its provider response, approval is never cached,
and preview, absence, denial, invalid decision, or handler failure all prevent
execution. Approval data is not inserted into provider history or normal
session messages.

Provider adapters retain native protocol details:

* Ollama translates function definitions, assistant `tool_calls`, and ordered
  `tool` result messages. It has no native call identifier, so validated round
  ordering preserves correlation.
* OpenAI translates Responses API function tools, `function_call` items, and
  `function_call_output` items.
* Anthropic translates Messages API tools, assistant `tool_use` blocks, and
  user `tool_result` blocks.

The CLI keeps tools opt-in. `--enable-tools` registers the safe synchronous
calculator, while `--workspace PATH` authorizes the read-only `list_files`,
`read_file`, `search_text`, `search_symbols`, `inspect_git_status`, and
`inspect_git_diff` tools for one root. `--enable-actions` requires that
workspace and appends `apply_file_patch`, `apply_text_replacement`,
`apply_workspace_changes`, `run_ruff_format`, `run_ruff_check`, and
`run_pytest`. The calculator, when
enabled, remains first. Without a tool option or workspace, no registry is
created.
`--show-tool-traces` adds an optional callback for completed provider-independent
rounds; traces are compact JSON, redacted, and excluded from normal CLI history.
Internal tool rounds remain inside a single loop and are not persisted in normal
CLI history across later user turns.

This separation keeps provider adapters declarative and prevents them from
directly executing application capabilities.

## Workspace Boundary

Workspace access is explicit through `Workspace(root)`, a frozen slotted model
that stores a canonical existing directory. It resolves requested paths
strictly before containment checks, rejecting absolute paths, traversal,
prefix-confusion, and symlink escapes. Symlinks resolving inside the root are
permitted.

```text
Workspace
├── root
├── files
├── Git state
├── permissions
└── available tools
```

`list_files` returns deterministic sorted direct children, including hidden
entries, and caps a directory at 128 entries. `read_file` accepts strict UTF-8
only and caps content at 100 KiB. Both report canonical relative paths. They
are read-only. `search_text` provides bounded literal recursive search of
regular UTF-8 files in deterministic relative order, skips invalid UTF-8 and
directory symlinks, and reports truncation at its query, file, byte, match, and
line limits.

`search_symbols` parses Python with `ast.parse()` and never imports or executes
inspected code. Lexical AST scope identifies classes, top-level and nested
functions, asynchronous functions, methods, and nested classes. The portable
kinds are `class`, `function`, and `method`; `any` is the unfiltered input
value, and `is_async` independently identifies asynchronous functions and
methods. Literal name and qualified-name matching is case-insensitive by
default. Results are ordered by canonical relative path, line, and qualified
name.

Recursive symbol search includes hidden paths, skips directory symlinks, and
deduplicates canonical files. Explicit internal file symlinks resolve to their
canonical targets. Invalid UTF-8, `SyntaxError`, and oversized files are
skipped with `files_skipped` during directory search and rejected safely when
requested explicitly. Limits are 256 query characters, 512 Python files,
100 KiB per file, 256 matches, and 512 qualified-name characters;
`truncated` reports bounded file, match, or name handling.

Git inspection runs only fixed non-shell status and diff commands with external
helpers disabled, a three-second timeout, and 100 KiB output limits; it
separates unstaged and staged diff results.

The controlled write boundary is intentionally stricter than reads.
`apply_file_patch` never follows a target or parent symlink, rejects `.git`,
and supports only one complete-content compare-and-swap update or creation.
The preview contains the complete deterministic unified diff and bounded
metadata. Execution validates again after approval; existing files use a
same-directory temporary file and atomic replacement with portable permission
preservation, while new files use exclusive creation.

`apply_text_replacement` performs a smaller optimistic update for existing
UTF-8 files. It requires an exact non-empty literal fragment, replacement
text, the expected number of non-overlapping occurrences, and the SHA-256
returned by the latest `read_file` result. The complete file is hashed even
when `read_file` returns only a bounded line range. Preview and execution
reconstruct the complete replacement internally, enforce the existing patch
limits, and reuse the same complete diff, post-approval revalidation,
permission preservation, and atomic replacement boundary as
`apply_file_patch`. It does not support regular expressions, creation,
deletion, rename, or ambiguous occurrence counts.

`apply_workspace_changes` adds an immutable multi-file plan without weakening
that per-file boundary. Its provider-independent schema is one closed object
containing a `changes` array. Each closed change object requires `path`,
`expected_content`, and `replacement_content`, with optional
`create_if_missing`. No alternate nested patch shape or automatic argument
repair is accepted.

```text
request
  ↓
preflight every target and complete diff
  ↓
canonical-path duplicate rejection and relative-path sorting
  ↓
one complete combined approval preview
  ↓
prepare replacement and rollback material for every change
  ↓
post-approval full-plan revalidation
  ↓
commit in sorted order
  ↓ handled in-process failure
rollback applied changes in reverse order
```

Both the original request order and supplied content remain unmodified; only
the internal plan is sorted by canonical relative path for deterministic
preview, commit, result metadata, and rollback. Duplicate canonical targets
are rejected even when written with different relative spellings. Preflight
and post-approval revalidation cover every target, complete expected content,
operation, diff, and limit so a stale plan writes nothing.

The existing, expected, and replacement content for each file is limited to
100 KiB, with at most 500 added and removed lines. A transaction contains
1–16 files and allows at most 512 KiB of combined expected content, 512 KiB
of combined replacement content, 2,000 changed lines, and 256 KiB for the
complete JSON approval preview containing every diff.

The preparation phase writes same-directory replacement files and update
backups without changing targets. The commit phase atomically replaces updates
or exclusively creates missing targets one at a time. If a handled commit
failure occurs, rollback restores applied updates and removes applied
creations in reverse order, then temporary artifacts are cleaned.

This is transaction-like recovery for handled in-process failures, not global
filesystem atomicity. The guarantee holds only when rollback itself succeeds;
it does not cover power loss, `SIGKILL`, abrupt process or operating-system
termination, filesystem or disk failure, or rollback failure. An incomplete
rollback raises a safe completion error containing only the relative paths
that must be inspected manually. The CLI renders the warning without exposing
absolute host paths or committing the failed conversation turn.

Validation uses the current Python interpreter with fixed Ruff or pytest
module arguments, `shell=False`, a canonical workspace cwd, minimal offline
environment, isolated process groups, timeouts, and streaming 100 KiB limits
for each output stream. Ruff and pytest findings are normal results; start,
capture, module, and timeout failures are safe errors.

The reusable factory constructs definitions and handlers but never prompts.
The CLI owns informed display and exact-invocation approval. `AgentSession`
forwards the callback transactionally, so denied or invalid turns roll back
while prior successful conversation remains.

Read access, controlled writes, fixed command execution, network access, and
Git lifecycle mutations remain separate permissions. Arbitrary commands,
deletion, rename, crash-safe transaction journaling, and network/MCP
capabilities remain future work.

## Git Worktree Isolation Boundary

Worktree lifecycle management is operator-side application behavior, not a
provider tool. `WorktreePlan` is a frozen, slotted, validated-only model that
pins the canonical primary repository, complete source HEAD, exact new branch,
canonical absent target, and safe target display. `plan_git_worktree()` is
read-only and requires:

* The supplied path itself to be the top-level primary worktree with a real
  `.git` directory, not a linked or bare repository.
* An existing complete HEAD and a completely clean status including staged,
  unstaged, and untracked files.
* No merge, rebase, cherry-pick, revert, bisect, sequencer, or lock state.
* A non-option Git-valid new local branch that does not already exist.
* An absent target with an existing directory parent and no symlinked parent
  component.
* No source containment, `.git` containment, or collision with registered,
  locked, prunable, or ambiguous worktrees.
* No repository-local clean, smudge, process, external-diff, command, or
  text-conversion configuration that could execute during checkout.

The plan preview exposes only `.`, the pinned HEAD, branch, safe target display,
fixed command tokens, and explicit local-only effects. Absolute canonical paths
remain private.

```text
Clean primary repository
        ↓
WorktreePlan pins source HEAD, branch, and absent target
        ↓
WorktreeApprovalRequest(CREATE)
        ↓ explicit APPROVE
post-approval complete revalidation
        ↓
fixed local worktree creation
        ↓ complete identity verification
WorktreeHandle
        ↓
create_isolated_agent_session()
        ↓
AgentSession bound only to the isolated Workspace
```

Every Git process uses `shell=False`, a short timeout, bounded streaming output,
process-group termination, a minimal credential-free environment,
`GIT_CONFIG_NOSYSTEM=1`, an isolated global configuration, and fixed
`core.hooksPath=/dev/null` and `core.fsmonitor=false` overrides. Creation's
only mutating boundary is equivalent to:

```text
git -C SOURCE \
  -c core.hooksPath=/dev/null \
  -c core.fsmonitor=false \
  worktree add -b BRANCH TARGET PINNED_HEAD
```

Removal's only mutating boundary is equivalent to:

```text
git -C SOURCE \
  -c core.hooksPath=/dev/null \
  -c core.fsmonitor=false \
  worktree remove TARGET
```

There is no `--force`, branch deletion, prune, checkout of the primary tree,
commit, merge, rebase, push, fetch, reset, clean, stash, arbitrary subcommand,
caller flag, or network operation.

`create_git_worktree()` requires an explicit `ToolApprovalDecision` from a
dedicated operator-side approval request. Approval is exact, one-use, and
never cached. It re-plans after approval and creates only when every pinned
condition is unchanged. A `WorktreeHandle` is returned only after registration,
target, branch, HEAD, source cleanliness, local branch existence, and absent
upstream are verified. On command or verification failure, bounded read-only
inspection reports whether the branch, target, and registration exist; partial
or ambiguous state is preserved for manual recovery.

`inspect_git_worktree()` revalidates the handle's source, target,
registration, branch, and HEAD on every call and returns only safe immutable
state and a changed-entry count. `plan_git_worktree_removal()` accepts only a
registered, attached, completely clean worktree; its HEAD may have advanced
through a manual commit. `remove_git_worktree()` requests a separate exact
approval, revalidates the clean plan, uses no force, verifies target and
registration removal, and verifies that the local branch and source identity
remain. Dirty, untracked, locked, switched, detached, missing, failed, or
ambiguous worktrees are preserved.

`create_isolated_agent_session()` revalidates the handle and source
configuration, uses `dataclasses.replace()` to bind a copied
`RuntimeConfiguration` to the worktree, and delegates to the existing session
factory. Provider, model, profile, prompt, generation, response-format, tool,
action, trace, and maximum-round behavior are preserved. Every registry
workspace capability captures only the isolated `Workspace`.

Context documents are not reused from the primary tree. A source-contained
context path is mapped by its relative path, reloaded and revalidated inside
the isolated worktree, and kept in original order. Missing mapped or external
context is rejected. Construction failure returns no session and preserves the
created worktree and branch.

The CLI activates this boundary only when both `--worktree-path` and
`--worktree-branch` accompany `--workspace`. It separately prompts for
creation, runs the normal CLI with one prebuilt isolated session, then
re-inspects. Dirty output is preserved with safe recovery information and may
enter the separately approved commit boundary below. A clean worktree may be
removed only after its own default-deny approval, and its local branch remains.
Successful local Git commands cannot provide crash-safe guarantees; unexpected
state always requires manual recovery.

### Approved isolated commit boundary

`IsolatedCommitPlan` is a frozen, slotted, validated-only snapshot associated
with a verified `WorktreeHandle`. It pins the isolated branch and old HEAD,
primary branch and HEAD, repository-local author identity, exact commit
message, deterministic complete path set, operations, old and current bytes,
complete unified diffs, aggregate counts, and a SHA-256 fingerprint. Canonical
source and worktree paths remain private.

Planning is read-only. It revalidates the source and target identities, active
Git operations, absence of upstream tracking, and a clean real index. Every
current change must be eligible; no path is silently excluded. The first
boundary supports:

* Modifications to tracked UTF-8 regular files.
* New untracked UTF-8 regular files.

It rejects deletions, renames, copies, mode changes, symlinks, submodules,
binaries, conflicts, staged or intent-to-add entries, unsafe or ignored paths,
and arbitrary path selection outside the plan. Limits are 100 KiB each for a
file's old and current content, 500 changed lines per file, 32 files, 1 MiB
combined old bytes, 1 MiB combined current bytes, 4,000 combined changed lines,
a 512 KiB complete preview, and a 4 KiB UTF-8 commit message.

```text
Dirty verified isolated worktree
        ↓
Validate every changed entry
        ↓
Require clean index
        ↓
Create immutable commit plan
        ↓
Review exact message, paths, and complete diffs
        ↓
Approve once
        ↓
Revalidate branch, HEAD, index, paths, contents, and diff
        ↓
Stage only approved paths
        ↓
Verify exact staged path set and diff
        ↓
Create fixed local commit
        ↓
Verify parent, message, paths, diff, index, and worktree
        ↓
Verify primary worktree unchanged
        ↓
Optionally approve clean worktree removal
        ↓
Preserve local branch and commit
```

`IsolatedCommitApprovalRequest` contains an independent immutable copy of the
complete preview. Approval is explicit, exact, one-use, and never available to
the model. After approval, the implementation regenerates and compares the
entire plan before any staging. It stages only the sorted approved paths,
reconstructs their actual index blobs and modes, and requires the exact
approved staged path set, operations, content, diffs, identity, and
fingerprint.

The only mutating commit forms are equivalent to:

```text
git -C WORKTREE add -- APPROVED_PATHS...

git -C WORKTREE \
  -c core.hooksPath=/dev/null \
  -c commit.gpgSign=false \
  -c tag.gpgSign=false \
  -c core.editor=false \
  commit \
  --no-verify \
  --no-gpg-sign \
  --file=-
```

The exact message is supplied through standard input. Git hooks, signing, and
editors are disabled; the repository-local `user.name` and `user.email` are
required and pinned. There is no add-dot, add-all, commit-all, amend, merge,
rebase, push, fetch, pull, reset, clean, stash, restore, checkout, switch,
branch deletion, force removal, arbitrary Git subcommand, arbitrary flag,
shell execution, or caller-controlled environment.

After the command, verification requires one new commit whose sole parent is
the approved old HEAD, the exact approved message, exact add/modify path set,
tree blobs, modes, and diffs, a clean index and worktree, the same isolated
branch without upstream, and the unchanged primary branch, HEAD, and clean
state. `IsolatedCommitResult` exposes only safe branch, old/new HEAD, message,
ordered paths, and counts. Clean removal is then planned and approved
separately; it preserves the local branch and reachable commit.

The boundary is deliberately not transactional across staging and commit.
Before staging, stale state causes no Git mutation. Once staging begins, a
failure may leave the exact or a partial index staged; the implementation
reports bounded relative staged paths and performs no automatic unstage or
cleanup. Once commit begins, an unexpected advanced HEAD or unverifiable
metadata remains in place. There is no retry, amend, reset, restore, clean,
stash, forced removal, or branch deletion. The operator must inspect the
isolated index, HEAD, branch, and worktree manually. This is conservative
handled-failure behavior, not crash-safe recovery.

## Agent Session Boundary

`AgentSession` is the provider-independent application boundary for one
configured synchronous conversation. The CLI requests one session and remains
responsible for input, output, headers, exit handling, configuration
resolution, error rendering, and tool-trace formatting.

Session construction is reusable through `create_agent_session()`. Callers
supply an explicit `SessionId` and an already resolved `RuntimeConfiguration`.
Argument parsing, environment resolution, profile/context loading, and
interactive setup happen before this boundary. The factory creates the
provider, forwards the resolved profile, prompt, context, generation, and
response-format models, constructs the optional workspace and registry, and
returns one `AgentSession`. It performs no input, output, trace rendering,
completion, lifecycle management, or conversation mutation.

```text
RuntimeConfiguration resolution
        ↓
create_agent_session()
        ↓
AgentSession
        ↓
ChatProvider / run_tool_calling_loop()
        ↓
CLI rendering
```

Factory tool construction remains deterministic:

* No tool options: no registry.
* Calculator only: `calculator`.
* Workspace only: `list_files`, `read_file`, `search_text`,
  `search_symbols`, `inspect_git_status`, `inspect_git_diff`.
* Combined: `calculator`, followed by the workspace-only order.

All workspace registrations share one canonical `Workspace`. Trace enablement
stays in runtime presentation configuration and is never applied by the
factory. The CLI now owns only configuration resolution, its explicit
CLI-scoped session identifier, terminal presentation, trace formatting, and
error presentation; it does not construct providers, workspaces, registries,
or session fields.

```text
AgentSession
├── SessionId
├── ChatProvider
│   ├── provider name
│   └── provider-derived model name
├── optional AgentProfile metadata
├── resolved system prompt
├── ContextDocument tuple
├── GenerationConfig
├── optional JSONResponseFormat
├── optional TaskSpec metadata
├── optional ToolRegistry
├── maximum tool rounds
├── owned successful Message history
└── SessionStatus
    ├── ready
    ├── completing
    └── failed
```

`SessionId` is immutable and caller-supplied. Model identity is read from the
configured provider rather than duplicated. Context, generation, response
format, and ordered tool definitions are forwarded through the existing
`ChatRequest` contract on every turn.

`TaskSpec` provides optional immutable session metadata containing one exact
objective and an ordered tuple of acceptance criteria. The public
`AgentSession.task_spec` property is read-only and defaults to `None`.

The base session does not automatically forward the task specification through
`ChatRequest`, translate it in provider adapters, evaluate it in the tool loop,
or use it to control session lifecycle. Application controllers may consume
the metadata explicitly; the deterministic coding controller includes its
objective and ordered acceptance criteria in each model-facing phase prompt.

The synchronous `send()` operation rejects blank content and re-entrant sends,
sets the status to `completing`, and selects direct provider completion or the
existing `run_tool_calling_loop()`. It accepts optional per-send tool names and
a per-send round limit, forwards an optional observer, and does not implement
another tool loop. Per-send values never mutate the session defaults.

Conversation updates are transactional. A successful send appends the user
message and final assistant text exactly once and returns to `ready`; internal
tool rounds are not persisted in normal conversation history. Provider,
tool-loop, maximum-round, or observer exceptions leave prior history unchanged,
set `failed`, and propagate unchanged. A later successful send is allowed and
returns the session to `ready`.

The base session boundary now supports optional immutable `TaskSpec` metadata,
but it does not implement task assignment, task lifecycle, dependencies,
acceptance evaluation, multiple simultaneous sessions, orchestration,
persistence, serialization, concurrency, cancellation, asynchronous APIs, RAG,
MCP, VS Code, or voice input. The additive isolated-session factory binds one
such session to one validated worktree without changing those boundaries.

## Deterministic Coding Controller

`run_autonomous_coding_task()` is an implemented single-session application
controller. It owns a typed `CodingPhase`, bounded `CodingWorkflowLimits`, and
accumulated tool evidence. Every model-facing prompt repeats the sanitized
original objective and ordered `TaskSpec` acceptance criteria. Model-facing
work is restricted to:

* `DISCOVER`: existing read-only repository/workspace tools, with a four-round
  maximum. Successful rounds produce typed evidence containing only bounded
  safe workspace-relative paths and allowlisted metadata. Item, per-item
  character, and combined character limits prevent tool output from becoming
  unbounded. A normal completion also contributes a bounded sanitized
  discovery summary.
* `EDIT`: read-only tools plus controlled patch, replacement, and transaction
  actions. Its prompt carries the explicit discovery evidence even when the
  DISCOVER send exhausted its round limit and its conversation turn was rolled
  back.
* `REPAIR`: the same tools as EDIT, with the original objective, failed
  validation names and exit codes, bounded redacted output, safe changed paths,
  and current counters repeated in every prompt.

The controller invokes `run_ruff_format`, `run_ruff_check`, and `run_pytest`
against `"."` in that exact order through the registered preview, explicit
approval, and execution interfaces. Any error or non-zero exit code enters
REPAIR. A successful repair must contain a new successful workspace action,
after which the complete validation sequence runs again. Two EDIT completion
continuations, two repair attempts, and two completion continuations per repair
are the v1 defaults.

If one EDIT or REPAIR send raises the exact maximum-tool-round error, the
controller inspects only tool rounds from that call. A successful approved
workspace change advances to validation with a deterministic fallback summary.
Otherwise the send consumes one existing bounded completion continuation and
the next prompt states the exhaustion reason. Other completion errors remain
terminal.

After the latest validation sequence succeeds, the controller invokes
`inspect_git_status` and `inspect_git_diff`. DONE requires a successful
workspace action, a non-empty tracked/staged Git diff, successful latest Ruff
format/check/pytest results, and successful final Git inspections. A model
response cannot select DONE or override these gates. Terminal failures raise a
phase-specific `CompletionError`, and the isolated workflow cannot plan a
commit unless the result phase is DONE.

`AutonomousCodingResult` preserves tool rounds, exact `ToolResult` snapshots,
executed and approved tool names, validation runs, Git inspection evidence, and
assistant summary. It also exposes final phase, workspace-change evidence,
repair attempts, and completion continuations for stable CLI rendering.

The v1 controller remains Python-specific. Its final diff gate uses the
existing Git diff inspection, so a task whose only changes are new untracked
files cannot reach DONE until a future inspection contract represents those
contents.

## Broader Orchestration Boundary

Broader multi-session orchestration remains planned. It should coordinate
sessions and tasks without weakening the implemented deterministic coding
controller.

It should not:

* Contain provider-specific API calls.
* Directly manipulate provider SDK request formats.
* Bypass workspace permissions.
* Modify files without a tool executor.
* Hide agent activity from the user.

The orchestrator may:

* Assign tasks.
* Track dependencies.
* Request agent reviews.
* Pass approved results between sessions.
* Detect blocked work.
* Collect final outputs.

## Persistence

Current state is process-local and in memory.

The project does not yet persist:

* Conversation history.
* Agent sessions.
* Setup selections.
* Task state.
* Tool traces.
* Project indexes.
* Evaluation results.

Persistence should be added only after the session and task models are stable.

## Security Principles

The architecture follows these current security rules:

* Secrets remain outside source code.
* Local `.env` files are ignored by Git.
* Context files are validated before provider creation.
* Unsupported paths and formats are rejected.
* Provider-specific errors are translated.
* Automated tests do not call paid APIs.
* Existing environment variables are not overwritten by `.env`.

Current workspace execution adds explicit authorization, path containment,
fixed-command confirmation, approved writes, and optional redacted traces.
Worktree creation adds write isolation for one supervised local session, and
the approved commit boundary can complete eligible reviewed add/modify work on
that isolated branch. Broader destructive-action permissions, persistent audit
records, stronger secret isolation, and network-access controls remain future
work.

## Architectural Non-Goals

The current architecture does not yet provide:

* Fully autonomous agents.
* Multiple simultaneous agent sessions.
* Shell execution.
* Project indexing.
* Retrieval-Augmented Generation.
* Persistent task state.
* Automatic commit approval, merge, push, or branch deletion.
* Concurrent worktree orchestration.
* A VS Code extension.
* Background execution.
* Cloud deployment.

The current tool implementation is synchronous and contains the opt-in
calculator, explicitly authorized workspace inspection, approved structured
file changes, and fixed Ruff/pytest validation. Worktree lifecycle operations
remain operator-side and are not model tools. Network, MCP, asynchronous, and
user-defined tools are not included.

The presence of future-oriented abstractions in documentation does not imply
that these capabilities are already implemented.

## Related Documentation

* [Product Vision](product-vision.md)
* [Getting Started](getting-started.md)
* [Runtime Configuration](runtime-configuration.md)
* [Agent Profiles](agent-profiles.md)
* [Context Files](context-files.md)
* [Structured Outputs](structured-outputs.md)
* [Roadmap](roadmap.md)
