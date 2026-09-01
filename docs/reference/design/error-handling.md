# Error Handling — Discipline and Gate

> What may be caught, what must be said about it, and which paths a linter
> enforces it on.

## 1. The rule

**Catch what you can handle, and leave a record of what you swallowed.**

A caught exception that produces no log, no comment and no fallback is
indistinguishable from a bug that has not been found yet. Most of the cost of a
silent handler is paid later, by whoever is trying to work out why a write
never landed.

Three concrete obligations:

1. **Catch specifically.** File IO raises `OSError`; JSON raises `ValueError`
   (and `json.JSONDecodeError`); `ContextVar.reset` raises `ValueError` for a
   foreign token and `RuntimeError` for a spent one. Name everything the
   operation actually raises.
2. **Never swallow a programmer error.** `AttributeError`, `TypeError`,
   `NameError` and friends are defects in our own code, not conditions to
   tolerate. A handler broad enough to hide one must either be narrowed until
   it cannot, or log so the defect is visible.
3. **Say why.** Every remaining swallow carries either a log line with context,
   or a comment stating why ignoring it is correct. Preferably both when the
   reasoning is not obvious.

## 2. Boundaries

A broad `except Exception` is legitimate at a boundary — a place where letting
the exception propagate would take down something larger than the failed
operation:

- **WebSocket handlers and HTTP routes**, where an exception would drop the
  connection rather than return an error.
- **Thread and worker entry points**, where an exception dies unobserved.
- **Callback invocation**, where caller-supplied code runs inside our loop.
- **Optional subsystems** (memory, project git, usage metering), where absence
  degrades a feature rather than failing the turn.
- **Best-effort bookkeeping** that the next turn re-derives anyway.

A boundary catch still logs with enough context to identify the session or the
operation. "Boundary" is a reason to keep running, never a reason to stay
quiet.

## 3. Choosing the log level

| Level | When |
|---|---|
| `warning` | Something was lost that the user could notice: a write that did not land, a status left stale, a record that will be missing. |
| `debug` | An optional path degraded as designed: an absent subsystem, a best-effort cache, a fallback that worked. |

Use `exc_info=True` so the traceback survives, and include the session id or
equivalent identifier — a log line that cannot be tied to a session is close to
useless when several agents run at once.

## 4. What good handlers look like

Silent loss of a durable write, made visible:

```python
except OSError:
    _log.warning(
        "index.json not saved for session %s", session_id, exc_info=True)
```

A narrow catch where a broad one was hiding defects:

```python
except (ValueError, RuntimeError):
    # ContextVar.reset raises ValueError for a token minted in another
    # context and RuntimeError for one already spent. Narrowing to only
    # the first lets a cancelled turn's spent token escape and replace
    # the CancelledError.
    _log.debug("context var token already spent or foreign", exc_info=True)
```

Narrowing is only correct once you know the full set. Check what the call
actually raises — `ContextVar.reset` above raises two unrelated types, and a
handler that names one of them turns a swallowed failure into an escaping
one.

An optional subsystem, with the reason stated:

```python
except Exception:
    # Memory is optional: an unavailable provider degrades to no memory
    # block, never a failed turn.
    _log.debug("memory system prompt block unavailable", exc_info=True)
```

Handlers that guard an operation which already swallows its own errors, or that
catch something the code cannot raise, are deleted rather than annotated.

## 5. The gate

`ruff` enforces two rules, configured in `pyproject.toml`:

| Rule | What it catches |
|---|---|
| `E722` | A bare `except:`, which also swallows `KeyboardInterrupt` and `SystemExit`. |
| `S110` | `try` / `except` / `pass` — an exception discarded with no log, no comment and no fallback. |

Run it with:

```bash
.venv/bin/ruff check .
```

### Scope

The gate is enforced on three core paths, which are clean:

- `openprogram/store/` — the durable record
- `openprogram/context/` — what the model is shown
- `openprogram/agent/dispatcher/` — the turn itself

These carry the state whose silent corruption is hardest to diagnose later.
Everywhere else is muted through `per-file-ignores`, because several hundred
pre-existing sites would make the gate unpassable and therefore ignored. The
mute list is the backlog: clean a directory, delete its line, and the gate
covers it.

A `per-file-ignores` pattern matches the whole path and its `*` crosses `/`,
so `openprogram/*.py` mutes every module in the package rather than the
top-level ones — which is why the top-level modules are named individually.
Check any new pattern against a nested file before trusting it: a pattern
that mutes too much turns the gate off without failing anything.

`BLE001` (blind `except Exception`) is deliberately not enabled. A boundary
handler that logs and degrades is correct design, and flagging every one of
them would produce noise rather than signal. Section 2 governs those by
review instead.

Ruff is scoped to this gate alone. Formatting and import order are left alone
on purpose — this is a correctness rule, not a style regime.

## Appendix: Implementation Status

Implemented. The three core paths pass `E722` and `S110`; `ruff` is in the
`dev` extra and configured under `[tool.ruff]` in `pyproject.toml`.

Not yet done: the muted directories in `per-file-ignores`. `openprogram/webui/`
and `openprogram/agent/` outside the dispatcher hold the largest remaining
concentrations.

## Related Files

- [Unified execution control](runtime/execution/execution-control.html) — pause, continue, step, steering, cancellation, and why `CancelledError` is a `BaseException`
- [`runtime/dag/overview.md`](runtime/dag/overview.md) — error as a terminal node status
