"""
Auth v2 — interactive login methods.

Every ``LoginMethod`` produces a fresh :class:`Credential`. The manager
stores it; the provider plugin system wires up which methods a given
provider supports. Methods are kept deliberately decoupled from
providers: a PKCE flow doesn't know which provider it's being run for
beyond a small :class:`PkceConfig` it's handed at construction time.

Implemented:

  * :mod:`.pkce_oauth`      — browser + localhost callback, PKCE S256
  * :mod:`.device_code`     — RFC 8628 device authorization
  * :mod:`.api_key_paste`   — simplest: one prompt, one key
  * :mod:`.cli_import`      — read another CLI's on-disk auth file
  * :mod:`.credential_process`— shell out to a helper each API call
  * :mod:`.sso`             — protocol stub (enterprise SSO placeholder)

See individual modules for design notes. The pattern is consistent:
each exposes a subclass of :class:`openprogram.auth.types.LoginMethod`
with a concrete ``run(ui)`` implementation that returns a
:class:`Credential`.
"""
from .api_key_paste import ApiKeyPasteMethod
from .cli_import import CliImportMethod
from .device_code import DeviceCodeConfig, DeviceCodeMethod
from .credential_process import CredentialProcessMethod
from .pkce_oauth import PkceConfig, PkceLoginMethod
from .sso import SsoStubMethod

__all__ = [
    "ApiKeyPasteMethod",
    "CliImportMethod",
    "DeviceCodeConfig", "DeviceCodeMethod",
    "CredentialProcessMethod",
    "PkceConfig", "PkceLoginMethod",
    "SsoStubMethod",
]
