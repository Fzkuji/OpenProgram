# Config write safety — atomic `update_config`

Status: **planned** · Owner: core/config · Created: 2026-06-04

Optimization-roadmap item #5. Follows the config-IO consolidation that made the
webui delegate to `setup._read_config`/`_write_config` and enforce 0o600.

## 1. Problem

`config.json` is mutated by separate `_read_config()` … `_write_config()` calls
with modification in between — a non-atomic read-modify-write — from several
places, with **no shared lock** across them:

- `config_schema.set_setting` (`config_schema.py:253,288`) — TUI `/config` + web
  System tab + `openprogram config`.
- `routes/config.py:save_config` — the web "Save API keys" form (read config,
  merge `api_keys`, write).
- `setup.py:set_ui_ports` / `write_search_default_provider` (`135,176`).
- `_setup_sections/*` — the `openprogram setup` wizard.

`storage.py` already serialises its **providers**-section writes with a
module-level `threading.Lock` (`_cache_lock`), but that lock is private to that
module — the writers above don't take it.

So two concurrent writers race and the later write clobbers the earlier one:
- In-process: a TUI tool-toggle (`set_setting`) and a web api-key save
  (`save_config`) both run in the **worker** process; without a shared lock one
  overwrites the other.
- Cross-process: `openprogram config` / `openprogram setup` are **separate
  processes** writing the same file while the worker writes it — a `threading`
  lock can't see across processes.

## 2. Design

One atomic entry point in `setup.py`:

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

- `filelock` (3.16.1, already a dependency) gives the cross-process lock; the
  `threading.Lock` gives the in-process one (filelock is re-entrant per process
  but the thread lock makes the read-modify-write critical section atomic across
  the worker's threads too).
- `_read_config` / `_write_config` stay for read-only / full-replace; only
  read-modify-write moves to `update_config`.

## 3. Migration

1. **(this step)** Add `update_config` to `setup.py` + a unit test (two
   "concurrent" mutators serialise; result reflects both).
2. Migrate the two web-facing racers — `config_schema.set_setting` (both the
   `_set_at` branch and the `tools.disabled` branch) and
   `routes/config.py:save_config` (the api_keys merge) — to `update_config`.
3. Migrate `setup.py`'s own `set_ui_ports` / `write_search_default_provider` and
   the `_setup_sections/*` wizard writers.
4. Have `storage.py`'s providers-section writes go through `update_config` too
   (keeping behaviour) so the providers writes are cross-process safe, then drop
   the now-redundant private `_cache_lock` read-modify-write wrapping.

Each step: restart worker, `/healthz`, save a setting + an api key from the web,
confirm both persist (no clobber), tests green.

## 4. Non-goals

Not a config schema/validation change (that's `config_schema`); not a move off
JSON. Just making every write atomic and mutually exclusive.
