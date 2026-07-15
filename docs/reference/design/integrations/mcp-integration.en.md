# MCP Tool Integration

Status: **implemented** (stdio transport only).
Related code: `openprogram/mcp/`.

## In one sentence

Register the tools provided by an external MCP server as in-framework `AgentTool`s, so the LLM calls them exactly the way it calls a local `@function`/`@agentic_function`. On worker startup, spawn a subprocess for every enabled server in the config file, run JSON-RPC over stdio, wrap their `tools/list`, and mount the result into `_registry`.

## Role — we are the client, not the server

MCP is a protocol that defines a "tool provider" (server) and a "tool consumer" (client).

| Role | Who plays it | Examples |
|------|------|------|
| Server | The process that provides tools | `@drawio/mcp`, `@modelcontextprotocol/server-filesystem` |
| Client | The LLM framework, which exposes the server's tools to the LLM | OpenProgram, Claude Desktop, Claude Code, Cursor, Cline, opencode |

We are the client. This design **does not cover** exposing OpenProgram's own tools to an external client (if we ever do that, it will be a separate, standalone module that does not reuse the code in `openprogram/mcp/`).

## Directory location

`openprogram/mcp/` is a **top-level module**, a sibling of `openprogram/channels/`. It does **not** live under `openprogram/functions/`.

Rationale:
- MCP is an **external-protocol adapter layer**, which is a different thing from "the implementation of a function." Our function system (`@function` / `@agentic_function` / the code under `agentics/`) is locally-implemented tools; MCP is tools fetched from an external process. The two layers are orthogonal concepts.
- Both opencode (`src/mcp/`) and Claude Code (`src/services/mcp/`) treat MCP as a standalone module, fully separate from local tool implementations.
- In our project, `channels/` (message channels) is the existing precedent for an "external-protocol adapter" — it brings external message sources in. MCP also brings something external in, just a tool source instead, so its **location should match channels**.

## Reference implementation

For a full side-by-side, see `references/opencode/packages/opencode/src/mcp/`. OpenProgram's MCP layer is essentially a Python translation of opencode's.

`references/hermes-agent/mcp_serve.py` and `references/openclaw/src/mcp/` also provide MCP, but **both are the server side** (exposing their own capabilities to Claude Desktop and the like), which is the opposite direction of what we're doing.

## Configuration

`<state_dir>/mcp_servers.json` (default `~/.openprogram/mcp_servers.json`; the path follows profile switches automatically via `paths.get_state_dir`).

```json
{
  "servers": {
    "drawio": {
      "type": "local",
      "command": ["npx", "-y", "@drawio/mcp"],
      "env": {},
      "enabled": true,
      "timeout_seconds": 60
    }
  }
}
```

| Field | Description |
|------|------|
| `type` | Currently only `"local"` (stdio) is supported. `"remote"` (HTTP/SSE + OAuth) is reserved; opencode has it, we haven't done it yet |
| `command` | Subprocess command + argument list |
| `env` | Environment variables injected into the subprocess (the base environment is inherited from the parent process; this overrides / appends to it) |
| `enabled` | Set false to temporarily disable a server without deleting its config |
| `timeout_seconds` | Startup wait + the read timeout for a single call |

A missing file or a parse failure is non-fatal — `load_configs()` returns an empty list, the worker starts normally, and one warning line is logged. MCP is an opt-in feature.

## Code structure

```
openprogram/mcp/
  __init__.py    public re-exports: load_mcp_servers / shutdown_mcp_servers / server_status
  config.py      MCPServerConfig + load_configs (reads mcp_servers.json, purely static)
  client.py     MCPClient — a single server's stdio subprocess + ClientSession + supervisor task
  adapter.py    MCP wire types ↔ AgentTool type translation (pure functions, stateless)
  registry.py    global manager: load_mcp_servers / shutdown_mcp_servers / server_status
```

Four files, one responsibility each: config, single connection, translation, management.

## Call path — there is only one, LLM-initiated automatically

```
You type "draw a flowchart" in the webui
  ↓
worker → agent_loop → calls the LLM API with all tools (local + MCP)
  ↓
LLM decides, emits tool_call(name="drawio__open_drawio_xml", args={...})
  ↓
agent_loop looks up the AgentTool in _registry, awaits execute(args)
  ↓ execute is the closure built in adapter.register_remote_tool
  ↓ the closure has captured the corresponding MCPClient, and directly calls client.call_tool(original tool name, args)
  ↓ MCPClient sends it over stdio JSON-RPC to the corresponding drawio subprocess
  ↓
the drawio MCP subprocess executes (invokes the macOS open command to launch the browser)
  ↓ returns CallToolResult
  ↓
adapter.convert_call_result turns the MCP content blocks into an AgentToolResult
  ↓
agent_loop stuffs the result into chat history and calls the LLM once more
  ↓
the LLM sees the tool result and generates the final text reply "Done"
  ↓
the webui displays it
```

The whole chain completes synchronously on the FastAPI main loop, with **no cross-thread / cross-event-loop scheduling involved**.

`/api/run/{name}` / the CLI `programs run` / the `/functions` page do **not** wire up MCP tools — those are entry points for local functions. MCP tools are invisible to the user; the LLM uses them implicitly.

## Key technical decisions

### 1. Persistent connection + supervisor task

