# Project Configuration

## Purpose

Agent Workbench should allow each software repository to define how AI agents
are configured and how they may interact with that project.

The current command-line interface supports explicit runtime configuration and
discovers foundational coding defaults from `.agent-workbench/config.toml`.
Configured projects may also provide automatic project-level coding
instructions. The broader structures described later in this document remain
future design work.

## Implemented Project Coding Instructions

A configured project may contain:

```text
your-project/
├── .agent-workbench/
│   ├── config.toml
│   └── instructions.md
└── project source files
```

The `instructions.md` file is optional. It belongs to the exact project root
containing the `.agent-workbench/config.toml` selected by configuration
discovery. Starting a coding command in a nested directory uses that discovered
root; Agent Workbench does not perform a separate instructions traversal or
load instructions from a parent project, sibling repository, neighbouring
worktree, or user home directory.

`agent-workbench init` creates `config.toml`, but it does not create
`instructions.md`. A minimal configured workflow is:

```bash
agent-workbench init --provider ollama --model gpt-oss:20b
agent-workbench code "Fix the failing tests."
```

An instructions file can contain ordinary Markdown such as:

```markdown
# Project Instructions

- Use Python 3.12.
- Keep all public functions fully typed.
- Prefer small, focused changes.
- Run Ruff and pytest after modifications.
- Do not modify public APIs without explicit approval.
```

### Loading and validation

Project instructions must be a regular, readable file encoded as strict UTF-8.
The maximum size is 102,400 bytes: content exactly at 100 KiB is accepted, and
larger content is rejected before session or provider construction. Invalid
UTF-8, directories, and unsupported filesystem objects are rejected with
concise configuration errors. Empty and whitespace-only files are valid but
contribute no additional instructions. Agent Workbench reads the contents
without modifying, creating, deleting, renaming, or normalizing the source
file.

### System-context composition

For coding tasks, the complete existing system prompt or agent-profile
instructions remain first. Agent Workbench then appends exactly one section:

```text
<project_instructions>
...
</project_instructions>
```

The original non-whitespace Markdown is preserved inside the delimiter. The
source path is not included in model context, and the contents are not inserted
into the user task, treated as a context-file attachment, or duplicated.
Explicit system prompts and built-in or custom agent profiles retain their
existing selection semantics. Direct and isolated coding workflows use the
same system-context composition, and validated instructions do not leak into
later, unrelated sessions. Non-coding interactive sessions do not apply these
coding instructions.

### Security boundary

Project instructions are model instructions, not executable commands, tool
authorization, or approval. They do not bypass default-deny tool and action
approvals and cannot authorize arbitrary command execution. File modifications
and fixed validation commands remain subject to the existing approval policy.

A local Codex `AGENTS.md` is separate operator guidance. Agent Workbench loads
only `.agent-workbench/instructions.md` for this feature and does not treat
`AGENTS.md` as project instructions.

## Proposed Project Structure

A future project may contain:

```
your-project/
├── .agent-workbench/
│   ├── project.md
│   ├── project.local.md
│   ├── settings.toml
│   ├── settings.local.toml
│   ├── agents/
│   │   ├── developer.toml
│   │   ├── reviewer.toml
│   │   └── tester.toml
│   ├── rules/
│   │   ├── code-style.md
│   │   ├── testing.md
│   │   └── security.md
│   ├── skills/
│   │   ├── code-review/
│   │   │   └── SKILL.md
│   │   └── release-validation/
│   │       └── SKILL.md
│   ├── commands/
│   │   ├── review.md
│   │   ├── fix-issue.md
│   │   └── validate-release.md
│   └── mcp.toml
└── project source files
```

This is a proposed structure rather than a final implemented format.

The exact filenames and schemas should be stabilised only after agent sessions,
tool calling, permissions, and workspace access have clear application models.

## Shared and Local Configuration

Project configuration should distinguish between files shared through Git and
private local overrides.

### Shared Configuration

The following project configuration may be committed to the repository:

- `.agent-workbench/project.md`
- `.agent-workbench/settings.toml`
- `.agent-workbench/agents/`
- `.agent-workbench/rules/`
- `.agent-workbench/skills/`
- `.agent-workbench/commands/`
- `.agent-workbench/mcp.toml`

Shared configuration may define:

- Project instructions.
- Supported workflows.
- Shared agent profiles.
- Coding conventions.
- Testing requirements.
- Security requirements.
- Safe tool permissions.
- MCP server definitions that contain no credentials.

