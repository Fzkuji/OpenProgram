# Getting Started

This page takes you through five minutes of setup: install, connect an LLM provider, open the interfaces, send your first message, and install your first ready-made agent program.

## Step 1: Install

Install the complete release runtime with its managed Python on macOS or Linux:

```bash
curl -fsSL https://openprogram.io/install | sh
```

On Windows x86_64, use PowerShell:

```powershell
irm https://openprogram.io/install.ps1 | iex
```

The installer supplies its own Python and includes the Web UI, so Node.js is not a runtime requirement. Git is required for session and Memory history; `openprogram doctor` reports whether it is available. macOS Desktop uses the release DMG. Windows Desktop uses a signed `win-x64.exe` only when it is attached to that published release; otherwise use the complete Windows CLI/server runtime and browser UI. Windows sandbox execution is separate. Platform scope and development-checkout instructions are in [Install](../install/install.md).

## Step 2: First run — connect a provider

```bash
openprogram
```

The first run enters a setup wizard that walks you through provider configuration — import credentials from a logged-in Claude Code / Codex / Gemini CLI, or paste an API key — then drops you straight into the terminal chat. Re-run the wizard any time with `openprogram setup`.

You can also skip the wizard with environment variables:

```bash
export ANTHROPIC_API_KEY=sk-ant-...     # Claude
export OPENAI_API_KEY=sk-...            # GPT
export GEMINI_API_KEY=...               # Gemini (GOOGLE_API_KEY also works)
```

Sanity check: `openprogram providers` lists the detected credentials.

## Step 3: Open the web UI

```bash
openprogram web
```

This starts the background worker and opens your browser at **http://localhost:18100** — a single port serving the web UI, the API, and the WebSocket. Change it with `openprogram ports --port <p>`.

## Step 4: Send your first message

Type directly into the terminal chat or the web input box. To quickly verify from the command line:

```bash
openprogram --print "Introduce yourself in one sentence"
```

It sends one message, prints the reply, and exits. Resume an earlier session with `openprogram --resume <session_id>` — ids come from `openprogram sessions list` or the web sidebar.

## Step 5: Use the included agent programs

Every release includes the GUI, Research, and Wiki Programs. They appear in the Web UI and function list without a separate installation step. Use `openprogram programs available` to inspect their registration status.

The Program installer is reserved for third-party Programs and developer source overlays. It does not define a reduced or expanded end-user edition.

## Next steps

- [Models & providers](../models/README.md) — how each provider connects, multi-account, key rotation
- [Agentic Programming](../capabilities/agentic-programming/README.md) — write your own `@agentic_function`
- [Interfaces](../interfaces/README.md) — terminal TUI, web UI, and channels
- [Daily use](daily-use.md) — session management, branching, and rollback
