# Self-Hosting Agent Workbench Development

This playbook uses the local `gpt-oss:20b` model for one bounded coding task
inside a separately approved Git worktree. The model may inspect, edit, format,
lint, test, and review the isolated workspace. Worktree creation, the final
local commit, and optional clean removal remain operator-side lifecycle
actions. Pushes and Pull Requests remain manual.

The primary repository must be completely clean before launch. Do not use this
workflow as a substitute for external review of security-sensitive or
repository-lifecycle changes.

## Supported workflow

1. Sync `main` manually.
2. When changing Agent Workbench itself, create the Agent Workbench feature
   branch manually.
3. Launch Agent Workbench with a separate absent sibling worktree and new local
   isolated branch.
4. Give `gpt-oss:20b` one bounded atomic task.
5. Require repository and named-file inspection before editing.
6. Require one `apply_workspace_changes` invocation containing every planned
   file.
7. Review the complete transaction preview and diffs.
8. Approve Ruff format, Ruff check, and pytest separately.
9. Require Git status and complete diff inspection.
10. Exit the session.
11. Enter the exact commit message at the operator prompt.
12. Review the immutable commit preview, exact path set, and every complete
    diff.
13. Approve the isolated local commit once.
14. Preserve the local branch and commit; optionally approve clean worktree
    removal separately.
15. Push and create a Pull Request manually only after external review.

## Recommended launch command

From the clean primary Agent Workbench repository:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent developer \
  --workspace . \
  --worktree-path ../agent-workbench-task \
  --worktree-branch agent/task-name \
  --enable-actions \
  --show-tool-traces
```

The worktree target must be absent, its parent must already exist, and the
branch must be a new local branch. Worktree isolation does not enable actions
implicitly. Keep `--enable-actions` only when the task genuinely requires
writes or validation commands.

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

Use test-driven development:
1. Add or update focused tests first.
2. Run <FOCUSED_TESTS> and explicitly confirm RED.
3. Prepare exactly one apply_workspace_changes transaction containing every
   planned file with complete expected and replacement content.
4. Wait for operator approval before that transaction.
5. Implement only the smallest correct change.
6. Run <FOCUSED_TESTS> and explicitly confirm GREEN.

Request at most one effectful tool per response. Wait for approval before
every effectful action. After focused GREEN, request these validations
separately and in order:
1. run_ruff_format for "."
2. run_ruff_check for "."
3. run_pytest for the focused target
4. run_pytest for "."

Then inspect Git status and the complete Git diff. Do not weaken tests or
validation. Do not independently request Git commit, push, merge, branch
deletion, reset, restore, clean, or stash. Do not use multiple effectful
actions in one response.

Stop and report:
- focused RED result;
- focused GREEN result;
- complete test count;
- Ruff results;
- exact changed files;
- complete diff review findings;
- remaining risks;
- proposed commit message: <COMMIT_MESSAGE>.
```

The operator, not the model, enters `<COMMIT_MESSAGE>` after the session exits.
Do not place secrets, credentials, private paths, or `.env` values in the task
prompt.

## What the operator must verify at approvals

### Transaction approval

- The path list exactly matches the allowed files.
- Every complete diff implements the requested behavior.
- Tests are not weakened or removed.
- Dependencies and configuration are unchanged unless explicitly authorized.
- No unrelated file, secret, `.env`, `.git`, or external path appears.
- Expected-content snapshots match the reviewed current files.

### Validation approval

- The action is the expected fixed Ruff or pytest tool.
- The target is exactly `"."` or the explicitly approved focused target.
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

## What to send to ChatGPT for review

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
- Proposed exact commit message.
- Local-agent final response.
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
- **Staging or commit failure:** the isolated index may be partially or fully
  staged. Do not reset or unstage automatically; inspect `HEAD`, the branch,
  index, worktree, and exact relative paths.
- **Dirty worktree:** preserve it. Clean removal is intentionally unavailable.
- **Ambiguous worktree, HEAD, ref, or registration:** stop and request external
  review. Do not retry, amend, force-remove, or delete the branch.

Never use reset, restore, clean, stash, or forced removal without deliberate
manual analysis of the exact preserved state.

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

## `COMMIT-842` validation evidence

The milestone was validated with a fresh temporary repository and the real
local `gpt-oss:20b` model. Worktree creation pinned a clean primary HEAD. The
model read the two implementation files and their test, changed exactly
`demo/labels.py` and `demo/math_ops.py` in one approved transaction, ran Ruff
format, Ruff check, and pytest successfully, and inspected status and diff.

The primary branch, HEAD, status, and tracked bytes remained unchanged. An
immutable preview for `fix: correct demo behavior` contained exactly both
complete diffs. One separate commit approval staged only those paths and
created one commit; its parent, exact message, path set, content, and diff were
verified. The isolated index and worktree became clean. Clean removal was
approved separately, while `agent/commit-842` and its commit remained.

No merge, push, fetch, pull, amend, reset, restore, clean, stash, force removal,
or branch deletion occurred. Separate smokes verified denial, stale plans,
partial staging, staged-diff mismatch, failed commit preservation, ambiguous
advanced HEAD preservation, and rejection of unsupported changes.
