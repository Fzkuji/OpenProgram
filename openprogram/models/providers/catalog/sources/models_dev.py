"""``models.dev`` enricher.

Pulls the public JSON catalogue at https://models.dev/api.json (the
same data OpenCode and several other AI-tooling projects use) and
normalises it into the schema described in ``sources.__init__``.

Stale-while-revalidate cache with a 1-hour fresh TTL, in memory and on disk
(the disk copy's age is its file's). Expired data is served immediately
while one background thread refreshes it; only a first start with no cache
waits for the network.
"""
from __future__ import annotations

import atexit
import logging
import threading
import time
from typing import Any

from openprogram.host._platform.filesystem import replace_file


_CATALOGUE_URL = "https://models.dev/api.json"
_TTL_SECONDS = 3600  # 1 hour on success
_FAIL_TTL_SECONDS = 60  # short retry window on failure/empty

_FETCH_TIMEOUT_SECONDS = 3
_cache_lock = threading.Lock()
_refresh_thread: threading.Thread | None = None
# While the ``openprogram.models.providers`` package is importing, a refresh
# is only noted (``_refresh_wanted``) and the thread starts when the package
# releases the hold. See hold_background_refresh.
_refresh_held = False
_refresh_wanted = False
_cache: dict[str, Any] = {
    "data": None,
    "fetched_at": 0.0,
    "last_attempt_at": 0.0,
    "refreshing": False,
}
_logger = logging.getLogger(__name__)


def __getattr__(name: str) -> Any:
    """``safe_http``, imported when the catalogue is first fetched: it brings
    in the HTTP client (0.07 s), and every process that reads a model imports
    this module while few of them refresh the catalogue."""
    if name == "safe_http":
        from openprogram.host.security import safe_http

        globals()["safe_http"] = safe_http
        return safe_http
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


#: The fields of a catalogue row this module reads. The catalogue carries
#: ten thousand models; kept whole it holds 26 MB for as long as the process
#: lives, most of it descriptions and ids nothing here reads.
_PROVIDER_FIELDS = ("name", "env", "api", "doc", "npm")
_MODEL_FIELDS = (
    "name", "family", "knowledge", "release_date", "last_updated", "open_weights",
    "reasoning", "tool_call", "structured_output", "attachment", "temperature",
    "modalities", "limit", "cost", "experimental",
)


def _slim(data: dict[str, Any]) -> dict[str, Any]:
    """The catalogue with only the fields read here, its repeated values shared.

    The same model is listed under many providers with the same limits,
    prices and modalities, so equal values become one object. Nothing here
    changes a row after it is loaded; readers copy what they hand out.
    """
    import json
    import sys

    values: dict[str, Any] = {}

    def shared(value: Any) -> Any:
        if isinstance(value, str):
            return sys.intern(value)
        if isinstance(value, (list, dict)):
            key = json.dumps(value, sort_keys=True)
            kept = values.get(key)
            if kept is None:
                if isinstance(value, list):
                    kept = [shared(item) for item in value]
                else:
                    kept = {sys.intern(k) if isinstance(k, str) else k: shared(v)
                            for k, v in value.items()}
                values[key] = kept
            return kept
        return value

    out: dict[str, Any] = {}
    for pid, provider in data.items():
        if not isinstance(provider, dict):
            continue
        row = {key: shared(provider[key]) for key in _PROVIDER_FIELDS if key in provider}
        models = provider.get("models")
        if isinstance(models, dict):
            row["models"] = {
                mid: {key: shared(model[key]) for key in _MODEL_FIELDS if key in model}
                for mid, model in models.items() if isinstance(model, dict)
            }
        out[pid] = row
    return out


def _fetch_catalogue(timeout: float) -> dict[str, Any]:
    safe_http = __getattr__("safe_http")
    try:
        with safe_http.safe_client("webui.model_listing.fixed") as client:
            response = client.get(_CATALOGUE_URL, timeout=timeout)
            response.raise_for_status()
            safe_http.require_json_mime(response)
            data = response.json()
        if not isinstance(data, dict):
            return {}
        if data:
            _write_disk_cache(data)
        return _slim(data)
    except Exception:
        return {}


def _finish_refresh(data: dict[str, Any]) -> None:
    now = time.time()
    with _cache_lock:
        if data:
            _cache.update(
                data=data,
                fetched_at=now,
                last_attempt_at=now,
                refreshing=False,
            )
        else:
            # Preserve stale success data. With no cache, remember the empty
            # failure briefly so a cold-start burst does not retry immediately.
            if not _cache["data"]:
                _cache.update(data={}, fetched_at=now)
            _cache.update(last_attempt_at=now, refreshing=False)


