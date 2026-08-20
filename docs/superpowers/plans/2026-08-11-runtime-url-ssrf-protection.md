# Runtime URL SSRF Protection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Route every Runtime-owned HTTP fetch through one registry-keyed policy and transport that prevents DNS rebinding, private-network access, unsafe redirects, credential leakage, and unbounded responses while preserving explicitly configured local services.

**Architecture:** `url_policy.py` is a pure normalization and address-policy module. `safe_http.py` owns the immutable consumer registry, sync/async `httpx` transports backed by peer-constrained `httpcore` network backends, manual redirect handling, credential and resource limits, and SDK client factories. Call sites supply only a registry key plus the configured-service or callback origin required by that registry entry. A static inventory checker and `doctor` consume the same registry, so an unregistered Runtime network call or an active unmanaged SDK transport fails verification.

**Tech Stack:** Python 3.11, `ipaddress`, `socket`, `ssl`, `httpx` 0.28, `httpcore` 1.0, Pydantic/config schema already in the repository, pytest, Ruff, MkDocs documentation checks.

## Global Constraints

- `docs/reference/design/runtime/ssrf-protection.html` is the sole normative specification. Browser navigation, CDP/Playwright/Electron browser control, sandboxed arbitrary-code networking, external provider CLI processes, and package-manager child processes remain separately documented boundaries.
- Production code changes follow test-first development. Each numbered implementation task begins with the stated failing test command and records its failure in `.superpowers/sdd/runtime-url-ssrf-protection/red-green.log` before production edits.
- Callers pass a consumer registry key, never a policy object. Address classification exists only in `openprogram/security/url_policy.py`; network I/O exists only in `openprogram/security/safe_http.py` or an explicitly excluded boundary listed by the static inventory.
- Public consumers default to `trust_env=False`. A policy proxy is used only when owner configuration declares that the proxy enforces target policy; `doctor` reports that enforcement is delegated.
- No TLS verification disabling, implicit redirects, fallback raw HTTP call, or uncontrolled second DNS lookup is permitted.
- An implementation task is complete only after its focused tests pass and its task report records the exact RED and GREEN commands and results. Each task receives spec-compliance review before code-quality review.
- Fixed default limits are: 5 redirects; 10-second connect; 30-second read/idle and write; 5-second pool wait; 120-second overall request; 100 response headers; 65,536 encoded header bytes; and consumer-specific decoded-body caps declared in the registry. Supported response content encodings are identity, gzip, and deflate.
- Owner exceptions are exact consumer plus normalized origin or consumer plus canonical CIDR. Link-local metadata destinations remain blocked even when an exception exists.

---

### Task 1: Pure URL policy and immutable consumer registry

**Files:**
- Create: `openprogram/security/__init__.py`
- Create: `openprogram/security/url_policy.py`
- Create: `openprogram/security/safe_http.py`
- Create: `tests/security/test_url_policy.py`
- Create: `tests/security/test_consumer_registry.py`

**Step 1: Write the normalization and address-policy tests**

Add parameterized tests that call `evaluate_url()` with an injected resolver and assert stable reason codes:

```python
@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://127.0.0.1/", "NON_GLOBAL_ADDRESS"),
        ("http://[::1]/", "NON_GLOBAL_ADDRESS"),
        ("http://[::ffff:127.0.0.1]/", "NON_GLOBAL_ADDRESS"),
        ("http://169.254.169.254/latest/meta-data/", "METADATA_ADDRESS"),
        ("http://2130706433/", "AMBIGUOUS_HOST"),
        ("http://017700000001/", "AMBIGUOUS_HOST"),
        ("http://0x7f000001/", "AMBIGUOUS_HOST"),
        ("http://user:pass@example.com/", "USERINFO_FORBIDDEN"),
        ("http://example.com:22/", "PORT_FORBIDDEN"),
        ("http://example.com\\@127.0.0.1/", "INVALID_URL"),
        ("http://example.com/%0aHost:x", "CONTROL_CHARACTER"),
    ],
)
def test_untrusted_public_rejects_unsafe_url(url, reason, public_resolver):
    with pytest.raises(URLPolicyError) as exc:
        evaluate_url("tool.web_fetch", "GET", url, resolver=public_resolver)
    assert exc.value.reason == reason
```

Cover uppercase/trailing-dot/IDNA normalization, IPv6 zone identifiers, invalid and overflowing ports, unsupported schemes, non-GET/HEAD public methods, every non-global `ipaddress` category, NXDOMAIN/timeout/empty results, mixed public/private results, IPv4-mapped IPv6, and duplicate resolver answers. Assert that error text and `safe_url` omit userinfo, query values, and fragments.

Add configured-service and loopback tests proving exact scheme/host/port matching, allowed local/private endpoints, exact callback IP/port, and metadata denial despite owner exception.

**Step 2: Run the RED tests**

Run:

```bash
uv run pytest -q tests/security/test_url_policy.py tests/security/test_consumer_registry.py
```

Expected: collection fails because `openprogram.security.url_policy` and registry symbols do not exist.

**Step 3: Implement the pure policy API**

Implement the following public types in `url_policy.py`:

```python
class URLTrustClass(str, Enum):
    UNTRUSTED_PUBLIC = "untrusted_public"
    FIXED_PUBLIC_SERVICE = "fixed_public_service"
    CONFIGURED_SERVICE = "configured_service"
    LOOPBACK_CALLBACK = "loopback_callback"

@dataclass(frozen=True)
class OwnerURLException:
    consumer: str
    origin: str | None = None
    network: ipaddress.IPv4Network | ipaddress.IPv6Network | None = None

@dataclass(frozen=True)
class URLDecision:
    consumer: str
    method: str
    normalized_url: str
    origin: str
    hostname: str
    port: int
    resolved_ips: tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]
    trust_class: URLTrustClass

class URLPolicyError(ValueError):
    def __init__(self, reason: str, safe_url: str):
        self.reason = reason
        self.safe_url = safe_url
        super().__init__(f"{reason}: {safe_url}")

```

The module also exports `normalize_url(url: str) -> NormalizedURL`, `normalize_origin(url: str) -> str`, and `evaluate_url(consumer: str, method: str, url: str, *, trust_class: URLTrustClass, allowed_methods: frozenset[str], allowed_ports: frozenset[int] | None, configured_origin: str | None = None, callback_origin: str | None = None, exceptions: tuple[OwnerURLException, ...] = (), resolver: Resolver = resolve_all) -> URLDecision`.

Use `urllib.parse.urlsplit` only after rejecting control characters and backslashes. Canonicalize IDNA hostnames, lowercase them, remove one terminal dot, normalize default ports, reject ambiguous integer/octal/hex hosts before DNS, call `socket.getaddrinfo` once, canonicalize and deduplicate every A/AAAA result, and fail closed when any result violates policy. Treat CGNAT, documentation/test networks, reserved, multicast, link-local, loopback, private, unspecified, and IPv4-mapped forms as non-global. Preserve the normalized hostname in the decision separately from the approved IP tuple.

**Step 4: Define the complete initial registry**

In `safe_http.py`, add frozen `ConsumerSpec`, `SDKDisposition`, and a `MappingProxyType` registry. Every entry declares trust class, allowed methods/ports, redirect policy, max redirects, decoded body cap, accepted MIME prefixes, credential origin policy, exception capability, and SDK disposition. Include registry keys for:

```text
tool.web_fetch
tool.web_search.fixed_api
tool.web_search.configured_api
tool.image_api.fixed
tool.image_api.configured
tool.image_result.download
channel.attachment.download
channel.telegram.api
channel.discord.api
channel.slack.api
channel.wechat.api
channel.feishu.api
channel.matrix.configured
channel.generated_asset.download
skills.github.catalog
skills.configured.catalog
plugins.marketplace
plugins.autoupdate
updater.github
updater.pip
provider.fixed_api
provider.configured_api
provider.oauth.fixed
provider.google.sdk
provider.openai.sdk
provider.anthropic.sdk
mcp.configured.http
mcp.configured.sse
mcp.loopback.callback
tts.fixed_api
tts.configured_api
webui.mcp.catalog
webui.model_listing.fixed
webui.model_listing.configured
runtime.local_probe
```

Registry tests assert unique keys, positive finite limits, allowed-method/credential consistency, owner-exception restrictions, one of the four SDK dispositions for SDK entries, and no `UNMANAGED` disposition.

**Step 5: Run GREEN and quality checks**

Run:

```bash
uv run pytest -q tests/security/test_url_policy.py tests/security/test_consumer_registry.py
uv run ruff check openprogram/security tests/security/test_url_policy.py tests/security/test_consumer_registry.py
```

Expected: both commands exit 0.

**Step 6: Commit**

```bash
git add openprogram/security tests/security
git commit -m "feat(security): define runtime URL policy"
```

### Task 2: Peer-constrained sync and async transports

