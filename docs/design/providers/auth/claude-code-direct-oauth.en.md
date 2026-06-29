# claude-code direct subscription connection (drop Meridian)

## Goal

Change the `claude-code` provider from "a local Meridian proxy daemon" to "the anthropic SDK
connecting directly to `api.anthropic.com` using a subscription OAuth token" — fully isomorphic to
how `openai-codex` reads `~/.codex/auth.json` directly and connects directly to
`chatgpt.com/backend-api`.

Constraints (decided by the user):
- **Keep the `claude-code` provider name** (WebUI/CLI unchanged); only swap the underlying Runtime.
- **Don't touch the macOS Keychain**; use OpenProgram's own credential system (AuthStore +
  the `~/.claude/.credentials.json` file).

## Background: why Meridian isn't a technical necessity

`anthropic.py:245-261` has long supported direct subscription OAuth connections: when the token is
`sk-ant-oat…`, it sends to the Messages API directly with `auth_token=<token>` +
`anthropic-beta: claude-code-20250219,oauth-2025-04-20,…` +
`user-agent: claude-cli/<ver>`. This is the same approach codex uses to connect directly to chatgpt.com.
The two reasons Meridian was originally chosen no longer hold:

- "A Max account doesn't expose an api.anthropic.com key" — true, but the subscription uses an
  **OAuth token**, not an api-key; the direct connection uses a Bearer token + beta header and needs
  no api-key.
- "A third-party proxy mangled the image block into `[object Object]`" — that was a bug in some
  proxy; sending to the Messages API directly through the official `anthropic` SDK natively supports
  image blocks, so multimodal content isn't lost.

## The real obstacle: how the token gets in + how it refreshes

| Credential form | Source | kind | refresh |
| --- | --- | --- | --- |
| Observe the Claude CLI | `~/.claude/.credentials.json` | `cli_delegated` | The Claude CLI refreshes itself; OpenProgram observes and re-reads |
| Self-held api-key | `openprogram auth login anthropic --api-key` | `api_key` | Doesn't expire |

Two gaps in the current state:
1. The anthropic provider parses the token with `resolve_provider_key()`, which **explicitly excludes
   OAuth** (`env_api_keys.py:52` comment: OAuth goes through the claude-code daemon).
2. The resolver's `_extract_token()` returns None for `cli_delegated`
   (`resolver.py:132-137`), so even switching to `resolve_api_key_sync` can't pull out the
   subscription token.

## Plan (lightweight, freeloading off the Claude CLI's refresh — identical to codex)

codex's cli_delegated mode: the codex CLI maintains `~/.codex/auth.json`, and OpenProgram re-reads
the latest access_token each time. We copy this verbatim for Claude: the Claude CLI maintains
`~/.claude/.credentials.json` (on Linux/Win it's a file, read directly; we don't touch the mac
Keychain), and OpenProgram re-reads `claudeAiOauth.accessToken` each time.

### Change points

