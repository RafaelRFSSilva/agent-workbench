# Development Log

## 2026-07-27 — Approved Isolated Git Commits

### Implemented

- Added immutable validated `IsolatedCommitPlan` snapshots and complete safe
  commit previews in commit `7b77a2f`.
- Added exact one-use approval, post-approval revalidation, exact-path staging,
  staged-diff verification, fixed commit creation, result metadata, and
  conservative partial-state reporting in commit `708ca82`.
- Added the operator-side CLI message, complete preview, default-deny commit
  approval, success reinspection, and separate clean-removal flow in commit
  `51033ed`.
- Added the practical self-hosting operator guide and updated the worktree,
  runtime, architecture, and roadmap documentation in this milestone.

### Commit Architecture

- `IsolatedCommitPlan` is frozen, slotted, and validated-only. It pins the
  verified `WorktreeHandle`, source and isolated identities, exact branch and
  old HEAD, repository-local author identity, unchanged message, deterministic
  complete path set, add/modify operations, old/current bytes, complete unified
  diffs, aggregate counts, and a SHA-256 fingerprint.
- Planning requires a clean real index and includes every eligible change.
  Supported entries are modified tracked and new untracked UTF-8 regular files.
  Deletions, renames, copies, mode changes, symlinks, submodules, binaries,
  conflicts, pre-staged content, ignored/unsafe paths, and partial selection
  are rejected.
- Approval is operator-side, exact, one-use, and never exposed as a model tool.
  The complete plan is regenerated after approval and before staging.
- Staging is limited to exact approved paths. Actual index blobs, modes, path
  set, operations, diffs, identities, and fingerprint must match the plan.
- Commit verification requires one new commit with the exact old HEAD parent,
  message, path set, tree content, modes, and diffs; a clean isolated index and
  worktree; the same branch without upstream; and unchanged primary identity.
- Clean worktree removal retains its independent approval and preserves the
  local branch and commit.

### Fixed Git Mutation Boundary

The approved paths are staged only through the equivalent of:

```text
git -C WORKTREE add -- APPROVED_PATHS...
```

The commit is created only through the equivalent of:

```text
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

The exact message is supplied through standard input. Hooks, editor, and
signing are disabled. Add-dot, add-all, commit-all, amend, merge, rebase, push,
fetch, pull, reset, clean, stash, restore, checkout, switch, branch deletion,
force removal, arbitrary Git, arbitrary shell, and caller-controlled
environment are outside the boundary.

### Recovery Policy

- Stale or rejected state before staging causes no Git mutation.
- After staging starts, failure may leave a partial or complete isolated index.
  The error reports bounded relative staged paths and performs no reset,
  restore, clean, stash, unstage, or retry.
- After commit starts, an advanced or ambiguously verified HEAD remains
  preserved. There is no amend, destructive cleanup, removal, or branch
  deletion.
- The operator must inspect the isolated branch, HEAD, index, and worktree
  manually. This is handled-failure preservation, not transactionality across
  Git staging/commit and not crash-safe recovery.

### `COMMIT-842` Real Local Validation

- A real local `gpt-oss:20b` session used only
  `create_isolated_agent_session()` with a worktree pinned to a fresh clean
  primary HEAD on `agent/commit-842`.
- The model made the exact ordered calls: three reads, one approved
  `apply_workspace_changes` transaction for `demo/labels.py` and
  `demo/math_ops.py`, Ruff format, Ruff check, pytest, Git status, and Git diff.
  It returned all required `COMMIT-842`, isolation, Ruff, pytest, and
  ready-to-commit markers.
- Ruff and pytest passed. Tests and project configuration were unchanged. The
  isolated index remained clean before commit planning, exactly two worktree
  entries were dirty, normal session history contained only the user and final
  assistant messages, and the primary tracked bytes, status, branch, and HEAD
  remained unchanged.
- One complete immutable preview for `fix: correct demo behavior` was approved
  separately. Exact paths were staged; the exact message was supplied through
  standard input; one commit was created and its parent, message, path set,
  diff, index, worktree, branch, and absent upstream were verified.
- Clean removal was approved separately. The worktree and registration were
  removed while the local branch and commit remained reachable.
- Fresh-repository smokes passed for denied, missing, and malformed approval;
  file/path/HEAD/branch/source/index staleness; partial staging; staged-diff
  mismatch; fixed commit failure; ambiguous advanced HEAD; and unsupported
  deletion, binary, symlink, staged-index, and missing-identity planning.
- No merge, push, fetch, pull, amend, reset, restore, clean, stash, force
  removal, or branch deletion occurred.

### Validation and Self-Hosting Readiness

- Stage 3 focused commit-lifecycle tests pass with 24 tests.
- The complete automated suite passes with 804 tests before and after the
  documentation-only stage.
- Ruff formatting, Ruff linting, and `git diff --check` pass.
- Agent Workbench is ready for small supervised local feature tasks using the
  operator playbook in `docs/self-hosting.md`.
- Execution remains synchronous. Only eligible UTF-8 add/modify commits are
  supported. Push, Pull Request creation, merge, signing, deletion/rename,
  conflict automation, branch deletion, concurrent sessions, orchestration,
  persistence, and crash-safe recovery remain manual or future work.

## 2026-07-27 — Supervised Git Worktree Isolation

### Implemented

- Added immutable validated `WorktreePlan` creation in commit `6919568`.
- Added separately approved fixed-command worktree creation, inspection, and
  clean-only removal with verified `WorktreeHandle` state in commit `b9c318d`.
- Added `create_isolated_agent_session()` with source-relative context remapping
  and an isolated copied runtime configuration in commit `8261af4`.
- Added paired `--worktree-path` and `--worktree-branch` CLI integration,
  default-deny lifecycle prompts, dirty preservation, and optional clean
  removal in commit `c001cfa`.

### Security and Recovery Decisions

- Require the supplied source itself to be a clean top-level primary non-bare
  repository with an existing pinned HEAD and no in-progress Git operation.
- Validate new branch names through fixed `check-ref-format`, require local
  branch absence, and never infer or rewrite a name.
- Require an absent target with an existing non-symlinked parent; reject source
  and `.git` containment plus registered, locked, prunable, and ambiguous
  worktree collisions.
- Reject repository-local checkout filters, processes, external diff commands,
  and text conversions that could execute external programs.
- Isolate system/global Git configuration, disable hooks and fsmonitor, use a
  minimal credential-free environment, `shell=False`, bounded output, short
  timeouts, and process-group termination.
- Revalidate all safety-relevant state after exact one-use approval. Never
  cache approval or expose worktree lifecycle operations to the model as
  tools.
- Preserve dirty, partial, failed, unexpected, and ambiguous state for manual
  recovery. Never force-remove a worktree or automatically delete its branch.

### Fixed Git Mutation Boundary

Creation is limited to the equivalent fixed token sequence:

```text
git -C SOURCE \
  -c core.hooksPath=/dev/null \
  -c core.fsmonitor=false \
  worktree add -b BRANCH TARGET PINNED_HEAD
```

Clean removal is limited to:

```text
git -C SOURCE \
  -c core.hooksPath=/dev/null \
  -c core.fsmonitor=false \
  worktree remove TARGET
