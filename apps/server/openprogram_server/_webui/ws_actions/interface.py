"""Authenticated interface presence and request-scoped operation receipts."""
from openprogram.framework.interface import register_window, result

ACTIONS = {
    'framework_interface_register': register_window,
    'framework_interface_result': result,
}
