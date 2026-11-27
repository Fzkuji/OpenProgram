"""Cindy, a desktop app that runs Claude Code, Codex and Pi: an import link.

After yetone/magpie ``internal/agent/cindy.go`` (MIT License, Copyright (c)
yetone). Cindy keeps its providers in its own database, their keys
encrypted, and takes a new one only through its import link, which the user
confirms in Cindy::

    cindy://provider/import?v=1&data=<base64url of the provider's JSON>

So OpenProgram has nothing to set there: its row is the link, the gateway as
one custom provider with an endpoint for each of Cindy's runtimes (Anthropic
Messages for Claude Code, Responses for Codex, Chat Completions for Pi), and
whether Cindy has it already, read from Cindy's databases and never written.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

from . import appdb
from .base import Agent, App, Wiring, mac_app_removed, user_config_dir

SCHEME = "cindy://provider/import?"
PROVIDER_ID = "openprogram"
#: Cindy 0.1.94's brand identity: com.xd.cindycn for its cn build (data in
#: Cindy), com.xd.cindy for its global one (CindyGlobal), both Cindy.app.
APPS = (App("com.xd.cindycn", ("Cindy.app",)), App("com.xd.cindy", ("Cindy.app",)))


def link(at: Wiring) -> str:
    """The link that adds the gateway to Cindy as the provider OpenProgram,
    keyed so its requests are counted as Cindy's."""
    v1 = at.base("openai-chat")
    models = v1 + "/models"
    payload = {
        "kind": "custom", "name": "OpenProgram", "id": PROVIDER_ID,
        "auth": {"method": "apiKey", "apiKey": at.key},
        "endpoints": [
            {"protocol": "anthropic-messages", "baseUrl": at.base("anthropic"), "targets": ["claude-code"], "modelsUrl": models},
            {"protocol": "openai-responses", "baseUrl": v1, "targets": ["codex"], "modelsUrl": models},
            {"protocol": "openai-chat", "baseUrl": v1, "targets": ["pi"], "modelsUrl": models},
        ],
    }
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return SCHEME + "v=1&data=" + base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def gateway_root(at: Wiring) -> str:
    """What every address of the gateway's begins with."""
    return at.worker + "/gateway"


def has(path: Path, gateway: str) -> tuple[bool, bool]:
    """Whether Cindy's database at ``path`` has OpenProgram among its custom
    providers: by the id or name the link gives it, or by the gateway's
    address in its endpoints. No database is no; one that can't be read is
    unknown (magpie ``cindyHas``)."""
    return appdb.count(path, "SELECT count(*) FROM custom_providers WHERE id = ? OR lower(name) = ? OR instr(runtimes, ?) > 0",
                       PROVIDER_ID, PROVIDER_ID, gateway)


def dirs() -> list[Path]:
    """Where Cindy keeps its data (Cindy, CindyGlobal for its global build),
    those there first."""
    base = user_config_dir()
    found = [base / name for name in ("Cindy", "CindyGlobal")]
    return [d for d in found if d.exists()] + [d for d in found if not d.exists()]


def added(folders: list[Path], gateway: str) -> tuple[bool, bool]:
    """Whether any of Cindy's databases has the gateway: one per account
    (cindy-<account>.db) once signed in, cindy-local-v1.db before. One that
    has it is enough; else one that couldn't be read leaves it unknown."""
    known = True
    for folder in folders:
        try:
            dbs = sorted(Path(folder).glob("cindy-*.db"))
        except OSError:
            known = False
            continue
        for db in dbs:
            found, ok = has(db, gateway)
            if found:
                return True, True
            known = known and ok
    return False, known


def _detect() -> bool:
    return any(d.is_dir() for d in dirs()) and not mac_app_removed(*APPS)


def agent() -> Agent:
    return Agent(
        id="cindy", name="Cindy", icon="cindy", kind="app", fields=[], ua=("cindy",),
        path=lambda: dirs()[0], dir=lambda: dirs()[0], wire=lambda at: [],
        detect_only=True, detect=_detect, link=link, added=lambda at: added(dirs(), gateway_root(at)),
    )
