# 编辑器（ACP）

在编辑器里直接用 OpenProgram。[ACP](https://agentclientprotocol.com)（Agent Client Protocol）是编辑器驱动外部 agent 的通用标准，`openprogram acp` 就是它的 agent 侧。Zed 原生支持 ACP，所以不用装任何扩展，OpenProgram 就成了编辑器里的对话 agent。

```bash
openprogram acp
```

这条命令在 stdin/stdout 上讲 JSON-RPC，编辑器断开就退出。你不用自己跑它，由编辑器拉起。

## 配置 Zed

打开 Zed 的 `settings.json`（`cmd-shift-p` → `zed: open settings file`），在 `agent_servers` 下加一项：

```json
{
  "agent_servers": {
    "OpenProgram": {
      "type": "custom",
      "command": "openprogram",
      "args": ["acp"],
      "env": {}
    }
  }
}
```

打开 Agent Panel，在新建线程菜单里选 **OpenProgram**，就可以开始聊了。如果 Zed 看到的 `PATH` 里没有 `openprogram`，把 `command` 换成绝对路径（`which openprogram` 查）。

两个参数值得知道：

- `--agent <id>` 用指定 agent 跑会话（默认 `main`），编辑器里的线程可以有一套跟终端会话不同的系统提示词和工具集。
- `--permission <mode>` 设定工具调用的权限档位：`ask`（默认）、`acceptEdits`、`plan`、`auto`、`bypass`。见[权限](../capabilities/tools.zh.md)。

```json
"args": ["acp", "--agent", "coding", "--permission", "acceptEdits"]
```

## 在编辑器里能用到什么

**流式回复。** 文字逐字到达；模型有推理输出时，思考过程走单独一条流。

**工具调用。** agent 每次调工具，开始和结束各上报一次，带参数和结果。涉及文件的工具会带上路径，Zed 据此跟随高亮正在改的文件。

**编辑器上下文。** 你把选区或文件挂到消息上时，编辑器把片段随请求一起送过来。OpenProgram 把它按路径折进 prompt，模型既看到代码也知道它来自哪里，需要更多内容可以自己去读原文件。

**权限确认。** 工具需要批准时，编辑器里弹出请求，三个选项：**Allow**、**Always allow**、**Reject**。选"Always allow"会把这个工具的常驻放行规则写进项目设置，跟在 Web UI 里批准是同一回事。

**取消。** 在编辑器里停掉线程会取消正在跑的这一轮，还挂着的权限卡片一并撤回。

**续聊。** 在编辑器里重新打开的线程会从会话存储里回放历史。这里开的会话就是普通的 OpenProgram 会话，同一段对话在 [Web UI](web.zh.md) 和 [TUI](tui.zh.md) 里都看得到，也能从那边接着聊。

## 边界

会话以本机 owner 身份运行，前提是编辑器就在你自己的机器上、键盘前坐的是你。这也正是 agent 能向你请求批准的前提。不要把这条命令暴露到网络传输上。

编辑器在 `session/new` 里传来的 MCP server 会被忽略，OpenProgram 用自己的 [MCP 配置](../capabilities/mcp.zh.md)。prompt 里的音频内容不支持。

实现的协议版本是 1，覆盖编辑器侧的 `initialize`、`session/new`、`session/load`、`session/prompt`、`session/cancel`，以及回传的 `session/update` 和 `session/request_permission`。
