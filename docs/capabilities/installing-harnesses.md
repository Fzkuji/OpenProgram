# Harnesses

A **harness** (an *agentic program*) is a self-contained git repo of
agentic functions. Every supported release already contains the GUI, Research,
and Wiki first-party Program packages and their supported runtime assets.
The GUI package is installed without PyTorch, OpenCV, or EasyOCR, as described
below. `openprogram programs install` is for additional third-party Programs
or developer source overlays; it is not a step required to complete a release
installation. The immutable product runtime rejects in-place Program install,
upgrade, and uninstall operations.

> **Where the agent reads this:** this file is the canonical procedure.
> When a user asks to install a harness the agent doesn't have, follow
> the steps below — they're written to be executed step by step.

## TL;DR

```bash
# First-party Programs are already present:
openprogram programs available

# Add a third-party harness in a mutable extension/development environment:
openprogram programs install https://github.com/<owner>/<Harness-Name>
openprogram programs install <owner>/<Harness-Name>     # GitHub shorthand

# Manage:
openprogram programs available             # status, incl. third-party
openprogram programs uninstall <Harness-Name>   # third-party: by dir name
openprogram programs install <ref> --upgrade    # git pull + re-resolve deps

# …restart OpenProgram. Done — the functions self-register.
```

---

# Part 1 — Using harnesses

## What `programs install` does

For a third-party Program or developer source overlay, the command performs four steps:

1. **Shallow-clone** the repo into
   `openprogram/programs/applications/<Repo-Name>/` — a real, editable
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
4. **Record the owner-approved source.** On the next launch the registry imports
   only recorded `<package>.agentics` packages, the
   `@agentic_function` decorators fire, and the functions appear in
   chat / the Programs page / `openprogram programs run`.

Guard rails: for an existing **dev symlink**, `install` verifies the harness
contract and records the link without modifying its target. It refuses a
same-named non-git directory. `uninstall` on a symlink removes only the link,
never the checkout it points to.

## First-party Programs (gui / research / wiki)

| Program | Release status | Notes |
|---|---|---|
| [Research Agent](https://github.com/Fzkuji/Research-Agent-Harness) | Included | The product manifest records the fixed source commit; the builder installs the declared PDF dependencies, and the runtime manifest records the resolved distributions. |
| [Wiki Agent](https://github.com/Fzkuji/Wiki-Agent-Harness) | Included | The product manifest records the fixed source commit; the builder installs the declared dependencies, and the runtime manifest records the resolved distributions. |
| [GUI Agent](https://github.com/Fzkuji/GUI-Agent-Harness) | Included | The Program is registered and the GPA detector weight is shipped. PyTorch, OpenCV, and EasyOCR are not in the product runtime. |

Release users do not run `programs install all`, a first-run Program wizard,
or the GUI harness asset installer. Developers may replace a first-party
Program with an editable checkout or configure a different OCR/browser backend;
those overlays add development behavior without changing the product manifest.

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

`<APPLICATIONS>` is OpenProgram's owner-recorded external Program folder:

```bash
python -c "import openprogram,os;print(os.path.join(os.path.dirname(openprogram.__file__),'programs','applications'))"
```

```bash
git clone <repo-url> /path/to/Harness-Name
openprogram programs install file:///path/to/Harness-Name
# restart OpenProgram
```

Auto-discovery picks up any recorded directory in `<APPLICATIONS>` that satisfies the
contract — that's all the install command automates.

</details>

## Developer setup (work on a harness you're writing)

Symlink your working checkout instead of cloning a copy:

```bash
ln -s /path/to/your/Harness-Checkout "<APPLICATIONS>/Harness-Checkout"
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
(in chat, or `openprogram programs run <fn> -a key=value`).

## Platform notes

- **Supported CLI/server release hosts are macOS, Linux, and Windows x86_64/arm64.**
  Windows Desktop is supported on both architectures. Sandboxed command
  execution on Windows uses optional WSL2 plus bubblewrap.
- **These Program commands require a mutable environment.** They work in a
  source-development checkout and may be used by a CLI release only when its
  release notes explicitly support Program mutation. Packaged desktop builds
  refuse the mutation commands until Programs have isolated external
  environments.
- **No symlinks are required** in a supported mutable environment: the
  installer records a real checkout under `<APPLICATIONS>` by default.
- **A harness can still be platform-specific in its own code** (e.g. a
  desktop-GUI harness may only implement macOS / Linux backends).
  Whether installation and every function run on a supported host depends on
  the harness's declared dependencies and platform support; check its README.
- **Encoding / paths:** OpenProgram's own tooling is UTF-8 and
  `os.path`-based throughout; a well-behaved harness should be too.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Harness functions don't appear after restart | Folder doesn't match the contract — confirm `<pkg>/agentics/__init__.py` exists and exports `AGENTIC_FUNCTIONS`. Run with `OPENPROGRAM_DEBUG_REGISTRY=1`. |
| `[!] … no package with an agentics/__init__.py was found` at install | Same as above — the repo doesn't satisfy the contract (Part 2). |
| `ModuleNotFoundError` for the harness's own deps | The Program environment preparation failed — rerun `openprogram programs install <source>` and inspect its error. |
| Imports inside the harness fail (`from <pkg>.x import y`) | The package dir isn't named like the import root, or a missing `__init__.py`. The package folder name must equal the import name. |
| An existing dev symlink does not load | Run `openprogram programs install <git-source>` once to verify and record it; the installer does not modify the linked checkout. |
| A harness fails to install or run on Windows | Check the harness README and dependency markers. The OpenProgram CLI/server is supported, but an individual harness may still provide only macOS/Linux backends. |

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
openprogram programs run my_tool -a text=hello           # smoke test
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
