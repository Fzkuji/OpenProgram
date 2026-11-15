# `openprogram/host/sandbox/`

> System-level sandbox — restrict a shell command's file, process and

## Overview

network access.

macOS: sandbox-exec (Seatbelt). Linux: bubblewrap (bwrap). Windows delegates
the same bubblewrap policy to the default WSL2 distribution; it never rewrites
Windows ACLs.

The policy is resolved from ``~/.openprogram/config.json`` (the
``sandbox.*`` keys in ``state.settings.SETTINGS``) at the moment a command
is wrapped. That matters more than it sounds: the switch used to live on
a ``ContextVar`` and was lost at every boundary that starts a fresh
context — the web UI's asyncio task handing work to a bare thread, the
``spawn`` subprocess behind ``Agent method``, and any nested CLI.
A file every process reads cannot be lost at a boundary, and cannot be
skipped by an approval-layer bypass either, because it is read below the
approval layer. Callers that already hold a policy pass it explicitly to
``wrap_command``.

What the boundary is for every available platform backend:

* writes are confined to the working directory plus configured roots
* inside them, files that something outside the sandbox runs later (shell
  startup files, the repository's git hooks and config, OpenProgram's own
  state) stay read-only; see ``protected_writes``
* reads are open EXCEPT the credential globs in ``deny_read``
* the network is off unless ``sandbox.network`` is on
* the child environment is an allowlist, so API keys do not reach it
* execution is unrestricted — children inherit the sandbox, so filtering
  binaries by path buys nothing (``/bin/bash -c`` runs arbitrary code
  either way) and breaks git/python3/make, whose ``/usr/bin`` entries are
  shims into the developer-tools directory

## Files in this directory

- **`network_policy.py`** — Destination policy for managed sandbox HTTP proxies
- **`network_runner.py`** — Per-command authenticated HTTP proxy with a proxy-only macOS child sandbox
- **`protected_writes.py`** — Writes a sandboxed command may never make, whatever its writable roots
- **`recoverable_delete.py`** — Best-effort recoverable deletion for local agent child processes

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
