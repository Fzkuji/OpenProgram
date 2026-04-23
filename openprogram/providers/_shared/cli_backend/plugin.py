"""CliBackendPlugin — plugin-owned defaults for a CLI-backed runtime.

Python port of ``references/openclaw/src/plugins/cli-backend.types.ts``.

A plugin bundles the backend ``CliBackendConfig`` together with provider-
specific hooks the generic ``CliRunner`` should call (text transforms,
system-prompt tweaks, auth-profile defaults, per-execution bridge). Every
field here mirrors openclaw's type except for Python naming conventions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal, Optional, Union

from .config import CliBackendConfig


CliBackendAuthEpochMode = Literal["combined", "profile-only"]
"""Session/auth epoch source policy.

- ``combined`` — legacy "host credential + auth profile" fingerprint
- ``profile-only`` — selected auth profile is the sole session owner
"""

CliBundleMcpMode = Literal[
    "claude-config-file",
    "codex-config-overrides",
    "gemini-system-settings",
]
"""How a backend wires OpenProgram's bundled MCP config into the CLI."""


@dataclass(frozen=True)
class PluginTextReplacement:
    """A single from→to text rewrite. ``from_`` can be a plain string or
    a compiled ``re.Pattern``; runner decides based on type."""

    from_: object  # str | re.Pattern
    to: str


@dataclass(frozen=True)
class PluginTextTransforms:
    """Bidirectional rewrites applied around the CLI boundary."""

    input: tuple[PluginTextReplacement, ...] = ()
    """Rewrites applied to system/user prompts before the CLI sees them."""

    output: tuple[PluginTextReplacement, ...] = ()
    """Rewrites applied to streamed assistant text before consumers see it."""


@dataclass(frozen=True)
class PrepareExecutionContext:
    """Input to ``CliBackendPlugin.prepare_execution``.

    Forwarded from core at the moment a run is about to launch. Lets the
    plugin stage a private CLI home, write an auth bridge file, etc.
    """

    workspace_dir: str
    provider: str
    model_id: str
    agent_dir: Optional[str] = None
    auth_profile_id: Optional[str] = None
    # ``config`` intentionally left untyped here — it's the whole
    # OpenProgram config object, which we don't want to circular-import.
    config: Optional[object] = None


@dataclass(frozen=True)
class PreparedExecution:
    """Output of ``CliBackendPlugin.prepare_execution``.

    Merged by the runner into the final subprocess env. ``cleanup`` runs
    after the subprocess exits (best-effort).
    """

    env: Optional[dict[str, str]] = None
    clear_env: Optional[tuple[str, ...]] = None
    cleanup: Optional[Callable[[], Awaitable[None]]] = None


@dataclass(frozen=True)
class LiveTestConfig:
    """Opt-in metadata for the provider's live-smoke harness."""

    default_model_ref: Optional[str] = None
    default_image_probe: bool = False
    default_mcp_probe: bool = False
    docker_npm_package: Optional[str] = None
    docker_binary_name: Optional[str] = None


PrepareExecutionHook = Callable[
    [PrepareExecutionContext],
    Union[PreparedExecution, None, Awaitable[Optional[PreparedExecution]]],
]
"""Signature for prepare_execution. May be sync or async."""


TransformSystemPromptContext = dict
"""Loose dict carrying provider/model/agentId/systemPrompt — matches the
openclaw shape verbatim. We keep it as a dict to avoid a 7-field struct."""


TransformSystemPromptHook = Callable[[TransformSystemPromptContext], Optional[str]]
NormalizeConfigHook = Callable[[CliBackendConfig], CliBackendConfig]


@dataclass(frozen=True)
class CliBackendPlugin:
    """Plugin-owned defaults for one CLI backend.

    Concrete providers (Anthropic / Gemini CLI / Codex / OpenClaw CLI)
    each build one of these and register it. The generic ``CliRunner``
    reads it and nothing else — no per-provider subprocess code.
    """

    id: str
    """Provider id used in model refs, e.g. ``claude-cli/opus``."""

    config: CliBackendConfig
    """Default backend config. User overrides merge on top at load time."""

    live_test: Optional[LiveTestConfig] = None
    """Live-smoke harness metadata, if any."""

    bundle_mcp: bool = False
    """Whether to inject OpenProgram's bundled MCP config into the CLI."""

    bundle_mcp_mode: Optional[CliBundleMcpMode] = None
    """How to inject bundled MCP for this CLI (provider-specific strategy)."""

    normalize_config: Optional[NormalizeConfigHook] = None
    """Plugin-owned config normalizer applied after user overrides."""

    transform_system_prompt: Optional[TransformSystemPromptHook] = None
    """Last-mile system-prompt rewrite (return None to keep as-is)."""

    text_transforms: Optional[PluginTextTransforms] = None
    """Input/output text transforms applied around the CLI boundary."""

    default_auth_profile_id: Optional[str] = None
    """Preferred auth-profile id when caller didn't specify one."""

    auth_epoch_mode: CliBackendAuthEpochMode = "combined"
    """Session invalidation policy. See ``CliBackendAuthEpochMode``."""

    prepare_execution: Optional[PrepareExecutionHook] = None
    """Async/sync hook to stage a per-run auth/config bridge, if needed."""


__all__ = [
    "CliBackendAuthEpochMode",
    "CliBackendPlugin",
    "CliBundleMcpMode",
    "LiveTestConfig",
    "PluginTextReplacement",
    "PluginTextTransforms",
    "PrepareExecutionContext",
    "PreparedExecution",
    "PrepareExecutionHook",
    "NormalizeConfigHook",
    "TransformSystemPromptHook",
]
