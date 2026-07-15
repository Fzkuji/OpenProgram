# Daily Use

This page covers the operations you'll use every day once installed: the two entry points (terminal and web), resuming and managing sessions, and branching / rollback in the web UI.

## Two entry points, one set of sessions

- `openprogram` — terminal chat interface (TUI).
- `openprogram web` — browser interface, http://localhost:18100.

Session data lives in `~/.openprogram/sessions/`, and the terminal and the web see the same history: a session started in the terminal shows up in the web sidebar, and vice versa.

One-off questions don't need an interface:

```bash
openprogram --print "Summarise this error message for me: ..."
```

Common commands for the background service:

```bash
openprogram status      # is the service running (PID, ports, uptime)
openprogram restart     # restart after code or config changes
openprogram stop        # stop
```

## Resuming sessions

```bash
openprogram sessions list          # list all sessions across all agents
openprogram --resume <session_id>  # resume a session in the terminal
```

Session ids are also visible in the web sidebar. There is also `openprogram sessions resume`, used to answer a session that is currently waiting for user input.

## Binding sessions to channels

If you have configured a chat channel (Telegram / Discord / Slack / WeChat), you can pin a channel user's messages to a specific session:

```bash
openprogram sessions attach    # route a channel user's messages into a given session
openprogram sessions detach    # unbind, back to default routing
openprogram sessions aliases   # list all session-to-channel-user bindings
```

## Session operations in the web UI

Session history is stored as a git DAG, and branches are first-class. Hovering over any message reveals action buttons:

- **Copy** — copy the message content.
- **Retry from here** — regenerate everything after this message.
- **Edit message** — modify a message you sent and regenerate.
- **Branch to new session** — fork a new session from this message; the original thread is untouched.
- **Roll back to here** — reset the session to the state at this message.

After an edit or retry, the same slot holds multiple versions — switch with the previous / next version arrows beside the message. The branch menu in the top bar lists and switches the current session's branches.

The right sidebar is the session's DAG view: each node is a user message, an LLM call, or a function call. The view scrolls with the chat, and clicking a node scrolls the conversation to the corresponding message. Branches that touch files run in isolated git worktrees under the hood, so concurrent work on different branches never fights over the same source tree.
