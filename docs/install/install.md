# Installation

## The model — read this first

**OpenProgram is the host. You install it once, then add agent *programs* into it.**

```
OpenProgram  (the host runtime — install this first, anywhere you like)
└── openprogram/functions/agentics/      ← programs live here, auto-discovered
    ├── GUI-Agent-Harness/               ← `gui_agent`      (clone in + run its installer)
    ├── Research-Agent-Harness/          ← `research_agent` (openprogram programs install research)
    └── Wiki-Agent-Harness/              ← `wiki_agent`     (openprogram programs install wiki)
```

A program dropped into `functions/agentics/` is **auto-registered** at launch
(`import_installed_programs()` imports its `agentics` sub-package, firing the
`@agentic_function` decorator) — so it shows up in the **web UI** and function
list with no extra wiring. Install order is therefore always: **OpenProgram
first, then the program(s).**

> ⚠️ Installing just the Python package is **not** the whole job — it doesn't
> build the web UI (needs `npm`), fetch the GUI agent's model weight, or warm the
> OCR models. **The install script below is the source of truth** — it does
> everything.

---

## One command (recommended)

**macOS / Linux**
```bash
curl -fsSL https://raw.githubusercontent.com/Fzkuji/OpenProgram/main/scripts/install.sh | bash
# from a checkout: ./scripts/install.sh   # everything · bare host: --minimal
```

**Windows (PowerShell)**
```powershell
iwr -useb https://raw.githubusercontent.com/Fzkuji/OpenProgram/main/scripts/install.ps1 | iex
# from a checkout: .\scripts\install.ps1   # everything · bare host: -Minimal
```

When not run from a checkout, the script first clones the repo to `~/OpenProgram`
(change with `--target DIR`), then hands off to the install. The default install
brings up **everything light** in the host: web UI (built), terminal UI, browser
tool + channels. Agent programs (GUI / Research / Wiki) are **not part of the
default install** — with a terminal attached the script shows a menu to pick
them, or add them later with `openprogram programs install <research|wiki|gui>`
(GUI downloads PyTorch), or via `openprogram setup` → programs. `--minimal`
installs a bare host instead.

Then just start it — the **first run walks you through provider setup**, then
opens the chat:
```bash
openprogram                                   # first run = guided provider setup, then chat
openprogram web                               # or the browser UI -> http://localhost:18100
```

The installer is **idempotent** — re-run it any time to repair or update.

---

## What the installer does

