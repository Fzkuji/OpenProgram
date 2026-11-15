"""The page ```visual blocks are drawn in (interfaces/server/routes/visual_frame.py)."""
from __future__ import annotations

import asyncio

from openprogram.interfaces.server.routes import visual_frame


class _App:
    def __init__(self):
        self.routes = {}

    def get(self, path):
        def add(fn):
            self.routes[path] = fn
            return fn
        return add


def test_the_view_page_is_sandboxed_and_reaches_only_the_cdns():
    app = _App()
    visual_frame.register(app)
    response = asyncio.run(app.routes["/visual-frame"]())
    policy = response.headers["content-security-policy"]
    directives = {part.split()[0]: part.split()[1:] for part in policy.split("; ")}
    assert directives["sandbox"] == ["allow-scripts"], "an opaque origin: no allow-same-origin"
    assert directives["connect-src"] == ["'none'"]
    assert directives["frame-ancestors"] == ["'self'"]
    script_hosts = {s for s in directives["script-src"] if s.startswith("https://")}
    assert script_hosts == set(visual_frame.CDN_HOSTS)
    assert "https:" not in directives["img-src"], "no remote images that could carry data out"
    page = response.body.decode()
    for name in ("sendPrompt", "openLink", "themeColor", '"ready"', '"size"'):
        assert name in page


def test_every_name_the_guide_teaches_exists_in_the_view():
    import re
    from pathlib import Path
    root = Path(visual_frame.__file__).parents[4]
    guide = (root / "openprogram/runtime/context/guides/visuals.md").read_text(encoding="utf-8")
    page = (root / "openprogram/interfaces/server/routes/visual-frame.html").read_text(encoding="utf-8")
    mounter = (root / "apps/web/lib/chat/markdown/visual-frames.ts").read_text(encoding="utf-8")
    for token in set(re.findall(r"--[a-z]+(?:-[a-z]+)*", guide)):
        assert f'["{token}",' in mounter, f"the App never sends {token}"
    for name in ("card", "grid", "metric", "row", "muted", "value", "label", "primary"):
        assert re.search(rf"\.{name}\b", page), name
    for fn in ("sendPrompt", "openLink", "themeColor", "themechange"):
        assert fn in guide and fn in page, fn
    for host in visual_frame.CDN_HOSTS:
        assert host.removeprefix("https://") in guide
