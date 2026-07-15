# Getting Started

This page takes you through five minutes of setup: install, connect an LLM provider, open the interfaces, send your first message, and install your first ready-made agent program.

## Step 1: Install

The one-command install script sets up the Python package, web UI, terminal UI, browser tools, and channels:

```bash
git clone https://github.com/Fzkuji/OpenProgram.git && cd OpenProgram
./scripts/install.sh              # macOS/Linux   ·   Windows:  .\scripts\install.ps1
```

Requires Python ≥ 3.11, Node ≥ 20, and git (the script makes a best effort to install what's missing). The script is idempotent — re-run it any time. Agent programs (GUI / Research / Wiki) are not installed by default; pick them in the interactive menu during install, or add them later (see Step 5). Full flags and the dependency matrix: [Install](../install/install.md).

## Step 2: First run — connect a provider

```bash
openprogram
```

The first run enters a setup wizard that walks you through provider configuration — import credentials from a logged-in Claude Code / Codex / Gemini CLI, or paste an API key — then drops you straight into the terminal chat. Re-run the wizard any time with `openprogram setup`.

You can also skip the wizard with environment variables:

```bash
export ANTHROPIC_API_KEY=sk-ant-...     # Claude
export OPENAI_API_KEY=sk-...            # GPT
export GOOGLE_API_KEY=...               # Gemini
```

Sanity check: `openprogram providers` lists the detected credentials.

## Step 3: Open the web UI

```bash
openprogram web
```

This starts the backend and the Next.js frontend together and opens your browser at **http://localhost:18100** (not :18109 — that's the backend API port). Change ports with `openprogram ports --backend <p> --frontend <p>`.

## Step 4: Send your first message

Type directly into the terminal chat or the web input box. To quickly verify from the command line:

```bash
openprogram --print "Introduce yourself in one sentence"
```

It sends one message, prints the reply, and exits. Resume an earlier session with `openprogram --resume <session_id>` — ids come from `openprogram sessions list` or the web sidebar.

## Step 5: Install a ready-made agent program

OpenProgram is the host; agent programs installed into it show up in the web UI and the function list:

```bash
openprogram programs install research     # or wiki / gui
openprogram programs available            # check install status
```

`research` / `wiki` are pure Python and install quickly; `gui` downloads PyTorch and model weights, which is much larger. After installing, run `openprogram restart` (or hit Refresh on the Functions page) and the program appears in the UI.

## Next steps

- [Models & providers](../models/README.md) — how each provider connects, multi-account, key rotation
- [Agentic Programming](../capabilities/agentic-programming/README.md) — write your own `@agentic_function`
- [Interfaces](../interfaces/README.md) — terminal TUI, web UI, and channels
- [Daily use](daily-use.md) — session management, branching, and rollback
