"""Shared Runtime HTTP denial audit and fail-closed source inventory."""

from __future__ import annotations

import ast
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import os
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
        path="programs/tools/web/browser/_chrome_bootstrap.py",
        boundary_owner="browser-control",
        reason="browser bootstrap/navigation is outside Runtime URL fetch policy",
        kinds=frozenset({"socket.create_connection", "urllib.request.build_opener"}),
        call_counts=(
            ("socket.create_connection", 1),
            ("urllib.request.build_opener", 2),
        ),
    ),
    BoundaryExclusion(
        path="apps/cli/python/openprogram_cli/_impl/commands/mcp.py",
        boundary_owner="owner-control-plane",
        reason="owner CLI calls the authenticated OpenProgram backend",
        kinds=frozenset({"urllib.request.build_opener"}),
    ),
    BoundaryExclusion(
        path="apps/cli/python/openprogram_cli/_impl/commands/execution.py",
        boundary_owner="owner-control-plane",
        reason="owner CLI cancels executions on the authenticated OpenProgram backend",
        kinds=frozenset({"urllib.request.build_opener"}),
    ),
    BoundaryExclusion(
        path="mcp/server/service.py",
        boundary_owner="owner-control-plane",
        reason="stdio MCP calls the authenticated loopback OpenProgram worker",
        kinds=frozenset({"urllib.request.build_opener"}),
    ),
    BoundaryExclusion(
        path="apps/cli/python/openprogram_cli/_impl/ink.py",
        boundary_owner="owner-control-plane",
        reason="loopback worker liveness probe does not fetch a URL",
        kinds=frozenset({"socket.connect"}),
    ),
    BoundaryExclusion(
        path="apps/cli/python/openprogram_cli/_impl/commands/doctor.py",
        boundary_owner="owner-control-plane",
        reason="loopback worker liveness probe does not fetch a URL",
        kinds=frozenset({"socket.connect"}),
    ),
    BoundaryExclusion(
        path="apps/cli/python/openprogram_cli/_impl/commands/rescue.py",
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
        "openprogram.programs.tools.web.web_search._http.get_json",
        "openprogram.programs.tools.web.web_search._http.post_json",
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
        "boto3.client",
        "boto3.session.Session.client",
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
        "boto3.client": "provider.amazon_bedrock.sdk",
        "boto3.session.Session.client": "provider.amazon_bedrock.sdk",
    }
)
_SDK_INJECTION_KEYWORDS = frozenset(
    {"http_client", "http_options", "httpx_client_factory"}
)
_SDK_GUARD_CALLS = frozenset(
    {"openprogram.security.safe_http.require_active_sdk_transport"}
)
_BOTO3_SESSION_PROVENANCE = "sdk.boto3.session.Session"


