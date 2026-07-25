# Product Vision

## Purpose

Agent Workbench is an AI engineering workspace for building, configuring, and
coordinating software-development agents powered by local and cloud language
models.

The project begins as a provider-independent command-line application, but its
long-term purpose is broader:

> Provide a workspace where a developer can create, configure, observe, and
> orchestrate multiple AI agents working on the same software project.

The user remains responsible for defining objectives, approving important
actions, reviewing results, and coordinating how agents collaborate.

Agent Workbench should make local models practical for software-development
workflows while preserving optional access to cloud providers when a task
requires different capabilities.

## Current Foundation

The current command-line application already provides several foundations for
the future workspace:

* Provider-independent model access.
* Local inference through Ollama.
* Cloud inference through OpenAI and Anthropic.
* Reusable agent profiles.
* Custom agent instructions.
* File-based conversation context.
* Provider-independent generation configuration.
* Provider-independent structured outputs.
* Interactive runtime configuration.
* Automated testing and provider simulation.

These capabilities are not the final user experience.

They establish shared application abstractions that future terminal and VS Code
interfaces can reuse.

```text
Current CLI
    ↓
Provider-Independent Application Layer
    ↓
Future Interfaces
├── Navigable terminal workspace
├── VS Code extension
└── Automated orchestration workflows
```

## Target Experience

The intended experience is a multi-agent development workspace inside VS Code.

A user should be able to open a software project and create several agent
sessions with different responsibilities:

```text
VS Code Workspace
├── Planner Agent
├── Developer Agent
├── Reviewer Agent
├── Tester Agent
└── Custom Agents
```

Each session can have its own:

* Agent profile.
* Provider.
* Model.
* System instructions.
* Generation configuration.
* Response format.
* Workspace context.
* Tool permissions.
* Conversation history.
* Assigned task.
* Execution status.

The user acts as the orchestrator rather than manually performing every
individual interaction.

## Example Workflow

A future workflow may look like:

```text
User Objective
    ↓
Planner Agent
    ├── analyses the request
    ├── inspects the project
    └── creates an implementation plan
            ↓
Developer Agent
    ├── receives an approved task
    ├── reads relevant files
    ├── modifies code
    └── runs focused checks
            ↓
Tester Agent
    ├── inspects the changes
    ├── creates or updates tests
    └── reports failures
            ↓
Reviewer Agent
    ├── reviews the diff
    ├── identifies risks
    └── recommends approval or revision
            ↓
User
    └── reviews and approves the final result
```

Agents should not operate as uncontrolled autonomous processes.

The user must remain able to inspect tasks, permissions, actions, results, and
changes.

## Core Product Principles

### Local First

Agent Workbench should provide first-class support for models running locally.

A developer should be able to use the workspace without sending private source
code to a cloud provider.

Local execution should remain useful even when cloud credentials are not
configured.

### Cloud Optional

Cloud providers should remain available when the user explicitly selects them.

The same agent and workspace abstractions should support Ollama, OpenAI,
Anthropic, and future providers without moving provider-specific logic into the
orchestration layer.

### Provider Independence

Agent behavior, project context, tools, tasks, and orchestration should not
depend directly on one model provider.

```text
Agent Session
    ↓
Provider-Independent Request
    ↓
Selected Provider Adapter
├── Ollama
├── OpenAI
└── Anthropic
```

### Human Orchestration

The user should control:

* Which agents exist.
* Which model each agent uses.
* Which task each agent receives.
* Which files an agent may access.
* Which tools an agent may execute.
* Which changes may be written.
* Which results are accepted.
* Whether another agent should review the work.

### Visible Work

Agent activity should be observable.

The user should be able to understand:

* What an agent is currently doing.
* Why it selected a tool.
* Which files it inspected.
* Which files it changed.
* Which commands it executed.
* Which tests passed or failed.
* Which task remains blocked.
* Which agent produced each result.

### Safe Execution

Reading files, modifying code, executing commands, accessing the network, and
changing Git state should be represented as explicit permissions.

Dangerous or destructive actions should require confirmation.

Agents should receive only the capabilities required for their assigned role.

## Agent Sessions

An agent session represents one active worker inside the workspace.

A future session abstraction may contain:

```text
AgentSession
├── id
├── profile
├── provider
├── model
├── system_prompt
├── generation_config
├── response_format
├── workspace_scope
├── tools
├── permissions
├── conversation
├── assigned_task
└── status
```

