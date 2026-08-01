# Credential status — usable or stopped

A credential is either usable, or stopped for a reason the user must act on.
There is no user-visible "cooling" state.

## Why not a cooling window

A pay-as-you-go API key has no natural cooling state, so collapsing different
failures into one `cooldown_until_ms` window gives each of them the wrong
behaviour:

- a 404 (model not found) would cool the whole key, punishing every other model
  on that key for one bad request;
- a 402 (out of credits) would cool the key for hours, but credits do not come
  back by waiting — the user has to top up;
- a 5xx would cool the key, but an upstream outage says nothing about the key.

Only subscription and quota accounts (Claude Pro windows, free-tier models)
genuinely have "wait until reset" semantics, and that is a different concept
from key health.

## What other frameworks do

- **OpenClaw** (our pool's ancestor) keeps THREE separate states:
  `cooldownUntil` (transient 429, laddered 30s→1m→5m), `disabledUntil`
  (402 billing / permanent auth — disabled, exponential backoff), and
  `blockedUntil` (subscription quota with a real reset timestamp from
  the usage API). 5xx never touches profile health; OpenRouter is
  explicitly exempted from cooldowns.
- **opencode**: no key pool, no cooldown. Errors are request-level —
  429/5xx retried twice with exponential backoff, everything else is a
  structured error returned to the caller.
- **Claude Code**: single account. Every failure is a chat-side message
  with an action (402 → top up, 401 → /login, 404 → /model, quota →
  reset countdown + options menu). Settings show no transient state.

## The status model

**User-visible status (persisted, shown in the accounts panel):**

| status | meaning | recovery |
|---|---|---|
| `valid` | usable | — |
| `billing_blocked` | 402 — stopped, out of credits | top up, then Validate (success auto-restores `valid`) |
| `needs_reauth` | 401/403 — stopped, credential rejected | re-add key / sign in |
| `revoked` | permanently dead | replace |
| `rate_limited` | 429 — briefly throttled | auto-restores on next success / window expiry |

The status column carries the whole answer, so no separate badge is needed.
A `rate_limited` credential whose window has expired reports as `valid`.

**Internal scheduling (never shown):**

- 429 keeps a short `cooldown_until_ms` so multi-key rotation skips the
  throttled key; single-key setups still send (better than nothing).
- 5xx and network errors do not touch the credential — transport failures say
  nothing about key health, matching OpenClaw's semantics.
- Request-level 4xx (404/400/422) do not touch the credential; they are
  `request_error`.

**Chat side:** stream errors render as red error bubbles carrying the
provider's own message ("Insufficient Balance", …). That is the user-facing
notification; the accounts panel is only for diagnosis.

## How the status is maintained

- `auth/usage.py report_failure` — only `rate_limit`, `rate_limit_long`,
  `billing_blocked`, and `needs_reauth` reach the pool; `request_error`,
  `server_error`, and `network_error` return without touching it.
- `auth/pool.py mark_failure` — `billing_blocked` sets the status with no
  cooldown timestamp: stopped until re-validated, rather than a timed wait.
- `auth/pool.py` auto-restore — only `rate_limited` self-heals;
  `billing_blocked` is excluded, so validation is the only way back.
- `auth/usage.py _account_healthy` — `billing_blocked` counts as unhealthy for
  rotation alongside `revoked` and `needs_reauth`.
- `webui/routes/accounts.py` — a successful Validate writes `status="valid"`
  and clears the cooldown and `last_error`, which closes the top-up → Validate
  → restored loop; the account record carries no `cooling` field, and a
  `rate_limited` credential past its window reports `valid`.
- `web .. account-manager.tsx` — status renders as
  有效 / 限流中 / 欠费停用 / 需重新验证 / 已失效.
