"""The checkout gate keys on ``caller``, not ``predecessor``.

``predecessor`` has been the CONVERSATION-chain edge since the
parent/called_by rename — every turn after the first carries one, so a
predecessor-based gate rejects the entire chain (the "why can't I fork
any reply" bug). Chain-level turns carry ``caller`` of "ROOT" (user
turns, ROOT-hung code records) or "" (reply nodes); only a node whose
caller is another call is function-internal.
"""
from types import SimpleNamespace

from openprogram.webui._chat_routes import is_checkout_target


def _node(caller, predecessor):
    return SimpleNamespace(caller=caller, predecessor=predecessor)


def test_first_user_turn_is_a_target():
    assert is_checkout_target(_node("ROOT", "ROOT"))


def test_later_user_turn_is_a_target():
    assert is_checkout_target(_node("ROOT", "some_reply_id"))


def test_reply_node_is_a_target():
    # On disk: 0002-l caller='' pred=<user turn>. The old
    # predecessor-based gate rejected exactly this node.
    assert is_checkout_target(_node("", "some_user_id"))


def test_root_hung_code_record_is_a_target():
    assert is_checkout_target(_node("ROOT", None))


def test_function_internal_node_is_rejected():
    assert not is_checkout_target(_node("some_call_id", None))


def test_function_internal_with_predecessor_is_rejected():
    # In-frame exec rows may chain among themselves; caller decides.
    assert not is_checkout_target(_node("some_call_id", "prev_exec_row"))
