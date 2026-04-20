"""
claude_models — curated Claude Code CLI (subscription-mode) model registry.

Scope and strategy
------------------
This module is the single source of truth for "which Claude models can be
selected in the web UI when the backend runtime is Claude Code CLI
(subscription mode, not the Anthropic API)".

* File: `claude_models.json` (next to this module).
* Schema is frozen and validated — malformed files are auto-restored via
  `doctor()` so a bad LLM rewrite can't brick the UI.

* Families (weakest → strongest): **haiku < sonnet < opus**. All THREE
  families must appear in the registry at all times — the UI always
  exposes a small/medium/large choice. For each family we keep the
  LATEST generation plus, when it exists, the previous generation (so
  users can fall back to a cheaper model when pricing shifts). When a
  new version ships, `refresh_claude_models` promotes it and demotes
  the older one out of the prev slot automatically.

* Each kept generation is listed twice when applicable: the base entry
  (200K context) + the 1M variant (`[id][1m]` form). `requires_extra_usage
  = true` is set when the CLI loads the 1M variant but the Anthropic
  backend rejects it with "Extra usage is required for 1M context" on
  the current subscription — the UI still shows it (prompts upgrade)
  but chat will fail until the user enables extra_usage.

* Ordering: STRONGEST first — opus → sonnet → haiku. Within a family,
  newest generation first; within a generation, 1M variant before 200K
  base. Total surface is ~6-7 entries. The default model is NOT placed
  first by position; the UI highlights the current selection visually.

* DEFAULT model (`openprogram/providers/__init__.py::PROVIDERS`) = Sonnet
  4.6 (200K) — middle family, confirmed to actually run on the
  subscription. The 1M Sonnet variant needs `extra_usage` which isn't
  enabled by default, so it's excluded from the registry until users
  explicitly opt in.

Refresh flow
------------
`refresh_claude_models(runtime)` is an `@agentic_function`. It:

  1. Pulls the live model list from the Anthropic API (`/v1/models`).
  2. Probes each candidate via the Claude Code CLI to verify it actually
     loads AND to read `contextWindow` from `modelUsage`.
  3. Probes the `[1m]` suffix variant of each to distinguish
     "1M works" vs "needs extra_usage" vs "not supported".
  4. Hands the results + the existing curated JSON to the LLM and asks
     it to produce an updated JSON obeying the scope rules in this
     module's docstring.
  5. Validates the LLM output against the schema; on any validation error
     or JSON-parse error it rejects the update and keeps the old file.

Doctor
------
If the on-disk JSON fails schema validation at load time, `doctor()`
overwrites it with the embedded seed (same data as the initial file)
and logs a warning. This prevents one bad refresh from breaking the UI.
Inspired by `claude doctor` (restores broken Claude Code configs).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import warnings
from datetime import datetime, timezone
from typing import Any, Optional

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
CLAUDE_MODELS_JSON_PATH = os.path.join(_THIS_DIR, "claude_models.json")

_SCHEMA_VERSION = 1

# Embedded seed — used by doctor() when the on-disk file is corrupted.
# Keep in sync with claude_models.json when intentional changes are made.
#
# Seed encodes the strategy: all 3 families present, display order is
# STRONGEST first (opus → sonnet → haiku); per family keep latest + prev
# gen (when prev exists); [1m] variant before 200K base. The default model
# is `claude-sonnet-4-6` (200K) — Sonnet is the middle family and 200K
# works on every subscription without extra_usage. The 1M Sonnet variant
# is excluded until the subscription opts in to extra_usage.
_SEED: dict = {
    "schema_version": _SCHEMA_VERSION,
    "last_updated": "2026-04-17T19:30:00Z",
    "source": "seed",
    "note": "Claude Code CLI subscription-mode registry. 三个家族（由弱到强）：haiku < sonnet < opus，都必须在列表里。显示顺序：最强在前 opus → sonnet → haiku；家族内新版在前、[1m] 在 200K 前。默认 = Sonnet 4.6（中间档、实际可跑）。注：sonnet 和 opus-4.6 的 1M 变体因需要 extra_usage / 已淘汰而暂不列入。",
    "models": [
        {
            "id": "claude-opus-4-7[1m]",
            "display": "Claude Opus 4.7 (1M)",
            "family": "opus",
            "generation": "4.7",
            "context_window": 1000000,
            "max_output": 64000,
            "requires_extra_usage": False,
            "recommended": True,
        },
        {
            "id": "claude-opus-4-7",
            "display": "Claude Opus 4.7",
            "family": "opus",
            "generation": "4.7",
            "context_window": 200000,
            "max_output": 64000,
            "requires_extra_usage": False,
            "recommended": True,
        },
        {
            "id": "claude-opus-4-6",
            "display": "Claude Opus 4.6",
            "family": "opus",
            "generation": "4.6",
            "context_window": 200000,
            "max_output": 64000,
            "requires_extra_usage": False,
            "recommended": False,
        },
        {
            "id": "claude-sonnet-4-6",
            "display": "Claude Sonnet 4.6 — default",
            "family": "sonnet",
            "generation": "4.6",
            "context_window": 200000,
            "max_output": 32000,
            "requires_extra_usage": False,
            "recommended": True,
        },
        {
            "id": "claude-haiku-4-5-20251001",
            "display": "Claude Haiku 4.5",
            "family": "haiku",
            "generation": "4.5",
            "context_window": 200000,
            "max_output": 32000,
            "requires_extra_usage": False,
            "recommended": True,
        },
    ],
}

# Families in strength order (used by LLM prompt + UI rendering).
_FAMILIES_BY_STRENGTH = ["haiku", "sonnet", "opus"]

_REQUIRED_MODEL_FIELDS = {
    "id": str,
    "display": str,
    "family": str,
    "generation": str,
    "context_window": int,
    "max_output": int,
    "requires_extra_usage": bool,
    "recommended": bool,
}


class ModelRegistryError(ValueError):
    """Raised when claude_models.json fails schema validation."""


def validate_schema(data: Any) -> None:
    """Strict structural check — any deviation raises ModelRegistryError.

    Called after every LLM-produced refresh so a hallucinated/garbled file
    never replaces a good one. Also called by doctor() at load time.
    """
    if not isinstance(data, dict):
        raise ModelRegistryError("root must be an object")
    if data.get("schema_version") != _SCHEMA_VERSION:
        raise ModelRegistryError(
            f"schema_version mismatch: got {data.get('schema_version')!r}, "
            f"expected {_SCHEMA_VERSION}"
        )
    if not isinstance(data.get("last_updated"), str):
        raise ModelRegistryError("last_updated must be an ISO8601 string")

    models = data.get("models")
    if not isinstance(models, list) or not models:
        raise ModelRegistryError("models must be a non-empty list")

    seen_ids = set()
    for i, m in enumerate(models):
        if not isinstance(m, dict):
            raise ModelRegistryError(f"models[{i}] must be an object")
        for field, typ in _REQUIRED_MODEL_FIELDS.items():
            if field not in m:
                raise ModelRegistryError(f"models[{i}] missing field {field!r}")
            # bool is subclass of int — check bool first
            if typ is bool and not isinstance(m[field], bool):
                raise ModelRegistryError(f"models[{i}].{field} must be bool")
            if typ is not bool and not isinstance(m[field], typ):
                raise ModelRegistryError(
                    f"models[{i}].{field} must be {typ.__name__}, got {type(m[field]).__name__}"
                )
        mid = m["id"]
        if mid in seen_ids:
            raise ModelRegistryError(f"duplicate model id: {mid}")
        seen_ids.add(mid)
        if m["context_window"] <= 0:
            raise ModelRegistryError(f"models[{i}].context_window must be > 0")
        if m["max_output"] <= 0:
            raise ModelRegistryError(f"models[{i}].max_output must be > 0")
        if m["family"] not in ("opus", "sonnet", "haiku"):
            raise ModelRegistryError(
                f"models[{i}].family must be opus|sonnet|haiku, got {m['family']!r}"
            )

    # Structural ordering rule: strongest family first, no interleaving.
    # Catches LLM refreshes that drift away from the intended presentation.
    family_order = {"opus": 0, "sonnet": 1, "haiku": 2}
    seen_family_rank = -1
    current_family = None
    for i, m in enumerate(models):
        rank = family_order[m["family"]]
        if m["family"] != current_family:
            if rank < seen_family_rank:
                raise ModelRegistryError(
                    f"models[{i}] family {m['family']!r} appears out of order; "
                    f"expected opus → sonnet → haiku, no interleaving"
                )
            seen_family_rank = rank
            current_family = m["family"]

    # All 3 families must be present — UI always exposes small/medium/large.
    families_present = {m["family"] for m in models}
    for required in ("opus", "sonnet", "haiku"):
        if required not in families_present:
            raise ModelRegistryError(
                f"family {required!r} missing; all 3 families (haiku/sonnet/opus) must be present"
            )


def doctor() -> dict:
    """Restore claude_models.json from the embedded seed.

    Like `claude doctor` — used when the file is missing or fails schema
    validation. Overwrites atomically and returns the restored data.
    """
    _atomic_write(CLAUDE_MODELS_JSON_PATH, _SEED)
    warnings.warn(
        f"claude_models.json restored from seed at {CLAUDE_MODELS_JSON_PATH}",
        RuntimeWarning,
        stacklevel=2,
    )
    return dict(_SEED)


def load_claude_models() -> dict:
    """Load the registry, auto-doctoring on corruption.

    Returns the validated dict. Callers must treat the returned value as
    read-only (mutations won't persist unless you pass through
    `_atomic_write`).
    """
    if not os.path.exists(CLAUDE_MODELS_JSON_PATH):
        return doctor()
    try:
        with open(CLAUDE_MODELS_JSON_PATH, "r") as f:
            data = json.load(f)
        validate_schema(data)
        return data
    except (json.JSONDecodeError, ModelRegistryError) as e:
        warnings.warn(
            f"claude_models.json invalid ({type(e).__name__}: {e}); running doctor()",
            RuntimeWarning,
            stacklevel=2,
        )
        return doctor()


def list_model_ids(include_extra_usage: bool = True) -> list[str]:
    """Return all usable model IDs.

    Ordering follows the `models` array order in `claude_models.json`
    verbatim — the JSON file is the authoritative source of presentation
    order (default first, then by family sonnet→opus→haiku, within family
    newer generation first, within generation 1M before 200K).
    """
    data = load_claude_models()
    models = data["models"]
    if not include_extra_usage:
        models = [m for m in models if not m["requires_extra_usage"]]
    return [m["id"] for m in models]


def _atomic_write(path: str, data: dict) -> None:
    """Write JSON atomically (tmp + rename) so a crash never leaves partial."""
    dirpath = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=dirpath, prefix=".claude_models.", suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def is_stale(max_age_hours: float = 24.0) -> bool:
    """True if the registry hasn't been refreshed within `max_age_hours`."""
    data = load_claude_models()
    try:
        ts = datetime.fromisoformat(data["last_updated"].replace("Z", "+00:00"))
    except Exception:
        return True
    age = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
    # Negative age = future timestamp (clock drift or bogus seed) → count as stale.
    return age < 0 or age >= max_age_hours


