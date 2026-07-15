# Harness Standard — how an agentic program plugs into OpenProgram

> Status: standard / spec. The contract every **harness** (a self-contained
> agentic program shipped as its own repo) must satisfy so that cloning it
> into OpenProgram's `agentics/` folder makes it **auto-detected and usable
> with no host edits**. The three first-party harnesses (GUI / Research /
> Wiki) are the reference implementations; third parties follow the same
> rules.
> Related: [`../installing-harnesses.md`](../../../capabilities/installing-harnesses.md)
> (install procedure), `openprogram/functions/_registry.py` (the loader),
> `openprogram/functions/_programs.py` (first-party catalogue).

## 0. The one rule that matters

**A harness is anything you drop into `<openprogram>/functions/agentics/`
that exposes its functions through `<pkg>/agentics/__init__.py`.** The host
walks that folder on startup (and, with hot-reload, whenever it changes),
finds the inner package, imports `<pkg>.agentics`, and the
`@agentic_function` decorators self-register. Nothing else is required and
no host file is edited.

```
<Harness-Repo>/                    ← what you git clone into agentics/
└── <pkg>/                         ← importable package; folder name == import name
    ├── __init__.py                ← marks it a package (may re-export niceties)
    └── agentics/
        └── __init__.py            ← THE ENTRY POINT — exposes AGENTIC_FUNCTIONS
```

If a cloned folder doesn't match this shape, the host **silently ignores
it** (a non-harness folder must never break the load). So "auto-detect"
== "matches this contract".

## 1. The entry point: `<pkg>/agentics/__init__.py`

Must define `AGENTIC_FUNCTIONS` — a list of `@agentic_function`-decorated
callables. Importing this module fires the decorators, which register the
functions into the shared tool registry. The list is also the harness's
declared public surface.

**Required pattern — degrade gracefully, never crash the host:**

```python
# <pkg>/agentics/__init__.py
"""Entry point for the Foo harness."""
try:
    from .foo_agent import foo_agent, foo_helper
    AGENTIC_FUNCTIONS = [foo_agent, foo_helper]
except Exception:          # missing optional dep, unsupported platform, …
    # The host imports this module during discovery. If the harness can't
    # load on this machine (a heavy dep isn't installed, this OS has no
    # backend, …), expose NOTHING rather than raising — "can run → listed,
    # can't → skipped", per the discovery contract. The host logs the
    # cause under OPENPROGRAM_DEBUG_REGISTRY=1.
    AGENTIC_FUNCTIONS = []
```

Rules:
- `AGENTIC_FUNCTIONS` **must always be defined** (even if `[]`).
- The `try/except` is **mandatory** — a harness that can't run on this
  machine must yield `[]`, not propagate an `ImportError`. This is what
  makes "clone anything in, the ones that can run light up, the rest stay
  dark" work.
- Don't do heavy work at import time (no model downloads, no network, no
  GUI grab). Import must be cheap and side-effect-free beyond registration.

## 2. The package: `<pkg>/`

- **Folder name == import name.** `gui_harness/` is imported as
  `gui_harness`. The host puts the harness root on `sys.path` so the
  harness's own absolute imports (`from gui_harness.x import y`) resolve.
  A hyphenated *repo* folder (`GUI-Agent-Harness/`) is fine — the host
  finds the inner ascii-identifier package inside it.
- May vendor sibling packages (e.g. GUI harness ships `desktop_env/`
  alongside `gui_harness/`). The host picks the **one** package that has
  an `agentics/` sub-package as the entry; vendored siblings are ignored
  by discovery and just ride along on `sys.path`.

## 3. Configuration — the unified convention

Harnesses differ wildly in what they configure (Research: providers +
work dir; Wiki: a vault path; GUI: vision model + platform backend). The
**standard is the mechanism, not the parameters**:

1. **Per-call parameters first.** Anything that varies per invocation is a
   function argument with a sane default:
   ```python
   @agentic_function(name="foo_agent")
   def foo_agent(task: str, max_steps: int = 15, runtime=None) -> dict: ...
   ```