def _qualname(node: ast.AST, aliases: Mapping[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _qualname(node.value, aliases)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


_ManagedKey = tuple[tuple[str, ...], str]
_ManagedState = dict[_ManagedKey, frozenset[str]]


@dataclass
class _BlockFlow:
    normal: _ManagedState | None
    breaks: list[_ManagedState]
    continues: list[_ManagedState]
    returns: list[_ManagedState]
    raises: list[_ManagedState]


@dataclass
class _FlowCollector:
    breaks: list[_ManagedState]
    continues: list[_ManagedState]
    returns: list[_ManagedState]
    raises: list[_ManagedState]
    terminated: bool = False


class _HTTPVisitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.aliases: dict[str, str] = {}
        self.socket_variables: set[str] = set()
        self.calls: list[RuntimeHTTPCall] = []
        self.consumers: set[str] = set()
        self.sdk_calls: list[
            tuple[RuntimeHTTPCall, frozenset[str], frozenset[str]]
        ] = []
        self.constant_variables: dict[str, str] = {}
        self.managed_values: dict[tuple[tuple[str, ...], str], frozenset[str]] = {}
        self.scope: list[str] = []
        self._flow_collector: _FlowCollector | None = None

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
        for decorator in node.decorator_list:
            self.visit(decorator)
        for type_param in getattr(node, "type_params", ()):
            self.visit(type_param)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword)
        outer_collector = self._flow_collector
        self._flow_collector = None
        self.scope.append(node.name)
        try:
            self._visit_block_flow_from(node.body, self.managed_values)
        finally:
            self.scope.pop()
            self._flow_collector = outer_collector

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        self.visit(node.args)
        if node.returns is not None:
            self.visit(node.returns)
        outer_collector = self._flow_collector
        self._flow_collector = None
        self.scope.append(node.name)
        try:
            self._visit_block_flow_from(node.body, self.managed_values)
        finally:
            self.scope.pop()
            self._flow_collector = outer_collector

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
        if (
            isinstance(node.value, ast.Call)
            and _qualname(node.value.func, self.aliases) == "boto3.session.Session"
        ):
            provenance = frozenset({_BOTO3_SESSION_PROVENANCE})
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

    def _active_sdk_guards(self) -> frozenset[str]:
        guards: set[str] = set()
        prefix = "@sdk_guard:"
        for (scope, name), values in self.managed_values.items():
            if scope == tuple(self.scope) and name.startswith(prefix):
                guards.update(values)
        for depth in range(len(self.scope)):
            parent = tuple(self.scope[:depth])
            for (scope, name), values in self.managed_values.items():
                if scope == parent and name.startswith(prefix):
                    guards.update(values)
        return frozenset(guards)

    def _visit_block_flow_from(
        self,
        statements: list[ast.stmt],
        initial: Mapping[_ManagedKey, frozenset[str]],
    ) -> _BlockFlow:
        outer = self.managed_values
        parent_collector = self._flow_collector
        collector = _FlowCollector([], [], [], [])
        self.managed_values = dict(initial)
        self._flow_collector = collector
        try:
            for statement in statements:
                self.visit(statement)
                if collector.terminated:
                    break
            return _BlockFlow(
                normal=None if collector.terminated else dict(self.managed_values),
                breaks=collector.breaks,
                continues=collector.continues,
                returns=collector.returns,
                raises=collector.raises,
            )
        finally:
            self.managed_values = outer
            self._flow_collector = parent_collector

    def _visit_block_with_checkpoints_from(
        self,
        statements: list[ast.stmt],
        initial: Mapping[_ManagedKey, frozenset[str]],
    ) -> tuple[
        _BlockFlow,
        list[Mapping[_ManagedKey, frozenset[str]]],
    ]:
        outer = self.managed_values
        parent_collector = self._flow_collector
        collector = _FlowCollector([], [], [], [])
        self.managed_values = dict(initial)
        self._flow_collector = collector
        checkpoints: list[Mapping[_ManagedKey, frozenset[str]]] = [dict(initial)]
        try:
            for statement in statements:
                self.visit(statement)
                checkpoints.append(dict(self.managed_values))
                if collector.terminated:
                    break
            return (
                _BlockFlow(
                    normal=None if collector.terminated else dict(self.managed_values),
                    breaks=collector.breaks,
                    continues=collector.continues,
                    returns=collector.returns,
                    raises=collector.raises,
                ),
                checkpoints,
            )
        finally:
            self.managed_values = outer
            self._flow_collector = parent_collector

    def _apply_flow(self, flow: _BlockFlow) -> None:
        collector = self._flow_collector
        if collector is not None:
            collector.breaks.extend(flow.breaks)
            collector.continues.extend(flow.continues)
            collector.returns.extend(flow.returns)
            collector.raises.extend(flow.raises)
            if flow.normal is None:
                collector.terminated = True
        self.managed_values = {} if flow.normal is None else flow.normal

    @staticmethod
    def _merge_managed_values(
        states: list[Mapping[_ManagedKey, frozenset[str]]],
    ) -> _ManagedState:
        if not states:
            return {}
        first = states[0]
        return {
            key: value
            for key, value in first.items()
            if all(state.get(key) == value for state in states[1:])
        }

    def _terminate_flow(self, kind: str) -> None:
        collector = self._flow_collector
        if collector is None:
            return
        getattr(collector, kind).append(dict(self.managed_values))
        collector.terminated = True

    def visit_Break(self, node: ast.Break) -> None:
        self._terminate_flow("breaks")

    def visit_Continue(self, node: ast.Continue) -> None:
        self._terminate_flow("continues")

    def visit_Return(self, node: ast.Return) -> None:
        if node.value is not None:
            self.visit(node.value)
        self._terminate_flow("returns")

    def visit_Raise(self, node: ast.Raise) -> None:
        if node.exc is not None:
            self.visit(node.exc)
        if node.cause is not None:
            self.visit(node.cause)
        self._terminate_flow("raises")

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        initial = dict(self.managed_values)
        body = self._visit_block_flow_from(node.body, initial)
        other = self._visit_block_flow_from(node.orelse, initial)
        normals = [state for state in (body.normal, other.normal) if state is not None]
        self._apply_flow(
            _BlockFlow(
                normal=self._merge_managed_values(normals) if normals else None,
                breaks=body.breaks + other.breaks,
                continues=body.continues + other.continues,
                returns=body.returns + other.returns,
                raises=body.raises + other.raises,
            )
        )

    def _visit_try(self, node: ast.Try | ast.TryStar) -> None:
        initial = dict(self.managed_values)
        body, exception_states = self._visit_block_with_checkpoints_from(
            node.body, initial
        )
        normal_flow = (
            self._visit_block_flow_from(node.orelse, body.normal)
            if body.normal is not None
            else _BlockFlow(None, [], [], [], [])
        )
        handler_flows: list[_BlockFlow] = []
        exception_state = self._merge_managed_values([*exception_states, *body.raises])
        for handler in node.handlers:
            handler_initial = dict(exception_state)
            if handler.name:
                handler_initial.pop((tuple(self.scope), handler.name), None)
            outer = self.managed_values
            self.managed_values = handler_initial
            try:
                if handler.type is not None:
                    self.visit(handler.type)
                handler_flows.append(
                    self._visit_block_flow_from(handler.body, self.managed_values)
                )
            finally:
                self.managed_values = outer
        flows = [normal_flow, *handler_flows]
        normals = [flow.normal for flow in flows if flow.normal is not None]
        combined = _BlockFlow(
            normal=self._merge_managed_values(normals) if normals else None,
            breaks=body.breaks + [state for flow in flows for state in flow.breaks],
            continues=body.continues
            + [state for flow in flows for state in flow.continues],
            returns=body.returns + [state for flow in flows for state in flow.returns],
            raises=body.raises + [state for flow in flows for state in flow.raises],
        )
        if node.finalbody:
            combined = self._apply_finally_to_flow(combined, node.finalbody)
        self._apply_flow(combined)

    def _apply_finally_to_flow(
        self,
        incoming: _BlockFlow,
        finalbody: list[ast.stmt],
    ) -> _BlockFlow:
        incoming_by_kind: dict[str, list[_ManagedState]] = {
            "normal": [] if incoming.normal is None else [incoming.normal],
            "breaks": incoming.breaks,
            "continues": incoming.continues,
            "returns": incoming.returns,
            "raises": incoming.raises,
        }
        outgoing: dict[str, list[_ManagedState]] = {
            "normal": [],
            "breaks": [],
            "continues": [],
            "returns": [],
            "raises": [],
        }
        first_execution = True
        for incoming_kind, states in incoming_by_kind.items():
            distinct: list[_ManagedState] = []
            for state in states:
                if state not in distinct:
                    distinct.append(state)
            for state in distinct:
                call_start = len(self.calls)
                sdk_start = len(self.sdk_calls)
                final = self._visit_block_flow_from(finalbody, state)
                if not first_execution:
                    del self.calls[call_start:]
                    repeated_sdk_calls = self.sdk_calls[sdk_start:]
                    del self.sdk_calls[sdk_start:]
                    for issue, consumers in repeated_sdk_calls:
                        for index, (known_issue, known_consumers) in enumerate(
                            self.sdk_calls
                        ):
                            if known_issue == issue:
                                self.sdk_calls[index] = (
                                    known_issue,
                                    known_consumers & consumers,
                                )
                                break
                        else:
                            self.sdk_calls.append((issue, consumers))
                first_execution = False
                if final.normal is not None:
                    outgoing[incoming_kind].append(final.normal)
                outgoing["breaks"].extend(final.breaks)
                outgoing["continues"].extend(final.continues)
                outgoing["returns"].extend(final.returns)
                outgoing["raises"].extend(final.raises)
        normals = outgoing["normal"]
        return _BlockFlow(
            normal=self._merge_managed_values(normals) if normals else None,
            breaks=outgoing["breaks"],
            continues=outgoing["continues"],
            returns=outgoing["returns"],
            raises=outgoing["raises"],
        )

    def visit_Try(self, node: ast.Try) -> None:
        self._visit_try(node)

    def visit_TryStar(self, node: ast.TryStar) -> None:
        self._visit_try(node)

    def _visit_loop(
        self,
        *,
        body: list[ast.stmt],
        orelse: list[ast.stmt],
        iteration_initial: Mapping[_ManagedKey, frozenset[str]] | None = None,
    ) -> None:
        initial = dict(self.managed_values)
        body_flow = self._visit_block_flow_from(
            body,
            initial if iteration_initial is None else iteration_initial,
        )
        normal_iterations = [
            state
            for state in [body_flow.normal, *body_flow.continues]
            if state is not None
        ]
        exits: list[Mapping[_ManagedKey, frozenset[str]]] = list(body_flow.breaks)
        else_flows: list[_BlockFlow] = []
        if orelse:
            for state in [initial, *normal_iterations]:
                flow = self._visit_block_flow_from(orelse, state)
                else_flows.append(flow)
                if flow.normal is not None:
                    exits.append(flow.normal)
        else:
            exits.extend([initial, *normal_iterations])
        self._apply_flow(
            _BlockFlow(
                normal=self._merge_managed_values(exits) if exits else None,
                breaks=[state for flow in else_flows for state in flow.breaks],
                continues=[state for flow in else_flows for state in flow.continues],
                returns=body_flow.returns
                + [state for flow in else_flows for state in flow.returns],
                raises=body_flow.raises
                + [state for flow in else_flows for state in flow.raises],
            )
        )

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.target)
        self.visit(node.iter)
        iteration_initial = dict(self.managed_values)
        for name in self._bound_names(node.target):
            iteration_initial.pop((tuple(self.scope), name), None)
        self._visit_loop(
            body=node.body,
            orelse=node.orelse,
            iteration_initial=iteration_initial,
        )

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.visit(node.target)
        self.visit(node.iter)
        iteration_initial = dict(self.managed_values)
        for name in self._bound_names(node.target):
            iteration_initial.pop((tuple(self.scope), name), None)
        self._visit_loop(
            body=node.body,
            orelse=node.orelse,
            iteration_initial=iteration_initial,
        )

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        self._visit_loop(body=node.body, orelse=node.orelse)

    @staticmethod
    def _match_pattern_is_irrefutable(pattern: ast.pattern) -> bool:
        if isinstance(pattern, ast.MatchAs):
            return (
                pattern.pattern is None
                or _HTTPVisitor._match_pattern_is_irrefutable(pattern.pattern)
            )
        if isinstance(pattern, ast.MatchOr):
            return any(
                _HTTPVisitor._match_pattern_is_irrefutable(item)
                for item in pattern.patterns
            )
        return False

    @staticmethod
    def _bound_names(node: ast.AST) -> frozenset[str]:
        names: set[str] = set()
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, (ast.Tuple, ast.List)):
            for item in node.elts:
                names.update(_HTTPVisitor._bound_names(item))
        elif isinstance(node, ast.Starred):
            names.update(_HTTPVisitor._bound_names(node.value))
        elif isinstance(node, ast.MatchAs):
            if node.name is not None:
                names.add(node.name)
            if node.pattern is not None:
                names.update(_HTTPVisitor._bound_names(node.pattern))
        elif isinstance(node, ast.MatchStar):
            if node.name is not None:
                names.add(node.name)
        elif isinstance(node, ast.MatchMapping):
            if node.rest is not None:
                names.add(node.rest)
            for pattern in node.patterns:
                names.update(_HTTPVisitor._bound_names(pattern))
        elif isinstance(node, ast.MatchClass):
            for pattern in (*node.patterns, *node.kwd_patterns):
                names.update(_HTTPVisitor._bound_names(pattern))
        elif isinstance(node, (ast.MatchSequence, ast.MatchOr)):
            for pattern in node.patterns:
                names.update(_HTTPVisitor._bound_names(pattern))
        return frozenset(names)

    def visit_Match(self, node: ast.Match) -> None:
        self.visit(node.subject)
        initial = dict(self.managed_values)
        flows: list[_BlockFlow] = []
        fallbacks: list[_ManagedState] = [initial]
        for case in node.cases:
            if not fallbacks:
                break
            pre_pattern = fallbacks
            outer = self.managed_values
            self.managed_values = self._merge_managed_values(fallbacks)
            try:
                self.visit(case.pattern)
                for name in self._bound_names(case.pattern):
                    self.managed_values.pop((tuple(self.scope), name), None)
                if case.guard is not None:
                    self.visit(case.guard)
                captured = dict(self.managed_values)
                flows.append(self._visit_block_flow_from(case.body, captured))
            finally:
                self.managed_values = outer
            fallbacks = []
            if not self._match_pattern_is_irrefutable(case.pattern):
                fallbacks.extend(pre_pattern)
            if case.guard is not None:
                fallbacks.append(captured)
        normals = [flow.normal for flow in flows if flow.normal is not None]
        normals.extend(fallbacks)
        self._apply_flow(
            _BlockFlow(
                normal=self._merge_managed_values(normals) if normals else None,
                breaks=[state for flow in flows for state in flow.breaks],
                continues=[state for flow in flows for state in flow.continues],
                returns=[state for flow in flows for state in flow.returns],
                raises=[state for flow in flows for state in flow.raises],
            )
        )

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
        if name in _SDK_GUARD_CALLS and node.args:
            consumer = self._constant_string(node.args[0])
            if consumer is not None:
                self.consumers.add(consumer)
                self.managed_values[
                    (tuple(self.scope), f"@sdk_guard:{consumer}")
                ] = frozenset({consumer})

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
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "client"
            and (
                (
                    isinstance(node.func.value, ast.Call)
                    and _qualname(node.func.value.func, self.aliases)
                    == "boto3.session.Session"
                )
                or (
                    isinstance(node.func.value, ast.Name)
                    and _BOTO3_SESSION_PROVENANCE
                    in self._lookup_managed_value(node.func.value.id)
                )
            )
        ):
            sdk_name = "boto3.session.Session.client"
        if sdk_name and sdk_name.startswith("openprogram.providers."):
            sdk_name = ".".join(sdk_name.split(".")[-2:])
        if sdk_name in _SDK_CONSTRUCTORS or short in _SDK_CONSTRUCTORS:
            issue = RuntimeHTTPCall(self.path, node.lineno, f"sdk.{sdk_name}")
            injected_consumers: set[str] = set()
            for keyword in node.keywords:
                if keyword.arg in _SDK_INJECTION_KEYWORDS or keyword.arg is None:
                    injected_consumers.update(self._managed_consumers(keyword.value))
            self.sdk_calls.append(
                (
                    issue,
                    frozenset(injected_consumers),
                    self._active_sdk_guards(),
                )
            )
        self.generic_visit(node)


