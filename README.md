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
- Provider-independent tool calling with an opt-in calculator, read-only
  workspace inspection, and explicitly approved controlled workspace actions.
- Provider-independent `AgentSession` identity, lifecycle, transactional
  conversation ownership, and synchronous direct or tool-enabled sends.
- Immutable provider-independent `TaskSpec` metadata with ordered acceptance
  criteria and optional read-only attachment to an `AgentSession`.
- One-shot deterministic coding tasks through `--task`, with controller-owned
  DISCOVER, EDIT, VALIDATE, REPAIR, VERIFY, and DONE phases; explicit action
  approvals; fixed validation; final Git inspection; and optional tool traces.
- Reusable `AgentSession` construction from resolved runtime configuration,
  including providers and deterministic optional tool registries.
- End-to-end isolated autonomous coding with approved worktree creation,
  controlled actions, required Ruff and pytest validation, final Git inspection,
  an approved exact local commit, and preserved worktree and branch state.
- Automated tests, Ruff checks, and GitHub Actions.

Arbitrary shell and network tools, RAG, MCP, asynchronous execution,
user-defined tools, multiple simultaneous agents, and the VS Code interface
are planned but not yet implemented.

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
Read-only workspace inspection is also available on demand; project indexing
and RAG remain future milestones.

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

### Read-Only Workspace Tools

Authorize one workspace explicitly with `--workspace` to expose `list_files`,
`read_file`, `search_text`, `search_symbols`, `inspect_git_status`, and
`inspect_git_diff`; no workspace tools are available without it:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --workspace .
```

Combine it with `--enable-tools` to expose all tools in deterministic order:
`calculator`, `list_files`, `read_file`, `search_text`, `search_symbols`,
`inspect_git_status`, then `inspect_git_diff`.

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --enable-tools \
  --workspace .
```

The workspace root and every requested path are resolved canonically. Absolute
paths, traversal, prefix-confusion paths, and symlinks escaping the root are
rejected; symlinks that resolve within the root remain available. `list_files`
returns sorted direct children, including hidden entries, with a 128-entry
limit. `read_file` returns UTF-8 files up to 100 KiB using canonical relative
paths and includes the SHA-256 digest of the complete file, including for
partial line-range reads. Deliberately classified input failures return concise
`Invalid tool arguments: ...` diagnostics so a model can correct its next
invocation. Workspace resolution failures use one static diagnostic that does
not expose filesystem details, while unexpected handler failures remain the
generic `Tool execution failed.` error.

`search_text` performs literal recursive search in deterministic
workspace-relative order, including hidden entries but never following
directory symlinks. It skips invalid UTF-8 safely and is bounded to a
256-character query, 512 files, 100 KiB per file, 256 matching lines, and
1,000 returned characters per line; its `truncated` result indicates a limit.
Recursive listing and searches share one immutable traversal policy that omits
Git internals, virtual environments, Python/tool caches, dependency trees, and
generated build, coverage, and frontend output directories. Explicit safe
`list_files` requests starting inside those directories are rejected, while
explicit safe `read_file` access is unchanged.

`search_symbols` uses the standard-library Python AST without importing or
executing inspected code. It finds classes, functions, asynchronous functions,
methods, and nested definitions by literal name or qualified-name substring.
The valid `kind` values are exactly `any`, `class`, `function`, and `method`;
asynchronous state is reported separately through `is_async`. Matching is
case-insensitive by default, results use canonical relative paths and
deterministic file/line/qualified-name order, and hidden Python paths are
included. Directory symlinks are not followed, while an explicitly requested
internal file symlink resolves to its canonical target. Directory searches
skip invalid UTF-8, invalid syntax, and oversized Python files; explicit file
requests reject them with safe errors.

Symbol search limits are a 256-character query, 512 Python files, 100 KiB per
file, 256 matches, and 512 characters per returned qualified name.
`files_skipped` reports malformed or oversized files, and `truncated` reports
file, match, or qualified-name limits.

Git inspection uses only fixed non-shell commands: status is equivalent to
`git status --short --branch`, while diff returns separate unstaged, staged,
and safe untracked evidence. Safe untracked UTF-8 regular files appear as
new-file diffs without staging or index mutation. Inspection represents at
most 64 untracked files, reads at most 32 KiB per file, caps combined untracked
diff evidence at 64 KiB, and limits the complete compact, sorted-key UTF-8 JSON
result to 100 KiB. Unsupported or unsafe files return omission metadata without
contents. Detailed omission metadata has a 16 KiB budget and collapses to
deterministic reason counts when necessary.