```

Read-only planning and verification use fixed `rev-parse`, `status`,
`worktree list`, `check-ref-format`, `show-ref`, `config`, `symbolic-ref`, and
upstream-inspection forms only. There is no force, prune, branch deletion,
commit, checkout of the primary tree, merge, rebase, push, fetch, reset, clean,
stash, arbitrary Git command, caller-controlled flag, or network operation.

### Isolated Session Boundary

- Return `WorktreeHandle` only after verifying target registration, pinned
  HEAD, requested branch, clean unchanged primary source, local branch
  existence, and absence of upstream tracking.
- Revalidate the handle before constructing a session and replace only the
  copied `RuntimeConfiguration.workspace_root`; every registered workspace
  capability targets the isolated worktree.
- Map context files inside the source by relative path and reload them from the
  isolated worktree in original order. Reject missing mapped or external
  context.
- Preserve provider, model, profile, prompt, generation, response format,
  opt-in tools, controlled actions, traces, and maximum-round behavior.

### WORKTREE-842 Validation

- A real local `gpt-oss:20b` session was created only through
  `create_isolated_agent_session()` from a worktree pinned to the clean primary
  HEAD.
- The model read `demo/math_ops.py`, `demo/labels.py`, and
  `tests/test_demo.py`; requested one approved two-file transaction; ran Ruff
  format, Ruff check, and pytest; inspected Git status and diff; and returned
  the four required `WORKTREE-842`, isolation, Ruff, and pytest markers.
- Exactly the two source files changed in the isolated worktree. Ruff and
  pytest exited successfully. The primary tracked bytes, Git status, branch,
  and HEAD remained unchanged; the external file and secret remained
  untouched; no commit or upstream was created.
- Dirty inspection reported two changed entries and rejected removal planning
  before approval, preserving the worktree and branch.
- A separate clean worktree was removed after its own exact approval; its
  registration and directory disappeared, its local branch remained, and the
  primary repository stayed clean.
- Direct smokes confirmed creation denial, stale HEAD, dirty source, branch and
  target collisions, partial-state reporting, session-construction
  preservation, removal denial, untracked-file rejection, failed-removal
  preservation, and absence of force or branch deletion.
- The complete automated suite passes with 728 tests.

### Current Limitations

- Worktree lifecycle operations are synchronous and local.
- There is no automatic commit, merge, push, branch deletion, reset, clean,
  stash, forced removal, crash-safe lifecycle journal, concurrent worktree
  coordination, orchestration, persistence, network tool, or MCP integration.
- Successful local Git commands cannot guarantee recovery after power loss,
  abrupt process termination, filesystem failure, or other external
  interruption; uncertain state requires manual inspection.

## 2026-07-27 — Approved Atomic Workspace Changes

### Implemented

- Added deterministic multi-file planning, complete combined approval
  previews, execution preparation, commit, and reverse-order rollback in
  commit `cc41632`.
- Registered `apply_workspace_changes` after `apply_file_patch` and integrated
  transaction previews, safe completion errors, trace redaction, and CLI
  warnings in commit `9cb4e47`.
- Improved the model-facing tool description and transaction-specific nested
  validation errors without weakening the closed schema in commit `bfde0e9`.
- Preserved single-file action behavior, provider-independent definitions,
  exact one-use approval, and the fixed Ruff and pytest execution boundary.

### Schema and Limits

- Accept one closed top-level object containing only `changes`; each array
  element is a closed object requiring `path`, `expected_content`, and
  `replacement_content`, with optional boolean `create_if_missing`.
- Validate and sort the internal plan by canonical relative path, reject
  duplicate canonical targets, and leave caller-supplied arguments and content
  unmodified.
- Retain per-file limits of 100 KiB for existing, expected, and replacement
  content and 500 changed lines.
- Limit one transaction to 16 files, 512 KiB each of combined expected and
  replacement content, 2,000 changed lines, and a complete 256 KiB combined
  approval preview.

### Transaction and Recovery Boundary

- Preflight every target and complete diff before approval, prepare every
  replacement and rollback artifact without changing targets, revalidate the
  complete plan after approval, then commit changes in sorted order.
- On a handled in-process commit failure, roll back applied changes in reverse
  order by restoring updates and removing transaction-created files.
- The guarantee applies only when rollback succeeds. It does not promise global
  filesystem atomicity or recovery from power loss, `SIGKILL`, abrupt process
  or operating-system termination, filesystem or disk failure, or rollback
  failure.
- An incomplete rollback reports only the relative paths requiring manual
  inspection. Preparation and stale-plan failures write nothing, and temporary
  transaction artifacts are cleaned after handled outcomes.

### Validation

- Focused recovery tests first failed in 7 cases because transaction nested
  validation reused the single-file error and the description did not list the
  required change fields; the focused suite passed with 81 tests after the
  minimal correction.
- The complete automated suite passed with 610 tests before documentation.
- Direct fault-injection smokes confirmed stale plans write nothing; complete
  rollback restores updates, removes creations, leaves later targets
  untouched, and cleans temporaries; incomplete rollback reports safe relative
  paths for manual inspection.
- A real factory-created localhost `gpt-oss:20b` session completed on its first
  attempt. It read `demo/math_ops.py`, `demo/labels.py`, and
  `tests/test_demo.py`; requested exactly one approved transaction changing
  only the two source files; ran Ruff format, Ruff check, and pytest; inspected
  Git status and diff; and returned:

  ```text
  ATOMIC-842
  transaction: passed
  ruff: passed
  pytest: passed
  ```

### Current Limitations

- Transactions cannot delete, rename, create directories, change modes, or
  accept arbitrary commands.
- Rollback is synchronous and in-process; crash-safe journaling, cross-device
  atomicity, cancellation, worktree isolation, persistence, concurrency,
  asynchronous execution, network tools, and MCP remain future work.

## 2026-07-27 — AgentSession Runtime Factory

### Implemented

- Added `create_agent_session()` in commit `a31a28f` as the reusable boundary
  from resolved `RuntimeConfiguration` to one configured `AgentSession`.
- Moved provider, workspace, deterministic registry, and session construction
  out of the CLI in commit `ec461ac`.
- Preserved already resolved profile/system-prompt precedence, ordered context,
  generation configuration, optional structured response format, provider
  behavior, workspace errors, and trace presentation.

### Architecture Decisions

- Keep argument parsing, environment resolution, profile/context loading, and
  interactive setup before the factory.
- Create providers through the existing provider factory and never add
  provider-specific session behavior.
- Build a fresh optional registry per factory call: none by default,
  calculator when enabled, six ordered workspace tools when authorized, and
  calculator followed by those tools when combined.
- Bind all workspace registrations to one canonical `Workspace`; keep traces,
  conversation, lifecycle, completion, commit, and rollback outside the
  construction factory.

### Validation

- A real factory-created localhost `gpt-oss:20b` session returned
  `FACTORY-842`, then `FACTORY-HISTORY-842 FACTORY-842`.
- A calculator-enabled factory session invoked `(29 * 31) + 1` once, observed
  one successful result of 900, and returned `FACTORY-900 result: 900`.
- A workspace factory session invoked the exact `FactoryProbe731` symbol
  search and returned `FACTORY-731 src/factory_probe.py FactoryProbe731`.
- Direct checks confirmed absent unauthorized workspace tools, safe invalid
  workspace failure followed by successful construction, unchanged process
  directory and files, no inspected-code execution, and no external-content
  exposure.
- The complete automated suite passed with 461 tests before documentation.

### Current Limitations

- The factory constructs one session at a time; it does not manage a session
  collection, tasks, assignment, orchestration, persistence, concurrency,
  cancellation, or expanded lifecycle semantics.

## 2026-07-27 — Provider-Independent AgentSession Foundation

### Implemented

- Added immutable `SessionId`, `SessionStatus`, and synchronous `AgentSession`
  in commit `fb99377`.
- Moved conversation ownership, request construction, direct completion,
  tool-loop selection, and transactional commit/rollback out of the CLI in
  commit `7eaf724`.
- Preserved the existing `run_cli()` signature and all CLI presentation,
  configuration, provider construction, workspace registration, errors, and
  trace rendering.

### Architecture Decisions

- Derive provider and model identity from the configured `ChatProvider`.
- Keep already-loaded profiles, resolved system prompts, immutable context,
  generation configuration, response format, and optional `ToolRegistry`
  provider-independent.
- Commit only the user message and final assistant response after a complete
  successful send; never persist internal tool rounds in normal history.
- Roll back the pending turn on direct, tool-loop, maximum-round, or observer
  failure, preserve the original exception, mark the session failed, and allow
  a later successful retry.
- Reject obvious nested sends while completing without introducing locks,
  threads, asynchronous APIs, or cancellation.

### Validation

- A real localhost `gpt-oss:20b` session returned `SESSION-842`, then
  `HISTORY-842 SESSION-842` with exact ordered two-turn history.
- A separate real tool-enabled session invoked calculator exactly once with
  `(37 * 23) + 9`, observed one successful result of 860, and returned
  `TOOL-860 and result: 860`.
- Controlled smoke checks confirmed failure rollback, retry from `failed`,
  re-entrant-send rejection, no partial commit, and recovery to `ready`.
- The complete automated suite passed with 446 tests before documentation.

### Current Limitations

- Sessions are synchronous, process-local, and not serializable or persistent.
- Tasks, assignment, multiple simultaneous sessions, orchestration,
  concurrency, cancellation, extended lifecycle states, async APIs, RAG, MCP,
  worktrees, VS Code, and voice input remain future work.

## 2026-07-27 — Safe Python Symbol Search

### Implemented

- Added provider-independent `search_symbols` in commit `7f9bee7`.
- Registered it automatically for authorized CLI workspaces in commit
  `c8a7548`.
- Preserved deterministic registry order between `search_text` and the Git
  inspection tools.
- Added focused coverage for symbol semantics, containment, bounds,
  non-execution, registry construction, CLI traces, and history isolation.

### Security Decisions

- Parse Python with the standard-library AST only; inspected modules are never
  imported or executed.
- Use lexical scope for classes, functions, methods, nested definitions, and
  qualified names. Async remains `is_async` metadata rather than a distinct
  kind; valid filters are exactly `any`, `class`, `function`, and `method`.
- Include hidden Python paths, skip directory symlinks, and allow explicit
  internal file symlinks only through canonical `Workspace` resolution.
- Skip invalid UTF-8, invalid syntax, and oversized files during directory
  search; reject the same conditions safely for explicit files.
- Limit queries to 256 characters, inspection to 512 Python files and 100 KiB
  per file, results to 256 matches, and qualified names to 512 characters.

### Validation

- Ran a real localhost Ollama smoke against installed `gpt-oss:20b`.
- The model made exact class and method searches for `SymbolProbe842` and
  `SymbolProbe842.inspect_workspace`, observed `is_async=true`, and returned
  `SYMBOL-842` with `src/domain/models.py`.
- Traversal, absolute external paths, an external directory symlink, invalid
  UTF-8, invalid syntax, and an explicit non-Python file all produced safe
  error ToolResults without exposing external content.
- Inspected Python was not executed, the marker remained absent, and temporary
  workspace contents were unchanged.
- The complete automated suite passed with 418 tests before documentation.

### Current Limitations

- Workspace tools remain read-only and synchronous.
- Controlled writes, approved execution, permissions, cancellation,
  destructive-action protection, network tools, MCP, and cross-turn
  tool-round persistence remain future work.
- Filesystem race protection between resolution and later access is not yet
  guaranteed.

## 2026-07-27 — Complete Read-Only Workspace Inspection

### Implemented

- Added bounded literal `search_text` for deterministic recursive inspection.
- Added fixed-command `inspect_git_status` and `inspect_git_diff` tools.
- Extended the workspace registry with search and Git inspection after
  `list_files` and `read_file`.
- Added opt-in `--show-tool-traces` for completed tool-call rounds.

### Security Decisions

- Search includes hidden paths but skips invalid UTF-8 and directory symlinks;
  it is bounded by query, file, byte, match, and line limits.
- Git tools accept no caller-controlled flags, use no shell, disable external
  diff helpers, use a minimal environment, enforce a three-second timeout, and
  return bounded output only.
- Traces use deterministic JSON, redact read content and absolute paths, and
  never enter normal conversation history.

### Validation

- Ran a real localhost Ollama smoke test against installed `gpt-oss:20b`.
- The model invoked `search_text`, `inspect_git_status`, and
  `inspect_git_diff`, then returned `INSPECT-842` with the correct staged and
  unstaged summary.
- Direct `../outside` search and diff probes returned safe error ToolResults
  without exposing outside data; the temporary repository remained unchanged.
- The complete automated suite passed with 396 tests before documentation.

### Current Limitations

- `search_symbols`, writes, arbitrary command execution, network tools, MCP,
  asynchronous execution, and cross-turn tool-round persistence are absent.
- Filesystem race protection between path resolution and later access is not
  yet guaranteed.

## 2026-07-27 — Safe Read-Only Workspace Tools

### Implemented

- Added a canonical `Workspace` root with strict containment checks.
- Added provider-independent `list_files` and `read_file` tools.
- Added explicit CLI authorization through `--workspace PATH`.
- Kept calculator-only enablement under `--enable-tools`; combined registries
  are ordered `calculator`, `list_files`, `read_file`.

### Security Decisions

- Reject absolute paths, traversal, prefix-confusion paths, and symlinks that
  resolve outside the canonical workspace root.
- Permit symlinks that resolve inside the root.
- Limit listings to 128 sorted direct entries, including hidden entries.
- Limit reads to strict UTF-8 files of at most 100 KiB and return canonical
  relative paths.
- Keep tools read-only; no write, deletion, recursive search, globbing,
  network, MCP, or command execution capability was added.

### Validation

- Ran a real localhost Ollama smoke test against installed `gpt-oss:20b`.
- The model invoked `list_files` for `.` and `read_file` for `README.md`.
- The final response contained `WORKSPACE-731`.
- A direct `../outside.txt` registry invocation returned an error ToolResult
  without exposing outside content.
- The complete automated suite passed with 372 tests before documentation.

### Current Limitations

- Tool execution is synchronous and internal tool rounds do not persist across
  separate CLI user turns.
- Filesystem race protection between path resolution and later access is not
  yet guaranteed.

This document records the incremental development of Agent Workbench,
including architecture decisions, implementation milestones, validation,
and known limitations.

## 2026-07-20 — Initial Local Model Integration

### Objective

Establish the minimum application architecture required for a Python service
to communicate with a locally hosted language model.

### Implemented

- Initialized a packaged Python project using uv.
- Added the Ollama Python client as the first model provider dependency.
- Integrated the application with a locally hosted `gpt-oss:20b` model.
- Validated communication through both the Ollama HTTP API and Python client.
- Confirmed GPU-backed local inference.
- Added project documentation, licensing, and an incremental Git history.

### Architecture

```text
Python Application
        ↓
