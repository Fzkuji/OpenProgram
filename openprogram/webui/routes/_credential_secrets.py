"""Shared Web UI rules for credential names, values, and masked display."""
from __future__ import annotations


_SHORT_CREDENTIAL_MASK = "•" * 8


def mask_credential(value: str) -> str:
    """Return the stable, non-retrievable credential presentation."""
    if not value:
        return ""
    visible = value[:3] + value[-4:]
    if len(value) >= 12 and all(ord(char) < 0x80 for char in visible):
        return f"{value[:3]}…{value[-4:]}"
    return _SHORT_CREDENTIAL_MASK


def is_nonempty_printable_ascii(value: object) -> bool:
    """Whether ``value`` is a non-empty string in ASCII's printable range."""
    return isinstance(value, str) and bool(value) and all(
        0x20 <= ord(char) <= 0x7E for char in value
    )


def declared_credential_names() -> frozenset[str]:
    """Every credential env-var name any live registry declares.

    Three sources, all read at call time so a provider registered after
    import still counts:

      * the static LLM provider table (``_PROVIDER_ENV_VARS``),
      * the models.dev community catalogue — 160+ providers with no static
        row, whose env-var names would otherwise be undeclared and so
        unsettable through the Web UI,
      * the Web-search provider registry (``requires_env``).
    """
    names: set[str] = set()

    from openprogram.providers.env_api_keys import _PROVIDER_ENV_VARS

    for declared in _PROVIDER_ENV_VARS.values():
        names.update(declared)

    # Community providers arrive from the models.dev catalogue, not from any
    # table in this repo. A cold / unreachable cache yields [] — the static
    # names above still answer, so this only ever widens the set.
    try:
        from openprogram.providers.sources import models_dev

        for provider in models_dev.list_providers():
            env_var = provider.get("env_var")
            if isinstance(env_var, str) and env_var:
                names.add(env_var)
    except Exception:
        pass

    # Importing the provider package performs the registry registrations.
    import openprogram.programs.functions.web_search.providers  # noqa: F401
    from openprogram.programs.functions.web_search.registry import registry

    for provider in registry.all():
        names.update(getattr(provider, "requires_env", ()) or ())

    return frozenset(names)


def is_declared_credential_name(name: object) -> bool:
    """Whether a provider or Web-search registry declares ``name``."""
    if not isinstance(name, str) or not name:
        return False
    return name in declared_credential_names()


def has_credential_field(body: object) -> bool:
    """Whether a request body carries a field that could hold a secret.

    The account and provider-config endpoints administer accounts — they
    never accept a credential. A key-shaped field arriving there means the
    caller is aiming at the wrong endpoint, and answering anything but a
    rejection risks storing a secret on a path with no rotation contract.
    """
    if not isinstance(body, dict):
        return False
    return any(
        isinstance(key, str)
        and (
            "api_key" in key.lower()
            or "apikey" in key.lower()
            or "secret" in key.lower()
            or "token" in key.lower()
            or "password" in key.lower()
            or "credential" in key.lower()
            or is_declared_credential_name(key)
        )
        for key in body
    )


def check_request_body(body: object, allowed: set[str], required: set[str] = frozenset()):
    """Validate a request body against an exact field set.

    Returns an error string, or ``None`` when the body is acceptable: a
    JSON object whose fields are all in ``allowed``, that carries every
    field in ``required``, and that holds no credential-shaped field.
    """
    if not isinstance(body, dict):
        return "body must be a JSON object"
    if has_credential_field(body):
        return "this endpoint does not accept credentials"
    unknown = sorted(set(body) - set(allowed))
    if unknown:
        return f"unknown field: {unknown[0]}"
    absent = sorted(set(required) - set(body))
    if absent:
        return f"missing field: {absent[0]}"
    return None


__all__ = [
    "check_request_body",
    "declared_credential_names",
    "has_credential_field",
    "is_declared_credential_name",
    "is_nonempty_printable_ascii",
    "mask_credential",
]
