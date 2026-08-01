import pytest
from retry import retry


def test_succeeds_first_try():
    calls = []

    @retry(attempts=3, exceptions=(ValueError,))
    def f():
        calls.append(1)
        return "ok"

    assert f() == "ok"
    assert len(calls) == 1


def test_retries_then_succeeds():
    calls = []

    @retry(attempts=3, exceptions=(ValueError,))
    def f():
        calls.append(1)
        if len(calls) < 3:
            raise ValueError("nope")
        return "ok"

    assert f() == "ok"
    assert len(calls) == 3


def test_exhausts_and_reraises():
    calls = []

    @retry(attempts=2, exceptions=(ValueError,))
    def f():
        calls.append(1)
        raise ValueError("always")

    with pytest.raises(ValueError):
        f()
    assert len(calls) == 2


def test_unlisted_exception_not_retried():
    calls = []

    @retry(attempts=3, exceptions=(ValueError,))
    def f():
        calls.append(1)
        raise KeyError("k")

    with pytest.raises(KeyError):
        f()
    assert len(calls) == 1


def test_backoff_and_hook(monkeypatch):
    slept, hooks = [], []
    import time as _t
    monkeypatch.setattr(_t, "sleep", lambda s: slept.append(s))

    @retry(attempts=4, exceptions=(ValueError,), backoff=0.5,
           on_retry=lambda e, n: hooks.append(n))
    def f():
        raise ValueError()

    with pytest.raises(ValueError):
        f()
    assert slept == [0.5, 1.0, 2.0]
    assert hooks == [1, 2, 3]


def test_metadata_preserved():
    @retry(attempts=1, exceptions=(ValueError,))
    def myfunc():
        """docs here"""

    assert myfunc.__name__ == "myfunc"
    assert myfunc.__doc__ == "docs here"