Ollama Python Client
        ↓
Ollama Local API
        ↓
Local Language Model
```

### Validation

The application successfully connected to the local model and returned the
expected response:

```text
Python connected to the local model
```

### Technical Decisions

- Used WSL 2 with Ubuntu 24.04 to align the local development environment
  with common Linux-based deployment targets.
- Selected Python 3.12 and uv to provide reproducible dependency and
  interpreter management.
- Used Ollama as an independent local inference server, keeping the Python
  application decoupled from the model runtime.
- Accessed the model through the Ollama client instead of embedding inference
  logic directly into the application.
- Kept the initial implementation intentionally small to validate connectivity,
  model availability, and GPU-backed inference before introducing additional
  abstractions.

### Current Limitations

- The prompt is currently hard-coded.
- Only one local model provider is supported.
- There is no interactive command-line interface.
- Error handling, configuration, logging, and automated tests are not yet
  implemented.

### Next Milestone

Build an interactive command-line interface and move the Ollama integration
behind a dedicated provider abstraction.

## 2026-07-20 — Interactive CLI and Runtime Configuration

### Objective

Replace the fixed model connectivity check with a testable interactive
application capable of maintaining multi-turn conversations and handling
runtime failures clearly.

### Implemented

- Added an interactive command-line conversation loop.
- Added support for `/exit` and `/quit` session commands.
- Ignored empty input without contacting the model.
- Preserved user and assistant messages across multiple requests.
- Moved the command-line implementation into a dedicated `cli` module.
- Added dependency injection for the completion function.
- Added model selection through the `AGENT_WORKBENCH_MODEL` environment
  variable.
- Added application-specific error translation for Ollama connection and
  response failures.
- Prevented failed requests from being stored in conversation history.
- Added Ruff for formatting and static analysis.
- Added pytest-based automated tests.

### Architecture

```text
Application Entry Point
        ↓
Runtime Configuration
        ↓
Interactive CLI
        ↓
Injected Completion Function
        ↓
Ollama Client
        ↓
Local Model
```

During automated testing, the real completion function is replaced by a
deterministic test implementation:

```text
Interactive CLI
        ↓
Fake Completion Function
        ↓
Deterministic Test Response
```

### Validation

The implementation was validated through:

- A real multi-turn conversation with the local model.
- A conversation-history test using a remembered code word.
- Verification that empty input does not trigger a model request.
- Verification that `/exit` and `/quit` terminate the session.
- Verification that the configured model name is read from the environment.
- Verification that blank configuration falls back to the default model.
- Verification that unavailable models produce a clear application error.
- Verification that the CLI continues running after a completion failure.
- Nine passing automated tests.
- Successful Ruff formatting and static-analysis checks.

### Technical Decisions

- Separated the CLI from the package initializer to keep the package structure
  explicit and maintainable.
- Injected the completion function into the CLI so conversation behavior can be
  tested independently of Ollama, network availability, and GPU resources.
- Used `functools.partial` to bind runtime model configuration while preserving
  the single-argument completion interface expected by the CLI.
- Stored conversation history in the application rather than relying on
  provider-side state.
- Added user messages to the in-memory conversation history only after a
  successful model response.
- Translated provider-specific exceptions into an application-level
  `CompletionError` to prevent implementation details from leaking into the
  user interface.
- Used environment-based configuration to change local models without editing
  source code.

### Current Limitations

- Ollama is still accessed directly from the CLI module.
- Only the Ollama provider is supported.
- Conversation history exists only for the current process.
- Responses are not streamed.
- Logging and structured observability are not implemented.
- Model generation parameters are not externally configurable.
- The test suite does not currently include integration tests against a live
  Ollama server.

### Next Milestone

Extract the Ollama integration behind a provider-independent interface while
preserving the existing CLI behavior and automated tests.

## 2026-07-20 — Provider-Independent Chat Architecture

### Objective

Decouple the command-line interface from the Ollama implementation and define
a reusable contract for future language model providers.

### Implemented

- Extracted the shared message structure into a dedicated module.
- Introduced the `ChatProvider` protocol as the provider-independent interface.
- Created an `OllamaProvider` implementation for local model inference.
- Moved Ollama API calls and provider-specific error translation out of the CLI.
- Replaced completion-function injection with provider-object injection.
- Updated the CLI to display both the active provider and model.
- Added a deterministic `FakeProvider` for isolated CLI testing.
- Added dedicated automated tests for the Ollama provider.
- Preserved existing conversation, configuration, and error-handling behavior.

### Architecture

```text
Application Entry Point
        ↓
Runtime Configuration
        ↓
OllamaProvider
        ↓
ChatProvider Contract
        ↓
Interactive CLI
```

At runtime, the CLI receives an object compatible with the provider contract:

```text
Interactive CLI
        ↓
ChatProvider
        ↓
OllamaProvider
        ↓
Ollama API
```

During automated testing:

```text
Interactive CLI
        ↓
FakeProvider
        ↓
Deterministic Outcomes
```

### Validation

The refactoring was validated through:

- Successful Ruff formatting and static-analysis checks.
- Ten passing automated tests.
- Dedicated tests for successful Ollama responses.
- Dedicated tests for connection and missing-model failures.
- CLI tests using a deterministic provider implementation.
- A successful real completion through the local Ollama server.
- Verification that multi-turn conversation history remained unchanged.

### Technical Decisions

- Used a Python `Protocol` to define behavior without requiring providers to
  inherit from a concrete base class.
- Kept provider-specific dependencies and exceptions outside the CLI layer.
- Assigned model configuration to the provider instance rather than passing it
  with every completion request.
- Used an immutable, slotted dataclass for `OllamaProvider` because its runtime
  configuration should not change after initialization.
- Retained application-level `CompletionError` handling so the CLI remains
  independent of provider-specific exception types.
- Replaced function-level test doubles with a fake provider that follows the
  same interface as production implementations.

### Current Limitations

- Ollama remains the only implemented provider.
- Provider selection is not yet configurable independently of model selection.
- All providers currently use the same internal message representation.
- Responses are not streamed.
- Generation parameters are not exposed through the provider interface.
- Provider capabilities are not yet represented explicitly.
- Integration tests against a live Ollama server are still manual.

### Next Milestone

Add the first cloud-based provider while preserving the provider-independent
CLI and shared message contract.


## 2026-07-21 — OpenAI Provider and Secure Runtime Selection

### Objective

Add the first cloud-based language model provider while preserving the existing
provider-independent CLI, shared message contract, and local Ollama workflow.

### Implemented

- Added the official OpenAI Python SDK.
- Implemented `OpenAIProvider` using the OpenAI Responses API.
- Added runtime selection between Ollama and OpenAI.
- Added a provider factory responsible for constructing configured providers.
- Added explicit provider and model validation.
- Required an explicit model when using the OpenAI provider.
- Added validation for the `OPENAI_API_KEY` environment variable.
- Added application-level translation for OpenAI connection, authentication,
  unavailable-model, rate-limit, and API status errors.
- Added local `.env` loading through `python-dotenv`.
- Preserved existing runtime variables by disabling `.env` overrides.
- Added a public `.env.example` without credentials.
- Updated `.gitignore` to exclude local environment files.
- Added automated tests for OpenAI behavior, provider construction,
  configuration validation, and environment loading.

### Architecture

```text
Runtime Configuration
        ↓
Provider Factory
        ↓
ChatProvider
        ├── OllamaProvider
        │       ↓
        │   Ollama Local API
        │
        └── OpenAIProvider
                ↓
        OpenAI Responses API
```

The provider factory creates only the provider selected at runtime. OpenAI
credentials are therefore required only when the OpenAI provider is selected.

### Configuration

```text
AGENT_WORKBENCH_PROVIDER
        ↓
Select Ollama or OpenAI

AGENT_WORKBENCH_MODEL
        ↓
Select the provider-specific model

OPENAI_API_KEY
        ↓
Authenticate OpenAI requests
```

Configuration is loaded in this order:

```text
Runtime Environment
        ↓
Local .env File
        ↓
Application Defaults
```

Values already present in the runtime environment are not overwritten by the
local `.env` file.

### Validation

The implementation was validated through:

- Successful Ruff formatting and static-analysis checks.
- Twenty-four passing automated tests.
- Simulated OpenAI Responses API success and failure scenarios.
- Verification of authentication, connection, unavailable-model, and
  rate-limit error translation.
- Verification that unsupported providers are rejected.
- Verification that OpenAI requires an explicit model and API key.
- Verification that Ollama remains the default provider.
- Verification that `.env` does not override runtime environment variables.
- Verification that the private `.env` file is ignored by Git.
- A successful real completion through the OpenAI Responses API.
- Confirmation that the existing Ollama workflow remains available.

### Technical Decisions

- Used the OpenAI Responses API rather than introducing a separate
  conversation implementation.
- Injected the OpenAI SDK client into `OpenAIProvider` so provider behavior can
  be tested without network access or paid requests.
- Centralized provider construction in a factory to keep the CLI independent
  of provider-specific initialization.
- Required explicit OpenAI model selection instead of silently choosing a
  cloud model with potentially different availability or cost.
- Kept Ollama as the default provider so the application remains usable
  locally without cloud credentials.
- Stored API credentials only in environment variables and ignored local
  environment files through Git.
- Used `.env.example` to document required configuration without publishing
  secrets.
- Preserved runtime environment precedence to support CI, containers, and
  future cloud deployment.

### Current Limitations

- Only Ollama and OpenAI are implemented.
- The provider is selected only at application startup.
- Responses are not streamed.
- Generation parameters are not yet configurable.
- Usage metadata and token consumption are not exposed by the provider
  abstraction.
- Provider capabilities are not represented explicitly.
- Conversation history exists only in memory for the current process.
- OpenAI integration tests use simulated clients; live API validation remains
  a manual operation.
- Logging and structured observability are not implemented.

### Next Milestone

Add Anthropic as a second cloud provider and evaluate whether the shared
message contract requires provider-specific normalization.

## 2026-07-22 — Anthropic Provider Integration

### Objective

Add Anthropic as a second cloud-based language model provider while preserving
the provider-independent CLI, the local Ollama workflow, and the existing
OpenAI integration.

### Implemented

- Added the official Anthropic Python SDK.
- Implemented `AnthropicProvider` using the Anthropic Messages API.
- Added Anthropic to runtime provider selection.
- Added Anthropic provider construction through the shared provider factory.
- Required explicit Anthropic model configuration.
- Added validation for the `ANTHROPIC_API_KEY` environment variable.
- Added application-level translation for connection, authentication,
  unavailable-model, rate-limit, and generic API status errors.
- Added support for extracting and concatenating text content blocks from
  Anthropic responses.
- Added configurable `max_tokens` to the provider implementation.
- Updated `.env.example` with the Anthropic API key variable.
- Added automated tests for provider behavior, configuration, and factory
  construction.

### Architecture

```text
Runtime Configuration
        ↓