def _refresh_cache() -> None:
    _finish_refresh(_fetch_catalogue(timeout=_FETCH_TIMEOUT_SECONDS))


def _join_refresh() -> None:
    """Let an in-flight refresh finish before the interpreter finalizes.

    A daemon thread still inside the fetch when Python 3.12 tears the
    interpreter down is exited mid-call, which glibc reports as "FATAL:
    exception not rethrown" and a SIGABRT. The fetch is bounded by its
    timeout, so the wait is too.
    """
    _exiting.set()
    thread = _refresh_thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=_FETCH_TIMEOUT_SECONDS + 1)


atexit.register(_join_refresh)


def hold_background_refresh() -> None:
    """Postpone refresh threads until :func:`release_background_refresh`.

    The ``openprogram.models.providers`` package builds the enabled-model
    registry while it imports, and that read can find the catalogue stale
    and want a refresh. A thread started at that point imports
    ``utils.http_transport.http_proxy`` on its way to the socket, which needs the package
    lock the importing thread still holds, while the importing thread goes
    on to import ``utils`` itself and waits for the lock the thread took
    first: Python reports the cycle as ``_DeadlockError`` and the import
    fails. The package calls this before the registry loads and releases
    the hold as its last statement, so the thread only ever starts against
    a fully imported package.
    """
    global _refresh_held
    with _cache_lock:
        _refresh_held = True


def release_background_refresh() -> None:
    """Lift the import-time hold and start the refresh it postponed, if any."""
    global _refresh_held, _refresh_wanted
    with _cache_lock:
        _refresh_held = False
        wanted, _refresh_wanted = _refresh_wanted, False
    if wanted:
        _spawn_refresh_thread()


def _start_background_refresh() -> None:
    """Refresh the catalogue on a daemon thread; the caller has marked
    ``_cache["refreshing"]``. Under the import-time hold the start waits
    for release_background_refresh."""
    global _refresh_wanted
    with _cache_lock:
        if _refresh_held:
            _refresh_wanted = True
            return
    _spawn_refresh_thread()


#: A refresh wanted while the process is starting waits this long after its
#: start. The first read of a Worker comes from the first screen of the App,
#: which is served from the copy on disk; fetching, parsing and rewriting the
#: catalogue (7 MB, 0.3 s of the interpreter) beside it only slows that screen.
_QUIET_START_SECONDS = 20.0
_process_started = time.monotonic()
_exiting = threading.Event()


def _refresh_after_start() -> None:
    wait = _QUIET_START_SECONDS - (time.monotonic() - _process_started)
    # A process that ends first (a command) leaves the refresh to the Worker.
    if wait > 0 and _exiting.wait(wait):
        return
    _refresh_cache()


def _spawn_refresh_thread() -> None:
    global _refresh_thread
    try:
        _refresh_thread = threading.Thread(
            target=_refresh_after_start,
            name="models-dev-refresh",
            daemon=True,
        )
        _refresh_thread.start()
    except Exception:
        with _cache_lock:
            _cache.update(last_attempt_at=time.time(), refreshing=False)


def _load() -> dict[str, Any]:
    """Return fresh catalogue data, or stale data while it revalidates."""
    now = time.time()
    start_refresh = False
    with _cache_lock:
        data = _cache["data"]
        if data and now - _cache["fetched_at"] < _TTL_SECONDS:
            return data
        if data:
            if (
                not _cache["refreshing"]
                and now - _cache["last_attempt_at"] >= _FAIL_TTL_SECONDS
            ):
                _cache.update(refreshing=True, last_attempt_at=now)
                start_refresh = True
            stale = data
        else:
            stale = None
            failed_recently = (
                data is not None
                and now - _cache["fetched_at"] < _FAIL_TTL_SECONDS
            )

    if stale is not None:
        if start_refresh:
            _start_background_refresh()
        return stale

    # Disk data is usable regardless of its age. Reading it before the cold
    # network path is what keeps session hydration non-blocking. Its age is
    # the file's: each command is a new process, and one that took the file
    # for stale whatever its age fetched the whole catalogue again and
    # waited for that fetch on its way out.
    disk_data = _read_disk_cache()
    if disk_data:
        written_at = min(_disk_cache_written_at(), now)
        with _cache_lock:
            current = _cache["data"]
            if current:
                return current
            _cache.update(data=disk_data, fetched_at=written_at)
            if now - written_at < _TTL_SECONDS:
                return disk_data
            if not _cache["refreshing"]:
                _cache.update(refreshing=True, last_attempt_at=now)
                start_refresh = True
        if start_refresh:
            _start_background_refresh()
        return disk_data

    if failed_recently:
        return {}

    # A first start with no memory or disk cache is the only synchronous
    # network path. Coordinate concurrent callers so only one request starts.
    with _cache_lock:
        data = _cache["data"]
        if data:
            return data
        if data is not None and time.time() - _cache["fetched_at"] < _FAIL_TTL_SECONDS:
            return data
        if _cache["refreshing"]:
            return {}
        _cache.update(refreshing=True, last_attempt_at=time.time())

    data = _fetch_catalogue(timeout=3)
    _finish_refresh(data)
    return data


