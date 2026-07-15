# LLM-call fault tolerance & timeout management

A cross-project study of how reference agent frameworks call LLMs
robustly — retry, backoff, timeouts, connection handling, failover — and
where OpenProgram stands after the 2026-05 hardening pass.

Sources studied (all under `references/`, read-only):

| Project | Lang | Role |
|---|---|---|
| **openclaw** | TS | Claude-Code-style agent; the most complete transport layer |
| **opencode** (sst/opencode) | TS | Effect.js + Vercel-AI-SDK-style executor |
| **hermes-agent** (NousResearch) | **Py** | Closest analog to us; richest fault tolerance |
| **pi-ai** (badlogic/pi-mono) | TS | The direct reference our codex provider was ported from |
| **claude-code** | TS | Partial bundle; HTTP behavior = the Anthropic SDK |

---

## 1. The comparison matrix

| Dimension | openclaw | opencode | hermes-agent | pi-ai (codex) | OpenProgram (now) |
|---|---|---|---|---|---|
| Retry attempts | 3 (+2 inner transient) | 2 | 3 | 3 | 3 |
| Backoff base | 300 ms | 500 ms | 5 s | 1 s | 1 s |
| Backoff cap | 30 s | 10 s | 120 s | none | **30 s** ✅ new |
| Jitter | symmetric / positive | ±20% | decorrelated (0.5) | none | symmetric / positive |
| Retryable status | 408/409/429/5xx | 429/503/504/529 | 429/5xx/524 | 429/5xx | 429/5xx + body patterns |
| Retry-After | ms+sec+date | ms+sec+date (cap 10s) | none | none | **ms+sec+date** ✅ new |
| Body / idle timeout | **30 min, any byte** (undici) | none (HTTP) / 5 min (WS) | 180 s stale, context-scaled | none | **30 min any-byte + 15 min data-stall + 2 h cap** ✅ new |
| Connect timeout | undici default | none / 15 s (WS) | SDK default | none | 30 s |
| TTFB guard | 30 s (Azure) | n/a | 120 s (codex) | none | covered by idle/read |
| HTTP version | **force HTTP/1.1** | default | auto (h2) | — | httpx default (h1.1) |
| IPv6 / Happy Eyeballs | **autoSelectFamily** | no | no | — | ❌ gap |
| TCP keepalive tuning | undici default | no | **SO_KEEPALIVE 30/10/3** | — | ❌ gap |
| Connection reuse | undici keep-alive | WS pool, 55-min recycle | **shared client + rebuild on stale** | — | ❌ per-call client |
| API-key rotation | **yes** | no | **yes (pool + cooldowns)** | — | ❌ gap |
| Provider/model failover | **yes** | WS→HTTP only | **yes (chain)** | — | ❌ gap |
| After-first-token break | error | error | **partial + continue** | error | error |
| OAuth refresh mid-call | — | — | **per-request token provider** | per-call | per-call resolve |
| Rate-limit header parse | — | **yes (x-ratelimit-*)** | yes (Nous) | — | ❌ gap |
| Error classification | yes | yes (tagged union) | yes | basic | yes (`ErrorReason`) |

---

## 2. Notable per-project patterns

### openclaw (best transport layer)
- **Stream timeout = 30 min, set on the undici global dispatcher** as
  `bodyTimeout = headersTimeout = DEFAULT_UNDICI_STREAM_TIMEOUT_MS`
  (`src/infra/net/undici-global-dispatcher.ts:16`), reset on **any** byte.
  *This is the key insight:* don't put a tight read timeout on a
  reasoning stream — give it 30 minutes, reset on any traffic.
- **Forces HTTP/1.1** (`allowH2:false`) and **Happy Eyeballs**
  (`autoSelectFamily`) — avoids h2 stream resets and broken-IPv6 hangs
  (the classic VPN failure).
- **Two-tier retry**: outer `retry.ts` (3, 300ms→30s) + inner
  `operation-retry.ts` (2, 250ms→1s) for transient provider ops.
- **API-key rotation** (`api-key-rotation.ts`): outer loop over keys,
  inner transient retry per key.
- **Failover categories** (`failover-matches.ts`): rate_limit / overloaded
  / server / timeout / network — each a regex group.
