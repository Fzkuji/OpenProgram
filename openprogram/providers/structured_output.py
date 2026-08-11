"""Strict JSON Schema output normalization and local validation."""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from jsonschema import SchemaError, validators


_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class JsonSchemaOutput:
    schema: dict[str, Any]
    name: str = "response"
    description: str | None = None
    strict: bool = True
    fallback: Literal["auto", "none", "prompt"] = "auto"
    max_validation_retries: Literal[0, 1] = 1
    type: Literal["json_schema"] = "json_schema"


class StructuredOutputError(ValueError):
    def __init__(self, message: str, *, code: str, issues: list[dict[str, str]] | None = None):
        super().__init__(message)
        self.code = code
        self.issues = issues or []


class StructuredOutputSchemaError(StructuredOutputError):
    pass


class StructuredOutputValidationError(StructuredOutputError):
    pass


def normalize_response_format(value: dict[str, Any] | JsonSchemaOutput) -> JsonSchemaOutput:
    """Copy, normalize, and meta-validate a public response-format value."""
    if isinstance(value, JsonSchemaOutput):
        output = JsonSchemaOutput(**{**value.__dict__, "schema": copy.deepcopy(value.schema)})
    elif isinstance(value, dict):
        raw = copy.deepcopy(value)
        if raw.get("type") == "json_schema":
            allowed = {
                "type", "schema", "name", "description", "strict", "fallback",
                "max_validation_retries",
            }
            unknown = sorted(set(raw) - allowed)
            if unknown:
                raise StructuredOutputSchemaError(
                    f"Unknown response_format field: {unknown[0]}", code="invalid_schema"
                )
            schema = raw.pop("schema", None)
            raw.pop("type", None)
            if not isinstance(schema, dict):
                raise StructuredOutputSchemaError(
                    "response_format.schema must be an object", code="invalid_schema"
                )
            try:
                output = JsonSchemaOutput(schema=schema, **raw)
            except (TypeError, ValueError) as exc:
                raise StructuredOutputSchemaError(str(exc), code="invalid_schema") from exc
        else:
            output = JsonSchemaOutput(schema=raw)
    else:
        raise StructuredOutputSchemaError(
            "response_format must be a JSON Schema object or JsonSchemaOutput",
            code="invalid_schema",
        )

    if not _NAME_RE.fullmatch(output.name):
        raise StructuredOutputSchemaError("Invalid structured output name", code="invalid_schema")
    if output.fallback not in ("auto", "none", "prompt"):
        raise StructuredOutputSchemaError("Invalid structured output fallback", code="invalid_schema")
    if output.max_validation_retries not in (0, 1):
        raise StructuredOutputSchemaError(
            "max_validation_retries must be 0 or 1", code="invalid_schema"
        )
    try:
        validator_cls = validators.validator_for(output.schema)
        validator_cls.check_schema(output.schema)
    except SchemaError as exc:
        raise StructuredOutputSchemaError(
            f"Invalid JSON Schema: {exc.message}", code="invalid_schema"
        ) from exc
    return output


def _reject_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON number: {value}")


def _pointer(parts) -> str:
    return "".join(f"/{str(part).replace('~', '~0').replace('/', '~1')}" for part in parts)


def parse_and_validate_json(raw: str, output: JsonSchemaOutput) -> Any:
    """Parse one complete JSON value and validate it against the original schema."""
    try:
        value = json.loads(raw, parse_constant=_reject_constant)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StructuredOutputValidationError(
            "Structured output is not valid JSON", code="invalid_json"
        ) from exc

    validator = validators.validator_for(output.schema)(output.schema)
    errors = sorted(
        validator.iter_errors(value),
        key=lambda error: (
            tuple(str(part) for part in error.absolute_path),
            tuple(str(part) for part in error.absolute_schema_path),
            error.message,
        ),
    )
    if errors:
        issues = [
            {
                "code": "schema_violation",
                "path": _pointer(error.absolute_path),
                "schema_path": _pointer(error.absolute_schema_path),
                "message": error.message[:500],
            }
            for error in errors[:20]
        ]
        raise StructuredOutputValidationError(
            "Structured output failed JSON Schema validation",
            code="validation_failed",
            issues=issues,
        )
    return value
