"""Shared Runtime HTTP denial audit and fail-closed source inventory."""

from __future__ import annotations

import ast
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import threading
from types import MappingProxyType
from typing import Mapping

from .safe_http import CONSUMER_REGISTRY, SDKDisposition
from .url_policy import URLPolicyError, normalize_origin


RUNTIME_HTTP_AUDIT_CAPACITY = 256


@dataclass(frozen=True)
class RuntimeHTTPAuditEvent:
    consumer: str
    reason: str
    safe_origin: str
    delegated_to_policy_proxy: bool
    timestamp: str


@dataclass(frozen=True)
class RuntimeHTTPCall:
    path: str
    line: int
    kind: str


@dataclass(frozen=True)
class BoundaryExclusion:
    path: str
    boundary_owner: str
    reason: str
    kinds: frozenset[str] = frozenset()
    call_counts: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class RuntimeHTTPInventory:
    unregistered: tuple[RuntimeHTTPCall, ...]
    active_unmanaged_transports: tuple[str, ...]
    registry_without_consumer: tuple[str, ...]
    stale_exclusions: tuple[str, ...]


_AUDIT_LOCK = threading.Lock()
_AUDIT_EVENTS: deque[RuntimeHTTPAuditEvent] = deque(maxlen=RUNTIME_HTTP_AUDIT_CAPACITY)
_REASON_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z", re.ASCII)


def _safe_origin(url: object) -> str:
    if not isinstance(url, str) or len(url) > 4096:
        return "<invalid-url>"
    try:
        return normalize_origin(url)
    except URLPolicyError as exc:
        return exc.safe_url
    except Exception:
        return "<invalid-url>"