Possible session states include:

```text
idle
planning
waiting_for_approval
working
blocked
reviewing
completed
failed
```

The command-line conversation currently implemented by Agent Workbench can
become the first execution interface for an `AgentSession`.

## Workspace Context

The current `--context-file` workflow is an initial explicit context mechanism.

The final workspace should support multiple levels of project access.

### Explicit Attachments

The user can attach a specific file to an agent session.

Example:

```text
Review this file:
src/agent_workbench/providers/openai.py
```

### On-Demand File Access

An authorised agent can inspect project files through tools when required.

Example tools:

```text
list_files
read_file
search_text
search_symbols
inspect_git_diff
```

The complete project should not be inserted into every model request.

Agents should retrieve only the information needed for the active task.

### Project Indexing and Retrieval

For larger projects, Agent Workbench should support local project indexing and
Retrieval-Augmented Generation.

```text
Project Files
    ↓
Parsing and Chunking
    ↓
Embeddings
    ↓
Local Vector Store
    ↓
Relevant Project Context
    ↓
Agent Session
```

Direct file tools and semantic retrieval should complement each other.

File tools provide exact source access, while retrieval helps locate relevant
areas in large repositories.

## Tool Calling

Tools are required before agents can perform meaningful project work.

The shared tool system should eventually represent:

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

Initial workspace tools should focus on safe project inspection:

* List files.
* Read files.
* Search text.
* Inspect Git status.
* Inspect Git diffs.

Write and execution tools should be introduced separately with stricter
permissions:

* Create or update files.
* Run formatters.
* Run static analysis.
* Run tests.
* Execute approved commands.
* Create branches or worktrees.

## Multi-Agent Orchestration

The orchestrator coordinates agents but should not contain provider-specific
model logic.

Its responsibilities may include:

* Creating agent sessions.
* Assigning tasks.
* Tracking dependencies.
* Sharing approved results between agents.
* Requesting reviews.
* Detecting blocked tasks.
* Preventing conflicting writes.
* Collecting final outputs.
* Returning control to the user.

```text
User
    ↓
Orchestrator
├── Planner Session
├── Developer Session
├── Tester Session
└── Reviewer Session
```

The first orchestrator should be simple and deterministic.

Complex autonomous planning frameworks should only be introduced after tasks,
tools, permissions, and session state are stable.

## Isolated Agent Work

Multiple writing agents should not modify the same working directory without
coordination.

Future isolation options include:

* Separate Git branches.
* Git worktrees.
* Read-only review sessions.
* Per-agent temporary directories.
* Controlled patch generation.
* Explicit merge or apply operations.

A possible design is:

```text
Main Workspace
├── Planner: read-only
├── Developer A: worktree A
├── Developer B: worktree B
├── Tester: read-only or dedicated test worktree
└── Reviewer: read-only access to proposed diffs
```

Isolation should make parallel agent work understandable and reversible.

## VS Code Experience

The intended visual interface is a VS Code extension or workspace panel.

A future interface may include:

```text
Agent Workbench
├── Sessions
│   ├── Planner
│   ├── Developer
│   ├── Reviewer
│   └── Tester
│
├── Tasks
│   ├── Planned
│   ├── In Progress
│   ├── Blocked
│   └── Completed
│
├── Context
│   ├── Attached files
│   ├── Workspace access
│   └── Retrieved project context
│
├── Activity
│   ├── Tool calls
│   ├── File changes
│   ├── Commands
│   └── Test results
│
└── Controls
    ├── Start
    ├── Pause
    ├── Approve
    ├── Reject
    └── Reassign
```

Users should be able to open multiple agent terminals or session views inside
the same editor.

The interface should make it clear that the user is coordinating several
workers rather than chatting with one general assistant.

## Terminal Experience

Before the complete VS Code interface exists, Agent Workbench can evolve
through a navigable terminal workspace.

The future terminal interface may provide:

* Arrow-key menu navigation.
* Agent session lists.
* Session status indicators.
* Task assignment.
* Model and provider selection.
* Context attachment.
* Permission summaries.
* Confirmation screens.
* Switching between active sessions.

The existing prompt-based `--setup` flow remains a functional foundation, not
the final terminal experience.

## Voice Input

