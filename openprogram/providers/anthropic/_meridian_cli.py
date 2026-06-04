"""CLI for the Meridian account (profile) that OpenProgram's ``claude-code``
provider is pinned to.

Background: ``claude-code`` reaches Claude through a local Meridian proxy,
which can hold several Claude subscriptions as named *profiles*. Pinning a
profile decouples OpenProgram's Claude account from whatever the terminal
``claude auth login`` last logged in (see
docs/design/claude-code-meridian-profile.md).

Division of labour:

  * Meridian owns **login** — adding an account is a browser OAuth flow run
    by Meridian's own CLI (``meridian profile add <name>``). A human does
    that step.
  * OpenProgram owns the **binding** — which existing profile its
    claude-code traffic uses. That's pure config, so it's fully
    command-line drivable here (``use`` / ``clear``) and inspectable
    (``status`` / ``list``). An agent can run everything except the
    browser login.

Verbs (registered as ``openprogram providers meridian <verb>``):

  * ``status``         — is Meridian installed / running, what is OpenProgram
                         pinned to, what profiles exist.
  * ``list``           — the profiles Meridian knows (passthrough).
  * ``use <name>``     — pin OpenProgram's claude-code to that profile.
  * ``clear``          — unpin (follow Meridian's default / keychain login).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys


_DEFAULT_PROXY_URL = "http://localhost:3456"


def build_parser(meridian_sub: "argparse._SubParsersAction") -> None:
    """Register the meridian verbs on the ``providers meridian`` subparser."""
    meridian_sub.add_parser(
        "status",
        help="Show Meridian install/run state, the pinned profile, and "
             "available profiles.",
    )
    meridian_sub.add_parser(
        "list", help="List the Claude accounts (profiles) Meridian knows.",
    )
    p_use = meridian_sub.add_parser(
        "use",
        help="Pin OpenProgram's claude-code traffic to a Meridian profile "
             "(decouples it from the terminal `claude auth login`).",
    )
    p_use.add_argument("profile_name", help="Meridian profile name to pin to.")
    meridian_sub.add_parser(
        "clear",
        help="Unpin — claude-code follows Meridian's default/active profile "
             "(or the keychain login) again.",
    )


def dispatch(args: argparse.Namespace) -> int:
    verb = getattr(args, "meridian_cmd", None)
    if verb == "status":
        return _cmd_status()
    if verb == "list":
        return _cmd_list()
    if verb == "use":
        return _cmd_use(args.profile_name)
    if verb == "clear":
        return _cmd_clear()
    print(
        "Usage: openprogram providers meridian <verb>\n"
        "Verbs: status, list, use <name>, clear",
        file=sys.stderr,
    )
    return 2


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _meridian_bin() -> str | None:
    return shutil.which("meridian")


def _run_meridian(args: list[str]) -> tuple[int, str]:
    binp = _meridian_bin()
    if not binp:
        return 127, "meridian is not installed (npm install -g @rynfar/meridian)"
    try:
        p = subprocess.run([binp, *args], capture_output=True, text=True, timeout=30)
    except Exception as e:  # noqa: BLE001
        return 1, f"failed to run meridian: {e}"
    return p.returncode, (p.stdout + p.stderr)


def _current_pin() -> str | None:
    from ._claude_max_proxy_registry import meridian_profile
    return meridian_profile()


def _proxy_alive() -> tuple[bool, str]:
    url = os.environ.get("CLAUDE_MAX_PROXY_URL") or _DEFAULT_PROXY_URL
    try:
        import urllib.request

        urllib.request.urlopen(url.rstrip("/") + "/v1/models", timeout=3)
        return True, url
    except Exception:
        return False, url


# ---------------------------------------------------------------------------
# verbs
# ---------------------------------------------------------------------------

def _cmd_use(name: str) -> int:
    name = (name or "").strip()
    if not name:
        print("A profile name is required: openprogram providers meridian use <name>",
              file=sys.stderr)
        return 2
    from openprogram.webui._model_catalog.storage import set_provider_config

    set_provider_config("claude-code", {"meridian_profile": name})
    print(f"✓ OpenProgram's claude-code is now pinned to Meridian profile {name!r}.")
    print("  Takes effect on the next request — no restart. The terminal "
          "Claude Code login is unaffected.")
    # Gentle nudge if the named profile isn't among Meridian's known ones.
    rc, out = _run_meridian(["profile", "list"])
    if rc == 0 and name not in out:
        print(f"  Note: {name!r} isn't in `meridian profile list` yet — add it "
              f"with `meridian profile add {name}` (browser login).")
    return 0


def _cmd_clear() -> int:
    from openprogram.webui._model_catalog.storage import set_provider_config

    set_provider_config("claude-code", {"meridian_profile": ""})
    print("✓ Unpinned. claude-code now follows Meridian's default/active "
          "profile (or the keychain `claude auth login`).")
    return 0


def _cmd_list() -> int:
    rc, out = _run_meridian(["profile", "list"])
    print(out.rstrip())
    cur = _current_pin()
    print(
        f"\nOpenProgram is pinned to: {cur!r}" if cur
        else "\nOpenProgram is not pinned (claude-code follows Meridian's default)."
    )
    return 0 if rc == 0 else rc


def _cmd_status() -> int:
    binp = _meridian_bin()
    alive, url = _proxy_alive()
    cur = _current_pin()

    print("Meridian (claude-code proxy) status")
    print(f"  installed : {binp or 'NO — npm install -g @rynfar/meridian'}")
    print(f"  running   : {'yes (' + url + ')' if alive else 'NO — start it with: meridian'}")
    print(f"  pinned to : {cur!r}" if cur
          else "  pinned to : (none — follows Meridian default / keychain login)")

    if binp:
        rc, out = _run_meridian(["profile", "list"])
        print("\nMeridian profiles:")
        for line in out.rstrip().splitlines():
            print(f"  {line}")

    print("\nAdd an account (browser login, you do this):")
    print("  meridian profile add <name>")
    print("Pin OpenProgram to it (agent can run this):")
    print("  openprogram providers meridian use <name>")
    return 0


__all__ = ["build_parser", "dispatch"]
