# `openprogram/auth/`

> OpenProgram auth v2 — credential management.

## Overview

Public surface, layered from inside out:

  * :mod:`.types`   — plain dataclasses + errors + events, zero deps
  * :mod:`.store`   — on-disk persistence, singleton, per-pool locks
  * :mod:`.manager` — refresh, pool rotation, fallback chains (v2 task 106)
  * :mod:`.methods` — interactive login flows (v2 task 107)
  * :mod:`.sources` — external credential importers (v2 task 108)
  * :mod:`.profiles` — isolation boundary (v2 task 109)

Call sites should reach for ``manager.acquire`` for API usage and
``manager.login`` for interactive enrollment. The lower layers are
intentionally minimal so they can be exercised in tests without
mocking the network.

## Files in this directory

- **`adapter.py`** — ProviderAuthAdapter
- **`aliases.py`** — Provider alias table
- **`cli.py`** — Command-line entry points for auth v2
- **`context.py`** — Ambient auth context
- **`interactive.py`** — Interactive auth wizard
- **`manager.py`** — Auth v2
- **`pool.py`** — Auth v2
- **`profiles.py`** — Profile manager
- **`resolver.py`** — Single entry point callers use to resolve "the right credential, now"
- **`store.py`** — Auth v2
- **`tui.py`** — Clack-style terminal UI primitives
- **`types.py`** — Auth v2

## Sub-packages

- **`methods/`** — Auth v2
- **`sources/`** — External credential sources

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `_gen_dir_readmes.py` to refresh._
