"""claude-code provider registration.

claude-code connects DIRECT to api.anthropic.com on a Claude subscription
(OAuth). It has no list-models call baked in like HTTP providers.

Historically this module injected a small "seed" model set into
``ENABLED_MODELS`` at import time so the provider wouldn't vanish from the
settings UI with zero registry entries. That bypassed config: the picker then
showed rows the user never enabled. Per docs/design/providers/models/models.md
§4.2 the registry is now built from config spec rows ONLY; the default model
set is written to config as an *enable* on the user's behalf at login — see
``openprogram.auth.login_enable`` (the claude-code default set lives there).

Nothing is registered at import here anymore. The module is kept as the
documented home of the claude-code wire mapping (anthropic-messages +
https://api.anthropic.com, OAuth + 1M via beta headers) and imported by the
anthropic package for that documentation anchor.
"""
from __future__ import annotations
