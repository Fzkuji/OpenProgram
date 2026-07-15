# MCP

接入任意 MCP（Model Context Protocol）server，它的工具会以 `<name>_` 前缀出现在聊天里供模型调用。这一页讲怎么加 server、配置存在哪、支持哪些传输方式。

## 快速上手

```bash
openprogram mcp add drawio npx -y @drawio/mcp     # 加一个 stdio server，立即生效
openprogram mcp list                              # 每个已配置 server 的状态
openprogram mcp show drawio                       # 该 server 的工具与完整 schema
```

`mcp add` 的选项：`--env KEY=VALUE`（注入子进程环境变量，可重复）、`--timeout`（启动与单次调用超时秒数）、`--disabled`（只写配置不启动）。`name` 会用作该 server 所有工具的前缀。

全部子命令：

```bash
openprogram mcp list | show | add | rm | restart | enable | disable | edit | test
```

- `rm` 停止并删除配置；`enable` / `disable` 切换（disable 保留配置）。
- `edit` 用 `$EDITOR` 直接改配置文件——HTTP / SSE 类型的 server 目前通过它添加（`add` 只覆盖 stdio）。
- `test` 用一份临时配置试拉起 server 并确认能返回工具列表，不写盘。

管理命令与常驻的 OpenProgram 后台服务通信；服务未运行时先 `openprogram status` / 启动一次聊天。

## 配置存哪

`~/.openprogram/mcp_servers.json`（使用 `--profile <name>` 时是 `~/.openprogram-<name>/mcp_servers.json`）。格式：

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

## 传输与认证

| `type` | 说明 | 用到的字段 |
|---|---|---|
| `local` | stdio 子进程 | `command`、`env` |
| `http` | Streamable HTTP | `url`、`headers`、`auth` |
| `sse` | 旧式 SSE | `url`、`headers`、`auth` |

`auth.kind` 支持 `none` / `bearer`（`token` 字段）/ `oauth`（OAuth 2.1 PKCE；支持动态客户端注册的 server 零配置即可，预注册客户端的 server 才需要填 `client_id` / `client_secret`）。

除了工具，MCP 的另外两类原语——resources 和 prompts——也通过内置的 meta 工具暴露给模型（见[内置工具](tools.md)的 `mcp_meta`）。
