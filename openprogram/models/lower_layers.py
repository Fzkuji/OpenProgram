"""What the models layer hands down to the state layer and between its parts.

``openprogram.state`` sits below the models layer and never imports it.
What it needs from it arrives through ports it owns, filled in here:

* ``state.model_facts`` — each provider's status (the settings list's
  Providers rows), how a model call's failure classifies and the usage
  scope a memory write's model calls are counted under;
* ``state.setting_rules`` — the checks of the settings the models layer
  owns: a replay recording must load, and changing the prompt-cache
  lifetime tells the prompt-cache check the next requests change on
  purpose.

Inside the layer the parts are ordered: credentials (``auth``), then the
provider adapters and their catalogue (``providers``), then usage metering
(``usage``), then what the interfaces show and change (``listing``), with
the gateway beside listing. A lower part never imports a higher one; what
it needs from one arrives through a port it owns, filled in here too (left
in ``models.part_ports``, so filling them loads none of the parts):

* ``auth.provider_port`` — the adapters' refresh hooks and sign-in steps,
  what the catalogue says about a provider and whether an error says a
  balance ran out;
* ``auth.context`` — the conversation the running call's usage context
  names;
* ``providers.usage_port`` — what the provider call path tells usage
  metering (quota headers, finished and failed requests, a fallback
  switch) and asks of it (who is calling, a Goal's receipt, the
  prompt-cache check);
* ``providers.catalog.listed_models`` — a model the provider lists but no
  one enabled.

``providers.catalog.enabled_models`` (the enabled models changed) is filled
by the Worker alone as it starts (``interfaces/server/server.py``): only the
Worker keeps connected outside agents' files in step, never a CLI command.

The root package installs these before any other module runs. Each
provider imports its models module when it is called, never here, so
loading this stays cheap and a test that replaces a models function is
what the state layer and the lower parts see.
"""
from __future__ import annotations

from typing import Any


from openprogram.models import part_ports
from openprogram.state import model_facts, setting_rules


def _provider_status() -> dict[str, dict[str, Any]]:
    from openprogram.models.providers.catalog import registry

    return registry.check_providers()


def _classify_failure(exc: BaseException) -> tuple[str, bool]:
    from openprogram.models.providers.utils.failures import errors

    reason, retryable = errors.classify_error(exc)
    return reason.value, retryable


def _usage_scope(*, call_kind: str, session_id: str):
    from openprogram.models import usage

    return usage.usage_scope(call_kind=call_kind, session_id=session_id)


def _check_replay_recording(_value: Any, config: dict) -> str | None:
    """A replay recording must load before ``record_replay`` points at it."""
    section = config.get("record_replay")
    section = section if isinstance(section, dict) else {}
    if section.get("mode") != "replay":
        return None
    try:
        from openprogram.models.providers.recording.recording import resolve_recording_selector
        from openprogram.models.providers.recording.replay import ReplayProvider

        ReplayProvider(resolve_recording_selector(str(section.get("file", ""))))
    except (OSError, ValueError, RuntimeError) as exc:
        return f"invalid replay recording: {exc}"
    return None


def _prompt_cache_ttl_written(_value: Any) -> None:
    # Every conversation's next request changes its cache lifetime on
    # purpose: the prompt-cache check records it as expected.
    from openprogram.models.usage.prefix_fingerprint import expect_break_everywhere

    expect_break_everywhere("ttl_switch")



# --- auth.provider_port -----------------------------------------------------

def _register_auth_adapters() -> None:
    from openprogram.models.providers.catalog import register

    register.register_auth_adapters()


def _is_local_provider(provider_id: str) -> bool:
    from openprogram.models.providers.catalog import local

    return local.is_local_provider(provider_id)


def _label_for(provider_id: str) -> str | None:
    from openprogram.models.providers.catalog import metadata

    return metadata.label_for(provider_id)


def _catalogue_env_var(provider_id: str) -> str | None:
    from openprogram.models.providers.catalog import metadata

    return metadata.catalogue_env_var(provider_id)


def _default_api_for(provider_id: str) -> str | None:
    from openprogram.models.providers.catalog import metadata

    return metadata.default_api_for(provider_id)


def _catalogue_providers() -> list[dict]:
    from openprogram.models.providers.catalog.sources import models_dev

    return models_dev.list_providers()


