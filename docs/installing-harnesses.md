# Installing harnesses (agentic programs)

How to add a self-contained **agentic program** — first-party (gui /
research / wiki) or any **third-party** one — so OpenProgram picks it up
and the agent can call its functions. Cross-platform (macOS / Linux /
Windows); no symlinks required.

> **Where the agent reads this:** this file is the canonical procedure.
> When a user asks to install a harness the agent doesn't have, follow
> the steps below — they're written to be executed step by step.

## TL;DR

```bash
# First-party programs — one command, by name:
openprogram programs install research      # light (no extra deps)
openprogram programs install gui           # heavy (pulls torch/opencv)
openprogram programs available             # list + install status

# Any third-party harness — just clone it into the agentics folder:
git clone <repo-url> "<AGENTICS>/<Harness-Name>"
pip install -r "<AGENTICS>/<Harness-Name>/requirements.txt"   # if it has one
# …restart OpenProgram. Done — its functions self-register.
```

`<AGENTICS>` is OpenProgram's bundled-functions folder. Find it with:

```bash
python -c "import openprogram,os;print(os.path.join(os.path.dirname(openprogram.__file__),'functions','agentics'))"
```

## Why this works: drop-in directories

OpenProgram loads functions from `openprogram/functions/agentics/`. At
startup it walks that folder and treats **every sub-directory** (a real
directory from `git clone`, *or* a symlink) as a candidate harness — so
installing one is just "put its folder there". No registry edit, no
symlink, no `pip install` into site-packages.

A harness is recognized when its folder contains a Python package that
exposes its agentic functions (the **contract** below). If it doesn't
match, it's silently ignored — dropping a random folder never breaks the
load.

## The contract a harness must satisfy

Inside the cloned folder there must be an importable package whose
`agentics/__init__.py` exposes the entry points:

```
<Harness-Name>/                      ← what you clone into agentics/
└── <package>/                       ← an importable package (ascii name)
    ├── __init__.py
    └── agentics/
        └── __init__.py              ← exposes AGENTIC_FUNCTIONS = [...]
```

`agentics/__init__.py` imports the decorated callables; importing it
fires the `@agentic_function` decorators, which self-register into the
shared tool registry:

```python
# <package>/agentics/__init__.py
from openprogram.agentic_programming.function import agentic_function

@agentic_function(name="my_tool")
def my_tool(x: str = "") -> str:
    "What it does."
    ...

AGENTIC_FUNCTIONS = [my_tool]
```

The harness folder may also vendor other packages (deps, a `desktop_env/`,
etc.) — OpenProgram finds the *one* package that has an `agentics/`
sub-package and puts the harness root on `sys.path` so the harness's own
absolute imports (`from <package>.foo import bar`) resolve.

## Step-by-step: install a third-party harness

1. **Locate the agentics folder** (the one-liner above). Call it `<AGENTICS>`.

2. **Clone the harness into it**, keeping the repo's own folder name:

   ```bash
   git clone https://github.com/<owner>/<Harness-Name> "<AGENTICS>/<Harness-Name>"
   ```

   (A real directory is fine — no symlink. Works the same on Windows.)

3. **Install the harness's dependencies**, if it declares any:

   ```bash
   # whichever the repo provides:
   pip install -r "<AGENTICS>/<Harness-Name>/requirements.txt"
   pip install -e "<AGENTICS>/<Harness-Name>"        # if it ships a pyproject/setup
   ```

   Light harnesses that only use OpenProgram itself need nothing here.

4. **Restart OpenProgram** (the registry loads at startup). Verify:

   ```bash
   openprogram programs available     # first-party status
   openprogram programs list          # all registered functions — your
                                      # harness's functions should appear
   ```

   To see why a present-but-broken harness didn't load:

   ```bash
   OPENPROGRAM_DEBUG_REGISTRY=1 openprogram programs list
   ```
   (Windows PowerShell: `$env:OPENPROGRAM_DEBUG_REGISTRY=1; openprogram programs list`)

5. **Use it** — the harness's functions are now callable like any built-in
   (in chat, or `openprogram programs run <fn> key=value`).

## First-party programs (gui / research / wiki)

These live in their own repos and are installed by name — the command
does the clone-into-`<AGENTICS>` for you, pinned and dependency-aware:

```bash
openprogram programs install research      # research_agent — light
openprogram programs install gui           # gui_agent — heavy: clones it
                                           #   then pip-installs the
                                           #   harness's own deps
                                           #   (torch via ultralytics, opencv)
openprogram programs install all
openprogram programs uninstall <name>
openprogram programs install <name> --upgrade   # git pull the clone
```

`research` / `wiki` carry no extra deps; `gui` is heavy (native ML deps)
and is the reason these aren't bundled into the base `pip install
openprogram` — the core stays light and the heavy program is opt-in.

## Platform notes

- **Base install is one step, every OS:** `pip install openprogram`, then
  the `openprogram` command works. Harnesses are the only thing installed
  separately, and only when wanted.
- **No symlinks needed** — cloning a real directory into `<AGENTICS>` is
  the supported path, so there's no Windows admin/developer-mode hurdle.
- **A harness can still be platform-specific in its own code** (e.g. a
  desktop-GUI harness that drives the screen may only implement macOS /
  Linux backends). Installing it always works; whether every function
  *runs* on your OS depends on that harness's own implementation — check
  its README. (This procedure only covers install/registration.)
- **Encoding / paths:** OpenProgram's own tooling is UTF-8 and
  `os.path`-based throughout; a well-behaved harness should be too.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Harness functions don't appear after restart | Folder doesn't match the contract — confirm `<pkg>/agentics/__init__.py` exists and exports `AGENTIC_FUNCTIONS`. Run with `OPENPROGRAM_DEBUG_REGISTRY=1`. |
| `ModuleNotFoundError` for the harness's own deps | Step 3 wasn't run / a dep failed to install. Install the repo's requirements. |
| Imports inside the harness fail (`from <pkg>.x import y`) | The package dir isn't named like the import root, or there's no `__init__.py` at the package root. The package folder name must equal the import name. |
| A function loads but errors when *run* on Windows | The harness's own code is platform-specific — that's the harness's concern, not the install. See its README. |