| Step | Action | Notes |
|------|--------|-------|
| 1 | Verify / install **Python 3.11+, Node 20+, git** | macOS `brew` / Linux `apt`·`dnf`·`pacman` / Windows `winget`. Best-effort. |
| 2 | **Python env** | Active `venv`/conda if any, else creates `./.venv`. Override: `--python` / `-Python`. This is the "wherever you want" location. |
| 3 | **OpenProgram** editable install (`pip install -e .`) | The host + base deps. |
| 4 | **Web UI** — `npm install && npm run build` in `web/` | Next.js frontend on **:18100**, backend on **:18109**. `--minimal` skips the build (the worker builds on first start). |
| 5 | **Ink TUI** — `npm install && npm run build` in `cli/` | POSIX only; Windows uses the Rich REPL. `--minimal` skips. |
| 6 | **Agent programs (opt-in)** — menu when a terminal is attached, or `--programs <research\|wiki\|gui\|all>` | **No program installs by default.** When selected: `research` / `wiki` are pure Python, cloned into `functions/agentics/` as in-tree git checkouts that auto-register (`research` needs nothing beyond openprogram; `wiki` adds Jinja2 + PyYAML); `gui` pulls PyTorch (~300 MB — the CPU wheel is auto-selected on GPU-less Linux; ~3 GB only on CUDA boxes). Add any of them later with `openprogram programs install <name>`. |
| 7 | **Browser tool + channels** | `pip install -e .[all]` + `playwright install chromium` (~150 MB). `--minimal` skips. Heavier stealth browsers / agent-browser stay opt-in — see [Extras](#extras). |

---

## Flags

The full flag matrix (`install.sh --help` prints it; the PowerShell flags are documented at the top of `install.ps1`):

| Flag (POSIX) | Flag (Windows) | Controls | Default |
|--------------|----------------|----------|---------|
| `--minimal` | `-Minimal` | Bare host: skip web build / TUI / programs / extras | off (everything light) |
| `--python /path/python` | `-Python C:\path\python.exe` | Target a specific Python interpreter | auto-detect (active venv/conda, else create `./.venv`) |
| `--stealth` | `-Stealth` | Also install stealth browsers (patchright + camoufox, ~350 MB) | off |
| `--agent-browser` | `-AgentBrowser` | Also install the global npm `agent-browser` (~150 MB) | off |
| `--programs <gui\|research\|wiki\|all>` | `-Programs <…>` | Install agent programs non-interactively during the install (repeatable or comma-separated) | none (pick in the first-run wizard) |
| `--target DIR` | `-Target DIR` | Where to clone when run from the web | `~/OpenProgram` (Win: `$HOME\OpenProgram`) |
| `--yes` / `-y` | `-Yes` | Skip all prompts, take every default | off (menu when a terminal is attached) |

Explicit CUDA/CPU PyTorch for the GUI harness: run its own installer after the
host install — `openprogram/functions/agentics/GUI-Agent-Harness/scripts/install.sh --cuda cu124`.

### Non-interactive / AI-agent installs

For agent-driven installs, **no special flags are needed**: the `curl … | bash`
one-liner already runs unattended. Without a terminal (piped, CI) it takes the
defaults automatically; even with a terminal, every `/dev/tty` read has a
60-second timeout that falls back to the default (printing a one-line
`(no input in 60s — using default)`) — so **no prompt can hang forever**. Change
the timeout with `OPENPROGRAM_PROMPT_TIMEOUT=<seconds>`.

To take the defaults immediately instead of waiting out the timeout, add
`--yes` / `-y`; to also install agent programs non-interactively, add
`--programs all` (or `gui` / `research` / `wiki`). These **environment
variables** are equivalent to `--yes` — if any matches, all defaults are taken
and no prompt is shown:

| Environment variable | Triggers when |
|----------------------|---------------|
| `CI` | non-empty (the common CI convention — GitHub Actions etc.) |
| `DEBIAN_FRONTEND` | equals `noninteractive` (the Debian/Ubuntu convention) |
| `OPENPROGRAM_INSTALL_YES` | non-empty (this project's own switch) |

Fully non-interactive, with agent programs included, in one command:

```bash
curl -fsSL https://raw.githubusercontent.com/Fzkuji/OpenProgram/main/scripts/install.sh | bash -s -- -y --programs all
```

> Windows' `Read-Host` has no timeout mechanism, so `install.ps1` prompts do
> **not** fall back to defaults on their own — on Windows an agent must pass
> `-Yes` or set one of the environment variables above.

---

## Adding agent programs

Programs always land in `functions/agentics/<Repo>/` and auto-register on the next
start. The **universal** way — works for the bundled harnesses *and your own* —
is to clone a repo into that folder and run its installer:

```bash
cd openprogram/functions/agentics
git clone <harness-repo>
cd <Harness>
./scripts/install.sh          # if it ships one (Windows: .\scripts\install.ps1)
```

The **GUI agent** has native deps (PyTorch, detector weight, OCR), so it ships its
own per-platform installer — use it via the steps above; full guide in its
[install section](https://github.com/Fzkuji/OpenProgram/tree/main/openprogram/functions/agentics/GUI-Agent-Harness#1-install).
(When GUI is opted in — checked in the menu, or `--programs gui`/`all` — the install script clones it and pulls PyTorch; run the harness's own installer afterwards for its asset setup or an explicit CUDA/CPU torch.)

For the bundled harnesses there's a one-line shortcut that clones, installs,
and registers them for you:
```bash
openprogram programs install research     # or: wiki / gui / all
openprogram programs available            # see install status
```
`programs install` clones the repo and pip-installs its declared deps
(**non-editable**: deps go to site-packages, the code runs in-tree). For `gui`
that includes PyTorch, but **not** native assets like the YOLO weight or the
OCR warm-up — run the GUI harness's own installer (above) for those.

After any of these, restart the worker (or hit **Refresh** on the Functions page)
and the program shows in the web UI. Third-party harnesses install the same way —
`openprogram programs install <git-url | owner/repo>`; details:
[installing-harnesses.md](../capabilities/installing-harnesses.md).

---

## Extras

The **browser tool + chat channels install by default** (the `[all]` extra), and
the installer fetches the Playwright Chromium binary for you — nothing to opt into.
Pass `--minimal` / `-Minimal` to skip them (e.g. CI / air-gapped / bandwidth-limited).

| Default extra | Installs | Post-install (automated) | Size |
|---------------|----------|--------------------------|------|
| browser (`[browser]`) | `playwright` | `playwright install chromium` | ~150 MB |
| channels (`[channels]`) | `discord.py`, `slack_sdk`, `qrcode` | *(set tokens in `~/.openprogram/config.json`)* | small |

Heavier, still opt-in (add the flag):

| Flag / extra | Installs | Post-install (automated) | Size |
|--------------|----------|--------------------------|------|
| `--stealth` · `[browser-stealth]` | `patchright`, `camoufox` | `patchright install chromium`, `camoufox fetch` | ~350 MB |
| `--agent-browser` · `[agent-browser]` | global npm `agent-browser` | `agent-browser install` | ~150 MB |

Provider SDKs (`anthropic`, `openai`, `google-genai`) ship in the base install —
no extra needed.

---

## Providers / credentials

At least one provider is required before any chat turn:
```bash
openprogram providers login openai-codex      # ChatGPT subscription (recommended)
openprogram providers login anthropic          # Claude
export ANTHROPIC_API_KEY=sk-ant-...             # …or an API key (Windows: $env:ANTHROPIC_API_KEY="...")
```
Auto-adopts an installed Claude Code / Codex / Gemini CLI. Check with `openprogram doctor`.

---

## Ports

| Port | Service | Notes |
|------|---------|-------|
| **18100** | Next.js **frontend** — open this | `http://localhost:18100` |
| **18109** | FastAPI **backend** (API + WebSocket) | proxied by the frontend; no HTML pages |

Change with `openprogram ports --backend <p> --frontend <p>`.

---

## Full dependency matrix

Everything beyond `pip`. The installer handles every "auto" row.

### Host (OpenProgram)

| Item | Required for | How | Platform | Auto? |
|------|--------------|-----|----------|-------|
| Python ≥ 3.11 | everything | system / pyenv / conda | all | check |
| Node.js ≥ 20 + npm | web UI, TUI | nodejs.org / pkg mgr | all | install |
| git | sessions are git repos | pkg mgr | all | install |
| `web/node_modules` | web UI (:18100) | `npm install` in `web/` | all | **auto** |
| `cli/` Ink bundle | TUI | `npm install && npm run build` in `cli/` | macOS/Linux | **auto** |
| provider credential | any chat turn | `openprogram providers login` (or settings UI) | all | manual |
| Playwright / patchright / camoufox / agent-browser | browser tools | flags above | all | flag |

### GUI-Agent-Harness program (opt-in — once selected; see [Adding agent programs](#adding-agent-programs))

| Item | Required for | How | Platform | Auto? |
|------|--------------|-----|----------|-------|
| PyTorch (+ torchvision) | YOLO / OCR | pip resolves the default build; the harness's own installer auto-detects NVIDIA GPU → CUDA (`--cpu` / `--cuda cuXXX` to force) | all | **auto** |
| harness Python deps | core | `pip install -e .[ocr]` (ultralytics, opencv, pynput, easyocr) | all | **auto** |
| **GPA YOLO weight** `model.pt` | element detection | `Salesforce/GPA-GUI-Detector` → `~/GPA-GUI-Detector/model.pt` | all | **auto** |
| EasyOCR models (en + ch_sim) | text detection | pre-warmed (`~/.EasyOCR/model`, ~300 MB) | Win/Linux | **auto** |
| `xclip` (+ wmctrl/xdotool/scrot) | clipboard, windows | `apt install …` | Linux | **auto** |
| Xcode CLT (Swift) | Apple Vision OCR | `xcode-select --install` | macOS | best-effort* |
| Screen Recording + Accessibility | screenshots, clicks | System Settings → Privacy | macOS | manual |
| Win32 + PowerShell clipboard | everything | built-in | Windows | n/a |

\* EasyOCR is installed as a cross-platform fallback, so the GUI agent works on
macOS without Xcode CLT — Apple Vision is just faster. Full GUI specifics:
[GUI-Agent-Harness/docs/install.md](https://github.com/Fzkuji/OpenProgram/blob/main/openprogram/functions/agentics/GUI-Agent-Harness/docs/install.md).

---

## Troubleshooting

- **`openprogram web` showed a page that won't load / only the backend came up.**
  The Next.js `node_modules` weren't installed. Re-run the installer, then open
  **http://localhost:18100** (not :18109).
- **`pip` can't reinstall: `WinError 32 … openprogram.exe is being used`.**
  Stop the running `openprogram web` / worker first, then re-run.
- **`gui_agent` doesn't appear in the UI.** Restart the worker (or Refresh the
  Functions page). Confirm it's registered: `openprogram programs available`.
- **NVIDIA GPU unused.** The installer auto-detects it; if it picked CPU (no driver at install time, or you passed `--cpu`): `pip uninstall -y torch torchvision`, then re-run the installer.
- **GPA weight didn't download** (offline): `hf download Salesforce/GPA-GUI-Detector model.pt --local-dir ~/GPA-GUI-Detector`.

---

## Manual / advanced

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .                                  # host
( cd web && npm install )                         # web UI
( cd cli && npm install && npm run build )         # TUI (POSIX)
# GUI program (editable, in-tree → auto-registers):
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -e "openprogram/functions/agentics/GUI-Agent-Harness[ocr]"
hf download Salesforce/GPA-GUI-Detector model.pt --local-dir ~/GPA-GUI-Detector
python -c "import easyocr; easyocr.Reader(['en','ch_sim'], gpu=False)"
```

Multi-repo local development (editing several harnesses side-by-side):
[troubleshooting.md → Local-development install](../server/troubleshooting.md#local-development-install-multi-repo).