# ---------------------------------------------------------------------------
# Probing helpers (used by refresh_claude_models)
# ---------------------------------------------------------------------------

def _fetch_anthropic_api_models() -> list[dict]:
    """Pull the authoritative model list from the Anthropic API.

    Uses the configured API key (env or ~/.agentic/config.json). Returns []
    if no key is available — refresh will then probe a hard-coded candidate
    set derived from the current registry.
    """
    try:
        import anthropic
    except ImportError:
        return []

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        try:
            cfg_path = os.path.expanduser("~/.agentic/config.json")
            if os.path.exists(cfg_path):
                with open(cfg_path, "r") as f:
                    cfg = json.load(f)
                api_key = cfg.get("api_keys", {}).get("ANTHROPIC_API_KEY")
        except Exception:
            pass
    if not api_key:
        return []

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.models.list(limit=100)
        return [
            {"id": m.id, "display": getattr(m, "display_name", None)}
            for m in resp.data
        ]
    except Exception:
        return []


def _probe_cli_model(cli_path: str, model_id: str, timeout: int = 45) -> dict:
    """Verify a model loads via Claude Code CLI and read its contextWindow.

    Returns a dict with:
        status: "ok" | "extra_usage_required" | "failed"
        context_window: int | None
        max_output: int | None
        resolved_id: str | None    (what system.model reports)
    """
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)  # force subscription path

    cmd = [
        cli_path,
        "--permission-mode", "bypassPermissions",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
        "--model", model_id,
    ]
    try:
        p = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1, env=env,
        )
    except Exception:
        return {"status": "failed", "context_window": None, "max_output": None, "resolved_id": None}

    msg = json.dumps({
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": "say: ok"}]},
    })
    try:
        p.stdin.write(msg + "\n"); p.stdin.flush()
    except Exception:
        p.kill()
        return {"status": "failed", "context_window": None, "max_output": None, "resolved_id": None}

    start = time.time()
    sys_model = None
    is_error = False
    error_text = None
    ctx = None
    max_out = None
    try:
        while time.time() - start < timeout:
            if p.poll() is not None:
                break
            line = p.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            t = d.get("type")
            if t == "system":
                sys_model = d.get("model")
            elif t == "result":
                is_error = bool(d.get("is_error"))
                error_text = (d.get("result") or "")[:300]
                mu = d.get("modelUsage") or {}
                # Primary = the one matching system.model. Otherwise pick
                # by highest output_tokens (Haiku routing helper entries
                # have zero output for the main reply, so they lose).
                for k, v in mu.items():
                    if sys_model and (k == sys_model or sys_model in k):
                        ctx = v.get("contextWindow")
                        max_out = v.get("maxOutputTokens")
                        break
                if ctx is None and mu:
                    k, v = max(mu.items(), key=lambda kv: kv[1].get("outputTokens", 0))
                    ctx = v.get("contextWindow")
                    max_out = v.get("maxOutputTokens")
                break
    finally:
        try:
            p.terminate(); p.wait(timeout=3)
        except Exception:
            try: p.kill()
            except Exception: pass

    if is_error:
        if error_text and "Extra usage is required" in error_text:
            return {
                "status": "extra_usage_required",
                "context_window": None, "max_output": None,
                "resolved_id": sys_model,
            }
        return {
            "status": "failed", "context_window": None, "max_output": None,
            "resolved_id": sys_model,
        }

    if ctx is None:
        return {"status": "failed", "context_window": None, "max_output": None, "resolved_id": sys_model}

    return {
        "status": "ok",
        "context_window": ctx,
        "max_output": max_out,
        "resolved_id": sys_model,
    }


