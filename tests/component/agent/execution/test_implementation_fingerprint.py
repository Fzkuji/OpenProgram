"""A tool's implementation fingerprint depends only on its code.

Continuations compare it to the one saved at the safe point. The fingerprint
used to be marshal output, which changes with reference counts, so a
continuation refused tools that had not changed.
"""
import os
import subprocess
import sys

from openprogram.runtime.conversation.checkpoint import _callable_descriptor


def _tool(x):
    return x in {"a", "b"} and ("kept", 1.5, None)


def _other(x):
    return x in {"a", "c"} and ("kept", 1.5, None)


def test_more_references_do_not_change_the_fingerprint():
    before = _callable_descriptor(_tool)["code_sha256"]
    held = [*_tool.__code__.co_consts, *_tool.__code__.co_names, *_tool.__code__.co_varnames]
    assert _callable_descriptor(_tool)["code_sha256"] == before
    assert held


def test_the_hash_seed_does_not_change_the_fingerprint():
    script = (
        "from openprogram.runtime.conversation.checkpoint import _callable_descriptor\n"
        "def f(x):\n    return x in {'a', 'b', 'c', 'd', 'e'}\n"
        "print(_callable_descriptor(f)['code_sha256'])\n"
    )
    seen = {
        subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True,
                       env={**os.environ, "PYTHONHASHSEED": seed}).stdout
        for seed in ("1", "2", "3")
    }
    assert len(seen) == 1


def test_different_code_has_a_different_fingerprint():
    assert _callable_descriptor(_tool)["code_sha256"] != _callable_descriptor(_other)["code_sha256"]
