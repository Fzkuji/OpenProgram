# Harnesses: install, manage, write your own

A **harness** (an *agentic program*) is a self-contained git repo of
agentic functions — OpenProgram discovers it under
`openprogram/functions/agentics/` and its functions register like
built-ins. This is a **general mechanism**: the first-party programs
(gui / research / wiki) and any third-party repo install the exact same
way. Cross-platform (macOS / Linux / Windows); no symlinks required.

> **Where the agent reads this:** this file is the canonical procedure.
> When a user asks to install a harness the agent doesn't have, follow
> the steps below — they're written to be executed step by step.

## TL;DR

```bash
# First-party programs — by name:
openprogram programs install research      # light (no extra deps)
openprogram programs install gui           # heavy (pulls torch/opencv)

# ANY third-party harness — same command, by git source:
openprogram programs install https://github.com/<owner>/<Harness-Name>
openprogram programs install <owner>/<Harness-Name>     # GitHub shorthand

# Manage:
openprogram programs available             # status, incl. third-party
openprogram programs uninstall research    # first-party: by name
openprogram programs uninstall <Harness-Name>   # third-party: by dir name
openprogram programs install <ref> --upgrade    # git pull + re-resolve deps

# …restart OpenProgram. Done — the functions self-register.
```

---

# Part 1 — Using harnesses

## What `programs install` does

The same four steps for first-party and third-party:

1. **Shallow-clone** the repo into
   `openprogram/functions/agentics/<Repo-Name>/` — a real, editable
   directory (not site-packages). The clone is git-ignored by
   OpenProgram, so it stays an independent checkout you can `git pull`
   or edit in place.
2. **Install the harness's own declared dependencies** — the harness is
   self-describing: its `pyproject.toml`/`setup.py` (preferred) or
   `requirements.txt` is installed. OpenProgram carries no per-harness
   dependency lists.
3. **Verify the contract** — the clone must contain a package with
   `agentics/__init__.py` (see Part 2). A repo that doesn't match is
   reported and will simply not register; it never breaks the load.
4. On the next launch the registry imports `<package>.agentics`, the
   `@agentic_function` decorators fire, and the functions appear in
   chat / the Functions page / `openprogram programs run`.