Provider Factory
        ↓
ChatProvider
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

### Validation

The implementation was validated through:

- Successful Ruff formatting and static-analysis checks.
- Thirty-three passing automated tests.
- Simulated Anthropic success and failure scenarios.
- Verification that text response blocks are concatenated correctly.
- Verification that non-text content blocks are ignored.
- Verification of connection, authentication, unavailable-model, and
  rate-limit error translation.
- Verification that Anthropic requires an explicit model and API key.
- Verification that no API request is made when the API key is missing.
- A successful direct request through the Anthropic Python SDK.
- A successful real completion through `AnthropicProvider`.
- Confirmation that the private `.env` file remains excluded from Git.
- Confirmation that the existing Ollama and OpenAI providers remain available.

### Technical Decisions

- Used the Anthropic Messages API as the provider-specific completion
  interface.
- Injected the Anthropic SDK client into `AnthropicProvider` so automated tests
  remain deterministic and do not make paid network requests.
- Kept Anthropic response parsing inside the provider implementation because
  the Messages API returns structured content blocks.
- Ignored non-text blocks until tool calling is represented in the shared
  provider contract.
- Made `max_tokens` a provider property because it is required by the
  Anthropic Messages API.
- Required explicit model selection instead of choosing a cloud model
  automatically.
- Kept Ollama as the default provider so the project remains usable without
  cloud credentials or API costs.

### Current Limitations

- Provider and model selection rely on environment variables rather than
  command-line arguments.
- Responses are not streamed.
- System prompts are not represented separately.
- Usage metadata and token consumption are not exposed.
- Generation parameters are not managed through a shared configuration model.
- Tool-use content blocks are currently ignored.
- Conversation history exists only in memory for the current process.
- Logging and structured observability are not implemented.
- Multiple agents are not yet coordinated by an orchestrator.

### Next Milestone

Add command-line arguments for provider and model selection so users can switch
between Ollama, OpenAI, and Anthropic without editing `.env`.

## 2026-07-22 — Command-Line Runtime Configuration

### Objective

Allow users to select a provider and model for each application execution
without editing the local `.env` file.

### Implemented

- Added command-line parsing through Python's `argparse`.
- Added the optional `--provider` argument.
- Added the optional `--model` argument.
- Restricted provider values to Ollama, OpenAI, and Anthropic.
- Added validation that rejects blank model arguments.
- Added immutable data structures for parsed arguments and resolved runtime
  configuration.
- Added explicit configuration precedence between command-line arguments,
  environment variables, `.env`, and application defaults.
- Required `--model` whenever `--provider` is supplied.
- Updated the CLI entry point to resolve configuration before constructing the
  selected provider.
- Added automated tests for parsing, validation, environment fallback, and CLI
  precedence.

### Configuration Precedence

```text
Command-Line Arguments
        ↓
Runtime Environment Variables
        ↓
Local .env File
        ↓
Application Defaults
```

For example, a local `.env` can keep Anthropic as its configured provider while
a single execution temporarily selects Ollama:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b
```

The `.env` file is not modified by this command.

### Validation

The implementation was validated through:

- Successful Ruff formatting and static-analysis checks.
- Thirty-nine passing automated tests.
- Verification that supported provider names are displayed in `--help`.
- Verification that unsupported providers are rejected by `argparse`.
- Verification that blank model values are rejected.
- Verification that command-line arguments override environment configuration.
- Verification that environment configuration is used when CLI arguments are
  absent.
- Verification that a model can be overridden while retaining the configured
  provider.
- Verification that `--provider` without `--model` produces a clear
  configuration error.
- A successful Ollama execution while Anthropic remained configured in
  `.env`.

### Technical Decisions

- Used the Python standard-library `argparse` module instead of introducing an
  additional CLI dependency.
- Separated argument parsing from runtime configuration resolution so each
  responsibility can be tested independently.
- Represented parsed and resolved configuration using immutable, slotted
  dataclasses.
- Required a model whenever the provider is overridden to prevent
  provider-model mismatches.
- Preserved environment configuration as the fallback so existing `.env`, CI,
  container, and cloud workflows continue to work.
- Kept provider construction inside the existing provider factory.

### Current Limitations

- The CLI supports only provider and model overrides.
- Provider-specific generation parameters are not exposed.
- API keys cannot be supplied through command-line arguments.
- Responses are not streamed.
- Configuration profiles are not implemented.
- Conversation history exists only in memory.
- Multiple agents are not yet coordinated by an orchestrator.
- Logging and structured observability are not implemented.

### Next Milestone

Introduce structured system prompts and conversation roles before adding tool
calling and multi-agent orchestration.

## 2026-07-22 — Provider-Independent Chat Requests and System Prompts

### Objective

Introduce a provider-independent request model and allow users to define the
assistant's role and behavior through a system prompt.

### Implemented

- Added the immutable `ChatRequest` data structure.
- Separated system instructions from conversation messages.
- Updated the `ChatProvider` protocol to accept `ChatRequest`.
- Updated Ollama, OpenAI, and Anthropic providers to translate shared requests
  into their provider-specific formats.
- Added Ollama system instructions through a `system` message.
- Added OpenAI system instructions through the Responses API `instructions`
  parameter.
- Added Anthropic system instructions through the Messages API `system`
  parameter.
- Updated the interactive CLI to construct provider-independent chat requests.
- Added the `--system-prompt` command-line argument.
- Added validation that rejects blank system prompts.
- Forwarded the system prompt with every completion request in the session.
- Kept system instructions outside user and assistant conversation history.
- Updated automated provider and CLI tests for the new request contract.

### Architecture

```text
Command-Line Configuration
        ↓
System Prompt
        ↓
Interactive CLI
        ↓
ChatRequest
├── system_prompt
└── messages
        ↓
ChatProvider
        ├── OllamaProvider
        │       ↓
        │   system message
        │
        ├── OpenAIProvider
        │       ↓
        │   instructions parameter
        │
        └── AnthropicProvider
                ↓
            system parameter
```

### Conversation State

The system prompt is configuration rather than conversation history:

```text
System Prompt
    └── Defines identity and behavior

Conversation History
    ├── User message
    ├── Assistant message
    ├── User message
    └── Assistant message
```

The same system prompt accompanies each request, while only successful user
and assistant messages are preserved in the session history.

### Validation

The implementation was validated through:

- Successful Ruff formatting and static-analysis checks.
- Forty-two passing automated tests.
- Verification that all providers accept the shared `ChatRequest`.
- Verification that Ollama receives a system message before conversation
  messages.
- Verification that OpenAI receives system instructions separately from input
  messages.
- Verification that Anthropic receives the dedicated system parameter.
- Verification that provider errors continue to use `CompletionError`.
- Verification that blank system prompt arguments are rejected.
- Verification that the system prompt is forwarded with every request.
- Verification that system instructions do not enter conversation history.
- A successful real Ollama completion using a software reviewer identity.
- Confirmation that the existing provider and model CLI arguments remain
  functional.

### Technical Decisions

- Represented system instructions separately from the `Message` type because
  they describe agent configuration rather than conversation history.
- Removed the `system` role from the shared conversation message contract.
- Used a provider-independent `ChatRequest` instead of adding provider-specific
  parameters to the CLI.
- Kept provider translation inside each provider implementation.
- Used an immutable, slotted dataclass for `ChatRequest`.
- Avoided storing the system prompt repeatedly in the in-memory message list.
- Added system prompts as runtime configuration before introducing persistent
  agent profiles.

### Current Limitations

- System prompts are supplied as raw command-line text.
- Reusable agent profiles are not yet implemented.
- System prompts cannot yet be loaded from dedicated files.
- Provider-specific generation parameters are not exposed.
- Responses are not streamed.
- Conversation history exists only in memory.
- Tool calling is not implemented.
- Multiple agents are not yet coordinated by an orchestrator.
- Logging and structured observability are not implemented.

### Next Milestone

Introduce reusable agent profiles so named roles such as `Planner`,
`Developer`, `Reviewer`, and `Tester` can define their own system prompts and
later receive provider, model, and tool configuration.

## 2026-07-22 — Reusable Agent Profiles

### Objective

Introduce reusable agent identities that define consistent responsibilities,
descriptions, and system instructions independently of the selected model
provider.

### Implemented

- Added the immutable `AgentProfile` data structure.
- Added built-in `planner`, `developer`, `reviewer`, and `tester` profiles.
- Added a centralized agent profile registry.
- Added normalized profile lookup.
- Added clear validation for unsupported agent names.
- Added the `--agent` command-line argument.
- Restricted the argument to registered agent profiles.
- Resolved agent profiles into system prompts during runtime configuration.
- Prevented simultaneous use of `--agent` and `--system-prompt`.
- Added the active agent name to the CLI session header.
- Added the agent role description to the session startup output.
- Kept agent identity independent from provider and model selection.
- Added automated tests for profile registration, lookup, configuration, and
  CLI presentation.

### Built-In Profiles

```text
planner
    └── Plans tasks, dependencies, risks, assumptions, and acceptance criteria

developer
    └── Designs and implements maintainable, testable, and secure software

reviewer
    └── Reviews correctness, security, maintainability, tests, and edge cases

