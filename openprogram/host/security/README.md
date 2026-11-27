# `openprogram/host/security/`

> Runtime-owned security policy: outbound network access, the owner's outbound URL settings, proxy routing and credential masking.

## Files in this directory

- **`backend_endpoint.py`** — Reaching the active Web backend from a client process
- **`credential_mask.py`** — The one masked shape every surface shows for a stored credential
- **`http_proxy.py`** — Proxy configuration diagnostics for explicit policy-proxy setup
- **`outbound_settings.py`** — The owner's outbound URL policy as stored in ``config.json``
- **`remote_access.py`** — Where other devices reach this computer's OpenProgram: the local network and anywhere
- **`runtime_http_audit.py`** — Shared Runtime HTTP denial audit and fail-closed source inventory
- **`safe_http.py`** — Consumer declarations and peer-constrained Runtime HTTP clients
- **`url_policy.py`** — Pure URL normalization and address policy for Runtime-owned HTTP

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/generators/gen_dir_readmes.py` from the repo root to refresh._
