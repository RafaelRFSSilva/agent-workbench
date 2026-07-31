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
* Opt-in provider-independent tool calling, including safe workspace
  inspection and explicitly approved controlled coding actions.

The current application runs as a command-line interface.

Multi-agent orchestration, arbitrary shell/network access, MCP integration,
project retrieval, and the VS Code interface are future milestones.

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

Project indexing and RAG are not yet implemented; opt-in workspace tools can
inspect files and Python symbols on demand.

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

This exposes `list_files`, `read_file`, `search_text`, `search_symbols`,
`inspect_git_status`, and `inspect_git_diff`. `list_files` returns sorted
direct children only, including hidden entries, with file, directory, symlink,
and other classifications; it refuses directories with more than 128 entries.
`read_file` reads strict UTF-8 text up to 100 KiB and returns its canonical
workspace-relative path.

The workspace root and requested paths are resolved canonically. Absolute
paths, traversal, prefix-confusion paths, and symlinks escaping the root are
rejected. Internal symlinks remain available. `search_text` performs literal
recursive search in deterministic workspace-relative order, includes hidden
files and directories, skips invalid UTF-8, and does not follow directory
symlinks. It limits queries to 256 characters, inspects at most 512 files and
100 KiB per file, returns at most 256 matching lines of 1,000 characters, and
sets `truncated` when a limit applies.

`search_symbols` is Python-only and parses source with the standard-library
AST. It never imports or executes inspected modules. It finds classes,
functions, asynchronous functions, methods, and nested definitions using a
literal substring match against names and qualified names. Matching is
case-insensitive by default. The valid `kind` filters are exactly `any`,
`class`, `function`, and `method`; asynchronous functions and methods use
`is_async=true` rather than a separate kind.

Results use canonical workspace-relative paths and deterministic
path/line/qualified-name ordering. Hidden Python files and directories are
included, but recursive search does not follow directory symlinks. An
explicitly requested internal Python file symlink resolves to its canonical
target. Directory searches skip invalid UTF-8, invalid Python syntax, and
oversized files; explicit file searches reject these conditions safely.

Symbol queries are limited to 256 characters. One call inspects at most 512
Python files of at most 100 KiB each, returns at most 256 matches, and returns
qualified names up to 512 characters. `files_skipped` counts skipped files,
while `truncated` reports file, match, or qualified-name limits.

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
`read_file`, `search_text`, `search_symbols`, `inspect_git_status`, then
`inspect_git_diff`.

Show completed calls and results without changing conversation history:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --workspace . \
  --show-tool-traces
```

Traces are opt-in, compact deterministic JSON, and redact read content,
file-patch content, and absolute paths. Tool execution remains synchronous,
and internal tool rounds are not persisted across separate CLI user turns.

## Run a Controlled Coding Workflow

Controlled writes and validation are disabled by default. Enable them only for
an explicitly authorized workspace:

```bash
uv run agent-workbench code \
  --provider ollama \
  --model qwen3-coder:30b \
  --agent developer \
  --workspace . \
  --enable-tools \
  --enable-actions \
  --task "Fix the failing tests."
