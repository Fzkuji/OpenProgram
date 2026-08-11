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
_MANAGED_FACTORIES = frozenset(
    {
        "safe_client",
        "safe_async_client",
        "configured_safe_client",
        "configured_safe_async_client",
        "safe_http.safe_client",
        "safe_http.safe_async_client",
        "safe_http.configured_safe_client",
        "safe_http.configured_safe_async_client",
        "http_client.get_shared_async_client",
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


def _is_managed_factory_name(name: str | None) -> bool:
    if name is None:
        return False
    short = ".".join(name.split(".")[-2:])
    return name in _MANAGED_FACTORIES or short in _MANAGED_FACTORIES


class _HTTPVisitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.aliases: dict[str, str] = {}
        self.socket_variables: set[str] = set()
        self.calls: list[RuntimeHTTPCall] = []
        self.consumers: set[str] = set()
        self.sdk_calls: list[tuple[RuntimeHTTPCall, ast.Call]] = []
        self.sdk_guards: set[str] = set()
        self.constant_variables: dict[str, str] = {}
        self.managed_transport_containers: set[str] = set()

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
        if self._contains_injected_transport(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.managed_transport_containers.add(target.id)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None and self._contains_injected_transport(node.value):
            if isinstance(node.target, ast.Name):
                self.managed_transport_containers.add(node.target.id)
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

    def _contains_injected_transport(self, node: ast.AST) -> bool:
        for child in ast.walk(node):
            if isinstance(child, ast.Dict):
                for key, value in zip(child.keys, child.values, strict=True):
                    if (
                        isinstance(key, ast.Constant)
                        and key.value in _SDK_INJECTION_KEYWORDS
                        and self._contains_managed_factory(value)
                    ):
                        return True
            if (
                isinstance(child, ast.Name)
                and child.id in self.managed_transport_containers
            ):
                return True
        return False

    def _contains_managed_factory(self, node: ast.AST) -> bool:
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            name = _qualname(child.func, self.aliases)
            if _is_managed_factory_name(name):
                return True
        return False

    def visit_Call(self, node: ast.Call) -> None:
        name = _qualname(node.func, self.aliases)
        short = None if name is None else ".".join(name.split(".")[-2:])
        if _is_managed_factory_name(name) and node.args:
            consumer = self._constant_string(node.args[0])
            if consumer is not None:
                self.consumers.add(consumer)
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
            self.sdk_calls.append((issue, node))
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
        for issue, call in visitor.sdk_calls:
            exclusion_key = (relative, issue.kind)
            observed = observed_exclusions.get(exclusion_key, 0)
            observed_exclusions[exclusion_key] = observed + 1
            if issue.kind in expected and observed < expected[issue.kind]:
                continue
            keywords = {keyword.arg for keyword in call.keywords}
            sdk_name = issue.kind.removeprefix("sdk.")
            consumer = _SDK_CONSUMERS.get(sdk_name)
            spec = registry.get(consumer) if consumer is not None else None
            disabled = bool(
                spec is not None
                and getattr(spec, "sdk_disposition", None) == SDKDisposition.DISABLED
                and consumer in visitor.sdk_guards
            )
            has_injected_argument = bool(
                any(
                    keyword.arg in _SDK_INJECTION_KEYWORDS
                    and not (
                        isinstance(keyword.value, ast.Constant)
                        and keyword.value.value is None
                    )
                    for keyword in call.keywords
                )
                or any(
                    keyword.arg is None
                    and visitor._contains_injected_transport(keyword.value)
                    for keyword in call.keywords
                )
            )
            injected = bool(
                spec is not None
                and getattr(spec, "sdk_disposition", None)
                == SDKDisposition.INJECTED_TRANSPORT
                and has_injected_argument
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
