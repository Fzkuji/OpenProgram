"""Credential CLI login responsibilities."""
from __future__ import annotations
import asyncio
import getpass
import sys
from typing import Optional
from ..account.accounts import get_account_manager
from ..store import AuthStore, get_store
from ..types import AuthConfigError, AuthError, Credential, CredentialData


def _cmd_login(provider: str, account: str, method: Optional[str], *,
               api_key: Optional[str] = None,
               api_key_stdin: bool = False) -> int:
    from .formatting import _payload_summary
    store = get_store()
    choices = _available_login_methods(provider)
    if not choices:
        print(f"No login method implemented for provider {provider!r}.", file=sys.stderr)
        return 1
    method_ids = {m for m, _ in choices}

    # --- resolve the login method, preferring a fully non-interactive path
    # so agents / scripts never block on getpass or have a piped key eaten
    # by the method picker. Precedence:
    #   1. an explicitly supplied key (--api-key / --api-key-stdin)
    #   2. an explicit --method
    #   3. no tty: auto-read stdin as the key if api_key login is possible,
    #      else fail clearly (don't silently block on a terminal read)
    #   4. interactive picker (the original UX)
    supplied_key: Optional[str] = None
    if api_key_stdin:
        supplied_key = sys.stdin.read().strip()
        if not supplied_key:
            print("--api-key-stdin given but stdin was empty.", file=sys.stderr)
            return 1
    elif api_key is not None:
        supplied_key = api_key.strip()
        if not supplied_key:
            print("--api-key was empty — nothing to save.", file=sys.stderr)
            return 1

    if supplied_key is not None:
        if "api_key" not in method_ids:
            print(f"Provider {provider!r} doesn't support api_key login "
                  f"(methods: {', '.join(sorted(method_ids))}).", file=sys.stderr)
            return 1
        chosen = "api_key"
    elif method is not None:
        chosen = method
        if chosen not in method_ids:
            print(f"Method {chosen!r} not available for {provider}. "
                  f"Try: {', '.join(sorted(method_ids))}", file=sys.stderr)
            return 1
    elif not sys.stdin.isatty():
        # Non-interactive context, no explicit key/method given. If the
        # provider takes an API key, read it from stdin so a bare
        # `printf %s "$KEY" | openprogram providers login <prov>` works.
        # Otherwise (OAuth-only, e.g. openai-codex) there's nothing we can
        # do headlessly — say so instead of hanging on /dev/tty.
        if "api_key" in method_ids:
            supplied_key = sys.stdin.read().strip()
            if not supplied_key:
                print(f"{provider}: no terminal and no key supplied. Pass the "
                      f"key with `--api-key <key>`, `--api-key-stdin`, or pipe "
                      f"it on stdin.", file=sys.stderr)
                return 1
            chosen = "api_key"
        else:
            print(f"{provider}: login needs an interactive terminal "
                  f"(methods: {', '.join(sorted(method_ids))}).", file=sys.stderr)
            return 1
    else:
        # Prefer the arrow-key picker; if questionary isn't installed
        # it returns None and we fall through to the numeric prompt.
        from ..login.interactive import pick_login_method_interactive
        chosen_or_none = pick_login_method_interactive(provider, choices)
        if chosen_or_none is not None:
            chosen = chosen_or_none
        else:
            print(f"Login to {provider} (account: {account})")
            print("Available methods:")
            for i, (mid, label) in enumerate(choices, 1):
                print(f"  {i}. {label}")
            try:
                pick = input(f"Pick a method [1-{len(choices)}] (default 1): ").strip() or "1"
                chosen = choices[int(pick) - 1][0]
            except (EOFError, ValueError, IndexError):
                print("Aborted.", file=sys.stderr)
                return 1

    try:
        cred = _run_login_method(provider, account, chosen, api_key=supplied_key)
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
    saved_account = cred.account_id
    print(f"\n✓ Saved credential {cred.credential_id} for "
          f"{saved_provider}/{saved_account}")
    print(f"  kind: {cred.kind}")
    print(f"  preview: {_payload_summary(cred)}")
    print(f"  store: {store.root}/{saved_provider}/{saved_account}.json")
    # OAuth pool hygiene: a fresh PKCE / device-code login supersedes
    # any older OAuth rows in the same pool (refresh-token rotation
    # makes them dead bytes). Drop them so ``providers status`` doesn't
    # latch onto the stale row and report "expired" right after a
    # successful relogin.
    pruned = _prune_superseded_oauth(store, cred)
    if pruned:
        print(f"  retired {len(pruned)} superseded OAuth credential(s): "
              f"{', '.join(pruned)}")
    # Subscription providers (claude-code, openai-codex) have no list-models
    # API; enable their default model set into config on first login. Keyed on
    # the USER-facing provider (claude-code credentials route to the anthropic
    # pool, but the models belong under claude-code). No-op when the provider
    # already has spec rows — a disabled default never resurrects.
    try:
        from openprogram.auth.login.login_seed_models import enable_default_models_on_login
        written = enable_default_models_on_login(provider)
        if written:
            print(f"  enabled default models: {', '.join(written)}")
    except Exception:
        pass
    if saved_provider != provider:
        print(f"\n  Note: routed to {saved_provider!r} (not {provider!r}) "
              f"because that's where this credential shape belongs.")
    # Auto-fetch the provider's live model list right after login, so its
    # catalog (incl. real image/video modalities that the static registry
    # underreports — e.g. MiniMax) is correct without a separate "Fetch
    # models" step. Mirrors the web UI's save-key → fetch. Best-effort and
    # only meaningful for providers that expose a /v1/models listing;
    # OAuth / static-only providers just no-op silently.
    try:
        from openprogram.webui._model_listing import fetch_models_remote
        res = fetch_models_remote(saved_provider)
        if isinstance(res, dict) and res.get("fetched"):
            print(f"  ↻ fetched {res['fetched']} models for {saved_provider}")
    except Exception:
        pass
    return 0



