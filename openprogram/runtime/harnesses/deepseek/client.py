"""DeepSeek Harness's ACP server (``dsh --profile acp``): one process per conversation.

DeepSeek Harness ships an Agent Client Protocol server as a ``dsh``
profile: newline-delimited JSON-RPC 2.0 on stdio, the same line protocol
``codex app-server`` speaks, so the Codex client's reader, requests and
per-conversation process pool are reused. Its sessions are saved under
``~/.dsh/sessions`` with the DeepSeek Harness app's own, and its
credentials are the app's.

The person's own dsh can be put on OpenProgram's gateway
(``models/gateway/agents/dsh.py``: OpenProgram's routes in llm-pi-ai and
the start of new sessions on one of them). The chats here still run on
dsh's own models and the person's own sign-in: while the patch lists dsh
reads name OpenProgram's routes, its process is given a patch overlay of
OpenProgram's (``--patch``, applied after every other layer) holding the
person's own llm-pi-ai routes, start and web-search rows, as they were
before the gateway (the gateway's stash), and a model on one of OpenProgram's routes is never
offered or picked here.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

from openprogram.runtime.harnesses.codex.client import AppServer, AppServerError, ProcessPool

#: The ACP protocol version spoken.
PROTOCOL_VERSION = 1
#: Where the macOS app keeps its command-line launcher.
_APP_LAUNCHER = Path("/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh")


def binary_path() -> Optional[str]:
    """The ``dsh`` launcher: ``DSH_BINARY``, ``dsh`` on PATH, or the app's own."""
    configured = os.environ.get("DSH_BINARY")
    if configured and Path(configured).is_file():
        return configured
    found = shutil.which("dsh")
    if found:
        return found
    return str(_APP_LAUNCHER) if _APP_LAUNCHER.is_file() else None


def dsh_home() -> Path:
    return Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh")


#: The credentials OpenProgram's gateway wiring puts in dsh's key store and .env.
_GATEWAY_KEY_REFS = frozenset({"OPENPROGRAM_GATEWAY_KEY"})


def signed_in() -> bool:
    """Whether DeepSeek Harness has credentials of the person's own saved, by
    their names (their values are never used): OpenProgram's gateway key,
    which its wiring puts in the store, is not a sign-in. A store that can't
    be read counts as one."""
    import yaml
    try:
        doc = yaml.safe_load((dsh_home() / ".credentials.yaml").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError, yaml.YAMLError):
        return True
    if not isinstance(doc, dict):
        return bool(doc)
    names: set[str] = set()
    for section in ("refs", "records"):
        if isinstance(doc.get(section), dict):
            names |= {str(name) for name, value in doc[section].items() if value}
    others = set(doc) - {"version", "refs", "records"}
    return bool(others or names - _GATEWAY_KEY_REFS)


# -- OpenProgram's gateway in the person's dsh ---------------------------------

#: The rows the shipped tree has that the overlay may give back, by the
#: launcher dsh printed them from and its mtime.
_shipped: dict[tuple[str, float], dict[str, dict]] = {}


def shipped_rows() -> dict[str, dict]:
    """dsh's own config of its start and web-search rows, as the installed dsh
    ships them: ``dsh --profile acp --dump-default-config`` (the profile tree
    without the person's layer or any overlay), by row id; ``{}`` when dsh
    can't say."""
    from openprogram.models.gateway.agents import dsh as wiring
    binary = binary_path()
    if not binary:
        return {}
    try:
        stamp = (binary, os.path.getmtime(binary))
    except OSError:
        return {}
    if stamp not in _shipped:
        import subprocess
        rows: dict[str, dict] = {}
        try:
            done = subprocess.run([binary, "--profile", "acp", "--dump-default-config"], capture_output=True,
                                  text=True, timeout=60, env={**os.environ, "DSH_HOME": str(dsh_home())})
            if done.returncode != 0:
                return {}
            # the tree as dsh prints it, its !!js values and all
            _, items = wiring.parse(done.stdout, "dsh --dump-default-config")
            for row in (wiring.START_ROW, wiring.SEARCH_ROW, wiring.WEB_ROW):
                i = wiring.config_index(items, row)
                if i >= 0:
                    rows[row] = dict(items[i].config() or {})
        except (OSError, subprocess.SubprocessError, wiring.PatchUnreadable):
            return {}
        _shipped[stamp] = rows
    return {row: dict(config) for row, config in _shipped[stamp].items()}


