# Unified account management + pool rotation/fallback

**Goal (user):** one consistent way to manage accounts across CLI / web / TUI —
list / add / activate / rename / remove multiple accounts per provider, plus
rotation / failover with on/off switches. The *backend* per provider (Meridian
for claude-code, AuthStore for the rest) is an implementation detail; the
**management surface is unified**. Builds on docs/design/unified-auth-storage.md
(the login side, P1).

## Core idea

An **account = a named profile**. AuthStore already keys every credential pool
by `(provider_id, profile_id)` and persists one file per pool
(`~/.openprogram/auth/<provider>/<profile>.json`), and `ProfileManager` already
does profile CRUD. So "multiple accounts" and "multiple profiles" are the same
concept — each account is a profile id. claude-code's `ClaudeAccounts` panel
(list / add-via-login / activate / rename / remove) is the UX template to
**generalize to every provider**; claude-code becomes just one instance of it.

## What's already there (don't rebuild)

- Multi-profile storage + `ProfileManager` CRUD (`auth/store.py`, `auth/profiles.py`).
- Pool strategy model — `PoolStrategy = fill_first | round_robin | random |
  least_used`, `credentials[]`, `_rr_cursor`, `fallback_chain`, per-cred
  `cooldown_until_ms` / `status` — all serialized (`auth/types.py:335-390`).
- Pool selection honoring strategy + health filter + cooldown skip
  (`auth/pool.py:99-161`); fallback recursion (`auth/manager.py:247-312`);
  cooldown durations + `mark_failure`/`mark_success`/`clear_cooldown`
  (`auth/pool.py:57-276`); manager wrappers `report_failure`/`report_success`
  (`auth/manager.py:450-497`).
- A richer multi-profile REST surface already mounted (`webui/_auth_routes.py`:
  `/profiles`, `/pools`, `/pools/.../credentials`, `/doctor`, SSE `/events`) —
  used by only one page today.
- The unified login endpoints from P1 (`/api/providers/{id}/login/{start,poll,
  submit,cancel}`) + `<ProviderLogin>`.

## The two load-bearing gaps (fix first — everything else is cosmetic without them)

1. **No active-profile selection at request time.** `AuthManager.acquire`
   defaults `profile_id="default"` and the request path never enters
   `auth_scope(...)`, so a user can't actually run on "work" vs "personal".
   The *only* working active-account selector is claude-code's, via the
   provider-config value `meridian_profile`. Needed: a generic
   `get/set_active_profile(provider_id)` + make `acquire`/`resolver` default to
   it + have the chat/execute entry enter the scope.
2. **Rotation/cooldown/fallback never engage.** `report_failure` / `report_success`
   have **zero callers** outside their definitions — no provider runtime feeds a
   429/402/5xx back to the pool, so `cooldown_until_ms` stays 0, `fill_first`
   always returns cred #0, and rotation/fallback are dead. Needed: runtimes call
   `manager.report_failure(...)` on failure and `report_success(...)` on 2xx.

(Also: a second OAuth login is pruned by `_prune_superseded_oauth`, so OAuth
pools can't accumulate two creds — fine for rotation across *API keys*, which is
the real use case; OAuth multi-account is handled by multiple *profiles*.)

## Target architecture

- **active account = active profile**, settable per provider, honored by the
  runtime (gap 1).
- **Generic accounts REST** `/api/providers/{id}/accounts/*` mirroring the
  claude-code shape so the frontend is literally reused:
  `GET …/accounts` → `{active, accounts:[{name,label,email?,status,kind}]}`,
  `POST …/accounts/use {name}` (""=deactivate), `…/rename {old,new}`, add (reuse
  `/login/start|poll|submit` with a target account name), remove (reuse the
  existing credential/pool delete). claude-code keeps its Meridian-backed
  implementation behind the *same* routes (adapter), so the UI doesn't branch.
- **One `<ProviderAccounts>` React component** (generalized from
  `claude-accounts.tsx`) + **one Ink picker** (generalized from
  `claudeAccounts.tsx`), reused for every provider. `detail.tsx` renders it for
  all; TUI `/login <prov>` stops punting to web.
- **Pool controls** — `PATCH /api/providers/pools/{prov}/{prof}` to set
  `strategy` + `fallback_chain` on/off, `…/clear_cooldown` ("retry now"), a
  `…/health` view; surfaced as a strategy dropdown + fallback toggle + per-cred
  health badge in web/TUI, and `providers pool {strategy,fallback,retry}` CLI
  verbs. Meaningful only after gap 2 is wired.

## Phased plan

- **P-A — active-profile infrastructure** ✅ DONE (commits 836a4a9b, d8454d6a).
  `auth/active.py` (`get/set_active_profile(provider)` + `get_active_pin`);
  `acquire`/`resolver` default to it; CLI `providers use <provider> [profile]`
  + `← active` marker in `providers list`. Default stays `"default"`, fully
  backward compatible.
