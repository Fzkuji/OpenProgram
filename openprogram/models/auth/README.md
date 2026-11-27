# `openprogram/models/auth/`

> OpenProgram credentials.

## Overview

* :mod:`.types`    — dataclasses, errors, events; no dependencies
* :mod:`.store`    — one ``credentials.json`` per provider, atomic writes
* :mod:`.carry_over` — the one-time move from the per-account layout
* :mod:`.selection` — which credential a request uses, and what a failure marks on it
* :mod:`.credential_provider` — refresh of sign-ins, per-provider hooks
* :mod:`.resolver` — a credential to the bearer value a request sends
* :mod:`.login`, :mod:`.methods`, :mod:`.sources` — adding credentials

## Files in this directory

- **`adoption.py`** — Adopting sign-ins other tools already hold on this machine
- **`carry_over.py`** — The one-time carry-over of the old credential layout
- **`company_keys.py`** — A company's API keys: each key stored once, for the company that issued it
- **`context.py`** — Which conversation the current model call belongs to
- **`credential_provider.py`** — Refreshing the sign-ins a request is about to use
- **`doctor.py`** — Credential health report: what is wrong with the stored credential pools
- **`env_api_keys.py`** — Provider API-key resolution outside a model request
- **`key_move.py`** — Move every API key to the company that issued it, once per key
- **`provider_contract.py`** — ProviderAuthContract
- **`provider_policy.py`** — Which credentials and providers OpenProgram accepts
- **`provider_port.py`** — What credentials need from the provider adapters and their catalogue
- **`redaction.py`** — Scrub credential-shaped values out of text and JSON-like data
- **`refresh_lock.py`** — Cross-process lock held while one OAuth refresh token is spent
- **`resolver.py`** — From a credential to what one request sends
- **`selection.py`** — Which credential a request uses
- **`services.py`** — The services tools reach with a key, and which company's keys they read
- **`store.py`** — The credential store: one ordered credential list per provider
- **`summary.py`** — Short, secret-free descriptions of credentials for listings and reports
- **`types.py`** — Auth v2

## Sub-packages

- **`account/`** — Credential labels and provider short names (aliases)
- **`credentials/`** — Which state files hold secrets, how a backup treats them, and checking them
- **`login/`** — Login implementation
- **`methods/`** — Auth v2
- **`sources/`** — External credential sources

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/generators/gen_dir_readmes.py` from the repo root to refresh._