**Files:**
- Modify: `openprogram/security/safe_http.py`
- Create: `tests/security/test_safe_http_transport.py`
- Create: `tests/security/test_safe_http_tls.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Step 1: Add real-socket transport tests**

Add local HTTP and TLS fixtures. Generate a test CA and a server certificate with SAN `safe.test` using the dev-only `cryptography` dependency. Inject a resolver returning `127.0.0.1` under a loopback callback or exact owner-exception scope, and record the accepted socket peer, HTTP Host header, and TLS SNI.

Tests must prove:

```python
decision = client.request("mcp.loopback.callback", "GET", url, callback_origin=url)
assert server.peer_address == "127.0.0.1"
assert server.host_header == f"safe.test:{server.port}"
assert server.sni_name == "safe.test"
```

Add sync and async cases in which the resolver returns an approved address on the first call and private address on a second call; assert the resolver is called exactly once and the accepted peer is in `URLDecision.resolved_ips`. Add a backend that reports a peer outside the decision and assert `PEER_ADDRESS_MISMATCH`. Add retry tests proving retries use only the same approved IP tuple, and a fresh request creates a fresh decision.

Add certificate tests proving the original hostname is verified and `verify=False` is not exposed. Add pool tests proving connections cannot be reused across consumer, scope, normalized origin, or policy-proxy identity.

**Step 2: Run RED**

```bash
uv run pytest -q tests/security/test_safe_http_transport.py tests/security/test_safe_http_tls.py
```

Expected: tests fail because managed transports and client factories are absent.

**Step 3: Implement the constrained network backends**

Add `DecisionNetworkBackend` and `AsyncDecisionNetworkBackend` implementing the `httpcore` backend protocols. `connect_tcp()` receives the original hostname from `httpcore` but calls the underlying backend with only an approved IP literal. Wrap each returned stream so `get_extra_info("server_addr")` is checked against the approved tuple before use. Do not resolve in the backend. Leave `start_tls(server_hostname=...)` unchanged so SNI and certificate verification use the original normalized hostname.

Implement `ManagedHTTPTransport(httpx.BaseTransport)` and `AsyncManagedHTTPTransport(httpx.AsyncBaseTransport)` with a separate `httpcore.ConnectionPool` per decision key:

```python
PoolKey = tuple[str, str, str, tuple[str, ...], str | None]
# consumer, trust class, normalized origin, approved IPs, policy-proxy identity
```

Construct SSL contexts with `httpx.create_ssl_context(verify=...)`. Never disable verification. Close all decision pools on client close.

**Step 4: Add registry-keyed client factories**

Implement `safe_client(consumer: str, *, configured_origin: str | None = None, callback_origin: str | None = None, security: OutboundSecurityConfig | None = None) -> SafeClient` and `safe_async_client(consumer: str, *, configured_origin: str | None = None, callback_origin: str | None = None, security: OutboundSecurityConfig | None = None) -> SafeAsyncClient`.

Each request looks up the immutable registry entry, obtains one `URLDecision`, and passes that same decision to its pool/backend. The public API rejects an unknown registry key.

**Step 5: Add the TLS test dependency and run GREEN**

Add `cryptography>=42` to the development dependency group and regenerate the lock with:

```bash
uv lock
uv run pytest -q tests/security/test_safe_http_transport.py tests/security/test_safe_http_tls.py
uv run ruff check openprogram/security tests/security/test_safe_http_transport.py tests/security/test_safe_http_tls.py
```

Expected: commands exit 0.

**Step 6: Commit**

```bash
git add openprogram/security tests/security pyproject.toml uv.lock
git commit -m "feat(security): constrain runtime HTTP peers"
```

### Task 3: Redirect, credential, response, decompression, and policy-proxy enforcement

**Files:**
- Modify: `openprogram/security/safe_http.py`
- Create: `tests/security/test_safe_http_redirects.py`
- Create: `tests/security/test_safe_http_limits.py`
- Create: `tests/security/test_safe_http_proxy.py`

**Step 1: Add redirect and credential tests**

Use a scripted transport plus real local servers to cover relative and absolute redirects, HTTP-to-HTTPS, HTTPS-to-HTTP denial, cross-origin redirects, a redirect loop, and redirect count 6. Assert every `Location` is normalized and evaluated before the next request is sent. A redirect to private or metadata space must produce no connection attempt.

Parameterize `Authorization`, `Proxy-Authorization`, `Cookie`, explicit cookie jar, API-key headers declared by a consumer, and URL userinfo. Assert configured-service credentials are sent only to the exact initial origin, and cross-origin redirects either fail or remove all credentials according to the registry entry. Assert safe audit events contain no token, query value, cookie, or URL userinfo.

**Step 2: Add resource-limit tests**

Test response header count 101, encoded header size 65,537, absent and false `Content-Length`, decoded gzip/deflate expansion beyond the consumer cap, unknown `Content-Encoding: br`, chunked streaming beyond the cap, connect/read/write/pool/overall timeout mapping, and interruption during download. Assert partial temporary files are removed and the destination is atomically replaced only after full validation.

**Step 3: Add policy-proxy tests**

Test that environment proxy variables are ignored, a configured proxy without `enforces_target_policy: true` is rejected, declared delegation is visible in audit state, target policy is evaluated before CONNECT or absolute-form proxy requests, and proxy connections use a separately validated configured-service decision. Assert no direct fallback occurs after proxy failure.

**Step 4: Run RED**

```bash
uv run pytest -q tests/security/test_safe_http_redirects.py tests/security/test_safe_http_limits.py tests/security/test_safe_http_proxy.py
```

Expected: tests fail because manual redirect, bounded response/download, and policy-proxy behavior are absent.

**Step 5: Implement request orchestration and limits**

Implement manual redirect loops in `SafeClient.request()` and `SafeAsyncClient.request()` with `follow_redirects=False`. Resolve a new decision for every hop. Preserve only RFC-safe method/body behavior and reject a replay of non-rewindable request bodies. Enforce exact-origin credential policy before each send.

Implement header validation before body consumption. Send `Accept-Encoding: gzip, deflate`, reject any other content encoding, count decoded bytes from `httpx.Response.iter_bytes()`/`aiter_bytes()`, enforce MIME prefixes after removing parameters, and expose bounded `content`, `text`, `json()`, and streaming-to-temporary-file APIs. Use `os.replace()` only after a complete validated download and `fsync()` of the temporary file.

Implement `PolicyProxyConfig(url, enforces_target_policy)` and construct `httpcore.HTTPProxy`/`AsyncHTTPProxy` with a backend constrained to the proxy decision. Record sanitized structured audit events with consumer, reason, safe origin, delegation state, and timestamp.

**Step 6: Run GREEN**

```bash
uv run pytest -q tests/security/test_safe_http_redirects.py tests/security/test_safe_http_limits.py tests/security/test_safe_http_proxy.py
uv run ruff check openprogram/security tests/security/test_safe_http_redirects.py tests/security/test_safe_http_limits.py tests/security/test_safe_http_proxy.py
```

Expected: commands exit 0.

**Step 7: Commit**

```bash
git add openprogram/security tests/security
git commit -m "feat(security): bound managed HTTP requests"
```

### Task 4: Migrate arbitrary and derived URL consumers

**Files:**
- Modify: `openprogram/programs/functions/vanilla/web/web_fetch/web_fetch.py`
- Modify: `openprogram/channels/_attachments.py`
- Modify: `openprogram/channels/_transport.py`
- Modify: `openprogram/programs/functions/vanilla/web/image_generate/image_generate.py`
- Modify: `openprogram/programs/functions/vanilla/web/image_analyze/providers/gemini.py`
- Create: `tests/security/test_web_fetch_consumer.py`
- Create: `tests/security/test_runtime_derived_url_consumers.py`

**Step 1: Add end-to-end consumer RED tests**

Invoke public consumer entry points, not `safe_http` helpers. Cover `web_fetch`, channel attachment download, channel generated-asset download/upload URL, image-generation result download, and Gemini image URL ingestion. For each, use the local socket fixtures and assert a public URL succeeds under an injected public resolver while a redirect or DNS answer to loopback/private/metadata is denied before the server receives a request. Assert existing result shapes and error messages remain compatible.

Add `web_fetch` regressions for charset detection, binary MIME rejection, truncated content reporting, and the existing 5 MiB result cap under decoded-byte accounting.

**Step 2: Run RED**

```bash
uv run pytest -q tests/security/test_web_fetch_consumer.py tests/security/test_runtime_derived_url_consumers.py
```

Expected: the new SSRF cases fail because the consumers use `urllib` or `requests` directly.

**Step 3: Migrate consumers**

Replace raw calls with the following registry keys:

```text
web_fetch                              -> tool.web_fetch
channel inbound attachment            -> channel.attachment.download
channel upload/generated asset URL    -> channel.generated_asset.download
image generation result URL           -> tool.image_result.download
Gemini image input URL                 -> tool.image_result.download
```

Remove each local redirect, byte-limit, and URL-scheme implementation after the managed client supplies the same or stricter behavior. Preserve consumer-specific MIME validation and user-facing result fields. Never retry with the old network path.

**Step 4: Run GREEN and affected tests**

```bash
uv run pytest -q tests/security/test_web_fetch_consumer.py tests/security/test_runtime_derived_url_consumers.py tests/unit/test_channels_attachments.py tests/unit/test_channels_transport_retry.py tests/unit/test_channels_telegram_semantics.py
uv run ruff check openprogram/programs/functions/vanilla/web/web_fetch openprogram/channels openprogram/programs/functions/vanilla/web/image_generate openprogram/programs/functions/vanilla/web/image_analyze tests/security/test_runtime_derived_url_consumers.py
```

Expected: commands exit 0.

**Step 5: Commit**

```bash
git add openprogram/programs/functions/vanilla/web/web_fetch openprogram/channels openprogram/programs/functions/vanilla/web/image_generate openprogram/programs/functions/vanilla/web/image_analyze tests
git commit -m "refactor(security): protect derived URL fetches"
```

### Task 5: Migrate fixed API, configured API, catalog, and updater consumers

**Files:**
- Modify: `openprogram/programs/functions/vanilla/web/web_search/_http.py`
- Modify: `openprogram/programs/functions/vanilla/web/web_search/providers/brave.py`
- Modify: `openprogram/programs/functions/vanilla/web/web_search/providers/exa.py`
- Modify: `openprogram/programs/functions/vanilla/web/web_search/providers/firecrawl.py`
- Modify: `openprogram/programs/functions/vanilla/web/web_search/providers/google.py`
- Modify: `openprogram/programs/functions/vanilla/web/web_search/providers/minimax.py`
- Modify: `openprogram/programs/functions/vanilla/web/web_search/providers/moonshot.py`
- Modify: `openprogram/programs/functions/vanilla/web/web_search/providers/ollama.py`
- Modify: `openprogram/programs/functions/vanilla/web/web_search/providers/perplexity.py`
- Modify: `openprogram/programs/functions/vanilla/web/web_search/providers/searxng.py`
- Modify: `openprogram/programs/functions/vanilla/web/web_search/providers/tavily.py`
- Modify: `openprogram/programs/functions/vanilla/web/image_generate/providers/fal.py`
- Modify: `openprogram/programs/functions/vanilla/web/image_generate/providers/gemini.py`
- Modify: `openprogram/programs/functions/vanilla/web/image_generate/providers/openai.py`
- Modify: `openprogram/programs/functions/vanilla/web/image_analyze/providers/anthropic.py`
- Modify: `openprogram/programs/functions/vanilla/web/image_analyze/providers/gemini.py`
- Modify: `openprogram/programs/functions/vanilla/web/image_analyze/providers/openai.py`
- Modify: `openprogram/skills/discovery.py`
- Modify: `openprogram/plugins/marketplace.py`
- Modify: `openprogram/plugins/autoupdate.py`
- Modify: `openprogram/updater/github.py`
- Modify: `openprogram/updater/pip.py`
- Modify: `openprogram/webui/routes/mcp.py`
- Modify: `openprogram/webui/_model_listing/credentials.py`
- Modify: `openprogram/webui/_model_listing/fetchers/openai_compat.py`
- Modify: `openprogram/webui/_model_listing/test_provider.py`
- Create: `tests/security/test_runtime_catalog_consumers.py`
- Create: `tests/security/test_runtime_api_consumers.py`

**Step 1: Add full-chain RED tests**

Call every provider adapter through its normal function with fake credentials and local public-test fixtures. Assert fixed endpoints accept only their shipped canonical origins; configured endpoints accept the owner-configured canonical origin including local/private addresses; API credentials never survive a redirect; and catalog/archive responses respect MIME and decoded-size limits.

Cover skills GitHub index and ZIP, configured skills index, plugin marketplace, plugin autoupdate metadata/archive, GitHub updater metadata/archive, pip metadata/archive URL, Web MCP catalogs/templates, web search providers, image provider calls, and model-listing credentials/fetchers.

**Step 2: Run RED**

```bash
uv run pytest -q tests/security/test_runtime_catalog_consumers.py tests/security/test_runtime_api_consumers.py
```

Expected: SSRF, redirect, or credential-origin assertions fail at existing raw `urllib`, `requests`, or `httpx` seams.

**Step 3: Migrate fixed and configured consumers**

Use `tool.web_search.fixed_api` for shipped HTTPS APIs and `tool.web_search.configured_api` for SearXNG/Ollama/custom endpoints. Use corresponding image keys for image APIs. Use catalog/update registry keys for discovery, marketplace, autoupdate, GitHub updater, and pip updater. Use `webui.mcp.catalog`, `webui.model_listing.fixed`, and `webui.model_listing.configured` for Web-owned Runtime fetches.

At each configured-service constructor or request boundary, normalize and freeze the configured origin before attaching credentials. Default cross-origin redirect behavior is rejection. ZIP/tar/package installation remains outside this URL-fetch policy after the validated bytes are handed to its existing parser/installer.

**Step 4: Run GREEN and affected tests**

```bash
uv run pytest -q tests/security/test_runtime_catalog_consumers.py tests/security/test_runtime_api_consumers.py tests/unit/test_skills_cli.py tests/unit/test_skills_registry.py tests/unit/test_web_config_schema.py tests/providers/test_models_dev_cache.py
uv run ruff check openprogram/programs/functions/vanilla/web/web_search openprogram/programs/functions/vanilla/web/image_generate/providers openprogram/programs/functions/vanilla/web/image_analyze/providers openprogram/skills openprogram/plugins openprogram/updater openprogram/webui/routes/mcp.py openprogram/webui/_model_listing tests/security
```

Expected: commands exit 0.

**Step 5: Commit**

```bash
git add openprogram/programs openprogram/skills openprogram/plugins openprogram/updater openprogram/webui tests
git commit -m "refactor(security): protect runtime API and catalog fetches"
```

### Task 6: Migrate channels, TTS, provider HTTP, OAuth, and MCP SDK transports

**Files:**
- Modify: `openprogram/channels/implementations/telegram.py`
- Modify: `openprogram/channels/implementations/wechat.py`
- Modify: `openprogram/channels/implementations/discord.py`
- Modify: `openprogram/channels/implementations/slack.py`
- Modify: `openprogram/tts.py`
- Modify: `openprogram/_ports.py`
- Modify: `openprogram/providers/utils/http_client.py`
- Modify: `openprogram/providers/utils/http_proxy.py`
- Modify: `openprogram/providers/_shared/anthropic_token_count.py`
- Modify: `openprogram/providers/anthropic/auth_adapter.py`
- Modify: `openprogram/providers/anthropic/list_models.py`
- Modify: `openprogram/providers/anthropic/probe_thinking.py`
- Modify: `openprogram/providers/deepseek/list_models.py`
- Modify: `openprogram/providers/deepseek/probe_thinking.py`
- Modify: `openprogram/providers/github_copilot/list_models.py`
- Modify: `openprogram/providers/github_copilot/token_cache.py`
- Modify: `openprogram/providers/google/list_models.py`
- Modify: `openprogram/providers/google/probe_thinking.py`
- Modify: `openprogram/providers/google_gemini_cli/google_gemini_cli.py`
- Modify: `openprogram/providers/openai_codex/auth_adapter.py`
- Modify: `openprogram/providers/openai_codex/list_models.py`
- Modify: `openprogram/providers/openai_codex/oauth.py`
- Modify: `openprogram/providers/openai_codex/probe_thinking.py`
- Modify: `openprogram/providers/openai_completions/probe_thinking.py`
- Modify: `openprogram/providers/openai_responses/probe_thinking.py`
- Modify: `openprogram/providers/sources/models_dev.py`
- Modify: `openprogram/providers/utils/oauth/anthropic.py`
- Modify: `openprogram/providers/utils/oauth/github_copilot.py`
- Modify: `openprogram/providers/utils/oauth/google_gemini_cli.py`
- Modify: `openprogram/providers/google/google.py`
- Modify: `openprogram/mcp/client.py`
- Modify: `openprogram/auth/methods/device_code.py`
- Modify: `openprogram/auth/methods/pkce_oauth.py`
- Create: `tests/security/test_runtime_sdk_transports.py`
- Create: `tests/security/test_runtime_service_consumers.py`
- Modify: `tests/providers/test_shared_client_leak.py`

**Step 1: Add real SDK-chain RED tests**

Monkeypatch only DNS/socket endpoints, not SDK request methods. Exercise:

```text
OpenAI/Anthropic shared provider clients -> injected managed httpx client
Google GenAI HttpOptions.httpxClient/httpxAsyncClient -> injected managed transports
MCP streamablehttp_client/sse_client -> httpx_client_factory returning managed async clients
Telegram/Slack/Discord/Feishu/Wechat SDK or adapter constructors -> declared fixed/configured origin transport
```

Assert the real SDK call chain reaches the constrained backend, keeps original Host/SNI/certificate validation, and refuses private rebound or cross-origin credential redirects. For an SDK that cannot accept a transport and cannot guarantee exact-origin/no-redirect behavior, assert startup is disabled with `UNMANAGED_TRANSPORT` unless an enforcing policy proxy is configured.

Add local-provider compatibility tests for OpenAI-compatible `http://localhost:<port>/v1`, localhost MCP HTTP/SSE, Matrix/internal channel bases, and local TTS. Add public compatibility tests for Telegram and fixed provider endpoints.

