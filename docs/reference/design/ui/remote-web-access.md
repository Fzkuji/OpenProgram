# Remote Web Access — Owner-Only, Self-Hosted Control

> This document defines how the owner reaches OpenProgram's existing Web UI
> from the same machine, a trusted LAN or VPN, an SSH tunnel, or an
> owner-operated HTTPS reverse proxy. The English text is normative; the
> Chinese translation and the standalone HTML page present the same design.
> Related code: `openprogram/webui/`, `openprogram/agent/authority.py`,
> `openprogram/mcp/token_storage.py`, and `openprogram/_compat.py`.
> Related designs: [speaker identity](../memory/speaker-identity.md),
> [permission model](../runtime/permission-model.md), and
> [MCP server](../integrations/mcp-server.md).

OpenProgram keeps one authority model in every deployment mode: a
process-lifetime instance token authenticates the active profile/state
instance's single owner, and every authenticated Web request receives that
owner tier and its fixed capability set.
OpenProgram does not operate a public relay and does not turn the Web UI into a
multi-user application.

## 1. Method and scope

### 1.1 One active state instance, one owner

The Web UI exposes sessions, files, processes, credentials, settings, tools,
approvals, and agent actions. These functions are one administrative boundary,
not separate project roles. The Web authentication question is therefore:
"does this request possess the current Web process token?" It is not "which
registered user sent this request?"

Successful authentication maps a request to the stable principal
`owner/install/<16hex>` from `openprogram/agent/authority.py` and to the
owner tier with its fixed capability set. Despite the principal's retained identifier format,
its `owner.json` record lives under the profile-aware state directory. The
single-owner boundary therefore applies to the active profile/state instance,
not globally across every OpenProgram profile installed for one OS account.
Token possession establishes authorization as that singleton owner; it does
not prove which physical person is operating the browser. OpenProgram stores no
Web user table, password database, membership, role assignment, or per-project
ACL.

Shared human participation remains a channel concern. Telegram, Discord, and
other channel messages retain speaker attribution and the restricted
`paired` authority tier. A channel participant never becomes the Web
owner because a channel turn exists.

### 1.2 Four supported access modes

| Mode | OpenProgram bind | Browser path | Required protection |
|---|---|---|---|
| Same machine | `127.0.0.1` | Direct loopback URL | Instance token, Host/Origin checks |
| Trusted LAN or encrypted VPN | Explicit `web.host`, often `0.0.0.0` | Direct address | Instance token, non-empty exact origins, Host checks, and HTTPS or an encrypted network; direct HTTP is warning-only and limited to the enumerated local/overlay ranges in section 5.5 |
| SSH tunnel | `127.0.0.1` | Local forwarded port | Instance token; SSH supplies transport encryption |
| Public owner domain | `127.0.0.1` behind a same-host proxy | Owner-managed HTTPS URL | Instance token, exact origin/Host, trusted loopback proxy, HTTPS |

`web.host` is necessary only for direct LAN or VPN access. It is not necessary
for an SSH tunnel or a reverse proxy running on the OpenProgram host; both
connect to the loopback listener.

### 1.3 Product boundaries

OpenProgram supplies application authentication and validates the browser
boundary. It does not:

- issue or renew TLS certificates;
- operate a public relay, hosted tunnel, rendezvous service, or sharing URL;
- create Web accounts, registration, invitations, RBAC, or project
  permissions;
- accept an identity-aware proxy, Tailscale identity, or OAuth provider as a
  substitute for the instance token;
- provide an unauthenticated or `--insecure` mode.

SSH, a VPN, nginx, Caddy, and certificate automation remain independently
operated transport and deployment components. Authentication is still enabled
when any of them is present.

## 2. Current implementation and threat model

### 2.1 What exists now

`_web_config()` in `openprogram/webui/server.py` defaults to `127.0.0.1` and
loads `web.host` plus `web.allowed_origins`. `create_app()` uses a FastAPI
lifespan context and installs `BrowserOriginGuard` for both HTTP and WebSocket
scopes. The guard currently evaluates rules in this order:

- requires a loopback `Host` only while the configured bind host is loopback;
- accepts an exact string in the unvalidated `allowed_origins` list before
  inspecting `Sec-Fetch-Site`, including a configured `null` value;
- otherwise rejects `Sec-Fetch-Site: cross-site` and an opaque `null` Origin;
- otherwise accepts a matching Host origin or a loopback origin;
- accepts requests with no `Origin`, for terminal and native clients.

These rules reduce browser-origin and DNS-rebinding exposure, but they do not
authenticate a caller. When `web.host` is non-loopback,
`enforce_loopback_host` becomes false, so the existing Host restriction is
disabled. `allowed_origins` values are compared as normalized strings rather
than parsed and validated origin authorities.
The current Uvicorn startup does not disable its proxy-header processing, so an
OpenProgram middleware cannot yet rely on the ASGI client and scheme as the raw
socket peer and transport.

The `/ws` handler accepts the connection before any caller authentication and
then exposes the full `WS_ACTIONS` registry. HTTP routes and SSE endpoints have
no common authentication dependency. Web chat entry points already attach the
owner authority object, but today they do so before proving that the
request came from the owner.

Credential responses are masked by default in part of the provider API, while
two production paths still return plaintext:

- `GET /api/providers/{provider}/accounts/{name}/reveal`;
- `GET /api/config/key/{env_var}?reveal=1`.