```

`--enable-actions` cannot be used without `--workspace`. It adds, after the
six read-only workspace tools:

1. `apply_file_patch`
2. `apply_file_rewrite`
3. `apply_text_replacement`
4. `apply_line_range_replacement`
5. `apply_workspace_changes`
6. `run_ruff_format`
7. `run_ruff_check`
8. `run_pytest`

When the calculator is also enabled it remains first, followed by the six
read-only tools and these eight actions.

Every action displays an informed preview and asks:

```text
Approve action? [y/N]:
```

Only `y` or `yes`, case-insensitively, approves. Blank input, EOF, interruption,
or any other text denies. Approval is one-use and exact-invocation only. The
model must request one effectful action per tool round; mixed or multiple
actions are rejected before execution.

Normal `code` output consists of concise typed phase, changed-path, validation,
repair, verification, DONE, or terminal-failure lines. Complete mutation diffs
remain visible in approval previews. Raw tool-call records are hidden unless
`--show-tool-traces` is supplied; the model's complete final prose is hidden
unless `--show-assistant-summary` is supplied.

`apply_file_patch` accepts exactly `path`, `expected_content`,
`replacement_content`, and optional `create_if_missing`. It updates one
existing UTF-8 file only when its complete current content matches, or creates
one new file in an existing directory when explicitly requested. The complete
unified diff is shown before approval, then target state is revalidated.
Existing files use same-directory atomic replacement. Writes never follow
symlinks and never target `.git`.

Patch content and existing files are limited to 100 KiB, one patch may change
at most 500 removed/added lines, and the complete preview must fit within
64 KiB without truncation.

Use `apply_file_rewrite` for a whole-file replacement of an existing file.
First call `read_file` for the complete file; do not construct the replacement
from a partial line-range read. Pass the SHA-256 from that complete latest read
and the complete resulting file as `replacement_content`. The action cannot
create a file. It internally verifies the SHA, shows the complete diff,
revalidates after approval, atomically replaces the file, and preserves its
permissions.

Use `apply_text_replacement` for a small exact edit to an existing file after
reading that file. Its closed input shape is:

```json
{
  "path": "relative/path.py",
  "expected_text": "exact existing literal text",
  "replacement_text": "literal replacement text",
  "expected_file_sha256": "sha256 from the latest read_file result",
  "expected_occurrences": 1
}
```

`expected_text` must be non-empty. `expected_occurrences` must match the
current number of non-overlapping literal occurrences exactly. The file's
complete current SHA-256 must match `expected_file_sha256`, including when the
preceding `read_file` call returned only a bounded line range. The action does
not use regular expressions and cannot create files.

Agent Workbench constructs the complete replacement internally and shows the
complete unified diff before approval. Execution then rereads and revalidates
the file before reusing the same atomic update and permission-preservation
boundary as `apply_file_patch`.

Use `apply_line_range_replacement` after inspecting a known range, especially
in a large existing file. Its exact closed input shape is:

```json
{
  "path": "relative/path.py",
  "start_line": 120,
  "end_line": 124,
  "replacement_content": "exact replacement for the selected lines",
  "expected_file_sha256": "sha256 from the latest read_file result"
}
```

Lines are one-based and inclusive. Every field is required and the complete
current file SHA-256 must match. The action rejects additional fields, invalid
or out-of-range coordinates, stale hashes, fuzzy matching, and automatic
fallback to a whole-file rewrite. It preserves surrounding content, shows the
complete unified diff, and revalidates before atomic replacement. Never guess
the hash or line numbers; inspect the current file first.

Use `apply_workspace_changes` when several creations or updates must succeed
together. Its exact closed input shape is:

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

Every element requires `path`, `expected_content`, and `replacement_content`;
`create_if_missing` is optional and defaults to false. Planning validates all
targets, rejects duplicate canonical paths, sorts changes by canonical
relative path, and presents every complete diff in one approval preview. The
transaction is limited to 16 files, 512 KiB each of combined expected and
replacement content, 2,000 changed lines, and a complete combined preview of
256 KiB. The 100 KiB existing/expected/replacement and 500-changed-line limits
still apply to each file.

After approval, execution prepares replacement and rollback material for every
change, revalidates the complete plan, then commits in deterministic order.
Handled in-process commit failures roll back applied changes in reverse order:
updates are restored and created files removed. This guarantee applies only
when rollback succeeds. It is not global filesystem atomicity and does not
cover power loss, `SIGKILL`, abrupt process or operating-system termination,
filesystem or disk failure, or rollback failure. If rollback is incomplete,
Agent Workbench reports the relative paths that require manual inspection.

File deletion, rename, directory creation, binary files, and mode changes are
unsupported.

The validation tools run fixed commands without a shell or caller flags.
During autonomous coding, the controller formats only existing `.py` paths
reported by successful approved workspace actions in the current task, one
sorted workspace-relative path at a time. Baseline-dirty and unrelated files
are not formatter targets unless a successful task action changed that exact
path. A non-Python-only task records a typed formatter skip. Ruff check and
pytest still target `"."` and inspect the complete project. Ruff commands time
out after 30 seconds; pytest times out after 120 seconds. Stdout and stderr are
independently capped at 100 KiB. The minimal offline environment provides no
dependency installation or public-network capability. Ruff and pytest
non-zero exit codes are returned normally so the model can diagnose them.
Ruff format may change its exact approved target, and pytest executes project
code, so review each preview carefully.

The controller captures baseline changed paths before DISCOVER. After every
formatter invocation and again before DONE, it rejects any tracked or
untracked changed path outside that baseline plus the exact paths from
successful approved actions. It never resets, restores, cleans, stages, or
deletes unexpected paths; the workspace remains available for manual recovery.

Suggested request:

```text
Inspect the relevant files. Use apply_text_replacement when the exact current
fragment is known and reasonably small. After inspection, use
apply_line_range_replacement for a known one-based inclusive range,
particularly in a large file, with the exact current read_file SHA-256. Never
guess a hash or uninspected line numbers. Before a true whole-file change, call
read_file for the complete file, then use apply_file_rewrite with that read's
SHA and the complete resulting file; never use it to avoid an exact-content
mismatch. Use apply_workspace_changes when changes must succeed together.
Request approval before every write or validation action, run Ruff and pytest,
inspect the final Git status and diff, and summarize the result. Never weaken
tests or validation. Do not commit or push.
```

## Run a Controlled Workflow in an Isolated Worktree

The primary workspace can remain clean while one coding session uses a new
local branch and sibling Git worktree:

```bash
uv run agent-workbench code \
  --provider ollama \
  --model gpt-oss:20b \
  --agent developer \
  --workspace . \
  --worktree-path ../agent-workbench-task \
  --worktree-branch agent/task \
  --enable-actions \
  --task "Fix the failing tests." \
  --commit-message "fix: correct the failing tests"