- **Positive-only jitter** when honoring Retry-After (never sleep less
  than the server asked); **SDK-retry bypass** via `x-should-retry`.

### hermes-agent (richest; Python, closest to us)
- **TCP keepalive socket injection** (`run_agent.py`): `SO_KEEPALIVE=1`,
  `TCP_KEEPIDLE=30s`, `TCP_KEEPINTVL=10s`, `TCP_KEEPCNT=3` → **dead peer
  detected in ~60 s** instead of hanging. Plus force-close TCP before
  SDK close to avoid CLOSE_WAIT pileup.
- **Decorrelated jittered backoff** seeded from `time_ns ^ counter` so
  concurrent sessions don't retry in lockstep (base 5s, ×2, cap 120s).
- **Context-scaled stream-stale timeout**: 180s base, →240s >50k tokens,
  →300s >100k tokens; disabled entirely for local providers.
- **Separate TTFB vs inter-event timeouts** (codex TTFB 120s, disabled
  above 25k context to avoid false positives during long prefill).
- **Credential pool** with rotation strategies (round-robin / least-used)
  and exhaustion cooldowns (401→5 min, 429/402→1 h, dead→prune 24 h).
- **OAuth per-request token provider** via httpx event hook (refresh
  skew 60 s) — tokens refresh mid-session without rebuilding the client.
- **Partial-response recovery**: on a break *after* the first token it
  returns the partial text + `finish_reason=length` and lets the next
  turn continue — no lost work, no blind retry.

### opencode
- **No body/idle timeout on HTTP** — streams unbounded (like pi-ai).
- WebSocket path: connect 15s, idle 5min (reset per frame), **55-min
  connection-age recycle**, WS→HTTP fallback after 5 stream failures.
- **Rate-limit header parsing** for OpenAI + Anthropic into a structured
  object (enables proactive client-side throttling).
- Tagged-union error model; honors Retry-After (cap 10s).

### pi-ai (our codex's reference)
- `MAX_RETRIES=3`, `BASE_DELAY_MS=1000`, retries 429/5xx + body patterns —
  **no explicit body-read timeout**; relies on fetch + retry. (Our old
  120s httpx read cap was an OpenProgram-only addition — the bug.)

---

## 3. What we changed (2026-05 pass)

All in `openprogram/providers/`:

1. **Codex timeouts decoupled & made generous** (`openai_codex/openai_codex.py`):
   - httpx `Timeout(connect=30, read=1860, write=30, pool=30)` — the old
     single `timeout=120` float capped the body read at 120 s, firing
     before our idle budget over a buffering proxy/VPN (the reported bug).
   - SSE governor rebuilt into **two budgets + a backstop**, matching
     openclaw's "generous, reset-on-any-byte" model:
     - `SSE_IDLE_TIMEOUT_S = 1800` (30 min) — "no bytes at all", reset on
       **any** line (pings included) ≈ openclaw `bodyTimeout`.
     - `SSE_DATA_STALL_TIMEOUT_S = 900` (15 min) — **our extra**: "no real
       data", reset only on parsed events; catches ping-flood stalls
       openclaw can't see.
     - `SSE_TOTAL_TIMEOUT_S = 7200` (2 h) — runaway backstop.
   - All env-overridable (`OPENPROGRAM_SSE_*`, `OPENPROGRAM_HTTPX_*`).

2. **Backoff cap** (`utils/stream_retry.py`): exponential component capped
   at 30 s (`OPENPROGRAM_PROVIDER_STREAM_BACKOFF_MAX_S`); a larger
   server Retry-After is still honored.

3. **Retry-After: all three forms** (`utils/errors.py`): `retry-after-ms`,
   integer seconds, and HTTP-date — previously seconds-only.

---

## 4. What's now implemented vs deferred

**Implemented (new modules under `providers/utils/`, wired into codex; the
generic ones available to every HTTP provider):**

- **Central timeout policy** (`timeouts.py`) — one source of truth, loosened
  to OpenClaw's 30-min level, with context-scaling helpers.
