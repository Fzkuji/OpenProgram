# Outbound network proxy — survey, comparison, unified design

*2026-07-16. Engineering record for the proxy refactor; product-facing
documentation lives in `docs/server/configuration.md`.*

## 1. The problem that triggered this

A user shell with `ALL_PROXY=socks5://127.0.0.1:7891` crashed every
API-routed provider (e.g. a custom OpenAI-compatible provider) with:

> Using SOCKS proxy, but the 'socksio' package is not installed.

while CLI-backed providers (claude-code, codex) kept working. The immediate
fix was declaring `httpx[socks]` in `pyproject.toml`. The investigation
exposed a deeper problem: **the process had two different proxy semantics
depending on which code path a request happened to take.**

## 2. State before the refactor

Three outbound paths coexisted:

| Path | Who used it | Proxy semantics |
|---|---|---|
| Hardened client (`providers/utils/http_client.py`) | anthropic, openai_codex, google_gemini_cli streaming | Only `HTTPS_PROXY`/`HTTP_PROXY` (uppercase), read by our own `get_proxy_url()`. `ALL_PROXY` ignored, **`NO_PROXY` ignored** (no bypass list), lowercase vars ignored. Because the client always received an explicit `transport=`, httpx 0.28 skipped its own env handling (`allow_env_proxies = trust_env and transport is None`). |
| SDK / ad-hoc raw httpx | OpenAI-compat chat (openai SDK inside `openai_completions` / `openai_responses`), OAuth flows, token refresh, model listing, "test provider" button | Full httpx env semantics: lowercase beats uppercase, `ALL_PROXY` honoured (hence the socks crash), `NO_PROXY` honoured. |
| CLI subprocess | claude_code, codex CLI, gemini CLI | Inherits the shell env; the external CLI does its own proxy handling. |

Consequences:

- The same provider could behave differently between "test provider"
  (raw httpx) and actual chat (SDK) and the Anthropic path (hardened).
- A user with a proxy plus a `NO_PROXY` whitelist (e.g. a mainland-direct
  API endpoint) was silently betrayed on the hardened path — everything
  went through the proxy.
- The connection hardening (TCP keepalive, force-IPv4, generous streaming
  timeouts) only covered the hand-written providers; the openai SDK built
  a fresh default client per call.

## 3. How OpenClaw does it (comparison)

Source: `references/openclaw/src/infra/net/proxy-env.ts`, `proxy-fetch.ts`,
`src/infra/net/proxy/`, and https://docs.openclaw.ai/cli/proxy/.

- **One canonical env resolver** (`proxy-env.ts`) that deliberately mirrors
  undici `EnvHttpProxyAgent` semantics: lowercase vars take precedence over
  uppercase; HTTPS requests prefer `https_proxy` then fall back to
  `http_proxy`; `ALL_PROXY` is a fallback fed in explicitly. A full
  `NO_PROXY` matcher (comma/whitespace split, case-insensitive, `*`,
  leading-dot, `*.`, subdomain suffix, optional `:port`, bracketed IPv6,
  plus their own IPv4-CIDR extension) gates every proxy decision — it is
  a reimplementation kept in sync with undici because undici doesn't
  export its matcher.
- **One explicit override**: `--proxy-url` flag / `proxy.proxyUrl` config /
  `OPENCLAW_PROXY_URL` env, with optional `--proxy-ca-file`, implemented as
  a `makeProxyFetch(proxyUrl)` wrapper over undici `ProxyAgent`, plus a
  managed-proxy lifecycle (validation, TLS options, active-state tracking).
- Provider HTTP helpers all route through these helpers; the SSRF guard
  (`fetch-guard.ts`) composes with the same `matchesNoProxy`.

So: same direction we need — a single resolver with standard env semantics
plus a first-party override knob. OpenClaw goes further with proxy
lifecycle/validation and SSRF gating; we don't need those yet.

## 4. Unified design (implemented in this refactor)

**One rule: every httpx client in the process resolves proxies with the
same semantics — httpx's own env semantics — and the hardened clients get
them via `mounts=` so hardening and proxying compose.**

### 4.1 Resolution order

1. `OPENPROGRAM_PROXY_URL` — explicit first-party override. When set, all
   traffic goes through it (any scheme httpx supports: `http://`,
   `https://`, `socks5://`). `NO_PROXY` bypasses are still honoured.