def _available_login_methods(provider: str) -> list[tuple[str, str]]:
    """Login methods the CLI can drive for ``provider`` (first = default).

    Delegates to the shared registry in ``openprogram/auth/login/login_method_registry.py``
    so the CLI, web and TUI all offer the SAME set instead of each keeping its
    own map.
    """
    from openprogram.auth.login.login_method_registry import login_methods
    return login_methods(provider)



def _prune_superseded_oauth(store: AuthStore, new_cred: Credential) -> list[str]:
    """Retire sibling OAuth credentials after a fresh interactive login.

    OAuth refresh-token rotation means at most one OAuth credential in a
    given pool can be valid at a time — once the user runs ``providers
    login`` and lands a fresh access+refresh pair, every older OAuth row
    in the same pool is dead bytes. Without pruning them, ``providers
    status`` (which iterates pool members in insertion order) keeps
    reporting the stale row as "expired and no refresh is configured",
    making the user think the relogin didn't take. Observed in practice
    against ``openai-codex`` after a successful PKCE flow.

    Only ``kind == "oauth"`` siblings are touched. Non-OAuth members
    (``api_key``, ``cli_delegated`` pointing at a sibling tool's auth
    file) are left alone — they're independent artifacts the user
    might still want.

    Returns the list of removed credential ids so the caller can mention
    them in the success message.
    """
    if new_cred.kind != "oauth":
        return []
    pool = store.find_pool(new_cred.provider_id, new_cred.account_id)
    if pool is None:
        return []
    removed: list[str] = []
    for sibling in list(pool.credentials):
        if sibling.credential_id == new_cred.credential_id:
            continue
        if sibling.kind != "oauth":
            continue
        store.remove_credential(
            sibling.provider_id, sibling.account_id, sibling.credential_id,
        )
        removed.append(sibling.credential_id)
    return removed



