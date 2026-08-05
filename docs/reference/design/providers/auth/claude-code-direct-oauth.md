# claude-code direct subscription connection

The `claude-code` provider connects directly to `api.anthropic.com` with the
anthropic SDK, using a subscription OAuth token — the same shape as
`openai-codex`, which reads `~/.codex/auth.json` directly and connects directly
to `chatgpt.com/backend-api`. There is no local proxy daemon in the path.

Two constraints shape the design:

- The provider is named `claude-code` in the WebUI and CLI. Only the underlying
  Runtime is Anthropic-direct; the user-facing name does not change.
- Credentials live in OpenProgram's own system (AuthStore plus the
  `~/.claude/.credentials.json` file). The macOS Keychain is not touched.

## Why a proxy is not required

`anthropic.py:245-261` supports subscription OAuth directly: when the token is
`sk-ant-oat…`, the request goes to the Messages API with `auth_token=<token>`,
`anthropic-beta: claude-code-20250219,oauth-2025-04-20,…`, and
`user-agent: claude-cli/<ver>`. This is the same approach codex uses against
chatgpt.com.

The two arguments that once favoured a proxy do not hold. A Max account exposing
no `api.anthropic.com` key is true but irrelevant — the subscription uses an
**OAuth token**, not an api-key, and the direct connection needs only a Bearer
token plus the beta header. And image blocks arriving as `[object Object]` was a
bug in one particular proxy; the official `anthropic` SDK supports image blocks
natively against the Messages API, so multimodal content survives.

## Credential forms

| Credential form | Source | kind | refresh |
| --- | --- | --- | --- |
| Observe the Claude CLI | `~/.claude/.credentials.json` | `cli_delegated` | The Claude CLI refreshes itself; OpenProgram observes and re-reads |
| Self-held api-key | `openprogram auth login anthropic --api-key` | `api_key` | Doesn't expire |

The `cli_delegated` mode mirrors codex exactly. The codex CLI maintains
`~/.codex/auth.json` and OpenProgram re-reads the latest access_token on each
use; likewise the Claude CLI maintains `~/.claude/.credentials.json` (a plain
file on Linux and Windows, read directly) and OpenProgram re-reads
`claudeAiOauth.accessToken` on each use. Refresh is the external CLI's
responsibility, which is what makes the mode cheap.

## Mechanics

**Token extraction.** `auth/resolver.py:_extract_token` re-reads `store_path`
for a `CliDelegatedPayload` and pulls the access_token from `access_key_path`.
This is general to the credential kind, so codex's `cli_delegated` uses the same
path.

**Unified resolution in the anthropic provider.** `stream_simple` in
`providers/anthropic/anthropic.py` and `registry.py`'s `anthropic`
`create_runtime` path both resolve the token through
`resolve_api_key_sync(provider)`, which covers OAuth, `cli_delegated`, and
manager-driven refresh.

**Registry.** `providers/registry.py` maps `"claude-code"` to the direct
Runtime. That Runtime is lightweight: its models go through the
`anthropic:<id>` namespace, reusing the anthropic provider's wire, and its token
resolves from the `anthropic` pool. Model alias normalisation (opus / sonnet /
haiku) carries over.

**Expiry.** When a `cli_delegated` credential expires, AuthManager raises
`AuthReadOnlyError` — the credential is read-only and cannot refresh itself —
and the message directs the user to `claude login`. The direct path reuses this
rather than adding its own expiry handling.

The `api="claude-code-cli"` wire label is declared in
`_claude_code_registry.py` and has no consumer; requests always go through
Runtime to the `anthropic:<id>` Messages wire, so no wire implementation is
involved.

## Subscription login

The direct connection covers using a token; login covers how the token arrives.
claude-code uses the same PKCE framework as codex.

- **OAuth parameters** live in `auth_adapter.py`: `OAUTH_CLIENT_ID` =
  `9d1c250a-e61b-44d9-88ed-5944d1962f5e`, authorize =
  `claude.ai/oauth/authorize`, token = `console.anthropic.com/v1/oauth/token`,
  redirect = `console.anthropic.com/oauth/code/callback`. `build_pkce_config()`
  uses manual-paste mode, because Anthropic is a hosted redirect that displays
  `code#state` rather than a loopback callback, plus token JSON.
- **The shared PKCE framework** carries three switches for this —
  `manual_paste_only`, `redirect_uri_override`, `token_use_json` — along with
  `_credential_from_tokens` extraction and exchange with state, all in
  `pkce_oauth.py`. They generalize the framework rather than special-casing
  Anthropic inside it.
- **Refresh** is `_anthropic_refresh` (the refresh_token is swapped for a new
  one, JSON), registered on ProviderAuthConfig. Credentials with no
  refresh_token, such as setup-token, no-op automatically.
- **setup-token** goes through `import_setup_token`, which stores an oauth kind
  with an empty refresh_token and roughly a year's expiry.
- **Login methods** for anthropic and claude-code are `pkce_oauth` (default) and
  `setup_token` only. Importing from `~/.claude` and pasting an api key are not
  offered.
- **The driver** (`login_driver`) has an anthropic pkce branch plus setup_token
  dispatch; `_credential_provider_id` maps claude-code to anthropic so the
  credential lands in the anthropic pool.
- **Multiple accounts** are one profile each, reusing unified account management
  and 429 rotation.

## Account management in the WebUI

claude-code's accounts go through the general account routes, not
provider-specific ones. `webui/routes/accounts.py` maps claude-code to the
anthropic pool via `_pool_id`, so every general route stores and fetches by
pool, and `_api_key_env` returns `""` for claude-code, which forces
`add_mode=login` and hides the key-paste field. `setup_hints.py` describes the
provider as a direct Anthropic connection over subscription OAuth and explains
the two login methods.

The frontend needs no claude-code branch: `account-manager.tsx` and
`provider-login.tsx` are data-driven, so `add_mode=login` renders the two login
buttons on its own.

## Implementation status

The direct connection, subscription login, and WebUI account management
described above are all in place:

- `auth/resolver.py:_extract_token` plus `_read_delegated_token` re-read
  `store_path` for `cli_delegated`.
- `providers/anthropic/anthropic.py:stream_simple` and
  `registry.py:_http_api_key_for` (the `anthropic` create_runtime path)
  resolve through `resolve_api_key_sync`.
- `providers/anthropic/_claude_code_direct_runtime.py` holds the direct
  ClaudeCodeRuntime.
- `providers/registry.py` points `claude-code` at it.
- `tests/unit/test_claude_code_direct_oauth.py` covers the path;
  `test_runtime_key_ladder.py` mock points target the unified resolution.

`_max_proxy_runtime.py`, `_claude_max_proxy_registry.py`, and `_meridian_cli.py`
remain on disk but are no longer reached by any route or referenced by the
registry. They are removable once the WebUI's remaining "Add Claude account"
references are confirmed gone.
