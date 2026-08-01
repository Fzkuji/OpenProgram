# Account management and pool rotation

Accounts are managed the same way across CLI, web, and TUI: list, add, activate,
rename, remove, with several accounts per provider, plus rotation and failover
that can be switched on or off. The backend behind a given provider is an
implementation detail; the management surface is uniform. This builds on the
login side described in [unified-auth-storage.md](./unified-auth-storage.md).

## An account is a profile

AuthStore keys every credential pool by `(provider_id, profile_id)` and persists
one file per pool at `~/.openprogram/auth/<provider>/<profile>.json`, and
`ProfileManager` already does profile CRUD. "Multiple accounts" and "multiple
profiles" are therefore the same concept — each account is a profile id.

The constraint that forces this model is OAuth refresh-token rotation:
`_prune_superseded_oauth` means **at most one OAuth credential can live in a
pool**, so OAuth multi-account has to be separate profiles. Since that model
also covers api-key providers, account = profile is the only model that covers
every provider, and each named api-key is a profile too.

## What the pool provides

The storage and rotation machinery it builds on:

- Multi-profile storage plus `ProfileManager` CRUD (`auth/store.py`,
  `auth/profiles.py`).
- The pool strategy model — `PoolStrategy = fill_first | round_robin | random |
  least_used`, `credentials[]`, `_rr_cursor`, `fallback_chain`, and per-credential
  `cooldown_until_ms` / `status`, all serialized (`auth/types.py:335-390`).
- Pool selection honouring strategy, health filter, and cooldown skip
  (`auth/pool.py:99-161`); fallback recursion (`auth/manager.py:247-312`);
  cooldown durations with `mark_failure` / `mark_success` / `clear_cooldown`
  (`auth/pool.py:57-276`); and the manager wrappers `report_failure` /
  `report_success` (`auth/manager.py:450-497`).
- A multi-profile REST surface (`webui/_auth_routes.py`: `/profiles`, `/pools`,
  `/pools/.../credentials`, `/doctor`, SSE `/events`).
- The unified login endpoints (`/api/providers/{id}/login/{start,poll,submit,
  cancel}`) and `<ProviderLogin>`.

## Two things the pool needs to be live

Rotation and per-account selection are inert unless two connections exist, and
without them the rest of the surface is decoration:

1. **Active-profile selection at request time.** `AuthManager.acquire` defaults
   to `profile_id="default"`, so unless the request path enters
   `auth_scope(...)` a user cannot actually run on "work" rather than
   "personal". `auth/active.py` provides `get_active_profile(provider)` /
   `set_active_profile(provider)` plus `get_active_pin`; `acquire` and the
   resolver default to it, and the chat/execute entry enters the scope. The
   default stays `"default"`, so activating another profile is opt-in.
2. **Outcome reporting from the call path.** `report_failure` and
   `report_success` only matter if a provider runtime feeds a 429/402/5xx back
   to the pool. Without that, `cooldown_until_ms` stays 0, `fill_first` always
   returns credential #0, and rotation and fallback never engage. The call path
   (`auth/usage.py` plus `openai_completions.stream_simple`) acquires per
   request from the pool and reports the outcome, so a 429 cools a key down and
   the outer retry rotates to the next.

The reporting is gated: it is a no-op unless the provider has a real AuthStore
pool, so env-key and OAuth paths are unaffected.

## The management surface

**REST.** `/api/providers/{id}/accounts/*` is generic:
`GET …/accounts` returns `{active, accounts:[{name,label,email?,status,kind}]}`;
`POST …/accounts/use {name}` activates one (`""` deactivates); `…/rename
{old,new}`; add reuses `/login/start|poll|submit` with a target account name;
remove reuses the existing credential/pool delete. Every provider reports an
`add_mode` (`code_paste` or `login`) so the frontend does not branch on provider
identity.

**Pool controls.** `GET …/{name}/keys` returns masked keys with per-key health
and the current strategy; `POST …/{name}/strategy` sets it; `…/{name}/retry`
clears cooldowns; `POST`/`DELETE …/{name}/keys` add and remove a key. The
account record carries `strategy` and cooling state.

**One component per surface.** Web renders a single `<AccountManager
driver={…}>` for every provider — the list, the rotation toggle, and the add
area — with a thin driver per backend supplying the data and the
use/rename/remove/rotation calls over the endpoints above. The TUI has one
generic picker (`providerAccounts.tsx`) and an in-TUI login flow
(`providerLoginFlow.tsx`) driving the shared `/login/*`, so `/login <provider>`
works for any provider rather than deferring to web.

The reason for one component is that api-key providers and login providers
differ in only two respects: **how you add** (paste a key, sign in, or paste a
code) and **what an identity looks like** (a masked key or an email). Everything
else — rename, Use, remove, and the optional rotation toggle — is uniform, so
separate `<ProviderKeys>` and `<ProviderAccounts>` panels would differ for no
reason.

## Per-account operations

Uniform across providers: **rename**, **Use** (switch the active account),
**remove**, and an optional **rotation toggle**, off by default. Turned on,
rotation rotates across the provider's profiles — cooling a profile's credential
on a 429, skipping it, and moving on; off means the active profile only.
Rotation lives in `auth/usage.acquire_pooled` plus a per-provider rotation
setting, leaving the hot `manager.acquire` path untouched.

On a profile's credential: reveal the full key, update (replace it), validate
this one, and validate-all.

Only **add** branches per backend: an api-key add creates a profile and adds the
key; a login add is the shared login flow with `profile=<name>`.

## Implementation status

In place: active-profile infrastructure (`auth/active.py`, CLI `providers use
<provider> [profile]`, the `← active` marker in `providers list`); the generic
accounts REST surface and the unified web and TUI components; and the rotation
wiring with its control surface (`routes/accounts.py` strategy/retry/keys
endpoints, the "Keys & rotation" panel in `pool-controls.tsx`).

Remaining: a `fallback_chain` toggle in the UI; TUI pool controls (web, REST,
and CLI-via-REST already cover the same operations); native `providers pool …`
CLI verbs; and retiring the api-key credentials-in-pool surface
(`…/accounts/default/keys*`, pool `active_credential_id`/`fixed`) now that
account = profile is the model.
