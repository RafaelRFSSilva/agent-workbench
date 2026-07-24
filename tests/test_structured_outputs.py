"""Tests for provider-independent structured output configuration."""

from typing import cast
import json
from pathlib import Path

import pytest

from agent_workbench.errors import ConfigurationError
from agent_workbench.structured_outputs import (
    MAX_RESPONSE_FORMAT_FILE_SIZE_BYTES,
    JSONResponseFormat,
    JSONSchema,
    load_response_format_file,
)


def create_valid_schema() -> JSONSchema:
    """Create a valid portable JSON object schema."""

    return {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
            },
            "risk_level": {
                "type": "string",
                "enum": [
                    "low",
                    "medium",
                    "high",
                ],
            },
        },
        "required": [
            "summary",
            "risk_level",
        ],
        "additionalProperties": False,
    }


def test_json_response_format_preserves_name_and_schema() -> None:
    """Preserve a valid response format definition."""

    schema = create_valid_schema()

    response_format = JSONResponseFormat(
        name="software_review",
        schema=schema,
    )

    assert response_format.name == "software_review"
    assert response_format.schema == schema


def test_json_response_format_normalizes_name() -> None:
    """Remove surrounding whitespace from the format name."""

    response_format = JSONResponseFormat(
        name="  software_review  ",
        schema=create_valid_schema(),
    )

    assert response_format.name == "software_review"


def test_equivalent_schemas_produce_equal_formats() -> None:
    """Compare schemas independently of dictionary key order."""

    first_format = JSONResponseFormat(
        name="result",
        schema={
            "type": "object",
            "properties": {
                "value": {
                    "type": "string",
                }
            },
        },
    )
    second_format = JSONResponseFormat(
        name="result",
        schema={
            "properties": {
                "value": {
                    "type": "string",
                }
            },
            "type": "object",
        },
    )

    assert first_format == second_format


def test_json_response_format_defensively_copies_schema() -> None:
    """Prevent external mutation of the stored schema."""

    schema = create_valid_schema()
    response_format = JSONResponseFormat(
        name="software_review",
        schema=schema,
    )

    schema["type"] = "array"

    returned_schema = response_format.schema
    returned_schema["type"] = "string"

    assert response_format.schema["type"] == "object"


@pytest.mark.parametrize(
    "name",
    [
        "",
        "   ",
        "software review",
        "software.review",
        "x" * 65,
    ],
)
def test_json_response_format_rejects_invalid_name(
    name,
) -> None:
    """Reject names outside the portable OpenAI-compatible format."""

    with pytest.raises(
        ConfigurationError,
        match="response format name must contain",
    ):
        JSONResponseFormat(
            name=name,
            schema=create_valid_schema(),
        )


@pytest.mark.parametrize(
    "schema",
    [
        {},
        {
            "type": "array",
        },
        {
            "properties": {},
        },
    ],
)
def test_json_response_format_rejects_invalid_root_schema(
    schema,
) -> None:
    """Require a non-empty top-level object schema."""

    with pytest.raises(ConfigurationError):
        JSONResponseFormat(
            name="result",
            schema=cast(JSONSchema, schema),
        )


def test_json_response_format_rejects_non_string_object_keys() -> None:
    """Reject object keys that are not valid JSON strings."""

    schema = cast(
        JSONSchema,
        {
            "type": "object",
            1: {
                "type": "string",
            },
        },
    )

    with pytest.raises(
        ConfigurationError,
        match="object keys must be strings",
    ):
        JSONResponseFormat(
            name="result",
            schema=schema,
        )


def test_json_response_format_rejects_non_json_values() -> None:
    """Reject schema values that cannot be serialized as JSON."""

    schema = cast(
        JSONSchema,
        {
            "type": "object",
            "invalid": object(),
        },
    )

    with pytest.raises(
        ConfigurationError,
        match="JSON-compatible values",
    ):
        JSONResponseFormat(
            name="result",
            schema=schema,
        )


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
    ],
)
def test_json_response_format_rejects_non_finite_numbers(
    value,
) -> None:
    """Reject numeric values that are invalid in strict JSON."""

    schema = cast(
        JSONSchema,
        {
            "type": "object",
            "invalid": value,
        },
    )

    with pytest.raises(
        ConfigurationError,
        match="finite JSON numbers",
    ):
        JSONResponseFormat(
            name="result",
            schema=schema,
        )


def write_response_format_file(
    path: Path,
    *,
    name: object = "software_review",
    schema: object | None = None,
    additional_fields: dict[str, object] | None = None,
) -> None:
    """Write a response format definition for loader tests."""

    response_format = {
        "name": name,
        "schema": schema if schema is not None else create_valid_schema(),
    }

    if additional_fields is not None:
        response_format.update(additional_fields)

    path.write_text(
        json.dumps(response_format),
        encoding="utf-8",
    )


