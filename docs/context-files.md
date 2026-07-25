# Context Files

## Overview

Agent Workbench allows users to attach local text files as explicit reference
context for an active conversation.

Context files are supplied through the repeatable `--context-file` command-line
argument or through the prompt-based interactive setup.

Example:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent reviewer \
  --context-file README.md \
  --context-file pyproject.toml
```

Each selected file is validated, loaded, and represented independently from
conversation history.

The current implementation sends the complete contents of every selected file
with each model request.

It does not yet provide project-wide file discovery, on-demand workspace
tools, indexing, embeddings, or Retrieval-Augmented Generation.

## Supported File Types

The current loader supports:

- `.txt`
- `.md`
- `.py`
- `.toml`
- `.json`
- `.yaml`
- `.yml`

These formats are treated as UTF-8 text.

Binary files are not supported.

## File Requirements

Each context file must:

- Exist.
- Refer to a regular file rather than a directory.
- Use a supported extension.
- Contain valid UTF-8 text.
- Contain non-whitespace content.
- Not exceed 100 KiB.

Validation occurs before the provider session starts.

An invalid file prevents direct runtime configuration from completing.

During interactive setup, an invalid file is reported and the user may enter
another path.

## Command-Line Usage

Attach one file:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --context-file README.md
```

Attach multiple files by repeating the argument:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --context-file README.md \
  --context-file pyproject.toml \
  --context-file src/agent_workbench/cli.py
```

The input order is preserved.

## Interactive Setup

Start the setup flow:

```bash
uv run agent-workbench --setup
```

The context step displays:

```text
Context files:
Enter one file path at a time. Press Enter when finished.
Context file [done]: README.md
Added context file: README.md
Context file [done]: pyproject.toml
Added context file: pyproject.toml
Context file [done]:
```

Press Enter without a path to finish context selection.

Each path is validated immediately.

An invalid file produces an error and repeats only the context-file question.

The complete setup does not restart.

## ContextDocument

A loaded file is represented through the provider-independent
`ContextDocument` abstraction.

Conceptually:

```text
ContextDocument
├── source
└── content
```

- `source` identifies the original file path.
- `content` contains the validated UTF-8 text.

The representation does not contain provider-specific request fields.

## Runtime Pipeline

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
        ↓
Provider Adapter
```

The direct CLI and prompt-based setup both use the same context loader.

This avoids separate validation behavior between configuration interfaces.

## Separation from Conversation History

Context documents remain separate from user and assistant messages.

```text
ChatRequest
├── system_prompt
├── context_documents
└── messages
    ├── user
    └── assistant
```

A context file is not converted into a synthetic user message.

This distinction allows provider adapters to place reference material into the
most appropriate native instruction or system field.

Conversation history contains only the actual interactive user and assistant
turns.

## Provider Translation

Each provider receives the shared context through its adapter.

Conceptually:

```text
Context Documents
├── Ollama
│   └── system message content
├── OpenAI
│   └── instructions content
└── Anthropic
    └── system parameter content
```

The active system prompt remains first.

Context documents are then added as clearly identified reference material.

Provider-specific formatting remains isolated inside each adapter.

## Context Delimiting

Every context document should remain visibly separated from other instructions
and files.

Conceptually:

```text
System Instructions

Reference Documents

--- Document: README.md ---
<document contents>
--- End Document ---

--- Document: pyproject.toml ---
<document contents>
--- End Document ---
```

Clear delimiters reduce ambiguity between project content and agent
instructions.

They do not eliminate prompt-injection risk.

Project files must still be treated as untrusted input.

## File Size Limit

Each context file is limited to 100 KiB.

The limit prevents unexpectedly large files from being loaded into every model
request.

It does not guarantee that the combined documents fit within the selected
model's context window.

Several valid files may still produce excessive total context.

Agent Workbench does not currently estimate token usage before the provider
request.

## Complete-File Delivery

The current implementation sends each selected file in full.

This approach is simple and deterministic for small files.

It is useful for:

- Reviewing a focused source file.
- Supplying project configuration.
- Providing a short specification.
- Comparing a small number of documents.
- Validating the provider-independent context pipeline.

It is not efficient for large repositories.

## Context Window Considerations

The model request includes multiple sources of content:

```text
Context Window Usage
├── System prompt
├── Agent instructions
├── Context documents
├── Conversation history
├── Current user message
└── Model response budget
```

Large context documents reduce the remaining space available for conversation
history and model output.

Different providers and models support different context-window limits.

Agent Workbench does not currently:

- Count tokens.
- Reserve a guaranteed output budget.
- Truncate context documents.
- Select relevant chunks.
- Summarise oversized context.
- Warn before a provider context-limit error.

These capabilities belong to future context-management and retrieval
milestones.

## Security

Context files must be treated as untrusted text.

A file may contain instructions intended to influence the model.

The runtime should preserve the distinction between:

- Trusted system instructions.
- Agent profile instructions.
- Project reference material.
- User prompts.

