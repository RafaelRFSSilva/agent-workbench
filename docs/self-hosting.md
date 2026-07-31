# Self-Hosting Agent Workbench Development

This playbook documents the current supervised local-model workflow validated
with Ollama `gpt-oss:20b` on a bounded Python smoke task. The model may inspect
and request controlled edits. The application controller, rather than the
model, formats only approved changed Python paths, runs project-wide Ruff check
and pytest, and inspects Git status and diff in fixed phases. Worktree creation,
controlled actions, executable validation, and isolated commit creation remain
explicitly approved. Pushes and Pull Requests remain manual.

The primary repository must be completely clean before launch. Do not use this
workflow as a substitute for external review of security-sensitive or
repository-lifecycle changes.

## Validated supervised self-hosting workflow

Observed end-to-end behavior for a bounded Python implementation task:

1. Approved creation of one isolated worktree and branch.
2. Controlled repository inspection during DISCOVER.
3. Approved edit attempt with expected-content guards.
4. Safe rejection when an expected fragment did not match.
5. Approved recovery edit using a whole-file rewrite.
6. Approved follow-up edit to restore the required final newline.
7. Controller-owned Ruff formatting on changed Python paths.
8. Controller-owned Ruff linting and pytest execution.
9. Successful target-project test run.
10. Final verification of status and exact changed-path set.
11. Explicit approval of one isolated local commit.
12. One created isolated commit with exactly one modified source file.
13. Clean isolated worktree after commit.
14. Unchanged primary workspace.
15. Process exit status `0`.

This evidence validates supervised isolated operation for small bounded tasks.
It does not claim fully autonomous or production-ready operation for broad,
multi-file, or high-risk changes.

## Prerequisites

- A clean source repository before launch.
- Python and project dependencies already installed.
- A supported provider and model available.
- Explicit approval for worktree creation and all mutating actions.
- Sufficient `--max-tool-rounds` and model-output budget for the selected task.
- Repository-local Git identity (`user.name` and `user.email`) when isolated
  commit creation is requested.

Global Git identity is not sufficient for this workflow policy. The source
repository must contain its own local `user.name` and `user.email`.

## Repository-local identity preflight

Identity preflight runs before any isolated worktree creation and before
provider execution. If required repository-local Git fields are missing, the
workflow exits with status `1` and names the missing fields.

When preflight fails, the workflow does not:

- create a branch;
- create a worktree;
- start discovery;
- call the provider;
- request approval;
- edit files;
- run Ruff or pytest;
- create a commit.

Configure identity in the source repository:

```bash
git -C "/path/to/project" config --local user.name "Your Name"
git -C "/path/to/project" config --local user.email "you@example.com"
```

## Supported workflow

1. Sync `main` manually.
2. When changing Agent Workbench itself, create the Agent Workbench feature
   branch manually.
3. Choose one bounded atomic task, one exact commit message, one absent sibling
   worktree path, and one new local isolated branch.
4. Launch the complete isolated autonomous workflow.
5. Let the bounded read-only DISCOVER phase inspect repository and named files.
6. Use `apply_text_replacement` when the exact current fragment is known and
   reasonably small. After inspecting the current file, use
   `apply_line_range_replacement` for a known one-based inclusive range,
   particularly in a large file, with the exact current `read_file` SHA-256.
   Never guess a hash or uninspected line numbers. Use `apply_file_rewrite`
   only for a true whole-file change after a complete read, never to avoid an
   exact-content mismatch. Use one `apply_workspace_changes` transaction when
   several file changes must succeed together or when creation is required.
7. Review every complete action preview and diff.
8. Approve each controller-invoked Ruff format target separately, then approve
   project-wide Ruff check and pytest. Formatter targets are sorted existing
   Python paths produced by successful approved actions; formatting is safely
   skipped when none exist.
9. Require controller-owned DONE evidence: successful or safely skipped latest
   Ruff format, successful Ruff check and pytest, successful Git status and
   complete diff inspection, and non-empty tracked, staged, or safe untracked
   diff evidence.
10. Require the final Git path set to equal the effective paths produced by
    successful approved workspace actions.
11. Review the immutable commit preview, exact CLI-supplied message, exact path
    set, and every complete diff.
12. Approve the isolated local commit once.
13. Require the workflow to verify the new commit and a clean isolated worktree.
14. Preserve the worktree, local branch, and commit for external review.
15. Push and create a Pull Request manually only after external review.

