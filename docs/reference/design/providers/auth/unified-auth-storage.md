# Self-contained auth storage

Every provider's credentials live under `~/.openprogram`, managed by OpenProgram
itself, rather than being read out of other CLIs' credential files
(`~/.codex/auth.json`, `~/.claude/.credentials.json`, `~/.gemini`, `~/.qwen`,
`~/.config/gh`) on every use. One store, one login flow, consistent across CLI,
web, and TUI.

## The store

`openprogram/auth/store.py` implements `AuthStore` at
`~/.openprogram/auth/<provider_id>/<profile_id>.json` — 0600, atomic
write→fsync→replace, cross-process `flock`, and an in-memory mtime/size watch so
a file changed underneath is re-loaded. `openprogram/auth/types.py` defines the
credential kinds:

| kind | secret storage |
|---|---|
| `api_key` | copy of the key |
| `oauth` | copy: access + refresh + `expires_at_ms` + client_id + token_endpoint |
| `device_code` | copy (same shape as oauth) |
| `cli_delegated` | **POINTER only** — `store_path` + key-paths into an external file; re-read each use |
| `external_process` | argv run on demand |

`AuthManager` is **store-authoritative and never re-discovers**: it serves
whatever pool is on disk. Importing is an explicit, write-once step
(`cli_import`, `import_from_codex_file`), so once a credential is copied into
the store, nothing re-reads the external file.
`openprogram/auth/methods/cli_import.py` with `mode="copy"` dereferences an
external file once and builds a writable, store-owned `oauth` credential.

### Where each provider's credential comes from

| provider | origin | self-contained? |
|---|---|---|
| `openai-codex` | native PKCE OR copy of `~/.codex/auth.json`; **OpenProgram refreshes** (`_codex_refresh` → `auth.openai.com/oauth/token`, mirrors back to `~/.codex`) | yes (refresh works) |
| `github-copilot` | native device-code → store oauth | yes |
| `openai`, `gemini`, other key providers | env → `config.json["api_keys"]` (NOT the pool store) | key-based, but dual-stored |
| `anthropic` (API key) | env/paste copy | yes |
| `anthropic` (subscription) | `~/.claude/.credentials.json` pointer; **refresh = None** | no |
| `gemini-subscription` | `~/.gemini/oauth_creds.json` pointer; **refresh = None** | no |
| `qwen` | `~/.qwen/oauth_creds.json` pointer (no runtime package anyway) | no |

## Patterns borrowed from the references

**opencode** is fully self-contained with zero adoption. Even for the same
OpenAI account the `codex` CLI uses, opencode runs its own PKCE with a public
`CLIENT_ID` and stores the result in its own `auth.json`. It keys a `provider →
AuthHook` registry where each provider declares `methods[]` and an
`authorize()` returning `method:"auto"` (loopback/poll, no paste) or
`method:"code"` (the user pastes). Refresh happens on demand inside the request
`fetch`: compare `expires < Date.now()`, single-flight refresh, write tokens
back. Notably, anthropic and google have **no OAuth in opencode at all** — API
key only, which is their answer to the two providers that cannot be
self-refreshed.

**openclaw** keys `auth-profiles.json` by `<provider>:<label>` profile id
(multiple credentials per provider) and splits **secrets from rotation/usage
state** into a sibling file. Its credential union is `oauth | api_key | token`,
where `token` is a static non-refreshable bearer, and secrets can be inline or a
`SecretRef` (env/file/exec/keychain). Refresh runs on demand under a
cross-process lock that **re-reads from disk inside the lock**, so a concurrent
refresh is adopted rather than clobbered — that race-safe core is the part worth
copying. One shared `createVpsAwareOAuthHandlers` picks browser-callback versus
paste-code from a remote-env flag and is reused by every OAuth provider, over
one shared PKCE generator.

What openclaw does that this design does not follow: `cli-credentials.ts` and
`external-cli-sync.ts` read codex/minimax/claude CLI files directly — the
cross-CLI coupling this design removes.

## Constraints that are not engineering gaps

1. **`gemini-subscription`** cannot be self-refreshed: Google's Code-Assist
   OAuth uses an embedded client secret OpenProgram cannot ship
   (`google_gemini_cli/auth_adapter.py:14-21`).
2. **`anthropic` subscription OAuth** cannot be self-run: Anthropic has not
   published a third-party OAuth client (`anthropic/auth_adapter.py:16-21`).

For both, self-contained *storage* is achievable — copy the token into the
store, stop pointing at the external file — but self-contained *refresh* is not.
When the short-lived access token expires, OpenProgram can only ask the user to
sign in again, or fall back to an API key, which is the choice opencode makes.

## Design

1. **One store, copy not pointer.** Every credential is copied into
   `~/.openprogram/auth/<provider>/<profile>.json`. `cli_delegated` pointers are
   not the default; importing is a one-time copy, after which the external file
   is irrelevant. A pointer link remains available as an explicit opt-in.
2. **One login registry.** A `provider → [auth method]` table where each method
   is one of `pkce_oauth | device_code | api_key | paste_code`, backed by shared
   helpers (`pkce_browser_flow`, `device_code_flow`, and a `browser_vs_paste`
   chooser keyed off a remote/headless flag). It replaces the ad-hoc map in
   `auth/cli.py::_available_login_methods` as the single source of truth, and
   each method names a shared handler. `auth/methods/{pkce_oauth,device_code,
   api_key_paste,cli_import}.py` supply the handlers.
3. **Three surfaces drive the same registry.** Web and TUI both drive it, so any
   provider's login works from any surface rather than the CLI being the only
   place native PKCE and device-code run.
4. **Refresh ownership in-house** where a public client exists (codex and
   copilot today). The two constrained providers copy into the store and prompt
   re-login on expiry.
5. **One source of truth for api keys.** Key providers currently resolve at
   runtime via env → `config.json["api_keys"]` rather than the pool. The pool
   becomes authoritative with a `config.json` mirror, so the dual store
   collapses.

For the constrained providers the accepted behaviour is explicit: copy the token
into the store and re-login on expiry, stopping the pointer into `~/.gemini` and
`~/.claude`. Since OpenProgram cannot auto-refresh these, an expired access
token prompts a fresh sign-in; they rarely expire, and a rotation just means
signing in again.

## Build order

1. **Login-method registry** — the declarative `provider → [method]` table as
   the single source of truth, with the CLI reading from it first. A pure
   refactor with no behaviour change, so it is verifiable on its own.
2. **Shared login handlers** — extract `pkce_browser_flow`, `device_code_flow`,
   and the `browser_vs_paste` chooser from `auth/methods/*` so all three
   surfaces call the same code.
3. **Web native login** — drive the registry from the provider detail page.
4. **TUI native login** — the same, from the `/login` panel.
5. Copying gemini-subscription, qwen, and anthropic-subscription into the store,
   and collapsing the api_key dual store.

Doing the uncontroversial core first — codex and copilot fully self-contained,
plus unified login across CLI/web/TUI — keeps the working codex share intact
rather than changing everything at once.
