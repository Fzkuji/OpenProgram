"""Build a Runtime for a provider: auto-detected, built-in or api-routed.

    from openprogram.runtime.agent.runtime.factory import create_runtime

    rt = create_runtime()                   # runtime for the detected provider
    rt = create_runtime(provider="anthropic", model="claude-sonnet-4-6")
    rt = create_runtime(provider="deepseek", model="deepseek-chat")  # api-routed

The provider side (the ``PROVIDERS`` table, detection, credential
resolution) lives in ``openprogram.models.providers.catalog.registry``; this
module turns it into a Runtime. The subscription/CLI-credential backends
get the class registered in ``openprogram.runtime.agent.provider_runtimes``;
plain API-key HTTP providers get the base ``Runtime`` with the key
resolved up front; every other provider gets the base ``Runtime`` routed
through its model's ``api``. Every runtime carries an authoritative
``provider_id``.
"""
from __future__ import annotations

from openprogram.models.providers.catalog import registry as _registry
import logging

_log = logging.getLogger(__name__)


def _api_routed_runtime(provider: str, model: str = None, **kwargs):
    """Build a Runtime for a provider that has no dedicated Runtime class
    (i.e. not in ``PROVIDERS``) but IS supported via its model's ``api``
    through the api_registry — every openai-/anthropic-compatible
    provider. The base ``Runtime("<provider>:<model>")`` resolves the
    model's wire api + base_url and streams via the registered api
    provider, the same path the chat dispatcher uses."""
    from openprogram.runtime.agent.runtime import Runtime
    from openprogram.models.providers.catalog.models import get_model
    from openprogram.models.providers.catalog.enabled_models import ENABLED_MODELS

    if not model:
        cands = [m for m in ENABLED_MODELS.values() if m.provider == provider]
        if cands:
            model = cands[0].id
    if not model:
        raise ValueError(
            f"Provider {provider!r} has no registered models — pass an "
            f"explicit model, or run `openprogram providers available "
            f"{provider}` / fetch its models first."
        )
    # Community / fetched models live in the user's config, not the static
    # registry — register so the model's api + base_url resolve here too
    # (mirrors the chat path's resolve_model).
    if get_model(provider, model) is None:
        try:
            from openprogram.models.providers.catalog.enabled_models import register_model_from_config
            register_model_from_config(provider, model)
        except Exception:
            # Optional registration; Runtime reports an unresolved model itself.
            _log.debug("model %s:%s not registered from config", provider, model, exc_info=True)
    return Runtime(model=f"{provider}:{model}", **kwargs)


def _replay_runtime(provider: str, model: str, entry: dict, kwargs: dict):
    from openprogram.runtime.agent.runtime import Runtime
    from openprogram.models.providers.catalog.models import get_model

    namespace = entry["model_namespace"]
    source = get_model(provider, model) or get_model(namespace, model)
    if source is None:
        from openprogram.models.providers.catalog.suggestions import unknown_model_message

        raise ValueError(unknown_model_message(provider, model))

    runtime_kwargs = {
        key: kwargs[key]
        for key in ("call", "max_retries", "api_key", "skills")
        if key in kwargs
    }
    runtime = Runtime(model="default", **runtime_kwargs)
    runtime.model = f"{namespace}:{model}"
    runtime.api_model = source.model_copy(update={"provider": namespace})
    runtime.provider_id = provider
    if "system" in kwargs:
        runtime.system = kwargs["system"]
    return runtime


def create_runtime(provider: str = None, model: str = None, **kwargs):
    """Create a Runtime instance with auto-detection or explicit provider.

    Args:
        provider:  Provider name (e.g. "anthropic", "claude-code",
                   "openai", "gemini-cli"). Pass "auto" or None to
                   auto-detect the best available provider via
                   detect_provider().
        model:     Model name override.
        **kwargs:  Forwarded to the Runtime constructor.

    Returns:
        A Runtime instance ready to use. Every runtime carries an
        authoritative ``provider_id`` attribute (derived from its model
        namespace, or set by the subscription runtime classes).
    """
    from openprogram.models.auth.provider_policy import UNSUPPORTED_PROVIDERS, API_KEY_REQUIRED
    if provider in UNSUPPORTED_PROVIDERS:
        raise ValueError(API_KEY_REQUIRED)
    from openprogram.models.providers.catalog.initialization import initialize_provider_runtime

    runtime_snapshot = initialize_provider_runtime()

    if (
        provider
        and isinstance(model, str)
        and model.startswith(f"{provider}:")
    ):
        model = model[len(provider) + 1:]

    if runtime_snapshot.mode == "replay":
        if not provider or provider == "auto":
            configured = _registry._load_provider_config()
            if configured is None:
                raise RuntimeError(
                    "Replay mode requires an explicit provider/model or a "
                    "last used model"
                )
            provider, detected_model = configured
            model = model or detected_model
        if provider not in _registry.PROVIDERS:
            from openprogram.models.providers.catalog.models import get_model

            source = get_model(provider, model) if model else None
            if source is None:
                from openprogram.models.providers.catalog.suggestions import unknown_model_message

                raise ValueError(unknown_model_message(provider, model or ""))
            return _replay_runtime(
                provider, model, {"model_namespace": source.provider}, kwargs
            )
        entry = _registry.PROVIDERS[provider]
        use_model = model or entry["default_model"]
        return _replay_runtime(provider, use_model, entry, kwargs)

    if provider and provider != "auto":
        if provider not in _registry.PROVIDERS:
            # ``PROVIDERS`` is NOT the list of supported providers — it
            # only holds the 6 built-in backends (claude-code,
            # openai-codex, gemini-cli, anthropic, openai, gemini). Every
            # other provider — deepseek, groq, openrouter, minimax, kimi,
            # everything models.dev knows — is supported through its
            # model's ``api`` + the api_registry, exactly how the chat
            # dispatcher streams them. Route those through the base
            # Runtime instead of failing, so create_runtime() matches
            # chat coverage.
            return _api_routed_runtime(provider, model, **kwargs)
        entry = _registry.PROVIDERS[provider]
    else:
        provider, detected_model = _registry.detect_provider()
        if provider in UNSUPPORTED_PROVIDERS:
            raise ValueError(API_KEY_REQUIRED)
        model = model or detected_model
        if provider not in _registry.PROVIDERS:
            return _api_routed_runtime(provider, model, **kwargs)
        entry = _registry.PROVIDERS[provider]
        # detect_provider returns None for CLI providers ("we found
        # the binary but don't have an opinion on which model") — the
        # table default below covers that.

    use_model = model or entry["default_model"]

    from openprogram.runtime.agent.provider_runtimes import runtime_class_for
    cls = runtime_class_for(provider)
    if cls is not None:
        return cls(model=use_model, **kwargs)

    # Plain API-key HTTP provider: the base Runtime + the api_registry wire
    # IS the runtime. Resolve the key up front so a missing credential fails
    # here with guidance instead of at first exec.
    from openprogram.runtime.agent.runtime import Runtime
    api_key = kwargs.pop("api_key", None) or _registry.http_api_key_for(entry)
    if not api_key:
        raise ValueError(entry["credential_error"])
    return Runtime(
        model=f"{entry['model_namespace']}:{use_model}", api_key=api_key, **kwargs
    )


__all__ = ["create_runtime"]