**Step 2: Run RED**

```bash
uv run pytest -q tests/security/test_runtime_sdk_transports.py tests/security/test_runtime_service_consumers.py
```

Expected: tests fail because SDK and service clients do not consistently use managed transports.

**Step 3: Inject managed clients and migrate raw service calls**

Change `make_async_http_client()` to require `consumer` and `configured_origin`, and construct its `httpx.AsyncClient` with `AsyncManagedHTTPTransport`. Update provider SDK creation so OpenAI and Anthropic receive this client, and Google `HttpOptions` receives managed sync/async clients. Provider probes, model listing, token exchange, and OAuth calls use fixed or configured registry keys; exact callback servers use `mcp.loopback.callback` or the existing inbound-only local listener as declared.

Pass a managed `httpx_client_factory` to both MCP HTTP SDK functions. Freeze the MCP server origin from owner configuration; credentials are valid only for that exact origin, and redirects are disabled.

For channel SDKs, implement one of the four registry-declared dispositions: injected transport; exact-origin configured service with redirect disabled; derived URL extraction followed by managed fetch; or disabled without an enforcing policy proxy. No active registry entry may use an unmanaged disposition. Replace remaining raw channel/TTS calls with their registry keys.

**Step 4: Run GREEN and affected tests**

```bash
uv run pytest -q tests/security/test_runtime_sdk_transports.py tests/security/test_runtime_service_consumers.py tests/providers tests/integration/test_mcp_client.py tests/unit/test_mcp_oauth_persistence.py tests/unit/test_channels_run_forever.py tests/unit/test_channels_attachments.py tests/unit/test_channels_transport_retry.py tests/unit/test_channels_telegram_semantics.py tests/unit/test_credential_validation.py
uv run ruff check openprogram/providers openprogram/mcp openprogram/auth openprogram/channels openprogram/tts.py tests/security
```

