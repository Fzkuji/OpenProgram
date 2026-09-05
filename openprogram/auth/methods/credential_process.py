"""Credential produced by shelling out to a helper command.

A small but important niche. Some providers expect the user to have a
vendor CLI (``aws configure``, corporate ``sso-helper``, enterprise
``token-fetcher``) that prints a fresh token on stdout. We can't OAuth
our way into those flows — the vendor requires their tool be the one
doing the dance — but we can still be good citizens: run the helper,
parse its output, and treat the result like an access token with a
short (configurable) cache.

Design calls:

  * Every call goes through a ``cache_seconds`` window so we don't fork
    the helper on every single request. Matches what ``aws`` and ``gcloud``
    do internally.
  * Helper runs under the *current account*'s subprocess HOME (see
    :class:`Account`) so corporate creds that bleed in via ``~/.aws/``
    don't cross account boundaries.
  * Helper stderr is captured and surfaced on failure, scrubbed through
    the diagnostics redactor first because a failing token helper often
    echoes the credential it was fetching; stdout is parsed
    as JSON or raw text per config. No shell interpolation — we take a
    ``list[str]`` argv so there's nothing to escape.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Literal

from ..types import (
    AuthCredentialProcessError,
    Credential,
    CredentialData,
    LoginMethod,
    LoginUi,
)


@dataclass
class CredentialProcessConfig:
    command: list[str]
    parses: Literal["json", "text"] = "json"
    # Which key path to extract when ``parses == "json"``. Same walking
    # convention as :mod:`.cli_import`.
    json_key_path: list[str] = field(default_factory=list)
    cache_seconds: int = 300
    # Working directory for the helper. Resolved relative to the current
    # account's home when None.
    cwd: str | None = None
    # Additional env vars for the helper. Account env + os.environ merge
    # in the Account layer; these layer on top.
    env: dict[str, str] = field(default_factory=dict)
    # Max wall time for one helper invocation. Too short → user sees
    # noise; too long → a hung helper hangs the agent. One minute is a
    # compromise biased toward "you probably want to fix your helper".
    timeout_seconds: float = 60.0


class CredentialProcessMethod(LoginMethod):
    """Runs a helper during login to verify it produces output.

    The login flow doesn't actually persist a cached token — it just
    records the helper invocation shape. Every API call later re-runs
    the helper (via ``CredentialData(kind="credential_process")``), subject to
    the ``cache_seconds`` de-dup window applied by :func:`token_for_payload`
    on the resolver's path.
    """

    method_id = "credential_process"

    def __init__(
        self,
        provider_id: str,
        config: CredentialProcessConfig,
        *,
        account_id: str = "default",
        metadata: dict | None = None,
    ) -> None:
        self.provider_id = provider_id
        self._cfg = config
        self._account_id = account_id
        self._metadata = dict(metadata or {})

    async def run(self, ui: LoginUi) -> Credential:
        # Smoke-test the helper once during login so the user isn't told
        # at their first API call that their helper doesn't work. We
        # don't keep the output — just check exit code + that it's
        # non-empty.
        await ui.show_progress(f"Running helper: {' '.join(self._cfg.command)} …")
        try:
            output = await _run_helper(self._cfg)
        except Exception as e:
            raise RuntimeError(f"helper failed during login smoke test: {e}") from e
        if not output.strip():
            raise RuntimeError("helper returned empty output during login smoke test")

        return Credential(
            provider_id=self.provider_id,
            account_id=self._account_id,
            kind="credential_process",
            payload=CredentialData(
                kind="credential_process",
                data={
                    "command": list(self._cfg.command),
                    "parses": self._cfg.parses,
                    "json_key_path": list(self._cfg.json_key_path),
                    "cache_seconds": self._cfg.cache_seconds,
                },
            ),
            source=f"{self.method_id}:{self.provider_id}",
            metadata=self._metadata,
        )


def config_from_payload(data: dict) -> CredentialProcessConfig:
    """Rebuild the helper config from a stored ``CredentialData.data``.

    Missing keys fall back to :class:`CredentialProcessConfig` defaults, so
    a credential written by an older/leaner producer (the web UI stores
    only ``command``) still runs with sane parsing + cache behaviour.
    """
    command = list(data.get("command") or [])
    if not command:
        raise AuthCredentialProcessError("credential_process credential has no command")
    defaults = CredentialProcessConfig(command=command)
    return CredentialProcessConfig(
        command=command,
        parses=data.get("parses") or defaults.parses,
        json_key_path=list(data.get("json_key_path") or []),
        cache_seconds=int(data.get("cache_seconds", defaults.cache_seconds)),
        cwd=data.get("cwd"),
        env=dict(data.get("env") or {}),
        timeout_seconds=float(data.get("timeout_seconds", defaults.timeout_seconds)),
    )


def _parse_output(cfg: CredentialProcessConfig, output: str) -> str:
    if cfg.parses == "text":
        return output.strip()
    try:
        node: object = json.loads(output)
    except json.JSONDecodeError as e:
        raise AuthCredentialProcessError(f"helper output is not valid JSON: {e}") from e
    for key in cfg.json_key_path:
        if not isinstance(node, dict) or key not in node:
            raise AuthCredentialProcessError(
                f"helper JSON has no key path {'.'.join(cfg.json_key_path)!r}"
            )
        node = node[key]
    if not isinstance(node, str) or not node:
        raise AuthCredentialProcessError("helper JSON key path did not yield a non-empty string")
    return node


# ponytail: process-local dict cache, no eviction. The key space is
# "credentials the user configured" — a handful. Swap for an LRU if that
# ever stops being true.
_token_cache: dict[tuple, tuple[float, str]] = {}
_cache_lock = threading.Lock()


def _cache_key(cfg: CredentialProcessConfig, scope: str) -> tuple:
    return (scope, tuple(cfg.command), cfg.parses, tuple(cfg.json_key_path), cfg.cwd)


def token_for_payload(data: dict, *, scope: str = "") -> str:
    """Run the helper (or reuse a cached run) and return the token.

    ``scope`` separates cache entries that share a command but must not
    share a token — pass ``f"{provider_id}/{account_id}"``.

    Raises :class:`AuthCredentialProcessError` on any failure. Callers must
    NOT swallow it: the user explicitly configured this helper, so a
    silent fallback to some other credential layer is exactly the
    failure mode this method exists to avoid.
    """
    cfg = config_from_payload(data)
    key = _cache_key(cfg, scope)
    now = time.monotonic()
    with _cache_lock:
        hit = _token_cache.get(key)
        if hit is not None and now < hit[0]:
            return hit[1]
    try:
        output = _run_helper_sync(cfg)
    except AuthCredentialProcessError:
        raise
    except Exception as e:  # noqa: BLE001 — normalize to our error type
        raise AuthCredentialProcessError(str(e)) from e
    token = _parse_output(cfg, output)
    if cfg.cache_seconds > 0:
        with _cache_lock:
            _token_cache[key] = (time.monotonic() + cfg.cache_seconds, token)
    return token


def clear_token_cache() -> None:
    """Drop every cached helper token. For tests and credential edits."""
    with _cache_lock:
        _token_cache.clear()


def _run_helper_sync(cfg: CredentialProcessConfig) -> str:
    """Sync bridge for :func:`_run_helper`.

    The resolver is sync by design (see :mod:`auth.resolver`), and it may
    itself be called from inside a running loop. Mirrors
    :meth:`CredentialProvider.acquire_sync`: own loop when free, private thread
    when a loop is already running on this thread.
    """
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is None:
        return asyncio.run(_run_helper(cfg))

    box: dict = {}

    def _runner() -> None:
        try:
            box["out"] = asyncio.run(_run_helper(cfg))
        except BaseException as e:  # noqa: BLE001
            box["err"] = e

    t = threading.Thread(target=_runner, daemon=True, name="external-process-helper")
    t.start()
    t.join()
    if "err" in box:
        raise box["err"]
    return box["out"]


def _redacted_stderr(stderr: bytes) -> str:
    """Scrub helper stderr before it reaches an error message or a log.

    A failing token helper prints whatever it likes — an echoed request
    carrying the token it just fetched, an upstream response body, a
    verbose curl trace. Reuses the diagnostics scrubber so this path and
    the support bundle cannot disagree about what counts as a secret.
    Redaction runs before truncation: cutting first could leave a secret's
    prefix behind with nothing left for a pattern to match.
    """
    from openprogram.cli.commands.diagnostics import redact_text

    return redact_text(stderr.decode("utf-8", errors="replace"))[:200]


async def _run_helper(cfg: CredentialProcessConfig) -> str:
    from openprogram._compat import executable_cmd, no_window_creation_flags

    env = os.environ.copy()
    env.update(cfg.env)
    proc = await asyncio.create_subprocess_exec(
        *executable_cmd(cfg.command),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=cfg.cwd,
        creationflags=no_window_creation_flags(),
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=cfg.timeout_seconds,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"helper timed out after {cfg.timeout_seconds}s")
    if proc.returncode != 0:
        raise RuntimeError(
            f"helper exited {proc.returncode}: {_redacted_stderr(stderr)}"
        )
    return stdout.decode("utf-8")
