# Troubleshooting

Common gotchas. The full operator runbook for a fresh install /
upgrade is in [`GETTING_STARTED.md`](../start/GETTING_STARTED.md); this
page collects the recurring "it doesn't work" cases.

## "No provider available"

`openprogram providers` lists the credentials on file; `openprogram providers discover` scans for external CLI logins (Claude Code, Codex, Gemini CLI) to adopt. Common causes:

- forgot `openprogram providers login <provider>` (or the login of the matching external CLI)
- API key set in a different shell than the one running the worker
- token expired — log in again; `openprogram providers doctor` diagnoses credential expiry / refresh / conflicts

## "command not found: openprogram"

The supported CLI/server installer creates `~/.local/bin/openprogram`. Run the
installer again, then ensure that directory is on `PATH`:

```bash
curl -fsSL https://openprogram.io/install | sh
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```

## Web UI port in use

Set this env var before starting the worker (one port serves API, WebSocket, and web UI):

```bash
export OPENPROGRAM_WEB_PORT=8101         # single port (defaults to 18100)
```

Or persist the preference: `openprogram ports --port 8101`.

## Local development

Source-checkout development uses `uv sync --locked --extra dev`. External
harness development is documented in [Installing harnesses](../capabilities/installing-harnesses.md)
and is not a normal product installation path.

## Worker doesn't start / starts on the wrong port

`openprogram doctor` runs a fast end-to-end check: the
Python/Node/git toolchain, skills and plugins loading, provider
credentials, MCP servers, disk cache, and whether the worker is
listening on :18100. On Windows it also reports long-path configuration and a
read-only Defender performance advisory. It never changes the registry,
Defender exclusions, or file ACLs. `openprogram rescue` goes beyond diagnosis
and prints the fix commands directly. Read their output before
raising an issue.

## `import openprogram` raises ModuleNotFoundError

Managed product installations use a private bundled Python and intentionally do
not expose OpenProgram to whichever Python is active in your shell. For source
development, run `uv sync --locked` in the checkout, then execute scripts with
`uv run --project /path/to/OpenProgram python ...`; alternatively, activate the
checkout's `.venv` first.

## CI says "tests pass" but Mac runs differently

A handful of tests are explicitly skipped on bare CI runners
because they need a configured provider in `$HOME`. The skip
list lives in the test files themselves — search for
`pytest.mark.skipif`. Dev machines with credentials see the
full suite.