The future Agent Workbench interface should allow users to create prompts
through speech instead of requiring every instruction to be typed manually.

A user should be able to select an agent session, activate voice input, speak
an instruction in English or another supported language, review the generated
transcript, and confirm it before sending it to the agent.

The intended flow is:

1. The user selects an agent session.
2. The user activates push-to-talk or starts a recording.
3. A speech-to-text provider transcribes the audio.
4. The transcript appears inside the normal prompt editor.
5. The user reviews, corrects, or extends the transcript.
6. The user explicitly sends the final text to the agent.

Voice input should produce normal text prompts.

The selected language-model provider should not need to know whether the
prompt was typed or transcribed from speech.

Speech-to-text should use a provider-independent application boundary.

Possible transcription backends include:

- A local speech-to-text engine.
- An optional cloud transcription provider.
- Future operating-system or editor integrations.

The project should remain local-first.

Users should be able to transcribe prompts without uploading audio to a cloud
service when a compatible local transcription engine is available.

Voice input should support:

- English transcription.
- Explicit language selection.
- Automatic language detection where supported.
- Push-to-talk.
- Recording cancellation.
- Transcript preview.
- Manual transcript editing.
- Per-agent session input.
- Visible transcription errors.
- Configurable transcription providers.

Audio should not be retained by default.

A partial or unreviewed transcript should never trigger tool execution,
filesystem modification, command execution, or agent orchestration
automatically.

The user must confirm the final text prompt before it enters the selected agent
session.

A future VS Code interface may provide:

- A microphone button beside the prompt editor.
- A keyboard shortcut for push-to-talk.
- A recording status indicator.
- A transcript preview.
- Language and transcription-provider selection.
- Clear confirmation and cancellation controls.

Text input must remain fully supported.

Voice input is an additional interaction method rather than a replacement for
the normal prompt editor.

## Configuration

Users should eventually be able to configure an agent without modifying source
code.

A complete agent configuration may include:

```text
identity
instructions
provider
model
generation parameters
response format
tools
permissions
workspace scope
retrieval configuration
```

Configuration should be inspectable, portable, and safe to share when it does
not contain secrets.

API credentials must remain outside agent profile files.

## Evaluation and Observability

Agent Workbench should provide evidence that an agent workflow is reliable.

Future evaluation and observability capabilities may include:

* Task success rates.
* Tool execution traces.
* Provider latency.
* Token usage.
* Local model performance.
* Test results.
* Structured output validity.
* Retrieval quality.
* Agent handoff quality.
* Failure categorisation.
* Reproducible benchmark scenarios.

This information is important both for development and for demonstrating the
project in a professional portfolio.

## Development Direction

The intended development order is:

```text
Provider-Independent Foundation
        ↓
Structured Outputs
        ↓
Tool Calling
        ↓
Workspace and Filesystem Tools
        ↓
Agent Sessions
        ↓
Project Retrieval
        ↓
Multi-Agent Orchestration
        ↓
Execution Isolation
        ↓
VS Code Experience
        ↓
Evaluation and Deployment
```

The order may change when implementation discoveries require earlier
foundational work.

The product vision should remain stable even when individual milestones are
reordered.

## Near-Term Priorities

The next major engineering priorities are:

1. Provider-independent tool calling.
2. Safe read-only workspace tools.
3. Agent session abstraction.
4. Local project retrieval.
5. Initial orchestration between specialised agents.

The complete VS Code interface is a later product milestone, but architectural
decisions made now should avoid preventing it.

## Non-Goals for the Current Stage

The current stage does not attempt to provide:

* Fully autonomous software development.
* Unrestricted shell execution.
* Unreviewed source-code modification.
* Automatic deployment of agent-generated code.
* A complete VS Code extension.
* Parallel writes to the same working directory.
* Long-running background agents.
* Enterprise identity or access management.

These capabilities should not be implied before they are safely implemented.

## Success Criteria

Agent Workbench will fulfil its long-term vision when a developer can:

* Open a project in VS Code.
* Create multiple specialised agent sessions.
* Select local or cloud models for each session.
* Assign tasks and project context.
* Control file and tool permissions.
* Observe agent progress and actions.
* Allow agents to inspect, modify, test, and review code safely.
* Coordinate handoffs between agents.
* Review and approve the final result.
* Understand why each agent made its decisions.
