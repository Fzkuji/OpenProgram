# Installing OpenProgram

## The model — read this first

**OpenProgram is the host. You install it once, then add agent *programs* into it.**

```
OpenProgram  (the host runtime — install this first, anywhere you like)
└── openprogram/functions/agentics/      ← programs live here, auto-discovered
    ├── GUI-Agent-Harness/               ← `gui_agent`     (openprogram programs install gui)
    ├── Research-Agent-Harness/          ← `research_agent`
    └── Wiki-Agent-Harness/              ← `wiki_agent`
```

A program dropped into `functions/agentics/` is **auto-registered** at launch
(`import_installed_programs()` imports its `agentics` sub-package, firing the
`@agentic_function` decorator) — so it shows up in the **web UI** and function
list with no extra wiring. Install order is therefore always: **OpenProgram
first, then the program(s).**

> ⚠️ `pip install openprogram` installs only the Python package — it does **not**
> build the web UI (needs `npm`), fetch the GUI agent's model weight, or warm the
> OCR models. **The install script below is the source of truth** — it does the
> whole job. Treat plain `pip install` as "part of the install", not all of it.

---

## One command (recommended)

Clone the host wherever you want it to live, then run the installer. `--gui`
also installs the GUI agent program into `functions/agentics/` and finishes its
setup (PyTorch, YOLO weight, OCR).

**macOS / Linux**
```bash
git clone https://github.com/Fzkuji/OpenProgram
cd OpenProgram
./scripts/install.sh --gui            # drop --gui for host only; --cuda cu121 for NVIDIA
```

**Windows (PowerShell)**
```powershell
git clone https://github.com/Fzkuji/OpenProgram
cd OpenProgram
.\scripts\install.ps1 -Gui            # drop -Gui for host only; -Cuda cu121 for NVIDIA
```

Then pick a provider and launch — `gui_agent` appears in the UI automatically:
```bash
openprogram providers login openai-codex     # or: export ANTHROPIC_API_KEY=sk-ant-...
openprogram web                               # -> http://localhost:18100
```

The installer is **idempotent** — re-run it any time to repair or update.

---

## What the installer does

| Step | Action | Notes |
|------|--------|-------|
| 1 | Verify / install **Python 3.11+, Node 20+, git** | macOS `brew` / Linux `apt`·`dnf`·`pacman` / Windows `winget`. Best-effort. |
| 2 | **Python env** | Active `venv`/conda if any, else creates `./.venv`. Override: `--python` / `-Python`. This is the "wherever you want" location. |
| 3 | **OpenProgram** editable install (`pip install -e .`) | The host + base deps. |
| 4 | **Web UI** — `npm install` in `web/` | Next.js frontend on **:18100**, backend on **:18109**. |
| 5 | **Ink TUI** — `npm install && npm run build` in `cli/` | POSIX only; Windows uses the Rich REPL. |
| 6 | `--gui` / `-Gui`: install the **GUI program** into `functions/agentics/` | Clones the harness in-tree (editable, auto-registers) and runs its asset setup → PyTorch + YOLO weight + EasyOCR. Delegates to the harness's own [installer](../openprogram/functions/agentics/GUI-Agent-Harness/scripts). |
| 7 | Optional extras behind flags | See [Optional extras](#optional-extras). |

---

## Flags

| Goal | `install.sh` (POSIX) | `install.ps1` (Windows) |
|------|----------------------|--------------------------|
| Also install the GUI agent | `--gui` | `-Gui` |
| CUDA build of PyTorch | `--cuda cu121` | `-Cuda cu121` |
| Force CPU torch (default) | `--cpu` | *(default)* |
| Target a specific interpreter | `--python /path/python` | `-Python C:\path\python.exe` |
| Pre-build the web bundle (`next build`) | `--build-web` | `-BuildWeb` |
| Skip the Ink TUI build | `--no-tui` | *(n/a)* |
| Browser tool (Playwright) | `--browser` | `-Browser` |
| Stealth browser (patchright + camoufox) | `--stealth` | `-Stealth` |
| `agent-browser` tool | `--agent-browser` | `-AgentBrowser` |
| Chat channels (Discord/Slack/WeChat) | `--channels` | `-Channels` |

---

## Adding agent programs

Programs always land in `functions/agentics/<Repo>/` and auto-register. Two ways:

**a) With the installer (recommended for source checkouts):** `./scripts/install.sh --gui`
clones the GUI harness in-tree **editable** (you can edit/commit it) and finishes
its setup. This is the developer-friendly path.

