"""Strict JSON Schema output normalization and local validation."""
from __future__ import annotations

import copy
import json
import re
from collections import deque
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import unquote, urldefrag, urljoin

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


class StructuredOutputGenerationError(StructuredOutputError):
    pass


class StructuredOutputUnsupportedError(StructuredOutputError):
    pass


def normalize_response_format(value: dict[str, Any] | JsonSchemaOutput) -> JsonSchemaOutput:
    """Copy, normalize, and meta-validate a public response-format value."""
    schema_candidate: Any = None
    if isinstance(value, JsonSchemaOutput):
        schema_candidate = value.schema
    elif isinstance(value, dict):
        schema_candidate = value.get("schema") if value.get("type") == "json_schema" else value
    if isinstance(schema_candidate, (dict, list)) and _schema_exceeds_depth_limit(
        schema_candidate,
        _GOOGLE_SCHEMA_MAX_DEPTH,
    ):
        raise StructuredOutputSchemaError(
            "JSON Schema exceeds depth limit",
            code="invalid_schema",
        )

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
    except (SchemaError, RecursionError) as exc:
        message = getattr(exc, "message", "recursive metaschema validation failed")
        raise StructuredOutputSchemaError(
            f"Invalid JSON Schema: {message}", code="invalid_schema"
        ) from exc
    return output


def _reject_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON number: {value}")


def _pointer(parts) -> str:
    return "".join(f"/{str(part).replace('~', '~0').replace('/', '~1')}" for part in parts)