Use `--show-tool-traces` with active tools to display compact deterministic
JSON invocation and result records before the final response. Traces are
opt-in, redact read content and absolute paths, and are not placed in
conversation history.

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --workspace . \
  --show-tool-traces
```

The calculator, all workspace tools, and traces can be enabled together:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --enable-tools \
  --workspace . \
  --show-tool-traces
```

These inspection tools remain read-only. They do not edit, delete, glob,
access the network, or use MCP.

### Controlled Workspace Actions

Controlled actions are disabled by default. `--enable-actions` requires an
explicit `--workspace PATH` and adds `apply_file_patch`,
`apply_text_replacement`, `apply_workspace_changes`, `run_ruff_format`,
`run_ruff_check`, and
`run_pytest` after the read-only tools:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent developer \
  --workspace . \
  --enable-actions \
  --show-tool-traces
```

Every effectful invocation displays a complete deterministic preview and
prompts `Approve action? [y/N]:`. Approval is default-deny, applies once to the
exact invocation, and is never cached. An approval-required action must be the
only invocation in its tool round. There is no automatic approval and no
arbitrary shell or caller-controlled command flag.

`apply_file_patch` performs one structured optimistic UTF-8 update or creation.
It compares the complete expected content, shows the complete unified diff,
and revalidates after approval. Existing files are replaced atomically while
preserving portable permission bits; new files use exclusive creation in an
existing directory.

`apply_text_replacement` performs one approved exact literal replacement in an
existing UTF-8 file without requiring the model to resend the complete file:

```json
{
  "path": "relative/path.py",
  "expected_text": "exact current fragment",
  "replacement_text": "exact replacement fragment",
  "expected_file_sha256": "sha256 from the latest read_file result",
  "expected_occurrences": 1
}
```

`expected_file_sha256` is required and must match the complete current file
digest returned by the latest `read_file` call. The action rejects any file
change between the read, preview, and execution, including changes outside the
literal fragment. `expected_occurrences` is optional and defaults to one. The
current file must contain exactly that number of non-overlapping literal
occurrences, with a
maximum of 16. Regular expressions, empty expected text, no-op replacements,
new-file creation, and ambiguous occurrence counts are rejected. Each literal
fragment is limited to 16 KiB. Agent Workbench constructs the complete
replacement internally, shows the complete unified diff, revalidates after
approval, and uses the same atomic replacement and permission-preservation
boundary as `apply_file_patch`.

`apply_workspace_changes` applies one approved transaction using this exact
closed shape:

```json
{
  "changes": [
    {
      "path": "relative/path.py",
      "expected_content": "complete expected content",
      "replacement_content": "complete replacement content",
      "create_if_missing": false
    }
  ]
}
```

The transaction validates every target and complete diff before approval,
sorts the plan by canonical relative path, rejects duplicate canonical
targets, and shows one complete combined preview. Execution prepares all
replacement and rollback material, revalidates the complete plan after
approval, then commits changes in sorted order. A handled in-process failure
rolls back already applied changes in reverse order. Successful rollback
restores updates and removes transaction-created files; an incomplete rollback
reports only the relative paths requiring manual inspection.

Per file, existing, expected, and replacement content is limited to 100 KiB
and a change to 500 added and removed lines. One transaction is limited to
16 files, 512 KiB of combined expected content, 512 KiB of combined
replacement content, 2,000 changed lines, and a complete 256 KiB combined
preview. Single-file previews retain their 64 KiB limit. Writes reject
absolute paths, traversal, every symlink component, `.git`, binaries, stale
content, missing parents, deletion, rename, directory creation, and mode
changes.

The rollback guarantee covers handled failures in the current process only
when rollback itself succeeds. It is not global filesystem atomicity and does
not cover power loss, `SIGKILL`, abrupt process or operating-system
termination, filesystem or disk failure, or rollback failure. Preserve and
inspect the reported paths manually after an incomplete rollback.

Validation uses only fixed non-shell commands against a canonical
workspace-relative target: Ruff format and check have 30-second timeouts,
while pytest has a 120-second timeout. Stdout and stderr are independently
bounded to 100 KiB, and the child environment is minimal and offline. The
tools cannot install dependencies or access the public network. Non-zero Ruff
and pytest exit codes are returned to the model for diagnosis. Ruff format may
modify files, and pytest executes project code, so all three commands require
approval.

With `--workspace` and `--enable-actions`, tool order is `list_files`,
`read_file`, `search_text`, `search_symbols`, `inspect_git_status`,
`inspect_git_diff`, `apply_file_patch`, `apply_text_replacement`,
`apply_workspace_changes`, `run_ruff_format`, `run_ruff_check`, then
`run_pytest`. With
`--enable-tools`, `calculator` comes first and the remaining order is
unchanged.

Example request:

```text
Inspect the relevant files. Prefer apply_text_replacement for small exact
edits to existing files, using the SHA-256 returned by read_file. For changes
that must succeed together, request one apply_workspace_changes transaction
with complete expected and replacement
content for every file. Request approval before every write or validation
action, run Ruff and pytest, inspect the final Git status and diff, and
summarize the result. Do not commit or push.
```

See [Architecture](docs/architecture.md) for the shared tool models and
provider translations.

## Run One Autonomous Coding Task

Use `--task` to give the configured agent one development objective, allow it
to inspect the authorized workspace, request approved changes, run validation,
inspect the final Git state, report the result, and then exit:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent developer \
  --workspace /path/to/project \
  --enable-actions \
  --max-tool-rounds 32 \
  --show-tool-traces \
  --task "Inspect the project, fix the defect with the smallest necessary change, run Ruff and pytest, inspect the final Git status and diff, and report the result."
```