MCP is designed around a long-lived bidirectional connection (the server can push progress notifications, resource-update notifications, etc.). The official Python SDK exposes the session as a nested async-context-manager:

```python
async with stdio_client(params) as (r, w):
    async with ClientSession(r, w) as session:
        await session.initialize()
        ...
```

Holding a context manager across function calls requires keeping `__aexit__` pending, for which Python has no direct API. The cleanest approach is to wrap the whole nested block in a long-lived coroutine and put it in an `asyncio.Task` acting as a supervisor:

```python
async def _supervisor(self):
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            result = await session.list_tools()
            self._session = session
            self.tools = list(result.tools)
            self._ready.set()
            await self._shutdown.wait()      # stay parked here, waiting for the shutdown signal
```

`start()` creates the supervisor task and `await self._ready.wait()`; `call_tool()` simply reuses `self._session`; `stop()` sets `self._shutdown`, the supervisor exits the nested `async with`, and the subprocess closes automatically.

A startup failure (the spawn doesn't come up, or initialize errors out) also goes through `self._ready.set()`, sets `self.error` to the reason string, unblocks `start()`, and the manager, seeing the error, skips tool registration and logs one line.

### 2. Tool namespace `{server}__{tool}`

opencode uses `{server}:{tool}` as the namespace separator. We switch to a double underscore, `{server}__{tool}`, because OpenAI's tool-name regex is `^[a-zA-Z0-9_-]+$`, which rejects the colon. Anthropic uses the same character set.

`adapter.namespace_tool_name(server, tool)` does both:
- replaces any character outside `[A-Za-z0-9_-]` with `_`
- truncates to 64 characters (OpenAI's hard limit), preserving the tool suffix

Same-named tools across servers will collide, and the later-registered one overrides the earlier — consistent with last-wins elsewhere in the framework.

### 3. eager spawn vs. lazy spawn

opencode is lazy: it doesn't connect when the service is instantiated; it spawns on the first `tools()` call. We choose **eager**: `load_mcp_servers()` runs to completion in the FastAPI startup hook before the worker enters its serving state.

Rationale: eager is simple to implement and leaves the state stable after startup. The cost is that each enabled server can block for up to `timeout_seconds` (servers start sequentially, one at a time). drawio-mcp's first `npx -y @drawio/mcp` also has to download from npm, which can be slow, so the timeout defaults to 30s and the user can raise it.

Non-fatal: if a server fails to start, the manager records the error, skips registering its tools, and the worker keeps starting and can still use the other servers (and all local tools).

### 4. Tolerant content conversion

`adapter.convert_call_result()` handles the MCP return value:

| MCP type | Converted to |
|---|---|
| `TextContent` | `providers.types.TextContent` (chat history) |
| `ImageContent` | `providers.types.ImageContent` (multimodal LLM) |
| `EmbeddedResource` | expanded into `[resource: <uri>]` text — raw bytes are not stuffed into chat |
| unknown / future types | `repr(block)` text — at least the LLM can see that something came back |

An `isError=True` return is reflected in `details["is_error"]`, and what the LLM sees in chat history is the tool-result text, consistent with the error path of local tools.

### 5. Test isolation — beware of `from openprogram.paths import get_state_dir`

`tests/integration/test_attach_lazy_session.py` uses `monkeypatch.setattr("openprogram.paths.get_state_dir", ...)` to redirect the state directory. If `config.py` pulls it in via `from openprogram.paths import get_state_dir`, the first import happens **during** the attach test (because the webui startup hook triggers `_start_mcp_servers` → import `openprogram.mcp` → import `config`), and the lambda gets permanently bound into our module, leaking after the attach test ends.

Fix: `from openprogram import paths as _paths`, and look up the attribute live on every call to `_paths.get_state_dir()`. Watch out: if the test suite adds a new paths monkeypatch, confirm our `config.py` still goes through the module reference.

## Current limitations

1. **stdio only**. HTTP/SSE/OAuth are still to come (the `MCPServerConfig.type` field already holds the slot).
2. **`tools/*` only**. MCP also has `prompts/*` `resources/*` `sampling/*`, none of which is wired up yet — we have no corresponding concept in our framework.
3. **A config change requires a worker restart**. There is no hot reload of the server list; there is no `restart` between `MCPClient.start()` and `stop()`.
4. **Multiple callers serialize on the ClientSession lock**. `MCPClient._call_lock` queues concurrent calls to the same server; this isn't a performance problem (an MCP server is single-flight to begin with), but note that one slow tool will block other tool calls on the same server.
5. **No tool-list caching**. The supervisor pulls `list_tools` once at startup and never asks again; if an MCP server adds/removes tools mid-flight, we won't see it (the protocol has a `tools/list_changed` notification, which we don't listen for).
6. **`_loaded` is a module global**. Tests have to reset it before re-testing; see the `tests/integration/test_mcp_client.py::_reset_mcp_loader_flag` fixture.

## Possible follow-ups

- Wire up remote transport (HTTP / SSE) + an OAuth provider — copy opencode's `mcp/auth.ts` + `mcp/oauth-provider.ts`
- Listen for `tools/list_changed` and register tools at runtime
- Have the webui settings page render `server_status()`, visualizing each server's status, error message, tool count, and most recent call result
- A `restart_server(name)` API — without restarting the whole worker
- Wire up the `sampling/createMessage` capability — a server can reverse-borrow the client's LLM to do inference (the client doesn't implement this today and will reject such requests)
