"""Whether a model tool's fetch may reach any address."""
from __future__ import annotations


def owner_open() -> bool:
    """The current turn is the owner's and ``security.outbound_url.model_tools`` is
    ``open``. A check-in carries the owner's authority yet reads pages nobody
    is watching, so it stays on the strict consumer that refuses private addresses."""
    from openprogram.runtime.agent.turn_request import get_turn_request
    from openprogram.runtime.permissions.authority import owner_principal_id
    from openprogram.runtime.permissions.identity import check_in
    from openprogram.state.authority import normalize_authority
    from openprogram.state.config import read_config

    request = get_turn_request()
    if request is None or check_in(request):
        return False
    authority = normalize_authority(request)
    if not authority or authority.get("authority_tier") != "owner":
        return False
    try:
        if authority.get("principal_id") != owner_principal_id():
            return False
    except Exception:  # noqa: BLE001 - an unknown owner keeps the strict consumer
        return False
    section = ((read_config().get("security") or {}).get("outbound_url") or {})
    return section.get("model_tools", "open") == "open"