def _source_files(root: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    for directory, names, filenames in os.walk(root):
        current = Path(directory)
        if current != root and (current / ".git").exists():
            names.clear()
            continue
        names[:] = [
            name
            for name in names
            if name != "__pycache__"
            and not name.startswith(".venv")
            and name != ".git"
        ]
        files.extend(
            current / name
            for name in filenames
            if name.endswith(".py") and not re.search(r" \d+\.py\Z", name)
        )
    return tuple(sorted(files))


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
    additional_roots: Mapping[str, Path] | None = None,
    exclusions: tuple[BoundaryExclusion, ...] = BOUNDARY_MANIFEST,
    registry: Mapping[str, object] = CONSUMER_REGISTRY,
) -> RuntimeHTTPInventory:
    root = Path(root)
    excluded = {item.path: item for item in exclusions}
    observed_exclusions: dict[tuple[str, str], int] = {}
    unregistered: list[RuntimeHTTPCall] = []
    consumers: set[str] = set()
    unmanaged: set[str] = set()
    observed_paths: set[str] = set()

    source_roots = [("", root)]
    source_roots.extend(
        (prefix.strip("/") + "/", Path(path))
        for prefix, path in (additional_roots or {}).items()
    )
    sources = (
        (prefix, source_root, source_path)
        for prefix, source_root in source_roots
        for source_path in _source_files(source_root)
    )
    for prefix, source_root, source_path in sources:
        relative = prefix + source_path.relative_to(source_root).as_posix()
        observed_paths.add(relative)
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
        for issue, injected_consumers, active_guards in visitor.sdk_calls:
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
                and consumer in active_guards
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
        if path not in observed_paths:
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
