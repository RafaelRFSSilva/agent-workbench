"""Provider-independent structured output configuration."""

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from agent_workbench.errors import ConfigurationError

MAX_RESPONSE_FORMAT_FILE_SIZE_BYTES = 100 * 1024

_SUPPORTED_RESPONSE_FORMAT_FIELDS = frozenset(
    {
        "name",
        "schema",
    }
)

type JSONValue = (
    None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]
)
type JSONSchema = dict[str, JSONValue]

_RESPONSE_FORMAT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True, slots=True, init=False)
class JSONResponseFormat:
    """Represent a strict provider-independent JSON response format."""

    name: str
    _schema_json: str = field(repr=False)

    def __init__(
        self,
        name: str,
        schema: JSONSchema,
    ) -> None:
        """Validate and store an immutable JSON response format."""

        normalized_name = name.strip()

        if not _RESPONSE_FORMAT_NAME_PATTERN.fullmatch(normalized_name):
            raise ConfigurationError(
                "response format name must contain 1 to 64 letters, "
                "numbers, underscores, or hyphens."
            )

        if not isinstance(schema, dict) or not schema:
            raise ConfigurationError(
                "response format schema must be a non-empty JSON object."
            )

        if schema.get("type") != "object":
            raise ConfigurationError(
                "response format schema must use 'object' as its top-level type."
            )

        _validate_json_value(schema)

        schema_json = json.dumps(
            schema,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

        object.__setattr__(
            self,
            "name",
            normalized_name,
        )
        object.__setattr__(
            self,
            "_schema_json",
            schema_json,
        )

    @property
    def schema(self) -> JSONSchema:
        """Return an independent copy of the JSON schema."""

        return cast(
            JSONSchema,
            json.loads(self._schema_json),
        )


def _validate_json_value(value: object) -> None:
    """Reject values that cannot be represented safely as JSON."""

    if value is None or isinstance(
        value,
        (bool, int, str),
    ):
        return

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ConfigurationError(
                "response format schema must contain only finite JSON numbers."
            )

        return

    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)

        return

    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ConfigurationError(
                    "response format schema object keys must be strings."
                )

            _validate_json_value(item)

        return

    raise ConfigurationError(
        "response format schema must contain only JSON-compatible values."
    )


def load_response_format_file(path: Path) -> JSONResponseFormat:
    """Load and validate a provider-independent JSON response format file."""

    source_path = path.expanduser()

    if not source_path.exists():
        raise ConfigurationError(f"Response format file does not exist: {source_path}")

    if not source_path.is_file():
        raise ConfigurationError(f"Response format path is not a file: {source_path}")

    if source_path.suffix.lower() != ".json":
        raise ConfigurationError(
            f"Response format file must use the '.json' extension: {source_path}"
        )

    try:
        file_size = source_path.stat().st_size
    except OSError as exc:
        raise ConfigurationError(
            f"Unable to inspect response format file: {source_path}"
        ) from exc

    if file_size > MAX_RESPONSE_FORMAT_FILE_SIZE_BYTES:
        raise ConfigurationError(
            "Response format file exceeds the "
            f"{MAX_RESPONSE_FORMAT_FILE_SIZE_BYTES}-byte limit: "
            f"{source_path}"
        )

    try:
        content = source_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigurationError(
            f"Response format file is not valid UTF-8: {source_path}"
        ) from exc
    except OSError as exc:
        raise ConfigurationError(
            f"Unable to read response format file: {source_path}"
        ) from exc

    if not content.strip():
        raise ConfigurationError(f"Response format file is empty: {source_path}")

    try:
        parsed_content = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            "Response format file contains invalid JSON at "
            f"line {exc.lineno}, column {exc.colno}: {source_path}"
        ) from exc

    if not isinstance(parsed_content, dict):
        raise ConfigurationError("Response format file root must be a JSON object.")

    unsupported_fields = set(parsed_content) - _SUPPORTED_RESPONSE_FORMAT_FIELDS

    if unsupported_fields:
        formatted_fields = ", ".join(sorted(unsupported_fields))
        raise ConfigurationError(
            f"Response format file contains unsupported fields: {formatted_fields}."
        )

    for required_field in sorted(_SUPPORTED_RESPONSE_FORMAT_FIELDS):
        if required_field not in parsed_content:
            raise ConfigurationError(
                f"Response format file is missing required field '{required_field}'."
            )

    name = parsed_content["name"]
    schema = parsed_content["schema"]

    if not isinstance(name, str):
        raise ConfigurationError("Response format file field 'name' must be a string.")

    if not isinstance(schema, dict):
        raise ConfigurationError(
            "Response format file field 'schema' must be a JSON object."
        )

    return JSONResponseFormat(
        name=name,
        schema=cast(JSONSchema, schema),
    )