Expected: commands exit 0.

**Step 5: Commit**

```bash
git add openprogram/providers openprogram/mcp openprogram/auth openprogram/channels openprogram/tts.py tests
git commit -m "refactor(security): manage service and SDK HTTP transports"
```

### Task 7: Owner security configuration, exact exceptions, doctor, audit, and static enforcement

**Files:**
- Modify: `openprogram/config_schema.py`
- Modify: `openprogram/webui/routes/config.py`
- Modify: `openprogram/webui/ws_actions/settings.py`
- Modify: `openprogram/_cli_cmds/doctor.py`
- Create: `openprogram/security/runtime_http_audit.py`
- Create: `scripts/check_runtime_http.py`
- Create: `tests/security/test_owner_url_exceptions.py`
- Create: `tests/security/test_runtime_http_inventory.py`
- Create: `tests/security/test_doctor_ssrf.py`
- Modify: `tests/unit/test_web_config_schema.py`
- Modify: `tests/unit/test_config_write_safety.py`

**Step 1: Add owner-config and audit RED tests**

Test schema parsing and write authorization for:

```yaml
security:
  outbound_url:
    exceptions:
      - consumer: skills.configured.catalog
        origin: https://catalog.corp.example:8443
      - consumer: provider.configured_api
        cidr: 10.20.0.0/16
    policy_proxy:
      url: http://127.0.0.1:3128
      enforces_target_policy: true
```

