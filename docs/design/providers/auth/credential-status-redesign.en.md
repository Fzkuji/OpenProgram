# Credential status redesign — "usable or stopped", no COOLING badge

## Problem

The account pool flattened three different failure semantics into one
`cooldown_until_ms` window, surfaced in the UI as a COOLING badge:

- a 404 (model not found) cooled the whole key for 30s — punishing every
  other model on that key for one bad request;
- a 402 (out of credits) "cooled" the key for 24h — but credits don't
  come back by waiting, the user has to top up;
- a 5xx cooled the key — but an upstream outage says nothing about the key.

A pay-as-you-go API key has no natural "cooling" state. It is either
usable, or stopped for a reason the user must act on (top up / re-auth).
Only subscription/quota accounts (Claude Pro windows, free-tier models)
genuinely have "wait until reset" semantics — and that is a different
concept from key health.

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

## New model

**User-visible status (persisted, shown in the accounts panel):**

| status | meaning | recovery |
|---|---|---|
| `valid` | usable | — |
| `billing_blocked` | 402 — stopped, out of credits | top up, then Validate (success auto-restores `valid`) |
| `needs_reauth` | 401/403 — stopped, credential rejected | re-add key / sign in |
| `revoked` | permanently dead | replace |
| `rate_limited` | 429 — briefly throttled | auto-restores on next success / window expiry |

No separate COOLING badge: the status column itself says everything.
`rate_limited` whose window has expired reports as `valid`.

**Internal scheduling (never shown):**

- 429 keeps a short `cooldown_until_ms` so multi-key rotation skips the
  throttled key; single-key setups still send (better than nothing).
- 5xx / network errors do NOT touch the credential — transport failures
  say nothing about key health (OpenClaw semantics).
- Request-level 4xx (404/400/422) do NOT touch the credential (landed
  earlier as `request_error`).

**Chat side:** stream errors already render as red error bubbles with
the provider's own message ("Insufficient Balance", …) — that is the
user-facing notification; the accounts panel is only for diagnosis.

## Changes

1. `auth/usage.py report_failure` — only `rate_limit`, `rate_limit_long`,
   `billing_blocked`, `needs_reauth` reach the pool; `request_error`,
   `server_error`, `network_error` return without touching it.
2. `auth/pool.py mark_failure` — `billing_blocked` sets the status with
   NO cooldown timestamp (stopped until re-validated, not "wait 24h").
3. `auth/pool.py` auto-restore — only `rate_limited` self-heals;
   `billing_blocked` is excluded (validate is the only way back).
4. `auth/usage.py _account_healthy` — `billing_blocked` joins
   `revoked`/`needs_reauth` as unhealthy for rotation.
5. `webui/routes/accounts.py` — Validate success writes
   `status="valid"`, clears cooldown + last_error (closing the top-up →
   Validate → restored loop); the `cooling` field is removed from the
   account record; `rate_limited` past its window reports `valid`.
6. `web .. account-manager.tsx` — COOLING badge removed; status renders
   as 有效 / 限流中 / 欠费停用 / 需重新验证 / 已失效.