2. Standard environment variables, parsed by httpx's own
   `get_environment_proxies()`: `http_proxy`/`HTTP_PROXY`,
   `https_proxy`/`HTTPS_PROXY`, `all_proxy`/`ALL_PROXY`,
   `no_proxy`/`NO_PROXY`. Using httpx's parser (not a reimplementation)
   guarantees the hardened path and every plain `httpx.AsyncClient()` in
   the process agree byte-for-byte — the exact property whose absence
   caused §1. Note this delegates to urllib's `getproxies()`, so on
   macOS/Windows the OS-level proxy settings apply when no env vars are
   set — same as any Python process.
3. Built-in loopback bypass: `localhost` / `127.0.0.1` / `[::1]` never go
   through a proxy, `NO_PROXY` or not. Local services (the worker, a
   localhost ollama, a local OpenAI-compatible endpoint) break behind
   forward proxies like Clash, which refuse loopback CONNECTs — that
   failure mode showed up as fake 502s during debugging.

### 4.2 Mechanics

- `providers/utils/http_proxy.py` exposes `get_proxy_mounts()` returning
  the httpx mount map (`pattern -> proxy URL or None-for-bypass`), with the
  `OPENPROGRAM_PROXY_URL` override folded in. The legacy
  `get_proxy_url()`/`get_proxies()`/`make_httpx_client()` are gone; the
  single consumer is `http_client.py`.
- `providers/utils/http_client.py::build_async_client` now builds one
  hardened `AsyncHTTPTransport` per mount entry (same socket options /
  IPv4 / keepalive as the default transport, plus `proxy=`), passes them
  as `mounts=`, and keeps the hardened no-proxy transport as the default.
  `trust_env` stays default-True so TLS env vars (`SSL_CERT_FILE`, …)
  keep working; httpx skips its own env-proxy pass because an explicit
  `transport=` is present.
- The openai SDK paths (`openai_completions.py`, `openai_responses.py`)
  now pass `http_client=get_shared_async_client("openai-sdk")` — SDK
  requests get the same proxy semantics AND the keepalive/IPv4 hardening,
  and reuse one connection pool per event loop instead of a fresh client
  per call. The SDK does not close externally-supplied clients; lifecycle
  stays with `aclose_current_loop_clients()`.
- One-shot raw httpx clients (OAuth flows, token refresh, marketplace,
  model listing) are left as plain `httpx.AsyncClient()`s on purpose:
  their env semantics are already identical by construction, and they
  don't need streaming hardening. `OPENPROGRAM_PROXY_URL` does not apply
  to them — acceptable for v1; noted as a future unification.
- `socksio` is a hard dependency (`httpx[socks]`) so a socks `ALL_PROXY`
  never again kills client construction.
- `openprogram rescue` gained a proxy probe: reports the resolved proxy
  configuration and fails with an exact fix when a socks proxy is
  configured but socksio is missing (belt-and-braces for installs that
  predate the dependency change).
- The test suite is proxy-isolated: `tests/conftest.py` strips the proxy
  env vars and pins urllib's OS-settings fallback to env-only, so a
  developer's Clash/system proxy can't hijack the integration tests'
  localhost requests. Live smoke tests opt back into the real network
  with `OPENPROGRAM_TEST_LIVE=1 pytest -m slow`.

### 4.3 What we deliberately did NOT build

- A `proxy.url` config key / CLI flag (OpenClaw has one) — env vars cover
  today's users; the override env var is the cheap 90%. Add the config key
  when someone needs per-profile proxies.
- Proxy validation / lifecycle management (OpenClaw's managed proxy) and
  `--proxy-ca-file` — TLS-intercepting corporate proxies already work via
  httpx's standard `SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE` handling
  (`trust_env=True`).
- An SSRF guard tied to proxy decisions — OpenProgram is a local tool, not
  a hosted gateway.

## 5. Invariants to keep

1. Any new provider HTTP code MUST get its client from
   `build_async_client` / `get_shared_async_client` — never construct
   `httpx.AsyncClient` with hand-rolled proxy kwargs.
2. Never pass `proxy=` or `proxies=` directly; proxy selection lives in
   `get_proxy_mounts()` only.
3. `get_proxy_mounts()` uses httpx's parser. If httpx ever privatises or
   moves `get_environment_proxies`, mirror its semantics — do not invent
   new ones (that is how §2 happened).
4. Tests: `tests/test_http_proxy.py` pins the resolution rules (override
   precedence, NO_PROXY bypass, per-URL transport selection).
