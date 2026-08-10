# Editor (ACP)

Drive OpenProgram from your editor. [ACP](https://agentclientprotocol.com) (Agent Client Protocol) is the editor-agnostic standard for an editor to run an external agent, and `openprogram acp` is the agent side of it. Zed talks ACP natively, so OpenProgram becomes a chat agent inside the editor with no extension to install.

```bash
openprogram acp
```

The command speaks JSON-RPC on stdin and stdout and exits when the editor disconnects. You do not run it yourself — the editor launches it.

## Configuring Zed

Open Zed's `settings.json` (`cmd-shift-p` → `zed: open settings file`) and add an entry under `agent_servers`:

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

Open the Agent Panel, pick **OpenProgram** from the new-thread menu, and start typing. If `openprogram` is not on the `PATH` Zed sees, use its absolute path (`which openprogram`) as `command`.

Two flags are worth knowing:

- `--agent <id>` runs sessions as a specific agent instead of `main`, so the editor thread can use a different system prompt and toolset than your terminal sessions.
- `--permission <mode>` sets the permission mode for tool calls: `ask` (default), `acceptEdits`, `plan`, `auto`, or `bypass`. See [Permissions](../capabilities/tools.md).

```json
"args": ["acp", "--agent", "coding", "--permission", "acceptEdits"]
```

## What works in the editor

**Streaming replies.** Text arrives token by token, and the model's reasoning shows up as a separate thought stream when the model produces one.

**Tool calls.** Every tool the agent runs is reported as it starts and again when it finishes, with its arguments and result. Tools that touch a file report the path, so Zed can follow along and highlight the file being edited.

**Editor context.** When you attach a selection or a file to your message, the editor ships the excerpt inline with the request. OpenProgram folds it into the prompt under its path, so the model sees both the code and where it came from, and can then read more of the file on its own.

**Permission prompts.** When a tool needs approval, the request appears in the editor with **Allow**, **Always allow**, and **Reject**. Choosing "Always allow" writes a persistent allow rule for that tool into the project's settings, exactly as approving in the Web UI does.

**Cancellation.** Stopping a thread in the editor cancels the running turn, and any permission prompt still on screen is withdrawn.

**Resuming.** Threads reopened in the editor replay their history from the session store. Sessions started here are ordinary OpenProgram sessions — the same conversation is visible in the [Web UI](web.md) and the [TUI](tui.md), and can be continued from either.

## Scope

Sessions run with local owner authority, on the assumption that the editor is on your own machine and you are the one at the keyboard. That is what allows the agent to ask you for approval at all. Do not expose the command over a network transport.

MCP servers passed by the editor in `session/new` are ignored; OpenProgram uses its own [MCP configuration](../capabilities/mcp.md) instead. Audio content in prompts is not supported.

The protocol version implemented is 1, covering `initialize`, `session/new`, `session/load`, `session/prompt` and `session/cancel` from the editor, and `session/update` and `session/request_permission` back to it.