`--task` requires both `--workspace` and `--enable-actions`. It cannot be
combined with `--setup`.

The controller limits DISCOVER to four read-only tool rounds. Use the optional
positive integer `--max-tool-rounds` value with `--task` to configure the
session default used by each EDIT or REPAIR send in direct or isolated
execution. The model may inspect the workspace and request structured file
changes. It cannot choose validation, verification, phase progression, or
DONE.

After a successful workspace action, the controller invokes
`run_ruff_format`, `run_ruff_check`, and `run_pytest` against `"."` in that
exact order. Failed validation enters a bounded REPAIR phase and reruns the
complete sequence after another successful change. The controller then invokes
Git status and diff inspection. Success requires a non-empty final
tracked, staged, or safe untracked diff and successful latest validation and
Git evidence. Omission metadata alone does not satisfy verification. Every file
change and executable validation remains default-deny and requires approval
for that exact invocation.

Direct and isolated results show the same stable evidence block:

```text
Final phase: DONE
Tool rounds: <count>
Workspace change applied: yes
Repair attempts: <count>
Completion continuations: <count>
Validation succeeded: yes
Git status inspected: yes
Git diff inspected: yes
```

`--show-tool-traces` displays each completed tool invocation and its redacted
result while the task is running. Read file contents and absolute workspace
paths are not displayed in traces.

The task mode does not automatically commit, merge, push, delete files, rename
files, install dependencies, run arbitrary shell commands, or access the public
network. Model quality and adherence to instructions depend on the selected
provider and model.

## Run One Autonomous Task in an Isolated Git Worktree

Keep a clean primary repository untouched while one supervised autonomous task
runs on a new local branch in a sibling worktree:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent developer \
  --workspace . \
  --enable-actions \
  --task "Fix the defect and validate the project." \
  --worktree-path ../agent-workbench-task \
  --worktree-branch agent/task \
  --commit-message "fix: correct the defect" \
  --show-tool-traces
