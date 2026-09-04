"""Restricted operations on the active verifier's isolated test object."""
from openprogram.self_update.ui_checks import handle_test_object

ACTIONS = {
    "self_update_test_object": handle_test_object,
}
