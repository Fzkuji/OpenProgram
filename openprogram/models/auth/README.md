# `openprogram/models/auth/`

> OpenProgram credentials.

## Overview

* :mod:`.types`    — dataclasses, errors, events; no dependencies
* :mod:`.store`    — one ``credentials.json`` per provider, atomic writes
* :mod:`.carry_over` — the one-time move from the per-account layout
* :mod:`.selection` — which credential a request uses, and failover
* :mod:`.manage`   — renaming, ordering, checking, removing; the list the web shows
* :mod:`.credential_provider` — refresh of sign-ins, per-provider hooks
* :mod:`.resolver` — a credential to the bearer value a request sends
* :mod:`.login`, :mod:`.methods`, :mod:`.sources` — adding credentials

## Files in this directory

- **`adoption.py`** — Adopting sign-ins other tools already hold on this machine
- **`carry_over.py`** — The one-time carry-over of the old credential layout
- **`context.py`** — Which conversation the current model call belongs to
- **`credential_provider.py`** — Refreshing the sign-ins a request is about to use
- **`doctor.py`** — Credential health report: what is wrong with the stored credential pools
- **`manage.py`** — Changing a provider's credential list, for the Providers page and the CLI
- **`provider_contract.py`** — ProviderAuthContract
- **`provider_policy.py`** — Anthropic credential requirements
- **`refresh_lock.py`** — Cross-process lock held while one OAuth refresh token is spent
- **`resolver.py`** — From a credential to what one request sends
- **`selection.py`** — Which credential a request uses, and what a failure does to it
- **`store.py`** — The credential store: one ordered credential list per provider
- **`summary.py`** — Short, secret-free descriptions of credentials for listings and reports
- **`types.py`** — Auth v2

## Sub-packages

- **`account/`** — Credential labels and provider short names (aliases)
- **`credentials/`** — Credential persistence without filesystem hardening policy
- **`login/`** — Login implementation
- **`methods/`** — Auth v2
- **`sources/`** — External credential sources

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/generators/gen_dir_readmes.py` from the repo root to refresh._