```

`--worktree-path` and `--worktree-branch` must be supplied together. Isolated
execution also requires `--workspace`, `--enable-actions`, `--task`, and
`--commit-message`. The former interactive worktree session is not available
through these options.

Before any Git mutation, Agent Workbench validates the complete workflow input.
It then validates and pins the clean primary repository, current HEAD, branch,
absent target, registered worktrees, and checkout-sensitive local Git
configuration before showing an exact default-deny worktree creation preview.
Only `y` or `yes` approves the fixed local worktree command.

The isolated `AgentSession` receives workspace tools for the new worktree only,
and source-relative context files are reloaded from the corresponding isolated
paths. Every file change and validation command remains separately approved.
Commit planning cannot begin unless the deterministic controller reaches DONE
with successful Ruff format, Ruff check, pytest, Git status, and non-empty Git
diff evidence.

The exact `--commit-message`, ordered path set, and every complete diff are shown
before a separate default-deny commit approval. A verified successful commit
advances only the isolated local branch and leaves its worktree clean. The
worktree and branch are preserved after success; the CLI does not merge, push,
remove the worktree, or delete the branch.

The commit boundary supports modified tracked and new untracked UTF-8 regular
files. It does not support deletion, rename, mode changes, symlinks, submodules,
binaries, conflicts, or pre-staged content. Any validation, planning, approval,
staging, commit, or verification failure preserves available isolated state for
manual recovery. There is no automatic reset, restore, clean, stash, retry,
force removal, merge, push, or branch deletion. See the
[self-hosting guide](docs/self-hosting.md).

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
| [Task Specifications](docs/task-specifications.md) | Immutable objectives, acceptance criteria, and session task metadata |
| [Product Vision](docs/product-vision.md) | Multi-agent VS Code workspace and voice input |
| [Project Configuration](docs/project-configuration.md) | `.agent-workbench/`, skills, commands, and MCP |
| [Roadmap](docs/roadmap.md) | Completed and planned milestones |
| [Self-Hosting](docs/self-hosting.md) | Supervised local-agent coding and commit playbook |
| [Development Log](DEVELOPMENT_LOG.md) | Implementation history and decisions |

## Roadmap Summary

Completed foundations:

- [x] Local and cloud providers.
- [x] Provider-independent requests.
- [x] Agent profiles and file context.
- [x] Generation configuration.
- [x] Prompt-based setup.
- [x] Structured outputs.
- [x] Provider-independent tool calling, calculator, safe workspace
  inspection, and controlled approved coding actions.
- [x] Provider-independent AgentSession and reusable runtime factory.
- [x] Provider-independent immutable task specifications and optional
  `AgentSession` task metadata.
- [x] Supervised worktree isolation and approved exact local commits.
- [x] Provider-independent structured recovery evidence for isolated commit and
  worktree lifecycle failures, using conservative read-only Git inspection.
- [x] Deterministic coding controller: bound DISCOVER and model-facing edit and
  repair work, perform approved changes, run fixed Ruff and pytest validation,
  require final Git status and non-empty diff evidence, and enter DONE only
  from controller-owned gates.
- [x] End-to-end isolated autonomous workflow: create an approved worktree,
  run controlled coding, require validation and Git inspection, create an
  approved exact local commit, and preserve the worktree and branch.

Next milestones:

- [ ] Persistent lifecycle records and crash-safe restart recovery.
- [ ] Deletion and rename transactions with explicit conflict handling.
- [ ] Local project retrieval and project configuration.
- [ ] Task lifecycle, assignment, dependencies, and multi-agent orchestration.
- [ ] Terminal and VS Code interfaces.
- [ ] Voice input, evaluation, persistence, MCP, and AWS deployment.

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

Controlled workspace execution uses explicit authorization, canonical path
containment, exact-invocation confirmation, fixed commands, timeouts, output
limits, and visible previews. Supervised worktree creation additionally
requires a clean primary repository, pinned HEAD, safe branch and target,
separate lifecycle approvals, and conservative recovery. Broader repository
and MCP trust remain future work.

## Current Limitations

Agent Workbench does not yet provide:

- Arbitrary shell or network tools.
- File deletion, rename, or directory creation.
- Crash-safe or globally atomic multi-file writes.
- Caller-controlled commands or command flags.
- User-defined tools.
- Asynchronous tool execution.
- Project indexing or RAG.
- Persistent conversations.
- Multiple simultaneous agents.
- Multi-agent orchestration.
- MCP integration.
- Concurrent worktree management, automatic approval, merge, or push.
- DONE when the only changes are binary, unsupported, unreadable, oversized,
  sensitive, ignored, or beyond the bounded untracked-evidence limits.
- A post-hardening real-model benchmark; the current regression battery is
  scripted, and the earlier `qwen3-coder:30b` benchmark predates these fixes.
- A navigable terminal workspace.
- A VS Code extension.
- Voice prompt input.
- Cloud deployment.

## Author

Developed and maintained by Rafael Silva.

## License

Copyright © 2026 Rafael Silva.

Licensed under the Apache License 2.0.