Every DISCOVER, EDIT, and REPAIR prompt, including continuations, receives the
original ordered acceptance criteria. The controller carries bounded sanitized
discovery paths, metadata, and a normal discovery summary into later phases;
that evidence remains available when a maximum-round DISCOVER turn is not
committed to session history. If one EDIT or REPAIR send exhausts its
tool-round budget, a successful approved change can still advance to
validation. Without a change, the controller consumes one of the existing two
bounded completion continuations and states the exhaustion reason. Unrelated
completion errors remain terminal.

Failed controlled actions are carried into later EDIT and REPAIR prompts as
bounded safe evidence. The model is told that the workspace did not change and
must reread the target. Failed validation enters REPAIR with every failed tool
name, status, exit code, and bounded sanitized stdout and stderr excerpt.
Assertion details and dynamic runtime requirements remain visible unless they
match a credential, `.env`, or private-path boundary. Generic objectives,
summaries, discovery, and action failures retain a separate, more conservative
sensitive-line boundary.

## Canonical isolated command

From the clean primary Agent Workbench repository:

```bash
set +e

PYTHONDONTWRITEBYTECODE=1 uv run agent-workbench code \
  --workspace "/path/to/project" \
  --worktree-path "/path/to/project-task-worktree" \
  --worktree-branch "agent/task-name" \
  --provider ollama \
  --model "gpt-oss:20b" \
  --agent developer \
  --enable-tools \
  --enable-actions \
  --max-tool-rounds 8 \
  --max-output-tokens 8192 \
  --commit-message "feat: bounded change" \
  --task "Implement one bounded, reviewable task."

WORKFLOW_EXIT_CODE=$?

set -e

printf 'WORKFLOW_EXIT_CODE=%s\n' "$WORKFLOW_EXIT_CODE"
```

The worktree target must be absent, its parent must already exist, and the
branch must be a new local branch. Isolated execution requires `--workspace`,
`--enable-actions`, `--task`, both worktree options, and `--commit-message`.
The task and commit message are supplied before worktree creation; they are not
requested interactively after the coding session.

Normal output is concise controller-owned progress. Exact complete diffs remain
visible for workspace and commit approval. Add `--show-tool-traces` only for
debugging tool calls, or `--show-assistant-summary` when the model's complete
final prose is needed.

## Expected successful lifecycle

Expected progression for a successful isolated run:

1. worktree approval
2. `DISCOVER`
3. `EDIT`
4. `VALIDATE`
5. `VERIFY`
6. `DONE`
7. isolated commit approval
8. `ISOLATED` commit confirmation
9. exit status `0`

Worktree creation, file mutations, executable validation, and isolated commit
creation remain explicitly approval-controlled.

## Failure semantics

| Outcome | Exit status | Expected state |
| --- | --- | --- |
| Complete success | `0` | Isolated commit created and isolated worktree clean |
| Terminal failure after work begins | `1` | Isolated branch/worktree and recoverable changes preserved for manual inspection |
| Invalid preflight | `1` | No branch, worktree, provider request, edit, validation, or commit |

When a failure preserves isolated state, inspect it deliberately and clean it
up manually. Destructive automatic cleanup is intentionally avoided.

## Patch recovery behavior

A controlled patch can be rejected when expected content no longer matches the
current file state. Rejection is explicit and safe; the failed patch is not
silently applied. The model may then propose another controlled action (for
example, a whole-file rewrite) that still requires explicit approval.

## Evidence checklist after an isolated run

Use generic variables and inspect the preserved evidence directly:

```bash
WORKSPACE="/path/to/project"
ISOLATED_WORKTREE="/path/to/project-task-worktree"
ISOLATED_BRANCH="agent/task-name"
COMMIT_REF="<isolated-commit-ref>"

# Source repository status.
git -C "$WORKSPACE" status -sb

# Registered worktrees.
git -C "$WORKSPACE" worktree list --porcelain

# Isolated branch and worktree status.
git -C "$WORKSPACE" branch --list "$ISOLATED_BRANCH"
git -C "$ISOLATED_WORKTREE" status -sb

# Recent commits in the isolated worktree.
git -C "$ISOLATED_WORKTREE" log --oneline -n 5

# Changed paths in the isolated commit.
git -C "$WORKSPACE" show --name-only --pretty="" "$COMMIT_REF"

# Whitespace validity for commit diff.
git -C "$WORKSPACE" show --check "$COMMIT_REF"

# Accidental Python bytecode cache directories.
find "$WORKSPACE" -type d -name "__pycache__" -print
find "$ISOLATED_WORKTREE" -type d -name "__pycache__" -print

# Workflow status captured immediately after Agent Workbench finished.
printf 'Recorded workflow exit code: %s\n' "$WORKFLOW_EXIT_CODE"
```