2. **The runtime is injected, never constructed.** A harness function that
   needs an LLM declares a `runtime` parameter; the host injects the
   active runtime. Harnesses **must not** build their own provider /
   read `ANTHROPIC_API_KEY` themselves for the in-host path — provider
   selection and auth belong to the host. (A harness MAY keep a
   standalone CLI that calls `create_runtime(...)` for use outside the
   host; that's separate from the in-host entry point.)
3. **Environment variables for machine-level settings**, namespaced with
   the harness's prefix, documented in the README:
   ```
   WAH_VAULT          # Wiki: vault root
   GUI_AGENT_MEMORY   # GUI: learned-component store
   ```
4. **No config files required to start.** A harness must work with zero
   config (sensible defaults); config is opt-in override, never a setup
   gate. No mandatory wizard / first-run prompt.
5. **State / scratch goes under the host state dir**, not a hand-rolled
   home path. Use `openprogram.paths.get_state_dir()` (→ `~/.openprogram/`)
   as the base; **never hardcode `~/.agentic/...`** (that path is retired).
   A per-harness subdir is the convention:
   `get_state_dir() / "harnesses" / "<pkg>"`.

## 4. Dependencies

- **Do NOT declare `openprogram` as a git dependency.** The host is the
  thing importing the harness, so it's already installed. A
  `openprogram @ git+https://...` line in the harness's `pyproject.toml`
  causes `pip install <harness>` to re-fetch and possibly downgrade the
  host. List openprogram only as a documented *assumption* ("install into
  an environment that already has openprogram"), not a hard dep.
- **Declare the harness's own third-party deps** (torch, Jinja2, …) in its
  `pyproject.toml` / `requirements.txt`. Installing those is the
  **harness's** install step (run when the user installs the harness),
  not OpenProgram's concern — the host never auto-installs harness deps.
- **Heavy / native deps go behind an extra** so a light clone stays light:
  ```toml
  [project.optional-dependencies]
  ocr = ["easyocr"]
  ```
- The graceful-degrade `try/except` in §1 is what lets a harness be
  *cloned* before its deps are installed without breaking the host — it
  just stays dark until the deps are present.

## 5. Platform support

- A harness MAY be platform-specific in its own code (GUI harness drives
  the desktop; its macOS / Linux backends differ, and Windows may be
  unimplemented). That's allowed.
- **Express "unsupported here" as `AGENTIC_FUNCTIONS = []`**, via the §1
  `try/except` or an explicit `platform.system()` check — never as an
  uncaught `NotImplementedError` at import. Install/registration always
  succeeds; whether a function is *listed* reflects whether it can run on
  this OS.
- Detect backends at runtime (`shutil.which`, `importlib.util.find_spec`),
  don't assume. Document the per-OS setup in the harness README.

## 6. Discovery & hot-reload (host side — what a harness can rely on)

- **Startup:** the host imports every matching folder under `agentics/`.
- **Hot-reload (planned):** the host watches `agentics/` and, when a new
  folder appears, runs the same discovery on it and broadcasts a
  `programs:changed` event so the web UI lists the new harness without a
  restart. A harness needs to do nothing special — just satisfy §1.
- **First-party catalogue:** GUI / Research / Wiki are also listed in
  `_programs.py` so `openprogram programs install <name>` can clone them
  by name. Third-party harnesses skip the catalogue and are installed by
  cloning into `agentics/` directly; discovery treats them identically.

## 7. Conformance checklist (for a harness author)

- [ ] Repo clones into `agentics/<Repo-Name>/`; inside is a package
      `<pkg>/` whose folder name equals its import name.
- [ ] `<pkg>/agentics/__init__.py` defines `AGENTIC_FUNCTIONS = [...]`.
- [ ] That module wraps its imports in `try/except` → `[]` on failure.
- [ ] Import is cheap: no network / model-download / GUI grab at import.
- [ ] LLM access via an injected `runtime` parameter, not self-built.
- [ ] No required config file / wizard; works at zero-config defaults.
- [ ] State under `get_state_dir()`, never `~/.agentic` or another
      hardcoded home path.
- [ ] `openprogram` is NOT a git dependency in `pyproject.toml`.
- [ ] Own third-party deps declared; heavy/native ones behind an extra.
- [ ] Platform-unsupported → `AGENTIC_FUNCTIONS = []`, not a crash.

## 8. Where the three first-party harnesses stand today (gap list)

Captured from a survey of the current repos — these are the changes
needed to bring them to this standard (tracked for when we update them):

| Harness | Conforms? | Gap to close |
|---|---|---|
| **Wiki** | ✅ closest | `agentics/__init__.py` + `AGENTIC_FUNCTIONS` + `try/except` already present. Fix: default vault path uses retired `~/.agentic/memory/wiki` → move under `get_state_dir()`. Drop `openprogram` git dep. |
| **GUI** | ⚠ partial | Exposes functions but no single standard `<pkg>/agentics/__init__.py` with `AGENTIC_FUNCTIONS`; decorators are spread across modules. Add the entry module. Keep heavy deps behind an extra (already partly done). Windows path: degrade to `[]`, don't crash. Drop `openprogram` git dep. |
| **Research** | ❌ non-conforming | **No `agentics/` sub-package at all** — uses its own `registry.py`, so the host's auto-discovery can't see it. Add `research_harness/agentics/__init__.py` exposing `AGENTIC_FUNCTIONS`. Drop `openprogram` git dep. |

All three currently declare `openprogram @ git+…` as a dependency (§4
violation) — removing that is a shared fix.
