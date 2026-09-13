"""Compatibility import for subscription model enablement.

The canonical implementation and model catalogue live in
``login_seed_models``. Keep this module because older interactive login code
imports it directly, without maintaining a second copy of the defaults.
"""
from .login_seed_models import enable_default_models_on_login
from .login_seed_models import seed_default_models_if_logged_in

__all__ = [
    "enable_default_models_on_login",
    "seed_default_models_if_logged_in",
]
