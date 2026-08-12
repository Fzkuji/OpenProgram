# Provider Runtime Explicit Initialization Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:test-driven-development`; run specification review before quality review.

**Goal:** Remove provider registration and record/replay activation from `import openprogram.providers`, while preserving existing off/record behavior and keeping replay credential-free.

**Approved design:** `docs/reference/design/providers/record-replay.html` at design commit `ea796046`.

**Base:** `990bfe36e625579443e1cb01a1b3266c8dbd0e87` in isolated worktree `/private/tmp/openprogram-provider-runtime-design-20260812`.

**Exclusions:** No recording-format change, workflow/session/DAG replay, vendor request rewrite, runtime hot reload, Web/Node UI, resource-governance change, or auth account-management refactor. Do not modify the user's separate auth/send-queue worktree.

## Task 1: Lifecycle and import boundary

**Files:**
- Create `openprogram/providers/initialization.py`
- Modify `openprogram/providers/api_registry.py`
- Modify `openprogram/providers/register.py`
- Modify `openprogram/providers/__init__.py`
- Modify `openprogram/auth/credential_provider.py`
- Create `tests/providers/test_provider_initialization.py`

1. Add subprocess RED tests proving package import does not read record/replay config, open a recording, install a transform, or register provider/auth plugins.
2. Add thread RED tests for one initializer, waiting callers, stable READY/FAILED results, interrupted initialization, and provider-batch publication failure.
3. Implement `NEW → INITIALIZING → READY|FAILED` with `threading.Condition`; no production reset API.
4. Make `get_api_provider()` initialize before registry lookup. Keep internal raw registry access private to initialization/register code so initialization does not recurse.
5. Make built-in registration locked, retry-safe before success, and batch-published. Replace auth's provider-package side-effect import with explicit auth-adapter registration.
6. Remove both import-time calls from `providers/__init__.py`.
7. Verify focused tests and existing registry/provider tests; commit.

## Task 2: Replay-safe runtime factory

**Files:**
- Modify `openprogram/providers/registry.py`
- Modify `tests/unit/test_create_runtime_routing.py`
- Modify `tests/providers/test_provider_initialization.py`

1. Add RED cases for explicit plain and all three subscription providers in replay, non-credential defaults, missing defaults, and off/record compatibility.
2. Add `model_namespace` metadata to the three subscription entries.
3. Have `create_runtime()` initialize first. In replay only, choose provider/model from explicit arguments or non-credential config defaults, then construct base `Runtime` from `model_namespace:model`; do not call CLI/AuthStore detection, credential resolution, or `runtime_class` import/construction.
4. Keep off/record code paths unchanged. Let `ReplayProvider` perform the existing full wire-level request comparison.
5. Verify focused factory, subscription, detection, structured-output, and record/replay tests; commit.

## Task 3: Explicit process startup and management recovery

**Files:**
- Modify `openprogram/webui/server.py`
- Modify `openprogram/worker/runner.py`
- Modify `openprogram/cli.py`
- Modify `tests/providers/test_record_replay_cli.py`
- Modify relevant worker/Web startup tests discovered from callers

1. Add RED tests proving Web/worker initialize before starting restore/warm-up/channel threads, while recordings status/off never initialize providers.
2. Call `initialize_provider_runtime()` synchronously at Web/worker startup before their background threads.
3. Delete `_OPENPROGRAM_RECORDINGS_MANAGEMENT`; dispatch recordings management directly.
4. Verify missing/corrupt/unsupported replay recovery through real CLI subprocesses and startup fail-fast behavior; commit.

## Task 4: Evidence, reviews, and gates

**Files:**
- Modify implementation-status/evidence blocks in `docs/reference/design/providers/record-replay.html`, `.md`, and `.zh.md` only after code review passes.

1. Run affected provider, factory, auth, CLI, worker, Web, usage, agent-loop, and runtime tests.
2. Run an independent specification review against `ea796046`; reproduce and repair only contract findings, then obtain PASS.
3. Run a fresh independent quality review; repair and re-review any load-bearing finding.
4. Run the repository full Python gate, changed-file Ruff, docs build, checklinks, `git diff --check`, and clean-status check. Record exact environment-only failures at both base and candidate.
5. Update implementation evidence and commit. Do not change the feature matrix: record/replay was already implemented and this task changes lifecycle quality, not capability scoring.

