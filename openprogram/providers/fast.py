"""Route-aware Fast capabilities shared by UI projection and dispatch."""
from __future__ import annotations

from urllib.parse import urlsplit


def fast_capability(provider: str | None, model_id: str | None, *, model=None) -> dict[str, str]:
    from ._config_read import read_providers_config
    from .models import get_model

    def result(status, source):
        return {'status': status, 'source': source}

    if not provider or not model_id:
        return result('unknown', 'missing-model')
    model_id = model_id.removeprefix(f'{provider}:')
    model = model or get_model(provider, model_id)
    cfg = read_providers_config().get(provider) or {}
    rows = cfg.get('models') or []
    if isinstance(rows, dict):
        rows = [dict(v, id=k) for k, v in rows.items() if isinstance(v, dict)]
    row = next((r for r in rows if isinstance(r, dict) and r.get('id') == model_id), {})
    if row.get('fast') is False:
        return result('unsupported', 'configuration')
    if provider == 'openai-codex':
        return result('supported' if getattr(model, 'fast', False) else 'unsupported', 'account-catalogue')
    if row.get('fast') is True:
        return result('supported', 'configuration')
    if provider == 'claude-code':
        from .enabled_models import default_fast
        return result('supported' if default_fast(model_id) else 'unsupported', 'provider-declaration')
    url = urlsplit(getattr(model, 'base_url', '') or '')
    if (provider == 'xai' and model_id == 'grok-4.6'
            and url.scheme == 'https' and url.netloc == 'api.x.ai'
            and url.path.rstrip('/') == '/v1'):
        return result('supported', 'xai-api')
    # Completions always rewrites this provider onto cli-chat-proxy; do not
    # require the catalog base_url (older logins may still list api.x.ai).
    if provider == 'xai-subscription':
        if model_id == 'grok-4.6':
            return result('supported', 'xai-subscription')
        return result('unknown', 'subscription-unverified')
    from .sources import models_dev
    try:
        entry = models_dev.lookup(provider, model_id) or {}
    except Exception:
        entry = {}
    if any(m.get('service_tier') == 'priority' or m.get('id') == 'fast'
           for m in entry.get('speed_modes') or []):
        return result('supported', 'model-catalogue')
    return result('unknown', 'unverified')


def resolve_service_tier(model, requested: str | None) -> str | None:
    """Prevent a preference from enabling priority on another model/route."""
    if requested not in ('priority', 'fast'):
        return requested
    capability = fast_capability(model.provider, model.id, model=model)
    return 'priority' if capability['status'] == 'supported' else None