def _is_billing_exhausted(text: str) -> bool:
    from openprogram.models.providers.utils.failures import errors

    return errors.is_billing_exhausted(text)


# --- auth.context -----------------------------------------------------------

def _call_conversation() -> str:
    from openprogram.models.usage import context

    ctx = context.current_usage_context()
    return ctx.parent_session_id or ctx.goal_session_id or ctx.session_id or ""


# --- providers.usage_port ---------------------------------------------------

def _observe_headers(provider: str, token, headers, *, account=None):
    from openprogram.models.usage import quota

    return quota.observe_headers(provider, token, headers, account=account)


def _exhausted_report(provider: str, account=None):
    from openprogram.models.usage import quota

    return quota.exhausted_report(provider, account)


def _exhausted_limit(provider: str, account=None):
    from openprogram.models.usage import quota

    return quota.exhausted_limit(provider, account)


def _after_failure(provider: str, exc: BaseException) -> None:
    from openprogram.models.usage import quota_fetchers

    quota_fetchers.after_failure(provider, exc)


def _call_context():
    from openprogram.models.usage import context

    return context.call_context()


def _request_context(session_id=None):
    from openprogram.models.usage import context

    return context.request_context(session_id)


def _usage_scope_of(**fields):
    from openprogram.models import usage

    return usage.usage_scope(**fields)


def _build_message_event(model, message, **kwargs):
    from openprogram.models.usage import recorder

    return recorder.build_message_event(model, message, **kwargs)


def _run_usage_hooks(event) -> None:
    from openprogram.models.usage import recorder

    recorder.run_usage_hooks(event)


def _record_message(model, message, **kwargs):
    from openprogram.models import usage

    return usage.record_message(model, message, **kwargs)


def _record_failed_request(model, partial, **kwargs):
    from openprogram.models import usage

    return usage.record_failed_request(model, partial, **kwargs)


def _record_route_switch(**fields) -> None:
    from openprogram.models.usage import recorder

    recorder.record_route_switch(**fields)


def _begin_request(model, options, **kwargs):
    from openprogram.models.usage import request

    return request.RequestReceipt.begin(model, options, **kwargs)


def _check_request(captured):
    from openprogram.models.usage import prefix_fingerprint

    return prefix_fingerprint.check_request(captured)


def _settle_check(check, usage):
    from openprogram.models.usage import prefix_fingerprint

    return prefix_fingerprint.settle_check(check, usage)


# --- providers.catalog.listed_models ----------------------------------------

def _spec_row_for(provider_id: str, model_id: str):
    from openprogram.models.listing import listing

    return listing.spec_row_for(provider_id, model_id)


def register() -> None:
    model_facts.register(
        provider_status=_provider_status,
        classify_failure=_classify_failure,
        usage_scope=_usage_scope,
    )
    setting_rules.register("record_replay.mode", check=_check_replay_recording)
    setting_rules.register("record_replay.file", check=_check_replay_recording)
    setting_rules.register("models.prompt_cache_ttl", after_write=_prompt_cache_ttl_written)
    part_ports.fill(
        "openprogram.models.auth.provider_port",
        register_auth_adapters=_register_auth_adapters,
        is_local_provider=_is_local_provider,
        label_for=_label_for,
        catalogue_env_var=_catalogue_env_var,
        default_api_for=_default_api_for,
        catalogue_providers=_catalogue_providers,
        is_billing_exhausted=_is_billing_exhausted,
    )
    part_ports.fill("openprogram.models.auth.context", call_conversation=_call_conversation)
    part_ports.fill(
        "openprogram.models.providers.usage_port",
        observe_headers=_observe_headers,
        exhausted_report=_exhausted_report,
        exhausted_limit=_exhausted_limit,
        after_failure=_after_failure,
        call_context=_call_context,
        request_context=_request_context,
        usage_scope=_usage_scope_of,
        build_message_event=_build_message_event,
        run_usage_hooks=_run_usage_hooks,
        record_message=_record_message,
        record_failed_request=_record_failed_request,
        record_route_switch=_record_route_switch,
        begin_request=_begin_request,
        check_request=_check_request,
        settle_check=_settle_check,
    )
    part_ports.fill("openprogram.models.providers.catalog.listed_models", spec_row_for=_spec_row_for)

__all__ = ["register"]
