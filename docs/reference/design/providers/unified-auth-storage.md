# Unified self-contained auth storage

**Goal (user request):** every provider's credentials live under `~/.openprogram`,
managed by OpenProgram itself — stop the read-only "adopt" of other CLIs'
credential files (`~/.codex/auth.json`, `~/.claude/.credentials.json`,
`~/.gemini`, `~/.qwen`, `~/.config/gh`) and the external Meridian profile dir.
One store, one login flow, consistent across CLI / web / TUI. Borrow the proven
patterns from `references/opencode` and `references/openclaw`.

## What already exists (we are not starting from zero)

`openprogram/auth/store.py` already implements a real `AuthStore` at
`~/.openprogram/auth/<provider_id>/<profile_id>.json` (0600, atomic
write→fsync→replace, cross-process `flock`, in-memory mtime/size watch so a file
changed underneath re-loads). `openprogram/auth/types.py` defines the credential
kinds:

| kind | secret storage |
|---|---|
| `api_key` | copy of the key |
| `oauth` | copy: access + refresh + `expires_at_ms` + client_id + token_endpoint |
| `device_code` | copy (same shape as oauth) |
| `cli_delegated` | **POINTER only** — `store_path` + key-paths into an external file; re-read each use |
| `external_process` | argv run on demand |

`AuthManager` is **store-authoritative and never re-discovers**: it serves
whatever pool is on disk; "adopt" is an explicit, write-once step
(`cli_import`, `import_from_codex_file`). So once a credential is copied INTO the
store, nothing re-reads the external file. The migration primitive already
exists: `openprogram/auth/methods/cli_import.py` `mode="copy"` dereferences an
external file *now* and builds a writable, store-owned `oauth` credential.

### Current per-provider origin

| provider | origin today | self-contained? |
|---|---|---|
| `openai-codex` | native PKCE OR copy of `~/.codex/auth.json`; **OpenProgram refreshes** (`_codex_refresh` → `auth.openai.com/oauth/token`, mirrors back to `~/.codex`) | yes (refresh works) |
| `github-copilot` | native device-code → store oauth | yes |
| `openai`, `gemini`, other key providers | env → `config.json["api_keys"]` (NOT the pool store) | key-based, but dual-stored |
| `anthropic` (API key) | env/paste copy | yes |
| `anthropic` (subscription) | adopt `~/.claude/.credentials.json` pointer; **refresh = None** | no |
| `gemini-subscription` | adopt `~/.gemini/oauth_creds.json` pointer; **refresh = None** | no |
| `qwen` | adopt `~/.qwen/oauth_creds.json` pointer (no runtime package anyway) | no |
| `claude-code` | **Meridian daemon** owns OAuth in `~/.config/meridian`; no AuthStore footprint | separate subsystem |

## Borrowed patterns

- **opencode** (`references/opencode`) — fully self-contained, zero adoption.
  Even for the *same* OpenAI account the `codex` CLI uses, opencode runs its own
  PKCE (public `CLIENT_ID`) and stores the result in its own `auth.json`. A
  `provider → AuthHook` registry: each provider declares `methods[]` and an
  `authorize()` returning `method:"auto"` (loopback/poll, no paste) or
  `method:"code"` (user pastes). Refresh is **on-demand inside the request
  `fetch`**: compare `expires < Date.now()`, single-flight refresh, write tokens
  back. Crucially: **anthropic and google have NO OAuth in opencode — API key
  only.** That's the pragmatic answer to the two providers OpenProgram can't
  self-refresh.
- **openclaw** (`references/openclaw`) — `auth-profiles.json` keyed by
  `<provider>:<label>` profile id (multiple creds per provider), **secrets split
  from rotation/usage state** into a sibling file, `oauth | api_key | token`
  union with `token` = static non-refreshable bearer, secrets support inline OR
  a `SecretRef` (env/file/exec/keychain). Refresh is **on-demand under a
  cross-process lock that re-reads from disk inside the lock** (adopts a
  concurrent refresh instead of clobbering) — the race-safe core to copy. One
  shared `createVpsAwareOAuthHandlers` picks browser-callback vs paste-code from
  a remote-env flag, reused by every OAuth provider; one shared PKCE generator.
  **What NOT to copy:** openclaw's `cli-credentials.ts` + `external-cli-sync.ts`
  adopt codex/minimax/claude CLI files — exactly the cross-CLI coupling we drop.

## Hard constraints (not engineering gaps)

1. **`gemini-subscription`** can't be self-refreshed: Google's Code-Assist OAuth
   uses an embedded client secret OpenProgram can't ship
   (`google_gemini_cli/auth_adapter.py:14-21` declined for this reason).
2. **`anthropic` subscription OAuth** can't be self-run: Anthropic has not
   published a third-party OAuth client (`anthropic/auth_adapter.py:16-21`).