def _bounded_pointer(pointer: str, max_length: int = 512) -> str:
    return pointer if len(pointer) <= max_length else ""


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
                "path": _bounded_pointer(_pointer(error.absolute_path)),
                "schema_path": _bounded_pointer(_pointer(error.absolute_schema_path)),
                "message": (
                    "Value does not satisfy JSON Schema constraint: "
                    f"{error.validator or 'unknown'}"
                )[:500],
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
        graph_issue = _google_reference_graph_issue(schema)
        path = graph_issue[0] if graph_issue is not None else None
        message = graph_issue[1] if graph_issue is not None else None
        if path is None:
            path = _first_unsupported_google_schema_path(schema)
        if path is not None:
            path = _bounded_google_pointer(path)
            return None, {
                "code": "unsupported_schema_constraint",
                "path": path,
                "schema_path": path,
                "message": message or (
                    "Google response_json_schema does not support this constraint"
                ),
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
_GOOGLE_SCHEMA_MAX_DEPTH = 100
_GOOGLE_SCHEMA_MAX_NODES = 4096
_GOOGLE_SCHEMA_MAX_EDGES = 8192
_GOOGLE_SCHEMA_MAX_POINTER_LENGTH = 512


def _schema_exceeds_depth_limit(value: Any, limit: int) -> bool:
    pending = [(value, 0)]
    deepest_seen: dict[int, int] = {}
    while pending:
        current, depth = pending.pop()
        if depth > limit:
            return True
        if not isinstance(current, (dict, list)):
            continue
        identity = id(current)
        if deepest_seen.get(identity, -1) >= depth:
            continue
        deepest_seen[identity] = depth
        children = current.values() if isinstance(current, dict) else current
        pending.extend(
            (child, depth + 1)
            for child in children
            if isinstance(child, (dict, list))
        )
    return False


def _bounded_google_pointer(pointer: str) -> str:
    return _bounded_pointer(pointer, _GOOGLE_SCHEMA_MAX_POINTER_LENGTH)


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


def _google_reference_graph_issue(
    schema: dict[str, Any],
) -> tuple[str, str] | None:
    nodes: dict[tuple[Any, ...], dict[str, Any]] = {}
    edges: dict[
        tuple[Any, ...],
        list[tuple[tuple[Any, ...], tuple[Any, ...] | None]],
    ] = {}
    node_bases: dict[tuple[Any, ...], str] = {}
    paths_by_pointer: dict[str, tuple[Any, ...]] = {}
    resource_roots: dict[str, tuple[Any, ...]] = {}
    anchors: dict[tuple[str, str], tuple[Any, ...]] = {}
    edge_count = 0

    def bounded_pointer(path: tuple[Any, ...]) -> str:
        return _bounded_google_pointer(_pointer(path))

    def limit_issue(limit: str, path: tuple[Any, ...]) -> tuple[str, str]:
        return (
            bounded_pointer(path),
            f"Google response_json_schema exceeds {limit} limit",
        )

    class GraphIssue(Exception):
        def __init__(self, issue: tuple[str, str]):
            self.issue = issue

    def add_edge(
        source: tuple[Any, ...],
        target: tuple[Any, ...],
        required_path: tuple[Any, ...] | None,
        issue_path: tuple[Any, ...],
    ) -> None:
        nonlocal edge_count
        edge_count += 1
        if edge_count > _GOOGLE_SCHEMA_MAX_EDGES:
            raise GraphIssue(limit_issue("edges", issue_path))
        edges[source].append((target, required_path))

    def register(
        node: Any,
        path: tuple[Any, ...],
        depth: int,
        inherited_base: str,
        inherited_resource_root: tuple[Any, ...],
    ) -> None:
        if not isinstance(node, dict):
            return
        if depth > _GOOGLE_SCHEMA_MAX_DEPTH:
            raise GraphIssue(limit_issue("depth", path))
        if len(nodes) >= _GOOGLE_SCHEMA_MAX_NODES:
            raise GraphIssue(limit_issue("nodes", path))

        base = inherited_base
        resource_root = inherited_resource_root
        identifier = node.get("$id")
        if isinstance(identifier, str):
            base = urljoin(inherited_base, identifier)
            resource_uri, _fragment = urldefrag(base)
            existing = resource_roots.get(resource_uri)
            if existing is not None and existing != path:
                raise GraphIssue((
                    bounded_pointer((*path, "$id")),
                    "Google response_json_schema has duplicate resource identifier",
                ))
            resource_roots[resource_uri] = path
            resource_root = path
        else:
            resource_uri, _fragment = urldefrag(base)
            resource_roots.setdefault(resource_uri, resource_root)

        nodes[path] = node
        node_bases[path] = base
        paths_by_pointer[_pointer(path)] = path
        edges.setdefault(path, [])
        anchor = node.get("$anchor")
        if isinstance(anchor, str):
            anchor_key = (resource_uri, anchor)
            existing = anchors.get(anchor_key)
            if existing is not None and existing != path:
                raise GraphIssue((
                    bounded_pointer((*path, "$anchor")),
                    "Google response_json_schema has duplicate anchor in resource",
                ))
            anchors[anchor_key] = path

        required = node.get("required")
        properties = node.get("properties")
        if isinstance(properties, dict):
            for name in sorted(properties):
                child = properties[name]
                if not isinstance(child, dict):
                    continue
                child_path = (*path, "properties", name)
                register(child, child_path, depth + 1, base, resource_root)
            if isinstance(required, list):
                for index, name in enumerate(required):
                    child = properties.get(name)
                    if isinstance(child, dict):
                        add_edge(
                            path,
                            (*path, "properties", name),
                            (*path, "required", index),
                            (*path, "required", index),
                        )

        definitions = node.get("$defs")
        if isinstance(definitions, dict):
            for name in sorted(definitions):
                child = definitions[name]
                if isinstance(child, dict):
                    child_path = (*path, "$defs", name)
                    register(child, child_path, depth + 1, base, resource_root)

        items = node.get("items")
        if isinstance(items, dict):
            child_path = (*path, "items")
            add_edge(path, child_path, None, child_path)
            register(items, child_path, depth + 1, base, resource_root)
        for keyword in ("prefixItems", "anyOf", "oneOf"):
            for index, child in enumerate(node.get(keyword) or []):
                if isinstance(child, dict):
                    child_path = (*path, keyword, index)
                    add_edge(path, child_path, None, child_path)
                    register(child, child_path, depth + 1, base, resource_root)
        additional = node.get("additionalProperties")
        if isinstance(additional, dict):
            child_path = (*path, "additionalProperties")
            add_edge(path, child_path, None, child_path)
            register(additional, child_path, depth + 1, base, resource_root)

    def resolve(path: tuple[Any, ...], ref: str) -> tuple[Any, ...] | None:
        resolved = urljoin(node_bases[path], ref)
        resource_uri, encoded_fragment = urldefrag(resolved)
        resource_root = resource_roots.get(resource_uri)
        if resource_root is None:
            return None
        fragment = unquote(encoded_fragment)
        if not fragment:
            return resource_root
        if fragment.startswith("/"):
            return paths_by_pointer.get(f"{_pointer(resource_root)}{fragment}")
        return anchors.get((resource_uri, fragment))

    try:
        register(schema, (), 0, "", ())
        for path, node in nodes.items():
            ref = node.get("$ref")
            if not isinstance(ref, str):
                continue
            target = resolve(path, ref)
            if target is None:
                return (
                    bounded_pointer((*path, "$ref")),
                    "Google response_json_schema cannot resolve local reference",
                )
            add_edge(path, target, None, (*path, "$ref"))
    except GraphIssue as exc:
        return exc.issue

    outdegree = {path: len(outgoing) for path, outgoing in edges.items()}
    predecessors: dict[tuple[Any, ...], list[tuple[Any, ...]]] = {
        path: [] for path in nodes
    }
    for source, outgoing in edges.items():
        for target, _required_path in outgoing:
            predecessors[target].append(source)
    removable = deque(path for path, degree in outdegree.items() if degree == 0)
    removed: set[tuple[Any, ...]] = set()
    while removable:
        current = removable.popleft()
        if current in removed:
            continue
        removed.add(current)
        for predecessor in predecessors[current]:
            outdegree[predecessor] -= 1
            if outdegree[predecessor] == 0:
                removable.append(predecessor)
    reaches_cycle = set(nodes) - removed

    pending = deque([()])
    visited: set[tuple[Any, ...]] = set()
    while pending:
        current = pending.popleft()
        if current in visited:
            continue
        visited.add(current)
        for target, required_path in edges[current]:
            if required_path is not None:
                if target in reaches_cycle:
                    return (
                        bounded_pointer(required_path),
                        "Google response_json_schema required property enters local reference cycle",
                    )
                continue
            pending.append(target)
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
