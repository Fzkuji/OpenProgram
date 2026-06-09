"""CLI for managing the Claude accounts OpenProgram's ``claude-code``
provider can use, and which one is active.

Registered as ``openprogram providers claude-code accounts <verb>``:

  * ``add <name>``    — add a Claude account (opens a browser login)
  * ``remove <name>`` — remove a saved account
  * ``list``          — list accounts and which one is active
  * ``use <name>``    — activate an account (claude-code runs on it)
  * ``status``        — backend readiness + active account + accounts

Implementation note: the accounts live in a local Claude proxy (Meridian)
that holds each subscription as a named profile; ``claude-code`` requests
carry an ``x-meridian-profile`` header naming the active one. That proxy
is an INTERNAL detail — nothing user-facing here says "meridian"; users
only ever see "Claude account". The one exception is the one-time backend
install hint in ``status`` (the proxy ships as an npm package, so its name
is unavoidable there). See docs/design/claude-code-meridian-profile.md.
"""
from __future__ import annotations

import argparse
import os
import re
import secrets
import shutil
import subprocess
import sys


_DEFAULT_PROXY_URL = "http://localhost:3456"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_OAUTH_URL_RE = re.compile(r"https://\S*oauth\S*")

# In-flight web logins: session id -> {proc, name}. The proxy's account login
# is an interactive OAuth (prints a URL, then waits on stdin for the code the
# user pastes back). The CLI gets that for free via an inherited terminal; the
# web flow drives it in two steps — start_add() launches it and returns the
# URL, submit_login_code() feeds the pasted code to the live process's stdin.
_PENDING_LOGINS: dict[str, dict] = {}


def build_parser(accounts_sub: "argparse._SubParsersAction") -> None:
    """Register account verbs on ``providers claude-code accounts``."""
    p_add = accounts_sub.add_parser(
        "add",
        help="Add a Claude account (opens a browser login — sign in with the "
             "account you want to add).",
        description=(
            "Opens a browser login and saves the account under <name>. Sign "
            "in with whichever Claude account you want to add (switch accounts "
            "in the browser first if a different one is signed in). Your "
            "terminal chat account is not affected — you drive everything "
            "through OpenProgram, there's no other tool to run."
        ),
    )
    p_add.add_argument("name", help="A label for the account, e.g. experiment.")

    p_remove = accounts_sub.add_parser(
        "remove", aliases=["rm"], help="Remove a saved Claude account.",
    )
    p_remove.add_argument("name", help="Account label to remove.")

    accounts_sub.add_parser(
        "list", help="List saved Claude accounts and which one is active.",
    )

    p_use = accounts_sub.add_parser(
        "use", aliases=["activate"],
        help="Activate an account — claude-code runs OpenProgram on it.",
    )
    p_use.add_argument("name", help="Account label to activate.")

    accounts_sub.add_parser(
        "deactivate",
        help="Deactivate — leave no account active (claude-code has none to run on).",
    )

    p_rename = accounts_sub.add_parser(
        "rename", help="Rename a saved Claude account.",
    )
    p_rename.add_argument("old", help="Current account label.")
    p_rename.add_argument("new", help="New label.")

    accounts_sub.add_parser(
        "status",
        help="Show backend readiness, the active account, and all accounts.",
    )


def dispatch(args: argparse.Namespace) -> int:
    verb = getattr(args, "accounts_cmd", None)
    if verb == "add":
        return _cmd_add(args.name)
    if verb in ("remove", "rm"):
        return _cmd_remove(args.name)
    if verb == "list":
        return _cmd_list()
    if verb in ("use", "activate"):
        return _cmd_use(args.name)
    if verb == "deactivate":
        return _cmd_deactivate()
    if verb == "rename":
        return _cmd_rename(args.old, args.new)
    if verb == "status":
        return _cmd_status()
    print(
        "Usage: openprogram providers claude-code accounts <verb>\n"
        "Verbs: add <name>, remove <name>, list, use <name>, deactivate, "
        "rename <old> <new>, status",
        file=sys.stderr,
    )
    return 2


# ---------------------------------------------------------------------------
# backend helpers (the local proxy = Meridian; kept internal)
# ---------------------------------------------------------------------------

