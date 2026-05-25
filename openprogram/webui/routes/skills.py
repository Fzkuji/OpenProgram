"""Skills routes — list, CRUD, toggle, invoke-trace, remote discovery.

Designed to be mounted via ``register(app)`` from the FastAPI host.
Mirrors the pattern in :mod:`openprogram.webui.routes.memory`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse


# ---------------------------------------------------------------------------
# Persistence helpers — small JSON state files under ~/.openprogram/
# ---------------------------------------------------------------------------

def _state_root() -> Path:
    return Path.home() / ".openprogram"


def _disabled_path() -> Path:
    return _state_root() / "skills.json"


def _discovery_path() -> Path:
    return _state_root() / "skills_discovery.json"


def _load_disabled() -> set[str]:
    p = _disabled_path()
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return set(data.get("disabled", []))
    except Exception:
        return set()


def _save_disabled(disabled: set[str]) -> None:
    p = _disabled_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"disabled": sorted(disabled)}, indent=2), encoding="utf-8")


# A few well-known skill index URLs we ship by default so the Discovery
# panel isn't empty on first run. Each entry is `{url, label, description}`;
# the API still returns the flat URL list for back-compat, but
# /api/skills/discovery/suggested exposes the rich metadata for the UI.
DEFAULT_DISCOVERY_SUGGESTIONS: list[dict] = [
    {
        "url": "https://github.com/anthropics/skills/tree/main/skills",
        "label": "Anthropic Skills",
        "slug": "anthropic-skills",
        "description": "Official Anthropic skill collection — 18 skills covering PDF/DOCX/PPTX, frontend design, MCP, Claude API, and more.",
    },
    {
        "url": "https://github.com/obra/superpowers",
        "label": "Superpowers (obra)",
        "slug": "superpowers",
        "description": "Community-curated agent skills by Jesse Vincent — brainstorming, parallel agents, code review, plans.",
    },
    {
        "url": "https://github.com/daymade/claude-code-skills",
        "label": "daymade Skills Marketplace",
        "slug": "daymade-skills",
        "description": "Production-ready skill marketplace by daymade — 50+ skills organised as a plugin marketplace.",
    },
    {
        "url": "https://github.com/alirezarezvani/claude-skills",
        "label": "Reza's Mega Pack",
        "slug": "reza-claude-skills",
        "description": "Massive cross-agent collection by Alireza Rezvani — 300+ skills + 30+ agents + custom commands spanning engineering, marketing, product, compliance, research, finance.",
    },
    {
        "url": "https://github.com/Claude-Skills-Org/skills-main",
        "label": "Claude-Skills-Org",
        "slug": "claude-skills-org",
        "description": "Community skill collection — 16 skills curated by the Claude-Skills-Org collective.",
    },
]


def _load_discovery_sources() -> list[str]:
    p = _discovery_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return list(data.get("sources", []))
    except Exception:
        return []


def _save_discovery_sources(sources: list[str]) -> None:
    p = _discovery_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"sources": sources}, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# WebSocket broadcast stub — real WS wiring lives in server.py; routes here
# just call this no-op so future hookup is one edit.
# ---------------------------------------------------------------------------

def _emit(event: str, data: dict) -> None:
    # TODO: wire to the global WS broadcaster once server.py exposes it.
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def register(app):
    @app.get("/api/skills")
    async def list_skills_endpoint():
        from openprogram.skills.loader import list_skills
        disabled = _load_disabled()
        out = []
        for s in list_skills():
            d = s.to_dict()
            d["enabled"] = s.name not in disabled
            d.pop("body", None)
            out.append(d)
        return JSONResponse(content=out)

    @app.get("/api/skills/_complete")
    async def skills_complete(q: str = "", limit: int = 20):
        from openprogram.skills.loader import complete
        return JSONResponse(content=complete(q, limit=limit))

    @app.get("/api/skills/_resolve")
    async def skills_resolve(q: str):
        from openprogram.skills.loader import resolve, AmbiguousSkillError
        try:
            s = resolve(q)
        except AmbiguousSkillError as e:
            return JSONResponse(
                content={"resolved": None, "ambiguous": True, "candidates": e.candidates},
                status_code=409,
            )
        if s is None:
            return JSONResponse(content={"resolved": None, "ambiguous": False, "candidates": []}, status_code=404)
        return JSONResponse(content={"resolved": s.name, "ambiguous": False, "candidates": [s.name]})

    # NOTE: discovery routes MUST be declared before the {name:path} catchall
    # below, otherwise FastAPI matches them as if the literal string "discovery"
    # were a skill name and returns 404.

    @app.get("/api/skills/discovery/suggested")
    async def list_discovery_suggested():
        added = set(_load_discovery_sources())
        out = [
            {**entry, "added": entry["url"] in added}
            for entry in DEFAULT_DISCOVERY_SUGGESTIONS
        ]
        return JSONResponse(content=out)

    @app.get("/api/skills/discovery/sources")
    async def list_discovery_sources():
        return JSONResponse(content=_load_discovery_sources())

    @app.post("/api/skills/discovery/sources")
    async def add_or_remove_discovery_source(request: Request):
        body = await request.json()
        action = body.get("action", "add")
        url = (body.get("url") or "").strip()
        if not url:
            return JSONResponse(content={"error": "url required"}, status_code=400)
        sources = _load_discovery_sources()
        if action == "remove":
            sources = [s for s in sources if s != url]
        else:
            if url not in sources:
                sources.append(url)
        _save_discovery_sources(sources)
        return JSONResponse(content=sources)

    @app.post("/api/skills/discovery/pull")
    async def pull_discovery(request: Request):
        from openprogram.skills.discovery import pull
        body = await request.json()
        url = (body.get("url") or "").strip()
        namespace = body.get("namespace")
        if namespace is not None:
            namespace = str(namespace).strip()
        if not url:
            return JSONResponse(content={"error": "url required"}, status_code=400)
        try:
            pulled = pull(url, namespace=namespace)
        except Exception as e:
            return JSONResponse(
                content={"error": f"{type(e).__name__}: {e}"}, status_code=502,
            )
        _emit("skills:changed", {"pulled": pulled})
        return JSONResponse(content={"pulled": pulled})

    @app.get("/api/skills/discovery/browse")
    async def browse_discovery(url: str = ""):
        from openprogram.skills.discovery import browse
        url = (url or "").strip()
        if not url:
            return JSONResponse(content={"error": "url required"}, status_code=400)
        try:
            entries = browse(url)
        except Exception as e:
            return JSONResponse(
                content={"error": f"{type(e).__name__}: {e}"}, status_code=502,
            )
        return JSONResponse(content={"url": url, "entries": entries})

    @app.post("/api/skills/discovery/install")
    async def install_discovery(request: Request):
        from openprogram.skills.discovery import install_one
        body = await request.json()
        url = (body.get("url") or "").strip()
        name = (body.get("name") or "").strip()
        namespace = body.get("namespace")
        if namespace is not None:
            namespace = str(namespace).strip()
        if not url or not name:
            return JSONResponse(
                content={"error": "url and name required"}, status_code=400,
            )
        try:
            installed = install_one(url, name, namespace=namespace)
        except Exception as e:
            return JSONResponse(
                content={"error": f"{type(e).__name__}: {e}"}, status_code=502,
            )
        if installed is None:
            return JSONResponse(
                content={"error": f"skill not found in source: {name}"},
                status_code=404,
            )
        _emit("skills:changed", {"installed": installed})
        return JSONResponse(content={"installed": installed})

    @app.get("/api/skills/{name:path}")
    async def get_skill_endpoint(name: str):
        from openprogram.skills.loader import get_skill, skill_resource_tree
        s = get_skill(name)
        if s is None:
            return JSONResponse(content={"error": "not found"}, status_code=404)
        disabled = _load_disabled()
        return JSONResponse(content={
            "name": s.name,
            "description": s.description,
            "category": s.category,
            "optional": s.optional,
            "allowed_tools": s.allowed_tools,
            "triggers": s.triggers,
            "version": s.version,
            "source": s.source,
            "path": s.path,
            "body": s.body,
            "resources": skill_resource_tree(s),
            "enabled": s.name not in disabled,
        })

    @app.post("/api/skills")
    async def create_skill_endpoint(request: Request):
        body = await request.json()
        raw = (body.get("name") or "").strip().strip("/")
        # Allow hierarchical names like "research/literature/survey".
        segments = [seg for seg in raw.split("/") if seg]
        if not segments or any(
            not seg or seg.startswith(".") or seg in (".", "..") or "\\" in seg
            for seg in segments
        ):
            return JSONResponse(content={"error": "invalid name"}, status_code=400)
        name = "/".join(segments)
        description = body.get("description", "")
        category = body.get("category", "")
        skill_body = body.get("body", "")
        target_dir = Path(os.getcwd()) / "skills" / Path(*segments)
        if (target_dir / "SKILL.md").exists():
            return JSONResponse(content={"error": "skill already exists"}, status_code=409)
        target_dir.mkdir(parents=True, exist_ok=True)
        fm_lines = ["---", f"name: {name}", f'description: "{description}"']
        if category:
            fm_lines.append(f"category: {category}")
        fm_lines.append("---")
        text = "\n".join(fm_lines) + "\n\n" + (skill_body or "")
        (target_dir / "SKILL.md").write_text(text, encoding="utf-8")
        _emit("skills:changed", {"name": name})
        return JSONResponse(content={"ok": True, "path": str(target_dir / "SKILL.md")})

    @app.delete("/api/skills/{name:path}")
    async def delete_skill_endpoint(name: str):
        from openprogram.skills.loader import get_skill
        s = get_skill(name)
        if s is None:
            return JSONResponse(content={"error": "not found"}, status_code=404)
        if s.source not in ("project", "user", "remote-cache"):
            return JSONResponse(
                content={"error": f"cannot delete skill from source '{s.source}'"},
                status_code=400,
            )
        skill_dir = Path(s.path).parent
        # Remove the whole skill directory.
        import shutil
        shutil.rmtree(skill_dir, ignore_errors=True)
        _emit("skills:changed", {"name": name})
        return JSONResponse(content={"ok": True})

    @app.post("/api/skills/{name:path}/toggle")
    async def toggle_skill_endpoint(name: str, request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        disabled = _load_disabled()
        if "enabled" in body:
            want = bool(body["enabled"])
        else:
            want = name in disabled  # flip
        if want:
            disabled.discard(name)
        else:
            disabled.add(name)
        _save_disabled(disabled)
        _emit("skills:changed", {"name": name})
        return JSONResponse(content={"name": name, "enabled": name not in disabled})

    @app.post("/api/skills/{name:path}/invoke-trace")
    async def skill_invoke_trace(name: str, request: Request):
        from openprogram.skills.tool import read_trace
        try:
            body = await request.json()
        except Exception:
            body = {}
        limit = int(body.get("limit", 50)) if isinstance(body, dict) else 50
        return JSONResponse(content=read_trace(name, limit=limit))
