# MCP

Connect any MCP (Model Context Protocol) server and its tools appear in chat as `<name>__<tool>` for the model to call. This page covers adding servers, where the configuration lives, and the supported transports.

## Quick start

```bash
openprogram mcp add drawio npx -y @drawio/mcp     # add a stdio server, effective immediately
openprogram mcp list                              # status of every configured server
openprogram mcp show drawio                       # that server's tools and full schemas
```

`mcp add` options: `--env KEY=VALUE` (inject an environment variable into the subprocess, repeatable), `--timeout` (startup and per-call timeout in seconds), `--disabled` (write the config without starting the server). The `name` becomes the prefix (`<name>__<tool>`) for all of that server's tools.

All subcommands:

```bash
openprogram mcp list | show | add | rm | restart | enable | disable | edit | test
```

- `rm` stops the server and deletes its config; `enable` / `disable` toggle it (disable keeps the config).
- `edit` opens the config file in `$EDITOR` — HTTP / SSE servers are currently added this way (`add` only covers stdio).
- `test` spins the server up with a throwaway config and confirms it returns a tool list, without writing anything to disk.

The management commands talk to the resident OpenProgram background worker; if it is not running, start it with `openprogram worker start` (check with `openprogram status`).

## Where the config lives

`~/.openprogram/mcp_servers.json` (with `--profile <name>`, `~/.openprogram-<name>/mcp_servers.json`). Format:

```json
{
  "servers": {
    "drawio": {
      "type": "local",
      "command": ["npx", "-y", "@drawio/mcp"],
      "env": {},
      "enabled": true,
      "timeout_seconds": 30
    },
    "linear": {
      "type": "http",
      "url": "https://mcp.linear.app/mcp",
      "auth": {"kind": "oauth", "client_name": "OpenProgram"},
      "enabled": true
    }
  }
}
```

## Transports and auth

| `type` | Description | Fields used |
|---|---|---|
| `local` | stdio subprocess | `command`, `env` |
| `http` | Streamable HTTP | `url`, `headers`, `auth` |
| `sse` | legacy SSE | `url`, `headers`, `auth` |

`auth.kind` supports `none` / `bearer` (a `token` field) / `oauth` (OAuth 2.1 PKCE; servers with dynamic client registration work with zero config — only servers requiring a pre-registered client need `client_id` / `client_secret`).

Beyond tools, MCP's other two primitives — resources and prompts — are also exposed to the model through built-in meta tools (see `mcp_meta` in [Built-in tools](tools.md)).