def shipped_start() -> dict:
    """dsh's own start, as the installed dsh ships it (:func:`shipped_rows`)."""
    from openprogram.models.gateway.agents import dsh as wiring
    return shipped_rows().get(wiring.START_ROW, {})


def _layers() -> list[Path]:
    """The patch lists ``dsh --profile acp`` reads, in order."""
    return [dsh_home() / "profiles" / "acp" / "cordis.patch.yml", dsh_home() / "cordis.patch.yml"]


def _own_start(paths: list[Path]) -> dict:
    """The start the person had before OpenProgram's gateway took it, as the
    gateway's stash keeps it (the start it replaced, or the copy of the file
    taken before), else dsh's own shipped one (:func:`shipped_start`), never
    a model named here."""
    from openprogram.models.gateway.agents import dsh as wiring
    try:
        from openprogram.models.gateway import stash
        record = stash.entry("dsh") or {}
    except Exception:  # noqa: BLE001 - wired by another instance, or a damaged stash
        record = {}
    starts = record.get("starts") or {}
    files = record.get("files") or {}
    for path in paths:
        key = os.path.realpath(path)
        candidates = []
        if starts.get(key):
            candidates.append("\n".join(starts[key]))
        copy = (files.get(key) or {}).get("copy")
        if copy:
            try:
                candidates.append(Path(copy).read_text(encoding="utf-8"))
            except OSError:
                pass
        for text in candidates:
            try:
                _, items = wiring.parse(text)
            except wiring.PatchUnreadable:
                continue
            i = wiring.find(items, wiring.START_ROW)
            if i >= 0 and not wiring.ours(items[i]) and items[i].config().get("model"):
                return items[i].config()
    return shipped_start()


def gateway_overlay() -> Optional[list]:
    """While the patch lists dsh reads put it on OpenProgram's gateway, the
    overlay that gives it back the person's own setup: llm-pi-ai with their
    routes alone, their own start, and their own web search
    (:func:`_own_search`); None while they don't."""
    from openprogram.models.gateway.agents import dsh as wiring
    pi: Optional[dict] = None
    start: Optional[dict] = None
    for path in _layers():
        try:
            _, items = wiring.parse(path.read_text(encoding="utf-8"), str(path))
        except (OSError, wiring.PatchUnreadable):
            continue
        # an entry's config replaces the whole config of the one before it
        i = wiring.config_index(items, wiring.PI_ROW)
        if i >= 0:
            pi = items[i].config()
        j = wiring.config_index(items, wiring.START_ROW)
        if j >= 0:
            start = items[j].config()
    overlay = []
    for row, config in _own_search(_layers()):
        overlay.append({"id": row, "config": config})
    providers = (pi or {}).get("providers")
    if isinstance(providers, dict) and any(wiring.is_route(key) for key in providers):
        own = {key: value for key, value in providers.items() if not wiring.is_route(key)}
        overlay.append({"id": wiring.PI_ROW, "config": {**(pi or {}), "providers": own}})
    if start and wiring.is_route(str(start.get("provider") or "")):
        # dsh's own start; one dsh can't say is left out, and the start on a
        # route the overlay took away reaches no gateway either
        own_start = _own_start(_layers())
        if own_start.get("model"):
            overlay.append({"id": wiring.START_ROW, "config": own_start})
    return overlay or None