_NPM_PREFIX_CACHE: "str | None | bool" = False  # False = not computed yet


def _npm_global_prefix() -> "str | None":
    """npm's global install prefix (cached). ``npm prefix -g`` is authoritative
    but spawns node, so we cache it for the process and fall back to the
    platform default (``%APPDATA%\\npm`` on Windows)."""
    global _NPM_PREFIX_CACHE
    if _NPM_PREFIX_CACHE is False:
        prefix = ""
        try:
            from openprogram._compat import node_tool_cmd
            r = subprocess.run(node_tool_cmd(["npm", "prefix", "-g"]),
                               capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                prefix = (r.stdout or "").strip()
        except Exception:
            prefix = ""
        if not prefix and sys.platform == "win32":
            appdata = os.environ.get("APPDATA")
            prefix = os.path.join(appdata, "npm") if appdata else ""
        _NPM_PREFIX_CACHE = prefix or None
    return _NPM_PREFIX_CACHE


def _npm_bin(name: str) -> str | None:
    """Locate an npm-installed CLI shim. Prefer PATH, but also look under npm's
    global bin dir — it isn't always on the worker's PATH (notably
    ``%APPDATA%\\npm`` on Windows), so a freshly npm-installed tool would
    otherwise look 'not installed' until a shell restart."""
    found = shutil.which(name)
    if found:
        return found
    prefix = _npm_global_prefix()
    if not prefix:
        return None
    if sys.platform == "win32":
        cands = [os.path.join(prefix, f"{name}{ext}") for ext in (".cmd", ".exe", "")]
    else:
        cands = [os.path.join(prefix, "bin", name)]
    return next((c for c in cands if os.path.isfile(c)), None)


def _proxy_bin() -> str | None:
    return _npm_bin("meridian")


def _run_proxy(args: list[str]) -> tuple[int, str]:
    binp = _proxy_bin()
    if not binp:
        return 127, "backend not installed"
    from openprogram._compat import node_tool_cmd
    try:
        # node_tool_cmd routes a Windows ``.cmd`` shim through cmd.exe — a bare
        # subprocess([binp,...]) can't exec a .cmd (WinError 193). Force UTF-8
        # decoding: the default Windows locale (e.g. gbk) crashes on the smart
        # quotes / box-drawing chars the backend prints.
        p = subprocess.run(node_tool_cmd([binp, *args]),
                           capture_output=True, encoding="utf-8",
                           errors="replace", timeout=30)
    except Exception as e:  # noqa: BLE001
        return 1, f"backend error: {e}"
    return p.returncode, (p.stdout + p.stderr)


def _active_account() -> str | None:
    """The account name claude-code is currently pinned to (or None)."""
    from ._claude_max_proxy_registry import meridian_profile
    return meridian_profile()


def _backend_ready() -> tuple[bool, str]:
    url = os.environ.get("CLAUDE_MAX_PROXY_URL") or _DEFAULT_PROXY_URL
    try:
        import urllib.request

        urllib.request.urlopen(url.rstrip("/") + "/v1/models", timeout=3)
        return True, url
    except Exception:
        return False, url


def ensure_backend(install: bool = True, start: bool = True) -> dict:
    """Make sure the local Claude proxy is installed AND running — the user
    never installs or starts it by hand. Installs it via npm if missing
    (one-time, same pattern as the agent-browser tool) and spawns it in the
    background if not running. Returns ``{ready, installed_now?, started?,
    error?}``."""
    binp = _proxy_bin()
    if not binp and install:
        npm = shutil.which("npm")
        if not npm:
            return {"ready": False,
                    "error": "Node.js / npm is required to set up the Claude "
                             "backend — install Node, then try again."}
        from openprogram._compat import node_tool_cmd
        try:
            # Timeout so a wedged npm can't pin the FastAPI threadpool thread
            # forever (the symptom: "Add account" hangs with no feedback).
            subprocess.run(
                node_tool_cmd(["npm", "install", "-g", "@rynfar/meridian",
                               "--ignore-scripts"]),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            return {"ready": False,
                    "error": "Setting up the Claude backend timed out — check "
                             "your network and try again, or install it manually "
                             "with `npm install -g @rynfar/meridian`."}
        except Exception as e:  # noqa: BLE001
            return {"ready": False, "error": f"backend install failed: {e}"}
        binp = _proxy_bin()
    if not binp:
        return {"ready": False, "error": "backend not installed"}

    if _backend_ready()[0]:
        return {"ready": True}
    if not start:
        return {"ready": False, "error": "backend not running"}
    # Spawn detached so it outlives this request (and the worker); it's a
    # plain HTTP daemon on 3456. The user doesn't manage its lifecycle.
    from openprogram._compat import node_tool_cmd
    spawn_kwargs: dict = dict(
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    if sys.platform == "win32":
        # start_new_session is a no-op on Windows; use creation flags so the
        # daemon truly detaches and has no console window.
        spawn_kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )
    else:
        spawn_kwargs["start_new_session"] = True
    try:
        subprocess.Popen(node_tool_cmd([binp]), **spawn_kwargs)
    except Exception as e:  # noqa: BLE001
        return {"ready": False, "error": f"backend start failed: {e}"}
    import time as _time
    for _ in range(12):
        _time.sleep(1)
        if _backend_ready()[0]:
            return {"ready": True, "started": True}
    return {"ready": False, "error": "backend started but isn't responding yet"}


def _reap_stale_logins(ttl: float = 300.0) -> None:
    """Kill + drop any in-flight web login the user abandoned (started the
    OAuth but never submitted the code), so the child process and PTY fd
    don't leak. Called at the start of each new login."""
    import time as _t
    now = _t.time()
    for sid in list(_PENDING_LOGINS):
        e = _PENDING_LOGINS.get(sid)
        if not e or now - e.get("started_at", now) <= ttl:
            continue
        try:
            e["driver"].kill()
            e["driver"].close()
        except Exception:
            pass
        _PENDING_LOGINS.pop(sid, None)


def _parse_accounts() -> list[dict]:
    """Parse the proxy's profile listing into ``[{name, email}]``.

    We don't pass the raw backend output through — it mentions the
    underlying tool. Instead we pull out the account label + email and
    re-render them under Claude-account wording.
    """
    rc, out = _run_proxy(["profile", "list"])
    accounts: list[dict] = []
    for line in out.splitlines():
        s = _ANSI.sub("", line).strip()
        if not s or s.endswith(":") or s.startswith("Config") or "no restart" in s:
            continue
        if "No profiles" in s or "Add one" in s:
            continue
        parts = s.split()
        if not parts:
            continue
        name = parts[0]
        email = next((p for p in parts if "@" in p), "")
        accounts.append({"name": name, "email": email})
    return accounts


def _meridian_config_dir() -> str:
    """The proxy's config dir, resolved per platform. It's a node tool, so
    mac + linux use XDG-style ~/.config/meridian; Windows uses %APPDATA%."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "meridian")
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(xdg, "meridian")


def _email_for(name: str) -> str:
    for a in _parse_accounts():
        if a["name"] == name:
            return a.get("email", "")
    return ""


def _rename_profile(old: str, new: str) -> bool:
    """Rename a profile (its id + on-disk config dir). The proxy picks up
    profiles.json automatically, so no restart. Returns success; refuses to
    clobber an existing name."""
    import json

    old, new = (old or "").strip(), (new or "").strip()
    if not old or not new or old == new:
        return False
    cfg = _meridian_config_dir()
    pj = os.path.join(cfg, "profiles.json")
    pdir = os.path.join(cfg, "profiles")
    try:
        with open(pj) as f:
            data = json.load(f)
    except Exception:
        return False
    if any(p.get("id") == new for p in data):
        return False
    for p in data:
        if p.get("id") == old:
            old_dir = os.path.join(pdir, old)
            new_dir = os.path.join(pdir, new)
            try:
                if os.path.isdir(old_dir):
                    os.rename(old_dir, new_dir)
            except OSError:
                return False
            p["id"] = new
            p["claudeConfigDir"] = new_dir
            try:
                with open(pj, "w") as f:
                    json.dump(data, f, indent=2)
            except Exception:
                return False
            return True
    return False


def accounts_summary() -> dict:
    """Structured Claude-account state for the REST / UI layer.

    CLI verbs and the web/TUI all read through this one shape. Never
    includes raw backend text, so no proxy/tool name leaks to the UI.
    """
    ready, url = _backend_ready()
    binp = _proxy_bin()
    summary = {
        "installed": bool(binp),
        "ready": ready,
        "backend_url": url,
        "active": _active_account(),
        "accounts": _parse_accounts() if binp else [],
    }
    # Capability + install-guidance flags so the UI can prompt the user to
    # install what's missing and offer both login methods.
    summary.update(prerequisites())
    return summary


def start_add(name: str) -> dict:
    """Begin a web account login: launch the proxy's OAuth, read the URL it
    prints, and keep the process alive waiting for the pasted code.

    Returns ``{session, url, name}``. The UI opens ``url`` for the user to
    sign in; the page hands back a code which the UI submits via
    :func:`submit_login_code`. (The CLI doesn't need this two-step dance —
    it inherits a terminal, so the user pastes the code straight in.)
    """
    eb = ensure_backend()
    if not eb.get("ready"):
        return {"error": eb.get("error", "backend not ready")}
    _reap_stale_logins()
    binp = _proxy_bin()
    name = (name or "").strip()
    auto_named = not name
    if not name:
        # Don't force the user to invent a name up front — auto-name to the
        # next free account-N; we rename it to the account's email after login.
        existing = {a["name"] for a in _parse_accounts()}
        i = 1
        while f"account-{i}" in existing:
            i += 1
        name = f"account-{i}"
    from openprogram._compat import (
        InteractivePty, interactive_pty_available, node_tool_cmd,
    )
    import time as _time

    if not interactive_pty_available():
        # Windows without pywinpty (or an exotic POSIX build): no pseudo-
        # terminal to drive the interactive browser login. The UI offers the
        # token-paste method instead; signal that distinctly.
        return {"error": "BROWSER_LOGIN_UNAVAILABLE",
                "detail": "Interactive browser sign-in needs a pseudo-terminal "
                          "that isn't available here. Use the token method: run "
                          "`claude setup-token` and paste the token."}

    # Strip inherited Claude credentials so the backend can't decide it's
    # "already authenticated" (e.g. off the worker's ANTHROPIC_API_KEY) and
    # skip the login — `add` needs a fresh browser OAuth for the NEW account.
    _strip = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_API_KEY",
              "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_BASE_URL"}
    env = {k: v for k, v in os.environ.items() if k not in _strip}
    if sys.platform != "win32":
        # POSIX headless workers (no GUI session): a no-op `open`/`xdg-open`
        # first on PATH so the backend doesn't block trying to launch a browser
        # — it prints the login URL regardless and the frontend opens it.
        # Windows has no such launcher to shim; `claude` opens the browser via
        # the OS handler and still prints the URL to the pty.
        import tempfile
        shim = tempfile.mkdtemp(prefix="op-noopen-")
        for _c in ("open", "xdg-open"):
            _p = os.path.join(shim, _c)
            with open(_p, "w") as _f:
                _f.write("#!/bin/sh\nexit 0\n")
            os.chmod(_p, 0o755)
        env["PATH"] = shim + os.pathsep + env.get("PATH", "")

    def _spawn_and_read_url():
        """Spawn ``profile add`` under a pseudo-terminal and read until the
        OAuth URL appears (or 30s elapse). Returns ``(driver, url|None,
        cleaned_out)``."""
        drv = InteractivePty(node_tool_cmd([binp, "profile", "add", name]), env=env)
        url, buf, end, answered = None, "", _time.time() + 30, False
        while _time.time() < end:
            data = drv.read_nonblocking(1.0)
            if not data:
                if not drv.alive:
                    break
                continue
            buf += data
            clean = _ANSI.sub("", buf)
            # The backend may first offer to import the terminal's existing
            # login ("Import as profile X? [Y/n]"). Decline — `add` means a NEW
            # account via browser OAuth, not re-adding the terminal's login.
            if not answered and ("import as profile" in clean.lower() or "[y/n]" in clean.lower()):
                drv.write("n\n")
                answered = True
            m = _OAUTH_URL_RE.search(clean)
            if m:
                url = m.group(0).rstrip(".")
                break
        return drv, url, _ANSI.sub("", buf)

    drv, url, last_out = _spawn_and_read_url()
    if not url:
        drv.kill()
        drv.close()
        # macOS: claude-code reads the login keychain (global service
        # "Claude Code-credentials", NOT isolated by CLAUDE_CONFIG_DIR). If any
        # Claude login is already present the proxy reports "already
        # authenticated" and skips the browser OAuth — so a *different* account
        # can't be added until the existing one is signed out. Drop the
        # duplicate profile the proxy made and tell the user how to proceed.
        if "already authenticated" in last_out.lower():
            try:
                _run_proxy(["profile", "remove", name])
            except Exception:
                pass
            return {"error": (
                "A Claude login already exists on this machine, so the browser "
                "sign-in was skipped and a new account couldn't be added. To "
                "add a different account, sign the existing one out first "
                "(run `claude logout` in a terminal), then try again."
            )}
        return {"error": "the backend didn't return a sign-in URL — try again."}
    # The proxy's paste-code flow wants the FULL string the callback page shows
    # — "<code>#<state>", not the bare ?code= param (which it rejects as
    # "Invalid code"). Stash the state from the login URL so submit can rebuild
    # the full string when the caller only has the bare code.
    import urllib.parse as _urlparse
    state = _urlparse.parse_qs(_urlparse.urlparse(url).query).get("state", [""])[0]
    session = secrets.token_hex(8)
    _PENDING_LOGINS[session] = {
        "driver": drv, "name": name, "auto": auto_named,
        "state": state, "started_at": _time.time(),
    }
    return {"session": session, "url": url, "name": name}


def _sync_keychain_to_profile(name: str) -> bool:
    """macOS: claude-code's OAuth stores the token in the login keychain
    (global, service ``Claude Code-credentials``), not in the per-profile
    ``CLAUDE_CONFIG_DIR``. The proxy isolates accounts by reading each
    profile's ``.credentials.json``, so a freshly-added account has no token
    file and reports "token expired". Copy the just-written keychain token
    into the profile's ``.credentials.json`` so the account works. No-op off
    macOS (there claude-code already writes a credentials file)."""
    import sys
    if sys.platform != "darwin":
        return False
    try:
        out = subprocess.run(
            ["security", "find-generic-password",
             "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return False
        import json
        json.loads(out.stdout)  # validate it's the oauth payload before writing
        pdir = os.path.join(_meridian_config_dir(), "profiles", name)
        os.makedirs(pdir, exist_ok=True)
        cred = os.path.join(pdir, ".credentials.json")
        with open(cred, "w") as f:
            f.write(out.stdout)
        os.chmod(cred, 0o600)
        return True
    except Exception:
        return False


def _finalize_added_account(entry: dict) -> str:
    """Post-completion bookkeeping shared by the paste-code and loopback paths.

    macOS: claude-code's OAuth writes the token to the login keychain, but the
    proxy reads each account's token from its profile's ``.credentials.json`` —
    so without this the brand-new account reports "token expired". Mirror the
    just-written keychain token into the profile file (no-op off macOS, where
    the token is already a file). Then rename an auto-named ``account-N`` to the
    account's email so the list is readable (the email metadata can lag the
    OAuth completion by a beat, so retry a few times)."""
    name = entry["name"]
    _sync_keychain_to_profile(name)
    if entry.get("auto"):
        import time as _t
        for _ in range(4):
            email = _email_for(name)
            if email:
                if _rename_profile(name, email):
                    return email
                return name
            _t.sleep(0.7)
    return name


def poll_add(session: str) -> dict:
    """Check an in-flight browser login. The Claude CLI may complete the OAuth
    on its own via a localhost loopback redirect (the page just says "you're
    all set up" — no code to paste); in that case the backend process exits and
    we finalize here. Returns ``{done, ok?, name?, error?}``; ``done=False``
    means keep waiting (the user hasn't finished, or it's the manual paste-code
    fallback)."""
    entry = _PENDING_LOGINS.get(session)
    if not entry:
        return {"done": True, "ok": False, "error": "no pending login (it may have timed out)"}
    drv = entry["driver"]
    if drv.alive:
        return {"done": False}
    # Process exited by itself → loopback completed (or it failed). Finalize.
    _PENDING_LOGINS.pop(session, None)
    try:
        rc = drv.wait(timeout=5)
    except Exception:  # noqa: BLE001
        rc = 1
    finally:
        drv.close()
    if rc != 0:
        return {"done": True, "ok": False, "error": "login didn't complete — try again."}
    return {"done": True, "ok": True, "name": _finalize_added_account(entry)}


def submit_login_code(session: str, code: str) -> dict:
    """Finish a web login by feeding the pasted ``code`` to the waiting process
    (the manual fallback when the page shows a code instead of auto-completing).
    Tolerates a process that already exited on its own (loopback)."""
    entry = _PENDING_LOGINS.pop(session, None)
    if not entry:
        return {"ok": False, "error": "no pending login (it may have timed out)"}
    drv = entry["driver"]
    try:
        if drv.alive:
            # The callback page shows "<code>#<state>" and the proxy needs that
            # whole string — a bare code (just the ?code= query param) is
            # rejected as "Invalid code". Rebuild it from the stashed state when
            # the caller passed only the code.
            full_code = (code or "").strip()
            state = entry.get("state", "")
            if state and "#" not in full_code:
                full_code = f"{full_code}#{state}"
            drv.write(full_code + "\n")
            # Exchanging the code for tokens hits claude.com; on slow / proxied
            # networks that can exceed a minute, and the code is single-use, so
            # give it generous time before giving up.
            rc = drv.wait(timeout=240)
        else:
            # Already completed on its own (loopback) — just collect the result.
            rc = drv.wait(timeout=5)
    except Exception as e:  # noqa: BLE001
        drv.kill()
        return {"ok": False, "error": f"login did not complete: {e}"}
    finally:
        drv.close()
    if rc != 0:
        return {"ok": False, "error": "login did not complete"}
    return {"ok": True, "name": _finalize_added_account(entry)}


def add_with_token(name: str, token: str) -> dict:
    """Add a Claude account from a setup-token — headless and cross-platform.

    The user mints the token out-of-band with ``claude setup-token`` (the
    Anthropic Claude Code CLI) and pastes it; we register it via
    ``meridian profile add NAME --oauth-token <token>``, a plain non-interactive
    subprocess that works identically on every OS (no pseudo-terminal needed).

    Trade-off vs the browser flow: a setup-token carries no refresh token, so
    the account won't auto-renew (~1y lifetime) and lists without an email."""
    token = (token or "").strip()
    if not token:
        return {"ok": False, "error": "Paste the token from `claude setup-token`."}
    if not token.startswith("sk-ant-"):
        return {"ok": False,
                "error": "That doesn't look like a Claude token — it should "
                         "start with `sk-ant-`. Run `claude setup-token` to "
                         "generate one."}
    eb = ensure_backend()
    if not eb.get("ready"):
        return {"ok": False, "error": eb.get("error", "backend not ready")}
    binp = _proxy_bin()
    name = (name or "").strip()
    if not name:
        existing = {a["name"] for a in _parse_accounts()}
        i = 1
        while f"account-{i}" in existing:
            i += 1
        name = f"account-{i}"
    from openprogram._compat import node_tool_cmd
    # Strip inherited creds so the backend records the pasted token, not an
    # ambient one off the worker's environment.
    _strip = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_API_KEY",
              "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_BASE_URL"}
    env = {k: v for k, v in os.environ.items() if k not in _strip}
    try:
        p = subprocess.run(
            node_tool_cmd([binp, "profile", "add", name, "--oauth-token", token]),
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=120, env=env,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "The backend took too long to add the account."}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Could not add the account: {e}"}
    if p.returncode != 0:
        lines = [ln for ln in _ANSI.sub("", (p.stderr or "") + (p.stdout or "")).splitlines() if ln.strip()]
        detail = lines[-1].strip() if lines else "the backend rejected the token."
        return {"ok": False, "error": f"Could not add the account: {detail}"}
    return {"ok": True, "name": name}


def prerequisites() -> dict:
    """What the UI needs to guide Claude-account setup: which pieces are present
    and the install command for any that are missing.

    Both login methods ultimately need the Claude Code CLI (``claude``) — it's
    what performs the Anthropic OAuth (Meridian shells out to it for the browser
    flow; the token flow needs ``claude setup-token`` to mint the token)."""
    from openprogram._compat import interactive_pty_available
    return {
        "claude_installed": _npm_bin("claude") is not None,
        "claude_install_cmd": "npm install -g @anthropic-ai/claude-code",
        "backend_installed": _proxy_bin() is not None,
        "backend_install_cmd": "npm install -g @rynfar/meridian",
        # Browser sign-in needs a pseudo-terminal: always on POSIX, needs
        # pywinpty on Windows. The token paste works anywhere.
        "browser_login": interactive_pty_available(),
        "token_login": True,
        "token_help": "Run `claude setup-token` in a terminal, then paste the token here.",
    }


def remove_account(name: str) -> dict:
    """Remove an account; clear the active pin if it pointed there."""
    name = (name or "").strip()
    if not name:
        return {"removed": False, "error": "an account name is required"}
    if not _proxy_bin():
        return {"removed": False, "error": "backend not installed"}
    rc, _out = _run_proxy(["profile", "remove", name])
    cleared = False
    if rc == 0 and _active_account() == name:
        from openprogram.webui._model_catalog.storage import set_provider_config
        set_provider_config("claude-code", {"meridian_profile": ""})
        cleared = True
    return {"removed": rc == 0, "name": name, "cleared_active": cleared}


def activate_account(name: str) -> dict:
    """Set ``name`` as the account claude-code runs on. Empty deactivates —
    no account is active, so claude-code has nothing to run on until one is
    activated again."""
    from openprogram.webui._model_catalog.storage import set_provider_config
    name = (name or "").strip()
    set_provider_config("claude-code", {"meridian_profile": name})
    return {"active": name}


def rename_account(old: str, new: str) -> dict:
    """Rename a saved account; if it was the active one, keep it active under
    the new name."""
    old, new = (old or "").strip(), (new or "").strip()
    if not new:
        return {"ok": False, "error": "a new name is required"}
    was_active = _active_account() == old
    if not _rename_profile(old, new):
        return {"ok": False, "error": "couldn't rename — name already taken or not found"}
    if was_active:
        from openprogram.webui._model_catalog.storage import set_provider_config
        set_provider_config("claude-code", {"meridian_profile": new})
    return {"ok": True, "name": new}


def _print_install_hint() -> None:
    print("\n  The Claude backend isn't ready yet. It installs and starts "
          "automatically\n  the first time you add an account — just run:")
    print("    openprogram providers claude-code accounts add")


# ---------------------------------------------------------------------------
# verbs
# ---------------------------------------------------------------------------

def _cmd_add(name: str) -> int:
    name = (name or "").strip()
    print("Preparing the Claude backend…")
    eb = ensure_backend()
    if not eb.get("ready"):
        print(eb.get("error", "backend not ready"), file=sys.stderr)
        return 1
    auto_named = not name
    if not name:
        existing = {a["name"] for a in _parse_accounts()}
        i = 1
        while f"account-{i}" in existing:
            i += 1
        name = f"account-{i}"
    print("A browser window will open — sign in with the account you want to "
          "add.\nYour terminal chat account is not affected.\n")
    # Strip inherited Claude credentials so it does a real browser OAuth (not
    # "already authenticated" off ANTHROPIC_API_KEY). Inherit stdio so the
    # import-prompt + paste-code steps work in the terminal.
    _strip = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_API_KEY",
              "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_BASE_URL"}
    env = {k: v for k, v in os.environ.items() if k not in _strip}
    from openprogram._compat import node_tool_cmd
    try:
        # node_tool_cmd so a Windows ``.cmd`` shim runs via cmd.exe (bare argv
        # of a .cmd raises WinError 193). Inherits this terminal's stdio so the
        # interactive browser login + code paste work directly.
        rc = subprocess.run(node_tool_cmd([_proxy_bin(), "profile", "add", name]),
                            env=env).returncode
    except Exception as e:  # noqa: BLE001
        print(f"Adding the account failed: {e}", file=sys.stderr)
        return 1
    if rc != 0:
        return rc
    final = name
    if auto_named:
        email = _email_for(name)
        if email and _rename_profile(name, email):
            final = email
    print(f"\n✓ Added Claude account {final!r}. Activate it for OpenProgram:")
    print(f"  openprogram providers claude-code accounts use {final}")
    return 0


