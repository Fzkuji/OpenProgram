# Outbound network proxy

Every httpx client in the process resolves proxies with the same semantics —
httpx's own env semantics — and the hardened clients receive them via `mounts=`
so that hardening and proxying compose. Product-facing documentation lives in
`docs/server/configuration.md`.

## 1. Why one resolver

The failure this prevents is a process with two different proxy semantics
depending on which code path a request happens to take. Three outbound paths
coexist, and if each resolves proxies on its own they disagree:

| Path | Who uses it | Proxy semantics without a shared resolver |
|---|---|---|
| Hardened client (`providers/utils/http_client.py`) | anthropic, openai_codex, google_gemini_cli streaming | Only `HTTPS_PROXY`/`HTTP_PROXY` (uppercase) read by a hand-written resolver. `ALL_PROXY` ignored, **`NO_PROXY` ignored** (no bypass list), lowercase vars ignored — because the client always receives an explicit `transport=`, so httpx skips its own env handling (`allow_env_proxies = trust_env and transport is None`). |
| SDK / ad-hoc raw httpx | OpenAI-compat chat (openai SDK inside `openai_completions` / `openai_responses`), OAuth flows, token refresh, model listing, "test provider" button | Full httpx env semantics: lowercase beats uppercase, `ALL_PROXY` honoured, `NO_PROXY` honoured. |
| CLI subprocess | claude_code, codex CLI, gemini CLI | Inherits the shell env; the external CLI does its own proxy handling. |

The consequences of that divergence are concrete: the same provider behaves
differently between "test provider" (raw httpx), actual chat (SDK), and the
Anthropic path (hardened); a user with a proxy plus a `NO_PROXY` whitelist —
say a mainland-direct API endpoint — has that whitelist silently ignored on the
hardened path; and the connection hardening (TCP keepalive, force-IPv4,
generous streaming timeouts) covers only the hand-written providers while the
openai SDK builds a fresh default client per call.

A socks proxy makes the divergence visible immediately: a shell with
`ALL_PROXY=socks5://127.0.0.1:7891` crashes every API-routed provider with
"Using SOCKS proxy, but the 'socksio' package is not installed" while
CLI-backed providers keep working.

## 2. How OpenClaw does it

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

The direction is the same one taken here — a single resolver with standard env
semantics plus a first-party override. OpenClaw goes further with proxy
lifecycle validation and SSRF gating, which this design does not need.

## 3. Resolution order

1. `OPENPROGRAM_PROXY_URL` — explicit first-party override. When set, all
   traffic goes through it (any scheme httpx supports: `http://`,
   `https://`, `socks5://`). `NO_PROXY` bypasses are still honoured.
2. Standard environment variables, parsed by httpx's own
   `get_environment_proxies()`: `http_proxy`/`HTTP_PROXY`,
   `https_proxy`/`HTTPS_PROXY`, `all_proxy`/`ALL_PROXY`,
   `no_proxy`/`NO_PROXY`. Using httpx's parser rather than a reimplementation
   guarantees the hardened path and every plain `httpx.AsyncClient()` in
   the process agree byte-for-byte — the exact property whose absence causes
   the divergence in §1. Note this delegates to urllib's `getproxies()`, so on
   macOS/Windows the OS-level proxy settings apply when no env vars are
   set — same as any Python process.
3. Built-in loopback bypass: `localhost` / `127.0.0.1` / `[::1]` never go
   through a proxy, `NO_PROXY` or not. Local services (the worker, a
   localhost ollama, a local OpenAI-compatible endpoint) break behind
   forward proxies like Clash, which refuse loopback CONNECTs — a failure
   mode that presents as a spurious 502.

## 4. Mechanics

- `providers/utils/http_proxy.py` exposes `get_proxy_mounts()` returning
  the httpx mount map (`pattern -> proxy URL or None-for-bypass`), with the
  `OPENPROGRAM_PROXY_URL` override folded in. Its single consumer is
  `http_client.py`.
- `providers/utils/http_client.py::build_async_client` builds one
  hardened `AsyncHTTPTransport` per mount entry (same socket options /
  IPv4 / keepalive as the default transport, plus `proxy=`), passes them
  as `mounts=`, and keeps the hardened no-proxy transport as the default.
  `trust_env` stays default-True so TLS env vars (`SSL_CERT_FILE`, …)
  keep working; httpx skips its own env-proxy pass because an explicit
  `transport=` is present.
- The openai SDK paths (`openai_completions.py`, `openai_responses.py`)
  pass `http_client=get_shared_async_client("openai-sdk")` — SDK
  requests get the same proxy semantics AND the keepalive/IPv4 hardening,
  and reuse one connection pool per event loop instead of a fresh client
  per call. The SDK does not close externally-supplied clients; lifecycle
  stays with `aclose_current_loop_clients()`.
- One-shot raw httpx clients (OAuth flows, token refresh, marketplace,
  model listing) are plain `httpx.AsyncClient()`s on purpose:
  their env semantics are already identical by construction, and they
  don't need streaming hardening. `OPENPROGRAM_PROXY_URL` does not apply
  to them, which is an accepted limit rather than an oversight.
- `socksio` is a hard dependency (`httpx[socks]`) so a socks `ALL_PROXY`
  never kills client construction.
- `openprogram rescue` carries a proxy probe: it reports the resolved proxy
  configuration and fails with an exact fix when a socks proxy is
  configured but socksio is missing.
- The test suite is proxy-isolated: `tests/conftest.py` strips the proxy
  env vars and pins urllib's OS-settings fallback to env-only, so a
  developer's Clash/system proxy can't hijack the integration tests'
  localhost requests. Live smoke tests opt back into the real network
  with `OPENPROGRAM_TEST_LIVE=1 pytest -m slow`.

## 5. Deliberately not built

- A `proxy.url` config key / CLI flag (OpenClaw has one) — env vars cover
  today's users; the override env var is the cheap 90%. Add the config key
  when someone needs per-profile proxies.
- Proxy validation / lifecycle management (OpenClaw's managed proxy) and
  `--proxy-ca-file` — TLS-intercepting corporate proxies already work via
  httpx's standard `SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE` handling
  (`trust_env=True`).
- An SSRF guard tied to proxy decisions — OpenProgram is a local tool, not
  a hosted gateway.

## 6. Invariants

1. Any new provider HTTP code MUST get its client from
   `build_async_client` / `get_shared_async_client` — never construct
   `httpx.AsyncClient` with hand-rolled proxy kwargs.
2. Never pass `proxy=` or `proxies=` directly; proxy selection lives in
   `get_proxy_mounts()` only.
3. `get_proxy_mounts()` uses httpx's parser. If httpx ever privatises or
   moves `get_environment_proxies`, mirror its semantics — do not invent
   new ones, since divergent semantics is the problem described in §1.
4. `tests/component/security/test_http_proxy.py` pins the resolution rules (override
   precedence, NO_PROXY bypass, per-URL transport selection).
