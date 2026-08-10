# `openprogram/auth/`

> OpenProgram auth v2 — credential management.

## Overview

Public surface, layered from inside out:

  * :mod:`.types`    — plain dataclasses + errors + events, zero deps
  * :mod:`.store`    — on-disk persistence, singleton, per-pool locks
  * :mod:`.credential_provider` — refresh, pool rotation, fallback chains
  * :mod:`.resolver` — flattens a credential to the bearer string a request sends
  * :mod:`.methods`  — interactive login flows
  * :mod:`.sources`  — external credential importers
  * :mod:`.accounts` — isolation boundary

Call sites should reach for ``credential_provider.acquire`` for API usage
and the ``methods`` login flows for interactive enrollment. The lower
layers are intentionally minimal so they can be exercised in tests
without mocking the network.

Two things named "account" and "profile" are kept apart deliberately:
an **account** here is a set of credentials for one provider identity
(``account_id``, ``~/.openprogram/profiles/<name>/``), while a
**profile** is the workspace scope in :mod:`openprogram.paths`
(``--profile``, ``~/.openprogram-<name>/``) covering config and
sessions. One workspace profile can hold many credential accounts.

## Files in this directory

- **`_migrate_payload.py`** — On-load migration of stored credential JSON to the current schema
- **`account_priority.py`** — Per-provider account priority
- **`account_selection.py`** — Per-provider active account selection
- **`accounts.py`** — Account manager
- **`aliases.py`** — Provider alias table
- **`cli.py`** — Command-line entry points for auth v2
- **`context.py`** — Ambient auth context
- **`credential_provider.py`** — Auth v2
- **`interactive.py`** — Interactive auth wizard
- **`login_driver.py`** — Surface-agnostic login driver
- **`login_method_registry.py`** — Single source of truth for which login methods each provider offers,
- **`login_seed_models.py`** — Subscription-login → config enablement
- **`pool.py`** — Auth v2
- **`provider_contract.py`** — ProviderAuthContract
- **`resolver.py`** — Single entry point callers use to resolve "the right credential, now"
- **`rotation.py`** — Per-provider rotation setting, and per-account membership in that rotation
- **`store.py`** — Auth v2
- **`tui.py`** — Clack-style terminal UI primitives
- **`types.py`** — Auth v2
- **`usage.py`** — Feed provider call outcomes back to the credential pool so rotation /

## Sub-packages

- **`methods/`** — Auth v2
- **`sources/`** — External credential sources

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
