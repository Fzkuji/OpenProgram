"""Single source of truth for user-editable settings.

Every surface that views or edits settings — the ``setup`` CLI sections,
the ``openprogram ports`` command, the TUI settings screen, the web pages
— renders from the one ``SETTINGS`` list here and writes through
``set_setting`` instead of poking the config dict directly. Adding a
setting = one ``SettingSpec``, and it shows up everywhere.

Modelled on openclaw's ``parseConfigPath`` / ``setConfigValueAtPath``
(dot-path access with a prototype-pollution blocklist) and opencode's
typed config service. Per-setting ``apply`` says whether a change takes
effect immediately (``"live"``) or only on the next worker/web start
(``"next_start"``), so the editor can tell the user the truth instead of
implying everything is instant.

See ``docs/design/cli/redesign.md``.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from decimal import Decimal, InvalidOperation
import ipaddress
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

from openprogram import setup as _setup

if TYPE_CHECKING:
    from openprogram.security.safe_http import OutboundSecurityConfig

# openclaw's isBlockedObjectKey — never let a dot-path write reach these.
_BLOCKED_KEYS = frozenset({"__proto__", "constructor", "prototype"})

APPLY_LIVE = "live"
APPLY_NEXT_START = "next_start"

_METADATA_ADDRESSES = tuple(
    ipaddress.ip_address(value)
    for value in (
        "100.100.100.200",
        "169.254.169.254",
        "169.254.170.2",
        "fd00:ec2::254",
    )
)
_METADATA_HOSTS = frozenset(
    {
        "metadata.google.internal",
        "metadata.azure.internal",
        "instance-data.ec2.internal",
    }
)
_LINK_LOCAL_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
)


class OwnerURLExceptionSetting(BaseModel):
    """One owner-approved, consumer-scoped private-network exception."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    consumer: str = Field(min_length=1, max_length=128)
    origin: str | None = Field(default=None, max_length=2048)
    cidr: str | None = Field(default=None, max_length=64)

    @field_validator("consumer")
    @classmethod
    def _registered_exception_consumer(cls, value: str) -> str:
        from openprogram.security.safe_http import CONSUMER_REGISTRY

        spec = CONSUMER_REGISTRY.get(value)
        if spec is None or not spec.allow_owner_exceptions:
            raise ValueError("consumer does not allow owner URL exceptions")
        return value

    @field_validator("origin")
    @classmethod
    def _exact_origin(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from openprogram.security.url_policy import normalize_url

        parsed = urlsplit(value)
        normalized = normalize_url(value)
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or value.strip().startswith(".")
            or "*" in value
        ):
            raise ValueError("origin must contain only an exact authority")
        if normalized.hostname in _METADATA_HOSTS:
            raise ValueError("metadata origin is forbidden")
        try:
            address = ipaddress.ip_address(normalized.hostname)
        except ValueError:
            pass
        else:
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                address = address.ipv4_mapped
            if address.is_link_local or address in _METADATA_ADDRESSES:
                raise ValueError("metadata or link-local origin is forbidden")
        return normalized.origin

    @field_validator("cidr")
    @classmethod
    def _bounded_network(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            network = ipaddress.ip_network(value, strict=False)
        except ValueError as exc:
            raise ValueError("invalid CIDR") from exc
        if network.prefixlen == 0:
            raise ValueError("default-route CIDR is forbidden")
        if network.is_global or network.is_multicast:
            raise ValueError("owner CIDR must be private or local")
        if any(
            network.version == blocked.version and network.overlaps(blocked)
            for blocked in _LINK_LOCAL_NETWORKS
        ):
            raise ValueError("link-local CIDR is forbidden")
        if any(
            address.version == network.version and address in network
            for address in _METADATA_ADDRESSES
        ):
            raise ValueError("metadata CIDR is forbidden")
        return network.with_prefixlen

    @model_validator(mode="after")
    def _exactly_one_target(self):
        if (self.origin is None) == (self.cidr is None):
            raise ValueError("exception requires exactly one of origin or cidr")
        return self


class PolicyProxySetting(BaseModel):
    """Owner assertion for a proxy that enforces target URL policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str = Field(max_length=2048)
    enforces_target_policy: StrictBool

    @field_validator("url")
    @classmethod
    def _exact_proxy_origin(cls, value: str) -> str:
        from openprogram.security.url_policy import normalize_url

        parsed = urlsplit(value)
        normalized = normalize_url(value)
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("proxy URL must contain only an exact authority")
        if normalized.hostname in _METADATA_HOSTS:
            raise ValueError("metadata proxy is forbidden")
        try:
            address = ipaddress.ip_address(normalized.hostname)
        except ValueError:
            pass
        else:
            if address.is_link_local or address in _METADATA_ADDRESSES:
                raise ValueError("metadata or link-local proxy is forbidden")
        return normalized.origin

    @model_validator(mode="after")
    def _requires_enforcement_assertion(self):
        if self.enforces_target_policy is not True:
            raise ValueError("policy proxy must assert target-policy enforcement")
        return self


class OutboundURLSettings(BaseModel):
    """Strict, immutable owner configuration for Runtime URL access."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    exceptions: tuple[OwnerURLExceptionSetting, ...] = Field(
        default=(), max_length=64
    )
    policy_proxy: PolicyProxySetting | None = None

    @model_validator(mode="after")
    def _unique_exceptions(self):
        keys = {
            (item.consumer, item.origin, item.cidr)
            for item in self.exceptions
        }
        if len(keys) != len(self.exceptions):
            raise ValueError("duplicate owner URL exception")
        return self

    def security_for(self, consumer: str) -> OutboundSecurityConfig:
        from openprogram.security.safe_http import (
            CONSUMER_REGISTRY,
            OutboundSecurityConfig,
            PolicyProxyConfig,
        )
        from openprogram.security.url_policy import OwnerURLException

        if consumer not in CONSUMER_REGISTRY:
            raise KeyError(consumer)
        exceptions = tuple(
            OwnerURLException(
                consumer=item.consumer,
                origin=item.origin,
                network=(
                    ipaddress.ip_network(item.cidr)
                    if item.cidr is not None
                    else None
                ),
            )
            for item in self.exceptions
            if item.consumer == consumer
        )
        if self.policy_proxy is not None:
            exceptions += (
                OwnerURLException(
                    consumer="runtime.local_probe",
                    origin=self.policy_proxy.url,
                ),
            )
        proxy = (
            PolicyProxyConfig(
                url=self.policy_proxy.url,
                enforces_target_policy=True,
            )
            if self.policy_proxy is not None
            else None
        )
        return OutboundSecurityConfig(
            owner_exceptions=exceptions,
            policy_proxy=proxy,
        )