The existing `/healthz` response is also too detailed for a public unauthenticated
probe: it includes code revision, uptime, database state, session visibility,
recent-message counts, and tool counts.

### 2.2 Why loopback still requires a token

Loopback limits which network interfaces accept connections; it does not
authenticate a browser request or another local process.

| Caller | What the current boundary permits | Required final control |
|---|---|---|
| Arbitrary Web page | It can send some HTTP requests to localhost; WebSocket is not protected by the HTTP same-origin read rule | Token plus exact Origin/Host checks before any action |
| DNS-rebinding page | It can make a hostile name resolve to a loopback address and present a foreign Host | Fail-closed Host authority validation |
| Local process or another OS account | It can omit `Origin`, as `curl` and native clients do | Bearer token; missing Origin conveys no trust |
| LAN peer after external bind | It can connect directly to every current route and `/ws` | Mandatory token, exact origins, Host validation, protected transport |
| Reverse-proxy client | It presents a public Host while the backend remains loopback | Explicit public origin plus a narrowly trusted loopback proxy |

Jupyter Notebook 4.3 made token authentication the default and generated the
token used by its automatically opened browser, preserving ordinary zero-input
startup while authenticating a browser-accessible local execution environment
([4.x changelog](https://github.com/jupyter/notebook/blob/4.x/docs/source/changelog.rst)).
Current Jupyter Server documentation retains token/cookie authentication and
requires HTTPS for public deployment. This is a precedent for the user
experience, not a claim that both products have identical code or threat sets.

### 2.3 Security invariants

The final implementation preserves these invariants:

1. No protected application state, session data, project or user file bytes,
   secret metadata, SSE event, or WebSocket frame is returned before
   authentication.
2. No HTTP, SSE, or WebSocket action executes before authentication.
3. A missing `Origin` never establishes trust; it is allowed only after valid
   Bearer authentication, or for a safe cookie-authenticated request whose
   method does not require an Origin.
4. Cookie authentication retains CSRF controls. The token complements Host and
   Origin validation; it does not replace them.
5. A non-loopback direct bind with incomplete origin configuration fails before
   the server starts accepting connections.
6. No route returns a stored secret or the Web instance token after initial
   entry.

## 3. Reference framework comparison

### 3.1 Survey boundary

The survey covers the open-source systems already used in OpenProgram's design
corpus and additional systems with directly relevant Web deployment,
authentication, proxy, or secret-handling behavior. An absent capability is
recorded explicitly. This is not an exhaustive enumeration of every public
agent repository.

| System | Verified design | Remote-access consequence | Use in OpenProgram |
|---|---|---|---|
| [OpenClaw](https://github.com/openclaw/openclaw) | Loopback-first gateway; authenticated non-loopback operation; explicit Control UI origins; SSH and reverse-proxy guidance; Host/DNS-rebinding and trusted-proxy controls ([remote access](https://github.com/openclaw/openclaw/blob/main/docs/gateway/remote.md), [security](https://github.com/openclaw/openclaw/blob/main/docs/gateway/security/index.md), [Control UI](https://github.com/openclaw/openclaw/blob/main/docs/web/control-ui.md)) | Covers local, tunnel, and owner-operated remote deployment | Adopt exact origins, fragment bootstrap, SSH/proxy patterns, and proxy trust limits; retain the token even when an external identity layer exists |
| [Jupyter Server](https://github.com/jupyter-server/jupyter_server) | Notebook 4.3 enabled token authentication by default and supplied the generated token to the automatically opened browser; current Server uses token/cookie authentication, requires HTTPS for public deployment, and retains XSRF plus WebSocket Origin checks ([4.x changelog](https://github.com/jupyter/notebook/blob/4.x/docs/source/changelog.rst), [security](https://github.com/jupyter-server/jupyter_server/blob/main/docs/source/operators/security.rst), [public server](https://github.com/jupyter-server/jupyter_server/blob/main/docs/source/operators/public-server.rst)) | Closest established single-owner browser experience | Adopt automatic token entry and cookie transition; replace query-token entry with a fragment |
| [Hermes Agent](https://github.com/NousResearch/Hermes-Agent) | Dashboard defaults to loopback; a non-loopback bind requires a password or OAuth provider and fails startup without one; `--insecure` is deprecated and cannot disable that gate; Desktop reuses an authenticated session for WebSocket through a single-use ticket; key listings are redacted, although an authenticated rate-limited [reveal route](https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/web_server.py#L7663-L7696) remains ([dashboard](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/web-dashboard.md#when-the-gate-engages), [WS tickets](https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/dashboard_auth/ws_tickets.py)) | Strong fail-closed external-bind, session/WS, and redaction precedent, but with a user-auth provider model and secret retrieval OpenProgram does not need | Adopt external fail-closed behavior and redacted secret views; use OpenProgram's instance token and same-origin cookie instead of auth-provider accounts or a second WebSocket ticket; reject reveal |
| [Agent Zero](https://github.com/agent0ai/agent-zero) | Local UI, optional single login, session cookie, CSRF and WebSocket Origin checks; supports reverse proxies and built-in third-party tunnels; [settings](https://github.com/agent0ai/agent-zero/blob/main/helpers/settings.py) use masked placeholders ([installation](https://github.com/agent0ai/agent-zero/blob/main/docs/setup/installation.md), [VPS deployment](https://github.com/agent0ai/agent-zero/blob/main/docs/setup/vps-deployment.md)) | Useful cookie/CSRF/Origin composition and secret-update behavior | Adopt the combined checks and masked-only update contract; reject optional authentication and built-in public tunnels |
| [OpenHands](https://github.com/OpenHands/OpenHands) | Agent Canvas uses a session API key across HTTP and WebSocket and documents SSH plus nginx/HTTPS self-hosting ([self-hosting](https://github.com/OpenHands/OpenHands/blob/main/docs/SELF_HOSTING.md)); other OpenHands editions add account-oriented controls | Shows an instance-key deployment without requiring RBAC for the local product | Adopt the common HTTP/WS key and loopback backend; reject token exposure in public HTML or browser storage and authenticated plaintext-secret retrieval |
| [opencode](https://github.com/sst/opencode) | Server/Web modes default to loopback; Basic authentication is optional; configured CORS origins and a short-lived PTY WebSocket ticket exist ([server](https://github.com/sst/opencode/blob/dev/packages/web/src/content/docs/server.mdx), [network options](https://github.com/sst/opencode/blob/dev/packages/opencode/src/cli/network.ts), [PTY ticket](https://github.com/sst/opencode/blob/dev/packages/core/src/pty/ticket.ts)) | Good local default, insufficient external fail-closed rule when the password is absent | Adopt loopback default; the HttpOnly cookie already avoids long-lived WebSocket query credentials, so no second ticket system is needed |
| [Open WebUI](https://github.com/open-webui/open-webui) | Multi-user accounts and roles; official nginx/Caddy guidance covers HTTPS, WebSocket Upgrade, and SSE buffering ([nginx](https://github.com/open-webui/docs/blob/main/docs/reference/https/nginx.md), [Caddy](https://github.com/open-webui/docs/blob/main/docs/reference/https/caddy.md)) | Deployment mechanics apply, identity model does not | Adopt proxy mechanics; reject signup, accounts, JWT user sessions, groups, and RBAC |
| [Dify](https://github.com/langgenius/dify) | Workspace/account roles and reverse-proxy deployment; [credential responses](https://github.com/langgenius/dify/blob/main/api/core/entities/provider_configuration.py) use obfuscation and hidden-value update semantics | Strong secret-response precedent inside a different identity model | Adopt masked-only secret responses and explicit replacement; reject tenant and role layers |
| [LibreChat](https://github.com/danny-avila/LibreChat) | Registration, administrators, user/group/role access, JWT sessions, and documented nginx HTTPS/WebSocket deployment ([authentication](https://github.com/LibreChat-AI/librechat.ai/blob/main/content/docs/features/authentication.mdx), [nginx](https://github.com/LibreChat-AI/librechat.ai/blob/main/content/docs/remote/nginx.mdx)) | Confirms what a real multi-user design requires | Use only the proxy configuration details; do not implement its identity data model |
| [AnythingLLM](https://github.com/Mintplex-Labs/anything-llm) | Single-user and multi-user modes are distinct; single-user requests bypass authentication when no password token is configured, while multi-user mode adds role checks ([authentication middleware](https://github.com/Mintplex-Labs/anything-llm/blob/master/server/utils/middleware/validatedRequest.js), [role middleware](https://github.com/Mintplex-Labs/anything-llm/blob/master/server/utils/middleware/multiUserProtected.js)) | A single shared credential is compatible with one owner, but optional authentication is not | Keep the single-owner concept and make authentication non-optional |
| [AutoGen Studio](https://github.com/microsoft/autogen/tree/main/python/packages/autogen-studio) | Loopback default; [default authentication type](https://github.com/microsoft/autogen/blob/main/python/packages/autogen-studio/autogenstudio/web/auth/manager.py) is none; optional OAuth/JWT support; project documentation calls it a research prototype | Not a production remote-access baseline | Adopt only loopback default; reject query/localStorage credentials, post-accept WebSocket auth, and optional authentication |
| [SWE-agent](https://github.com/SWE-agent/SWE-agent) | The current repository documents command-line agent execution and does not ship an owner-operated Web control UI ([documentation tree](https://github.com/SWE-agent/SWE-agent/tree/main/docs)) | The target remote-Web capability is absent | Record as not applicable to this authentication design |
| [pi-mono](https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent) | The coding-agent package documents a TUI, [SDK](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/sdk.md), and process-oriented [RPC mode](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/rpc.md), but its package documentation does not define an owner-operated remote Web control server authentication contract | Related transport and UI components exist; the target capability is absent | Record as not applicable to this authentication design |
| [pi-ai](https://github.com/badlogic/pi-mono/tree/main/packages/ai) | Provider transport library, not an agent control UI | No remote Web ownership or deployment boundary | Record as not applicable |
| [WeClaw](https://github.com/fastclaw-ai/weclaw/blob/main/README.md) | Its documented HTTP API defaults to `127.0.0.1:18011` and permits changing the listen address, but the project does not document an owner Web control UI browser-authentication contract | External HTTP is present, but the target browser surface is absent | Do not use it as a remote-Web security precedent |
| [Codex CLI](https://github.com/openai/codex) | `codex app-server` has stdio and experimental WebSocket transports, and its remote-control flow uses enrollment and pairing; it is not an owner-hosted browser UI ([app-server](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md)) | Protocol and managed remote-control concerns differ from this self-hosted page | Do not adopt its service-mediated remote-control path because OpenProgram does not operate a relay |

### 3.2 Patterns that remain separate

Three recurring designs solve different requirements and must not be combined
without need:

- **Instance credential:** Jupyter, OpenClaw, and OpenHands use a server- or
  session-level credential suitable for one administrative owner.
- **Application accounts:** Open WebUI, Dify, LibreChat, and multi-user
  AnythingLLM need registration, durable sessions, roles, and resource policy.
- **Managed remote control:** products with pairing, hosted tunnels, or relay
  services manage device enrollment and public connectivity outside the local
  process.

OpenProgram uses the first design. The second introduces state and policy that
the product does not need. The third requires a service OpenProgram explicitly
does not operate.

## 4. What OpenProgram adopts, modifies, and rejects

| Source pattern | Treatment | OpenProgram form |
|---|---|---|
| Jupyter's automatic launch-token experience | Modify | Put the token in a URL fragment, exchange it once for an HttpOnly cookie, and remove the fragment before any authenticated fetch |
| OpenClaw's explicit origins, SSH guidance, and proxy trust boundary | Adopt | Exact canonical origins, parsed Host authorities, loopback proxy trust, and no Host fallback |
| High-entropy process credential | Adopt | Generate 32 random bytes with the standard library for each Web process, store them owner-only, and compare decoded bytes with `hmac.compare_digest` |
| Agent Zero's cookie + CSRF + Origin + WebSocket checks | Adopt | Require all four where a browser cookie is used; reject unsafe cookie requests without a valid Origin |
| Dify and Agent Zero masked secret updates | Adopt | Return masks only; omit an unchanged secret; require a new value for explicit replacement |
| opencode's optional Basic authentication | Reject | There is no authentication-off combination and no warning-only external mode |
| Open WebUI/LibreChat account and JWT model | Reject | No user database, refresh tokens, roles, groups, or project ACL |
| OpenClaw trusted-proxy or Tailscale identity as authentication | Modify | A proxy may provide transport or an additional control, but the OpenProgram token remains mandatory |
| Agent Zero built-in tunnels and managed remote-control relays | Reject | Document SSH and owner-managed HTTPS only; OpenProgram does not create public endpoints |
| Plaintext secret reveal after login | Reject | Owner authentication authorizes replacement and use, not retrieval through the UI |

The resulting design has one credential type, one browser bootstrap, one
application authorization mapping, and one set of HTTP/WS/SSE checks. It does
not add a second WebSocket ticket, a user-session database, or an OAuth flow.

## 5. Final remote-access design

### 5.1 Token lifecycle

At each Web server start, OpenProgram generates exactly 32 random bytes with
`secrets.token_bytes(32)`. Its external form is unpadded base64url and is
therefore exactly 43 ASCII characters. Before writing it, the process acquires
an owner-only exclusive operating-system lock at `<state-dir>/web.lock`. One active Web
process is permitted per profile/state directory; a second process fails
without reading, replacing, or invalidating the first process's token. The
token:

- exists for one Web process lifetime and changes after restart;
- is atomically written to `<state-dir>/web/token` before the listener becomes
  ready;
- is created with owner-only permissions and passed through
  `openprogram._compat.restrict_to_user()` for cross-platform hardening;
- is never accepted from configuration, a command-line argument, or an
  environment variable;
- is decoded to exactly 32 bytes before comparison and compared only with
  `hmac.compare_digest`;
- is never printed by normal server logs, exception messages, access logs,
  telemetry, or Web responses;
- is represented in logs only as
  `sha256:<sha256(raw_token_bytes).hexdigest()[:12]>`.

The file write reuses the atomic temp-file, `os.replace`, and permission
patterns already used by `openprogram/mcp/token_storage.py`. Startup fails if
OpenProgram cannot acquire the lock or create and read back the token file
safely. If binding or later startup fails, the process removes the token only
while it still owns the lock and the file still contains its token. Orderly
shutdown applies the same ownership check. An unlocked stale file is replaced
atomically after the next process acquires the lock.

### 5.2 Credential forms

All forms derive from the same process token:

| Client | Credential | Transport rule |
|---|---|---|
| Browser after bootstrap | `openprogram_owner_<owner-id-suffix>` HttpOnly cookie containing `base64url_no_pad(HMAC-SHA256(key=raw_token_bytes, msg=b"openprogram-web-cookie-v1"))` | `<owner-id-suffix>` is the 16-hex suffix of the active profile's principal; `SameSite=Strict`, `Path=/`, no `Domain`; `Secure` when the trusted effective scheme is HTTPS; session cookie with no persistent expiry |
| Native HTTP/SSE | `Authorization: Bearer <token>` | Header only; query parameters are rejected |
| Native WebSocket | `Authorization: Bearer <token>` during the upgrade | Header only |
| Browser WebSocket | The same HttpOnly cookie | Browser supplies the cookie and Origin during the upgrade |

The per-profile name prevents cookies for simultaneous profile servers on
different ports from overwriting one another; cookies themselves are not
port-scoped. The cookie value is also exactly 43 base64url characters. It is
not a user session and has no database row. Its expected value is recomputed
from the current token, so a server restart invalidates it. Authentication maps
either valid form to `local_owner_authority()`. On protected routes other than
the bootstrap endpoint, an `Authorization` header selects only the Bearer path:
a non-Bearer scheme, malformed value, or incorrect token returns `401` and
never falls back to a valid cookie.

### 5.3 Fragment bootstrap

The normal local launch remains zero-input:

```text
CLI                     Browser                  Web server
 | start, read token       |                         |
 | open /#token=<token> -->|                         |
 |                         | GET / (fragment absent)|
 |                         |------------------------>|
 |                         | public static shell     |
 |                         |<------------------------|
 |                         | read token in memory    |
 |                         | history.replaceState()  |
 |                         | POST /api/auth/bootstrap|
 |                         | token in request body ->|
 |                         | Set-Cookie: HttpOnly    |
 |                         |<------------------------|
 |                         | authenticated HTTP/WS/SSE
```

The frontend reads the fragment into memory and removes `#token=...` with
`history.replaceState` before sending the bootstrap request or any other data
request. The fragment is not part of the HTTP request, proxy access log,
Referer header, or server route. The bootstrap endpoint:

1. accepts `POST` only;
2. requires a valid exact Origin and valid Host;
3. rejects every request containing an `Authorization` header with the same
   `401` response used for a missing or incorrect body token;
4. requires `Content-Type: application/json`, a body no larger than 256 bytes,
   and exactly the object `{"token":"<43-character unpadded base64url>"}`;
   unknown keys, duplicate keys, malformed base64url, and every other token
   length are rejected;
5. decodes the value to 32 bytes and compares it in constant time;
6. returns `204` plus the cookie on success;
7. returns the same `401` body for a missing or incorrect token;
8. applies `Cache-Control: no-store` and never includes the token in a response.

The public remote command is explicit:

```bash
openprogram web auth-url --base-url https://agent.example.com
```

It reads the live token file and writes one full fragment URL to the invoking
terminal. It does not write the URL to application logs. `--base-url` must be a
canonical effective origin and must contain no path, query, fragment, or user
information. HTTP is accepted only for exact `localhost` while the actual
listener is loopback, or for an IP literal in the explicit local/overlay range
set defined in section 5.5; every other DNS name requires HTTPS.

### 5.4 Route policy

Only these requests can reach route handling without the normal authentication
middleware:

- the static application shell and immutable static assets;
- `POST /api/auth/bootstrap`, which performs its own token validation;
- `GET /healthz`, reduced to a non-identifying liveness response such as
  `{"status":"ok"}`.

Every other HTTP route, raw file response, provider route, diagnostic route,
SSE stream, and WebSocket upgrade requires a valid cookie or Bearer token.
Detailed health fields move behind authentication. Static HTML contains no
token, configuration secret, session identifier, user data, or dynamic
credential material.

The static shell sends a Content Security Policy containing at least
`object-src 'none'`, `base-uri 'none'`, and `frame-ancestors 'none'`. Executable
scripts are limited to same-origin files and build-generated hashes or nonces;
third-party scripts and `unsafe-eval` are not permitted. The shell also sends
`X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, and
`X-Content-Type-Options: nosniff`.

Authentication failures use stable generic responses:

- missing or invalid credential: HTTP `401` with
  `{"error":"authentication_required"}` and
  `WWW-Authenticate: Bearer realm="OpenProgram"`;
- invalid Host, Origin, or browser request context: HTTP `403` with
  `{"error":"request_origin_rejected"}`;
- invalid non-loopback startup configuration: no listener is started.

Bootstrap responses, authentication failures, protected API responses,
credential-status responses, and SSE responses send `Cache-Control: no-store`.
Only content-addressed immutable static assets use long-lived caching.

WebSocket authentication and Host/Origin validation occur before
`websocket.accept`. A missing or invalid credential produces an HTTP `401`
upgrade denial, not an accepted socket followed by an application close frame.

### 5.5 Host, Origin, CSRF, and transport matrix

OpenProgram reads Web configuration from the active profile's
`<state-dir>/config.json`. `allowed_origins` entries are full canonical
origins:

```json
{
  "web": {
    "host": "0.0.0.0",
    "allowed_origins": [
      "https://agent.example.com",
      "http://192.168.1.20:18100"
    ]
  }
}
```

Each entry must contain only `scheme://host[:port]`. Wildcards, paths, query
strings, fragments, user information, opaque origins, and malformed IPv6 are
invalid. Only `http` and `https` are valid schemes. DNS names are IDNA- and
case-normalized, IPv6 literals are bracketed, and default ports are removed.
Unspecified and multicast IP literals are invalid origins, so bind addresses
such as `0.0.0.0` and `::` cannot appear in `allowed_origins`.

The server computes `effective_origins` as the union of validated configured
origins and a narrow loopback default. When the actual listener is loopback,
the default contains `http://localhost:<actual-port>` and the origin formed
from the actual bound literal, such as `http://127.0.0.1:<actual-port>` or
`http://[::1]:<actual-port>`. A literal is not added unless the server is
actually listening on it. A non-loopback listener receives no implicit
origin. An SSH forward using a different local port must add that exact local
Origin to `allowed_origins`. No request `Host` value is ever promoted into the
set.

For every request, OpenProgram requires exactly one syntactically valid `Host`
authority; duplicate, comma-joined, user-information, unspecified-address,
and multicast-address forms are rejected. It constructs `request_origin` from
the browser-equivalent transport scheme and parsed `Host`: `http` and `ws` map
to `http`, while `https` and `wss` map to `https`. Only a loopback peer may
replace that scheme through a single valid `X-Forwarded-Proto` value.
`request_origin` must be in `effective_origins`. When an `Origin` header is
required or present, it must parse to the same canonical origin as
`request_origin`; membership elsewhere
in the configured set is not sufficient. This single comparison implements
both exact browser-origin and DNS-rebinding checks.

The only safe methods are `GET`, `HEAD`, and `OPTIONS`. Every such route must be
free of state changes; `HEAD` mirrors `GET` without a body and `OPTIONS` only
reports protocol metadata. Every mutation uses `POST`, `PUT`, `PATCH`, or
`DELETE`.

| Request form | Credential | Origin rule | Host rule |
|---|---|---|---|
| Cookie, unsafe HTTP method | Required | Exact `request_origin` required; missing Origin rejected | `request_origin` must be effective |
| Cookie, WebSocket | Required before accept | Exact `request_origin` required; missing Origin rejected | `request_origin` must be effective |
| Cookie, safe HTTP/SSE | Required | Explicit Origin must equal `request_origin`; same-origin navigation may omit it | `request_origin` must be effective |
| Bearer HTTP/SSE | Required | May omit Origin; if present it must equal `request_origin` | `request_origin` must be effective |
| Bearer WebSocket | Required before accept | Native client may omit Origin; if present it must equal `request_origin` | `request_origin` must be effective |
| Fragment bootstrap | Token in body | Exact `request_origin` required | `request_origin` must be effective |

`Sec-Fetch-Site: cross-site` remains a rejection signal for browser requests.
CORS headers control which browser code can read a response; they are not used
as authentication. A same-site value is not sufficient without token, Host,
and the applicable Origin rule.

A direct non-loopback bind requires at least one valid configured origin and
fails closed otherwise. Direct HTTP is accepted only when `ipaddress` confirms
membership in one of these explicit networks: IPv4 loopback `127.0.0.0/8`,
RFC 1918 `10.0.0.0/8`, `172.16.0.0/12`, or `192.168.0.0/16`, IPv4 link-local
`169.254.0.0/16`, RFC 6598 shared space `100.64.0.0/10`, IPv6 loopback
`::1/128`, IPv6 ULA `fc00::/7`, or IPv6 link-local `fe80::/10`. IPv6 zone
identifiers are not accepted in origins. An allowlist rather than `is_private`
is used because that property includes unusable or reserved addresses and
excludes RFC 6598. Unspecified, multicast, documentation, benchmarking,
reserved, and globally routable addresses are therefore rejected for HTTP.

Every accepted non-loopback HTTP origin emits a prominent startup warning
because a network observer can read the bearer credential. RFC 6598 is accepted
for owner-configured encrypted overlays such as Tailscale, but the address alone
does not prove encryption; the owner is responsible for using it only over the
encrypted overlay. HTTPS remains the normal choice for any network whose
transport protection is uncertain. Exact `localhost` on an actual loopback
listener is the only HTTP DNS-name exception; every other DNS origin requires
HTTPS.

### 5.6 Reverse-proxy trust

A same-host nginx or Caddy process may terminate HTTPS while OpenProgram stays
on `127.0.0.1`. The Web server starts Uvicorn with `proxy_headers=False`, so
Uvicorn cannot rewrite the ASGI client or scheme before OpenProgram evaluates
the raw socket peer. OpenProgram never trusts `X-Forwarded-For` and trusts
`X-Forwarded-Proto` only when that raw immediate peer is loopback. The header
must contain exactly one `http` or `https` value; lists and every other value
are rejected. Forwarded headers from any non-loopback peer are ignored. The
proxy preserves the public Host, overwrites the effective scheme, supports
WebSocket Upgrade, and disables response buffering for SSE. The resulting
`request_origin` must still match an effective origin. A bootstrap request for
a DNS or public-IP origin returns `403` unless the trusted effective scheme is
HTTPS, so a missing proxy header cannot create an insecure owner cookie. The
proxy does not replace the instance token.

The OpenProgram configuration for a same-host proxy remains loopback:

```json
{
  "web": {
    "host": "127.0.0.1",
    "allowed_origins": ["https://agent.example.com"]
  }
}
```

Minimal nginx shape:

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 443 ssl;
    server_name agent.example.com;

    # ssl_certificate and ssl_certificate_key are owner-managed.

    location / {
        proxy_pass http://127.0.0.1:18100;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Forwarded "";
        proxy_set_header X-Forwarded-Host "";
        proxy_set_header X-Forwarded-For "";
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }
}
```

Minimal Caddy shape:

```caddyfile
agent.example.com {
    reverse_proxy 127.0.0.1:18100
}
```

Caddy manages certificates only because the owner selected Caddy; OpenProgram
does not call its certificate APIs. In both examples the configured origin is
`https://agent.example.com` and the OpenProgram backend remains loopback.

### 5.7 Direct and tunneled examples

SSH tunnel:

```bash
ssh -N -L 18100:127.0.0.1:18100 owner@remote-host
```

OpenProgram remains bound to `127.0.0.1`. After the tunnel is active, the owner
prints a live URL on the remote host with:

```bash
openprogram web auth-url --base-url http://127.0.0.1:18100
```

This example keeps the same local and remote port, so the implicit loopback
origin applies. If the local side uses a different port, that exact
`http://localhost:<local-port>` or loopback-literal origin must be added to
`allowed_origins` before `auth-url` will accept it.

For a direct LAN or VPN address, the owner explicitly configures both bind and
origin:

```json
{
  "web": {
    "host": "0.0.0.0",
    "allowed_origins": ["http://192.168.1.20:18100"]
  }
}
```

The browser uses the address present in `allowed_origins`; `0.0.0.0` is a bind
address and is never a browser origin.

### 5.8 Secret handling

Stored provider credentials are entry-only through the Web UI:

- `GET /api/config/key/{env_var}` returns exactly
  `{"has_value":false,"masked":""}` when unset and, when set, returns
  `{"has_value":true,"masked":"sk-…abc4"}` with the actual mask; `env_var`
  must be a declared provider or search credential name, and an unknown name
  returns `404`;
- `GET /api/providers/{provider}/accounts` keeps non-secret account metadata,
  but represents an API-key credential only through `has_value` and
  `masked_key`; `identity`, `can_reveal`, `value`, and every full-key field are
  absent.

The two storage shapes use separate mutation contracts:

| Target | Preserve | Replace | Delete |
|---|---|---|---|
| Config/environment key | Omit its name from the `api_keys` map | `POST /api/config` with exactly `{"api_keys":{"ENV_VAR":"new printable-ASCII value"}}` or a multi-key map; listed values are required and success is `200 {"saved":true}` | `DELETE /api/config/key/{env_var}` with no body; idempotently removes the saved entry and current-process environment value, then returns `204` |
| API-key account | Account metadata operations such as use, rename, reorder, and rotation do not accept or change `api_key` | `POST /api/providers/{provider}/accounts/{name}/update` with exactly `{"api_key":"new printable-ASCII value","validate":true}`; `validate` is the only optional field and defaults to `true`; success is exactly `200 {"ok":true}` | `POST /api/providers/{provider}/accounts/remove` with exactly `{"id":"account-name"}` deletes the whole credential pool; success is `200 {"removed":true,"name":"account-name","cleared_active":<bool>}` |

Unknown fields, unknown credential names, `null`, empty strings, non-printable
or non-ASCII values, and every displayed mask return `400` without mutation.
Replacing a key that validation definitively rejects returns `400`; transient
or offline `unknown` validation is non-blocking and the save succeeds. Account
replacement returns `404` when
the provider/account does not exist or is not an API-key account. Account
deletion returns `404` for a missing or unknown `id`. Deleting a config key also
removes its live-process environment value; a value supplied again by the
parent environment can reappear only after a later process restart.

Provider detail, API-key settings, and account manager remove every reveal
button and reveal request. The backend never interprets a displayed mask as a
secret value.

The stable mask is the first three ASCII characters, U+2026, and the last four
characters only for values of at least twelve characters, which hides at least
five characters. Values shorter than twelve use the fixed string `••••••••`.
The mask therefore does not encode the original length for short credentials.
It is presentation-only and is never accepted in a write payload.

The account reveal route is removed and returns `404`. The masked config-key
status route remains, but a request containing the `reveal` query parameter is
rejected with `404`; it never changes the response to plaintext. The unrelated
project-file reveal action remains subject to normal file authorization and Web
authentication; it is not a credential-retrieval endpoint.

## 6. Implementation contract and acceptance tests

### 6.1 Startup contract

The server validates configuration, generates and secures the token, computes
the token fingerprint, and only then starts accepting connections. The startup
log states:

- the actual bind address and whether it is loopback;
- the configured public origins;
- whether trusted loopback-proxy scheme handling is active;
- the token fingerprint;
- a warning for direct non-loopback HTTP.

It never states the token. A malformed origin, non-loopback bind without an
origin, unsafe public HTTP origin, unsafe token file, or occupied Web-process
lock is a startup error.
Uvicorn proxy-header rewriting is disabled in the server configuration before
the listener starts; only the common OpenProgram ASGI policy interprets the raw
peer and `X-Forwarded-Proto`.

### 6.2 Request pipeline

The common ASGI order is:

```text
request
  -> immediate peer, trusted effective scheme, canonical Host/request_origin
  -> route + method + credential-source classification
  -> Origin / Sec-Fetch-Site / CSRF policy
  -> public, bootstrap, cookie, or Bearer authentication rule
  -> owner authority attachment
  -> HTTP route, SSE generator, or WebSocket accept
```

Public static routes skip credential authentication but not Host and browser
context validation. The bootstrap route replaces the common credential check
with its constant-time body-token exchange. No route implements an independent
authentication interpretation.

### 6.3 Required tests

The feature is complete only when these behaviors are executable tests:

1. Loopback HTTP, SSE, and WebSocket access without a credential fails.
2. A correct Bearer token succeeds without `Origin`; an incorrect token returns
   the same `401` shape and performs no action.
3. Browser bootstrap clears the fragment before other fetches, accepts only the
   exact bounded JSON schema, sends no token in URL/query/Referer, derives the
   specified HMAC cookie, rejects every mixed body-token plus Authorization
   request, and can then open HTTP, SSE, and WS.
4. Cookie-authenticated unsafe HTTP and WebSocket requests reject missing,
   opaque, cross-site, and unlisted origins before action or socket acceptance.
5. A foreign, duplicate, comma-joined, unspecified, or multicast Host is
   rejected on loopback, direct external bind, and reverse-proxy deployments;
   HTTP/WS and HTTPS/WSS produce the same browser-equivalent origin pairs.
6. Default loopback accepts only its implicit `localhost` and actual-literal
   origins; a different SSH local port is rejected until explicitly configured.
7. Non-loopback bind without a non-empty valid origin list refuses startup;
   the explicit local/overlay HTTP ranges are accepted with warnings, while
   unspecified, multicast, documentation, benchmarking, reserved, and global
   literals are rejected for HTTP.
8. Uvicorn proxy-header rewriting is disabled; forwarded scheme is honored
   from the raw loopback peer only and ignored from every non-loopback peer;
   forged `X-Forwarded-For` never changes trust, a public bootstrap with a
   non-HTTPS effective scheme is rejected, and direct WS plus Caddy-proxied WSS
   produce the expected browser-equivalent origin.
9. A second Web process for the same state directory cannot modify the live
   token; bind failure removes only the token owned by the failing process.
10. Restart rotates the token and invalidates the prior Bearer token and cookie.
11. An invalid or malformed Authorization header never falls back to a valid
    cookie.
12. Static assets and the reduced liveness response contain no process token,
   session data, filesystem data, credential data, or detailed diagnostics.
13. Security headers reject framing; protected/auth/credential responses are
    `no-store`, and `401` responses advertise the Bearer realm.
14. Both reveal forms return `404`; config-key replace/preserve/DELETE and
    account replace/preserve/remove execute the exact schemas and status codes
    specified above; 8–11-character credentials use the fixed mask, masks are
    never accepted, and frontend builds and types contain no reveal action or
    full-secret response field.
15. nginx and Caddy smoke deployments carry authenticated HTTP, SSE, and
    WebSocket traffic over HTTPS while the backend remains loopback.
16. Channel messages retain their `paired` authority tier and do not inherit Web
    owner authority.
17. Two profile servers on the same loopback hostname and different ports use
    distinct cookie names, authenticate independently, and ignore each other's
    cookie.

## 7. Implementation status

Design statements are not implementation evidence. Status is based on current
production paths and tests.

### Implemented

| Item | Evidence |
|---|---|
| Default loopback bind | `_web_config()` in `openprogram/webui/server.py` defaults to `127.0.0.1` |
| FastAPI lifespan | `create_app()` uses `_lifespan`; deprecated `@app.on_event` handlers are absent |
| Stable per-profile owner principal and explicit owner/paired authority tiers | `openprogram/agent/authority.py`; Web, TUI, desktop, runtime, and paired channel entry points attach a tier; `tests/unit/test_authority_scope.py` and permission tests cover the fixed tier table |
| Cross-platform file-permission helper and atomic credential-file pattern | `openprogram._compat.restrict_to_user()` and `openprogram/mcp/token_storage.py` are available for reuse; the Web-token implementation still has to enforce its fail-closed contract |

### Partially implemented

| Item | Implemented part | Missing part |
|---|---|---|
| Browser Origin/Host guard | HTTP and WS share `BrowserOriginGuard`; outside an early unvalidated `allowed_origins` match, explicit cross-site, opaque Origin, and loopback foreign Host cases are rejected | It is not authentication; a configured `null` or cross-site Origin can pass through the early allowlist branch; missing Origin passes; Host enforcement is disabled for non-loopback binds; proxy trust and canonical origin validation are absent |
| `web.allowed_origins` | Configuration is loaded and exact strings can be allowed | No schema validation, parsed `effective_origins`, non-loopback fail-closed rule, or public-HTTPS rule |
| Web owner attribution | Web chat requests receive the owner authority object | The caller is not authenticated before receiving that authority |
| Secret masking | `/api/config/key/{env_var}` masks by default | Two plaintext reveal paths and their frontend controls remain |
| Health endpoint | `/healthz` exists | Its unauthenticated response is not minimal and exposes operational metadata |

### Not implemented

| Item | Required result |
|---|---|
| Web process token | Per-start generation, `<state-dir>/web.lock`, `<state-dir>/web/token`, safe atomic lifecycle, constant-time validation, fingerprint-only logs |
| Browser bootstrap | Fragment parsing and removal, `POST /api/auth/bootstrap`, derived HttpOnly Strict cookie |
| Unified authentication | One HTTP/SSE/WS middleware policy, Bearer support, pre-accept WS `401`, generic failures |
| Remote URL command | `openprogram web auth-url --base-url ...` with canonical effective-origin validation |
| External-bind startup validation | Non-empty exact origins, parsed Host authorities, HTTPS/public-address rules, direct-HTTP warning |
| Trusted reverse-proxy handling | Uvicorn proxy rewriting disabled, raw-peer loopback-only forwarded-scheme trust, and tested nginx/Caddy behavior |
| Public-route reduction | Static shell, bootstrap, and minimal liveness only; detailed health behind auth |
| Secret non-retrievability | Removal of both plaintext reveal forms, frontend actions, types, and plaintext response paths; distinct config-key and account mutation contracts |
| End-to-end security tests | Browser, native client, WebSocket, SSE, restart, proxy, DNS-rebinding, and secret tests listed above |

### Explicitly out of scope

| Item | Boundary |
|---|---|
| Web accounts, signup, invitations, and user sessions | One active profile/state instance has one owner principal |
| RBAC, groups, tenant/workspace roles, and project permissions | The Web UI is one full-capability owner administrative interface |
| OAuth/OIDC/SSO and identity-aware proxy authentication | They do not replace the mandatory instance token |
| Built-in TLS, ACME, certificate storage, and renewal | The owner operates nginx, Caddy, a VPN, or SSH |
| Public relay, hosted tunnel, rendezvous, pairing service, and public share URL | OpenProgram makes no outbound registration for Web reachability |
| Authentication-off or `--insecure` mode | Token authentication is always enabled, including loopback |
| Long-lived token in query parameters, localStorage, or public HTML | Fragment bootstrap and HttpOnly cookie are the only browser entry path |
| Plaintext retrieval of stored provider credentials | Replacement is supported; reveal is not |