Current protections include:

- Supported-extension validation.
- Regular-file validation.
- UTF-8 validation.
- Non-empty-content validation.
- Per-file size limits.
- Clear source identification.
- Clear document delimiters.
- Loading before provider construction.

Future workspace access must add stronger path and permission controls.

## Path Safety

The current explicit path workflow loads only paths supplied by the user.

Future workspace discovery and tools must protect against:

- Paths outside the workspace root.
- Symlink-based path escape.
- Hidden sensitive files.
- Secret files.
- Generated dependency directories.
- Binary files.
- Excessively large files.
- Unapproved directories.

Workspace access should be permission-aware rather than automatically exposing
the complete repository.

## Current Use Cases

### Review a Source File

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent reviewer \
  --context-file src/agent_workbench/providers/openai.py
```

### Review Implementation and Tests

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent reviewer \
  --context-file src/agent_workbench/structured_outputs.py \
  --context-file tests/test_structured_outputs.py
```

### Plan from Documentation

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent planner \
  --context-file README.md \
  --context-file docs/product-vision.md
```

### Use Context with Structured Output

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent reviewer \
  --context-file src/agent_workbench/cli.py \
  --temperature 0.0 \
  --max-output-tokens 512 \
  --response-format-file ./software-review.json
```

## Relationship to Future Attachments

The explicit `--context-file` argument is the first attachment mechanism.

A future terminal or VS Code interface should allow the user to attach files
without typing paths manually.

Possible interactions include:

- Select a file from the editor.
- Drag a file into an agent session.
- Use an “Attach current file” action.
- Attach the current selection.
- Attach the current Git diff.
- Attach several files from the workspace explorer.
- Remove an attachment before sending.

The underlying runtime can continue converting approved attachments into
provider-independent context objects.

## On-Demand Workspace Tools

Explicit attachments are different from agent-controlled file access.

A future authorised agent may use tools such as:

- `list_files`
- `read_file`
- `search_text`
- `search_symbols`
- `inspect_git_status`
- `inspect_git_diff`

This allows the agent to retrieve relevant files only when needed.

The complete project should not be inserted into every model request.

Tool access must be controlled by workspace permissions.

## Project-Wide Requests

A user may ask an agent to analyse the complete project.

That request should not cause every project file to be concatenated into one
prompt.

A future workflow may combine:

```text
Project Request
        ↓
Workspace Structure Inspection
        ↓
Text and Symbol Search
        ↓
Relevant File Reads
        ↓
Optional Semantic Retrieval
        ↓
Focused Model Context
```

This approach is more scalable and easier to audit.

## Retrieval-Augmented Generation

For larger repositories, Agent Workbench should support local project indexing
and retrieval.

A future RAG pipeline may include:

```text
Project Files
        ↓
Filtering
        ↓
Parsing
        ↓
Chunking
        ↓
Embeddings
        ↓
Local Vector Store
        ↓
Relevant Chunks
        ↓
Agent Session
```

Retrieval should complement direct file tools.

- File tools provide exact source access.
- Search locates known text and symbols.
- Semantic retrieval discovers conceptually relevant sections.
- Explicit attachments preserve direct user control.

## Context Sources

Future agent context may come from several sources:

```text
Agent Context
├── Explicit file attachments
├── On-demand workspace reads
├── Git status and diffs
├── Retrieved project chunks
├── MCP resources
├── Task descriptions
└── Results from other agents
```

Every source should remain identifiable.

The user should be able to understand where important information originated.

## Context Selection

Future context management should consider:

- Relevance to the active task.
- Agent permissions.
- Source trust.
- Context-window limits.
- Recency.
- File version.
- Duplicate content.
- Required output budget.
- Provider and model limits.

Context selection should be observable rather than hidden.

## Multi-Agent Context

Multiple agents should not automatically share all conversation and project
context.

A future orchestrator may pass:

- An approved task.
- A plan.
- A focused set of files.
- A structured result.
- A Git diff.
- Test failures.
- Review findings.

This is safer and more efficient than copying complete conversation histories
between agents.

## Current Limitations

The current context implementation does not support:

- Binary files.
- Images.
- PDFs.
- Directory arguments.
- Glob patterns.
- Workspace discovery.
- File removal during setup.
- Duplicate-path detection.
- Token estimation.
- Combined-size limits.
- Automatic truncation.
- Chunking.
- Embeddings.
- Vector databases.
- Semantic retrieval.
- On-demand file tools.
- Git diff attachments.
- Editor selections.
- Persistent attachments.
- Context changes during a session.
- Per-agent workspace permissions.

## Related Documentation

- [Getting Started](getting-started.md)
- [Runtime Configuration](runtime-configuration.md)
- [Architecture](architecture.md)
- [Agent Profiles](agent-profiles.md)
- [Structured Outputs](structured-outputs.md)
- [Project Configuration](project-configuration.md)
- [Product Vision](product-vision.md)
- [Roadmap](roadmap.md)