def parse_outbound_url_settings(value: Any) -> OutboundURLSettings:
    """Parse owner URL policy without reflecting rejected input in errors."""

    try:
        return OutboundURLSettings.model_validate(value)
    except Exception:
        raise ValueError("invalid outbound URL security configuration") from None


def load_outbound_security_config(
    consumer: str,
    *,
    config: dict[str, Any] | None = None,
) -> OutboundSecurityConfig:
    cfg = _setup._read_config() if config is None else config
    security = cfg.get("security", {})
    if not isinstance(security, dict):
        raise ValueError("invalid outbound URL security configuration")
    raw = security.get("outbound_url", {})
    return parse_outbound_url_settings(raw).security_for(consumer)


@dataclass(frozen=True)
class SettingSpec:
    key: str                              # stable id, e.g. "ui.web_port"
    path: tuple[str, ...]                 # dot-path into config.json
    group: str                            # "Ports" | "Memory" | ...
    label: str
    widget: str                           # "number" | "toggle" | "enum"
    apply: str                            # APPLY_LIVE | APPLY_NEXT_START
    default: Any = None
    choices: Optional[Callable[[], list[str]]] = None   # enum options, lazy
    validate: Optional[Callable[[Any], Optional[str]]] = None  # -> error|None
    help: str = ""
    secret: bool = False
    minimum: int | None = None


# validators / choice providers


def _validate_port(v: Any) -> Optional[str]:
    try:
        p = int(v)
    except (TypeError, ValueError):
        return "must be a whole number"
    if not 1 <= p <= 65535:
        return "must be in 1–65535"
    return None


def _validate_web_origins(value: Any) -> Optional[str]:
    if not isinstance(value, list) or not all(
        isinstance(origin, str) for origin in value
    ):
        return "must be a JSON list of Origin strings"
    from openprogram.backend_endpoint import OwnerAuthError, canonicalize_origin

    for origin in value:
        try:
            canonicalize_origin(origin)
        except OwnerAuthError as exc:
            return f"invalid Origin {origin!r}: {exc}"
    return None


def _validate_mcp_exposed_tools(value: Any) -> Optional[str]:
    if not isinstance(value, list) or not all(
        isinstance(name, str) for name in value
    ):
        return "must be a JSON list of tool-name strings"
    return None


def _validate_web_bind_host(value: Any) -> Optional[str]:
    from openprogram.backend_endpoint import OwnerAuthError, canonicalize_bind_host

    try:
        canonicalize_bind_host(str(value))
    except OwnerAuthError as exc:
        return str(exc)
    return None


def _search_choices() -> list[str]:
    """``auto`` + every registered web_search provider. Best-effort: an
    import failure degrades to just ``auto`` rather than breaking the
    whole settings read."""
    try:
        from openprogram.programs.tools.web.web_search.registry import registry as _wsr
        import openprogram.programs.tools.web.web_search.providers  # noqa: F401
        names = [getattr(p, "name", "") for p in _wsr.all()]
        return ["auto"] + [n for n in names if n]
    except Exception:
        return ["auto"]


def _output_styles() -> list[str]:
    """Every discovered output style, ``default`` first. Best-effort: a
    broken discovery degrades to just ``default``."""
    try:
        from openprogram.context.output_style import DEFAULT_STYLE, list_styles
        names = sorted(n for n in list_styles() if n != DEFAULT_STYLE)
        return [DEFAULT_STYLE] + names
    except Exception:
        return ["default"]


def _update_channels() -> list[str]:
    """Channel names known to ``openprogram upgrade``. Best-effort — a
    broken import degrades to the built-in default rather than breaking
    the whole settings read."""
    try:
        from openprogram.cli.commands.upgrade import CHANNELS
        return sorted(CHANNELS)
    except Exception:
        return ["stable"]