```

The two worktree options are an explicit pair; neither has an inferred value.
They require `--workspace`, and isolation does not imply `--enable-actions`.
The source must be the clean top-level primary worktree of a non-bare
repository with an existing HEAD. Staged, unstaged, and untracked changes or
an in-progress Git operation prevent planning.

The operator flow is:

```text
Clean primary repository
        ↓
Create immutable worktree plan
        ↓
Review and approve creation
        ↓
Create local branch and isolated worktree
        ↓
Construct isolated AgentSession
        ↓
Inspect, patch, format approved Python paths, check project, test project,
status, diff
        ↓
Validate every changed entry and require a clean index
        ↓
Enter an exact commit message
        ↓
Review complete ordered paths and unified diffs
        ↓
Approve one exact isolated local commit
        ↓
Verify commit and unchanged primary worktree
        ↓
Separately approve clean worktree removal
        ↓
Keep local branch and commit
```

Before the first prompt, `WorktreePlan` pins the canonical source root, complete
source HEAD, exact validated branch, canonical absent target, and safe target
display. Planning rejects a linked or bare source, an existing branch, unsafe
target parent or registered-worktree collision, and repository-local checkout
filters or external diff commands that could execute programs. The target
parent must already exist and contain no symlink component.

The creation prompt displays the safe source (`.`), pinned HEAD, branch,
target, exact fixed command, and effects. Approval is default-deny and applies
only to that plan. Every safety-relevant condition is revalidated after
approval. Git hooks are disabled, system and global Git configuration are
isolated, the child environment is minimal, and the command uses no shell or
network operation. A failed or ambiguously verified creation reports whether
the branch, target, and registration exist and preserves that state.

`WorktreeHandle` is returned only after the target registration, HEAD, branch,
unchanged source, and absence of upstream tracking are verified. The isolated
factory revalidates that handle and replaces only the copied runtime
configuration's workspace. Context documents inside the source are remapped by
relative path and reloaded from the worktree; missing mapped or external
context is rejected. Provider, model, profile, prompt, generation, output,
tool, action, and trace settings remain unchanged.

After a dirty CLI exit, Agent Workbench reports the safe target, branch, and
changed-entry count, then asks:

```text
Commit message (blank to preserve worktree):
```

Blank, EOF, or interruption preserves the dirty worktree without planning or
staging. A valid non-blank message produces an immutable preview containing the
exact branch, old HEAD, unchanged message, deterministic path list, counts, and
every complete per-file unified diff. The operator then receives one
default-deny prompt:

```text
Approve isolated commit? [y/N]:
```

Only `y` or `yes` approves. The implementation revalidates the complete plan,
stages only its exact paths, verifies the staged path set and content, creates
one fixed hookless/editorless/unsigned commit, and verifies its parent, message,
paths, diff, index, worktree, branch, and unchanged primary source. After
success it displays the new HEAD. Clean worktree removal remains a separate
approval and never deletes the local branch or its commit.

Supported commit entries are modified tracked and new untracked UTF-8 regular
files. Deletions, renames, copies, mode changes, symlinks, submodules, binaries,
conflicts, pre-staged content, and arbitrary path selection are rejected as a
whole. Stale state before staging causes no Git mutation. Failures after
staging begins may preserve a partially or fully staged index; failures after
commit begins may preserve an ambiguous advanced HEAD. There is no automatic
retry, amend, reset, restore, clean, stash, removal, or branch deletion.
Manual inspection is required, and the boundary is not crash-safe.

See [Self-Hosting](self-hosting.md) for a complete operator playbook.

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