1. **Have the resolver pull the token for cli_delegated** — `auth/resolver.py:_extract_token`
   re-reads `store_path` for a `CliDelegatedPayload` and pulls out the access_token at
   `access_key_path`. This is a general fix (codex's cli_delegated benefits too).

2. **Switch the anthropic provider to unified resolution** — in
   `providers/anthropic/anthropic.py` `stream_simple`, change token resolution from
   `resolve_provider_key(provider)` to `resolve_api_key_sync(provider)`
   (which includes OAuth/cli_delegated/manager-refresh). Change `AnthropicRuntime.__init__` the same way.

3. **Switch the registry** — in `providers/registry.py`, change
   `"claude-code"` from `_max_proxy_runtime.ClaudeCodeRuntime` to the direct-connection Runtime.
   Keep the `claude-code` name: add a lightweight Runtime whose models go through the `anthropic:<id>`
   namespace (reusing the anthropic provider's wire), with the token resolved from the `anthropic` pool.
   The model alias normalization (opus/sonnet/haiku) carries over.

4. **Expiry handling** — when cli_delegated expires, AuthManager throws `AuthReadOnlyError`
   (read-only, can't self-refresh), and the error message guides the user to `claude login`. The
   direct-connection path reuses this; nothing extra is built.

### Verification

- Unit: `_extract_token` re-reads the file and pulls the token for cli_delegated; the anthropic
  provider can resolve an `sk-ant-oat` token when only a cli_delegated credential is present; the
  registry resolves `claude-code` to the direct-connection Runtime rather than the Meridian Runtime.
- End-to-end: after `claude login` on the local machine, run claude-code once in OpenProgram and
  confirm it connects directly to api.anthropic.com (no Meridian process) and that multimodal image
  blocks work.

## Implementation status (landed)

1. ✅ `auth/resolver.py:_extract_token` + new `_read_delegated_token`: cli_delegated
   re-reads `store_path` to get the access_token (codex's cli_delegated benefits too).
2. ✅ `providers/anthropic/anthropic.py:stream_simple` + `runtime.py:AnthropicRuntime`
   switched to `resolve_api_key_sync` (includes OAuth/cli_delegated).
3. ✅ New `providers/anthropic/_claude_code_direct_runtime.py`: ClaudeCodeRuntime
   connects directly, maps models to `anthropic:<id>`, resolves the token from the anthropic pool.
4. ✅ `providers/registry.py`: `claude-code` → direct-connection Runtime (the old Meridian Runtime
   only lingers as importable).
5. ✅ Tests: `tests/unit/test_claude_code_direct_oauth.py` (10) added;
   `test_runtime_key_ladder.py` mock points updated (AnthropicRuntime uses unified resolution).
   Full unit suite: 810 passed / 4 skipped.

Note: the `api="claude-code-cli"` wire label is only declared in `_claude_code_registry.py` and has
no consumer anywhere in the repo (a dangling label); the actual request always goes through
Runtime → the `anthropic:<id>` Messages wire, so the switch doesn't touch any wire implementation.
Meridian's `x-meridian-profile` header injection hangs off the openai_completions chokepoint, and the
direct-connection model api is the anthropic Messages api, so it naturally never passes through there.

## Subscription login (browser OAuth + setup-token) — landed

The direct connection only solves "how to use a token once you have one"; login solves "how the token
gets in." Copy codex's PKCE framework to wire subscription login into claude-code:

- **OAuth parameters (verified working)**: `auth_adapter.py` adds `OAUTH_CLIENT_ID`
  =`9d1c250a-e61b-44d9-88ed-5944d1962f5e`, authorize=`claude.ai/oauth/authorize`,
  token=`console.anthropic.com/v1/oauth/token`, redirect=`console.anthropic.com/oauth/code/callback`.
  `build_pkce_config()` uses manual-paste mode (Anthropic is a hosted-redirect that displays
  `code#state`, not a loopback callback) + token JSON.
- **General PKCE framework extension**: `pkce_oauth.py` adds the three switches
  `manual_paste_only`/`redirect_uri_override`/`token_use_json` + the `_credential_from_tokens`
  extraction + exchange with state.
- **refresh**: `_anthropic_refresh` (refresh_token swapped for a new one, JSON), registered to
  ProviderAuthConfig; cases without a refresh_token (setup-token) automatically no-op.
- **setup-token**: `import_setup_token` stores oauth kind, an empty refresh_token, ~1y expiry.
- **Login methods**: in `login_methods`, anthropic + claude-code keep only
  `pkce_oauth` (default) + `setup_token` — **no import_from_cli, no api_key**
  (the user explicitly deprecated importing from ~/.claude).
- **driver**: `login_driver` adds an anthropic pkce branch + setup_token dispatch;
  `_credential_provider_id` (claude-code→anthropic) ensures the credential lands in the anthropic pool.
- **Multi-account**: one profile per account, reusing unified account management + 429 rotation.

## WebUI: claude-code breaks free of the Meridian account system — landed

claude-code's account UI was originally hardcoded into Meridian-specific routes on both frontend and
backend, bypassing the general login. The switch:

- `webui/routes/providers.py`: delete the whole block of `/api/providers/claude-code/accounts/*`
  literal routes (which called `_meridian_cli`; literal routes have higher priority and would
  intercept the request).
- `webui/routes/accounts.py`: add `_pool_id` (claude-code→anthropic), delete 5
  `if provider=="claude-code"` short-circuits so all general routes store/fetch by pool; `_api_key_env`
  returns "" for claude-code (forcing add_mode=login, hiding key-paste).
- `setup_hints.py`: change the claude-code copy to "direct connection to Anthropic with a subscription
  OAuth," delete the backend/Meridian description, and explain the two login methods.
- The frontend `account-manager.tsx`/`provider-login.tsx` are data-driven, zero changes: backend
  add_mode=login → automatically renders the two login buttons. Already build + worker restart + browser
  self-check confirmed the UI is correct (two buttons, no Import, copy correct).

`_meridian_cli.py`/`_max_proxy_runtime.py` and the like remain on disk but are no longer called by any route.

## Disposition of Meridian remnants

`_max_proxy_runtime.py` / `_claude_max_proxy_registry.py` / `_meridian_cli.py` are kept for now
(the WebUI's "Add Claude account" P1/P2 still reference them); only the registry no longer points to
them by default. Delete later once no references are confirmed.