3. **`claude-code`** runs entirely through the Meridian daemon; its OAuth lives
   in Meridian's profile dir, and Meridian (not OpenProgram) refreshes it.

For (1) and (2) "self-contained storage" is achievable (copy the token into our
store, stop pointing at the external file) but "self-contained refresh" is not —
when the short-lived access token expires OpenProgram can only ask the user to
sign in again (or fall back to an API key, the opencode choice).

## Target architecture

1. **One store, copy not pointer.** Every credential is copied into
   `~/.openprogram/auth/<provider>/<profile>.json`. `cli_delegated` pointers are
   no longer the default; adoption becomes "import (copy) once", after which the
   external file is irrelevant. Adopt-link stays available only as an explicit
   opt-in.
2. **One login registry** (opencode/openclaw shape). A `provider → [auth method]`
   table where each method is one of `pkce_oauth | device_code | api_key |
   paste_code` backed by **shared helpers** (`pkce_browser_flow`,
   `device_code_flow`, a `browser_vs_paste` chooser keyed off a remote/headless
   flag — which also cleans up the claude-code paste-code flow). OpenProgram
   already has `methods/{pkce_oauth,device_code,api_key_paste,cli_import}.py`;
   they just aren't wired to every provider or every surface.
3. **Three surfaces drive the same registry.** Today only the CLI can natively
   run PKCE/device-code (codex/copilot); web does native only for claude-code
   (Meridian), TUI punts everything else to web. Target: web + TUI both drive the
   registry, so any provider's login works from any surface.
4. **Refresh ownership moves in-house** where possible (codex ✓, copilot ✓; new
   refreshers feasible for anything with a public client). The two constrained
   providers copy-into-store + prompt re-login on expiry.
5. **Reconcile the api_key dual-store.** Key providers resolve at runtime via
   env → `config.json["api_keys"]`, not the pool. Make the pool authoritative
   and mirror to `config.json` (or vice-versa) so there's one source of truth.

## Phased migration

- **P1 — Codex defaults to self-contained.** Prefer OpenProgram's own PKCE +
  refresh; copy `~/.codex` into the store on first use instead of pointing at it;
  keep import as an explicit option. (Lowest risk: refresh already works.)
- **P2 — Unified login registry + shared helpers + native web/TUI login** for
  codex & copilot (the two with working native OAuth). Extract the
  browser-vs-paste chooser.
- **P3 — Copy gemini-subscription / qwen / anthropic-subscription INTO the
  store** (stop pointing at `~/.gemini`/`~/.qwen`/`~/.claude`); on expiry, prompt
  re-login (honest about the refresh constraint), with API key as the
  always-available fallback.
- **P4 — Relocate Meridian (claude-code) under `~/.openprogram`** by pointing its
  config dir at `~/.openprogram/meridian` instead of `~/.config/meridian`, so even
  the proxy's profiles live under the app dir.
- **P5 — Collapse the api_key dual-store** (pool authoritative + config.json
  mirror).

## Decisions (confirmed with user)

- **Scope:** do the uncontroversial core first — codex/copilot fully
  self-contained + unified login across CLI/web/TUI — then confirm P3 and the
  rest phase by phase. Not a single big-bang (avoids breaking the working codex
  share and the just-fixed claude-code account).
- **Constrained providers (gemini-subscription, anthropic subscription):** copy
  the token into the store and re-login on expiry. Stop pointing at `~/.gemini` /
  `~/.claude`; since OpenProgram can't auto-refresh these, an expired access
  token prompts a fresh sign-in — acceptable, they rarely expire and a rotation
  just means signing in again. (P3.)
- **Meridian (claude-code):** leave at `~/.config/meridian`, NOT relocated.
  claude-code is already isolated from the terminal `claude login`; only its
  directory isn't physically under `~/.openprogram`, which is acceptable. **P4
  is dropped.**

## Build order (revised after decisions)

1. **Login-method registry** — one declarative `provider → [method]` table
   (`pkce_oauth | device_code | api_key | paste_code | import`) as the single
   source of truth, replacing the ad-hoc map in `auth/cli.py
   _available_login_methods`. Each method names a shared handler. CLI reads from
   it first (no behaviour change — pure refactor, verifiable).
2. **Shared login handlers** — extract `pkce_browser_flow`, `device_code_flow`,
   and a `browser_vs_paste` chooser (remote/headless flag) from the existing
   `auth/methods/*` so all three surfaces call the same code.
3. **Web native login** — drive the registry from the provider detail page so
   codex/copilot log in from web (today web only does claude-code natively).
4. **TUI native login** — same, from the `/login` panel (today it punts to web).
5. Later, per separate confirmation: P3 (gemini/qwen/anthropic copy-into-store),
   P5 (api_key pool↔config.json single source).
