"""Strict JSON Schema output normalization and local validation."""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from jsonschema import SchemaError, validators


_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
HIDDEN_SUBMIT_TOOL_NAME = "__openprogram_submit_json"


@dataclass(frozen=True)
class JsonSchemaOutput:
    schema: dict[str, Any]
    name: str = "response"
    description: str | None = None
    strict: bool = True
    fallback: Literal["auto", "none", "prompt"] = "auto"
    max_validation_retries: Literal[0, 1] = 1
    type: Literal["json_schema"] = "json_schema"


@dataclass(frozen=True)
class StructuredOutputCapabilities:
    native: Literal["supported", "unsupported", "unknown"] = "unknown"
    dialect: str | None = None
    streaming: bool = True
    with_tools: bool = False
    strict_tool: bool = False
    schema_profile: str = "none"
    native_model_opt_in: bool = False


@dataclass(frozen=True)
class StructuredOutputPlan:
    mode: Literal["native", "tool", "prompt"]
    original_schema: dict[str, Any]
    provider_schema: dict[str, Any]
    submit_tool_name: str | None = None


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


def _project_schema(
    schema: dict[str, Any],
    profile: str,
) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    if profile in {"none", "passthrough"}:
        return copy.deepcopy(schema), None
    if profile == "google_json_schema":
        path = _first_unsupported_google_schema_path(schema)
        if path is None:
            path = _first_required_google_reference_cycle_path(schema)
        if path is not None:
            return None, {
                "code": "unsupported_schema_constraint",
                "path": path,
                "schema_path": path,
                "message": "Google response_json_schema does not support this constraint",
            }
        return copy.deepcopy(schema), None

    from openprogram.providers._schema import normalize

    try:
        projected = normalize(schema, profile)  # type: ignore[arg-type]
    except Exception:
        return None, {
            "code": "unsupported_schema_constraint",
            "path": "",
            "schema_path": "",
            "message": "Provider schema profile rejected this schema",
        }
    # Existing dialect normalizers may strengthen or delete constraints.
    # Response schemas may use them only when the projection is unchanged.
    if projected == schema:
        return projected, None
    path = _first_schema_difference(schema, projected)
    return None, {
        "code": "unsupported_schema_constraint",
        "path": path,
        "schema_path": path,
        "message": "Provider schema profile would change this constraint",
    }


def _first_schema_difference(original: Any, projected: Any, path: tuple[Any, ...] = ()) -> str:
    if isinstance(original, dict) and isinstance(projected, dict):
        for key in sorted(original):
            if key not in projected:
                return _pointer((*path, key))
            child = _first_schema_difference(original[key], projected[key], (*path, key))
            if child:
                return child
        for key in sorted(projected):
            if key not in original:
                return _pointer((*path, key))
        return ""
    if isinstance(original, list) and isinstance(projected, list):
        for index, (left, right) in enumerate(zip(original, projected)):
            child = _first_schema_difference(left, right, (*path, index))
            if child:
                return child
        if len(original) != len(projected):
            return _pointer((*path, min(len(original), len(projected))))
        return ""
    return "" if original == projected else _pointer(path)


_GOOGLE_SCHEMA_KEYWORDS = frozenset({
    "$anchor",
    "$defs",
    "$id",
    "$ref",
    "additionalProperties",
    "anyOf",
    "description",
    "enum",
    "format",
    "items",
    "maxItems",
    "maximum",
    "minItems",
    "minimum",
    "oneOf",
    "prefixItems",
    "properties",
    "propertyOrdering",
    "required",
    "title",
    "type",
})