Reject wildcard hosts, hostname suffixes, broad `0.0.0.0/0`/`::/0`, metadata/link-local networks, entries without a registry key, exception types the consumer disallows, URL credentials, unknown fields, and a proxy lacking the enforcement assertion. Prove only owner-authenticated existing config write paths can modify these values.

Test `doctor` output for active exact exceptions, policy delegation, recent sanitized denials, registry/inventory mismatch, and active unmanaged SDK disposition. Assert secrets never appear.

**Step 2: Add a fail-closed static inventory test**

Implement test expectations before the scanner:

```python
def test_runtime_http_inventory_has_no_unclassified_calls():
    result = scan_runtime_http(ROOT / "openprogram")
    assert result.unregistered == ()
    assert result.active_unmanaged_transports == ()
    assert result.registry_without_consumer == ()
```

The AST scanner detects imports/calls for `urllib.request.urlopen`, `requests` methods/sessions, `httpx` top-level methods/clients, `httpcore` pools/proxies, `aiohttp`, `urllib3`, raw `socket.connect`, and known network SDK constructors. Its explicit boundary manifest contains only browser-control modules, sandbox/arbitrary-code launchers, external provider CLI child processes, package-manager child processes, the managed transport implementation, and tests. Each excluded entry contains a boundary owner and reason; stale paths fail the scan.

**Step 3: Run RED**

```bash
uv run pytest -q tests/security/test_owner_url_exceptions.py tests/security/test_runtime_http_inventory.py tests/security/test_doctor_ssrf.py tests/unit/test_web_config_schema.py tests/unit/test_config_write_safety.py
```

Expected: tests fail because the config schema, scanner, and doctor checks are absent.

**Step 4: Implement owner configuration and shared audit state**

Add strict Pydantic/config types for exact exceptions and enforcing policy proxy. Normalize values at config load, reject unknown fields, and pass immutable security settings to managed client construction. Reuse existing owner authentication for Web settings/config mutations; add no alternate write path.

