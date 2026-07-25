# Structured Outputs

## Overview

Agent Workbench supports provider-independent structured outputs through
`JSONResponseFormat`.

A structured output asks the selected model to return JSON matching a supplied
JSON Schema instead of unrestricted text.

The same shared response format can be used with:

- Ollama.
- OpenAI through the Responses API.
- Anthropic through the Messages API.

Provider-specific request fields remain inside each provider adapter.

## Quick Start

Create `software-review.json`:

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

Start a session:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --response-format-file ./software-review.json
```

## File Structure

A response format file contains exactly:

```text
Response Format File
├── name
└── schema
```

- `name` identifies the response format.
- `schema` defines the required JSON object.

Unsupported top-level fields are rejected.

## Name Validation

The `name` field:

- Must be a string.
- Must contain between 1 and 64 characters.
- May contain letters, numbers, underscores, and hyphens.
- Must not contain spaces, dots, or unsupported punctuation.
- Is normalised by removing surrounding whitespace.

Valid examples:

```text
software_review
code-review
review_v2
```

Invalid examples:

```text
software review
software.review
review/result
```

## Schema Validation

The `schema` field:

- Must be a JSON object.
- Must not be empty.
- Must use `object` as its top-level type.
- Must contain only JSON-compatible values.
- Must use strings for object keys.
- Must not contain `NaN` or infinite numbers.

Example:

```json
{
  "type": "object",
  "properties": {
    "answer": {
      "type": "string"
    }
  },
  "required": [
    "answer"
  ],
  "additionalProperties": false
}
```

The current implementation validates the response-format envelope and strict
JSON compatibility.

It does not perform complete JSON Schema specification validation.

## File Requirements

Response format files:

- Must exist.
- Must refer to a regular file.
- Must use the `.json` extension.
- Must not exceed 100 KiB.
- Must contain valid UTF-8.
- Must not be empty.
- Must contain valid JSON.
- Must use an object as the file root.
- Must contain `name` and `schema`.
- Must not contain unsupported root fields.

Malformed JSON errors identify the source line and column.

Validation occurs before provider construction.

## Runtime Model

The validated configuration becomes:

```text
JSONResponseFormat
├── name
└── schema
```

It is attached to the shared request:

```text
ChatRequest
├── messages
├── system_prompt
├── context_documents
├── generation_config
└── response_format
```

When `response_format` is absent, providers preserve normal text behavior.

## Runtime Pipeline

```text
--response-format-file
        ↓
Response Format Loader
        ↓
JSONResponseFormat
        ↓
RuntimeConfiguration.response_format
        ↓
ChatRequest.response_format
        ↓
Provider Adapter
```

Structured output configuration remains separate from:

- Conversation history.
- Agent instructions.
- Context documents.
- Generation settings.

## Defensive Immutability

`JSONResponseFormat` is immutable.

Because a frozen dataclass does not make nested dictionaries immutable, the
validated schema is stored internally as canonical JSON.

```text
Input Dictionary
        ↓
Validation
        ↓
Canonical JSON Storage
        ↓
Independent Dictionary Copy on Access
```

This prevents mutation of the original dictionary or a returned schema copy
from changing the active response format.

Equivalent schemas compare equally even when their original key order differs.

## Provider Translation

Each adapter translates the shared response format:

```text
JSONResponseFormat
├── Ollama
│   └── format = schema
│
├── OpenAI Responses API
│   └── text.format
│       ├── type = "json_schema"
│       ├── name
│       ├── schema
│       └── strict = true
│
└── Anthropic Messages API
    └── output_config.format
        ├── type = "json_schema"
        └── schema
```

The portable `name` is required by OpenAI.

Ollama and Anthropic receive the schema without the shared name.

## Ollama Example

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --response-format-file ./software-review.json
```

Schema support may still vary between Ollama models.

## OpenAI Example

```bash
uv run agent-workbench \
  --provider openai \
  --model <openai-model> \
  --response-format-file ./software-review.json
```

The adapter enables strict JSON Schema behavior through `text.format`.

## Anthropic Example

```bash
uv run agent-workbench \
  --provider anthropic \
  --model <anthropic-model> \
  --response-format-file ./software-review.json
```

The adapter sends the schema through `output_config.format`.

## Interactive Setup

Structured output can also be selected through:

```bash
uv run agent-workbench --setup
```

The setup displays:

```text
Structured output:
Press Enter to use the normal unstructured text response.
Response format file [none]: ./software-review.json
Loaded response format: software_review
```

Press Enter to skip structured output.

Invalid files repeat only the response-format question.

`--setup` cannot be combined directly with `--response-format-file`.

## Generation Configuration

Structured output can be combined with portable generation settings:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --temperature 0.0 \
  --top-p 1.0 \
  --max-output-tokens 256 \
  --response-format-file ./software-review.json
```

A sufficient output budget is still required.

Very small output limits may prevent a reasoning model from producing the final
JSON response.

## Agent Profiles

Agent profiles and response formats have separate responsibilities.

```text
Agent Profile
└── Role and behaviour

Response Format
└── Expected output structure
```

Example:

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent reviewer \
  --response-format-file ./software-review.json
```

## Context Files

Context files provide reference material while the response format controls the
result structure.

```bash
uv run agent-workbench \
  --provider ollama \
  --model gpt-oss:20b \
  --agent reviewer \
  --context-file src/agent_workbench/providers/openai.py \
  --response-format-file ./software-review.json
```

## Real Validation

A direct Ollama validation returned:

```json
{"risk_level":"low","summary":"Structured output works."}
```

An interactive setup validation returned:

```json
{"risk_level":"medium","summary":"Setup structured output works."}
```

Both responses contained the required properties and respected the configured
enum.

JSON object property order is not significant.

## Response Handling

Provider responses remain strings.

Example:

```python
response = '{"risk_level":"low","summary":"Structured output works."}'
```

Agent Workbench does not currently:

- Deserialize JSON automatically.
- Return typed Python objects.
- Validate generated JSON locally against the schema.
- Repair invalid output.
- Retry automatically.

Applications that need Python objects must parse the returned string.

## Validation Errors

### Missing File

A missing path is rejected before provider construction.

### Unsupported Extension

Response format definitions must currently use `.json`.

### Invalid Root

This is invalid:

```json
[
  {
    "name": "software_review"
  }
]
```

The file root must be an object.

### Missing Required Fields

These are invalid:

```json
{
  "schema": {
    "type": "object"
  }
}
```

```json
{
  "name": "software_review"
}
```

### Unsupported Root Field

This is invalid:

```json
{
  "name": "software_review",
  "schema": {
    "type": "object"
  },
  "description": "Unsupported field"
}
```

### Invalid Top-Level Schema Type

This is invalid:

```json
{
  "name": "string_result",
  "schema": {
    "type": "string"
  }
}
```

The current implementation requires a top-level object schema.

## Security

A response format file is untrusted configuration input.

Current protections include:

- Path and file validation.
- Extension validation.
- Size limits.
- UTF-8 validation.
- JSON syntax validation.
- Required-field validation.
- Unsupported-field rejection.
- Strict JSON value validation.
- Defensive schema storage.

A response format does not grant:

- Filesystem access.
- Tool access.
- Command execution.
- Network access.
- Provider credentials.
- MCP access.

Future project-discovered schemas must still pass the same validation.

## Future Uses

Structured outputs provide a foundation for:

- Tool invocation arguments.
- Planner task lists.
- Reviewer findings.
- Tester reports.
- Agent handoffs.
- Orchestrator state.
- Evaluation records.
- VS Code interface data.

Example future reviewer result:

```json
{
  "summary": "Two issues found.",
  "findings": [
    {
      "severity": "high",
      "file": "src/example.py",
      "message": "Missing input validation."
    }
  ]
}
```

Structured communication avoids relying on parsing arbitrary prose between
agents and application components.

## Current Limitations

The current implementation does not support:

- Complete JSON Schema validation.
- Provider-specific schema subset detection.
- Model compatibility detection before the request.
- Top-level array or primitive schemas.
- Inline schema command-line input.
- Multiple active response formats.
- Changing formats during a session.
- Automatic JSON deserialization.
- Local post-generation validation.
- Automatic repair or retry.
- Pydantic model input.
- Python dataclass conversion.
- Schema generation from Python types.
- Persistent format selections.
- A terminal or VS Code schema editor.

## Related Documentation

- [Getting Started](getting-started.md)
- [Runtime Configuration](runtime-configuration.md)
- [Architecture](architecture.md)
- [Agent Profiles](agent-profiles.md)
- [Context Files](context-files.md)
- [Project Configuration](project-configuration.md)
- [Product Vision](product-vision.md)
- [Roadmap](roadmap.md)