- **P-B — generic accounts surface + unified UI** ✅ DONE (commits 45cac805,
  4c98adcc, bedb3439).
  - Backend: `routes/accounts.py` serves `/api/providers/{id}/accounts/*`
    (list/use/rename/remove from the AuthStore; add hands the UI the login
    methods). claude-code keeps its literal Meridian routes (registered first
    so they shadow `{provider}`); both report `add_mode` (`code_paste` vs
    `login`).
  - Web: one `<ProviderAccounts>` (generalized from `claude-accounts.tsx`)
    rendered for claude-code + login-only providers; `<ProviderLogin>` gained
    `profileId`/`bare` so it's the embedded "add account" step.
  - TUI: one generic picker (`providerAccounts.tsx`) + an in-TUI login flow
    (`providerLoginFlow.tsx`) driving the shared `/login/*`; `/login <provider>`
    opens it for ANY provider (no more punting to web).
- **P-C — rotation/failover wiring + switches** ✅ DONE (commits e0b04aa0,
  ac9b7f53, adbbf8af).
  - P-C1 wiring (`auth/usage.py` + `openai_completions.stream_simple`): the call
    path acquires per-request from the pool and reports the outcome
    (`report_failure`/`report_success`), so a 429 cools a key down and the outer
    retry rotates to the next. Gated — no-op unless the provider has a real
    AuthStore pool, so env-key / OAuth / claude-code are byte-for-byte unchanged.
  - P-C2 control surface (`routes/accounts.py`): `GET …/{name}/keys` (masked +
    per-key health + strategy), `POST …/{name}/strategy`, `…/{name}/retry`
    ("clear cooldowns"), `POST/DELETE …/{name}/keys` (add/remove a key); the
    account record gained `strategy` + `cooling`.
  - P-C3 web (`pool-controls.tsx`): a "Keys & rotation" panel on api-key
    providers — per-key health badges, strategy dropdown, "Retry now", add/remove.

  Remaining (secondary, not blocking the mandate): a `fallback_chain` toggle in
  the UI; TUI pool controls (web + REST + CLI-via-REST already cover it); native
  `providers pool …` CLI verbs.

## Backend (claude-code stays on Meridian)

claude-code keeps Meridian as its backend; it's adapted behind the unified
`/accounts/*` routes so the management UX matches every other provider. (The
design workflow confirmed a native AnthropicRuntime+OAuth path also exists and
works — `utils/oauth/anthropic.py` + the OAuth-token serving in `anthropic.py`
— so claude-code *could* later drop Meridian with no UX change; that's an
optional future simplification, explicitly out of scope here since the backend
is "whatever works".)

## Won't-break guardrails

- `default` profile stays the default everywhere; activating another profile is
  opt-in. The working yzhang6294 claude-code account is untouched (still Meridian).
- P-A ships behind the existing behavior (active defaults to "default"); P-C's
  switches are inert until a strategy other than fill_first is chosen.

## P-D — one management component for every provider (UI unification)

**Problem (user):** api-key providers and login providers showed *different*
panels (`<ProviderKeys>` vs `<ProviderAccounts>`) — different layout, labels,
interactions. That difference was incidental (two components I wrote
separately), not necessary. The only things that genuinely differ per provider
are **how you add** (paste a key / sign in / paste a code) and **what an
identity looks like** (a masked key / an email).

**Model.** Every provider has **accounts** = named, switchable credentials:
- api-key provider → an account is a **key** (id = credential_id, identity =
  masked key).
- login provider (codex / copilot / gemini-sub) → an account is a **sign-in**
  (id = profile_id, identity = email).
- claude-code → an account is a **Claude subscription** (Meridian profile).

Uniform operations everywhere: **rename**, **Use** (switch the active one),
**remove**, and an optional **rotation toggle** (off by default; on = rate-limit
failover across the accounts, where the backend supports it). Only **add**
branches: paste-key (+validate) / the shared sign-in flow / code-paste.

**Shape.** One React `<AccountManager driver={…}>` renders the list + rotation
toggle + the add area; a thin **driver** per backend supplies the data and the
use/rename/remove/rotation calls, wrapping the existing endpoints (api-key →
`…/accounts/default/keys*`; login/claude-code → `…/accounts*`). No backend
rearchitect — claude-code stays on Meridian. `detail.tsx` renders exactly one
`<AccountManager>` for every provider; `<ProviderKeys>` / `<ProviderAccounts>` /
the standalone `<ProviderLogin>` collapse into it.

(Future, optional: lift accounts to profiles for api-key too + rotate across
profiles, so the backend is uniform as well, not just the UI.)
