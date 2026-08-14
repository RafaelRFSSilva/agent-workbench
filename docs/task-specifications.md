# Task Specifications

## Overview

Agent Workbench provides provider-independent task metadata through `TaskSpec`.

A task specification describes one bounded objective together with the ordered
acceptance criteria that define when the task can be considered complete.

`TaskSpec` is an immutable application model. It does not depend on Ollama,
OpenAI, Anthropic, provider request formats, workspace tools, or the CLI.

## Data Model

```text
TaskSpec
├─ objective
├─ acceptance_criteria
├─ dependencies
└─ task_id
```

- `objective` is the exact non-blank string supplied by the caller.
- `acceptance_criteria` is an ordered immutable tuple of non-blank strings.
- `dependencies` is an ordered immutable tuple of `TaskId` instances. It defaults to an empty tuple and preserves both order and duplicates exactly.
- `task_id` is an optional `TaskId` instance or `None`. It preserves the
  original string value and enforces that it is a non-blank string.

### TaskId

```text
TaskId
└─ value
```

- `value` is the exact string supplied by the caller. Leading or trailing
  whitespace is preserved.

The model is frozen, slotted, value-comparable, and hashable.

## Validation

A valid task specification requires:

- An objective that is a string with at least one non-whitespace character.
- An iterable of acceptance criteria, each a string with at least one
  non-whitespace character.
- At least one acceptance criterion.
- A `task_id` that is either `None` or an instance of `TaskId`. If provided,
  the underlying value must be a non-blank string.
- Dependencies that are an iterable of `TaskId` instances. Each element must be a valid `TaskId`; strings and other types are rejected.

A bare string is rejected as the acceptance-criteria collection because it
would otherwise be interpreted as an iterable of individual characters.

Invalid values raise `ConfigurationError` with messages matching the
specification above.

## Preservation Semantics

Valid text is preserved exactly, including any leading or trailing whitespace.

Agent Workbench does not:

- Strip surrounding whitespace from valid values.
- Rewrite wording.
- Reorder acceptance criteria.
- Deduplicate criteria.
- Normalise capitalisation or punctuation.
- Alter the `task_id` value once set.

The acceptance-criteria iterable is converted to a tuple during construction,
and the optional `task_id` is stored as an immutable object. Later mutation of
an original list therefore cannot change the stored task.

Similarly, the dependencies iterable is snapshotted into an immutable tuple,
preserving order and duplicates exactly.

## AgentSession Integration

`AgentSession` accepts optional task metadata:

```python
from agent_workbench.session import AgentSession, SessionId
from agent_workbench.tasks import TaskSpec

task_spec = TaskSpec(
    objective="Add provider-independent task metadata.",
    acceptance_criteria=[
        "The task model is immutable.",
        "The session exposes the configured task.",
        "Existing session construction remains compatible.",
    ],
)

session = AgentSession(
    id=SessionId("task-session"),
    provider=provider,
    task_spec=task_spec,
)
```

The configured value is available through the read-only property:

```python
session.task_spec
```

When no task is supplied:

```python
session.task_spec is None
```

The exact `TaskSpec` instance supplied by the caller is preserved.

## Provider Independence

`TaskSpec` belongs to the shared application layer.

It is not translated into provider-specific request fields and does not change
the behavior of:

- `OllamaProvider`.
- `OpenAIProvider`.
- `AnthropicProvider`.
- `ChatProvider`.
- `ChatRequest`.
- Tool invocation or approval.
- Workspace actions.
- Git worktree isolation.

This keeps task identity independent from the model selected for a session.

## Current Behavior

Task metadata is currently descriptive only.

`dependencies` records dependency metadata only. Agent Workbench does not yet execute dependency graphs, schedule dependent tasks, resolve dependencies, or detect dependency cycles.

Attaching a `TaskSpec` to an `AgentSession` does not automatically:

- Insert the objective into the system prompt.
- Insert acceptance criteria into model context.
- Change provider requests.
- Start an autonomous task loop.
- Run tools or tests.
- Evaluate acceptance criteria.
- Track progress.
- Mark a task as complete, failed, or blocked.
- Assign the task to another agent.
- Persist task state.
- Create agent handoffs.

Callers remain responsible for deciding how the metadata is used.

## Intended Architecture

The current boundary is:

```text
Caller
  ↓
TaskSpec
  ↓
AgentSession metadata
```

Future orchestration may extend this into:

```text
User Objective
  ↓
Approved TaskSpec
  ↓
Assigned AgentSession
  ↓
Supervised execution
  ↓
Acceptance evaluation
  ↓
Review and user approval
```

Those orchestration and lifecycle capabilities are not part of the current
implementation.

## Future Work

Possible future additions include:

- Task lifecycle states.
- Manual task assignment.
- Planner-proposed tasks.
- Agent handoffs.
- Progress and blocked-work tracking.
- Acceptance-criteria evaluation.
- Task-aware prompt construction.
- Persistence and serialization.
- Terminal and VS Code task interfaces.
- Multi-agent orchestration traces.

Task identifiers are now part of the current data model; they no longer appear
in this list. All future extensions must remain provider-independent and
must not bypass workspace, permission, approval, or isolation boundaries.

## Related Documentation

- [Architecture](architecture.md)
- [Agent Profiles](agent-profiles.md)
- [Product Vision](product-vision.md)
- [Project Configuration](project-configuration.md)
- [Self-Hosting](self-hosting.md)
- [Roadmap](roadmap.md)