def _disk_cache_path():
    from openprogram.state.paths import get_state_dir

    d = get_state_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / "cache" / "models_dev.json"


def _disk_cache_written_at() -> float:
    """When the disk copy was last fetched; 0 when that cannot be told."""
    try:
        return _disk_cache_path().stat().st_mtime
    except OSError:
        return 0.0


def _write_disk_cache(data: dict[str, Any]) -> None:
    import json

    try:
        path = _disk_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        replace_file(tmp, path)
    except Exception:
        # Disk cache is best-effort; the next fetch rewrites it.
        _logger.debug("models.dev disk cache not written", exc_info=True)


def _read_disk_cache() -> dict[str, Any]:
    import json

    path = None
    try:
        path = _disk_cache_path()
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError("models.dev cache must be an object")
        return _slim(data)
    except FileNotFoundError:
        return {}
    except (OSError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        _logger.warning(
            "provider metadata load failed",
            extra={
                "source": "models_dev_cache",
                "path": str(path) if path is not None else "<unresolved>",
                "error_type": type(exc).__name__,
            },
        )
        return {}


def _normalise(raw: dict[str, Any]) -> dict[str, Any]:
    """Map the models.dev row shape onto our internal schema.

    Captures every field the catalogue exposes that the UI can do
    something useful with — capabilities (tool_call, reasoning,
    structured_output, attachment), full modality lists, all limit
    components (context / input cap / output cap), full pricing
    surface (input / output / cache_read / cache_write), and the
    metadata block (family, knowledge cutoff, release / update
    dates, open-weights flag).

    Anything missing from the upstream row is just omitted from the
    output — the React side checks for ``!= undefined`` and hides the
    corresponding line in the expanded panel.
    """
    out: dict[str, Any] = {}
    # identity / metadata
    for src_key, dst_key in (
        ("name", "name"),
        ("family", "family"),
        ("knowledge", "knowledge_cutoff"),
        ("release_date", "release_date"),
        ("last_updated", "last_updated"),
    ):
        v = raw.get(src_key)
        if v is not None and v != "":
            out[dst_key] = v
    if raw.get("open_weights") is not None:
        out["open_weights"] = bool(raw["open_weights"])

    # capabilities
    if raw.get("reasoning") is not None:
        out["reasoning"] = bool(raw["reasoning"])
    if raw.get("tool_call") is not None:
        out["tools"] = bool(raw["tool_call"])
    if raw.get("structured_output") is not None:
        out["structured_output"] = bool(raw["structured_output"])
    if raw.get("attachment") is not None:
        out["attachment"] = bool(raw["attachment"])
    if raw.get("temperature") is not None:
        out["temperature_param"] = bool(raw["temperature"])

    # modalities
    modalities = raw.get("modalities") or {}
    in_mods = modalities.get("input") or []
    out_mods = modalities.get("output") or []
    if in_mods:
        out["input_modalities"] = list(in_mods)
    if out_mods:
        out["output_modalities"] = list(out_mods)
    # Legacy flat booleans the existing UI consumes for badge rendering.
    if "image" in in_mods:
        out["vision"] = True
    if "video" in in_mods:
        out["video"] = True
    if "audio" in in_mods:
        out["audio"] = True

    # limits
    limit = raw.get("limit") or {}
    if limit.get("context"):
        try:
            out["context_window"] = int(limit["context"])
        except (TypeError, ValueError, OverflowError):
            # Optional limit; an unparseable value leaves it unset.
            _logger.debug("models.dev limit.context %r unparseable", limit["context"])
    if limit.get("input"):
        try:
            out["input_limit"] = int(limit["input"])
        except (TypeError, ValueError, OverflowError):
            # Optional limit; an unparseable value leaves it unset.
            _logger.debug("models.dev limit.input %r unparseable", limit["input"])
    if limit.get("output"):
        try:
            out["max_tokens"] = int(limit["output"])
        except (TypeError, ValueError, OverflowError):
            # Optional limit; an unparseable value leaves it unset.
            _logger.debug("models.dev limit.output %r unparseable", limit["output"])

    # pricing (USD / 1M tokens)
    cost = raw.get("cost") or {}
    for src_key, dst_key in (
        ("input", "input_cost"),
        ("output", "output_cost"),
        ("cache_read", "cache_read_cost"),
        ("cache_write", "cache_write_cost"),
    ):
        if cost.get(src_key) is not None:
            try:
                out[dst_key] = float(cost[src_key])
            except (TypeError, ValueError):
                # Unknown price means usage for this model is recorded without cost.
                _logger.warning("models.dev cost.%s %r unparseable", src_key, cost[src_key])
    # Tiered pricing (e.g. OpenAI's >200K context surcharge). Pass
    # through verbatim — the UI just renders it as JSON in the
    # expanded panel for now.
    if cost.get("tiers"):
        out["cost_tiers"] = cost["tiers"]
    if cost.get("context_over_200k"):
        out["cost_context_over_200k"] = cost["context_over_200k"]

    # Speed / priority modes (``experimental.modes`` — e.g. OpenAI's
    # "fast" = service_tier:priority). Normalised into a small list the
    # composer's speed pill consumes: ``[{id, service_tier, cost}]``.
    # When a model has none, the pill simply doesn't render for it.
    modes = (raw.get("experimental") or {}).get("modes") or {}
    speed_modes: list[dict[str, Any]] = []
    if isinstance(modes, dict):
        for mode_id, spec in modes.items():
            if not isinstance(spec, dict):
                continue
            body = ((spec.get("provider") or {}).get("body") or {})
            tier = body.get("service_tier")
            entry: dict[str, Any] = {"id": mode_id}
            if tier:
                entry["service_tier"] = tier
            if isinstance(spec.get("cost"), dict):
                entry["cost"] = spec["cost"]
            speed_modes.append(entry)
    if speed_modes:
        out["speed_modes"] = speed_modes
    return out


def lookup(provider_id: str, model_id: str) -> dict[str, Any] | None:
    """Resolve ``(provider_id, model_id)`` in the cached catalogue.
    Returns the normalised metadata dict, or ``None`` when not
    present.

    models.dev uses the same lowercase short provider ids we do for
    most cases (``deepseek``, ``openai``, ``anthropic``, ``groq``,
    ``cerebras``, ``openrouter``, …); ``_PROVIDER_ID_ALIASES`` covers
    the few that differ.
    """
    catalogue = _load()
    if not catalogue:
        return None
    pid = _PROVIDER_ID_ALIASES.get(provider_id, provider_id)
    provider = catalogue.get(pid)
    if not isinstance(provider, dict):
        return None
    models = provider.get("models")
    if not isinstance(models, dict):
        return None
    raw = models.get(model_id)
    if not isinstance(raw, dict):
        return None
    return _normalise(raw) or None


def conservative_limits(model_id: str) -> dict[str, int] | None:
    """Return conservative token limits for an exact model id.

    Custom OpenAI-compatible gateways often expose only ``id`` from their
    ``/models`` endpoint.  When that id also exists in models.dev, use the
    smallest positive limits advertised by any provider carrying the exact
    id.  Unknown ids remain unknown so governed execution still fails closed.
    """
    catalogue = _load()
    contexts: list[int] = []
    outputs: list[int] = []
    for provider in catalogue.values():
        if not isinstance(provider, dict):
            continue
        models = provider.get("models")
        raw = models.get(model_id) if isinstance(models, dict) else None
        if not isinstance(raw, dict):
            continue
        row = _normalise(raw)
        context = row.get("context_window")
        output = row.get("max_tokens")
        if isinstance(context, int) and context > 0:
            contexts.append(context)
        if isinstance(output, int) and output > 0:
            outputs.append(output)
    if not contexts:
        return None
    result = {"context_window": min(contexts)}
    if outputs:
        result["max_tokens"] = min(outputs)
    return result


def list_models(provider_id: str) -> dict[str, dict[str, Any]]:
    """Every model the catalogue knows for ``provider_id`` (alias-aware),
    as ``{model_id: normalised_dict}``.

    Honours the same ``_PROVIDER_ID_ALIASES`` map as :func:`lookup`, so e.g.
    ``openai-codex`` resolves to the upstream ``openai`` catalogue. Returns
    ``{}`` on a cache miss / unknown provider. This is the live source a
    no-list-endpoint provider (Codex) can fetch from instead of shipping a
    hand-maintained list."""
    catalogue = _load()
    if not catalogue:
        return {}
    pid = _PROVIDER_ID_ALIASES.get(provider_id, provider_id)
    provider = catalogue.get(pid)
    if not isinstance(provider, dict):
        return {}
    models = provider.get("models")
    if not isinstance(models, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for mid, raw in models.items():
        if isinstance(raw, dict):
            out[mid] = _normalise(raw)
    return out


# ---------------------------------------------------------------------------
# Provider-level (catalog-wide) accessors
# ---------------------------------------------------------------------------

def _normalise_provider(pid: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Pick the fields we care about out of a models.dev provider row.

    Shape returned matches what ``providers.py`` consumes:

      * ``label`` — display name (str)
      * ``env_var`` — primary env var holding the API key (str | None)
      * ``base_url`` — default API base URL (str | None)
      * ``doc_url`` — link to provider docs (str | None)
      * ``npm`` — vendor SDK on npm (str | None) — informational
      * ``model_ids`` — full id list of models in the catalogue (list)
    """
    env = raw.get("env")
    env_var: str | None = None
    if isinstance(env, list) and env:
        env_var = str(env[0]) if env[0] else None
    elif isinstance(env, str) and env:
        env_var = env

    models = raw.get("models") or {}
    model_ids = list(models.keys()) if isinstance(models, dict) else []

    return {
        "id": pid,
        "label": raw.get("name") or pid,
        "env_var": env_var,
        "base_url": raw.get("api") or None,
        "doc_url": raw.get("doc") or None,
        "npm": raw.get("npm") or None,
        "model_ids": model_ids,
    }


def provider_info(provider_id: str) -> dict[str, Any] | None:
    """Look up provider-level metadata. Honours the same id alias map
    as ``lookup()``, so e.g. ``openai-codex`` falls back to the
    ``openai`` row when no Codex-specific entry exists in the
    catalogue."""
    catalogue = _load()
    if not catalogue:
        return None
    # First try the verbatim id (a few providers like ``openai-codex``
    # genuinely have a distinct entry).
    raw = catalogue.get(provider_id)
    if not isinstance(raw, dict):
        # Fall back to alias mapping for "shares the same upstream
        # catalogue" cases.
        aliased = _PROVIDER_ID_ALIASES.get(provider_id)
        if aliased:
            raw = catalogue.get(aliased)
    if not isinstance(raw, dict):
        return None
    return _normalise_provider(provider_id, raw)


def list_providers() -> list[dict[str, Any]]:
    """Every provider in the cached catalogue, normalised. Used by the
    listing layer to surface community-known providers we haven't yet
    hard-coded in ``providers._PROVIDER_LABELS`` etc."""
    global _listed
    catalogue = _load()
    if not catalogue:
        return []
    # The list is asked for dozens of times per request (once per credential
    # looked up), and the catalogue it is made from changes once a day.
    source, rows = _listed
    if source is not catalogue:
        rows = [
            _normalise_provider(pid, raw)
            for pid, raw in catalogue.items()
            if isinstance(raw, dict) and isinstance(raw.get("models"), dict)
        ]
        _listed = (catalogue, rows)
    return [dict(row) for row in rows]


#: The catalogue :func:`list_providers` last normalised, and its rows.
_listed: tuple[Any, list[dict[str, Any]]] = (None, [])


# Provider id mapping for the few cases where our id differs from the
# models.dev key. Empty for now — DeepSeek / OpenAI / Anthropic /
# Groq / Cerebras / OpenRouter / Mistral / HuggingFace all match
# verbatim. Add entries here if a future provider does need
# translation.
_PROVIDER_ID_ALIASES: dict[str, str] = {
    # Our id            : models.dev id
    "openai-codex": "openai",        # models.dev tracks one OpenAI catalogue
    "gemini-subscription": "google",  # CodeAssist surfaces the same Gemini set
}