def _first_unsupported_google_schema_path(
    schema: Any,
    path: tuple[Any, ...] = (),
) -> str | None:
    if not isinstance(schema, dict):
        return _pointer(path)
    for key in sorted(schema):
        if key not in _GOOGLE_SCHEMA_KEYWORDS:
            return _pointer((*path, key))
    if "$ref" in schema:
        unsupported_siblings = sorted(
            key for key in schema if key != "$ref" and not key.startswith("$")
        )
        if unsupported_siblings:
            return _pointer((*path, unsupported_siblings[0]))
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name in sorted(properties):
            issue = _first_unsupported_google_schema_path(
                properties[name],
                (*path, "properties", name),
            )
            if issue is not None:
                return issue
    definitions = schema.get("$defs")
    if isinstance(definitions, dict):
        for name in sorted(definitions):
            issue = _first_unsupported_google_schema_path(
                definitions[name],
                (*path, "$defs", name),
            )
            if issue is not None:
                return issue
    items = schema.get("items")
    if isinstance(items, dict):
        issue = _first_unsupported_google_schema_path(items, (*path, "items"))
        if issue is not None:
            return issue
    for keyword in ("prefixItems", "anyOf", "oneOf"):
        for index, branch in enumerate(schema.get(keyword) or []):
            issue = _first_unsupported_google_schema_path(
                branch,
                (*path, keyword, index),
            )
            if issue is not None:
                return issue
    additional = schema.get("additionalProperties")
    if isinstance(additional, dict):
        return _first_unsupported_google_schema_path(
            additional,
            (*path, "additionalProperties"),
        )
    return None


def _first_required_google_reference_cycle_path(schema: dict[str, Any]) -> str | None:
    nodes: dict[tuple[Any, ...], dict[str, Any]] = {}
    edges: dict[tuple[Any, ...], list[tuple[Any, ...]]] = {}
    required_edges: list[tuple[tuple[Any, ...], tuple[Any, ...], tuple[Any, ...]]] = []
    anchors: dict[str, tuple[Any, ...]] = {}

    def register(node: Any, path: tuple[Any, ...]) -> None:
        if not isinstance(node, dict):
            return
        nodes[path] = node
        edges.setdefault(path, [])
        anchor = node.get("$anchor")
        if isinstance(anchor, str):
            anchors.setdefault(anchor, path)

        required = node.get("required")
        required_indexes = {
            name: index for index, name in enumerate(required)
        } if isinstance(required, list) else {}
        properties = node.get("properties")
        if isinstance(properties, dict):
            for name in sorted(properties):
                child = properties[name]
                if not isinstance(child, dict):
                    continue
                child_path = (*path, "properties", name)
                edges[path].append(child_path)
                if name in required_indexes:
                    required_edges.append((
                        path,
                        child_path,
                        (*path, "required", required_indexes[name]),
                    ))
                register(child, child_path)

        definitions = node.get("$defs")
        if isinstance(definitions, dict):
            for name in sorted(definitions):
                child = definitions[name]
                if isinstance(child, dict):
                    child_path = (*path, "$defs", name)
                    edges[path].append(child_path)
                    register(child, child_path)

        items = node.get("items")
        if isinstance(items, dict):
            child_path = (*path, "items")
            edges[path].append(child_path)
            register(items, child_path)
        for keyword in ("prefixItems", "anyOf", "oneOf"):
            for index, child in enumerate(node.get(keyword) or []):
                if isinstance(child, dict):
                    child_path = (*path, keyword, index)
                    edges[path].append(child_path)
                    register(child, child_path)
        additional = node.get("additionalProperties")
        if isinstance(additional, dict):
            child_path = (*path, "additionalProperties")
            edges[path].append(child_path)
            register(additional, child_path)

    def resolve(ref: Any) -> tuple[Any, ...] | None:
        if ref == "#":
            return ()
        if not isinstance(ref, str) or not ref.startswith("#"):
            return None
        if ref.startswith("#/"):
            fragment = ref[1:]
            return next((path for path in nodes if _pointer(path) == fragment), None)
        return anchors.get(ref[1:])

    register(schema, ())
    for path, node in nodes.items():
        target = resolve(node.get("$ref"))
        if target in nodes:
            edges[path].append(target)

    def reaches_cycle(start: tuple[Any, ...]) -> bool:
        active: set[tuple[Any, ...]] = set()
        complete: set[tuple[Any, ...]] = set()

        def visit(current: tuple[Any, ...]) -> bool:
            if current in active:
                return True
            if current in complete:
                return False
            active.add(current)
            if any(visit(child) for child in edges.get(current, ())):
                return True
            active.remove(current)
            complete.add(current)
            return False

        return visit(start)

    for _source, child, issue_path in sorted(required_edges, key=lambda edge: edge[2]):
        if reaches_cycle(child):
            return _pointer(issue_path)
    return None


def _tool_choice_allows_hidden_submit(tool_choice: Any) -> bool:
    if tool_choice in (None, "auto", "required"):
        return True
    if isinstance(tool_choice, dict):
        name = tool_choice.get("name")
        function = tool_choice.get("function")
        if isinstance(function, dict):
            name = function.get("name", name)
        return name == HIDDEN_SUBMIT_TOOL_NAME
    return False