Implement a bounded in-memory audit ring for recent denials plus deterministic registry/inventory status. Store only reason code, consumer key, sanitized origin, proxy delegation flag, and timestamp.

**Step 5: Implement static and doctor checks**

`scripts/check_runtime_http.py` exits nonzero for an unregistered raw call, stale exclusion, registry entry without a detected consumer or explicit test-only declaration, or active unmanaged SDK disposition. `doctor` imports the same registry and scanner data and emits stable labels:

```text
runtime-http-registry
runtime-http-owner-exceptions
runtime-http-policy-proxy
runtime-http-recent-denials
runtime-http-unmanaged-transport
```

**Step 6: Run GREEN**

```bash
uv run pytest -q tests/security/test_owner_url_exceptions.py tests/security/test_runtime_http_inventory.py tests/security/test_doctor_ssrf.py tests/unit/test_web_config_schema.py tests/unit/test_config_write_safety.py
uv run python scripts/check_runtime_http.py
uv run ruff check openprogram/security openprogram/config_schema.py openprogram/webui openprogram/_cli_cmds/doctor.py scripts/check_runtime_http.py tests/security
```

Expected: commands exit 0 and the scanner reports zero unregistered calls, zero active unmanaged transports, and zero unclassified consumers.

**Step 7: Commit**

```bash
git add openprogram/config_schema.py openprogram/webui openprogram/_cli_cmds/doctor.py openprogram/security scripts/check_runtime_http.py tests
git commit -m "feat(security): audit runtime HTTP enforcement"
```

### Task 8: Compatibility matrix and all-consumer acceptance tests

**Files:**
- Create: `tests/security/test_runtime_http_compatibility.py`
- Create: `tests/security/test_runtime_http_acceptance.py`
- Modify: registry entries in `openprogram/security/safe_http.py` only when an acceptance test demonstrates a required existing behavior

**Step 1: Write compatibility and registry-driven acceptance tests**

Parameterize over every registry key. For each entry, provide a valid fixture for its declared trust class and assert allowed methods, scheme, port, origin, redirect, MIME, body cap, credential behavior, and SDK disposition. Assert the parameter set equals `set(CONSUMER_REGISTRY)` so a new entry cannot avoid acceptance coverage.

Add end-to-end compatibility scenarios for public CDN download, GitHub catalog/update redirects that remain within allowed policy, Telegram fixed API, OpenAI-compatible localhost provider, localhost MCP, private enterprise configured service with exact exception, policy-proxy outage with no direct fallback, provider failover without shared pool or credential leakage, and IPv4/IPv6 loopback callbacks.

**Step 2: Run RED**

```bash
uv run pytest -q tests/security/test_runtime_http_compatibility.py tests/security/test_runtime_http_acceptance.py
```

Expected: uncovered registry keys or compatibility regressions fail with the exact consumer key.

**Step 3: Resolve failures without weakening global policy**

Adjust only the affected consumer's declared method, MIME prefix, decoded cap, or fixed origin when an existing supported workflow proves it is required. Do not add wildcard origin/address exceptions. If a third-party SDK cannot satisfy the managed-transport test, disable that SDK network feature unless an enforcing policy proxy is configured and keep the registry disposition explicit.

**Step 4: Run GREEN and the full affected suite**

```bash
uv run pytest -q tests/security
uv run pytest -q tests/meta_functions tests/providers tests/unit tests/webui tests/integration/test_mcp_client.py
uv run python scripts/check_runtime_http.py
uv run ruff check openprogram tests scripts/check_runtime_http.py
```

Expected: commands exit 0 with no inventory or SDK-classification gap.

**Step 5: Commit**

```bash
git add openprogram/security tests/security
git commit -m "test(security): verify runtime HTTP compatibility"
```

### Task 9: Documentation evidence, feature matrix, and final report

**Files:**
- Modify: `docs/reference/design/runtime/ssrf-protection.html`
- Modify: `docs/reference/design/feature-matrix.html`
- Create: `scripts/check_feature_matrix.py`
- Create: `tests/security/test_feature_matrix_mechanics.py`
- Create: `.superpowers/sdd/runtime-url-ssrf-protection/final-report.md`

**Step 1: Capture fresh implementation evidence**

Run and record complete command results in the report:

```bash
uv run pytest -q tests/security
uv run pytest -q tests/meta_functions tests/providers tests/unit tests/webui tests/integration/test_mcp_client.py
uv run pytest -q
uv run ruff check openprogram tests scripts/check_runtime_http.py
uv run python scripts/check_runtime_http.py
```

Do not edit implementation code in this task. A failure returns to the owning implementation task and its review loop.

**Step 2: Update the approved design document in its existing order**

Preserve the sequence “OpenProgram current state → other projects → OpenProgram plan.” Update only statements made current by the implementation and add an implementation/verification evidence subsection in the plan section. List the exact policy module, transport behavior, registry, migrated consumers, SDK dispositions, exclusions, owner exception format, doctor/static checks, and the commands/results from Step 1. Keep reference-project descriptions and direct primary-source links unchanged unless a link check proves a correction is required.

**Step 3: Update the feature matrix conservatively**