def _cmd_remove(name: str) -> int:
    name = (name or "").strip()
    if not name:
        print("An account label is required: openprogram providers claude-code "
              "accounts remove <name>", file=sys.stderr)
        return 2
    if not _proxy_bin():
        _print_install_hint()
        return 1
    rc, out = _run_proxy(["profile", "remove", name])
    # Surface a cleaned line, not the raw backend output.
    msg = _ANSI.sub("", out).strip().splitlines()
    print(f"✓ Removed Claude account {name!r}." if rc == 0
          else (msg[0] if msg else f"Could not remove {name!r}."))
    if rc == 0 and _active_account() == name:
        # It was the active one — clear the pin so we don't point at a gone account.
        from openprogram.webui._model_catalog.storage import set_provider_config
        set_provider_config("claude-code", {"meridian_profile": ""})
        print("  (It was active — OpenProgram is now unset; activate another "
              "with `accounts use <name>`.)")
    return rc


def _cmd_list() -> int:
    if not _proxy_bin():
        _print_install_hint()
        return 1
    accounts = _parse_accounts()
    active = _active_account()
    if not accounts:
        print("No Claude accounts yet. Add one:")
        print("  openprogram providers claude-code accounts add <name>")
        return 0
    print("Claude accounts (→ = active for OpenProgram):")
    for a in accounts:
        mark = "→" if a["name"] == active else " "
        print(f"  {mark} {a['name']:18s} {a.get('email', '')}")
    if active and active not in {a["name"] for a in accounts}:
        print(f"\n  Active account {active!r} isn't in the list — add it with "
              f"`accounts add {active}`.")
    return 0


