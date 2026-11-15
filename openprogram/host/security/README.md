# `openprogram/host/security/`

> Runtime-owned security policy: outbound network access, credential masking and identity headers.

## Files in this directory

- **`backend_endpoint.py`** — Reaching the active Web backend from a client process
- **`credential_mask.py`** — The one masked shape every surface shows for a stored credential
- **`identity_header.py`** — Normalize identity values from external platforms before they enter runtime-controlled headers
- **`runtime_http_audit.py`** — Shared Runtime HTTP denial audit and fail-closed source inventory
- **`safe_http.py`** — Consumer declarations and peer-constrained Runtime HTTP clients
- **`url_policy.py`** — Pure URL normalization and address policy for Runtime-owned HTTP

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