Mechanically locate the “private-network access and SSRF protection” row. Change OpenProgram from `·` to `●` only when all of these are evidenced: policy parsing/address tests; real sync/async socket and TLS peer tests; DNS rebinding prevention; manual redirect and credential-origin tests; body/decompression/timeout cleanup tests; every Runtime consumer registered and migrated; every SDK disposition classified with no active unmanaged transport; owner-only exact exceptions; doctor/audit/static scan; compatibility suite; full unit suite. Otherwise keep `·` and list the exact unmet gate in the row evidence. Never publish `◐` for this row.

First add `tests/security/test_feature_matrix_mechanics.py`, which imports `scripts.check_feature_matrix`, parses every feature row, requires 13 framework cells, accepts only `●` and `·`, recomputes OpenProgram score and gap counts from cell values, and asserts that every displayed summary count equals the recomputed value. Run it before creating the script and record the import failure as RED:

```bash
uv run pytest -q tests/security/test_feature_matrix_mechanics.py
```

Then implement `scripts/check_feature_matrix.py` with a nonzero exit for an invalid symbol, row-width mismatch, stale displayed count, or stale displayed score. Run GREEN:

```bash
uv run pytest -q tests/security/test_feature_matrix_mechanics.py
uv run python scripts/check_feature_matrix.py docs/reference/design/feature-matrix.html
```

**Step 4: Run documentation verification**

Run the repository-owned documentation commands and the source scan:

```bash
uv run python -m scripts.docs_site.build
uv run python -m scripts.docs_site.checklinks
rg -n "TODO|TBD|placeholder|partially implemented|unmanaged_transport" docs/reference/design/runtime/ssrf-protection.html docs/reference/design/feature-matrix.html
```

Expected: documentation commands exit 0; any `unmanaged_transport` mention describes a tested disabled state or zero active findings, not an active consumer.

**Step 5: Write the final report**

The report contains baseline SHA, commit list, implementation scope, per-consumer registry table, four-way SDK disposition table, excluded-boundary table, owner exception and proxy behavior, RED/GREEN evidence for each task, every final verification command and actual result, review findings/fixes, remaining concerns, final SHA, and `git status --short` output.

**Step 6: Commit documentation and report-producing metadata**

The report is stored in the gitignored SDD workspace and remains available to the controller. Commit tracked documentation:

```bash
git add docs/reference/design/runtime/ssrf-protection.html docs/reference/design/feature-matrix.html scripts/check_feature_matrix.py tests/security/test_feature_matrix_mechanics.py
git commit -m "docs(security): record runtime SSRF enforcement"
```

### Task 10: Whole-branch review and completion verification

**Files:**
- Review all changes from baseline `48ae209b811d00298c5658985cd0d3c1848c7511`
- Modify only files required by accepted review findings through their owning task's fix loop
- Finalize: `.superpowers/sdd/runtime-url-ssrf-protection/final-report.md`

**Step 1: Generate the whole-branch review package**

```bash
python /Users/fzkuji/.codex/plugins/cache/claude-plugins-official/superpowers/6.2.0/skills/subagent-driven-development/scripts/review-package.py \
  docs/superpowers/plans/2026-08-11-runtime-url-ssrf-protection.md \
  48ae209b811d00298c5658985cd0d3c1848c7511 \
  "$(git rev-parse HEAD)"
```

Dispatch a fresh frontier reviewer using `superpowers:requesting-code-review`. Require explicit verdicts for specification coverage, security boundaries, tests, code quality, and feature-matrix evidence. One consolidated fix wave is allowed, followed by scoped re-review and a final whole-branch verdict.

**Step 2: Run fresh completion verification**

Run without relying on earlier output:

```bash
uv run pytest -q tests/security
uv run pytest -q tests/meta_functions tests/providers tests/unit tests/webui tests/integration/test_mcp_client.py
uv run pytest -q
uv run ruff check openprogram tests scripts/check_runtime_http.py
uv run python scripts/check_runtime_http.py
```

Run `uv run python -m scripts.docs_site.build`, `uv run python -m scripts.docs_site.checklinks`, and `uv run python scripts/check_feature_matrix.py docs/reference/design/feature-matrix.html`. Record exit code, test counts, and output summaries in the final report.

**Step 3: Verify repository state and report integrity**

```bash
git diff --check 48ae209b811d00298c5658985cd0d3c1848c7511..HEAD
git status --short
git log --oneline 48ae209b811d00298c5658985cd0d3c1848c7511..HEAD
```

Expected: `git diff --check` exits 0; `git status --short` is empty; log contains the plan, implementation, tests, and documentation commits listed in the report. If the report is updated after the final tracked commit, keep it in the gitignored SDD workspace and record the final tracked SHA exactly.

## Plan self-review checklist

- Every approved design area maps to a numbered task: pure policy, sync/async constrained peer, Host/SNI/certificate validation, manual redirect, credential origin, resource/decompression limits, policy proxy, registry, Runtime consumer migrations, four SDK dispositions, owner-only exceptions, doctor/audit/static enforcement, compatibility, documentation, and feature-matrix gates.
- Every production-code task states a focused RED command before implementation and a GREEN command after implementation.
- Every currently discovered Runtime-owned raw HTTP family is assigned to Tasks 4–7; separately governed browser, arbitrary-code, external CLI, and package-manager boundaries are explicitly scanned and documented.
- No task permits an active unmanaged SDK transport, unregistered registry consumer, implicit redirect, TLS verification bypass, environment-proxy inheritance, or fallback raw network call.
- Type signatures use Python 3.11 syntax consistently; registry keys are stable strings; limits are numeric and finite.
- The plan contains no unresolved placeholder or undecided implementation branch.
