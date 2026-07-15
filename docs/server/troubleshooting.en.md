# Troubleshooting

Common gotchas. The full operator runbook for a fresh install /
upgrade is in [`GETTING_STARTED.md`](GETTING_STARTED.md); this
page collects the recurring "it doesn't work" cases.

## "No provider available"

`openprogram providers` shows what's detected. Common causes:

- forgot `claude login` / `codex auth` / `gemini auth login`
- API key set in a different shell than the one running the worker
- token expired — re-run the CLI login

## "command not found: openprogram"

pip install dir not on PATH. Two options:

```bash
# call the module directly
python3 -m openprogram <args>

# or add the user-base bin to PATH (idempotent)
echo 'export PATH="$(python3 -m site --user-base)/bin:$PATH"' >> ~/.zshrc
```

## Web UI port in use

Set one of these env vars before starting the worker:

```bash
export OPENPROGRAM_WEB_PORT=8101         # frontend (defaults to 18100)
export OPENPROGRAM_BACKEND_PORT=8102     # FastAPI (defaults to 18109)
```

Or persist the preference: `openprogram config ui`.

## Local-development install (multi-repo)

For working on [GUI-Agent-Harness](https://github.com/Fzkuji/GUI-Agent-Harness)
/ [Research-Agent-Harness](https://github.com/Fzkuji/Research-Agent-Harness)
side-by-side with OpenProgram:

```bash
pip install -e "$OPENPROGRAM_DIR"                   # always first
pip install -e "$GUI_HARNESS_DIR"                   # depends on openprogram
pip install -e "$RESEARCH_HARNESS_DIR"
```

`openprogram/functions/agentics/{GUI,Research}-Agent-Harness`
are symlinks — recreate if a repo moves:

```bash
cd openprogram/functions/agentics
rm -f GUI-Agent-Harness  && ln -s "$GUI_HARNESS_DIR"      GUI-Agent-Harness
rm -f Research-Agent-Harness && ln -s "$RESEARCH_HARNESS_DIR" Research-Agent-Harness
```

`pip install -e` writes absolute paths — rerun it from the new
location if you rename a parent folder.

## Worker doesn't start / starts on the wrong port

`openprogram doctor` runs a fast end-to-end check: Python deps,
Node bundle, providers, MCP, plugins, ports. Read its output
before raising an issue.

## `python -m openprogram._meta agentics_dir` returns nothing

The package isn't installed in the active Python. Either run the installer
(clone OpenProgram + `./scripts/install.sh`) or activate the venv where it is
installed.

## CI says "tests pass" but Mac runs differently

A handful of tests are explicitly skipped on bare CI runners
because they need a configured provider in `$HOME`. The skip
list lives in the test files themselves — search for
`pytest.mark.skipif`. Dev machines with credentials see the
full suite.