def _run_login_method(provider: str, account: str, method: str, *,
                      api_key: Optional[str] = None) -> Credential:
    """Drive a login from the terminal via the shared surface-agnostic driver
    (``openprogram/auth/login/login_driver.py``) so the CLI, web and TUI all run the
    same flow. The only CLI-specific bit is the multi-line OAuth note below."""
    if method == "pkce_oauth":
        title, body = _LOGIN_NOTES.get(provider, ("", []))
        if body:
            from ..tui import print_note
            print_note(title, body)
    from openprogram.auth.login.login_driver import run_login
    ui = _TerminalLoginUi()
    return asyncio.run(run_login(provider, account, method, ui, api_key=api_key))



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



_CLAUDE_SUB_NOTE = (
    "Sign in with Claude subscription",
    [
        "Browser will open for Claude (claude.ai) authentication.",
        "After authorizing, the page shows a code like 'xxxxx#yyyyy'.",
        "Copy that whole string and paste it back here.",
        "Requires a Claude Pro / Max subscription.",
    ],
)


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
    "anthropic": _CLAUDE_SUB_NOTE,
    "claude-code": _CLAUDE_SUB_NOTE,
}


def _login_paste_api_key(provider: str, account: str, *,
                         api_key: Optional[str] = None) -> Credential:
    # ``api_key`` is the non-interactive path (--api-key / --api-key-stdin /
    # piped stdin); when it's None we fall back to the hidden terminal
    # prompt. The AuthStore credential below is the single storage — the
    # UI catalog, validation, and the runtime ladder all read it first.
    key = api_key if api_key is not None else getpass.getpass(
        f"Paste API key for {provider} (hidden): ").strip()
    if not key:
        raise AuthConfigError("empty API key — nothing to save")
    # claude-code is an alias for the anthropic credential pool; store the
    # key there so the direct runtime (resolving from `anthropic`) finds it.
    store_provider = "anthropic" if provider == "claude-code" else provider
    return Credential(
        provider_id=store_provider,
        account_id=account,
        kind="api_key",
        payload=CredentialData(kind="api_key", auth_value=key),
        source="cli_paste",
        metadata={},
    )



def _login_import_from_cli(provider: str, account: str) -> Credential:
    """Delegate to the per-provider adapter's ``import_from_*`` helper.

    Adapters produce either writable OAuth (Codex — we rotate) or
    delegated read-only (Anthropic/Gemini/Qwen — external CLI rotates).
    The distinction is invisible here; we just hand off."""
    if provider == "openai-codex":
        from openprogram.providers.openai_codex import auth_adapter
        cred = auth_adapter.import_from_codex_file(account_id=account)
        if cred is None:
            raise AuthConfigError(
                f"{auth_adapter.codex_auth_path()} not found. "
                f"Run `codex login --device-auth` first, then re-run this command."
            )
        return cred
    if provider == "gemini-subscription":
        from openprogram.providers.google_gemini_cli import auth_adapter
        cred = auth_adapter.import_from_gemini_cli(account_id=account)
        if cred is None:
            raise AuthConfigError(
                f"{auth_adapter.gemini_cli_credentials_path()} not found. "
                f"Run `gemini auth login` first."
            )
        return cred
    if provider == "xai-subscription":
        from openprogram.providers.xai_subscription import auth_adapter
        cred = auth_adapter.import_from_grok_cli(account_id=account)
        if cred is None:
            raise AuthConfigError(
                f"{auth_adapter.grok_cli_auth_path()} not found. "
                f"Run `grok login` first, or Sign in on the Grok Subscription card."
            )
        return cred
    if provider == "qwen":
        from openprogram.auth.sources.qwen_cli import QwenCliSource
        src = QwenCliSource(account_id=account)
        creds = src.try_import(get_account_manager().get_account(account).root)
        if not creds:
            raise AuthConfigError(
                "~/.qwen/oauth_creds.json not found. Run `qwen login` first."
            )
        return creds[0]
    raise AuthConfigError(f"no import-from-CLI adapter for {provider!r}")