- **Robust client builder** (`http_client.py`):
  - **TCP keepalive** — `SO_KEEPALIVE` + idle/interval/count → ~60 s dead-peer
    detection (the VPN drop case). Defensive per-OS; `OPENPROGRAM_TCP_KEEPALIVE=0`
    to disable.
  - **Force-IPv4** escape hatch (`OPENPROGRAM_FORCE_IPV4=1`) for broken-IPv6 VPNs
    (binds an IPv4 source address — httpx has no Happy-Eyeballs).
  - **Connection reuse** — `get_shared_async_client` (loop-keyed); codex now
    reuses its TLS connection across turns instead of re-handshaking.
  - **Proxy** via httpx 0.28 `proxy=` (fixed the removed `proxies=` form, a
    latent crash).
- **Rate-limit header parsing** (`rate_limit.py`) — `x-ratelimit-*` /
  `anthropic-ratelimit-*`; codex warns when a bucket is low/exhausted.
- **Partial-response recovery** (`openai_codex.py`) — a transient mid-stream
  break *after* content finalizes the partial turn (`stop_reason="length"`)
  instead of erroring; permanent failures (auth/invalid/context/policy) still
  hard-fail. Toggle `OPENPROGRAM_PARTIAL_RECOVERY=0`.
- **Provider/model failover** (`failover.py` + `agent_loop.py`) — classifier
  (rate_limit/overloaded/server/timeout/network) + a `stream_with_failover`
  wrapper that tries the primary then each configured fallback on a
  **pre-content** failover-worthy failure (forwards events, suppresses the
  duplicate `start`, never switches after a token streamed). Wired into the
  turn loop **default-OFF**: a no-op unless `OPENPROGRAM_FALLBACK_MODELS`
  ("provider/model,provider2/model2") is set.
- Wired codex + gemini_cli to the shared client; **fixed gemini's
  `timeout=120.0` single-float bug** (same class as codex's).
- (earlier) backoff **cap** + **Retry-After** all three forms.

**Cleanly disabled where not applicable (designed, off by default):**

- **API-key rotation** — the full machinery already exists in the auth layer
  (`auth/pool.py`: `pick` rotation + `mark_failure`/`report_failure` cooldowns
  with strategies and TTLs). Rotation **on acquire** is automatic when a pool
  has >1 credential. The per-call **failure-cooldown** reporting is deliberately
  NOT wired into the live single-account path: cooling down the only credential
  would self-lock the user out for zero benefit. So on a single account rotation
  is a clean no-op; it activates automatically once multiple credentials are
  configured. No half-wired risky code in the hot path.
- **OAuth per-request token provider** — codex already resolves + refreshes the
  bearer per call via the auth manager; a full httpx event-hook provider is a
  nicety, not a fix, so it's left out.

---

## 5. Tunables added

| Env var | Default | Meaning |
|---|---|---|
| `OPENPROGRAM_SSE_IDLE_TIMEOUT_S` | 1800 | no-bytes-at-all (any line resets) |
| `OPENPROGRAM_SSE_DATA_STALL_TIMEOUT_S` | 900 | no-real-data (data resets) |
| `OPENPROGRAM_SSE_TOTAL_TIMEOUT_S` | 7200 | single-stream runaway cap |
| `OPENPROGRAM_HTTPX_CONNECT_TIMEOUT_S` | 30 | connect (fast-fail dead VPN) |
| `OPENPROGRAM_HTTPX_READ_TIMEOUT_S` | idle+60 | httpx read backstop |
| `OPENPROGRAM_PROVIDER_STREAM_RETRIES` | 3 | per-stream retry attempts |
| `OPENPROGRAM_PROVIDER_STREAM_BACKOFF_S` | 1.0 | backoff base |
| `OPENPROGRAM_PROVIDER_STREAM_BACKOFF_MAX_S` | 30.0 | backoff exponential cap |
| `OPENPROGRAM_TCP_KEEPALIVE` | 1 | enable TCP keepalive (dead-peer detection) |
| `OPENPROGRAM_TCP_KEEPIDLE_S` / `_KEEPINTVL_S` / `_KEEPCNT` | 30 / 10 / 3 | keepalive probe timing (~60 s detection) |
| `OPENPROGRAM_FORCE_IPV4` | 0 | bind IPv4 source (broken-IPv6 VPNs) |
| `OPENPROGRAM_PARTIAL_RECOVERY` | 1 | salvage partial output on mid-stream break |
| `OPENPROGRAM_FALLBACK_MODELS` | (empty) | `provider/model,…` — enable provider/model failover |