tester
    └── Designs tests and investigates failures, regressions, and assumptions
```

### Architecture

```text
Command-Line Arguments
        ↓
Agent Profile Registry
        ↓
AgentProfile
├── name
├── description
└── system_prompt
        ↓
Runtime Configuration
        ↓
Interactive CLI
        ↓
ChatRequest
        ↓
Selected Provider
```

Agent profiles do not contain provider-specific behavior. The same profile can
be combined with Ollama, OpenAI, or Anthropic.

### Validation

The implementation was validated through:

- Successful Ruff formatting and static-analysis checks.
- Fifty passing automated tests.
- Verification that all four built-in profiles are registered.
- Verification that every profile contains a name, description, and system
  prompt.
- Verification that profile names are normalized before lookup.
- Verification that unknown profile names are rejected.
- Verification that `--help` lists all supported agent profiles.
- Verification that the selected profile provides the runtime system prompt.
- Verification that `--agent` and `--system-prompt` cannot be combined.
- Verification that the active profile name and description appear in the CLI.
- A successful real Ollama conversation using the `reviewer` profile.
- Confirmation that the reviewer identified correctness, edge-case, and
  testing concerns in a Python division expression.

### Technical Decisions

- Stored built-in profiles in a centralized registry so discovery, validation,
  and lookup use the same source of truth.
- Used immutable, slotted dataclasses for agent profile definitions.
- Kept provider and model configuration outside `AgentProfile` in this first
  version so roles remain reusable across providers.
- Reused the existing system prompt pipeline instead of adding agent-specific
  logic to providers.
- Rejected simultaneous custom prompts and profiles to avoid unclear
  instruction precedence.
- Displayed the agent identity at session startup without changing the generic
  session termination message.
- Started with software engineering roles that can later participate in a
  coordinated multi-agent workflow.

### Current Limitations

- Only built-in profiles defined in Python are available.
- Profiles cannot yet be loaded from YAML, TOML, or JSON files.
- Profiles do not define provider, model, tools, or generation parameters.
- Only one agent profile can be active in each CLI process.
- Separate CLI processes are not coordinated.
- Agents do not share state, tasks, or conversation history.
- Tool calling is not implemented.
- Responses are not streamed.
- Logging and structured observability are not implemented.

### Next Milestone

Move agent definitions into external configuration files so users can create
and modify profiles without editing Python source code.

## 2026-07-23 — External Agent Profile Configuration

### Objective

Separate agent definitions from Python source code and allow users to load
custom agent identities and instructions from external TOML files.

### Implemented

- Moved the built-in `planner`, `developer`, `reviewer`, and `tester` profiles
  into packaged TOML resources.
- Added a TOML parser for agent profile definitions.
- Added validation for malformed TOML content.
- Added validation for missing required fields.
- Added validation for blank field values.
- Added validation for unsupported fields.
- Added loading of built-in resources through `importlib.resources`.
- Verified that packaged profiles are included in the generated wheel.
- Added loading of external UTF-8 TOML profile files.
- Added validation for missing files, directories, and unsupported file
  extensions.
- Added the `--agent-file` command-line argument.
- Resolved custom profile system instructions into runtime configuration.
- Prevented ambiguous combinations between built-in profiles, external
  profiles, and direct system prompts.
- Preserved compatibility with Ollama, OpenAI, and Anthropic providers.
- Added automated tests for profile parsing, filesystem loading, CLI parsing,
  and runtime configuration.

### Built-In Profile Resources

```text
src/agent_workbench/profiles/
├── __init__.py
├── developer.toml
├── planner.toml
├── reviewer.toml
└── tester.toml
```

The profile filename provides the built-in CLI identifier:

```text
reviewer.toml
      ↓
--agent reviewer
```

The file content provides the agent identity and behavior:

```toml
name = "Reviewer"
description = "Reviews software quality and risks."
system_prompt = "You are a strict software review agent."
```

### Custom Profile Flow

```text
--agent-file ./custom-agent.toml
                 ↓
        Validate file path
                 ↓
          Read UTF-8 TOML
                 ↓
       Parse and validate fields
                 ↓
            AgentProfile
                 ↓
       Runtime system prompt
                 ↓
         Selected provider
```

### Required Fields

Every profile must contain exactly:

```text
name
description
system_prompt
```

All fields must contain non-empty strings.

Unknown fields are rejected so spelling mistakes and unsupported configuration
do not pass silently.

### Configuration Precedence

The application accepts one agent instruction source per session:

```text
Built-in profile
    OR
External profile file
    OR
Direct system prompt
```

The following combinations are rejected:

```text
--agent + --agent-file
--agent + --system-prompt
--agent-file + --system-prompt
```

### Validation

The implementation was validated through:

- Successful Ruff formatting and static-analysis checks.
- Sixty-four passing automated tests.
- Verification that all four packaged TOML profiles are discovered.
- Verification that valid TOML content creates an `AgentProfile`.
- Verification that malformed TOML is rejected.
- Verification that missing, blank, and unsupported fields are rejected.
- Verification that missing files are rejected.
- Verification that directories cannot be used as profile files.
- Verification that custom profiles require the `.toml` extension.
- Verification that `--agent-file` appears in CLI help.
- Verification that custom profiles provide the runtime system prompt.
- Verification that conflicting profile arguments are rejected.
- Verification that the built-in TOML files are included in the generated
  wheel.
- A successful real Ollama session using an external `Security Reviewer`
  profile.
- Confirmation that the external system prompt changed the model behavior.

### Technical Decisions

- Used `tomllib` from the Python 3.12 standard library, avoiding another
  runtime dependency.
- Used `importlib.resources` instead of filesystem-relative paths for packaged
  resources.
- Kept the immutable `AgentProfile` data structure as the provider-independent
  representation.
- Used strict field validation to detect configuration mistakes early.
- Kept provider and model configuration outside the profile format.
- Added `--agent-file` rather than automatically scanning directories in this
  milestone, keeping profile selection explicit and predictable.
- Preserved the same system prompt pipeline for built-in profiles, custom
  profiles, and direct instructions.
- Prepared the profile loader for a future interactive configuration mode.

### Current Limitations

- Custom profiles must be selected through an explicit file path.
- The application does not yet discover profiles from a default user
  directory.
- Profiles cannot yet configure provider, model, temperature, tools, or
  context sources.
- File-based project context is not implemented.
- Only one profile can be active in each CLI session.
- The application does not yet provide an interactive selection wizard.
- Agents are not coordinated through an orchestrator.
- Conversation state is not persisted between sessions.

### Next Milestone

Add file-based context so users can provide source code, documentation, and
other text files to the active agent.

After context support is stable, add an interactive setup flow for selecting
the provider, model, agent profile, and context sources without requiring the
user to memorize command-line arguments.

## 2026-07-24 — File-Based Conversation Context

### Objective

Allow users to supply source code, documentation, configuration, and other
text files as provider-independent reference material for an interactive
conversation.

### Implemented

- Added the immutable `ContextDocument` data structure.
- Added local context file loading through the repeatable `--context-file`
  command-line argument.
- Preserved the order in which multiple context files are supplied.
- Added support for `.txt`, `.md`, `.py`, `.toml`, `.json`, `.yaml`, and `.yml`
  files.
- Added validation for missing files and directory paths.
- Added validation for unsupported file extensions.
- Added UTF-8 decoding validation.
- Rejected empty and whitespace-only context files.
- Added a 100 KiB individual file-size limit.
- Added context documents to `RuntimeConfiguration`.
- Added provider-independent context documents to `ChatRequest`.
- Kept context documents separate from user and assistant conversation history.
- Added shared formatting for context documents and their source paths.
- Combined system prompts and context documents through a common instruction
  builder.
- Added provider-specific context translation for Ollama, OpenAI, and
  Anthropic.
- Added automated tests for file loading, validation, formatting, runtime
  configuration, CLI forwarding, request behavior, and provider translation.

### Architecture

```text
--context-file
      ↓
CLIArguments.context_files
      ↓
Context Document Loader
      ↓
ContextDocument
├── source
└── content
      ↓
RuntimeConfiguration.context_documents
      ↓
Interactive CLI
      ↓
ChatRequest
├── system_prompt
├── context_documents
└── messages
      ↓
Provider Adapter
      ├── OllamaProvider
      │       ↓
      │   system message
      │
      ├── OpenAIProvider
      │       ↓
      │   instructions parameter
      │
      └── AnthropicProvider
              ↓
          system parameter
```

### Context Instruction Structure

The active system prompt remains separate from the loaded documents until the
provider request is constructed.

When context documents are present, the shared instruction builder produces a
structure similar to:

```text
Active system prompt

Reference-data instruction

<context_document source="README.md">
Document contents
</context_document>

