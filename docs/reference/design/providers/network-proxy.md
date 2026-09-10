# Outbound network proxy

Provider HTTP traffic has two separate concerns: httpx environment-proxy
diagnostics, and the managed client's outbound security policy. Managed
clients are built through `openprogram.security.safe_http`; they do not consume
the mount map from `http_proxy.py`. An enforcing proxy is part of
`OutboundSecurityConfig.policy_proxy`, so URL policy and proxy routing are
evaluated together. Product-facing documentation lives in
`docs/server/configuration.md`.

## 1. Why one resolver

The implementation keeps the following outbound paths distinct:

| Path | Who uses it | Proxy semantics without a shared resolver |
|---|---|---|
| Managed client (`providers/utils/http_client.py`) | provider SDK and streaming paths | Uses `safe_http` with a consumer-specific URL policy. A policy proxy is explicit in `OutboundSecurityConfig.policy_proxy`; the client does not activate `get_proxy_mounts()` or unmanaged proxy environment state. |
| SDK / ad-hoc raw httpx | OpenAI-compat chat (openai SDK inside `openai_completions` / `openai_responses`), OAuth flows, token refresh, "test provider" button | Full httpx env semantics: lowercase beats uppercase, `ALL_PROXY` honoured, `NO_PROXY` honoured. |
| CLI subprocess | claude_code, codex CLI, gemini CLI | Inherits the shell env; the external CLI does its own proxy handling. |

The managed path deliberately has stronger URL and credential policy than a
plain httpx client. A provider request therefore must not silently inherit an
unmanaged process proxy: callers either use the managed security configuration
or use a deliberately scoped plain client whose httpx environment semantics are
documented at that call site.

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

OpenProgram borrows the useful diagnostic property that standard environment
variables have one documented interpretation, while its managed provider path
adds a separate policy-proxy boundary. OpenClaw goes further with proxy
lifecycle validation and SSRF gating, which this design does not need.

## 3. Resolution order

1. `OPENPROGRAM_PROXY_URL` — explicit first-party override used by the
   diagnostic helper and callers that deliberately use the environment proxy
   map. It is not an implicit override for managed clients.
2. Standard environment variables, parsed by httpx's own
   `get_environment_proxies()`: `http_proxy`/`HTTP_PROXY`,
   `https_proxy`/`HTTPS_PROXY`, `all_proxy`/`ALL_PROXY`,
   `no_proxy`/`NO_PROXY`. Using httpx's parser rather than a reimplementation
   gives diagnostic callers the same parsing behavior as httpx. Note this
   delegates to urllib's `getproxies()`, so on
   macOS/Windows the OS-level proxy settings apply when no env vars are
   set — same as any Python process.
3. Managed provider clients do not use this environment resolution as an
   implicit security-policy override. If a managed request must use a proxy,
   declare it in `OutboundSecurityConfig.policy_proxy`; the policy layer then
   validates the proxy and target relationship before opening the connection.

## 4. Mechanics

- `providers/utils/http_proxy.py` exposes `get_proxy_mounts()` as a diagnostic
  helper for the environment proxy map and the `OPENPROGRAM_PROXY_URL`
  override. Managed provider clients do not consume this map; the rescue
  command uses it to report configured proxy state.
- `providers/utils/http_client.py::build_async_client` delegates to
  `safe_async_client` or `configured_safe_async_client`. The managed client
  applies the registered consumer's URL policy, bounded timeouts, and socket
  hardening. When a policy proxy is configured, `safe_http` creates the
  decision-aware proxy transport and strips proxy credentials from requests
  before dispatch.
- The OpenAI, Anthropic, and Google SDK consumers are registered in
  `openprogram/security/safe_http.py` with an injected-transport disposition.
  Their shared-client lifecycle remains explicit; it is not implemented by
  passing `mounts=` from `http_proxy.py`.
- One-shot raw httpx clients (OAuth flows, token refresh, marketplace) are
  plain `httpx.AsyncClient()`s on purpose:
  their env semantics are already identical by construction, and they
  don't need streaming hardening. `OPENPROGRAM_PROXY_URL` does not apply
  to them, which is an accepted limit rather than an oversight.
- Subscription model listing uses `safe_client("provider.fixed_api")`. It
  therefore follows `OutboundSecurityConfig.policy_proxy` and deliberately
  ignores ambient environment and OS proxy settings.
- A plain httpx caller that opts into SOCKS environment handling must provide
  the corresponding `httpx[socks]` dependency; managed clients do not infer
  that dependency from an ambient `ALL_PROXY`.
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
   `build_async_client` / `get_shared_async_client` and a registered
   `safe_http` consumer; never construct a managed client with hand-rolled
   proxy kwargs.
2. Never use `get_proxy_mounts()` as a substitute for
   `OutboundSecurityConfig.policy_proxy` on a managed request.
3. `get_proxy_mounts()` remains an environment-resolution diagnostic. If
   httpx moves `get_environment_proxies`, keep this helper's diagnostic output
   aligned with httpx rather than treating it as the managed transport
   contract.
4. `tests/component/security/test_http_proxy.py` pins both the diagnostic
   resolution rules and the fact that unmanaged environment proxies do not
   activate a managed provider client.