### Local Configuration

The following files should normally be ignored by Git:

- `.agent-workbench/project.local.md`
- `.agent-workbench/settings.local.toml`

Local configuration may define:

- Personal model preferences.
- Local provider selections.
- Local filesystem paths.
- Developer-specific permissions.
- Private MCP server overrides.
- Personal workflow preferences.

Secrets must not be stored directly in shared or local project configuration.

API keys and credentials should remain in environment variables or dedicated
secret stores.

## Project Instructions

`project.md` may provide instructions that apply to every agent working inside
the repository.

These instructions may describe:

- The purpose of the project.
- Architectural constraints.
- Supported language and runtime versions.
- Formatting and static-analysis commands.
- Test commands.
- Security requirements.
- Files that should not be modified.
- The expected Git workflow.
- Pull request requirements.

Project instructions should complement agent instructions.

They should not replace the identity or specialised behavior of an
`AgentProfile`.

The effective instructions for a session may eventually combine:

- Project instructions.
- Agent profile instructions.
- Relevant project rules.
- The task assigned by the user.

## Local Project Instructions

`project.local.md` may contain personal instructions that should not be shared
through Git.

Examples include:

- A preferred local model.
- Local development paths.
- Personal testing commands.
- Temporary experiments.
- Developer-specific notes.

Local instructions must not silently weaken shared security restrictions.

The future configuration resolver should define explicit precedence and merging
rules.

## Settings

`settings.toml` may eventually define shared project behavior.

Possible configuration areas include:

* The default agent.
* Default workspace permissions.
* Command confirmation requirements.
* Allowed project directories.
* Available response formats.
* Enabled rules and skills.
* Tool restrictions.
* MCP server availability.

`settings.local.toml` may provide approved local overrides without modifying the
shared project configuration.

The configuration format has not yet been implemented.

Its final schema should be defined only after the permission, workspace, tool,
and agent-session models are stable.

## Project Agents

Project-specific agents may be stored under:

* `.agent-workbench/agents/`

These agents should reuse the same provider-independent `AgentProfile`
abstraction currently used by built-in and custom profiles.

Project agents may describe specialised responsibilities such as:

* Backend development.
* Frontend development.
* Security review.
* Database migration review.
* Documentation maintenance.
* Release validation.
* Performance investigation.

The current `--agent-file` argument is the first explicit mechanism for loading
a project-specific agent.

A future project agent definition may reference:

* A profile name.
* A role description.
* System instructions.
* Default rules.
* Default skills.
* Preferred tools.
* Workspace scope.
* A response format.
* Generation defaults.
* Permission requirements.

Provider API credentials must never be stored in agent profile files.

Provider and model selection should remain configurable independently unless a
user explicitly chooses to define project defaults.

## Rules

Rules are modular project instructions that can be shared between agents.

Possible rule files include:

* `rules/code-style.md`
* `rules/testing.md`
* `rules/security.md`
* `rules/documentation.md`
* `rules/api-conventions.md`
* `rules/git-workflow.md`

Each rule should focus on one project concern.

For example, a testing rule may describe:

* The required test framework.
* Test directory conventions.
* Commands that must pass.
* Required edge-case coverage.
* Restrictions on real external API calls.
* Expectations for deterministic test doubles.

A security rule may describe:

* Secret-handling requirements.
* Input validation expectations.
* Prohibited unsafe operations.
* Path containment rules.
* Network-access restrictions.
* Required confirmation for destructive actions.

Agents may receive rules according to:

* Their profile.
* Their assigned task.
* The files involved in the task.
* Explicit user selection.
* Project-level defaults.

Rules should not all be inserted into every model request automatically.

The future runtime should select only relevant instructions to reduce context
usage and avoid contradictory guidance.

## Skills

Skills represent reusable workflows or capabilities.

A skill may define:

* When it is applicable.
* Required inputs.
* Required tools.
* Ordered workflow steps.
* Expected outputs.
* Validation requirements.
* Permission requirements.
* Failure and recovery behavior.

Possible skills include:

* Code review.
* Test failure investigation.
* Dependency upgrade analysis.
* Release validation.
* Documentation synchronisation.
* Security assessment.
* Pull request preparation.

A skill may be stored in a dedicated directory such as:

* `.agent-workbench/skills/code-review/SKILL.md`
* `.agent-workbench/skills/release-validation/SKILL.md`

A code-review skill may instruct an agent to:

1. Inspect the current Git diff.
2. Identify the affected modules.
3. Read relevant implementation files.
4. Inspect existing tests.
5. Evaluate correctness and security.
6. Return structured findings.
7. Avoid modifying source files.

A release-validation skill may instruct an agent to:

1. Inspect the release changes.
2. Run approved quality checks.
3. Verify documentation updates.
4. Inspect version information.
5. Report blockers through a structured response.

Skills should use the shared Agent Workbench tool system.

They should not invoke provider-specific APIs directly.

A skill describes a workflow, while an agent profile describes identity and
general behavior.

The same skill may therefore be used by multiple agents.

## Commands

Project commands provide named workflows that the user can start explicitly.

Possible commands include:

- `review`
- `fix-issue`
- `run-quality-checks`
- `validate-release`
- `prepare-pull-request`
- `investigate-failure`

A command may reference:

- An agent profile.
- A skill.
- Relevant rules.
- Required project context.
- Required tools.
- Tool permissions.
- Generation configuration.
- A response format.
- Approval steps.
- Expected validation commands.

Commands should remain visible and understandable.

They should not become hidden autonomous behavior that executes merely because
a repository contains a command definition.

A future command definition may describe:

- Its name.
- Its purpose.
- Required user input.
- The agent responsible for execution.
- The skill or workflow to apply.
- Read and write permissions.
- Commands that may be executed.
- Required confirmation points.
- Expected output.
- Completion criteria.

A command such as `review` may:

1. Select the Reviewer agent.
2. Load the code-review skill.
3. Allow read-only project access.
4. Inspect the current Git diff.
5. Read affected implementation and test files.
6. Return structured findings.
7. Avoid modifying the repository.

A command such as `fix-issue` may:

1. Ask the user for an issue description.
2. Select the Developer agent.
3. Inspect relevant project files.
4. Propose a plan.
5. Wait for approval.
6. Modify authorised files.
7. Run focused quality checks.
8. Request a review from another agent.

Commands should use the same application abstractions as manually created
agent sessions.

## MCP Configuration

Agent Workbench should support Model Context Protocol servers as one source of
external tools and context.

A project-level MCP configuration may eventually describe:

- Server identifiers.
- Local server commands.
- Remote server endpoints.
- Environment variable names.
- Enabled capabilities.
- Available tools.
- Agent access restrictions.
- Workspace restrictions.
- Confirmation requirements.
- Connection timeouts.

The proposed project file is:

- `.agent-workbench/mcp.toml`

The final format has not yet been implemented.

Its schema should be designed after the shared tool registry, permission model,
and tool execution loop are stable.

MCP configuration must not contain credentials directly.

Secrets should be supplied through:

- Environment variables.
- Operating-system credential stores.
- Dedicated secret-management systems.
- User-level private configuration.

A committed MCP configuration may reference the name of an environment
variable, but not its secret value.

## MCP Runtime Boundary

A future MCP integration may follow this application path:

1. Discover the project MCP configuration.
2. Validate every declared server.
3. Ask the user before starting unfamiliar local servers.
4. Connect through an MCP client manager.
5. Discover available tools and resources.
6. Convert supported capabilities into shared Agent Workbench definitions.
7. Apply agent and workspace permissions.
8. Register the approved capabilities.
9. Record invocations and results.

The provider adapter should not manage MCP servers directly.

Provider adapters should only translate shared model tool requests.

MCP connection management, permission checks, and capability execution belong
to the application and tool layers.

## MCP and Native Tools

MCP tools and native Agent Workbench tools should use the same shared
application boundary.

Native tools may include:

- `list_files`
- `read_file`
- `search_text`
- `search_symbols`
- `inspect_git_status`
- `inspect_git_diff`
- `run_quality_check`
- `run_tests`

MCP-provided tools may connect to:

- Git hosting services.
- Issue trackers.
- Documentation systems.
- Databases.
- Cloud services.
- Internal engineering platforms.
- External development tools.

The agent runtime should not need to know whether a capability is implemented
natively or supplied through MCP.

Every registered tool should still expose:

- A stable identifier.
- A description.
- An input schema.
- Permission requirements.
- An invocation record.
- A result.
- A clear error boundary.

The shared tool registry may eventually contain:

- Native workspace tools.
- Native Git tools.
- Native validation tools.
- MCP tools.
- User-defined approved tools.

## MCP Resources and Context

MCP may also provide resources that can be used as agent context.

Examples include:

- Issue descriptions.
- Project documentation.
- Repository metadata.
- Database schemas.
- API documentation.
- Build information.
- Deployment status.