def _cmd_use(name: str) -> int:
    name = (name or "").strip()
    if not name:
        print("An account label is required: openprogram providers claude-code "
              "accounts use <name>", file=sys.stderr)
        return 2
    from openprogram.webui._model_catalog.storage import set_provider_config

    set_provider_config("claude-code", {"meridian_profile": name})
    print(f"✓ OpenProgram now runs claude-code on the Claude account {name!r}.")
    print("  Takes effect on the next request — no restart. Your terminal chat "
          "account is unaffected.")
    accounts = {a["name"] for a in _parse_accounts()} if _proxy_bin() else set()
    if accounts and name not in accounts:
        print(f"  Note: {name!r} isn't added yet — add it with "
              f"`openprogram providers claude-code accounts add {name}`.")
    return 0


def _cmd_deactivate() -> int:
    activate_account("")
    print("✓ Deactivated. No Claude account is active — claude-code has none "
          "to run on until you activate one.")
    return 0


def _cmd_rename(old: str, new: str) -> int:
    r = rename_account(old, new)
    if r.get("ok"):
        print(f"✓ Renamed {old!r} → {r['name']!r}.")
        return 0
    print(r.get("error", "rename failed"), file=sys.stderr)
    return 1


def _cmd_status() -> int:
    binp = _proxy_bin()
    ready, url = _backend_ready()
    active = _active_account()

    print("claude-code — Claude accounts")
    print(f"  backend   : {'ready (' + url + ')' if ready else 'not ready'}")
    print(f"  active    : {active!r}" if active
          else "  active    : (none — claude-code follows your terminal login)")

    if binp:
        accounts = _parse_accounts()
        print("\n  accounts:")
        if accounts:
            for a in accounts:
                mark = "→" if a["name"] == active else " "
                print(f"    {mark} {a['name']:18s} {a.get('email', '')}")
        else:
            print("    (none yet)")
    if not binp or not ready:
        _print_install_hint()
    else:
        print("\n  Add an account:     openprogram providers claude-code accounts add <name>")
        print("  Activate one:       openprogram providers claude-code accounts use <name>")
    return 0


__all__ = [
    "build_parser", "dispatch", "ensure_backend",
    "accounts_summary", "start_add", "submit_login_code",
    "remove_account", "activate_account", "rename_account",
]