<context_document source="pyproject.toml">
Document contents
</context_document>
```

Each document is identified by its source path. Characters that could break
the source attribute are escaped, while the original document contents are
preserved.

Context documents are labelled as reference data and are not added to the
`user` and `assistant` conversation history.

### Validation

The implementation was validated through:

- Successful Ruff formatting and static-analysis checks.
- Ninety-five passing automated tests.
- Verification of every supported context file extension.
- Verification that uppercase supported extensions are accepted.
- Verification that original whitespace and line breaks are preserved.
- Verification that missing files and directories are rejected.
- Verification that unsupported file extensions are rejected.
- Verification that invalid UTF-8 content is rejected.
- Verification that blank and whitespace-only context files are rejected.
- Verification that files larger than 100 KiB are rejected.
- Verification that a file exactly at the 100 KiB limit is accepted.
- Verification that repeated `--context-file` arguments preserve order.
- Verification that context documents remain outside conversation history.
- Verification that Ollama receives context through a system message.
- Verification that OpenAI receives context through the `instructions`
  parameter.
- Verification that Anthropic receives context through the `system` parameter.
- Verification that automated provider tests use simulated clients and make no
  paid API requests.
- A successful real Ollama conversation using one context file and a built-in
  reviewer profile.
- A successful real Ollama conversation using two ordered context files.
- Verification that the model recovered distinct values from both supplied
  documents.
- Verification that missing and unsupported files produce clear CLI
  configuration errors.
- Confirmation that no temporary validation files remained in the repository.

### Technical Decisions

- Used `pathlib.Path` for filesystem-independent path handling.
- Used immutable, slotted dataclasses for context documents and shared
  requests.
- Used tuples to preserve document order and prevent accidental collection
  mutation.
- Used Python standard-library functionality for file loading and context
  formatting.
- Applied the individual file-size limit before reading the complete file into
  memory.
- Preserved original document content and used whitespace normalization only
  to detect empty files.
- Centralized context formatting so all providers receive the same logical
  representation.
- Kept provider-specific translation inside each provider adapter.
- Reused provider-native system instruction channels instead of adding context
  to user messages.
- Kept context documents separate from conversation history because they are
  runtime reference material rather than conversational turns.
- Sent complete documents in this milestone to establish a simple and testable
  baseline before introducing retrieval.
- Avoided embeddings and vector database dependencies until the RAG milestone.

### Current Limitations

- Every selected file is sent in full with every model request.
- The 100 KiB limit applies independently rather than through a total context
  budget.
- Token usage is not estimated before provider requests.
- Files are selected only through explicit command-line paths.
- Directories cannot be scanned recursively.
- Binary documents, PDFs, and office formats are not supported.
- Context documents are loaded only when the application starts.
- Context cannot be added or removed during an active session.
- Embeddings, chunking, semantic retrieval, and vector storage are not
  implemented.
- Provider context-window limits are not represented through the shared
  configuration model.
- Responses are not streamed.
- Conversation history remains in memory only.
- Logging and structured observability are not implemented.

### Next Milestone

Introduce a provider-independent generation configuration model for parameters
such as temperature, top-p, maximum output tokens, stop sequences, and
provider-supported options.

After generation configuration is stable, add an interactive setup wizard so
users can select providers, models, agents, context files, and inference
presets without memorizing command-line arguments.


## 2026-07-24 — Provider-Independent Generation Configuration

### Objective

Allow users to control common text-generation parameters without exposing the
interactive CLI and conversation layer to provider-specific API names.

### Implemented

* Added the immutable `GenerationConfig` data structure.
* Added optional provider-independent `temperature`, `top_p`, and
  `max_output_tokens` fields.
* Added validation for portable sampling values between `0.0` and `1.0`.
* Added validation requiring maximum output tokens to be a positive integer.
* Explicitly rejected boolean values from numeric generation fields.
* Added `GenerationConfig` to `ChatRequest`.
* Added generation configuration to `RuntimeConfiguration`.
* Forwarded generation settings separately from conversation history.
* Added the `--temperature` command-line argument.
* Added the `--top-p` command-line argument.
* Added the `--max-output-tokens` command-line argument.
* Added CLI parsing and early validation for generation values.
* Preserved provider defaults when optional parameters are not supplied.
* Added generation translation for Ollama.
* Added generation translation for the OpenAI Responses API.
* Added generation translation for the Anthropic Messages API.
* Added typed provider request argument structures.
* Added automated tests for validation, CLI parsing, runtime resolution,
  request forwarding, and provider translation.

### Architecture

```text
--temperature
--top-p
--max-output-tokens
        ↓
CLIArguments
        ↓
GenerationConfig
├── temperature
├── top_p
└── max_output_tokens
        ↓
RuntimeConfiguration.generation_config
        ↓
Interactive CLI
        ↓
ChatRequest.generation_config
        ↓
Provider Adapter
```

Generation configuration remains separate from:

```text
ChatRequest
├── system_prompt
├── context_documents
├── generation_config
└── messages
```

Generation parameters are runtime request configuration rather than
conversation messages or model instructions.

### Provider Translation

```text
GenerationConfig
├── OllamaProvider
│   ├── temperature → options["temperature"]
│   ├── top_p → options["top_p"]
│   └── max_output_tokens → options["num_predict"]
│
├── OpenAIProvider
│   ├── temperature → temperature
│   ├── top_p → top_p
│   └── max_output_tokens → max_output_tokens
│
└── AnthropicProvider
    ├── temperature → temperature
    ├── top_p → top_p
    └── max_output_tokens → max_tokens
```

Ollama and OpenAI generation parameters are omitted completely when they are
not configured, preserving provider and model defaults.

Anthropic requires `max_tokens` in every Messages API request. When the shared
maximum output value is absent, the provider continues to use its existing
default of `1024`.

### Validation

The implementation was validated through:

* Successful Ruff formatting and static-analysis checks.
* One hundred and thirty-four passing automated tests.
* Verification that all generation parameters remain optional.
* Verification that default `GenerationConfig` values are `None`.
* Verification that temperature accepts boundary values `0.0` and `1.0`.
* Verification that top-p accepts boundary values `0.0` and `1.0`.
* Verification that values outside the portable interval are rejected.
* Verification that non-numeric sampling values are rejected by the CLI.
* Verification that maximum output tokens must be a positive integer.
* Verification that floating-point, zero, negative, string, and boolean token
  limits are rejected.
* Verification that CLI arguments are preserved in runtime configuration.
* Verification that generation settings are forwarded separately from
  conversation history.
* Verification that omitted generation parameters do not alter Ollama or
  OpenAI provider calls.
* Verification that Ollama receives generation parameters through `options`.
* Verification that OpenAI receives native Responses API arguments.
* Verification that Anthropic receives native Messages API arguments.
* Verification that Anthropic preserves its provider output-token default when
  no shared limit is supplied.
* Verification that provider tests use simulated clients and make no paid API
  calls.
* A successful real Ollama session using `temperature=0.0`, `top_p=1.0`, and
  `max_output_tokens=256`.
* Confirmation that `gpt-oss:20b` returned the exact expected text
  `GENERATION-CONFIG-OK`.
* Observation that a `32`-token output budget produced empty final content for
  the same reasoning model.
* Confirmation that increasing the output budget to `256` allowed the model to
  produce its final answer.

### Technical Decisions

* Used a dedicated provider-independent configuration object instead of
  adding provider-specific parameters directly to `ChatRequest`.
* Used an immutable, slotted dataclass to prevent configuration mutation during
  a session.
* Kept every shared generation field optional so existing provider behavior
  remains unchanged.
* Used a portable `0.0` to `1.0` interval for temperature and top-p.
* Validated values both at the CLI boundary and inside `GenerationConfig`.
* Explicitly rejected booleans because Python treats `bool` as a subclass of
  `int`.
* Used `max_output_tokens` as the shared semantic name even though providers
  use different native names.
* Kept provider-specific translation inside each provider adapter.
* Used typed dictionaries and `Unpack` for dynamically constructed OpenAI and
  Anthropic keyword arguments.
* Omitted optional keyword arguments instead of sending explicit `None`
  values.
* Preserved the existing Anthropic provider fallback because its API requires
  a maximum output-token value.
* Limited the first implementation to common provider-independent parameters.
* Deferred stop sequences and provider-specific generation controls until a
  later milestone.

### Current Limitations

* Only temperature, top-p, and maximum output tokens are represented.
* Stop sequences are not supported by the shared configuration.
* Reasoning effort and thinking-budget controls are not represented.
* Seeds and deterministic generation controls are not represented.
* Provider-specific parameters cannot be supplied through the shared CLI.
* Model-specific parameter compatibility is not detected before the provider
  request.
* The application does not estimate whether the requested output limit is
  appropriate for the selected model.
* Very small output-token limits may prevent reasoning models from producing
  final response content.
* Generation presets cannot yet be stored in agent profile files.
* Generation settings cannot be changed during an active conversation.
* Responses are not streamed.
* Token usage and generation metadata are not displayed.
* Conversation state remains in memory only.
* Logging and structured observability are not implemented.

### Next Milestone

Add an interactive runtime setup flow so users can select a provider, model,
agent profile, context files, and generation settings without memorizing
command-line arguments.

After the interactive setup flow is stable, continue toward structured model
outputs, tool calling, Retrieval-Augmented Generation, agent orchestration,
evaluation, observability, and deployment.


## 2026-07-24 — Interactive Runtime Setup

### Objective

Provide a guided runtime configuration flow so users can prepare a complete
Agent Workbench session without memorizing provider, model, agent, context, and
generation command-line arguments.

### Implemented

* Added the `--setup` command-line argument.
* Added explicit validation preventing `--setup` from being combined with
  direct configuration arguments.
* Added the interactive runtime setup module.
* Added guided provider selection.
* Allowed providers to be selected by name or menu number.
* Displayed the currently configured provider as the default.
* Added safe model-default resolution.
* Prevented models configured for one provider from being suggested for a
  different provider.
* Required a non-empty model when no safe default exists.
* Added optional built-in agent profile selection.
* Allowed agent profiles to be selected by name or menu number.
* Added an explicit `none` agent option.
* Added interactive loading of zero or more context files.
* Validated context files immediately through the existing context loader.
* Preserved the order of multiple context documents.
* Added interactive temperature configuration.
* Added interactive top-p configuration.
* Added interactive maximum-output-token configuration.
* Preserved provider defaults when generation fields are skipped.
* Repeated individual questions after invalid input.
* Converted the collected values into the existing `RuntimeConfiguration`.
* Reused the existing provider factory and conversation execution flow.
* Preserved the existing non-interactive CLI behavior.
* Added automated tests for parsing, conflict validation, setup integration,
  provider selection, model selection, agents, context files, and generation
  settings.

### Architecture

```text
uv run agent-workbench --setup
              ↓
Interactive Runtime Setup
              ↓
Provider Selection
              ↓
Model Selection
              ↓
Agent Profile Selection
              ↓
Context File Selection
              ↓
Generation Configuration
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

The setup produces the same `RuntimeConfiguration` used by the direct
command-line workflow.

It does not create a separate provider-construction path or conversation
implementation.

### Provider and Model Defaults

The configured provider is displayed as the setup default.

The model is offered as a default only when it is safe for the selected
provider.

```text
Configured provider and model
        ↓
Selected provider matches configured provider
        ↓
Configured model can be offered
```

When the user switches providers, the application does not automatically reuse
the previously configured model.

Ollama can fall back to the application default `gpt-oss:20b`. Cloud providers
require the user to enter a model when no matching configured model is
available.

### Agent Selection

The setup lists the four packaged profiles:

```text
0. none
1. developer
2. planner
3. reviewer
4. tester
```

The selected built-in profile is resolved through the existing agent profile
registry.

The resulting profile supplies the system prompt, agent identity, and role
description used by the conversation.

### Context Selection

Users can enter context paths one at a time and press Enter when finished.

Each path is loaded immediately through `load_context_document()`.

Invalid paths, unsupported extensions, directories, oversized files, invalid
UTF-8 contents, and blank files are reported without terminating the setup.

Validated context documents are stored in their original input order.

Because `RuntimeConfiguration` is immutable, `dataclasses.replace()` is used
to attach the already validated context documents to the resolved
configuration.

### Generation Settings

The setup collects:

```text
temperature
top_p
max_output_tokens
```