def _build_probe_candidates(api_models: list[dict]) -> list[str]:
    """Candidate IDs to probe via CLI.

    Strategy: keep every 4.x+ model from the API list. The LLM filters
    down to "latest per family" during refresh. Drop Claude 3.x (too old;
    CLI rejects Haiku 3 anyway). Each surviving ID is probed in both the
    base form and the `[1m]` variant.

    If the API list is empty (no key available) the seed IDs are used as a
    fallback so refresh still produces a valid file.
    """
    kept = []
    for m in api_models:
        mid = m["id"]
        # Strip Claude 3.x and earlier — everything 4.x+ is eligible.
        if "claude-3-" in mid or "claude-2" in mid:
            continue
        kept.append(mid)

    if not kept:
        # Fallback: probe the seed IDs (strip [1m] suffix to get bases)
        kept = list({m["id"].replace("[1m]", "") for m in _SEED["models"]})

    # Add [1m] variants
    with_1m: list[str] = []
    for mid in kept:
        with_1m.append(mid)
        with_1m.append(f"{mid}[1m]")

    # Dedup preserving order
    seen = set(); out = []
    for mid in with_1m:
        if mid not in seen:
            seen.add(mid); out.append(mid)
    return out


# ---------------------------------------------------------------------------
# Agentic function
# ---------------------------------------------------------------------------