## Reusable local-agent prompt template

Copy this prompt and replace every angle-bracket placeholder:

```text
Read and follow AGENTS.md.

Complete one bounded atomic milestone: <MILESTONE>.

Before editing, inspect:
<FILES_TO_INSPECT>

Modify only:
<ALLOWED_FILES>

Do not make assumptions before inspecting the existing abstractions,
conventions, tests, and Git state. Do not read .env, secrets, .git contents,
or outside paths. Do not use the network, install dependencies, modify
unrelated files, or request arbitrary commands.

Use the existing test conventions:
1. Inspect the implementation and its tests during DISCOVER.
2. Add or update focused tests together with the smallest implementation
   change when the objective requires them.
3. Use apply_text_replacement when the exact current fragment is known and
   reasonably small. After inspecting the current file, use
   apply_line_range_replacement for a known one-based inclusive range,
   particularly in a large file, with the exact current read_file SHA-256.
   Never guess a hash or uninspected line numbers. Use apply_file_rewrite only
   for a true whole-file change after a complete read, never to avoid an
   exact-content mismatch. Use one apply_workspace_changes transaction when
   several changes must succeed together.
4. Wait for operator approval before every write action.
5. Implement only the smallest correct change.
6. Do not restart broad discovery during EDIT or REPAIR.

Request at most one effectful tool per response. Wait for approval before
every effectful action. Do not request validation or Git verification tools:
the controller withholds them during model-facing phases and invokes sorted
per-file Ruff format, project-wide Ruff check and pytest, then Git status and
Git diff. If validation fails, resolve every listed failure and dynamic runtime
requirement in the bounded REPAIR evidence, then make another controlled
change. Do not weaken tests or validation. Do not independently
request Git commit, push, merge, branch deletion, reset, restore, clean, or
stash. Do not use multiple effectful actions in one response.

The final assistant summary may report:
- exact changes;
- controller-supplied validation results;
- exact changed files;
- remaining risks.

Only controller evidence, not this summary, determines success.
```

The operator supplies `<COMMIT_MESSAGE>` through `--commit-message` before the
workflow starts. The model must not create, amend, merge, push, remove a
worktree, or delete a branch independently. Do not place secrets, credentials,
private paths, or `.env` values in the task prompt or commit message.

## What the operator must verify at approvals

### Workspace change approval

- For `apply_file_rewrite`, the path is an intended existing file, the SHA came
  from the latest read, and the complete replacement diff is exact.
- For `apply_text_replacement`, the path is allowed, the expected literal
  fragment is sufficiently specific, the expected occurrence count is correct,
  and the complete diff contains only the intended edit.
- For `apply_line_range_replacement`, the path, one-based inclusive range, and
  exact current SHA-256 match the latest inspection, and the complete diff
  changes only that range.
- For `apply_workspace_changes`, the complete path list exactly matches the
  allowed files.
- Every complete diff implements the requested behavior.
- Tests are not weakened or removed.
- Dependencies and configuration are unchanged unless explicitly authorized.
- No unrelated file, secret, `.env`, `.git`, or external path appears.
- Expected-content snapshots match the reviewed current files.

### Validation approval

- The action is the expected fixed Ruff or pytest tool.
- A Ruff format target is one expected approved changed Python file.
- Ruff check and pytest target `"."` and inspect the complete project.
- No caller flags, arbitrary command, environment, or unexpected path appears.
- Only one effectful action is present in the response.

### Commit approval

- The branch is the intended isolated local branch.
- The old HEAD is the reviewed isolated baseline.
- The commit message is exact.
- The deterministic path list contains every and only expected file.
- Every complete diff matches the transaction and final review.
- Counts and operations are plausible.
- No amend, merge, push, branch deletion, or unexpected file is included.

Commit approval does not make staging and commit globally transactional. If
execution reports partial state, stop and inspect manually.

## Evidence bundle for external review

Provide this compact evidence bundle without secrets:

- Current branch.
- `git status -sb`.
- `git log --oneline main..HEAD`.
- Focused RED command and output.
- Focused GREEN command and output.
- Complete pytest command, count, and result.
- Ruff format and check results.
- Transaction preview path/count summary.
- Final `git diff --stat` and complete diff review findings.
- Approved exact commit message and new isolated HEAD.
- Local-agent final response summary.
- Every denied or failed action and the preserved recovery state.

Do not include `.env`, credentials, external secret files, canonical temporary
paths, or unrelated repository content.

## Failure recovery

- **Denied patch:** no write should occur; inspect status before retrying.
- **Stale transaction:** the transaction is rejected before writes; inspect and
  create a fresh plan rather than reusing approval.
- **Handled patch failure:** the workspace transaction attempts reverse-order
  restoration of updates and removal of transaction-created files.
- **Incomplete rollback:** stop and manually inspect every path listed in the
  error. Do not assume the workspace returned to its original state.
- **Commit denied or stale before staging:** no new staging or commit occurs.
- **Unexpected final path:** commit planning fails before preview, approval, or
  staging. Preserve every tracked and untracked path for manual inspection.
- **Staging or commit failure:** the isolated index may be partially or fully
  staged. Do not reset or unstage automatically; inspect `HEAD`, the branch,
  index, worktree, and exact relative paths.
- **Dirty worktree:** preserve it. Clean removal is intentionally unavailable.
- **Ambiguous worktree, HEAD, ref, or registration:** stop and request external
  review. Do not retry, amend, force-remove, or delete the branch.

Never use reset, restore, clean, stash, or forced removal without deliberate
manual analysis of the exact preserved state.

## Automated evidence and real-model benchmarks

The implemented regression battery uses scripted providers and real temporary
Git repositories. It deterministically proves phase progression, edit
continuations (including maximum-tool-round recovery), bounded cross-phase
discovery evidence, ordered acceptance criteria, validation-driven repair, the
repair limit, bounded cross-send action failures, simultaneous assertion and
dynamic runtime evidence, SHA-guarded whole-file repair, false completion
rejection, generated-directory filtering, read-only untracked Git evidence,
untracked-only DONE, isolated new-file commits, constrained formatting,
unexpected-path rejection, concise repair progress, and the final diff gate.
Automated tests do not call Ollama or any paid/cloud provider.

Those tests validate controller behavior, not local-model capability. A prior
manual benchmark with `qwen3-coder:30b` through Ollama exercised the earlier
deterministic workflow and exposed the traversal and untracked-file limitations
addressed here. Record model name, task fixture, approval decisions, phase
counters, validation results, and final diff for future benchmark runs.

V1 can reach DONE for tasks composed only of safe untracked UTF-8 regular files.
Git inspection does not stage them: it renders at most 64 files, reads at most
32 KiB per file, and caps combined untracked diff evidence at 64 KiB. The
complete compact, sorted-key UTF-8 JSON result is limited to 100 KiB. Binary,
unsupported, unreadable, oversized, sensitive, ignored, and limit-exceeding
files are omitted with metadata whose detailed form has a 16 KiB budget and
collapses to deterministic reason counts when needed. Omission metadata alone
cannot satisfy VERIFY and requires manual review.

## Current limitations

- Workflows remain approval-supervised for all mutating actions.
- Isolated commits are local only and are not pushed or merged automatically.
- Failure-preserved worktrees require deliberate manual inspection and cleanup.
- Output quality depends on the selected provider/model and task complexity.
- The validated `gpt-oss:20b` smoke task was intentionally small.
- Broader multi-file tasks still require additional end-to-end validation.

## Tasks suitable for `gpt-oss:20b`

- Immutable data models.
- Provider-independent `TaskSpec` foundations.
- `AgentSession` task metadata.
- Focused deterministic validation.
- Small schema additions.
- Additional focused tests.
- Documentation.
- Bounded refactors with exact file scope.
- Provider-independent pure logic.

Keep each task small enough to inspect in one complete transaction preview.

## Tasks requiring extra external review

- Git lifecycle changes.
- Deletion, rename, copy, or mode changes.
- Authentication, authorization, credentials, and secrets.
- Network access or dependency installation.
- Arbitrary command execution.
- Persistence or schema migrations.
- Concurrency and process isolation.
- Crash recovery.
- Merge or conflict automation.
- Any security-boundary change.
