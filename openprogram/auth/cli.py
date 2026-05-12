"""Command-line entry points for auth v2.

Wired into ``openprogram`` via ``openprogram providers <subcommand>``. The
goal is feature parity with ``gh auth login`` / ``codex login`` — a
single interactive wizard that handles every provider's setup without
the user needing to know which auth kind it is.

Subcommands:

  * ``openprogram providers login <provider>`` — interactive wizard. Detects
    what's possible for the provider (paste key / device-code / import
    from another CLI) and drives the right flow.
  * ``openprogram providers list`` — tabular view of pools per profile with
    masked secret previews.
  * ``openprogram providers discover`` — non-destructive scan of external
    sources. Shows what could be imported but doesn't commit.
  * ``openprogram providers adopt`` — accept a previously-discovered
    credential into the store. Usually follows ``discover``.
  * ``openprogram providers logout <provider> [--profile P]`` — remove all
    credentials for the provider in the given profile and print any
    non-executable :class:`RemovalStep` so the user knows what they
    still need to clean up manually.
  * ``openprogram providers profiles {list,create,delete} [...]`` — profile
    CRUD without requiring the WebUI.
  * ``openprogram providers status <provider> [--profile P]`` — show the
    active credential for a provider and whether AuthManager would
    resolve it right now.

Design constraints:

  * no webserver or browser — this must work over SSH
  * no dependency on the WebUI routes — CLI and webui both hit
    :mod:`auth.store` + :mod:`auth.manager` directly
  * secrets are masked on display; paste flows read via
    :func:`getpass.getpass` so the key doesn't appear in shell history
    or in terminal scroll-back of a screen-sharing session
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
import time
from typing import Any, Optional

from .aliases import known_aliases, resolve as _resolve_alias
from .context import auth_scope
from .manager import AuthManager, get_manager
from .profiles import (
    DEFAULT_PROFILE_NAME,
    ProfileManager,
    get_profile_manager,
)
from .store import AuthStore, get_store
from .types import (
    ApiKeyPayload,
    AuthConfigError,
    AuthError,
    CliDelegatedPayload,
    Credential,
    CredentialPool,
    DeviceCodePayload,
    ExternalProcessPayload,
    OAuthPayload,
    RemovalStep,
)


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------

def build_parser(sub: "argparse._SubParsersAction") -> None:
    """Register credential-management verbs directly on the parent.

    Per docs/design/cli-naming.md, commands have the shape
    ``<noun> [<noun> ...] <verb>``. This function is called with the
    ``providers`` subparser as its parent, so the verbs land as
    ``providers login``, ``providers list``, etc. ``profiles`` is the
    only nested noun (`providers profiles list` / `create` / `delete`).

    Expected use from :func:`openprogram.cli.main`::

        p_providers = sub.add_parser("providers", ...)
        providers_sub = p_providers.add_subparsers(dest="providers_cmd")
        from openprogram.auth.cli import build_parser
        build_parser(providers_sub)
    """
    auth_sub = sub

    # login
    p_login = auth_sub.add_parser("login", help="Log into a provider")
    p_login.add_argument("provider", help="Provider id (e.g. openai-codex, anthropic)")
    p_login.add_argument("--profile", default=DEFAULT_PROFILE_NAME,
                         help=f"Profile (default: {DEFAULT_PROFILE_NAME})")
    p_login.add_argument("--method", default=None,
                         help="Force a login method (api_key / import_from_cli / device_code). "
                              "If omitted, the wizard auto-selects the best available.")

    # list
    p_list = auth_sub.add_parser("list", help="List pools per profile")
    p_list.add_argument("--profile", default=None,
                        help="Filter to one profile (default: all)")
    p_list.add_argument("--json", action="store_true", help="Output JSON")

    # discover
    p_disc = auth_sub.add_parser("discover", help="Scan external sources")
    p_disc.add_argument("--json", action="store_true", help="Output JSON")

    # adopt
    p_adopt = auth_sub.add_parser("adopt",
        help="Adopt a discovered credential into the store")
    p_adopt.add_argument("source_id", nargs="?", default=None,
        help="Source id from `discover` output (e.g. codex_cli, "
             "env:OPENAI_API_KEY). Omit when using --all.")
    p_adopt.add_argument("--profile", default=DEFAULT_PROFILE_NAME,
                         help=f"Target profile (default: {DEFAULT_PROFILE_NAME})")
    p_adopt.add_argument("--all", dest="adopt_all", action="store_true",
                         help="Adopt every credential discover() finds. "
                              "Skips pools that already contain the same credential_id.")

    # logout
    p_logout = auth_sub.add_parser("logout", help="Remove credentials for a provider")
    p_logout.add_argument("provider")
    p_logout.add_argument("--profile", default=DEFAULT_PROFILE_NAME)
    p_logout.add_argument("--yes", action="store_true", help="Skip confirmation")

    # status
    p_status = auth_sub.add_parser("status", help="Check a provider's current credential")
    p_status.add_argument("provider")
    p_status.add_argument("--profile", default=DEFAULT_PROFILE_NAME)

    # doctor — diagnostic report over every pool
    p_doctor = auth_sub.add_parser(
        "doctor", help="Diagnose credentials (expiry, refresh, cooldown, conflicts)",
    )
    p_doctor.add_argument("--json", action="store_true", help="Output JSON")

    # setup — interactive wizard that chains discover → login → status
    auth_sub.add_parser(
        "setup", help="Interactive first-time setup",
    )

    # aliases — show the short-name → canonical table
    p_aliases = auth_sub.add_parser(
        "aliases", help="List provider short-name aliases",
    )
    p_aliases.add_argument("--json", action="store_true", help="Output JSON")

    # profiles (plural noun, per CLI naming convention — see
    # docs/design/cli-naming.md). Verbs follow: list/create/delete.
    p_profiles = auth_sub.add_parser("profiles", help="Profile management")
    prof_sub = p_profiles.add_subparsers(dest="profiles_cmd", metavar="verb")
    prof_sub.add_parser("list", help="List profiles")
    pc = prof_sub.add_parser("create", help="Create a profile")
    pc.add_argument("name")
    pc.add_argument("--display-name", default="")
    pc.add_argument("--description", default="")
    pd = prof_sub.add_parser("delete", help="Delete a profile")
    pd.add_argument("name")
    pd.add_argument("--yes", action="store_true", help="Skip confirmation")


def dispatch(args: argparse.Namespace) -> int:
    """Run the selected credential-management verb.

    Reads :attr:`providers_cmd` from the argparse namespace (the parent
    subparser dest) rather than any private auth-scoped dest. Returns a
    shell-style exit code so the outer ``main()`` can propagate to
    ``sys.exit``.
    """
    cmd = args.providers_cmd
    if cmd == "login":
        return _cmd_login(_resolve_alias(args.provider), args.profile, args.method)
    if cmd == "list":
        return _cmd_list(args.profile, args.json)
    if cmd == "discover":
        return _cmd_discover(args.json)
    if cmd == "adopt":
        if getattr(args, "adopt_all", False):
            return _cmd_adopt_all(args.profile)
        if args.source_id is None:
            print("Usage: openprogram providers adopt <source_id> | --all",
                  file=sys.stderr)
            return 2
        return _cmd_adopt(args.source_id, args.profile)
    if cmd == "logout":
        return _cmd_logout(
            _resolve_alias(args.provider), args.profile, skip_confirm=args.yes,
        )
    if cmd == "status":
        return _cmd_status(_resolve_alias(args.provider), args.profile)
    if cmd == "doctor":
        return _cmd_doctor(args.json)
    if cmd == "setup":
        return _cmd_setup()
    if cmd == "aliases":
        return _cmd_aliases(args.json)
    if cmd == "profiles":
        return _dispatch_profiles(args)
    # No subcommand — print the help hint.
    print("Usage: openprogram providers <verb>\n"
          "Verbs: login, logout, list, status, discover, adopt, "
          "doctor, setup, aliases, profiles",
          file=sys.stderr)
    return 2


def _dispatch_profiles(args: argparse.Namespace) -> int:
    pc = args.profiles_cmd
    if pc == "list":
        return _cmd_profile_list()
    if pc == "create":
        return _cmd_profile_create(args.name, args.display_name, args.description)
    if pc == "delete":
        return _cmd_profile_delete(args.name, args.yes)
    print("Usage: openprogram providers profiles <verb>\n"
          "Verbs: list, create, delete", file=sys.stderr)
    return 2


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _mask(secret: str, keep_prefix: int = 6, keep_suffix: int = 4) -> str:
    if not secret:
        return ""
    if len(secret) <= keep_prefix + keep_suffix + 1:
        return "*" * len(secret)
    return f"{secret[:keep_prefix]}…{secret[-keep_suffix:]}"


def _payload_summary(cred: Credential) -> str:
    p = cred.payload
    if isinstance(p, ApiKeyPayload):
        return f"api_key {_mask(p.api_key)}"
    if isinstance(p, OAuthPayload):
        extra = " (+refresh)" if p.refresh_token else ""
        expiry = _fmt_expiry(p.expires_at_ms)
        return f"oauth {_mask(p.access_token)}{extra} exp={expiry}"
    if isinstance(p, DeviceCodePayload):
        return f"device_code {_mask(p.access_token)} exp={_fmt_expiry(p.expires_at_ms)}"
    if isinstance(p, CliDelegatedPayload):
        return f"cli_delegated → {p.store_path}"
    if isinstance(p, ExternalProcessPayload):
        return f"external_process {' '.join(p.command)}"
    return cred.kind


def _fmt_expiry(ms: int) -> str:
    if not ms:
        return "unknown"
    delta = ms - int(time.time() * 1000)
    if delta < 0:
        return f"{_fmt_duration(-delta)} ago (expired)"
    return f"in {_fmt_duration(delta)}"


def _fmt_duration(ms: int) -> str:
    seconds = ms // 1000
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86_400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86_400}d"


# ---------------------------------------------------------------------------
# login — interactive wizard
# ---------------------------------------------------------------------------

def _cmd_login(provider: str, profile: str, method: Optional[str]) -> int:
    store = get_store()
    choices = _available_login_methods(provider)
    if not choices:
        print(f"No login method implemented for provider {provider!r}.", file=sys.stderr)
        return 1

    if method is None:
        # Prefer the arrow-key picker; if questionary isn't installed
        # it returns None and we fall through to the numeric prompt.
        from .interactive import pick_login_method_interactive
        chosen_or_none = pick_login_method_interactive(provider, choices)
        if chosen_or_none is not None:
            chosen = chosen_or_none
        else:
            print(f"Login to {provider} (profile: {profile})")
            print("Available methods:")
            for i, (mid, label) in enumerate(choices, 1):
                print(f"  {i}. {mid:24s} — {label}")
            try:
                pick = input(f"Pick a method [1-{len(choices)}] (default 1): ").strip() or "1"
                chosen = choices[int(pick) - 1][0]
            except (EOFError, ValueError, IndexError):
                print("Aborted.", file=sys.stderr)
                return 1
    else:
        chosen = method
        if chosen not in {m for m, _ in choices}:
            print(f"Method {chosen!r} not available for {provider}. "
                  f"Try: {', '.join(m for m, _ in choices)}", file=sys.stderr)
            return 1

    try:
        cred = _run_login_method(provider, profile, chosen)
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        return 130
    except AuthError as e:
        print(f"Login failed: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Login failed: {e.__class__.__name__}: {e}", file=sys.stderr)
        return 1

    store.add_credential(cred)
    saved_provider = cred.provider_id
    saved_profile = cred.profile_id
    print(f"\n✓ Saved credential {cred.credential_id} for "
          f"{saved_provider}/{saved_profile}")
    print(f"  kind: {cred.kind}")
    print(f"  preview: {_payload_summary(cred)}")
    print(f"  store: {store.root}/{saved_provider}/{saved_profile}.json")
    if saved_provider != provider:
        print(f"\n  Note: routed to {saved_provider!r} (not {provider!r}) "
              f"because that's where this credential shape belongs.")
    return 0


def _available_login_methods(provider: str) -> list[tuple[str, str]]:
    """Enumerate login methods the CLI can drive for ``provider``.

    The ordering matters — the first entry is what ``--method`` defaults
    to if the user just hits Enter. For providers with a real OAuth
    flow we offer ONLY that flow (matches OpenClaw / Codex CLI UX —
    one button, opens a browser). API-key paste / import-from-cli are
    kept as the default for providers where OAuth isn't available.
    """
    if provider == "openai-codex":
        # Codex has a first-class OAuth flow; no reason to show the
        # clunky "paste your CLI's auth.json" or "paste an api_key"
        # options. The Codex runtime can't even use an api_key.
        return [("pkce_oauth", "Sign in with ChatGPT (opens browser)")]

    choices: list[tuple[str, str]] = []
    if provider == "anthropic":
        choices.append(("import_from_cli", "Import from Claude Code's ~/.claude/.credentials.json"))
    if provider == "gemini-subscription":
        choices.append(("import_from_cli", "Import from ~/.gemini/oauth_creds.json"))
    if provider == "qwen":
        choices.append(("import_from_cli", "Import from ~/.qwen/oauth_creds.json"))
    choices.append(("api_key", "Paste a static API key"))
    return choices


def _run_login_method(provider: str, profile: str, method: str) -> Credential:
    if method == "api_key":
        return _login_paste_api_key(provider, profile)
    if method == "import_from_cli":
        return _login_import_from_cli(provider, profile)
    if method == "pkce_oauth":
        return _login_pkce_oauth(provider, profile)
    raise AuthConfigError(f"unsupported method: {method!r}")


# ---------------------------------------------------------------------------
# Terminal LoginUi — threads PKCE's async flow through stdout/stdin.
# ---------------------------------------------------------------------------

class _TerminalLoginUi:
    """LoginUi bound to stdout/stdin + the system browser.

    Used by `providers login` to drive PKCE / device-code flows. The
    PKCE method runs an asyncio coroutine; we pass this object in so it
    can open the browser and prompt for a pasted redirect URL when the
    localhost callback can't reach us (SSH sessions, firewalls).
    """

    async def open_url(self, url: str) -> None:
        import webbrowser
        print(f"\nOpen this URL to continue sign-in:\n  {url}\n")
        try:
            webbrowser.open(url)
        except Exception:
            # Best-effort — if no browser, user pastes the URL manually.
            pass

    async def prompt(self, message: str, *, secret: bool = False) -> str:
        loop = asyncio.get_running_loop()
        if secret:
            return (await loop.run_in_executor(None, getpass.getpass, f"{message}: ")).strip()
        return (await loop.run_in_executor(None, input, f"{message}: ")).strip()

    async def show_progress(self, message: str) -> None:
        print(message, flush=True)

    async def show_code(self, user_code: str, verification_uri: str) -> None:
        print(f"\nGo to {verification_uri} and enter code: {user_code}\n", flush=True)


_LOGIN_NOTES: dict[str, tuple[str, list[str]]] = {
    "openai-codex": (
        "Sign in with ChatGPT",
        [
            "Browser will open for OpenAI authentication.",
            "Complete sign-in and the callback will finish automatically.",
            "Requires a ChatGPT Plus / Pro / Team / Enterprise account.",
            "OAuth callback listens on localhost:1455 — don't have another",
            "codex / pi process holding that port.",
        ],
    ),
}


def _login_pkce_oauth(provider: str, profile: str) -> Credential:
    """Run a PKCE OAuth flow for `provider`, blocking the CLI until done."""
    from openprogram.auth.methods.pkce_oauth import PkceLoginMethod
    from .tui import print_note

    if provider == "openai-codex":
        from openprogram.providers.openai_codex import auth_adapter
        config = auth_adapter.build_pkce_config()
    else:
        raise AuthConfigError(f"no PKCE config registered for {provider!r}")

    title, body = _LOGIN_NOTES.get(provider, (f"{provider} OAuth", []))
    if body:
        print_note(title, body)

    method = PkceLoginMethod(provider_id=provider, config=config, profile_id=profile)
    ui = _TerminalLoginUi()
    return asyncio.run(method.run(ui))


def _login_paste_api_key(provider: str, profile: str) -> Credential:
    key = getpass.getpass(f"Paste API key for {provider} (hidden): ").strip()
    if not key:
        raise AuthConfigError("empty API key — nothing to save")
    return Credential(
        provider_id=provider,
        profile_id=profile,
        kind="api_key",
        payload=ApiKeyPayload(api_key=key),
        source="cli_paste",
        metadata={},
    )


def _login_import_from_cli(provider: str, profile: str) -> Credential:
    """Delegate to the per-provider adapter's ``import_from_*`` helper.

    Adapters produce either writable OAuth (Codex — we rotate) or
    delegated read-only (Anthropic/Gemini/Qwen — external CLI rotates).
    The distinction is invisible here; we just hand off."""
    if provider == "openai-codex":
        from openprogram.providers.openai_codex import auth_adapter
        cred = auth_adapter.import_from_codex_file(profile_id=profile)
        if cred is None:
            raise AuthConfigError(
                f"{auth_adapter.codex_auth_path()} not found. "
                f"Run `codex login --device-auth` first, then re-run this command."
            )
        return cred
    if provider == "anthropic":
        from openprogram.providers.anthropic import auth_adapter
        cred = auth_adapter.import_from_claude_code(profile_id=profile)
        if cred is None:
            raise AuthConfigError(
                f"{auth_adapter.claude_code_credentials_path()} not found. "
                f"Run `claude login` (Claude Code CLI) first."
            )
        return cred
    if provider == "gemini-subscription":
        from openprogram.providers.google_gemini_cli import auth_adapter
        cred = auth_adapter.import_from_gemini_cli(profile_id=profile)
        if cred is None:
            raise AuthConfigError(
                f"{auth_adapter.gemini_cli_credentials_path()} not found. "
                f"Run `gemini auth login` first."
            )
        return cred
    if provider == "qwen":
        from openprogram.auth.sources.qwen_cli import QwenCliSource
        src = QwenCliSource(profile_id=profile)
        creds = src.try_import(get_profile_manager().get_profile(profile).root)
        if not creds:
            raise AuthConfigError(
                "~/.qwen/oauth_creds.json not found. Run `qwen login` first."
            )
        return creds[0]
    raise AuthConfigError(f"no import-from-CLI adapter for {provider!r}")


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def _cmd_list(profile_filter: Optional[str], as_json: bool) -> int:
    store = get_store()
    pm = get_profile_manager()
    pools = store.list_pools()
    if profile_filter:
        pools = [p for p in pools if p.profile_id == profile_filter]

    if as_json:
        out = [
            {
                "provider_id": p.provider_id,
                "profile_id": p.profile_id,
                "strategy": p.strategy,
                "credentials": [
                    {
                        "id": c.credential_id,
                        "kind": c.kind,
                        "preview": _payload_summary(c),
                        "status": c.status,
                        "read_only": c.read_only,
                    }
                    for c in p.credentials
                ],
            }
            for p in pools
        ]
        print(json.dumps(out, indent=2))
        return 0

    if not pools:
        print("No credential pools yet. Try:")
        print("  openprogram providers discover        # scan for existing credentials")
        print("  openprogram providers login <prov>    # add one manually")
        return 0

    print(f"{'provider':28s}  {'profile':16s}  credential")
    for p in pools:
        for c in p.credentials:
            ro = " [read-only]" if c.read_only else ""
            print(f"{p.provider_id:28s}  {p.profile_id:16s}  "
                  f"{c.credential_id} — {_payload_summary(c)}{ro}")
    return 0


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------

def _cmd_discover(as_json: bool) -> int:
    from openprogram.auth.sources import (
        ClaudeCodeSource,
        CodexCliSource,
        EnvApiKeySource,
        GhCliSource,
        QwenCliSource,
    )
    from openprogram.providers.env_api_keys import PROVIDER_ENV_VARS

    pm = get_profile_manager()
    default = pm.get_profile(DEFAULT_PROFILE_NAME)
    sources: list[Any] = [
        CodexCliSource(),
        ClaudeCodeSource(),
        QwenCliSource(),
        GhCliSource(),
    ]
    for provider, env_var in PROVIDER_ENV_VARS.items():
        sources.append(EnvApiKeySource(provider_id=provider, env_var=env_var))

    found: list[dict[str, Any]] = []
    for src in sources:
        try:
            creds = src.try_import(default.root)
        except Exception as e:
            found.append({"source_id": src.source_id, "error": str(e)})
            continue
        for cred in creds:
            found.append({
                "source_id": src.source_id,
                "provider": cred.provider_id,
                "profile": cred.profile_id,
                "kind": cred.kind,
                "preview": _payload_summary(cred),
                "read_only": cred.read_only,
            })

    if as_json:
        print(json.dumps(found, indent=2))
        return 0

    if not found:
        print("Nothing found. No existing CLIs or env-var keys detected on this machine.")
        return 0

    print(f"Found {len(found)} adoptable credential(s):\n")
    print(f"{'source':28s}  {'provider':24s}  preview")
    for f in found:
        if "error" in f:
            print(f"{f['source_id']:28s}  (error)                   {f['error']}")
            continue
        print(f"{f['source_id']:28s}  {f['provider']:24s}  {f['preview']}")
    print("\nAdopt one with:  openprogram providers adopt <source_id>")
    return 0


# ---------------------------------------------------------------------------
# adopt
# ---------------------------------------------------------------------------

def _cmd_adopt(source_id: str, profile: str) -> int:
    store = get_store()
    pm = get_profile_manager()
    profile_obj = pm.get_profile(profile)

    src = _source_by_id(source_id, profile)
    if src is None:
        print(f"Unknown source: {source_id!r}. "
              f"Run `openprogram providers discover` to see available ids.",
              file=sys.stderr)
        return 1

    try:
        creds = src.try_import(profile_obj.root)
    except Exception as e:
        print(f"Source failed: {e}", file=sys.stderr)
        return 1
    if not creds:
        print(f"Source {source_id!r} produced no credentials — nothing to adopt.",
              file=sys.stderr)
        return 1

    for cred in creds:
        # Force the caller's requested profile — some sources default
        # to "default" but the user may be scoping to "work".
        cred.profile_id = profile
        store.add_credential(cred)
        print(f"✓ Adopted {cred.provider_id}/{cred.profile_id}: {_payload_summary(cred)}")
    return 0


def _cmd_adopt_all(profile: str) -> int:
    """Batch-adopt every credential the discovery layer finds (CLI wrapper).

    Delegates to :func:`run_adopt_all` for the actual work; this function
    only formats output. Exit 0 on success (even zero adopted), 1 if
    any source errored.
    """
    result = run_adopt_all(profile)
    for item in result["events"]:
        level = item["level"]
        if level == "adopted":
            print(f"  + {item['provider_id']}/{profile}: {item['preview']}")
        elif level == "error":
            print(f"  ! {item['source_id']}: {item['error']}", file=sys.stderr)
    print(f"\nAdopted {result['adopted']} · "
          f"skipped {result['skipped']} · errored {result['errored']}")
    return 1 if result["errored"] else 0


def run_adopt_all(profile: str) -> dict[str, Any]:
    """Pure batch-adopt — no stdout, returns a structured report.

    Used by the CLI ``adopt --all`` verb and the
    ``POST /api/providers/adopt_all`` REST route.

    Idempotent: a second run skips credentials whose source label
    already appears in the destination pool, so the event-var path
    doesn't accumulate duplicates.
    """
    from openprogram.auth.sources import (
        ClaudeCodeSource,
        CodexCliSource,
        EnvApiKeySource,
        GhCliSource,
        QwenCliSource,
    )
    from openprogram.providers.env_api_keys import PROVIDER_ENV_VARS

    store = get_store()
    pm = get_profile_manager()
    profile_obj = pm.get_profile(profile)

    sources: list[Any] = [
        CodexCliSource(profile_id=profile),
        ClaudeCodeSource(profile_id=profile),
        QwenCliSource(profile_id=profile),
        GhCliSource(),
    ]
    for provider, env_var in PROVIDER_ENV_VARS.items():
        sources.append(
            EnvApiKeySource(provider_id=provider, env_var=env_var, profile_id=profile),
        )

    events: list[dict[str, Any]] = []
    adopted = 0
    skipped = 0
    errored = 0
    for src in sources:
        try:
            creds = src.try_import(profile_obj.root)
        except Exception as e:
            events.append({
                "level": "error",
                "source_id": src.source_id,
                "error": str(e),
            })
            errored += 1
            continue
        for cred in creds:
            cred.profile_id = profile
            existing = store.find_pool(cred.provider_id, cred.profile_id)
            if existing is not None and any(
                c.source == cred.source for c in existing.credentials
            ):
                skipped += 1
                continue
            try:
                store.add_credential(cred)
                adopted += 1
                events.append({
                    "level": "adopted",
                    "provider_id": cred.provider_id,
                    "preview": _payload_summary(cred),
                    "source_id": src.source_id,
                })
            except Exception as e:
                errored += 1
                events.append({
                    "level": "error",
                    "source_id": src.source_id,
                    "error": str(e),
                    "provider_id": cred.provider_id,
                })

    return {
        "adopted": adopted,
        "skipped": skipped,
        "errored": errored,
        "events": events,
        "profile": profile,
    }


def _source_by_id(source_id: str, profile: str):
    from openprogram.auth.sources import (
        ClaudeCodeSource,
        CodexCliSource,
        EnvApiKeySource,
        GhCliSource,
        QwenCliSource,
    )
    from openprogram.providers.env_api_keys import PROVIDER_ENV_VARS

    if source_id == "codex_cli":
        return CodexCliSource(profile_id=profile)
    if source_id == "claude_code":
        return ClaudeCodeSource(profile_id=profile)
    if source_id == "qwen_cli":
        return QwenCliSource(profile_id=profile)
    if source_id == "gh_cli":
        return GhCliSource()
    if source_id.startswith("env:"):
        env_var = source_id[4:]
        provider = next(
            (p for p, v in PROVIDER_ENV_VARS.items() if v == env_var), None,
        )
        if provider is None:
            return None
        return EnvApiKeySource(provider_id=provider, env_var=env_var, profile_id=profile)
    return None


# ---------------------------------------------------------------------------
# logout
# ---------------------------------------------------------------------------

def _cmd_logout(provider: str, profile: str, *, skip_confirm: bool) -> int:
    store = get_store()
    pool = store.find_pool(provider, profile)
    if pool is None or not pool.credentials:
        print(f"No credentials for {provider}/{profile} — nothing to remove.")
        return 0

    if not skip_confirm:
        print(f"About to remove {len(pool.credentials)} credential(s) for "
              f"{provider}/{profile}:")
        for c in pool.credentials:
            print(f"  - {c.credential_id} ({_payload_summary(c)})")
        confirm = input("Proceed? [y/N]: ").strip().lower()
        if confirm not in ("y", "yes"):
            print("Aborted.")
            return 0

    removed_steps = _collect_removal_steps(pool)
    store.delete_pool(provider, profile)
    print(f"✓ Removed {len(pool.credentials)} credential(s) from "
          f"{store.root}/{provider}/{profile}.json")

    non_exec = [s for s in removed_steps if not s.executable]
    if non_exec:
        print("\nManual cleanup still required:")
        for step in non_exec:
            print(f"  - {step.description}")
    return 0


def _collect_removal_steps(pool: CredentialPool) -> list[RemovalStep]:
    """Gather :class:`RemovalStep` entries from whichever source produced
    each credential. Returns a flat list; duplicates are OK because
    the user reads through them regardless."""
    steps: list[RemovalStep] = []
    for cred in pool.credentials:
        src = _source_by_id(cred.source, cred.profile_id) if cred.source else None
        if src is not None and hasattr(src, "removal_steps"):
            try:
                steps.extend(src.removal_steps(cred))
            except Exception:
                continue
    return steps


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def _cmd_status(provider: str, profile: str) -> int:
    manager = get_manager()
    with auth_scope(profile_id=profile):
        try:
            cred = manager.acquire_sync(provider, profile)
        except AuthConfigError as e:
            print(f"No credential configured for {provider}/{profile}.")
            print(f"  → {e}")
            print(f"Try: openprogram providers login {provider} --profile {profile}")
            return 1
        except AuthError as e:
            print(f"Credential exists but is not usable: {e}")
            return 1

    print(f"Provider: {provider}")
    print(f"Profile:  {profile}")
    print(f"Kind:     {cred.kind}")
    print(f"Status:   {cred.status}")
    print(f"Preview:  {_payload_summary(cred)}")
    if cred.metadata:
        print("Metadata:")
        for k, v in cred.metadata.items():
            print(f"  {k}: {v}")
    return 0


# ---------------------------------------------------------------------------
# profile CRUD
# ---------------------------------------------------------------------------

def _cmd_profile_list() -> int:
    pm = get_profile_manager()
    profiles = pm.list_profiles()
    print(f"{'name':16s}  {'display name':24s}  root")
    for p in profiles:
        print(f"{p.name:16s}  {p.display_name or '-':24s}  {p.root}")
    return 0


def _cmd_profile_create(name: str, display_name: str, description: str) -> int:
    pm = get_profile_manager()
    try:
        profile = pm.create_profile(
            name, display_name=display_name, description=description,
        )
    except AuthConfigError as e:
        print(f"Failed to create profile: {e}", file=sys.stderr)
        return 1
    print(f"✓ Created profile {profile.name} at {profile.root}")
    return 0


def _cmd_profile_delete(name: str, skip_confirm: bool) -> int:
    pm = get_profile_manager()
    if not skip_confirm:
        confirm = input(f"Delete profile {name!r} and all its credentials? [y/N]: ").strip().lower()
        if confirm not in ("y", "yes"):
            print("Aborted.")
            return 0
    try:
        pm.delete_profile(name)
    except AuthConfigError as e:
        print(f"Failed to delete profile: {e}", file=sys.stderr)
        return 1
    print(f"✓ Deleted profile {name}")
    return 0


# ---------------------------------------------------------------------------
# aliases
# ---------------------------------------------------------------------------

def _cmd_aliases(as_json: bool) -> int:
    table = known_aliases()
    if as_json:
        print(json.dumps(table, indent=2))
        return 0
    print(f"{'alias':24s}  canonical")
    for alias in sorted(table):
        print(f"{alias:24s}  {table[alias]}")
    print("\nUse either form: `openprogram providers login codex` and "
          "`openprogram providers login openai-codex` do the same thing.")
    return 0


# ---------------------------------------------------------------------------
# doctor — diagnostic report
# ---------------------------------------------------------------------------

def _cmd_doctor(as_json: bool) -> int:
    """Run the full credential health report.

    Thin wrapper over :func:`run_doctor` — the shared analysis is in that
    function so the REST route can reuse it without re-implementing the
    check matrix.
    """
    report = run_doctor()
    findings = report["findings"]

    if as_json:
        print(json.dumps(report, indent=2))
    else:
        _print_doctor_report(
            report["pools_checked"],
            report["profiles_checked"],
            findings,
        )

    has_error = any(f["level"] == "ERROR" for f in findings)
    return 1 if has_error else 0


def run_doctor() -> dict[str, Any]:
    """Pure analysis — no printing, no process exit. Returns a dict with
    ``pools_checked``, ``profiles_checked``, ``findings``.

    Checks (a superset of OpenClaw's ``doctor-auth`` list adapted for
    our store layout): expired OAuth, refresh availability, cooldown
    state, pool exhaustion, orphaned profile references, empty profiles,
    duplicate credential ids, imported creds whose source file is gone.

    Used by the CLI ``doctor`` verb and the ``POST /api/providers/doctor``
    REST route. Extracting this makes both share one source of truth.
    """
    from .manager import get_provider_config

    store = get_store()
    pm = get_profile_manager()
    profiles = {p.name: p for p in pm.list_profiles()}
    pools = store.list_pools()

    findings: list[dict[str, Any]] = []

    def add(level: str, code: str, message: str, **extra: Any) -> None:
        findings.append({"level": level, "code": code, "message": message, **extra})

    if not pools:
        add("WARN", "no_pools",
            "No credential pools configured. Run `openprogram providers setup`.")

    # Profiles referenced by pools but not registered with ProfileManager.
    pool_profile_ids = {p.profile_id for p in pools}
    orphaned_profile_refs = pool_profile_ids - set(profiles.keys())
    for pid in sorted(orphaned_profile_refs):
        add("ERROR", "orphan_profile_ref",
            f"Pool references profile {pid!r} which no longer exists.",
            profile=pid)

    # Profiles that exist but nobody's logged into.
    empty_profiles = sorted(set(profiles.keys()) - pool_profile_ids)
    for pid in empty_profiles:
        if pid == DEFAULT_PROFILE_NAME:
            continue  # default is expected to start empty
        add("INFO", "empty_profile",
            f"Profile {pid!r} has no credentials yet.",
            profile=pid)

    now_ms = int(time.time() * 1000)

    for pool in pools:
        cfg = get_provider_config(pool.provider_id)
        refresh_available = cfg.refresh is not None or cfg.async_refresh is not None

        # Dup credential_id detection (flags storage corruption).
        seen: dict[str, int] = {}
        for c in pool.credentials:
            seen[c.credential_id] = seen.get(c.credential_id, 0) + 1
        for cid, n in seen.items():
            if n > 1:
                add("ERROR", "duplicate_credential_id",
                    f"Pool {pool.provider_id}/{pool.profile_id} has "
                    f"{n} credentials with id {cid!r}.",
                    provider=pool.provider_id, profile=pool.profile_id)

        usable = 0
        for c in pool.credentials:
            # Source-file check — if we imported this from an external
            # path and that path is gone, the user likely `codex logout`-ed
            # and our pool is a stale mirror. Flag so `providers doctor`
            # catches it before the next API call raises a mysterious 401.
            src_path = (c.metadata or {}).get("source_path")
            if src_path:
                from pathlib import Path as _P
                if not _P(src_path).exists():
                    add("WARN", "missing_source_file",
                        f"{pool.provider_id}/{pool.profile_id} was imported "
                        f"from {src_path} which no longer exists. "
                        "Consider re-running login if the external CLI logged out.",
                        provider=pool.provider_id, profile=pool.profile_id,
                        credential_id=c.credential_id,
                        source_path=src_path)

            # Cooldown check.
            cooldown_until = getattr(c, "cooldown_until_ms", 0) or 0
            if cooldown_until and cooldown_until > now_ms:
                add("WARN", "cooling_down",
                    f"{pool.provider_id}/{pool.profile_id} credential "
                    f"{c.credential_id} cooling down for "
                    f"{_fmt_duration(cooldown_until - now_ms)}.",
                    provider=pool.provider_id, profile=pool.profile_id,
                    credential_id=c.credential_id)
                continue

            # Expiry on oauth/device_code.
            if c.kind in ("oauth", "device_code"):
                exp = getattr(c.payload, "expires_at_ms", 0) or 0
                if exp and exp <= now_ms:
                    if refresh_available or c.read_only:
                        # Read-only: external CLI owns refresh; surface
                        # as WARN because next call will either refresh
                        # or raise AuthReadOnlyError clearly.
                        add("WARN", "expired_token",
                            f"{pool.provider_id}/{pool.profile_id} access "
                            "token expired; will refresh on next use."
                            + (" (read-only — external CLI)" if c.read_only else ""),
                            provider=pool.provider_id,
                            profile=pool.profile_id,
                            credential_id=c.credential_id)
                        usable += 1  # refresh path makes it usable
                    else:
                        add("ERROR", "expired_no_refresh",
                            f"{pool.provider_id}/{pool.profile_id} access "
                            "token expired and no refresh configured. "
                            f"Run `openprogram providers login {pool.provider_id}`.",
                            provider=pool.provider_id,
                            profile=pool.profile_id,
                            credential_id=c.credential_id)
                        continue
                else:
                    usable += 1
            else:
                usable += 1

            # Refresh wiring sanity — if we have an oauth cred but no
            # refresh registered AND it's not read-only, the user can
            # still use it until expiry but should know.
            if (
                c.kind == "oauth"
                and not refresh_available
                and not c.read_only
            ):
                add("WARN", "no_refresh_registered",
                    f"{pool.provider_id} has an OAuth credential but no "
                    "refresh callback registered — will need manual re-login "
                    "after expiry.",
                    provider=pool.provider_id,
                    profile=pool.profile_id)

        if usable == 0 and pool.credentials:
            add("ERROR", "pool_exhausted",
                f"{pool.provider_id}/{pool.profile_id} has "
                f"{len(pool.credentials)} credential(s) but none are usable.",
                provider=pool.provider_id, profile=pool.profile_id)

    return {
        "pools_checked": len(pools),
        "profiles_checked": len(profiles),
        "findings": findings,
    }


def _print_doctor_report(pools_count: int, profiles_count: int, findings) -> None:
    print(f"Checked {pools_count} pool(s) across {profiles_count} profile(s).\n")
    if not findings:
        print("✓ All checks passed.")
        return
    order = {"ERROR": 0, "WARN": 1, "INFO": 2}
    findings_sorted = sorted(findings, key=lambda f: (order.get(f["level"], 3), f["code"]))
    tag = {"ERROR": "✗ ERROR", "WARN": "⚠ WARN ", "INFO": "· INFO "}
    counts = {"ERROR": 0, "WARN": 0, "INFO": 0}
    for f in findings_sorted:
        counts[f["level"]] = counts.get(f["level"], 0) + 1
        print(f"{tag.get(f['level'], f['level']):8s}  [{f['code']}] {f['message']}")
    print()
    print(f"Summary: {counts.get('ERROR', 0)} error(s), "
          f"{counts.get('WARN', 0)} warning(s), "
          f"{counts.get('INFO', 0)} info.")


# ---------------------------------------------------------------------------
# setup — first-time wizard
# ---------------------------------------------------------------------------

def _cmd_setup() -> int:
    """Interactive wizard — arrow-key menus when questionary is
    installed, plain input() fallback otherwise.

    The questionary path is the primary UX (modelled after OpenClaw's
    clack-based setup). The fallback exists so environments without the
    dep — minimal installs, some CI sandboxes — still get a usable
    wizard, just without the navigation ergonomics.
    """
    try:
        from .interactive import run_interactive_setup
        return run_interactive_setup()
    except (KeyboardInterrupt, EOFError):
        print("\n\nSetup interrupted. Run `openprogram providers setup` again "
              "when you're ready. Progress so far is already saved.",
              file=sys.stderr)
        return 130


def _run_setup() -> int:
    from openprogram.auth.sources import (
        ClaudeCodeSource,
        CodexCliSource,
        EnvApiKeySource,
        GhCliSource,
        QwenCliSource,
    )

    store = get_store()
    pm = get_profile_manager()
    default = pm.get_profile(DEFAULT_PROFILE_NAME)

    print("═" * 60)
    print("  OpenProgram — provider setup")
    print("═" * 60)
    print()
    print("This wizard walks you through connecting AI providers.")
    print("We'll scan for existing logins (Codex CLI, Claude Code, env")
    print("variables, etc.) and offer to import them. You can skip any")
    print("step. Nothing is sent anywhere; everything stays on this box.")
    print()
    if _confirm("Start?", default=True) is False:
        print("Cancelled.")
        return 0

    # --- step 1: scan -----------------------------------------------------
    print("\n[1/3] Scanning for existing credentials...\n")
    sources: list[Any] = [
        CodexCliSource(),
        ClaudeCodeSource(),
        QwenCliSource(),
        GhCliSource(),
    ]
    from openprogram.providers.env_api_keys import PROVIDER_ENV_VARS
    for provider, env_var in PROVIDER_ENV_VARS.items():
        sources.append(EnvApiKeySource(provider_id=provider, env_var=env_var))

    findings: list[tuple[Any, list[Credential]]] = []
    for src in sources:
        try:
            creds = src.try_import(default.root)
        except Exception:
            continue
        if creds:
            findings.append((src, creds))

    if not findings:
        print("  (nothing detected)")
    else:
        print(f"  Found {sum(len(c) for _, c in findings)} adoptable credential(s):")
        for src, creds in findings:
            for c in creds:
                print(f"    · {src.source_id:24s} → {c.provider_id} "
                      f"({_payload_summary(c)})")

    # --- step 2: adopt ----------------------------------------------------
    adopted = 0
    if findings:
        print()
        adopt_all = _confirm("Import all of them?", default=True)
        for src, creds in findings:
            for cred in creds:
                if adopt_all is False:
                    pick = _confirm(
                        f"  Import {src.source_id} → {cred.provider_id}?",
                        default=True,
                    )
                    if not pick:
                        continue
                # Dedup by source label — credential_id is freshly
                # minted every try_import, so id-equality never matches
                # and we used to create duplicates on every wizard run.
                existing = store.find_pool(cred.provider_id, cred.profile_id)
                if existing and any(
                    c.source == cred.source for c in existing.credentials
                ):
                    continue
                try:
                    store.add_credential(cred)
                    adopted += 1
                except Exception as e:
                    print(f"    (failed to add: {e})")
        print(f"\n  Imported {adopted} credential(s).")

    # --- step 3: manual login for missing popular providers ---------------
    print("\n[2/3] Manual login for providers not yet covered...\n")
    popular = [
        ("openai-codex", "OpenAI via Codex CLI (ChatGPT account)"),
        ("anthropic",    "Anthropic (Claude)"),
        ("gemini-subscription", "Google Gemini via CLI"),
        ("github-copilot", "GitHub Copilot"),
        ("openai",       "OpenAI (raw API key)"),
    ]
    for prov_id, label in popular:
        if store.find_pool(prov_id, DEFAULT_PROFILE_NAME) is not None:
            continue
        pick = _confirm(f"  Log into {label} now?", default=False)
        if not pick:
            continue
        try:
            rc = _cmd_login(prov_id, DEFAULT_PROFILE_NAME, method=None)
            if rc != 0:
                print(f"    (skipped — {prov_id} login exited {rc})")
        except (KeyboardInterrupt, EOFError):
            print("\n    (aborted)")
            continue

    # --- outro ------------------------------------------------------------
    print("\n[3/3] Done.\n")
    print("  Verify anytime:")
    print("    openprogram providers list")
    print("    openprogram providers doctor")
    print("    openprogram providers status <provider>")
    print()
    return 0


def _confirm(question: str, *, default: bool) -> bool:
    """Prompt for y/n; Enter accepts ``default``. Returns the bool.

    Accepts y/yes/n/no (case-insensitive). Any other input repeats the
    prompt. EOF/^C propagate — the wizard handles them at the top
    level so each sub-step doesn't have to."""
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        raw = input(f"{question} {hint} ").strip().lower()
        if raw == "":
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  (answer y or n)")


__all__ = ["build_parser", "dispatch"]