def _adapter_contract_matches(model: Any, capabilities: StructuredOutputCapabilities) -> bool:
    provider = getattr(model, "provider", "")
    api = getattr(model, "api", "")
    base_url = (getattr(model, "base_url", "") or "").rstrip("/").lower()
    dialect = capabilities.dialect
    if dialect in {"openai_chat", "openai_responses"}:
        return provider == "openai" and base_url == "https://api.openai.com/v1"
    if dialect == "azure_openai_responses":
        return provider == "azure-openai-responses" and api == "azure-openai-responses"
    if dialect == "anthropic":
        return provider == "anthropic" and base_url == "https://api.anthropic.com"
    if dialect == "google":
        return provider == "google" and api == "google-generative-ai"
    if dialect == "bedrock":
        return provider == "amazon-bedrock" and api == "bedrock-converse-stream"
    return True


def _strict_tool_contract_active(
    model: Any,
    capabilities: StructuredOutputCapabilities,
) -> bool:
    if not capabilities.strict_tool:
        return False
    if capabilities.schema_profile != "openai_strict":
        return True
    from openprogram.providers._schema import wants_strict_flag

    return wants_strict_flag(
        getattr(model, "api", None),
        getattr(model, "id", None),
    )


def _build_plan(
    model: Any,
    capabilities: StructuredOutputCapabilities,
    output: JsonSchemaOutput,
    tools: list[Any],
    *,
    tool_choice: Any = None,
    parallel_tool_calls: bool | None = None,
) -> StructuredOutputPlan:
    original_schema = copy.deepcopy(output.schema)
    provider_schema, projection_issue = _project_schema(
        output.schema,
        capabilities.schema_profile,
    )
    has_tools = bool(tools)

    native_available = (
        (
            capabilities.native == "supported"
            or (
                capabilities.native_model_opt_in
                and getattr(model, "structured_output", None) is True
            )
        )
        and _adapter_contract_matches(model, capabilities)
        and getattr(model, "structured_output", None) is not False
        and capabilities.streaming
        and (not has_tools or capabilities.with_tools)
        and provider_schema is not None
    )
    if native_available:
        return StructuredOutputPlan(
            mode="native",
            original_schema=original_schema,
            provider_schema=provider_schema,
        )

    hidden_conflict = (
        parallel_tool_calls is True
        or not _tool_choice_allows_hidden_submit(tool_choice)
        or any(getattr(tool, "name", None) == HIDDEN_SUBMIT_TOOL_NAME for tool in tools)
    )
    if (
        output.fallback == "auto"
        and _strict_tool_contract_active(model, capabilities)
        and _adapter_contract_matches(model, capabilities)
        and capabilities.streaming
        and provider_schema is not None
        and not hidden_conflict
    ):
        return StructuredOutputPlan(
            mode="tool",
            original_schema=original_schema,
            provider_schema=provider_schema,
            submit_tool_name=HIDDEN_SUBMIT_TOOL_NAME,
        )

    if output.fallback == "prompt":
        return StructuredOutputPlan(
            mode="prompt",
            original_schema=original_schema,
            provider_schema=copy.deepcopy(output.schema),
        )

    provider = getattr(model, "provider", "")
    api = getattr(model, "api", "")
    raise StructuredOutputUnsupportedError(
        f"Structured output is not verified for provider={provider!r}, api={api!r}",
        code="unsupported",
        issues=[projection_issue] if projection_issue is not None else None,
    )


def negotiate_structured_output(
    model: Any,
    capabilities: StructuredOutputCapabilities,
    output: JsonSchemaOutput,
    tools: list[Any] | None = None,
    *,
    tool_choice: Any = None,
    parallel_tool_calls: bool | None = None,
) -> StructuredOutputPlan:
    """Choose a verified mode without weakening caller controls."""
    return _build_plan(
        model,
        capabilities,
        output,
        tools or [],
        tool_choice=tool_choice,
        parallel_tool_calls=parallel_tool_calls,
    )


def build_prompt_fallback(output: JsonSchemaOutput) -> str:
    schema = json.dumps(output.schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (
        "Return only one complete JSON value matching this JSON Schema. "
        "Do not use Markdown fences or add explanatory text. JSON Schema: " + schema
    )
