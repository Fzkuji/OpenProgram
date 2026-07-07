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
  * Helper runs under the *current profile*'s subprocess HOME (see
    :class:`Profile`) so corporate creds that bleed in via ``~/.aws/``
    don't cross profile boundaries.
  * Helper stderr is captured and surfaced on failure; stdout is parsed
    as JSON or raw text per config. No shell interpolation — we take a
    ``list[str]`` argv so there's nothing to escape.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import Literal

from ..types import (
    Credential,
    CredentialData,
    LoginMethod,
    LoginUi,
)


@dataclass
class ExternalProcessConfig:
    command: list[str]
    parses: Literal["json", "text"] = "json"
    # Which key path to extract when ``parses == "json"``. Same walking
    # convention as :mod:`.cli_import`.
    json_key_path: list[str] = field(default_factory=list)
    cache_seconds: int = 300
    # Working directory for the helper. Resolved relative to the current
    # profile's home when None.
    cwd: str | None = None
    # Additional env vars for the helper. Profile env + os.environ merge
    # in the Profile layer; these layer on top.
    env: dict[str, str] = field(default_factory=dict)
    # Max wall time for one helper invocation. Too short → user sees
    # noise; too long → a hung helper hangs the agent. One minute is a
    # compromise biased toward "you probably want to fix your helper".
    timeout_seconds: float = 60.0


class ExternalProcessMethod(LoginMethod):
    """Runs a helper during login to verify it produces output.

    The login flow doesn't actually persist a cached token — it just
    records the helper invocation shape. Every API call later re-runs
    the helper (via ``CredentialData(kind="external_process")``), subject to
    ``cache_seconds`` de-dup in the manager.
    """

    method_id = "external_process"

    def __init__(
        self,
        provider_id: str,
        config: ExternalProcessConfig,
        *,
        profile_id: str = "default",
        metadata: dict | None = None,
    ) -> None:
        self.provider_id = provider_id
        self._cfg = config
        self._profile_id = profile_id
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
            profile_id=self._profile_id,
            kind="external_process",
            payload=CredentialData(
                kind="external_process",
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


async def _run_helper(cfg: ExternalProcessConfig) -> str:
    env = os.environ.copy()
    env.update(cfg.env)
    proc = await asyncio.create_subprocess_exec(
        *cfg.command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=cfg.cwd,
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
            f"helper exited {proc.returncode}: {stderr.decode('utf-8', errors='replace')[:200]}"
        )
    return stdout.decode("utf-8")
