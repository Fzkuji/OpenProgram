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
        kinds=frozenset({"urllib.request.build_opener"}),
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
        "get_shared_async_client",
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
        "mcp.client.sse.sse_client",
    }
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
        self.sdk_calls: list[tuple[RuntimeHTTPCall, ast.Call]] = []
        self.consumer_literals: set[str] = set()
        self.constant_variables: dict[str, str] = {}

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
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            if isinstance(item.context_expr, ast.Call):
                name = _qualname(item.context_expr.func, self.aliases)
                if name == "socket.socket" and isinstance(item.optional_vars, ast.Name):
                    self.socket_variables.add(item.optional_vars.id)
        self.generic_visit(node)

    visit_AsyncWith = visit_With

    def visit_Call(self, node: ast.Call) -> None:
        name = _qualname(node.func, self.aliases)
        for argument in node.args:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                self.consumer_literals.add(argument.value)
            elif (
                isinstance(argument, ast.Name)
                and argument.id in self.constant_variables
            ):
                self.consumer_literals.add(self.constant_variables[argument.id])
        for keyword in node.keywords:
            if isinstance(keyword.value, ast.Constant) and isinstance(
                keyword.value.value, str
            ):
                self.consumer_literals.add(keyword.value.value)
        short = None if name is None else ".".join(name.split(".")[-2:])
        factory = name if name in _MANAGED_FACTORIES else short
        if factory in _MANAGED_FACTORIES and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                self.consumers.add(first.value)
            elif isinstance(first, ast.Name) and first.id in self.constant_variables:
                self.consumers.add(self.constant_variables[first.id])
        for keyword in node.keywords:
            if keyword.arg == "consumer" and isinstance(keyword.value, ast.Constant):
                if isinstance(keyword.value.value, str):
                    self.consumers.add(keyword.value.value)
            elif (
                keyword.arg == "consumer"
                and isinstance(keyword.value, ast.Name)
                and keyword.value.id in self.constant_variables
            ):
                self.consumers.add(self.constant_variables[keyword.value.id])

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


def scan_runtime_http(
    root: Path,
    *,
    exclusions: tuple[BoundaryExclusion, ...] = BOUNDARY_MANIFEST,
    registry: Mapping[str, object] = CONSUMER_REGISTRY,
) -> RuntimeHTTPInventory:
    root = Path(root)
    excluded = {item.path: item for item in exclusions}
    matched_exclusions: set[str] = set()
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
        if relative != "security/safe_http.py":
            consumers.update(set(registry) & visitor.consumer_literals)
        boundary = excluded.get(relative)
        for issue in visitor.calls:
            if boundary is not None and issue.kind in boundary.kinds:
                matched_exclusions.add(relative)
            else:
                unregistered.append(issue)
        for issue, call in visitor.sdk_calls:
            if boundary is not None and issue.kind in boundary.kinds:
                matched_exclusions.add(relative)
                continue
            keywords = {keyword.arg for keyword in call.keywords}
            disabled = any(
                registry.get(consumer) is not None
                and getattr(registry[consumer], "sdk_disposition", None)
                == SDKDisposition.DISABLED
                for consumer in visitor.consumers
                | (set(registry) & visitor.consumer_literals)
            )
            injected = bool(
                {"http_client", "http_options", "httpx_client_factory"} & keywords
                or any(
                    registry.get(consumer) is not None
                    and getattr(registry[consumer], "sdk_disposition", None)
                    == SDKDisposition.INJECTED_TRANSPORT
                    for consumer in visitor.consumers
                )
            )
            if not disabled and not injected:
                unregistered.append(issue)
                unmanaged.add(issue.kind)

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
    stale = tuple(
        sorted(
            path
            for path in excluded
            if not (root / path).is_file() or path not in matched_exclusions
        )
    )
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