def record_runtime_http_denial(
    *,
    consumer: str,
    reason: str,
    url: object,
    delegated_to_policy_proxy: bool,
) -> None:
    safe_consumer = (
        consumer
        if isinstance(consumer, str) and consumer in CONSUMER_REGISTRY
        else "<unknown-consumer>"
    )
    safe_reason = (
        reason
        if isinstance(reason, str) and _REASON_CODE.fullmatch(reason)
        else "INVALID_REASON"
    )
    event = RuntimeHTTPAuditEvent(
        consumer=safe_consumer,
        reason=safe_reason,
        safe_origin=_safe_origin(url),
        delegated_to_policy_proxy=bool(delegated_to_policy_proxy),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    with _AUDIT_LOCK:
        _AUDIT_EVENTS.append(event)


def recent_runtime_http_denials() -> tuple[RuntimeHTTPAuditEvent, ...]:
    with _AUDIT_LOCK:
        return tuple(_AUDIT_EVENTS)


def clear_runtime_http_audit() -> None:
    with _AUDIT_LOCK:
        _AUDIT_EVENTS.clear()


BOUNDARY_MANIFEST = (
    BoundaryExclusion(
        path="security/safe_http.py",
        boundary_owner="runtime-http-managed-transport",
        reason="the peer-constrained transport implementation owns these httpcore pools",
        kinds=frozenset(
            {
                "httpcore.ConnectionPool",
                "httpcore.AsyncConnectionPool",
                "httpcore.HTTPProxy",
                "httpcore.AsyncHTTPProxy",
            }
        ),
    ),
    BoundaryExclusion(
        path="functions/tools/browser/_chrome_bootstrap.py",
        boundary_owner="browser-control",
        reason="browser bootstrap/navigation is outside Runtime URL fetch policy",
        kinds=frozenset({"socket.create_connection", "urllib.request.build_opener"}),
        call_counts=(
            ("socket.create_connection", 1),
            ("urllib.request.build_opener", 2),
        ),
    ),
    BoundaryExclusion(
        path="_cli_cmds/mcp.py",
        boundary_owner="owner-control-plane",
        reason="owner CLI calls the authenticated OpenProgram backend",
        kinds=frozenset({"urllib.request.build_opener"}),
    ),
    BoundaryExclusion(
        path="cli_ink.py",
        boundary_owner="owner-control-plane",
        reason="loopback worker liveness probe does not fetch a URL",
        kinds=frozenset({"socket.connect"}),
    ),
    BoundaryExclusion(
        path="_cli_cmds/doctor.py",
        boundary_owner="owner-control-plane",
        reason="loopback worker liveness probe does not fetch a URL",
        kinds=frozenset({"socket.connect"}),
    ),
    BoundaryExclusion(
        path="_cli_cmds/rescue.py",
        boundary_owner="owner-control-plane",
        reason="loopback worker liveness probe does not fetch a URL",
        kinds=frozenset({"socket.connect"}),
    ),
)


# Registry entries intentionally shipped without a current production caller.
# Their declarations remain immutable compatibility contracts rather than
# pretending that a non-existent implementation was detected by the scanner.
EXPLICIT_CONSUMER_DECLARATIONS: Mapping[str, str] = MappingProxyType(
    {
        "channel.feishu.api": "channel adapter is not shipped",
        "channel.matrix.configured": "channel adapter is not shipped",
        "channel.generated_asset.download": "legacy registry compatibility key",
        "channel.attachment.download": "attachment helper selects the channel consumer dynamically",
        "channel.slack.attachment": "attachment helper selects the channel consumer dynamically",
        "channel.telegram.attachment": "attachment helper selects the channel consumer dynamically",
        "mcp.configured.http": "MCP transport selects the registered key dynamically",
        "mcp.configured.sse": "MCP transport selects the registered key dynamically",
        "mcp.loopback.callback": "MCP callback transport is selected dynamically",
        "tool.image_api.configured": "image provider selects fixed or configured API key dynamically",
    }
)


_RAW_CALLS = frozenset(
    {
        "urllib.request.urlopen",
        "urllib.request.build_opener",
        "requests.get",
        "requests.post",
        "requests.put",
        "requests.patch",
        "requests.delete",
        "requests.head",
        "requests.request",
        "requests.Session",
        "httpx.get",
        "httpx.post",
        "httpx.put",
        "httpx.patch",
        "httpx.delete",
        "httpx.head",
        "httpx.request",
        "httpx.Client",
        "httpx.AsyncClient",
        "httpx.HTTPTransport",
        "httpx.AsyncHTTPTransport",
        "httpcore.ConnectionPool",
        "httpcore.AsyncConnectionPool",
        "httpcore.HTTPProxy",
        "httpcore.AsyncHTTPProxy",
        "aiohttp.ClientSession",
        "urllib3.PoolManager",
        "urllib3.ProxyManager",
        "urllib3.request",
        "socket.create_connection",
    }
)
_MANAGED_FACTORY_NAMES = frozenset(
    {
        "openprogram.security.safe_http.safe_client",
        "openprogram.security.safe_http.safe_async_client",
        "openprogram.security.safe_http.configured_safe_client",
        "openprogram.security.safe_http.configured_safe_async_client",
        "openprogram.providers.utils.http_client.get_shared_async_client",
        "utils.http_client.get_shared_async_client",
    }
)
_MANAGED_CONSUMER_CALL_NAMES = frozenset(
    {
        "openprogram.functions.tools.web_search._http.get_json",
        "openprogram.functions.tools.web_search._http.post_json",
    }
)
_SDK_CONSTRUCTORS = frozenset(
    {
        "openai.OpenAI",
        "openai.AsyncOpenAI",
        "openai.AzureOpenAI",
        "openai.AsyncAzureOpenAI",
        "anthropic.Anthropic",
        "anthropic.AsyncAnthropic",
        "genai.Client",
        "google.genai.Client",
        "edge_tts.Communicate",
        "slack_sdk.web.WebClient",
        "slack_sdk.socket_mode.SocketModeClient",
        "discord.Client",
        "mcp.client.streamable_http.streamable_http_client",
        "mcp.client.streamable_http.streamablehttp_client",
        "mcp.client.sse.sse_client",
    }
)

_SDK_CONSUMERS: Mapping[str, str] = MappingProxyType(
    {
        "openai.OpenAI": "provider.openai.sdk",
        "openai.AsyncOpenAI": "provider.openai.sdk",
        "openai.AzureOpenAI": "provider.openai.sdk",
        "openai.AsyncAzureOpenAI": "provider.openai.sdk",
        "anthropic.Anthropic": "provider.anthropic.sdk",
        "anthropic.AsyncAnthropic": "provider.anthropic.sdk",
        "genai.Client": "provider.google.sdk",
        "google.genai.Client": "provider.google.sdk",
        "edge_tts.Communicate": "tts.edge_sdk",
        "slack_sdk.web.WebClient": "channel.slack.gateway_sdk",
        "slack_sdk.socket_mode.SocketModeClient": "channel.slack.gateway_sdk",
        "discord.Client": "channel.discord.gateway_sdk",
        "mcp.client.streamable_http.streamable_http_client": "mcp.configured.http",
        "mcp.client.streamable_http.streamablehttp_client": "mcp.configured.http",
        "mcp.client.sse.sse_client": "mcp.configured.sse",
    }
)
_SDK_INJECTION_KEYWORDS = frozenset(
    {"http_client", "http_options", "httpx_client_factory"}
)


def _qualname(node: ast.AST, aliases: Mapping[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _qualname(node.value, aliases)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


class _HTTPVisitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.aliases: dict[str, str] = {}
        self.socket_variables: set[str] = set()
        self.calls: list[RuntimeHTTPCall] = []
        self.consumers: set[str] = set()
        self.sdk_calls: list[tuple[RuntimeHTTPCall, frozenset[str]]] = []
        self.sdk_guards: set[str] = set()
        self.constant_variables: dict[str, str] = {}
        self.managed_values: dict[tuple[tuple[str, ...], str], frozenset[str]] = {}
        self.scope: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for name in node.names:
            local = name.asname or name.name.split(".")[0]
            self.aliases[local] = name.name if name.asname else local
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            for name in node.names:
                self.aliases[name.asname or name.name] = f"{node.module}.{name.name}"
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Assign(self, node: ast.Assign) -> None:
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.constant_variables[target.id] = node.value.value
        if isinstance(node.value, ast.Call):
            name = _qualname(node.value.func, self.aliases)
            if name == "socket.socket":
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.socket_variables.add(target.id)
        provenance = self._managed_consumers(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                key = (tuple(self.scope), target.id)
                if provenance:
                    self.managed_values[(tuple(self.scope), target.id)] = provenance
                else:
                    self.managed_values.pop(key, None)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        provenance = (
            frozenset() if node.value is None else self._managed_consumers(node.value)
        )
        if isinstance(node.target, ast.Name):
            key = (tuple(self.scope), node.target.id)
            if provenance:
                self.managed_values[(tuple(self.scope), node.target.id)] = provenance
            else:
                self.managed_values.pop(key, None)
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            if isinstance(item.context_expr, ast.Call):
                name = _qualname(item.context_expr.func, self.aliases)
                if name == "socket.socket" and isinstance(item.optional_vars, ast.Name):
                    self.socket_variables.add(item.optional_vars.id)
        self.generic_visit(node)

    visit_AsyncWith = visit_With

    def _constant_string(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return self.constant_variables.get(node.id)
        return None

    def _lookup_managed_value(self, name: str) -> frozenset[str]:
        for depth in range(len(self.scope), -1, -1):
            value = self.managed_values.get((tuple(self.scope[:depth]), name))
            if value:
                return value
        return frozenset()

    def _managed_factory_consumer(self, call: ast.Call) -> str | None:
        name = _qualname(call.func, self.aliases)
        if name in _MANAGED_FACTORY_NAMES:
            if name.endswith("get_shared_async_client"):
                for keyword in call.keywords:
                    if keyword.arg == "consumer":
                        return self._constant_string(keyword.value)
                return None
            if call.args:
                return self._constant_string(call.args[0])
            for keyword in call.keywords:
                if keyword.arg == "consumer":
                    return self._constant_string(keyword.value)
            return None
        if name in {
            "openprogram.providers.utils.http_client.build_google_http_options",
            "utils.http_client.build_google_http_options",
        }:
            return "provider.google.sdk"
        if (
            self.path == "providers/anthropic/anthropic.py"
            and name == "_shared_http_client"
        ):
            return "provider.anthropic.sdk"
        if self.path == "mcp/client.py" and name == "self._managed_http_client_factory":
            if "_run_http" in self.scope:
                return "mcp.configured.http"
            if "_run_sse" in self.scope:
                return "mcp.configured.sse"
        return None

    def _managed_consumers(self, node: ast.AST) -> frozenset[str]:
        if isinstance(node, ast.Name):
            return self._lookup_managed_value(node.id)
        if isinstance(node, ast.Call):
            consumer = self._managed_factory_consumer(node)
            if consumer is not None:
                return frozenset({consumer})
            if isinstance(node.func, ast.Name):
                return self._lookup_managed_value(node.func.id)
            if isinstance(node.func, ast.Attribute) and node.func.attr == "items":
                return self._managed_consumers(node.func.value)
            return frozenset()
        if isinstance(node, ast.Dict):
            consumers: set[str] = set()
            for key, value in zip(node.keys, node.values, strict=True):
                if (
                    isinstance(key, ast.Constant)
                    and key.value in _SDK_INJECTION_KEYWORDS
                ):
                    consumers.update(self._managed_consumers(value))
            return frozenset(consumers)
        if isinstance(node, ast.DictComp):
            consumers: set[str] = set()
            for generator in node.generators:
                consumers.update(self._managed_consumers(generator.iter))
            return frozenset(consumers)
        if isinstance(node, ast.IfExp):
            body = self._managed_consumers(node.body)
            other = self._managed_consumers(node.orelse)
            return body if body and body == other else frozenset()
        return frozenset()

    def visit_Call(self, node: ast.Call) -> None:
        name = _qualname(node.func, self.aliases)
        short = None if name is None else ".".join(name.split(".")[-2:])
        factory_consumer = self._managed_factory_consumer(node)
        if factory_consumer is not None:
            self.consumers.add(factory_consumer)
        if name in _MANAGED_CONSUMER_CALL_NAMES:
            for keyword in node.keywords:
                if keyword.arg == "consumer":
                    consumer = self._constant_string(keyword.value)
                    if consumer is not None:
                        self.consumers.add(consumer)
        if name and name.endswith("require_active_sdk_transport") and node.args:
            consumer = self._constant_string(node.args[0])
            if consumer is not None:
                self.consumers.add(consumer)
                self.sdk_guards.add(consumer)

        if name in _RAW_CALLS:
            self.calls.append(RuntimeHTTPCall(self.path, node.lineno, name))
        elif isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if node.func.attr == "connect" and (
                (isinstance(owner, ast.Name) and owner.id in self.socket_variables)
                or (
                    isinstance(owner, ast.Call)
                    and _qualname(owner.func, self.aliases) == "socket.socket"
                )
            ):
                self.calls.append(
                    RuntimeHTTPCall(self.path, node.lineno, "socket.connect")
                )

        sdk_name = name
        if sdk_name and sdk_name.startswith("openprogram.providers."):
            sdk_name = ".".join(sdk_name.split(".")[-2:])
        if sdk_name in _SDK_CONSTRUCTORS or short in _SDK_CONSTRUCTORS:
            issue = RuntimeHTTPCall(self.path, node.lineno, f"sdk.{sdk_name}")
            injected_consumers: set[str] = set()
            for keyword in node.keywords:
                if keyword.arg in _SDK_INJECTION_KEYWORDS or keyword.arg is None:
                    injected_consumers.update(self._managed_consumers(keyword.value))
            self.sdk_calls.append((issue, frozenset(injected_consumers)))
        self.generic_visit(node)


def _source_files(root: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)
    )


def _expected_exclusion_counts(
    exclusion: BoundaryExclusion | None,
) -> dict[str, int]:
    if exclusion is None:
        return {}
    if exclusion.call_counts:
        counts = dict(exclusion.call_counts)
        if (
            len(counts) != len(exclusion.call_counts)
            or set(counts) != set(exclusion.kinds)
            or any(type(count) is not int or count < 1 for count in counts.values())
        ):
            return {}
        return counts
    return {kind: 1 for kind in exclusion.kinds}


def scan_runtime_http(
    root: Path,
    *,
    exclusions: tuple[BoundaryExclusion, ...] = BOUNDARY_MANIFEST,
    registry: Mapping[str, object] = CONSUMER_REGISTRY,
) -> RuntimeHTTPInventory:
    root = Path(root)
    excluded = {item.path: item for item in exclusions}
    observed_exclusions: dict[tuple[str, str], int] = {}
    unregistered: list[RuntimeHTTPCall] = []
    consumers: set[str] = set()
    unmanaged: set[str] = set()

    for source_path in _source_files(root):
        relative = source_path.relative_to(root).as_posix()
        try:
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            unregistered.append(RuntimeHTTPCall(relative, 0, "source.parse"))
            continue
        visitor = _HTTPVisitor(relative)
        visitor.visit(tree)
        consumers.update(visitor.consumers)
        boundary = excluded.get(relative)
        expected = _expected_exclusion_counts(boundary)
        for issue in visitor.calls:
            exclusion_key = (relative, issue.kind)
            observed = observed_exclusions.get(exclusion_key, 0)
            observed_exclusions[exclusion_key] = observed + 1
            if issue.kind in expected and observed < expected[issue.kind]:
                continue
            unregistered.append(issue)
        for issue, injected_consumers in visitor.sdk_calls:
            exclusion_key = (relative, issue.kind)
            observed = observed_exclusions.get(exclusion_key, 0)
            observed_exclusions[exclusion_key] = observed + 1
            if issue.kind in expected and observed < expected[issue.kind]:
                continue
            sdk_name = issue.kind.removeprefix("sdk.")
            consumer = _SDK_CONSUMERS.get(sdk_name)
            spec = registry.get(consumer) if consumer is not None else None
            disabled = bool(
                spec is not None
                and getattr(spec, "sdk_disposition", None) == SDKDisposition.DISABLED
                and consumer in visitor.sdk_guards
            )
            injected = bool(
                spec is not None
                and getattr(spec, "sdk_disposition", None)
                == SDKDisposition.INJECTED_TRANSPORT
                and injected_consumers == {consumer}
            )
            if not disabled and not injected:
                unregistered.append(issue)
                unmanaged.add(
                    consumer
                    if consumer is not None and consumer in registry
                    else issue.kind
                )

    declared = set(EXPLICIT_CONSUMER_DECLARATIONS)
    missing = tuple(sorted(set(registry) - consumers - declared))
    for consumer, spec in registry.items():
        disposition = getattr(spec, "sdk_disposition", None)
        if disposition is not None and disposition not in {
            SDKDisposition.INJECTED_TRANSPORT,
            SDKDisposition.EXACT_ORIGIN,
            SDKDisposition.POLICY_PROXY,
            SDKDisposition.DISABLED,
        }:
            unmanaged.add(consumer)
    stale_items: list[str] = []
    for path, boundary in excluded.items():
        if not (root / path).is_file():
            stale_items.append(path)
            continue
        expected = _expected_exclusion_counts(boundary)
        if not expected:
            stale_items.append(path)
            continue
        for kind, count in expected.items():
            actual = observed_exclusions.get((path, kind), 0)
            if actual < count:
                stale_items.append(f"{path}:{kind} expected={count} actual={actual}")
    stale = tuple(sorted(stale_items))
    return RuntimeHTTPInventory(
        unregistered=tuple(
            sorted(unregistered, key=lambda item: (item.path, item.line, item.kind))
        ),
        active_unmanaged_transports=tuple(sorted(unmanaged)),
        registry_without_consumer=missing,
        stale_exclusions=stale,
    )


__all__ = [
    "BOUNDARY_MANIFEST",
    "BoundaryExclusion",
    "EXPLICIT_CONSUMER_DECLARATIONS",
    "RUNTIME_HTTP_AUDIT_CAPACITY",
    "RuntimeHTTPAuditEvent",
    "RuntimeHTTPCall",
    "RuntimeHTTPInventory",
    "clear_runtime_http_audit",
    "recent_runtime_http_denials",
    "record_runtime_http_denial",
    "scan_runtime_http",
]