def _refresh_impl(runtime) -> dict:
    """Do the actual refresh work. Split out so both the agentic function
    and direct callers (server-startup hook) can reuse it."""
    api_models = _fetch_anthropic_api_models()

    cli_path = shutil.which("claude")
    probe_results: list[dict] = []
    if cli_path:
        for mid in _build_probe_candidates(api_models):
            r = _probe_cli_model(cli_path, mid)
            r["id"] = mid
            probe_results.append(r)

    current = load_claude_models()

    # Ask the LLM to merge. Keep the prompt small and explicit so we
    # don't drift — the validation step catches anything that slips.
    ok_probes = [r for r in probe_results if r["status"] == "ok"]
    extra_probes = [r for r in probe_results if r["status"] == "extra_usage_required"]

    prompt = f"""Update the Claude Code CLI model registry JSON.

STRATEGY — families by strength (weakest → strongest): {_FAMILIES_BY_STRENGTH}.
  All 3 families MUST appear. Per family, keep the LATEST generation plus
  the PREVIOUS generation if it appears in the probe results. Drop every
  older entry in the same family.

STRICT RULES:
1. Every one of these families must produce at least one entry:
   {_FAMILIES_BY_STRENGTH}. If a family has no probe result, fall back to
   the existing registry entry for that family (do NOT remove it) — we
   always expose a small/medium/large choice.
2. For each family, keep the NEWEST `generation` and, if it exists in the
   probes, the one immediately before it. Never keep 3 generations of the
   same family. Skip generations older than that.
3. For every kept generation, include both the base ID AND its `[1m]`
   variant if the variant was probed (as OK or as extra_usage_required).
4. Ordering of the `models` array (STRONGEST first, NOT default first):
     - opus family first (newer generation → older, 1M variant → 200K within a gen)
     - then sonnet family (newer → older, 1M → 200K)
     - then haiku family (newer → older, 1M → 200K)
   The default model may land in the middle of the list — that's OK.
5. Mark `requires_extra_usage: true` when probe status is
   `extra_usage_required`; otherwise `false`.
6. Mark `recommended: true` for the LATEST generation of each family
   and for the default. Older generations → `recommended: false`.
7. `family` must be exactly one of: {_FAMILIES_BY_STRENGTH}.
8. Every model MUST have fields: id, display, family, generation,
   context_window (int), max_output (int), requires_extra_usage (bool),
   recommended (bool). `display` = human label like "Claude Sonnet 4.6 (1M)".
9. Pull `context_window` and `max_output` directly from the probe results
   (the CLI reported them). Do not invent values.
10. Expected total size: ~5-8 entries. If you produce more than 10,
    something is wrong — prune older generations.

PROBE RESULTS — CLI loaded these IDs successfully:
{json.dumps(ok_probes, indent=2)}

PROBE RESULTS — CLI accepts but backend requires extra_usage (mark
requires_extra_usage: true; context_window = 1000000, max_output should
match the family's base model):
{json.dumps(extra_probes, indent=2)}

CURRENT REGISTRY (for style reference only — replace, don't merge):
{json.dumps(current, indent=2)}

Output ONLY the new JSON object. No markdown fences. The output must be a
valid JSON object with keys: schema_version (always {_SCHEMA_VERSION}),
last_updated (current UTC ISO8601), source ("refresh"), note, models (array).
"""

    reply = runtime.exec(content=[{"type": "text", "text": prompt}])

    # Strip any markdown fence the LLM might add despite instructions
    text = reply.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

    try:
        new_data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ModelRegistryError(f"LLM produced invalid JSON: {e}") from e

    # Force-stamp last_updated with actual UTC now (LLM might hallucinate a date)
    new_data["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_data["schema_version"] = _SCHEMA_VERSION
    new_data.setdefault("source", "refresh")

    # Schema check BEFORE overwriting the good file
    validate_schema(new_data)

    _atomic_write(CLAUDE_MODELS_JSON_PATH, new_data)
    return new_data


# Deferred import so this module is importable without triggering the full
# agentic stack during simple reads.
def _get_decorator():
    from openprogram.agentic_programming.function import agentic_function
    return agentic_function


# Build the decorated agentic function at import time.
_agentic_function = _get_decorator()


@_agentic_function(input={"runtime": {"hidden": True}})
def refresh_claude_models(runtime=None) -> str:
    """Refresh openprogram/providers/claude_models.json.

    Pipeline:
      1. Pull Anthropic API /v1/models (authoritative; empty list if no key).
      2. For each model whose generation matches the kept set (4.6 / 4.7 today),
         probe the Claude Code CLI with --model <id> to verify it loads and
         capture the real contextWindow from modelUsage. Probe both base and
         [1m] variants. Detect "Extra usage required" errors separately.
      3. Send the probe results + the current registry to the LLM and have it
         produce a new JSON object obeying the rules in `claude_models.py`'s
         module docstring.
      4. Validate the LLM output against the frozen schema. If it fails,
         raise without overwriting the file — the old registry stays intact.
      5. Atomic write (tmp + rename).

    Safety:
      - validate_schema() is called before overwriting, so a bad LLM reply
        cannot replace a good file.
      - doctor() in this module restores the embedded seed if the file is
        ever left in an unreadable state.
      - Atomic file replacement prevents partial writes.

    Scope:
      Only keeps current generations (4.6 + 4.7). Older generations (3.x,
      4.0–4.5) are pruned. When Claude 5.x ships, update _KEPT_GENERATIONS
      in this module (the docstring rules will flow through to the prompt
      automatically) and the next refresh will drop 4.6 and add 5.0.

    Returns: short summary string (e.g. "registry updated: 6 models").
    """
    new_data = _refresh_impl(runtime)
    n = len(new_data["models"])
    return f"registry updated: {n} models, last_updated={new_data['last_updated']}"
