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
├── objective
└── acceptance_criteria
```

- `objective` is the exact non-blank string supplied by the caller.
- `acceptance_criteria` is an ordered immutable tuple of non-blank strings.

The model is frozen, slotted, value-comparable, and hashable.

## Validation

A valid task specification requires:

- An objective that is a string.
- At least one non-whitespace character in the objective.
- An iterable of acceptance criteria.
- At least one acceptance criterion.
- Every criterion to be a string.
- At least one non-whitespace character in every criterion.

A bare string is rejected as the acceptance-criteria collection because it
would otherwise be interpreted as an iterable of individual characters.

Invalid values raise `ConfigurationError`.

## Preservation Semantics

Valid text is preserved exactly.

Agent Workbench does not:

- Strip surrounding whitespace from valid values.
- Rewrite wording.
- Reorder acceptance criteria.
- Deduplicate criteria.
- Normalise capitalisation or punctuation.

The acceptance-criteria iterable is converted to a tuple during construction.
Later mutation of an original list therefore cannot change the stored task.

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

- Task identifiers.
- Task lifecycle states.
- Dependencies.
- Manual task assignment.
- Planner-proposed tasks.
- Agent handoffs.
- Progress and blocked-work tracking.
- Acceptance-criteria evaluation.
- Task-aware prompt construction.
- Persistence and serialization.
- Terminal and VS Code task interfaces.
- Multi-agent orchestration traces.

These should remain provider-independent and must not bypass workspace,
permission, approval, or isolation boundaries.

## Related Documentation

- [Architecture](architecture.md)
- [Agent Profiles](agent-profiles.md)
- [Product Vision](product-vision.md)
- [Project Configuration](project-configuration.md)
- [Self-Hosting](self-hosting.md)
- [Roadmap](roadmap.md)
