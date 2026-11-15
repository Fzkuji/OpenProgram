# `openprogram/interfaces/`

Everything through which a user or another program talks to OpenProgram.

| Package | Source |
| --- | --- |
| `openprogram.interfaces.acp` — ACP server for editors | [`acp/`](acp/) |
| `openprogram.interfaces.channels` — chat channels | [`channels/`](channels/) |
| `openprogram.interfaces.speech` — speech input and output | [`speech/`](speech/) |
| `openprogram.interfaces.server` — the FastAPI Server application | [`server/`](server/) |
| `openprogram.interfaces.cli` — the Python CLI application | [`cli/`](cli/) |

All five are ordinary packages under this directory, found by the package
discovery in the root `pyproject.toml`, so a wheel, an editable install and a
plain checkout see the same layout. The React/Ink terminal client that the CLI
launches is a separate Node.js workspace in [`apps/cli/`](../../apps/cli/); its
release bundle is written to `cli/dist/index.mjs` and ships as package data.