Temperature and top-p accept optional values between `0.0` and `1.0`.

Maximum output tokens accepts an optional positive integer.

Leaving a value blank stores `None`, allowing the selected provider or model to
retain its default behavior.

The setup creates the existing provider-independent `GenerationConfig`; native
parameter translation remains inside each provider adapter.

### Argument Compatibility

The following combinations are rejected:

```text
--setup + --provider
--setup + --model
--setup + --system-prompt
--setup + --agent
--setup + --agent-file
--setup + --context-file
--setup + --temperature
--setup + --top-p
--setup + --max-output-tokens
```

This keeps interactive and direct configuration as two unambiguous entry
points into the same runtime architecture.

### Validation

The implementation was validated through:

* Successful Ruff formatting and static-analysis checks.
* One hundred and forty-nine passing automated tests.
* Verification that `--setup` appears in CLI help.
* Verification that `--setup` is parsed independently.
* Verification that setup conflicts with direct configuration arguments are
  rejected.
* Verification that the existing non-setup configuration path is preserved.
* Verification that `main()` uses the interactive configuration before
  constructing the provider.
* Verification that environment provider and model defaults can be accepted by
  pressing Enter.
* Verification that providers can be selected by name.
* Verification that providers can be selected by menu number.
* Verification that invalid provider selections are asked again.
* Verification that a model from one provider is not reused for another
  provider.
* Verification that blank models are rejected when no safe default exists.
* Verification that the setup can start without an agent.
* Verification that built-in agents can be selected by menu number.
* Verification that built-in agents can be selected by name.
* Verification that invalid agent selections are asked again.
* Verification that multiple context files preserve their input order.
* Verification that invalid context files do not terminate the setup.
* Verification that optional generation values can all remain unset.
* Verification that valid generation parameters are collected.
* Verification that invalid temperature values are asked again.
* Verification that invalid top-p values are asked again.
* Verification that invalid output-token limits are asked again.
* A successful real interactive setup using Ollama and `gpt-oss:20b`.
* A successful real selection of the built-in Reviewer profile.
* A successful real context-file load through the setup.
* A successful real setup using `temperature=0.0`, `top_p=1.0`, and
  `max_output_tokens=256`.
* Confirmation that the model recovered the exact validation code
  `SETUP-149-OK` from the supplied context.
* Confirmation that the model identified the project owner as `Rafael Silva`.
* Confirmation that the temporary validation file was removed after the test.

### Technical Decisions

* Made the setup explicit through `--setup` rather than changing the default
  startup behavior.
* Kept argument parsing separate from interactive terminal input.
* Kept interactive setup logic in a dedicated module.
* Used injectable input and output functions to make the setup deterministic
  in automated tests.
* Reused `RuntimeConfiguration` as the output of both direct and interactive
  configuration.
* Reused `resolve_runtime_configuration()` for agent and generation
  configuration.
* Reused `load_context_document()` for context validation.
* Reused `create_provider()` after setup completion.
* Preserved the provider-independent `ChatRequest` and conversation pipeline.
* Ordered providers and built-in agent names alphabetically for predictable
  menu numbering.
* Allowed both names and menu numbers to make selection convenient.
* Repeated only the invalid question rather than restarting the complete
  setup.
* Preserved optional provider defaults through blank generation inputs.
* Avoided saving setup values to disk in this milestone.
* Avoided adding a new third-party dependency for terminal menus.

### Current Limitations

* The setup is a plain text terminal flow.
* Arrow-key navigation and graphical terminal controls are not supported.
* Setup values are not persisted between sessions.
* The setup does not create or update `.env`.
* Custom agent profile files cannot be selected through the setup.
* Direct system prompts cannot be entered through the setup.
* Context directories cannot be scanned.
* Context files cannot be removed after being added during setup.
* Duplicate context paths are not detected.
* The setup cannot be cancelled through `/exit` or `/quit`.
* Provider API credentials are not collected by the setup.
* Provider and model availability are not verified before the conversation
  begins.
* Model-specific generation compatibility is not detected.
* Generation presets are not available.
* Setup values cannot be changed during an active conversation.
* Responses are not streamed.
* Conversation history remains in memory only.
* Logging and structured observability are not implemented.

### Next Milestone

Introduce provider-independent structured outputs so callers can request
machine-readable model responses through a shared schema or response-format
abstraction.

After structured outputs are stable, continue toward tool calling,
Retrieval-Augmented Generation, agent orchestration, evaluation,
observability, and deployment.

## 2026-07-24 — Provider-Independent Structured Outputs

### Objective

Allow callers to request machine-readable JSON responses through a shared
response-format abstraction without exposing the CLI, conversation layer, or
shared request model to provider-specific structured output arguments.

### Implemented

* Added the provider-independent `JSONResponseFormat` abstraction.
* Added the recursive `JSONValue` type alias.
* Added the shared `JSONSchema` type alias.
* Added portable response format name validation.
* Added validation requiring a non-empty schema object.
* Added validation requiring `object` as the top-level schema type.
* Added strict JSON-compatible value validation.
* Added rejection of non-string JSON object keys.
* Added rejection of non-finite floating-point values.
* Added canonical internal JSON storage.
* Added defensive schema copies through the `schema` property.
* Added equality independent of original dictionary key order.
* Added `response_format` to `ChatRequest`.
* Added `response_format_file` to `CLIArguments`.
* Added `response_format` to `RuntimeConfiguration`.
* Added the `--response-format-file` command-line argument.
* Added response format forwarding through `run_cli()`.
* Added structured output selection to the interactive setup.
* Added immediate interactive response format file validation.
* Added repeated prompts after invalid response format files.
* Added structured output translation for Ollama.
* Added structured output translation for the OpenAI Responses API.
* Added structured output translation for the Anthropic Messages API.
* Preserved existing unstructured response behavior when no format is
  supplied.
* Added automated tests for the shared abstraction, file loader, CLI parsing,
  runtime resolution, conversation forwarding, interactive setup, and all
  provider translations.

### Shared Architecture

```text
Response Format JSON File
        ↓
load_response_format_file()
        ↓
JSONResponseFormat
├── name
└── schema
        ↓
RuntimeConfiguration.response_format
        ↓
Interactive CLI
        ↓
ChatRequest.response_format
        ↓
Provider Adapter
```

Structured output configuration remains separate from:

```text
ChatRequest
├── messages
├── system_prompt
├── context_documents
├── generation_config
└── response_format
```

The response format is request configuration rather than a conversation
message or model instruction.

### Response Format Definition

A response format file contains:

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

Only `name` and `schema` are supported at the top level.

The format name is portable across the shared application model, even though
not every provider requires it in its native API request.

### Immutability

A frozen dataclass prevents field reassignment but does not make nested
dictionaries immutable.

The response format therefore stores the validated schema as canonical JSON:

```text
Mutable Input Dictionary
        ↓
Validation
        ↓
json.dumps(sort_keys=True, allow_nan=False)
        ↓
Immutable Internal String
        ↓
json.loads() when schema is requested
```

This prevents mutation of the original input dictionary and mutation of a
returned schema copy from changing the active response format.

Canonical key ordering also ensures that logically equivalent schemas compare
equally.

### File Loading and Validation

Response format files are validated for:

* File existence.
* Regular-file type.
* `.json` extension.
* Maximum size of 100 KiB.
* UTF-8 encoding.
* Non-empty content.
* Valid JSON syntax.
* JSON object root.
* Required `name` field.
* Required `schema` field.
* Unsupported top-level fields.
* String response format name.
* JSON object schema.
* Portable response format name syntax.
* Non-empty schema.
* Top-level schema type.
* JSON-compatible nested values.
* String object keys.
* Finite JSON numbers.

Malformed JSON errors include the source line and column.

### Provider Translation

```text
JSONResponseFormat
├── OllamaProvider
│   └── format = schema
│
├── OpenAIProvider
│   └── text
│       └── format
│           ├── type = "json_schema"
│           ├── name
│           ├── schema
│           └── strict = True
│
└── AnthropicProvider
    └── output_config
        └── format
            ├── type = "json_schema"
            └── schema
```

The OpenAI Responses API requires the portable response format name.

Ollama and Anthropic receive the shared schema but do not receive the name.

Provider-specific field names remain isolated inside each adapter.

### CLI Integration

