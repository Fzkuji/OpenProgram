# OpenProgram test guide

Read this file before adding, moving, or reviewing tests. The canonical design
and implementation record is
[`docs/reference/design/testing/test-system.html`](../docs/reference/design/testing/test-system.html).
Local and CI commands are maintained in [`CONTRIBUTING.md`](../CONTRIBUTING.md).

## Choose the layer first

Python test files use `tests/<layer>/<product-domain>/test_*.py`. Do not put a
test file directly under an execution layer such as `tests/unit/`.

| Layer | Use it for | Do not use it for |
| --- | --- | --- |
| `contracts` | Repository layout, manifests, registries, schemas, security inventories, and other structural invariants | External services or source-text assertions that duplicate observable behavior |
| `unit` | Pure functions, in-memory objects, local fakes, and isolated temporary files used by one module | `TestClient`, sockets, subprocesses, Playwright, test-owned real threads or pools, fixed sleeps, or process-global thread replacement |
| `component` | Multiple in-process production objects, temporary SQLite, `TestClient`, fake providers, and controlled threads | External networks or real credentials |
| `integration` | Loopback HTTP/WebSocket, real subprocesses, and several production modules working together | External services |
| `e2e` | Public CLI flows, real workers, built Web, and browser behavior | The developer's real home directory or pre-existing build output |
| `live` | Real providers, channels, remote services, and credentials | Ordinary pull-request checks |
| `support` | Shared fixtures and helpers | Test cases |

If a test matches more than one layer, use the strongest dependency. Keep the
whole file in one layer unless splitting it removes a real dependency without
duplicating fixtures or setup.

Markers describe execution requirements, not layers:

- `browser`: requires built Web and Playwright.
- `sandbox`: requires the host sandbox implementation.
- `live`: requires external services, network access, or credentials.
- `slow`: materially exceeds the layer's normal runtime.

Pytest uses strict markers and strict xfail. Do not add `@pytest.mark.unit`,
`component`, `integration`, or `e2e`; the directory already declares that.

## Unit-test boundaries

Unit tests may use `tmp_path`, local fakes, and deterministic in-process state.
They must not:

- import or invoke `TestClient`, `subprocess`, `socket`, or Playwright;
- directly create a real `threading.Thread` or `ThreadPoolExecutor`;
- replace `threading.Thread`, including through `monkeypatch`, `patch`, or
  `setattr`;
- use a fixed `time.sleep` or nonzero `asyncio.sleep`;
- capture `time.sleep` or `asyncio.sleep` into an alias before runtime guards;
- leave a production-created thread, timer, pool, singleton, or temporary
  resource alive after teardown.

Use an `Event`, `Condition`, or deadline-based condition wait for concurrency.
Module-qualified `asyncio.sleep(0)` is allowed only as an event-loop yield.
Production code may create a controlled thread or pool during a unit test only
when the test does not create it directly and teardown joins or closes it.

If the behavior genuinely requires `TestClient`, a real thread or pool,
loopback networking, or a subprocess, move the test to the corresponding
stronger layer. Do not bypass the guard with an alias or a process-global
monkeypatch.

## Test shape

- Reproduce a production defect through the lowest public or shared boundary
  that demonstrates it.
- Assert observable behavior. Use a source contract only when source structure
  itself is the invariant.
- Keep one behavioral assertion owner. Do not duplicate a pure helper's input
  and output assertions in a source-check script.
- Isolate `HOME`; tests must not read or mutate the developer's
  `~/.openprogram`.
- Clean up files, databases, subprocesses, sockets, threads, timers, pools,
  singleton state, registry mutations, and `atexit` registrations.
- Use existing helpers under `tests/support/` before adding a new fixture or
  polling loop.

## JavaScript and interface tests

- Web pure helper behavior: `web/tests/*.test.mjs`, executed by `npm test`.
- Web source structure: `web/scripts/check-*.mjs`, only when structure itself is
  the contract.
- Built-Web interaction: `tests/e2e/web/` with the `browser` marker.
- CLI TypeScript: the existing Vitest suite; CI runs typecheck, test, and build.
- Desktop: existing VM/fake-Electron component checks; CI runs
  `npm run check`.

Keep Web Node test files directly under `web/tests/`; the current test command
does not recursively collect nested directories.

## Required checks

Run the affected layer first, then the repository contracts:

```bash
uv run --locked --extra dev python -m pytest -q tests/<layer>/<product-domain>
uv run --locked --extra dev python -m pytest -q tests/contracts
```

For test infrastructure, shared fixtures, process state, or layer changes, run
the required selection three consecutive times with the fixed worker count:

```bash
for run in 1 2 3; do
  uv run --locked --extra dev python -m pytest -q -n 4 \
    tests/contracts tests/unit tests/component tests/integration tests/e2e \
    || exit 1
done
```

The repository contracts enforce tracked-file placement, declared top-level
directories, generated package README freshness, and the unit resource
boundary. CI separately executes contracts, unit on Python 3.11/3.12/3.13,
component, integration, non-browser e2e, built-Web browser tests, Web, CLI,
Desktop, documentation checks, and the unit coverage floor.

External-service tests are explicit only:

```bash
OPENPROGRAM_TEST_LIVE=1 \
  uv run --locked --extra dev python -m pytest -q -m live tests/live
```
