# `openprogram/models/auth/`

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
**profile** is the workspace scope in :mod:`openprogram.state.paths`
(``--profile``, ``~/.openprogram-<name>/``) covering config and
sessions. One workspace profile can hold many credential accounts.

## Files in this directory

- **`_migrate_payload.py`** — On-load migration of stored credential JSON to the current schema
- **`adoption.py`** — Adopting credentials that already exist on this machine
- **`context.py`** — Ambient auth context
- **`credential_provider.py`** — Auth v2
- **`doctor.py`** — Credential health report: what is wrong with the stored credential pools
- **`pool.py`** — Auth v2
- **`provider_contract.py`** — ProviderAuthContract
- **`provider_policy.py`** — Anthropic credential requirements
- **`refresh_lock.py`** — Cross-process lock held while one OAuth refresh token is spent
- **`resolver.py`** — Single entry point callers use to resolve "the right credential, now"
- **`rotation.py`** — Per-provider rotation setting, and per-account membership in that rotation
- **`store.py`** — Auth v2
- **`summary.py`** — Short, secret-free descriptions of credentials for listings and reports
- **`types.py`** — Auth v2
- **`usage.py`** — Feed provider call outcomes back to the credential pool so rotation /

## Sub-packages

- **`account/`** — Account implementation
- **`credentials/`** — Credential persistence without filesystem hardening policy
- **`login/`** — Login implementation
- **`methods/`** — Auth v2
- **`sources/`** — External credential sources

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