def test_load_response_format_file_returns_valid_format(
    tmp_path,
) -> None:
    """Load a valid response format from a JSON file."""

    format_path = tmp_path / "software-review.json"
    write_response_format_file(format_path)

    response_format = load_response_format_file(format_path)

    assert response_format.name == "software_review"
    assert response_format.schema == create_valid_schema()


def test_load_response_format_file_rejects_missing_file(
    tmp_path,
) -> None:
    """Reject a response format path that does not exist."""

    missing_path = tmp_path / "missing.json"

    with pytest.raises(
        ConfigurationError,
        match="Response format file does not exist",
    ):
        load_response_format_file(missing_path)


def test_load_response_format_file_rejects_directory(
    tmp_path,
) -> None:
    """Reject a response format path that refers to a directory."""

    directory_path = tmp_path / "schema.json"
    directory_path.mkdir()

    with pytest.raises(
        ConfigurationError,
        match="Response format path is not a file",
    ):
        load_response_format_file(directory_path)


def test_load_response_format_file_rejects_unsupported_extension(
    tmp_path,
) -> None:
    """Require response format files to use the JSON extension."""

    format_path = tmp_path / "software-review.yaml"
    write_response_format_file(format_path)

    with pytest.raises(
        ConfigurationError,
        match="must use the '.json' extension",
    ):
        load_response_format_file(format_path)


def test_load_response_format_file_rejects_oversized_file(
    tmp_path,
) -> None:
    """Reject response format files above the configured size limit."""

    format_path = tmp_path / "oversized.json"
    format_path.write_bytes(b"x" * (MAX_RESPONSE_FORMAT_FILE_SIZE_BYTES + 1))

    with pytest.raises(
        ConfigurationError,
        match="Response format file exceeds",
    ):
        load_response_format_file(format_path)


def test_load_response_format_file_rejects_invalid_utf8(
    tmp_path,
) -> None:
    """Reject response format files that are not valid UTF-8."""

    format_path = tmp_path / "invalid.json"
    format_path.write_bytes(b"\xff")

    with pytest.raises(
        ConfigurationError,
        match="not valid UTF-8",
    ):
        load_response_format_file(format_path)


def test_load_response_format_file_rejects_empty_file(
    tmp_path,
) -> None:
    """Reject empty and whitespace-only response format files."""

    format_path = tmp_path / "empty.json"
    format_path.write_text(
        "   \n",
        encoding="utf-8",
    )

    with pytest.raises(
        ConfigurationError,
        match="Response format file is empty",
    ):
        load_response_format_file(format_path)


def test_load_response_format_file_rejects_invalid_json(
    tmp_path,
) -> None:
    """Reject malformed JSON response format files."""

    format_path = tmp_path / "invalid.json"
    format_path.write_text(
        '{"name": "result",',
        encoding="utf-8",
    )

    with pytest.raises(
        ConfigurationError,
        match="contains invalid JSON",
    ):
        load_response_format_file(format_path)


def test_load_response_format_file_rejects_non_object_root(
    tmp_path,
) -> None:
    """Require the response format file root to be a JSON object."""

    format_path = tmp_path / "list.json"
    format_path.write_text(
        "[]",
        encoding="utf-8",
    )

    with pytest.raises(
        ConfigurationError,
        match="root must be a JSON object",
    ):
        load_response_format_file(format_path)


@pytest.mark.parametrize(
    "missing_field",
    [
        "name",
        "schema",
    ],
)
def test_load_response_format_file_rejects_missing_fields(
    tmp_path,
    missing_field,
) -> None:
    """Require both the name and schema fields."""

    format_path = tmp_path / "missing-field.json"
    content = {
        "name": "software_review",
        "schema": create_valid_schema(),
    }
    del content[missing_field]

    format_path.write_text(
        json.dumps(content),
        encoding="utf-8",
    )

    with pytest.raises(
        ConfigurationError,
        match=f"missing required field '{missing_field}'",
    ):
        load_response_format_file(format_path)


def test_load_response_format_file_rejects_unsupported_fields(
    tmp_path,
) -> None:
    """Reject fields outside the portable response format definition."""

    format_path = tmp_path / "unsupported-field.json"
    write_response_format_file(
        format_path,
        additional_fields={
            "strict": True,
        },
    )

    with pytest.raises(
        ConfigurationError,
        match="unsupported fields: strict",
    ):
        load_response_format_file(format_path)


def test_load_response_format_file_rejects_non_string_name(
    tmp_path,
) -> None:
    """Require the response format name to be a string."""

    format_path = tmp_path / "invalid-name.json"
    write_response_format_file(
        format_path,
        name=123,
    )

    with pytest.raises(
        ConfigurationError,
        match="field 'name' must be a string",
    ):
        load_response_format_file(format_path)


def test_load_response_format_file_rejects_non_object_schema(
    tmp_path,
) -> None:
    """Require the response format schema to be a JSON object."""

    format_path = tmp_path / "invalid-schema.json"
    write_response_format_file(
        format_path,
        schema=[],
    )

    with pytest.raises(
        ConfigurationError,
        match="field 'schema' must be a JSON object",
    ):
        load_response_format_file(format_path)