def _own_search(paths: list[Path]) -> list[tuple[str, dict]]:
    """The person's own web-search rows where the patch lists dsh reads end on
    OpenProgram's (its web-search-deepseek on the gateway, its web row naming
    that search): the rows as the files had them before OpenProgram, from the
    gateway's stash copies, else as dsh ships them, else dsh's defaults (an
    empty config), so a search in these chats never goes through the gateway."""
    from openprogram.models.gateway.agents import dsh as wiring
    live: dict[str, wiring.Item] = {}
    for path in paths:
        try:
            _, items = wiring.parse(path.read_text(encoding="utf-8"), str(path))
        except (OSError, wiring.PatchUnreadable):
            continue
        for row in (wiring.SEARCH_ROW, wiring.WEB_ROW):
            i = wiring.config_index(items, row)
            if i >= 0:
                live[row] = items[i]
    taken = [row for row, item in live.items()
             if item.ours and (row == wiring.SEARCH_ROW or wiring.web_ours(item))]
    if not taken:
        return []
    try:
        from openprogram.models.gateway import stash
        files = (stash.entry("dsh") or {}).get("files") or {}
    except Exception:  # noqa: BLE001 - wired by another instance, or a damaged stash
        files = {}
    before: dict[str, dict] = {}
    for path in paths:
        copy = (files.get(os.path.realpath(path)) or {}).get("copy")
        if not copy:
            continue
        try:
            _, items = wiring.parse(Path(copy).read_text(encoding="utf-8"), copy)
        except (OSError, wiring.PatchUnreadable):
            continue
        for row in taken:
            i = wiring.config_index(items, row)
            if i >= 0 and not items[i].ours:
                before[row] = items[i].config()
    shipped = shipped_rows() if any(row not in before for row in taken) else {}
    return [(row, before.get(row, shipped.get(row, {}))) for row in taken]


def _overlay_file(entries: list) -> str:
    """The overlay written in OpenProgram's cache (``cache/deepseek-harness``)."""
    import yaml
    from openprogram.models.gateway.agents.dsh import _Tagged

    class Dumper(yaml.SafeDumper):
        pass

    # a !!js value of the person's goes back as dsh's loader reads it
    Dumper.add_representer(_Tagged, lambda dumper, value: dumper.represent_scalar("tag:yaml.org,2002:js", str(value)))
    try:
        from openprogram.state.paths import get_state_dir
        root = Path(get_state_dir())
    except Exception:  # noqa: BLE001
        import tempfile
        root = Path(tempfile.gettempdir())
    folder = root / "cache" / "deepseek-harness"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "own-setup.patch.yml"
    text = "# OpenProgram: this dsh's own routes and start, over OpenProgram's gateway\n" + yaml.dump(
        entries, Dumper=Dumper, sort_keys=False, allow_unicode=True, default_flow_style=False)
    if not path.is_file() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")
        path.chmod(0o600)
    return str(path)


def launch_args() -> list[str]:
    """The extra arguments a turn starts dsh with: the overlay of the
    person's own setup while dsh is on OpenProgram's gateway."""
    overlay = gateway_overlay()
    return ["--patch", _overlay_file(overlay)] if overlay else []


def own_model() -> str:
    """``provider:model`` of the person's own start (:func:`_own_start`)."""
    start = _own_start(_layers())
    return f"{start.get('provider')}:{start.get('model')}" if start.get("model") else ""


def gateway_model(model: str) -> bool:
    """``provider:model`` names a model on one of OpenProgram's gateway routes."""
    from openprogram.models.gateway.agents.dsh import is_route
    return is_route(model.partition(":")[0])


def app_version() -> str:
    try:
        import json
        runtime = _APP_LAUNCHER.parents[3] / "runtime" / "primary-runtime" / "runtime.json"
        return str(json.loads(runtime.read_text(encoding="utf-8")).get("desktopVersion") or "")
    except (OSError, ValueError, IndexError):
        return ""


#: Whether the installed DeepSeek Harness reads images in a prompt (its ``initialize`` says).
_takes_images = False