def _sandbox_modes() -> tuple[str, ...]:
    from openprogram.sandbox import MODES
    return MODES


def _sandbox_unavailable_policy() -> tuple[str, ...]:
    from openprogram.sandbox import UNAVAILABLE_POLICY
    return UNAVAILABLE_POLICY


def _sandbox_default_deny_read() -> tuple[str, ...]:
    from openprogram.sandbox import DEFAULT_DENY_READ
    return DEFAULT_DENY_READ


def _sandbox_default_deny_write() -> tuple[str, ...]:
    from openprogram.sandbox import DEFAULT_DENY_WRITE
    return DEFAULT_DENY_WRITE


def _coerce(widget: str, value: Any) -> Any:
    if widget == "number":
        if isinstance(value, bool):
            raise ValueError("boolean is not a whole number")
        if isinstance(value, float) and not value.is_integer():
            raise ValueError("fractional value is not a whole number")
        if isinstance(value, str):
            stripped = value.strip()
            if not re.fullmatch(r"[+-]?\d+", stripped):
                raise ValueError("value is not a whole number")
            value = stripped
        return int(value)
    if widget == "toggle":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if widget == "json":
        import json
        return json.loads(value) if isinstance(value, str) else value
    return str(value)


def _validate_limit(v: Any) -> Optional[str]:
    """A collaboration budget: any whole number ≥ 0, where 0 = no limit."""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return "must be a whole number (0 = no limit)"
    if n < 0:
        return "must be 0 (no limit) or a positive whole number"
    return None


