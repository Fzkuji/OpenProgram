# LLM-call fault tolerance & timeout management

How OpenProgram calls LLMs robustly — retry, backoff, timeouts, connection
handling, failover — and how reference agent frameworks solve the same
problems.

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

| Dimension | openclaw | opencode | hermes-agent | pi-ai (codex) | OpenProgram |
|---|---|---|---|---|---|
| Retry attempts | 3 (+2 inner transient) | 2 | 3 | 3 | 3 |
| Backoff base | 300 ms | 500 ms | 5 s | 1 s | 1 s |
| Backoff cap | 30 s | 10 s | 120 s | none | **30 s** |
| Jitter | symmetric / positive | ±20% | decorrelated (0.5) | none | symmetric / positive |
| Retryable status | 408/409/429/5xx | 429/503/504/529 | 429/5xx/524 | 429/5xx | 429/5xx + body patterns |
| Retry-After | ms+sec+date | ms+sec+date (cap 10s) | none | none | **ms+sec+date** |
| Body / idle timeout | **30 min, any byte** (undici) | none (HTTP) / 5 min (WS) | 180 s stale, context-scaled | none | **30 min any-byte + 15 min data-stall + 2 h cap** |
| Connect timeout | undici default | none / 15 s (WS) | SDK default | none | 30 s |
| TTFB guard | 30 s (Azure) | n/a | 120 s (codex) | none | covered by idle/read |
| HTTP version | **force HTTP/1.1** | default | auto (h2) | — | httpx default (h1.1) |
| IPv6 / Happy Eyeballs | **autoSelectFamily** | no | no | — | force-IPv4 escape hatch |
| TCP keepalive tuning | undici default | no | **SO_KEEPALIVE 30/10/3** | — | **SO_KEEPALIVE 30/10/3** |
| Connection reuse | undici keep-alive | WS pool, 55-min recycle | **shared client + rebuild on stale** | — | shared loop-keyed client |
| API-key rotation | **yes** | no | **yes (pool + cooldowns)** | — | yes (pool + cooldowns) |
| Provider/model failover | **yes** | WS→HTTP only | **yes (chain)** | — | **yes (chain, on by default)** |
| After-first-token break | error | error | **partial + continue** | error | partial + continue |
| OAuth refresh mid-call | — | — | **per-request token provider** | per-call | per-call resolve |
| Rate-limit header parse | — | **yes (x-ratelimit-*)** | yes (Nous) | — | yes (x-ratelimit-* / anthropic-ratelimit-*) |
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
  **no explicit body-read timeout**; relies on fetch + retry. A 120 s httpx
  read cap has no counterpart here, which is why adding one killed healthy
  long streams (see §3).

---

## 3. Timeout policy

The governing principle, taken from openclaw: a reasoning stream must not carry
a tight read timeout. A single httpx `timeout=120` float caps the body read at
120 s and fires before the idle budget over a buffering proxy or VPN, which is
how a healthy long stream gets killed.

Codex therefore uses `Timeout(connect=30, read=1860, write=30, pool=30)`, and
the SSE governor is two budgets plus a backstop:

- `SSE_IDLE_TIMEOUT_S = 1800` (30 min) — "no bytes at all", reset on **any**
  line, pings included. This is openclaw's `bodyTimeout` equivalent.
- `SSE_DATA_STALL_TIMEOUT_S = 900` (15 min) — "no real data", reset only on
  parsed events. This catches ping-flood stalls that a byte-level timeout
  cannot see.
- `SSE_TOTAL_TIMEOUT_S = 7200` (2 h) — a runaway backstop.

All are env-overridable (`OPENPROGRAM_SSE_*`, `OPENPROGRAM_HTTPX_*`).

Backoff caps its exponential component at 30 s
(`OPENPROGRAM_PROVIDER_STREAM_BACKOFF_MAX_S`, in `utils/stream_retry.py`); a
larger server Retry-After is still honored. `utils/errors.py` parses all three
Retry-After forms: `retry-after-ms`, integer seconds, and HTTP-date.