External resources should not automatically be inserted into every model
request.

They should be selected according to:

- The active task.
- The agent's permissions.
- Explicit user selection.
- Retrieval relevance.
- Context-window limits.

The source of externally retrieved information should remain visible to the
user.

## MCP Security

MCP server definitions must be treated as untrusted project configuration.

Opening a repository must not automatically:

- Start arbitrary local processes.
- Connect to unknown remote services.
- expose environment variables.
- Grant tools to every agent.
- Permit network access.
- Execute discovered capabilities.
- Modify local files.

The user should be able to inspect:

- Which servers are declared.
- Which commands would be started.
- Which environment variables are referenced.
- Which tools are exposed.
- Which agents may use them.
- Which actions require confirmation.

Local MCP server commands should require explicit approval when the project is
not already trusted.

Tool results should be treated as external input and validated before being
used by the application.

## Relationship Between Tool Calling and MCP

Provider-independent tool calling must be implemented before MCP integration.

The intended dependency order is:

1. Shared tool definitions.
2. Shared model tool requests.
3. Tool invocation and result models.
4. Tool registry.
5. Permission checks.
6. Native read-only tools.
7. Stable execution loop.
8. MCP client integration.
9. MCP capability discovery.
10. MCP tools inside the shared registry.

MCP extends the available capabilities.

It does not replace the Agent Workbench tool abstraction, permission model, or
execution layer.

## Configuration Discovery

A future Agent Workbench runtime may search for project configuration from the
active workspace root.

The expected project directory is:

- `.agent-workbench/`

Configuration discovery should be explicit and predictable.

The runtime should not search arbitrary parent directories without clearly
defined workspace boundaries.

A possible discovery flow is:

1. Determine the active workspace root.
2. Look for `.agent-workbench/`.
3. Identify supported configuration files.
4. Validate file names, formats, and sizes.
5. Parse shared project configuration.
6. Parse approved local overrides.
7. Resolve the effective configuration.
8. Display warnings or errors.
9. Ask for approval before enabling executable capabilities.
10. Create agent sessions only after validation succeeds.

Invalid configuration should identify:

- The affected file.
- The invalid field.
- The expected format.
- The received value when safe to display.
- Whether the application can continue.
- Which capability has been disabled.

Automatic discovery must not automatically execute commands, start MCP
servers, grant write permissions, or connect to external services.

## Configuration Precedence

A possible future precedence model is:

1. Explicit session configuration.
2. Interactive user selections.
3. Local project overrides.
4. Shared project configuration.
5. User-level configuration.
6. Environment configuration.
7. Application defaults.

The final precedence rules should be defined only after the complete
configuration model exists.

Security settings require special handling.

A higher-precedence convenience setting must not silently weaken a mandatory
project or application safety restriction.

Examples of settings that should not be weakened without explicit approval
include:

- Workspace path restrictions.
- Read-only agent roles.
- Command confirmation requirements.
- Network-access restrictions.
- Secret-handling rules.
- Destructive-operation protection.
- MCP trust requirements.

Configuration resolution should make the effective value and its source
inspectable.

## User-Level Configuration

Agent Workbench may eventually support user-level configuration outside the
project repository.

User-level configuration may contain:

- Preferred providers.
- Preferred local models.
- Known MCP servers.
- Personal agent profiles.
- Personal skills.
- Default confirmation behavior.
- Trusted workspace records.
- UI preferences.
- Local cache locations.

User-level configuration must remain separate from project configuration.

A project should not be able to overwrite unrelated user preferences or access
private configuration without permission.

The exact user-level directory is not yet defined.

## Relationship to Current Features

Current Agent Workbench capabilities provide foundations for the proposed
project configuration model.

| Current capability | Future project capability |
| --- | --- |
| `--agent` | Built-in agent selection |
| `--agent-file` | Project agent discovery |
| `--context-file` | Explicit file attachments |
| `--system-prompt` | Session-specific instructions |
| `GenerationConfig` | Agent or command generation defaults |
| `JSONResponseFormat` | Reusable structured response definitions |
| `--setup` | Interactive project and session configuration |
| `ChatProvider` | Per-agent local or cloud provider selection |
| `RuntimeConfiguration` | Resolved agent-session configuration |

The future project configuration directory should reuse these abstractions
rather than replace them.

The current command-line arguments should remain available for explicit
overrides and automation.

## VS Code Integration

A future VS Code extension may discover the project configuration and display
its effective contents.

A possible project panel may include:

- Project instructions.
- Available agents.
- Available skills.
- Available commands.
- Active rules.
- Configured MCP servers.
- Available native tools.
- Tool permissions.
- Workspace restrictions.
- Local overrides.
- Configuration warnings.

The user should be able to inspect the effective configuration before starting
an agent session.

The interface may provide actions such as:

- Create an agent session.
- Select a project agent.
- Attach a file.
- Grant temporary workspace access.
- Enable a skill.
- Start a project command.
- Connect an approved MCP server.
- Review tool permissions.
- Compare shared and local settings.

The VS Code extension may offer editing assistance, validation, and navigation.

The underlying configuration should remain text-based and suitable for version
control.

The graphical interface must reuse the same configuration loaders and
validators as the CLI.

## Trust and Repository Onboarding

Opening an unfamiliar repository should place Agent Workbench in a restricted
state.

Before enabling project-defined behavior, the application should allow the
user to review:

- Project instructions.
- Declared agents.
- Rules and skills.
- Commands.
- MCP server definitions.
- Requested tools.
- Requested permissions.
- Referenced environment variables.
- Potential executable commands.

A future trust decision may be scoped to:

- The current session.
- The current repository.
- A specific configuration revision.
- A specific MCP server.
- A specific command or capability.

Changes to security-sensitive project configuration may invalidate previous
trust decisions.

## Security Requirements

Project configuration must be treated as untrusted input.

Future loaders and executors should protect against:

- Paths escaping the workspace root.
- Symlink-based path escape.
- Automatic command execution.
- Secret exposure.
- Unsafe MCP server startup.
- Permission escalation.
- Malformed configuration.
- Unsupported fields.
- Excessive file sizes.
- Hidden network access.
- Unapproved file modification.
- Destructive Git operations.
- Configuration-based prompt injection.
- Untrusted tool results.

Opening a repository must not automatically grant its agents, commands, skills,
or MCP servers permission to execute.

Read access, write access, command execution, network access, and Git
operations should be represented separately.

Security-sensitive actions should produce visible audit information.

## Validation Requirements

Every project configuration format should have:

- A documented schema.
- Explicit required fields.
- Explicit optional fields.
- Rejection of unsupported fields.
- Clear type validation.
- File-size limits.
- UTF-8 validation where applicable.
- Path-containment validation.
- Descriptive errors.
- Deterministic merging behavior.
- Automated tests.

Configuration validation should occur before an agent session begins.

Capabilities that cannot be validated safely should remain disabled.

## Implementation Order

Project configuration should be introduced incrementally.

The intended order is:

1. Provider-independent tool definitions.
2. Provider-independent tool invocation and result models.
3. Tool registry.
4. Permission model.
5. Safe read-only workspace tools.
6. Stable tool execution loop.
7. Agent session abstraction.
8. Project configuration discovery.
9. Shared and local settings.
10. Project agents and rules.
11. Skills and commands.
12. MCP client integration.
13. VS Code project configuration interface.

The `.agent-workbench/` structure should not be implemented as a large
configuration framework before the underlying runtime models are stable.

## Current Status

Project-local `.agent-workbench/config.toml` discovery and optional
`.agent-workbench/instructions.md` coding instructions are implemented. The
current application also supports command-line arguments, environment and
`.env` configuration, explicit profile and context files, and prompt-based
interactive setup.

The other proposed `.agent-workbench/` structures in this document—including
local overrides, project agents, rules, skills, commands, and MCP
configuration—remain future direction and must not be presented as current
functionality.

## Open Design Questions

The following decisions remain open:

- The final project configuration file format.
- Whether the primary settings format should be TOML or another format.
- How rules are selected for individual tasks.
- How skills reference tools and permissions.
- How commands reference agents and workflows.
- How shared and local configuration is merged.
- How trust decisions are persisted.
- How MCP server definitions are represented.
- How provider and model defaults interact with project agents.
- How user-level configuration is discovered.
- How configuration migrations are handled.
- Which configuration files may be generated through the VS Code interface.

These questions should be resolved through small implementation milestones and
tests rather than through a single speculative configuration schema.

## Related Documentation

- [Product Vision](product-vision.md)
- [Architecture](architecture.md)
- [Getting Started](getting-started.md)
- [Runtime Configuration](runtime-configuration.md)
- [Agent Profiles](agent-profiles.md)
- [Context Files](context-files.md)
- [Structured Outputs](structured-outputs.md)
- [Roadmap](roadmap.md)