class AcpServer(AppServer):
    """``dsh --profile acp``: JSON-RPC 2.0 lines and ACP's handshake."""

    label = "DeepSeek Harness"
    thread_name = "dsh-acp"

    def default_answer(self, message: dict) -> dict:
        if message.get("method") == "session/request_permission":
            return {"result": {"outcome": {"outcome": "cancelled"}}}  # ACP's answer when nobody decides
        return super().default_answer(message)

    def start(self) -> dict:
        from openprogram.host._platform.process_ownership import process_tree_popen_kwargs
        import subprocess
        import threading

        env = {**os.environ, **self.env}
        self._process = subprocess.Popen(
            self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, **process_tree_popen_kwargs(),
        )
        threading.Thread(target=self._read, name=f"{self.thread_name}-reader", daemon=True).start()
        threading.Thread(target=self._read_stderr, name=f"{self.thread_name}-stderr", daemon=True).start()
        answer = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
            "clientInfo": {"name": "OpenProgram", "version": "1"},
        }, timeout=120) or {}
        capabilities = (answer.get("agentCapabilities") or {}).get("promptCapabilities") or {}
        self.takes_images = bool(capabilities.get("image"))
        self._initialized(answer)
        return answer

    def _initialized(self, answer: dict) -> None:
        """The agent's ``initialize`` answer, read once it started."""
        global _takes_images
        _takes_images = self.takes_images

    def _write(self, message: dict) -> None:
        super()._write({"jsonrpc": "2.0", **message})


def start_acp_server(extra_args: Optional[list[str]] = None, env: Optional[dict[str, str]] = None) -> AcpServer:
    binary = binary_path()
    if binary is None:
        raise AppServerError("DeepSeek Harness is not installed on this computer.")
    server = AcpServer([binary, "--profile", "acp", *(extra_args or [])], env=env)
    try:
        server.start()
    except (AppServerError, OSError) as exc:
        tail = server.stderr_tail().splitlines()[-1:] if server.stderr_tail() else []
        server.kill()
        raise AppServerError(f"DeepSeek Harness did not start: {tail[0] if tail else exc}") from exc
    return server


_pool: Optional[ProcessPool] = None


def pool() -> ProcessPool:
    global _pool
    if _pool is None:
        # Closed when its turn ends: a running one holds the session's ``session.lock``,
        # and the DeepSeek Harness app cannot write a session another process holds.
        _pool = ProcessPool(starter=start_acp_server, idle_seconds=0)
        import atexit
        atexit.register(_pool.close_all)
    return _pool


# -- models -----------------------------------------------------------------

def model_id(value: str) -> str:
    """``["deepseek-official","deepseek-v4-flash"]`` (ACP's option value) → ``deepseek-official:deepseek-v4-flash``.

    A colon, not a slash: OpenProgram reads a model reference's provider up
    to its first slash (``deepseek-harness/deepseek-official:deepseek-v4-flash``)."""
    import json
    try:
        parts = json.loads(value)
    except (TypeError, ValueError):
        return str(value)
    return ":".join(str(p) for p in parts) if isinstance(parts, list) else str(value)


def option_value(model: str) -> str:
    """The ACP option value of ``provider:model``."""
    import json
    provider, _, name = model.partition(":")
    return json.dumps([provider, name], separators=(",", ":")) if name else model


def read_options(config_options: list) -> tuple[list[dict], list[str], Optional[str]]:
    """Models, efforts and the default effort a session's ``configOptions`` offer."""
    models: list[dict] = []
    efforts: list[str] = []
    default_effort: Optional[str] = None
    for option in config_options or []:
        if not isinstance(option, dict):
            continue
        if option.get("id") == "model":
            current = option.get("currentValue")
            for group in option.get("options") or []:
                for row in (group.get("options") if isinstance(group, dict) and "options" in group else [group]):
                    # never a model on OpenProgram's gateway: its chats run on dsh's own
                    if isinstance(row, dict) and row.get("value") and not gateway_model(model_id(row["value"])):
                        models.append({"id": model_id(row["value"]), "label": str(row.get("name") or row["value"]),
                                       "default": row["value"] == current})
        elif option.get("id") == "reasoning_effort":
            efforts = [str(o.get("value")) for o in option.get("options") or [] if isinstance(o, dict) and o.get("value")]
            default_effort = option.get("currentValue")
    for model in models:
        model.update(efforts=efforts, default_effort=default_effort, images=_takes_images, personality=False)
    return models, efforts, default_effort
