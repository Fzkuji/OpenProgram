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


class StructuredOutputUnsupportedError(StructuredOutputError):
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

    if not isinstance(output.schema, dict):
        raise StructuredOutputSchemaError("schema must be an object", code="invalid_schema")
    if not isinstance(output.name, str) or not _NAME_RE.fullmatch(output.name):
        raise StructuredOutputSchemaError("Invalid structured output name", code="invalid_schema")
    if output.description is not None and not isinstance(output.description, str):
        raise StructuredOutputSchemaError("description must be a string", code="invalid_schema")
    if not isinstance(output.strict, bool):
        raise StructuredOutputSchemaError("strict must be a boolean", code="invalid_schema")
    if output.fallback not in ("auto", "none", "prompt"):
        raise StructuredOutputSchemaError("Invalid structured output fallback", code="invalid_schema")
    if type(output.max_validation_retries) is not int or output.max_validation_retries not in (0, 1):
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


def build_repair_prompt(error: StructuredOutputValidationError) -> str:
    """Return a bounded, deterministic instruction for one semantic retry."""
    issues = json.dumps(error.issues, ensure_ascii=False, separators=(",", ":"))
    return (
        "Your previous response did not satisfy the requested JSON Schema. "
        "Return only one complete JSON value that satisfies the same schema. "
        f"Failure code: {error.code}. Issues: {issues}"
    )[:3999]


def negotiate_structured_output(model: Any, output: JsonSchemaOutput) -> Literal["native", "prompt"]:
    """Choose only modes backed by a request-contract implementation."""
    provider = getattr(model, "provider", "")
    api = getattr(model, "api", "")
    declared = getattr(model, "structured_output", None)
    if provider == "callable":
        return "native"
    native = (
        (provider == "openai" and api in {"openai-completions", "openai-responses"})
        or (provider == "anthropic" and api == "anthropic-messages")
    )
    if native:
        if declared is not False:
            return "native"
    if output.fallback == "prompt":
        return "prompt"
    raise StructuredOutputUnsupportedError(
        f"Structured output is not verified for provider={provider!r}, api={api!r}",
        code="unsupported",
    )


def build_prompt_fallback(output: JsonSchemaOutput) -> str:
    schema = json.dumps(output.schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (
        "Return only one complete JSON value matching this JSON Schema. "
        "Do not use Markdown fences or add explanatory text. JSON Schema: " + schema
    )