## 4. Transport and recovery

The modules under `providers/utils/` are generic and available to every HTTP
provider; codex is wired to all of them. openai-completions retries a
pre-content `APIError` that `classify_error` marks retryable — same model,
`PROVIDER_STREAM_MAX_ATTEMPTS` and `stream_backoff_seconds` — and re-raises
once any content has streamed.

- **Central timeout policy** (`timeouts.py`) — one source of truth at
  OpenClaw's 30-min level, with context-scaling helpers.
- **Client builder** (`http_client.py`):
  - **TCP keepalive** — `SO_KEEPALIVE` plus idle/interval/count, giving ~60 s
    dead-peer detection for the VPN-drop case. Applied defensively per OS;
    `OPENPROGRAM_TCP_KEEPALIVE=0` disables it.
  - **Force-IPv4** escape hatch (`OPENPROGRAM_FORCE_IPV4=1`) for broken-IPv6
    VPNs, binding an IPv4 source address, since httpx has no Happy Eyeballs.
  - **Connection reuse** — `get_shared_async_client` is loop-keyed, so codex
    reuses its TLS connection across turns instead of re-handshaking.
  - **Proxy** via httpx 0.28 `proxy=`.
- **Rate-limit header parsing** (`rate_limit.py`) — `x-ratelimit-*` and
  `anthropic-ratelimit-*`; codex warns when a bucket is low or exhausted.
- **Partial-response recovery** (`openai_codex.py`) — a transient mid-stream
  break *after* content arrives finalizes the partial turn with
  `stop_reason="length"` rather than erroring, so no work is lost and no blind
  retry follows. Permanent failures (auth, invalid, context, policy) still hard
  fail. Toggle with `OPENPROGRAM_PARTIAL_RECOVERY=0`.
- **Provider/model failover** (`failover.py` + `agent_loop.py`) — a classifier
  (rate_limit / overloaded / server / timeout / network) plus a
  `stream_with_failover` wrapper that tries the primary and then each
  configured fallback on a **pre-content** failover-worthy failure. It forwards
  events, suppresses the duplicate `start`, and never switches after a token has
  streamed. **On by default, conservatively:** with nothing configured the chain
  is the user's other enabled models of the *same provider* (at most 2, in
  config-row order), so a failover reuses the credential the call was already
  going to use and never contacts a provider the user has not configured. Set
  `OPENPROGRAM_FALLBACK_MODELS="provider/model,provider2/model2"` to override
  with an explicit list, which may cross providers; set it to `off` (or `none`)
  to disable failover entirely.
- **openai-completions pre-content retry** (`openai_completions.py`) — after
  `EventStart` and before any content block, a retryable `APIError` (including
  xAI's status-less `Internal error during token generation`) reopens the
  stream on the same model. After content arrives, or when the error is not
  retryable, the existing outer `APIError` handler records credential cooldown
  and re-raises.
- gemini_cli shares the same client, so it carries the same timeout semantics
  rather than its own single-float timeout.

**Deliberately off or not built:**

- **API-key rotation** — the machinery lives in the auth layer (`auth/pool.py`:
  `pick` rotation, `mark_failure` / `record_call_failure` cooldowns with strategies
  and TTLs). Rotation on acquire is automatic once a pool has more than one
  credential. Per-call failure-cooldown reporting is not wired into the live
  single-account path, because cooling down the only credential would lock the
  user out for no benefit. On a single account rotation is a clean no-op and
  activates by itself once multiple credentials exist.
- **OAuth per-request token provider** — codex already resolves and refreshes
  the bearer per call through the auth manager, so a full httpx event-hook
  provider would add machinery without fixing anything.

## 5. Tunables

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
| `OPENPROGRAM_FALLBACK_MODELS` | (empty) = same-provider chain | `provider/model,…` — explicit failover chain, may cross providers; `off`/`none` disables |
