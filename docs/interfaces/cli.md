# CLI

Call OpenProgram from scripts or other programs and get one reply per command.

## One-shot

```bash
openprogram --print "Summarize what the python files in this directory do"
```

Sends the prompt, prints the reply, exits. It does not enter the TUI and does not require a running worker (the call completes in-process). The conversation is written to the session store and can be revisited or continued later in the Web UI or TUI.

Note: `--print` only accepts the string in its argument; it does not read stdin. To put file contents into the prompt, use shell substitution:

```bash
openprogram --print "Review this code: $(cat main.py)"
```

## Resuming a specific session

```bash
openprogram sessions list                 # find the session id
openprogram --resume <session-id> --print "Given last time's conclusion, what is the next step"
```

`--resume` takes effect together with `--print`. For interactive resumption, pick the session with `/resume` inside the TUI instead (the flag is currently ignored when launching the interactive TUI); see [Terminal TUI](tui.md).

## Isolated environments

```bash
openprogram --profile ci --print "..."
```

`--profile <name>` switches config, sessions, and credentials wholesale to `~/.openprogram-<name>/`, keeping script environments from polluting your daily one. It can also be set via the `OPENPROGRAM_PROFILE` environment variable.

## Other scripting entry points

All non-chat subcommands can be scripted directly, most supporting `--json` output, for example:

```bash
openprogram sessions list
openprogram providers list --json
openprogram providers discover --json
openprogram status
openprogram programs run <name> --arg key=value   # run one agentic program
```

See `openprogram -h` for the full command list; each subcommand has its own `-h`.