The direct command-line workflow supports:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --response-format-file ./schemas/software-review.json
```

The argument parser rejects blank response format file paths.

The runtime resolver loads and validates the file before provider creation.

`--response-format-file` cannot be combined directly with `--setup`, because
the setup contains its own optional response format question.

### Interactive Setup Integration

The interactive setup now collects:

```text
Provider
Model
Built-In Agent Profile
Context Files
Generation Settings
Structured Output
```

The final optional question is:

```text
Structured output:
Press Enter to use the normal unstructured text response.
Response format file [none]:
```

Pressing Enter keeps `response_format=None`.

A valid file is loaded and displayed by name.

An invalid file produces a clear error and repeats only that question.

### Validation

The implementation was validated through:

* Successful Ruff formatting checks.
* Successful Ruff static-analysis checks.
* One hundred and ninety-two passing automated tests.
* Verification of response format name normalization.
* Verification of response format name length and character restrictions.
* Verification that schemas require a non-empty object root.
* Verification that non-string object keys are rejected.
* Verification that unsupported Python values are rejected.
* Verification that `NaN` and infinite values are rejected.
* Verification that schema mutation after construction does not affect the
  response format.
* Verification that mutation of a returned schema copy does not affect the
  response format.
* Verification that equivalent schemas compare equally regardless of key
  order.
* Verification of missing-file rejection.
* Verification of directory-path rejection.
* Verification of unsupported-extension rejection.
* Verification of the 100 KiB file-size limit.
* Verification of invalid UTF-8 rejection.
* Verification of empty-file rejection.
* Verification of malformed JSON rejection.
* Verification of non-object JSON root rejection.
* Verification of missing required fields.
* Verification of unsupported top-level fields.
* Verification of invalid `name` and `schema` field types.
* Verification that `ChatRequest` defaults to no response format.
* Verification that `RuntimeConfiguration` loads the selected response format.
* Verification that response formats remain separate from conversation
  history.
* Verification that setup conflicts with a directly supplied
  `--response-format-file`.
* Verification that interactive setup can skip structured output.
* Verification that interactive setup loads a valid response format.
* Verification that interactive setup repeats the question after an invalid
  response format.
* Verification that Ollama receives the schema through `format`.
* Verification that OpenAI receives `text.format` with strict JSON Schema
  configuration.
* Verification that Anthropic receives `output_config.format`.
* Verification that providers omit structured output arguments when no
  response format is supplied.
* Verification that provider responses remain strings.
* Confirmation that the CLI help displays `--response-format-file`.
* A successful real Ollama session using direct command-line configuration.
* A successful real Ollama session using interactive setup.
* A successful direct response:
  `{"risk_level":"low","summary":"Structured output works."}`.
* A successful setup response:
  `{"risk_level":"medium","summary":"Setup structured output works."}`.
* Confirmation that both real responses respected the required properties and
  configured enum.
* Confirmation that the temporary response format file was removed after
  validation.

### Technical Decisions

* Used a dedicated provider-independent response format abstraction.
* Used `JSONResponseFormat` rather than placing raw schemas directly on
  `ChatRequest`.
* Used `name` as a portable shared field because OpenAI requires a schema name.
* Kept strict structured output behavior implicit rather than exposing a
  provider-dependent `strict` option.
* Always enabled strict mode for the OpenAI translation.
* Used native structured output APIs for all three providers.
* Kept provider translation inside each provider adapter.
* Continued returning provider output as a string.
* Required file-based schema input rather than inline command-line JSON.
* Avoided shell escaping and quoting problems for large schemas.
* Limited response format files to 100 KiB.
* Required exactly `name` and `schema` at the file root.
* Stored schemas internally as canonical JSON to provide defensive
  immutability.
* Used `allow_nan=False` to enforce strict JSON numeric values.
* Reused the same loader for direct CLI and interactive setup workflows.
* Preserved the existing application behavior when structured output is not
  selected.
* Added no new third-party dependencies.

### Current Limitations

* Only top-level JSON object schemas are accepted.
* Complete JSON Schema specification validation is not implemented.
* Unsupported JSON Schema keywords are not detected locally.
* Provider-specific JSON Schema subsets are not represented.
* Model-specific structured output compatibility is not checked.
* Provider or model support is discovered only when the API request is made.
* Responses remain JSON strings.
* Responses are not deserialized into Python objects.
* Responses are not validated locally against the configured schema after
  generation.
* Invalid provider output is not repaired or retried automatically.
* Pydantic models cannot be supplied directly.
* Python dataclasses cannot be converted automatically into schemas.
* Inline JSON Schema command-line input is not supported.
* Only one response format can be active per session.
* Response formats cannot be changed during an active conversation.
* Response format files are not copied or persisted by the application.
* The interactive setup does not provide a schema editor.
* Responses are not streamed.
* Conversation history remains in memory only.
* Logging and structured observability are not implemented.

### Next Milestone

Introduce provider-independent tool calling so models can request structured
application actions through a shared tool definition, invocation, and result
abstraction.

After tool calling is stable, continue toward Retrieval-Augmented Generation,
agent orchestration, evaluation, observability, and deployment.

## 2026-07-25 — Documentation Restructure

### Objective

Reduce the size and repetition of the main README while creating focused
documentation for the current architecture, implemented capabilities, product
vision, project configuration, and future roadmap.

### Implemented

- Reduced `README.md` from approximately 1,333 lines to 450 lines.
- Converted the README into a concise project entry point.
- Added `docs/getting-started.md`.
- Added `docs/architecture.md`.
- Added `docs/runtime-configuration.md`.
- Added `docs/agent-profiles.md`.
- Added `docs/context-files.md`.
- Added `docs/structured-outputs.md`.
- Added `docs/product-vision.md`.
- Added `docs/project-configuration.md`.
- Added `docs/roadmap.md`.
- Documented the current provider-independent CLI architecture.
- Documented the long-term multi-agent VS Code workspace vision.
- Documented future project-local configuration through `.agent-workbench/`.
- Documented the intended relationship between native tools and MCP.
- Documented future local project retrieval and RAG.
- Documented future isolated execution through Git branches and worktrees.
- Documented future voice prompt input through editable speech-to-text
  transcripts.
- Clearly separated implemented capabilities from planned functionality.
- Defined provider-independent tool calling as the next implementation
  milestone.

### Validation

The documentation restructure was validated through:

- One hundred and ninety-two passing automated tests.
- Successful Ruff static-analysis checks.
- Successful Ruff formatting checks.
- Successful Git whitespace validation.
- Verification that every Markdown code fence is balanced.
- Verification that all relative Markdown documentation links resolve.
- Verification that documented CLI arguments match the current CLI help.
- Verification that all nine expected documents exist inside `docs/`.

### Technical Decisions

- Kept `README.md` focused on project introduction, quick start, essential
  examples, documentation links, security, and current limitations.
- Moved detailed feature documentation into dedicated files.
- Kept product vision separate from current implementation documentation.
- Kept future project configuration explicitly marked as proposed.
- Preserved provider independence across agents, context, generation,
  structured outputs, future tools, and MCP.
- Defined MCP as an extension of the shared tool and permission layers rather
  than a replacement for them.
- Kept voice input as a future interface capability that does not block the
  tool-calling or orchestration milestones.
- Kept `DEVELOPMENT_LOG.md` as the chronological engineering record.
- Future development-log entries should remain shorter and focused on
  implementation decisions, validation, and limitations.

### Current Documentation Structure

- `README.md` — concise project entry point.
- `docs/getting-started.md` — installation and usage guide.
- `docs/architecture.md` — current and planned architecture.
- `docs/runtime-configuration.md` — runtime configuration behavior.
- `docs/agent-profiles.md` — built-in and custom agent roles.
- `docs/context-files.md` — explicit context and future workspace retrieval.
- `docs/structured-outputs.md` — portable JSON Schema outputs.
- `docs/product-vision.md` — complete product direction.
- `docs/project-configuration.md` — proposed project-local configuration.
- `docs/roadmap.md` — completed and planned milestones.
- `DEVELOPMENT_LOG.md` — chronological implementation history.

### Next Milestone

Introduce provider-independent tool calling through shared tool definition,
invocation, result, registry, and execution-loop abstractions.

The initial implementation should use a safe deterministic tool and should not
yet introduce unrestricted filesystem or shell access.

## 2026-07-26 — Provider-Independent Tool Calling

### Implemented

- Added immutable provider-independent `ToolDefinition`, `ToolInvocation`, and
  `ToolResult` models.
- Extended shared requests, responses, and validated interaction rounds for
  ordered tool-calling history.
- Added translations for tool definitions, calls, results, and history in the
  Ollama, OpenAI Responses API, and Anthropic Messages API adapters.
- Added synchronous `ToolRegistry` execution and `run_tool_calling_loop()`.
- Added opt-in CLI tool enablement through `--enable-tools`.
- Added the first built-in tool: a restricted AST-based calculator.

### Architectural Decisions

- Kept tool definitions, invocations, results, registration, and execution
  provider-independent; providers only translate native request and response
  formats.
- Represented each completed request/response cycle as a validated
  `ToolInteractionRound`, preserving invocation and result ordering.
- Kept the CLI default unchanged: tools are disabled unless the user supplies
  `--enable-tools`.
- Kept execution synchronous and bounded new loop rounds with a positive
  maximum-round limit.
- Restricted the calculator to explicitly allowed arithmetic AST nodes and
  finite JSON-compatible results; it does not use dynamic code execution.

### Validation

- Ran a real localhost Ollama smoke test against installed `gpt-oss:20b`.
- The first model response invoked `calculator` with `(17 * 23) + 5`.
- The registry returned `{"expression": "(17 * 23) + 5", "result": 396}`.
- The final model response was `396`.
- The complete automated suite passed with 328 tests.

### Current Limitations

- Only the opt-in calculator is built in.
- Filesystem, network, MCP, asynchronous, and user-defined tools are not
  implemented.
- Internal tool rounds are not persisted across separate CLI user turns.

## 2026-07-27 — Controlled Single-Agent Coding Actions

### Implemented

- `75aff3e feat: add tool action approvals`
- `0587936 feat: add approved workspace file patches`
- `bb79303 feat: add approved validation tools`
- `d42444b feat: add controlled workspace actions to CLI`
- Added immutable exact-invocation approval requests and explicit approve/deny
  decisions without provider-specific fields.
- Added registry approval metadata and deterministic previews while preserving
  provider-facing tool schemas.
- Added one approved single-file complete-content compare-and-swap patch tool.
- Added fixed approved Ruff format/check and pytest tools.
- Added opt-in `--enable-actions`, requiring an explicit workspace, with
  default-deny CLI approval.

### Safety Design

- Approval is caller-owned, one-use, and valid only for the exact invocation.
- Approval-required tools must be requested alone in a provider response.
- Patch previews are complete unified diffs; preview and execution both verify
  content and path state.
- Writes reject absolute paths, traversal, all symlink components, `.git`,
  non-regular or invalid UTF-8 files, missing parents, stale content, oversized
  content, more than 500 changed lines, and previews over 64 KiB.
- Existing-file writes use same-directory atomic replacement and preserve
  portable permission bits; new files use exclusive creation.
- Validation commands are fixed non-shell Python module invocations with no
  caller flags or environment, minimal offline variables, isolated process
  groups, 30-second Ruff and 120-second pytest timeouts, and independent
  100 KiB stdout/stderr limits.
- Non-zero Ruff and pytest exits are returned to the model for diagnosis.

### Validation

- The complete automated suite passed with 557 tests.
- Ruff formatting, Ruff linting, and Git whitespace checks passed after every
  tracked stage.
- A real localhost `gpt-oss:20b` session created only through
  `create_agent_session()` read the source and test, requested one approved
  patch, formatted the source, ran Ruff, ran pytest, inspected the final Git
  diff, and returned `CODING-842`, `ruff: passed`, and `pytest: passed`.
- Only `demo/math_ops.py` changed in the temporary Git workspace; external
  content, tests, project configuration, `.git`, Git history, and the
  production repository remained unchanged.
- No external content was exposed and no unapproved action executed.
- Direct smokes confirmed absent/denied approval blocks patches and processes,
  multiple actions are rejected before execution, session retry remains
  possible, unsafe patch targets are rejected, option-like targets cannot
  inject flags, the environment is minimal, output is bounded, and timeouts
  terminate safely.

### Current Limitations

- Execution is synchronous.
- Writes are one file per invocation and cannot delete, rename, create
  directories, or form multi-file transactions.
- Only Ruff format/check and pytest are executable; arbitrary commands and
  caller-controlled flags are unavailable.
- Approval is interactive and not persisted.
- Internal tool rounds are not persisted across separate CLI user turns.
- Worktrees, broader permission profiles, task orchestration, persistence,
  concurrency, async execution, network tools, and MCP remain future work.