Guard rails: `install` refuses to touch an existing **dev symlink**
(that's yours, see below) or a same-named directory that isn't a git
clone. `uninstall` on a symlink removes only the link — never the
checkout it points to.

## First-party programs (gui / research / wiki)

| Program | Install | Notes |
|---|---|---|
| [Research Agent](https://github.com/Fzkuji/Research-Agent-Harness) | `openprogram programs install research` | no extra deps |
| [Wiki Agent](https://github.com/Fzkuji/Wiki-Agent-Harness) | `openprogram programs install wiki` | Jinja2 + PyYAML (tiny) |
| [GUI Agent](https://github.com/Fzkuji/GUI-Agent-Harness) | `openprogram programs install gui` | heavy: PyTorch via ultralytics + OpenCV. On GPU-less Linux the CPU torch wheel (~300 MB) is auto-selected instead of the ~3 GB CUDA build. |

`openprogram programs install all` installs the three; the first-run
setup wizard's "Agent programs" step offers the same choice
interactively.

> **GUI agent — one extra step.** Beyond its pip deps, `gui_agent` needs
> a YOLO detector weight + OCR models that aren't on PyPI. After the
> install, run the harness's own installer to fetch them (it skips the
> host since you already have it):
> `openprogram/functions/agentics/GUI-Agent-Harness/scripts/install.sh --no-host`
> (Windows: `…\scripts\install.ps1 -NoHost`). See the
> [GUI install guide](https://github.com/Fzkuji/GUI-Agent-Harness#1-install).

## Third-party harnesses

Anyone's harness repo installs with the same command — no catalogue
edit, no registration step anywhere:

```bash
openprogram programs install https://github.com/<owner>/<Harness-Name>
openprogram programs install <owner>/<Harness-Name>   # GitHub shorthand
openprogram programs install file:///path/to/checkout # local git source
```

`openprogram programs available` lists installed third-party harnesses
with their contract status; `openprogram programs uninstall
<Harness-Name>` removes one by its clone-dir name.

<details>
<summary>Manual equivalent (mirror / no GitHub access)</summary>

`<AGENTICS>` is OpenProgram's bundled-functions folder:

```bash
python -c "import openprogram,os;print(os.path.join(os.path.dirname(openprogram.__file__),'functions','agentics'))"
```

```bash
git clone <repo-url> "<AGENTICS>/<Harness-Name>"
pip install "<AGENTICS>/<Harness-Name>"        # or its requirements.txt
# restart OpenProgram
```

Auto-discovery picks up any directory in `<AGENTICS>` that satisfies the
contract — that's all the install command automates.

</details>

## Developer setup (work on a harness you're writing)

Symlink your working checkout instead of cloning a copy:

```bash
ln -s /path/to/your/Harness-Checkout "<AGENTICS>/Harness-Checkout"
```

Edits take effect on the next restart; `programs install` will refuse to
overwrite the link, and `programs uninstall <name>` removes only the
link. (Windows note: symlinks need developer mode — cloning a real
directory is the supported path there.)

## Verify an install

```bash
openprogram programs available     # install status (first- and third-party)
openprogram programs list          # all registered functions
```

To see why a present-but-broken harness didn't load:

```bash
OPENPROGRAM_DEBUG_REGISTRY=1 openprogram programs list
```
(Windows PowerShell: `$env:OPENPROGRAM_DEBUG_REGISTRY=1; openprogram programs list`)

Then use it — the harness's functions are callable like any built-in
(in chat, or `openprogram programs run <fn> key=value`).

## Platform notes

- **Base install is one command, every OS:** clone OpenProgram and run
  `./scripts/install.sh` (Windows: `.\scripts\install.ps1`).
- **No symlinks needed** — cloning a real directory into `<AGENTICS>` is
  the supported path, so there's no Windows admin/developer-mode hurdle.
- **A harness can still be platform-specific in its own code** (e.g. a
  desktop-GUI harness may only implement macOS / Linux backends).
  Installing always works; whether every function *runs* on your OS is
  the harness's concern — check its README.
- **Encoding / paths:** OpenProgram's own tooling is UTF-8 and
  `os.path`-based throughout; a well-behaved harness should be too.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Harness functions don't appear after restart | Folder doesn't match the contract — confirm `<pkg>/agentics/__init__.py` exists and exports `AGENTIC_FUNCTIONS`. Run with `OPENPROGRAM_DEBUG_REGISTRY=1`. |
| `[!] … no package with an agentics/__init__.py was found` at install | Same as above — the repo doesn't satisfy the contract (Part 2). |
| `ModuleNotFoundError` for the harness's own deps | The dep install step failed — `pip install` the clone (or its requirements.txt) and check the error. |
| Imports inside the harness fail (`from <pkg>.x import y`) | The package dir isn't named like the import root, or a missing `__init__.py`. The package folder name must equal the import name. |
| `[skip] … is a dev symlink` on install | Intentional: the installer never touches your linked checkout. Remove the link first if you really want a clone. |
| A function loads but errors when *run* on Windows | The harness's own code is platform-specific — its concern, not the install's. See its README. |

---

# Part 2 — Writing your own installable harness

Any repo that satisfies one layout contract becomes a one-command
install for every OpenProgram user.

## The contract

```
<Harness-Name>/                      ← the repo (any name)
├── pyproject.toml                   ← declares the harness's OWN deps only
└── <package>/                       ← an importable package (ascii name)
    ├── __init__.py                  ← kept dependency-light
    └── agentics/
        └── __init__.py              ← exposes AGENTIC_FUNCTIONS = [...]
```

The registration entry point is the **`agentics` sub-package** — at
startup OpenProgram imports `<package>.agentics`; that import fires the
`@agentic_function` decorators, which self-register into the shared
registry. The harness root may also vendor other packages — discovery
finds the one with an `agentics/` sub-package and puts the harness root
on `sys.path`, so the harness's own absolute imports
(`from <package>.foo import bar`) resolve.

## Minimal working template

```python
# <package>/agentics/__init__.py
from openprogram.agentic_programming.function import agentic_function


@agentic_function
def my_tool(text: str = "") -> str:
    "One line: what this does (shown in catalogs)."
    return text.upper()


AGENTIC_FUNCTIONS = [my_tool]
```

```python
# <package>/__init__.py
"""My harness — keep this import-light (see hard rule 2)."""
```

```toml
# pyproject.toml
[project]
name = "my-harness"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = []          # the harness's own deps — NEVER openprogram
```

That's a complete installable harness.

## Two hard rules

1. **Never declare `openprogram` as a dependency** (in `pyproject.toml`
   *or* `requirements.txt`). The harness runs inside an existing
   OpenProgram install; a declared `openprogram @ git+…` would make pip
   re-install the host from git, clobbering the user's local (often
   editable) install.
2. **Keep the top-level `<package>/__init__.py` dependency-light, and
   guard heavy imports in `agentics/__init__.py`.** Discovery imports
   `<package>.agentics` on every startup, including on machines that
   haven't installed your optional/heavy deps — a top-level import of
   cv2/torch/etc. would break the whole registry load. Lazy-import heavy
   modules inside function bodies, and guard the entry import:

   ```python
   # agentics/__init__.py — deps-less machines must not break the load
   try:
       from my_package.main import my_tool
       AGENTIC_FUNCTIONS = [my_tool]
   except ImportError:
       AGENTIC_FUNCTIONS = []
   ```

The three first-party harnesses follow this exact shape — read any of
them as a working template.

## Test locally before publishing

The install command accepts a `file://` source, so the full user flow is
testable against your local checkout:

```bash
cd /path/to/My-Harness && git add -A && git commit -m wip
openprogram programs install file:///path/to/My-Harness
openprogram programs available        # should show: My-Harness [ok] (package: …)
OPENPROGRAM_DEBUG_REGISTRY=1 openprogram programs list   # functions present?
openprogram programs run my_tool text=hello              # smoke test
openprogram programs uninstall My-Harness                # clean up
```

Checklist before you publish:

- [ ] `<package>/agentics/__init__.py` exposes `AGENTIC_FUNCTIONS`
- [ ] no `openprogram` in pyproject/requirements (hard rule 1)
- [ ] `python -c "import <package>.agentics"` succeeds in a bare venv
      with only OpenProgram installed (hard rule 2)
- [ ] `file://` install round-trip above passes

## Publish

Push to GitHub. Users install with:

```bash
openprogram programs install <owner>/<Harness-Name>
```

Nothing to register anywhere — the repo URL *is* the distribution.