def _validate_positive_optional(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
        return "must be empty (inherit/unlimited) or a positive whole number"
    return None


def _validate_positive_decimal_optional(v: Any) -> Optional[str]:
    if v is None:
        return None
    if not isinstance(v, str):
        return "must be a decimal string"
    try:
        value = Decimal(v)
    except InvalidOperation:
        return "must be a decimal string"
    if not value.is_finite() or value <= 0 or value.as_tuple().exponent < -6:
        return "must be positive with at most 6 decimal places"
    return None


def _validate_memory_writer_trigger(v: Any) -> Optional[str]:
    if v not in {8_000, 16_000, 32_000}:
        return "must be one of 8000, 16000, 32000"
    return None


def _validate_memory_top_k(v: Any) -> Optional[str]:
    if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 10:
        return "must be a whole number in 1–10"
    return None


def _validate_memory_recent_limit(v: Any) -> Optional[str]:
    if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 500:
        return "must be a whole number in 1–500"
    return None


def _validate_hooks(v: Any) -> Optional[str]:
    if not isinstance(v, dict):
        return 'must be a JSON object: {"<event>": [{"command": "...", "timeout": 60}]}'
    for event, entries in v.items():
        if not isinstance(entries, list):
            return f"{event}: must map to a list of entries"
        for e in entries:
            if not isinstance(e, dict) or not isinstance(e.get("command"), str) \
                    or not e["command"].strip():
                return f'{event}: each entry needs a non-empty "command" string'
            t = e.get("timeout")
            if t is not None and (not isinstance(t, int) or isinstance(t, bool)
                                  or t <= 0):
                return f"{event}: timeout must be a positive whole number of seconds"
    return None


def _validate_outbound_url_settings(value: Any) -> Optional[str]:
    try:
        parse_outbound_url_settings(value)
    except ValueError:
        return "invalid outbound URL security configuration"
    return None


# the registry

SETTINGS: list[SettingSpec] = [
    SettingSpec(
        key="ui.web_port", path=("ui", "web_port"), group="Ports",
        minimum=1,
        label="Frontend port", widget="number",
        apply=APPLY_NEXT_START, default=_setup.DEFAULT_WEB_PORT,
        validate=_validate_port,
        help="The port the web UI itself is served on — the address you open "
             "in the browser. Must differ from the backend port.",
    ),
    SettingSpec(
        key="ui.open_browser", path=("ui", "open_browser"), group="Ports",
        label="Auto-open browser", widget="toggle",
        apply=APPLY_NEXT_START, default=True,
        help="When you run `openprogram web`, also pop open a browser window "
             "pointed at the UI. Turn off to start the server only and open "
             "the address yourself (e.g. on a headless server).",
    ),
    SettingSpec(
        key="web.host", path=("web", "host"), group="Ports",
        label="Bind address", widget="text",
        apply=APPLY_NEXT_START, default="127.0.0.1",
        validate=_validate_web_bind_host,
        help="Network interface the server listens on. The default only "
             "accepts connections from this machine. Setting 0.0.0.0 exposes "
             "the authenticated UI to other interfaces and requires at least "
             "one exact web.allowed_origins entry. Prefer HTTPS through a "
             "same-host reverse proxy for any untrusted network.",
    ),
    SettingSpec(
        key="web.allowed_origins", path=("web", "allowed_origins"),
        group="Ports", label="Allowed browser origins", widget="json",
        apply=APPLY_NEXT_START, default=[],
        validate=_validate_web_origins,
        help="Exact browser Origins allowed to address this OpenProgram "
             "instance. Each value is scheme://host[:port], such as "
             "https://agent.example.com. This is a request-Origin and Host "
             "allowlist, not a CORS list for cross-origin frontends.",
    ),
    SettingSpec(
        key="mcp_server.exposed_tools",
        path=("mcp_server", "exposed_tools"),
        group="MCP server",
        label="Exposed Runtime tools",
        widget="json",
        apply=APPLY_NEXT_START,
        default=[],
        validate=_validate_mcp_exposed_tools,
        help="Runtime tools available to authenticated MCP clients. Empty by "
             "default; changes apply on the next server start.",
    ),
    SettingSpec(
        key="security.outbound_url",
        path=("security", "outbound_url"),
        group="Security",
        label="Outbound URL security",
        widget="json",
        apply=APPLY_LIVE,
        default={"exceptions": []},
        validate=_validate_outbound_url_settings,
        help="Owner-only exact Origin or CIDR exceptions for declared "
             "configured-service consumers, plus an optional policy proxy "
             "that explicitly asserts target-policy enforcement.",
    ),
    SettingSpec(
        key="search.default_provider", path=("search", "default_provider"),
        group="Search", label="Default web-search provider", widget="enum",
        apply=APPLY_LIVE, default="auto", choices=_search_choices,
        help="`auto` picks the highest-priority configured provider.",
    ),
    SettingSpec(
        key="memory.backend", path=("memory", "backend"), group="Memory",
        label="Memory backend", widget="enum", apply=APPLY_NEXT_START,
        default="local", choices=lambda: ["local", "none"],
        help="`local` = on-disk memory tool; `none` = disabled.",
    ),
    SettingSpec(
        key="memory.writer.model", path=("memory", "writer", "model"),
        group="Memory", label="Memory writer model", widget="text",
        apply=APPLY_LIVE, default="",
        help="Empty uses the default chat agent's provider and model. Set "
             "provider/model to override only background memory writing.",
    ),
    SettingSpec(
        key="memory.writer.enabled", path=("memory", "writer", "enabled"),
        group="Memory", label="Automatic writing", widget="toggle",
        apply=APPLY_LIVE, default=True,
        help="Turn completed conversations into Topic records in the background.",
    ),
    SettingSpec(
        key="memory.writer.trigger_tokens",
        path=("memory", "writer", "trigger_tokens"),
        group="Memory", label="Write frequency", widget="number",
        apply=APPLY_LIVE, default=16_000,
        validate=_validate_memory_writer_trigger,
        help="Conversation tokens accumulated before a background write.",
    ),
    SettingSpec(
        key="memory.retrieval.method",
        path=("memory", "retrieval", "method"),
        group="Memory", label="Recall method", widget="enum",
        apply=APPLY_LIVE, default="bm25",
        choices=lambda: ["agent", "bm25", "embedding", "hybrid"],
        help="Retrieval used for automatic recall and Memory search.",
    ),
    SettingSpec(
        key="memory.retrieval.top_k",
        path=("memory", "retrieval", "top_k"),
        group="Memory", label="Recall depth", widget="number",
        apply=APPLY_LIVE, default=5, validate=_validate_memory_top_k,
        help="Maximum matching records added automatically to a turn.",
    ),
    SettingSpec(
        key="memory.retrieval.include_sources",
        path=("memory", "retrieval", "include_sources"),
        group="Memory", label="Search Source evidence", widget="toggle",
        apply=APPLY_LIVE, default=True,
        help="Include archived evidence alongside curated Topic records.",
    ),
    SettingSpec(
        key="memory.core.inject", path=("memory", "core", "inject"),
        group="Memory", label="Core Memory in every chat", widget="toggle",
        apply=APPLY_LIVE, default=True,
        help="Inject the compact Core view into each system prompt.",
    ),
    SettingSpec(
        key="memory.recent.limit", path=("memory", "recent", "limit"),
        group="Memory", label="Recent view size", widget="number",
        apply=APPLY_LIVE, default=50,
        validate=_validate_memory_recent_limit,
        help="Latest records retained in the Recent derived view.",
    ),
    SettingSpec(
        key="record_replay.mode", path=("record_replay", "mode"),
        group="Recordings", label="Provider record/replay mode", widget="enum",
        apply=APPLY_NEXT_START, default="off",
        choices=lambda: ["off", "record", "replay"],
        help="Record or strictly replay all LLM provider calls on the next process start.",
    ),
    SettingSpec(
        key="record_replay.file", path=("record_replay", "file"),
        group="Recordings", label="Provider recording selector", widget="text",
        apply=APPLY_NEXT_START, default="",
        help="Managed recording ID or an explicit replay file path; record mode accepts IDs only.",
    ),
    SettingSpec(
        key="goal.max_turns", path=("goal", "max_turns"), group="Goal",
        label="Goal max rounds", widget="number",
        apply=APPLY_LIVE, default=None,
        validate=lambda v: (None if v in (None, "")
                            or str(v).lstrip("-").isdigit()
                            else "must be empty (default 150) or a "
                                 "whole number (0 or negative = "
                                 "unlimited)"),
        help="Upper bound on rounds one Goal Workflow may consume "
             "before it stops with status=capped. Empty (default) = "
             "150. Zero or a negative number = no cap (stop rules are "
             "then judge failures, idle-spin detection and /goal "
             "clear). Read when the Workflow starts; each run keeps "
             "the bound it started with.",
    ),
    SettingSpec(
        key="goal.judge_model", path=("goal", "judge_model"), group="Goal",
        label="Goal judge model", widget="text",
        apply=APPLY_LIVE, default="",
        help="Model the Goal completion judge runs on, as "
             "`provider/model` or a bare model name. Empty (default) = "
             "the session's picked model. Set a cheaper model to cut "
             "the per-round judgment cost.",
    ),
    SettingSpec(
        key="agent.output_style", path=("agent", "output_style"),
        group="Agent", label="Output style", widget="enum",
        apply=APPLY_LIVE, default="default", choices=_output_styles,
        help="Appends a block of guidance to the system prompt describing how "
             "replies should be written. `default` appends nothing. Drop a "
             "`<name>.md` file in `~/.openprogram/output-styles/` or "
             "`./output-styles/` to add your own.",
    ),
    SettingSpec(
        key="agent.max_spawn_depth", path=("agent", "max_spawn_depth"),
        group="Agent", label="Max spawn depth", widget="number",
        apply=APPLY_LIVE, default=1, validate=_validate_limit,
        help="How many generations of NEW agents one chain may create. "
             "1 (default) = you spawn workers and a worker does the work "
             "itself; 2 lets a worker spawn its own worker. 0 = no "
             "limit. Only creating an agent counts, so an agent that "
             "reads a worker's result can still create the next wave. A "
             "spawn past the limit is refused with a message telling the "
             "agent to do the work itself.",
    ),
    SettingSpec(
        key="agent.max_messages", path=("agent", "max_messages"),
        group="Agent", label="Max messages per chain", widget="number",
        apply=APPLY_LIVE, default=8, validate=_validate_limit,
        help="How many messages one collaboration chain may pass in "
             "total — spawns, send_message deliveries, and agent(to=…) "
             "dispatches all count, and a reply coming back counts too, "
             "so A↔B ping-pong stops at the ceiling. 8 by default, "
             "0 = no limit.",
    ),
    SettingSpec(
        key="agent.max_spawn_fanout", path=("agent", "max_spawn_fanout"),
        group="Agent", label="Max spawn fan-out", widget="number",
        apply=APPLY_LIVE, default=8, validate=_validate_limit,
        help="How many agents ONE turn may create, counted per (session, "
             "turn). The chain budgets bound how deep and how far a chain "
             "goes, not how wide one turn opens it. 8 by default = two "
             "widths of the task pool, so a turn can fill the pool and "
             "keep one wave queued; the next spawn is refused and points "
             "the agent at the ones it already has. 0 = no limit. Raise "
             "OPENPROGRAM_JOB_WORKERS with it or the extra agents only "
             "queue longer.",
    ),
    *[
        SettingSpec(
            key=f"agent.resource_limits.{name}",
            path=("agent", "resource_limits", name),
            group="Agent resources", label=label,
            widget="text" if name == "max_cost_usd" else "number",
            apply=APPLY_LIVE, default=None,
            minimum=1,
            validate=(
                _validate_positive_decimal_optional
                if name == "max_cost_usd" else _validate_positive_optional
            ),
            help="Empty means inherit or unlimited; non-empty values must be positive.",
        )
        for name, label in (
            ("max_live_per_session", "Max live jobs per session"),
            ("max_queued_per_session", "Max queued jobs per session"),
            ("max_jobs_per_session", "Max admitted jobs per session"),
            ("max_total_tokens", "Max total tokens"),
            ("max_cost_usd", "Max cost (USD)"),
            ("max_runtime_seconds", "Max runtime seconds"),
            ("idle_timeout_seconds", "Idle timeout seconds"),
        )
    ],
    SettingSpec(
        key="hooks", path=("hooks",), group="Hooks",
        label="Event hook commands", widget="json",
        apply=APPLY_NEXT_START, default={},
        validate=_validate_hooks,
        help='Shell commands subscribed to bus events, as {"<event>": '
             '[{"command": "...", "timeout": 60}]}. The event arrives as '
             "JSON on the command's stdin. Gate events (tool.before, "
             "turn.stop) follow the Claude Code hooks exit-code protocol: "
             "exit 0 allows, exit 2 denies with stderr as the reason, any "
             "other exit code is ignored (fail-open). Notify events "
             "(turn.start, turn.end, session.start, goal.update) run the "
             "command in the background and ignore the exit code. Timeout "
             "defaults to 60 seconds per command. Read once at worker "
             "start — restart to apply.",
    ),
    SettingSpec(
        key="sandbox.mode", path=("sandbox", "mode"), group="Sandbox",
        label="Sandbox mode", widget="enum", apply=APPLY_LIVE,
        default="auto", choices=lambda: list(_sandbox_modes()),
        help="`auto` uses the sandbox when its backend is available and "
             "otherwise keeps local commands usable without one. "
             "`danger-full-access` runs local model-driven commands with "
             "your full user authority. "
             "`workspace-write` applies the host-native sandbox "
             "(macOS sandbox-exec, Linux bubblewrap, or bubblewrap delegated "
             "to the default WSL2 distribution on Windows), restricts writes to "
             "the working directory and configured roots, blocks the paths "
             "listed under Blocked read paths, and disables network. Read per "
             "command, so a change applies to the next command everywhere "
             "— including background threads and spawned subprocesses.",
    ),
    SettingSpec(
        key="sandbox.writable_roots", path=("sandbox", "writable_roots"),
        group="Sandbox", label="Extra writable roots", widget="json",
        apply=APPLY_LIVE, default=[],
        validate=lambda v: (None if isinstance(v, list)
                            and all(isinstance(x, str) for x in v)
                            else "must be a JSON list of directory paths"),
        help="Directories a sandboxed command may write besides the "
             "working directory, as a JSON list. `~` is expanded.",
    ),
    SettingSpec(
        key="sandbox.deny_read", path=("sandbox", "deny_read"),
        group="Sandbox", label="Blocked read paths", widget="json",
        apply=APPLY_LIVE, default=list(_sandbox_default_deny_read()),
        validate=lambda v: (None if isinstance(v, list)
                            and all(isinstance(x, str) for x in v)
                            else "must be a JSON list of glob patterns"),
        help="Globs a sandboxed command cannot read, as a JSON list. "
             "Ships loaded with the credential paths, because a command "
             "that reads a key can still deliver it without any network: "
             "the memory writer's output returns to a later session's "
             "context. `**` matches any depth; on Linux a pattern "
             "containing a wildcard in the middle has no equivalent and "
             "is skipped, since bubblewrap masks paths rather than "
             "matching them. Protect sensitive Linux content with an "
             "exact path or a directory-level deny whose prefix is "
             "concrete, such as `/absolute/path/to/secrets/**`; do not "
             "rely on `**/.env` there.",
    ),
    SettingSpec(
        key="sandbox.allow_read", path=("sandbox", "allow_read"),
        group="Sandbox", label="Re-opened read paths", widget="json",
        apply=APPLY_LIVE, default=[],
        validate=lambda v: (None if isinstance(v, list)
                            and all(isinstance(x, str) for x in v)
                            else "must be a JSON list of paths"),
        help="Concrete paths re-opened inside a wider sandbox.deny_read "
             "entry. Narrower path wins; an equally-specific deny still "
             "blocks. Cannot open ~/.openprogram/auth or the agentics "
             "directory.",
    ),
    SettingSpec(
        key="sandbox.deny_write", path=("sandbox", "deny_write"),
        group="Sandbox", label="Blocked write paths", widget="json",
        apply=APPLY_LIVE, default=list(_sandbox_default_deny_write()),
        validate=lambda v: (None if isinstance(v, list)
                            and all(isinstance(x, str) for x in v)
                            else "must be a JSON list of glob patterns"),
        help="Globs a sandboxed command cannot write even inside the "
             "working directory, as a JSON list — the paths that arrange "
             "for code to run later outside the sandbox. The directory "
             "the function watcher auto-imports is always blocked and is "
             "not listed here, because a `.py` dropped there executes in "
             "the agent process within seconds. Empty otherwise: adding "
             "`**/.git/hooks/**` closes the next escape of that shape and "
             "also makes `git init` and `git clone` fail, since both "
             "write that directory.",
    ),
    SettingSpec(
        key="sandbox.network", path=("sandbox", "network"), group="Sandbox",
        label="Allow network inside the sandbox", widget="toggle",
        apply=APPLY_LIVE, default=False,
        help="Off means a sandboxed command has no network at all, which "
             "is what stops anything it read from leaving the machine. "
             "Turn it on and sandboxed package downloads work again, along "
             "with every other outbound connection.",
    ),
    SettingSpec(
        key="sandbox.pass_env", path=("sandbox", "pass_env"), group="Sandbox",
        label="Extra environment variables to pass", widget="json",
        apply=APPLY_LIVE, default=[],
        validate=lambda v: (None if isinstance(v, list)
                            and all(isinstance(x, str) for x in v)
                            else "must be a JSON list of variable names"),
        help="A sandboxed command inherits only PATH, HOME, SHELL, USER, "
             "LOGNAME, TERM, TMPDIR, TZ, PWD, LANG and LC_*, so API keys "
             "in your environment do not reach it. Name any additional "
             "variables here.",
    ),
    SettingSpec(
        key="sandbox.unavailable_policy", path=("sandbox", "unavailable_policy"),
        group="Sandbox", label="When the sandbox cannot run", widget="enum",
        apply=APPLY_LIVE, default="refuse",
        choices=lambda: list(_sandbox_unavailable_policy()),
        help="What happens when the mode is on but the platform backend is "
             "missing or cannot create its required isolation. `refuse` "
             "fails the command and says why. `warn` "
             "runs it unsandboxed and logs a warning — convenient, and "
             "the reason a security setting can end up doing nothing "
             "without anyone noticing.",
    ),
    SettingSpec(
        key="git.co_author", path=("git", "co_author"), group="Git",
        label="Co-author commits", widget="toggle", apply=APPLY_LIVE,
        default=True,
        help="Add the trailer `Co-Authored-By: <model> <noreply@openprogram.dev>` "
             "to commits OpenProgram writes, so the AI contribution is visible "
             "in `git log`. The model's display name is used when known, "
             "otherwise `OpenProgram`. Turning this off stops OpenProgram "
             "adding any attribution trailer.",
    ),
    SettingSpec(
        key="git.allow_remote_write", path=("git", "allow_remote_write"),
        group="Git", label="Allow pushes and pull requests", widget="toggle",
        apply=APPLY_LIVE, default=False,
        help="Let the `commit-push-pr` flow run `git push` and `gh pr create`. "
             "Off by default: branching, staging, and committing are local and "
             "reversible, but a push and a pull request are visible to other "
             "people and cannot be undone by resetting the local tree. Leave it "
             "off to keep the flow stopping at the commit, and use "
             "`git push --dry-run` to see what a push would send.",
    ),
    SettingSpec(
        key="update.channel", path=("update", "channel"), group="Updates",
        label="Update channel", widget="enum", apply=APPLY_LIVE,
        default="stable", choices=_update_channels,
        help="Which line of releases `openprogram upgrade` follows. "
             "Managed installs use the latest stable GitHub Release; "
             "source checkouts use origin/main.",
    ),
]

_BY_KEY = {s.key: s for s in SETTINGS}


# dot-path access (openclaw-style, blocklist-guarded)


def _get_at(cfg: dict, path: tuple[str, ...], default: Any) -> Any:
    node: Any = cfg
    for k in path:
        if not isinstance(node, dict) or k not in node:
            return default
        node = node[k]
    return node if node is not None else default


def _set_at(cfg: dict, path: tuple[str, ...], value: Any) -> None:
    # Reject a blocked key ANYWHERE in the path, not only the segment being
    # traversed — a non-terminal __proto__/constructor/prototype must never
    # slip through if path construction is ever relaxed.
    for k in path:
        if k in _BLOCKED_KEYS:
            raise ValueError(f"blocked config key: {k}")
    node = cfg
    for k in path[:-1]:
        nxt = node.get(k)
        if not isinstance(nxt, dict):
            nxt = {}
            node[k] = nxt
        node = nxt
    node[path[-1]] = value


# public API


def get_settings(
    session_id: str | None = None,
    *,
    key_prefix: str | None = None,
) -> list[dict]:
    """Resolved current settings for every spec, ready to render.

    Reads the config once; each row carries its value, group, label,
    widget, apply mode, resolved choices (for enums), and help. Secret
    values are returned as a bool ``set`` flag, never the value.
    """
    cfg = _setup._read_config()
    resolved_resources = None
    if session_id:
        from openprogram.agent.resource_governance import (
            ResourceLimits,
            resolve_resource_limits,
            session_resource_limits,
        )
        configured = ((cfg.get("agent") or {}).get("resource_limits") or {})
        resolved_resources = resolve_resource_limits(
            ResourceLimits.from_mapping(configured),
            session=session_resource_limits(session_id),
        )
    rows: list[dict] = []
    for s in SETTINGS:
        if key_prefix is not None and not s.key.startswith(key_prefix):
            continue
        raw = _get_at(cfg, s.path, s.default)
        row: dict = {
            "key": s.key,
            "group": s.group,
            "label": s.label,
            "widget": s.widget,
            "apply": s.apply,
            "help": s.help,
            "minimum": s.minimum if s.minimum is not None else (0 if s.widget == "number" else None),
        }
        if s.secret:
            row["set"] = bool(raw)
        elif s.widget == "json":
            # Serialized so the plain-text input renders/edits it as JSON.
            import json
            row["value"] = json.dumps(raw, ensure_ascii=False)
        else:
            row["value"] = raw
        if resolved_resources is not None and s.group == "Agent resources":
            resolved = resolved_resources.fields[s.key.rsplit(".", 1)[-1]]
            row["effective"] = resolved.effective
            row["source"] = resolved.source
        if s.choices is not None:
            try:
                row["choices"] = list(s.choices())
            except Exception:
                row["choices"] = []
        rows.append(row)

    # Providers are read-only status rows (✓ configured / ✗ not) with an
    # action — selecting one runs ``/login``. check_providers() is cheap
    # (~1ms; env + which checks). The web already has a full Providers tab;
    # this is the at-a-glance status for the TUI/CLI.
    if key_prefix is not None:
        return rows
    try:
        from openprogram.providers.registry import check_providers
        for name, st in check_providers().items():
            ok = bool(st.get("available"))
            rows.append({
                "key": f"providers.{name}",
                "group": "Providers",
                "label": name,
                "widget": "status",
                "apply": APPLY_LIVE,
                "help": f"{st.get('method', '')} · {'configured' if ok else 'not configured'}",
                "value": ok,
                "action": "/login",
            })
    except Exception:
        pass

    # Tools are dynamic — one live toggle per registered tool, ``on`` when
    # the user hasn't disabled it. Keyed ``tools.disabled.<name>`` so
    # set_setting can flip membership of ``tools.disabled``.
    try:
        from openprogram.programs import list_registered_agent_tools
        disabled = set((cfg.get("tools", {}) or {}).get("disabled", []) or [])
        for name in sorted(list_registered_agent_tools()):
            rows.append({
                "key": f"tools.disabled.{name}",
                "group": "Tools",
                "label": name,
                "widget": "toggle",
                "apply": APPLY_LIVE,
                "help": "",
                "value": name not in disabled,
            })
    except Exception:
        pass
    return rows


def set_setting(key: str, value: Any) -> dict:
    """Validate + persist one setting. Returns ``{applied, value[, note]}``
    on success or ``{error}`` on failure. ``applied`` is ``"live"`` or
    ``"next_start"``. Routes through the existing typed writer when one
    exists (``ui.*`` → ``set_ui_ports``), else a guarded dot-path write.
    """
    # Provider status rows are read-only (configure via /login or the web).
    if key.startswith("providers."):
        return {"error": "provider status is read-only — use /login or the Providers page"}

    # Dynamic per-tool toggles: ``on`` = enabled = not in tools.disabled.
    if key.startswith("tools.disabled."):
        name = key[len("tools.disabled."):]
        if not name:
            return {"error": "invalid tool key"}
        enable = _coerce("toggle", value)

        def _toggle_tool(cfg: dict) -> None:
            tools = cfg.setdefault("tools", {})
            disabled = set(tools.get("disabled", []) or [])
            disabled.discard(name) if enable else disabled.add(name)
            tools["disabled"] = sorted(disabled)

        _setup.update_config(_toggle_tool)
        return {"applied": APPLY_LIVE, "value": enable}

    spec = _BY_KEY.get(key)
    if spec is None:
        return {"error": f"unknown setting: {key}"}

    # Resource limits use an empty value for null/inherit. Cost stays a
    # decimal string at the API boundary; integer limits use normal numbers.
    try:
        if spec.key.startswith("agent.resource_limits.") and (
            value is None or (isinstance(value, str) and not value.strip())
        ):
            coerced = None
        elif spec.key == "agent.resource_limits.max_cost_usd":
            if not isinstance(value, str):
                raise ValueError("cost must be a decimal string")
            coerced = value.strip()
        else:
            coerced = _coerce(spec.widget, value)
    except (TypeError, ValueError):
        if spec.key == "security.outbound_url":
            return {
                "error": (
                    "Outbound URL security: "
                    "invalid outbound URL security configuration"
                )
            }
        return {"error": f"invalid value for {spec.label!r}: {value!r}"}

    if spec.validate is not None:
        err = spec.validate(coerced)
        if err:
            return {"error": f"{spec.label}: {err}"}

    if spec.widget == "enum" and spec.choices is not None:
        opts = list(spec.choices())
        if coerced not in opts:
            return {"error": f"{spec.label}: must be one of {', '.join(opts)}"}

    if spec.key == "memory.retrieval.method" and coerced in {
        "embedding", "hybrid",
    }:
        from openprogram.memory.retrieval.embedding import (
            default_model_is_available,
        )

        if not default_model_is_available():
            return {
                "error": (
                    f"{spec.label}: embedding model is not available locally"
                )
            }

    if spec.key in {"record_replay.mode", "record_replay.file"}:
        current = _setup._read_config().get("record_replay", {})
        candidate_mode = coerced if spec.key.endswith(".mode") else current.get("mode", "off")
        candidate_file = coerced if spec.key.endswith(".file") else current.get("file", "")
        if candidate_mode in {"record", "replay"} and not str(candidate_file).strip():
            return {"error": "record_replay.file is required when mode is record or replay"}
        if candidate_mode == "record" and (
            Path(str(candidate_file)).is_absolute()
            or "/" in str(candidate_file)
            or "\\" in str(candidate_file)
        ):
            return {"error": "record mode requires a managed recording ID"}
        if candidate_mode == "replay":
            try:
                from openprogram.providers.recording import resolve_recording_selector
                from openprogram.providers.replay import ReplayProvider

                ReplayProvider(resolve_recording_selector(str(candidate_file)))
            except (OSError, ValueError, RuntimeError) as exc:
                return {"error": f"invalid replay recording: {exc}"}

    if spec.key == "security.outbound_url":
        coerced = parse_outbound_url_settings(coerced).model_dump(
            mode="json", exclude_none=True
        )

    # route to the typed writer that already owns this key, else dot-path
    if spec.key == "ui.web_port":
        _setup.set_ui_ports(web_port=coerced)
    elif spec.key == "ui.open_browser":
        _setup.set_ui_ports(open_browser=coerced)
    elif spec.key == "search.default_provider":
        _setup.write_search_default_provider(None if coerced == "auto" else coerced)
    else:
        _setup.update_config(lambda cfg: _set_at(cfg, spec.path, coerced))

    result: dict = {"applied": spec.apply, "value": coerced}

    # surface a port conflict the way `openprogram ports` does, so the
    # editor can warn "that port is taken by <who>" right after saving.
    if spec.key == "ui.web_port":
        try:
            from openprogram._ports import describe_port_owner
            owner = describe_port_owner(coerced)
            if owner is not None and not owner.is_ours:
                result["note"] = f"port {coerced} is currently held by {owner.detail}"
        except Exception:
            pass

    return result