**b) On an existing host (end-user):** the built-in command clones + installs +
registers any catalogued program:
```bash
openprogram programs install gui          # or: research / wiki / all
openprogram programs available            # see install status
```
`programs install` does a **non-editable** install (deps to site-packages, code
runs in-tree). It does **not** fetch the GUI's YOLO weight or warm OCR — finish
those with the harness asset step (see its
[install guide](../openprogram/functions/agentics/GUI-Agent-Harness/docs/install.md)).

After either, restart the worker (or hit **Refresh** on the Functions page) and
`gui_agent` shows in the web UI. Any third-party harness works the same way:
[installing-harnesses.md](installing-harnesses.md).

---

## Optional extras

Off by default (large binaries). Add the flag to enable; the installer runs the
post-install step for you.

| Flag / extra | Installs | Post-install (automated) | Size |
|--------------|----------|--------------------------|------|
| `--browser` · `[browser]` | `playwright` | `playwright install chromium` | ~150 MB |
| `--stealth` · `[browser-stealth]` | `patchright`, `camoufox` | `patchright install chromium`, `camoufox fetch` | ~350 MB |
| `--agent-browser` · `[agent-browser]` | global npm `agent-browser` | `agent-browser install` | ~150 MB |
| `--channels` · `[channels]` | `discord.py`, `slack_sdk`, `qrcode` | *(set tokens in `~/.openprogram/config.json`)* | small |

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
| provider credential | any chat turn | `openprogram providers login` / env key | all | manual |
| Playwright / patchright / camoufox / agent-browser | browser tools | flags above | all | flag |

### GUI-Agent-Harness program (`--gui`)

| Item | Required for | How | Platform | Auto? |
|------|--------------|-----|----------|-------|
| PyTorch (+ torchvision) | YOLO / OCR | CPU wheel default; `--cuda cuXXX` | all | **auto** |
| harness Python deps | core | `pip install -e .[ocr]` (ultralytics, opencv, pynput, easyocr) | all | **auto** |
| **GPA YOLO weight** `model.pt` | element detection | `Salesforce/GPA-GUI-Detector` → `~/GPA-GUI-Detector/model.pt` | all | **auto** |
| EasyOCR models (en + ch_sim) | text detection | pre-warmed (`~/.EasyOCR/model`, ~300 MB) | Win/Linux | **auto** |
| `xclip` (+ wmctrl/xdotool/scrot) | clipboard, windows | `apt install …` | Linux | **auto** |
| Xcode CLT (Swift) | Apple Vision OCR | `xcode-select --install` | macOS | best-effort* |
| Screen Recording + Accessibility | screenshots, clicks | System Settings → Privacy | macOS | manual |
| Win32 + PowerShell clipboard | everything | built-in | Windows | n/a |

\* EasyOCR is installed as a cross-platform fallback, so the GUI agent works on
macOS without Xcode CLT — Apple Vision is just faster. Full GUI specifics:
[GUI-Agent-Harness/docs/install.md](../openprogram/functions/agentics/GUI-Agent-Harness/docs/install.md).

---

## Troubleshooting

- **`openprogram web` showed a page that won't load / only the backend came up.**
  The Next.js `node_modules` weren't installed. Re-run the installer, then open
  **http://localhost:18100** (not :18109).
- **`pip` can't reinstall: `WinError 32 … openprogram.exe is being used`.**
  Stop the running `openprogram web` / worker first, then re-run.
- **`gui_agent` doesn't appear in the UI.** Restart the worker (or Refresh the
  Functions page). Confirm it's registered: `openprogram programs available`.
- **NVIDIA GPU unused.** Default torch is the CPU build — re-run with `--cuda cu121`.
- **GPA weight didn't download** (offline): `hf download Salesforce/GPA-GUI-Detector model.pt --local-dir ~/GPA-GUI-Detector`.

---

## Manual / advanced

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .                                  # host
( cd web && npm install )                         # web UI
( cd cli && npm install && npm run build )        # TUI (POSIX)
# GUI program (editable, in-tree → auto-registers):
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -e "openprogram/functions/agentics/GUI-Agent-Harness[ocr]"
hf download Salesforce/GPA-GUI-Detector model.pt --local-dir ~/GPA-GUI-Detector
python -c "import easyocr; easyocr.Reader(['en','ch_sim'], gpu=False)"
```

Multi-repo local development (editing several harnesses side-by-side):
[troubleshooting.md → Local-development install](troubleshooting.md#local-development-install-multi-repo).
