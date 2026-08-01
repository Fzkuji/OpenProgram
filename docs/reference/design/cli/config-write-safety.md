# Config Write Safety — Design

> This document is the authoritative design of how `config.json` is mutated
> safely: the single atomic entry point, the two locks behind it, and which
> writers must route through it. For what a setting *is*, see
> [`cli-redesign.md`](cli-redesign.md).

## 1. The Hazard

`config.json` is a single JSON file mutated by writers spread across surfaces
and across processes:

- `config_schema.set_setting` — the TUI `/config` panel, the web System tab,
  and `openprogram config`.
- `routes/config.py:save_config` — the web "Save API keys" form.
- `setup.py:set_ui_ports` and `write_search_default_provider`.
- `_setup_sections/*` — the `openprogram setup` wizard.
- `storage.py` — the providers section.

A read-modify-write on a shared file is only correct under a lock covering the
whole sequence. Without one, two writers each read the same starting state,
each apply their own change, and the later write discards the earlier one. Two
scopes of concurrency both occur here:

- **In-process.** A TUI tool toggle and a web api-key save both run in the
  worker process, on different threads.
- **Cross-process.** `openprogram config` and `openprogram setup` are separate
  processes writing the same file while the worker writes it. A `threading`
  lock is invisible across processes and cannot help.

A private module-level lock does not solve this either: `storage.py` serialises
its own providers writes with `_cache_lock`, but no other writer takes that
lock, so it protects that module against itself and nothing more.

## 2. One Atomic Entry Point

`setup.update_config` is the only correct way to change part of the config.
It takes a mutator, holds both locks for the full read-modify-write, and
returns the resulting config:

```python
_config_write_lock = threading.Lock()          # in-process (worker threads)

def update_config(mutator: Callable[[dict], None]) -> dict:
    """Atomic read-modify-write of config.json. Holds an in-process lock AND a
    cross-process file lock (config.json.lock, via filelock), reads the current
    config, applies mutator(cfg) in place, writes it back (0o600), returns it.
    The ONLY correct way to change part of the config — never read_config() +
    write_config() separately, which races."""
    with _config_write_lock:
        with FileLock(str(get_config_path()) + ".lock", timeout=10):
            cfg = _read_config()
            mutator(cfg)
            _write_config(cfg)
            return cfg
```

Both locks are needed and neither is redundant. `filelock` covers the
cross-process case. The `threading.Lock` covers the worker's own threads:
`filelock` is re-entrant within a process, so on its own it would let two
worker threads interleave inside the critical section.

The mutator form is what makes the API hard to misuse. A caller cannot hold a
config dict across the lock boundary and write it back later, because it never
receives one outside the mutator.

`_read_config` and `_write_config` remain for read-only access and full
replacement. Only read-modify-write routes through `update_config`.

## 3. Scope

This design concerns write atomicity alone. Schema definition and value
validation belong to `config_schema` ([`cli-redesign.md`](cli-redesign.md) §3),
and the storage format stays JSON. The property being established is that every
write is atomic and mutually exclusive with every other write.

## Appendix: Implementation Status

`update_config` exists in `setup.py` with a unit test asserting that two
concurrent mutators serialise and the result reflects both.

Migrated: `config_schema.set_setting` (both the `_set_at` and `tools.disabled`
branches), `routes/config.py:save_config` (the api_keys merge), and
`setup.py`'s own `set_ui_ports` / `write_search_default_provider`. All
web-facing config write paths are atomic.

Not yet migrated: the `_setup_sections/*` wizard writers, and `storage.py`'s
providers-section writes. Both are in-process safe today (the latter via
`_cache_lock`); the open gap is a concurrent CLI or wizard write from another
process.
