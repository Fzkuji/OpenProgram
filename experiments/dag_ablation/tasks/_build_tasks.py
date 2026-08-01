"""One-shot generator for the DAG-ablation task set.

Rerunnable: wipes and rewrites every task dir under tasks/. Kept as a
single script instead of 14 hand-maintained trees so the whole suite is
diffable in one place.

Each task dir gets:
  task.md      instructions handed to the agent
  setup/       initial files copied into the trial workdir
  grade.py     run inside the workdir; prints a float 0..1 on stdout
  reference/   a correct solution, overlaid onto setup/ to self-check grade.py
"""
from __future__ import annotations

import os
import shutil
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))

# grade.py that just shells out to pytest over the workdir.
PYTEST_GRADER = '''\
import subprocess, sys, os
r = subprocess.run([sys.executable, "-m", "pytest", "-q", "--no-header", "-p",
                    "no:cacheprovider", os.path.dirname(os.path.abspath(__file__))],
                   capture_output=True, text=True, timeout=300)
print(1.0 if r.returncode == 0 else 0.0)
'''


def d(s: str) -> str:
    return textwrap.dedent(s).lstrip("\n")


TASKS: dict[str, dict] = {}


def task(name, *, task_md, setup, reference, grade=PYTEST_GRADER):
    TASKS[name] = dict(task_md=d(task_md), setup={k: d(v) for k, v in setup.items()},
                       reference={k: d(v) for k, v in reference.items()}, grade=grade)


# ---------------------------------------------------------------- 1
task(
    "prime_utils",
    task_md="""
    # Task: implement `prime_utils.py`

    `test_prime_utils.py` exists and currently fails. Implement
    `prime_utils.py` so that the whole test file passes.

    Required API:
      - `is_prime(n) -> bool`
      - `primes_up_to(n) -> list[int]` (ascending, sieve-based)
      - `prime_factors(n) -> list[int]` (ascending, with multiplicity)

    Run `python -m pytest -q` until green. Do not edit the test file.
    """,
    setup={
        "test_prime_utils.py": """
        from prime_utils import is_prime, primes_up_to, prime_factors


        def test_is_prime():
            assert not is_prime(0) and not is_prime(1)
            assert is_prime(2) and is_prime(3) and is_prime(97)
            assert not is_prime(-7) and not is_prime(91)


        def test_primes_up_to():
            assert primes_up_to(1) == []
            assert primes_up_to(20) == [2, 3, 5, 7, 11, 13, 17, 19]
            assert len(primes_up_to(1000)) == 168


        def test_prime_factors():
            assert prime_factors(1) == []
            assert prime_factors(12) == [2, 2, 3]
            assert prime_factors(97) == [97]
            assert prime_factors(360) == [2, 2, 2, 3, 3, 5]
        """,
    },
    reference={
        "prime_utils.py": """
        def is_prime(n):
            if n < 2:
                return False
            if n < 4:
                return True
            if n % 2 == 0:
                return False
            i = 3
            while i * i <= n:
                if n % i == 0:
                    return False
                i += 2
            return True


        def primes_up_to(n):
            if n < 2:
                return []
            sieve = [True] * (n + 1)
            sieve[0] = sieve[1] = False
            i = 2
            while i * i <= n:
                if sieve[i]:
                    for j in range(i * i, n + 1, i):
                        sieve[j] = False
                i += 1
            return [i for i, ok in enumerate(sieve) if ok]


        def prime_factors(n):
            out = []
            d = 2
            while d * d <= n:
                while n % d == 0:
                    out.append(d)
                    n //= d
                d += 1
            if n > 1:
                out.append(n)
            return out
        """,
    },
)

# ---------------------------------------------------------------- 2
task(
    "csv_stats_bug",
    task_md="""
    # Task: fix the bugs in `csv_stats.py`

    `csv_stats.py` summarises a CSV of numbers. Its tests fail. Find and
    fix the bugs — the tests encode the intended behaviour, they are
    correct. Do not edit `test_csv_stats.py`.

    Run `python -m pytest -q` until green.
    """,
    setup={
        "csv_stats.py": """
        import csv


        def load_column(path, column):
            \"\"\"Read one numeric column out of a CSV, skipping blanks.\"\"\"
            out = []
            with open(path) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    raw = row.get(column, "")
                    if raw == "":
                        continue
                    out.append(int(raw))   # BUG: values may be floats
            return out


        def mean(xs):
            return sum(xs) / len(xs)       # BUG: crashes on empty input


        def median(xs):
            s = sorted(xs)
            return s[len(s) // 2]          # BUG: even-length case


        def summary(path, column):
            xs = load_column(path, column)
            return {"n": len(xs), "mean": mean(xs), "median": median(xs)}
        """,
        "test_csv_stats.py": """
        import math
        import pytest
        from csv_stats import load_column, mean, median, summary

        CSV = "n,value\\na,1.5\\nb,2.5\\nc,\\nd,4\\n"


        @pytest.fixture
        def data(tmp_path):
            p = tmp_path / "d.csv"
            p.write_text(CSV)
            return str(p)


        def test_load_column_floats(data):
            assert load_column(data, "value") == [1.5, 2.5, 4.0]


        def test_mean_empty():
            assert mean([]) == 0.0


        def test_median_even():
            assert median([1, 2, 3, 4]) == 2.5
            assert median([3, 1, 2]) == 2


        def test_summary(data):
            s = summary(data, "value")
            assert s["n"] == 3
            assert math.isclose(s["mean"], 8.0 / 3)
            assert s["median"] == 2.5
        """,
    },
    reference={
        "csv_stats.py": """
        import csv


        def load_column(path, column):
            out = []
            with open(path) as f:
                for row in csv.DictReader(f):
                    raw = (row.get(column) or "").strip()
                    if raw == "":
                        continue
                    out.append(float(raw))
            return out


        def mean(xs):
            if not xs:
                return 0.0
            return sum(xs) / len(xs)


        def median(xs):
            s = sorted(xs)
            n = len(s)
            if n == 0:
                return 0.0
            if n % 2:
                return s[n // 2]
            return (s[n // 2 - 1] + s[n // 2]) / 2


        def summary(path, column):
            xs = load_column(path, column)
            return {"n": len(xs), "mean": mean(xs), "median": median(xs)}
        """,
    },
)

# ---------------------------------------------------------------- 3
task(
    "split_module",
    task_md="""
    # Task: split `bigmod.py` into a package

    `bigmod.py` mixes three concerns. Refactor it into a package
    `shop/` with exactly these modules:

      - `shop/money.py`   — `Money` class
      - `shop/cart.py`    — `Cart` class
      - `shop/tax.py`     — `tax_for` function
      - `shop/__init__.py` — re-exports `Money`, `Cart`, `tax_for`

    Then delete `bigmod.py`. Behaviour must not change.
    `test_shop.py` checks both the new layout and the behaviour.
    Do not edit the test file. Run `python -m pytest -q` until green.
    """,
    setup={
        "bigmod.py": """
        from dataclasses import dataclass

        TAX_RATES = {"US": 0.07, "JP": 0.10, "DE": 0.19}


        @dataclass(frozen=True)
        class Money:
            cents: int

            def __add__(self, other):
                return Money(self.cents + other.cents)

            def __mul__(self, k):
                return Money(round(self.cents * k))

            def __str__(self):
                return f"{self.cents / 100:.2f}"


        def tax_for(country, amount):
            return amount * TAX_RATES.get(country, 0.0)


        class Cart:
            def __init__(self):
                self.items = []

            def add(self, name, price, qty=1):
                self.items.append((name, price, qty))

            def subtotal(self):
                total = Money(0)
                for _, price, qty in self.items:
                    total = total + price * qty
                return total

            def total(self, country):
                sub = self.subtotal()
                return sub + tax_for(country, sub)
        """,
        "test_shop.py": """
        import os

        from shop import Money, Cart, tax_for


        def test_layout():
            for m in ("money", "cart", "tax", "__init__"):
                assert os.path.exists(os.path.join("shop", m + ".py")), m
            assert not os.path.exists("bigmod.py")


        def test_modules_own_their_symbols():
            import shop.money, shop.cart, shop.tax
            assert shop.money.Money is Money
            assert shop.cart.Cart is Cart
            assert shop.tax.tax_for is tax_for


        def test_behaviour():
            c = Cart()
            c.add("book", Money(1000), 2)
            c.add("pen", Money(150))
            assert c.subtotal() == Money(2150)
            assert str(c.total("US")) == "23.01"
            assert c.total("XX") == Money(2150)
        """,
    },
    reference={
        "shop/__init__.py": """
        from .money import Money
        from .cart import Cart
        from .tax import tax_for

        __all__ = ["Money", "Cart", "tax_for"]
        """,
        "shop/money.py": """
        from dataclasses import dataclass


        @dataclass(frozen=True)
        class Money:
            cents: int

            def __add__(self, other):
                return Money(self.cents + other.cents)

            def __mul__(self, k):
                return Money(round(self.cents * k))

            def __str__(self):
                return f"{self.cents / 100:.2f}"
        """,
        "shop/tax.py": """
        TAX_RATES = {"US": 0.07, "JP": 0.10, "DE": 0.19}


        def tax_for(country, amount):
            return amount * TAX_RATES.get(country, 0.0)
        """,
        "shop/cart.py": """
        from .money import Money
        from .tax import tax_for


        class Cart:
            def __init__(self):
                self.items = []

            def add(self, name, price, qty=1):
                self.items.append((name, price, qty))

            def subtotal(self):
                total = Money(0)
                for _, price, qty in self.items:
                    total = total + price * qty
                return total

            def total(self, country):
                sub = self.subtotal()
                return sub + tax_for(country, sub)
        """,
        "__DELETE__": "bigmod.py\n",
    },
)

# ---------------------------------------------------------------- 4
task(
    "json_config_merge",
    task_md="""
    # Task: implement `confmerge.py`

    Implement `deep_merge(base, override)` in `confmerge.py`:

      - dicts merge recursively; `override` wins on scalars
      - lists are replaced wholesale, never concatenated
      - a value of `None` in `override` DELETES the key from the result
      - neither input is mutated

    Also implement `load_layers(*paths)` which reads JSON files in order
    and deep-merges them left to right.

    `test_confmerge.py` is the spec. Do not edit it. Run
    `python -m pytest -q` until green.
    """,
    setup={
        "test_confmerge.py": """
        import json
        from confmerge import deep_merge, load_layers


        def test_scalar_override():
            assert deep_merge({"a": 1, "b": 2}, {"b": 3}) == {"a": 1, "b": 3}


        def test_recursive():
            base = {"x": {"y": 1, "z": 2}}
            assert deep_merge(base, {"x": {"z": 9, "w": 8}}) == {
                "x": {"y": 1, "z": 9, "w": 8}}


        def test_list_replaced():
            assert deep_merge({"l": [1, 2, 3]}, {"l": [9]}) == {"l": [9]}


        def test_none_deletes():
            assert deep_merge({"a": 1, "b": 2}, {"b": None}) == {"a": 1}
            assert deep_merge({"x": {"y": 1, "z": 2}}, {"x": {"y": None}}) == {
                "x": {"z": 2}}


        def test_no_mutation():
            base = {"x": {"y": 1}}
            deep_merge(base, {"x": {"y": 2}})
            assert base == {"x": {"y": 1}}


        def test_load_layers(tmp_path):
            a = tmp_path / "a.json"
            b = tmp_path / "b.json"
            a.write_text(json.dumps({"k": 1, "d": {"p": 1}}))
            b.write_text(json.dumps({"d": {"q": 2}, "k": None}))
            assert load_layers(str(a), str(b)) == {"d": {"p": 1, "q": 2}}
        """,
    },
    reference={
        "confmerge.py": """
        import copy
        import json


        def deep_merge(base, override):
            out = copy.deepcopy(base)
            for k, v in override.items():
                if v is None:
                    out.pop(k, None)
                elif isinstance(v, dict) and isinstance(out.get(k), dict):
                    out[k] = deep_merge(out[k], v)
                else:
                    out[k] = copy.deepcopy(v)
            return out


        def load_layers(*paths):
            out = {}
            for p in paths:
                with open(p) as f:
                    out = deep_merge(out, json.load(f))
            return out
        """,
    },
)

# ---------------------------------------------------------------- 5
task(
    "retry_decorator",
    task_md="""
    # Task: implement `retry.py`

    Implement a `retry` decorator factory:

        @retry(attempts=3, exceptions=(ValueError,), backoff=0.0, on_retry=None)

    Semantics:
      - call the function; on a listed exception, retry until `attempts`
        total calls have been made, then re-raise the last exception
      - exceptions NOT listed propagate immediately, no retry
      - sleep `backoff * 2**(i-1)` seconds before retry i (use `time.sleep`)
      - `on_retry(exc, attempt_number)` is called before each retry
      - preserve `__name__` and `__doc__` of the wrapped function

    `test_retry.py` is the spec. Do not edit it. Run
    `python -m pytest -q` until green.
    """,
    setup={
        "test_retry.py": """
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
                \"\"\"docs here\"\"\"

            assert myfunc.__name__ == "myfunc"
            assert myfunc.__doc__ == "docs here"
        """,
    },
    reference={
        "retry.py": """
        import functools
        import time


        def retry(attempts=3, exceptions=(Exception,), backoff=0.0, on_retry=None):
            def deco(fn):
                @functools.wraps(fn)
                def wrapper(*a, **kw):
                    last = None
                    for i in range(1, attempts + 1):
                        try:
                            return fn(*a, **kw)
                        except exceptions as e:
                            last = e
                            if i == attempts:
                                break
                            if on_retry is not None:
                                on_retry(e, i)
                            if backoff:
                                time.sleep(backoff * 2 ** (i - 1))
                    raise last
                return wrapper
            return deco
        """,
    },
)

# ---------------------------------------------------------------- 6
task(
    "sql_migration_bug",
    task_md="""
    # Task: fix `store.py`

    `store.py` is a tiny SQLite-backed key/value store with history.
    Its tests fail — there are three bugs (a SQL bug, a transaction bug,
    and an off-by-one in `recent`). Fix them. Do not edit the tests.

    Run `python -m pytest -q` until green.
    """,
    setup={
        "store.py": """
        import sqlite3

        SCHEMA = \"\"\"
        CREATE TABLE IF NOT EXISTS kv (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL,
            value TEXT NOT NULL
        );
        \"\"\"


        class Store:
            def __init__(self, path=":memory:"):
                self.conn = sqlite3.connect(path)
                self.conn.executescript(SCHEMA)

            def put(self, key, value):
                # BUG: plain INSERT explodes on an existing key
                self.conn.execute("INSERT INTO kv (key, value) VALUES (?, ?)",
                                  (key, value))
                self.conn.execute("INSERT INTO history (key, value) VALUES (?, ?)",
                                  (key, value))
                # BUG: never committed

            def get(self, key, default=None):
                row = self.conn.execute(
                    "SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
                return row[0] if row else default

            def recent(self, key, n=3):
                \"\"\"Most recent n values for key, newest first.\"\"\"
                rows = self.conn.execute(
                    "SELECT value FROM history WHERE key = ? "
                    "ORDER BY id DESC LIMIT ?", (key, n - 1)).fetchall()
                # BUG: LIMIT n - 1 returns one too few
                return [r[0] for r in rows]
        """,
        "test_store.py": """
        from store import Store


        def test_put_get():
            s = Store()
            s.put("a", "1")
            assert s.get("a") == "1"
            assert s.get("missing") is None
            assert s.get("missing", "d") == "d"


        def test_overwrite():
            s = Store()
            s.put("a", "1")
            s.put("a", "2")
            assert s.get("a") == "2"


        def test_persists_across_connections(tmp_path):
            p = str(tmp_path / "db.sqlite")
            s = Store(p)
            s.put("k", "v")
            assert Store(p).get("k") == "v"


        def test_recent():
            s = Store()
            for v in "12345":
                s.put("a", v)
            assert s.recent("a") == ["5", "4", "3"]
            assert s.recent("a", 2) == ["5", "4"]
            assert s.recent("nope") == []
        """,
    },
    reference={
        "store.py": """
        import sqlite3

        SCHEMA = \"\"\"
        CREATE TABLE IF NOT EXISTS kv (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL,
            value TEXT NOT NULL
        );
        \"\"\"


        class Store:
            def __init__(self, path=":memory:"):
                self.conn = sqlite3.connect(path)
                self.conn.executescript(SCHEMA)

            def put(self, key, value):
                self.conn.execute(
                    "INSERT INTO kv (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value))
                self.conn.execute("INSERT INTO history (key, value) VALUES (?, ?)",
                                  (key, value))
                self.conn.commit()

            def get(self, key, default=None):
                row = self.conn.execute(
                    "SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
                return row[0] if row else default

            def recent(self, key, n=3):
                rows = self.conn.execute(
                    "SELECT value FROM history WHERE key = ? "
                    "ORDER BY id DESC LIMIT ?", (key, n)).fetchall()
                return [r[0] for r in rows]
        """,
    },
)

# ---------------------------------------------------------------- 7
task(
    "text_wrap_bug",
    task_md="""
    # Task: fix `wrapper.py`

    `wrapper.py` implements greedy word wrapping. The tests describe the
    intended behaviour and currently fail. Fix the implementation.
    Do not edit the tests or use `textwrap` from the stdlib.

    Run `python -m pytest -q` until green.
    """,
    setup={
        "wrapper.py": """
        def wrap(text, width):
            \"\"\"Greedy-wrap text to lines of at most `width` chars.\"\"\"
            words = text.split()
            lines, cur = [], ""
            for w in words:
                if len(cur) + len(w) < width:   # BUG: off-by-one, ignores space
                    cur = cur + " " + w
                else:
                    lines.append(cur)
                    cur = w
            return lines                        # BUG: drops the last line


        def indent(text, prefix):
            return "\\n".join(prefix + line for line in text.split("\\n"))
        """,
        "test_wrapper.py": """
        from wrapper import wrap, indent


        def test_empty():
            assert wrap("", 10) == []


        def test_single_word():
            assert wrap("hello", 10) == ["hello"]


        def test_exact_fit():
            assert wrap("ab cd", 5) == ["ab cd"]


        def test_basic():
            assert wrap("the quick brown fox jumps", 10) == [
                "the quick", "brown fox", "jumps"]


        def test_long_word_gets_own_line():
            assert wrap("a supercalifragilistic b", 5) == [
                "a", "supercalifragilistic", "b"]


        def test_indent_skips_nothing():
            assert indent("a\\nb", "> ") == "> a\\n> b"
        """,
    },
    reference={
        "wrapper.py": """
        def wrap(text, width):
            words = text.split()
            lines, cur = [], ""
            for w in words:
                if not cur:
                    cur = w
                elif len(cur) + 1 + len(w) <= width:
                    cur = cur + " " + w
                else:
                    lines.append(cur)
                    cur = w
            if cur:
                lines.append(cur)
            return lines


        def indent(text, prefix):
            return "\\n".join(prefix + line for line in text.split("\\n"))
        """,
    },
)

# ---------------------------------------------------------------- 8
task(
    "event_bus",
    task_md="""
    # Task: implement `bus.py`

    Implement an `EventBus` class:

      - `subscribe(topic, fn) -> unsubscribe_callable`
      - `publish(topic, payload)` calls every subscriber of `topic` in
        subscription order, returns the number called
      - a handler raising an exception must NOT stop the others; collect
        the exceptions and expose them on `bus.errors` (list, cleared at
        the start of each `publish`)
      - `"*"` is a wildcard topic: its subscribers receive every event as
        `fn(topic, payload)` — normal subscribers get just `fn(payload)`
      - calling the returned unsubscribe twice is a no-op

    `test_bus.py` is the spec. Do not edit it. Run
    `python -m pytest -q` until green.
    """,
    setup={
        "test_bus.py": """
        from bus import EventBus


        def test_pub_sub_order():
            b, seen = EventBus(), []
            b.subscribe("t", lambda p: seen.append(("a", p)))
            b.subscribe("t", lambda p: seen.append(("b", p)))
            assert b.publish("t", 1) == 2
            assert seen == [("a", 1), ("b", 1)]


        def test_unknown_topic():
            assert EventBus().publish("nope", 1) == 0


        def test_unsubscribe_idempotent():
            b, seen = EventBus(), []
            off = b.subscribe("t", lambda p: seen.append(p))
            off()
            off()
            assert b.publish("t", 1) == 0
            assert seen == []


        def test_errors_isolated():
            b, seen = EventBus(), []

            def boom(p):
                raise RuntimeError("x")

            b.subscribe("t", boom)
            b.subscribe("t", lambda p: seen.append(p))
            assert b.publish("t", 1) == 2
            assert seen == [1]
            assert len(b.errors) == 1
            assert isinstance(b.errors[0], RuntimeError)
            b.publish("t", 2)
            assert len(b.errors) == 1


        def test_wildcard():
            b, seen = EventBus(), []
            b.subscribe("*", lambda t, p: seen.append((t, p)))
            b.subscribe("x", lambda p: seen.append(("direct", p)))
            assert b.publish("x", 7) == 2
            assert ("x", 7) in seen and ("direct", 7) in seen
            b.publish("y", 8)
            assert ("y", 8) in seen
        """,
    },
    reference={
        "bus.py": """
        class EventBus:
            def __init__(self):
                self._subs = {}
                self.errors = []

            def subscribe(self, topic, fn):
                self._subs.setdefault(topic, []).append(fn)
                done = [False]

                def off():
                    if done[0]:
                        return
                    done[0] = True
                    try:
                        self._subs.get(topic, []).remove(fn)
                    except ValueError:
                        pass
                return off

            def publish(self, topic, payload):
                self.errors = []
                n = 0
                for fn in list(self._subs.get(topic, [])):
                    n += 1
                    try:
                        fn(payload)
                    except Exception as e:
                        self.errors.append(e)
                if topic != "*":
                    for fn in list(self._subs.get("*", [])):
                        n += 1
                        try:
                            fn(topic, payload)
                        except Exception as e:
                            self.errors.append(e)
                return n
        """,
    },
)

# ---------------------------------------------------------------- 9
task(
    "cli_argparse",
    task_md="""
    # Task: implement the `tally` CLI

    Write `tally.py`, a command-line tool. Behaviour:

        python tally.py count FILE           -> "<n> lines"
        python tally.py count FILE --words   -> "<n> words"
        python tally.py top FILE -n K        -> K lines "<word> <count>",
                                                most frequent first,
                                                ties broken alphabetically
        python tally.py                      -> usage on stderr, exit 2

    Words are whitespace-separated, lowercased, stripped of leading and
    trailing punctuation. Missing file: print "no such file: PATH" to
    stderr and exit 1.

    `test_tally.py` drives the script as a subprocess. Do not edit it.
    Run `python -m pytest -q` until green.
    """,
    setup={
        "test_tally.py": """
        import subprocess
        import sys

        import pytest

        TEXT = "The cat sat. The cat, the mat!\\napple apple banana\\n"


        def run(*args, cwd=None):
            return subprocess.run([sys.executable, "tally.py", *args],
                                  capture_output=True, text=True, cwd=cwd)


        @pytest.fixture
        def f(tmp_path):
            p = tmp_path / "in.txt"
            p.write_text(TEXT)
            return str(p)


        def test_count_lines(f):
            r = run("count", f)
            assert r.returncode == 0
            assert r.stdout.strip() == "2 lines"


        def test_count_words(f):
            r = run("count", f, "--words")
            assert r.stdout.strip() == "10 words"


        def test_top(f):
            r = run("top", f, "-n", "3")
            assert r.returncode == 0
            assert r.stdout.split() == ["the", "3", "apple", "2", "cat", "2"]


        def test_no_args():
            r = run()
            assert r.returncode == 2
            assert r.stderr.strip()


        def test_missing_file():
            r = run("count", "/nope/nope.txt")
            assert r.returncode == 1
            assert "no such file" in r.stderr
        """,
    },
    reference={
        "tally.py": """
        import argparse
        import string
        import sys
        from collections import Counter


        def words_of(text):
            out = []
            for w in text.split():
                w = w.strip(string.punctuation).lower()
                if w:
                    out.append(w)
            return out


        def read(path):
            try:
                with open(path) as fh:
                    return fh.read()
            except OSError:
                sys.stderr.write(f"no such file: {path}\\n")
                raise SystemExit(1)


        def main(argv=None):
            p = argparse.ArgumentParser(prog="tally")
            sub = p.add_subparsers(dest="cmd")
            c = sub.add_parser("count")
            c.add_argument("file")
            c.add_argument("--words", action="store_true")
            t = sub.add_parser("top")
            t.add_argument("file")
            t.add_argument("-n", type=int, default=10)
            args = p.parse_args(argv)
            if not args.cmd:
                p.print_usage(sys.stderr)
                return 2
            text = read(args.file)
            if args.cmd == "count":
                if args.words:
                    print(f"{len(words_of(text))} words")
                else:
                    print(f"{len(text.splitlines())} lines")
                return 0
            counts = Counter(words_of(text))
            ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            for w, n in ranked[:args.n]:
                print(f"{w} {n}")
            return 0


        if __name__ == "__main__":
            raise SystemExit(main())
        """,
    },
)

# --------------------------------------------------- 10-13: big-fixture tasks
# These generate a multi-thousand-line fixture at setup time and force
# repeated grep/read over it -> designed to trip tool aging + node spill.

BIG_FIXTURE_GEN = '''\
"""Generates server.log — a big fixture the task must be mined from.

Deterministic (fixed seed) so every trial and the reference solution see
identical bytes.
"""
import random

LEVELS = ["DEBUG", "INFO", "WARN", "ERROR"]
SERVICES = ["auth", "billing", "search", "mailer", "gateway", "scheduler"]
MSGS = [
    "request completed", "cache miss", "cache hit", "retrying upstream",
    "connection reset", "token refreshed", "queue drained", "slow query",
]


def build(path="server.log", n=6000, seed=1337):
    rnd = random.Random(seed)
    lines = []
    for i in range(n):
        ts = 1700000000 + i * 7
        lvl = rnd.choices(LEVELS, weights=[50, 35, 10, 5])[0]
        svc = rnd.choice(SERVICES)
        ms = rnd.randint(1, 2500)
        code = rnd.choice([200, 200, 200, 201, 304, 400, 404, 500, 503])
        lines.append(
            f"{ts} {lvl} [{svc}] req_id=r{i:06d} status={code} "
            f"dur_ms={ms} msg=\\"{rnd.choice(MSGS)}\\""
        )
    # A few needles buried deep, only findable by actually searching.
    lines[4211] = ("1700029477 ERROR [billing] req_id=rSENTINEL "
                   "status=500 dur_ms=9999 msg=\\"ledger corruption detected\\"")
    lines[5904] = ("1700041328 ERROR [billing] req_id=rSENTINEL2 "
                   "status=500 dur_ms=8888 msg=\\"ledger corruption detected\\"")
    with open(path, "w") as f:
        f.write("\\n".join(lines) + "\\n")
    return path


if __name__ == "__main__":
    build()
'''

# Precompute the expected answers from the same generator so grade.py
# can assert on exact numbers without re-deriving them by hand.
_gen_ns: dict = {}
exec(BIG_FIXTURE_GEN, _gen_ns)

task(
    "log_mine_errors",
    task_md="""
    # Task: mine `server.log`

    `server.log` is a large log file (thousands of lines) in this
    directory. Each line looks like:

        <ts> <LEVEL> [<service>] req_id=<id> status=<code> dur_ms=<n> msg="<text>"

    Write `report.json` in this directory containing exactly:

      - `"total_lines"`: number of lines in the file
      - `"error_count"`: number of lines with level `ERROR`
      - `"errors_by_service"`: object mapping service name -> ERROR count,
        for services with at least one ERROR
      - `"sentinel_req_ids"`: sorted list of the `req_id` values of every
        line whose msg is exactly `ledger corruption detected`

    You must derive these from the actual file — search it, do not guess.
    Writing a small script to compute them is fine and encouraged.
    `report.json` is the graded artifact.
    """,
    setup={"_make_fixture.py": BIG_FIXTURE_GEN},
    reference={"__SOLVE__": "log_mine_errors"},
    grade='''\
import json, os, re, sys
d = os.path.dirname(os.path.abspath(__file__))
try:
    rep = json.load(open(os.path.join(d, "report.json")))
except Exception:
    print(0.0); sys.exit()
lines = open(os.path.join(d, "server.log")).read().splitlines()
errs = [l for l in lines if " ERROR " in l]
by = {}
for l in errs:
    by[re.search(r"\\[(\\w+)\\]", l).group(1)] = by.get(
        re.search(r"\\[(\\w+)\\]", l).group(1), 0) + 1
sent = sorted(re.search(r"req_id=(\\S+)", l).group(1) for l in lines
              if 'msg="ledger corruption detected"' in l)
ok = (rep.get("total_lines") == len(lines)
      and rep.get("error_count") == len(errs)
      and rep.get("errors_by_service") == by
      and sorted(rep.get("sentinel_req_ids") or []) == sent)
print(1.0 if ok else 0.0)
''',
)

task(
    "log_slowest",
    task_md="""
    # Task: latency report from `server.log`

    `server.log` is a large log file in this directory; each line has a
    `dur_ms=<n>` field, a `[<service>]` tag and a `status=<code>`.

    Write `slow.json` containing exactly:

      - `"p50"`, `"p95"`: integer dur_ms percentiles over ALL lines, using
        the nearest-rank method on the ascending sorted list:
        index = ceil(p/100 * N) - 1 (0-based)
      - `"slowest_req_id"`: req_id of the single line with the largest
        dur_ms (on a tie, the one appearing first in the file)
      - `"服务_over_2000"` is NOT wanted; instead `"services_over_2000"`:
        object mapping service -> count of its lines with dur_ms > 2000,
        only services with a nonzero count
      - `"error_rate"`: fraction of lines with status >= 500, rounded to
        4 decimal places

    Derive everything from the real file. A helper script is fine.
    `slow.json` is the graded artifact.
    """,
    setup={"_make_fixture.py": BIG_FIXTURE_GEN},
    reference={"__SOLVE__": "log_slowest"},
    grade='''\
import json, math, os, re, sys
d = os.path.dirname(os.path.abspath(__file__))
try:
    rep = json.load(open(os.path.join(d, "slow.json")))
except Exception:
    print(0.0); sys.exit()
lines = open(os.path.join(d, "server.log")).read().splitlines()
durs, best, best_id, over, n5 = [], -1, None, {}, 0
for l in lines:
    dur = int(re.search(r"dur_ms=(\\d+)", l).group(1))
    svc = re.search(r"\\[(\\w+)\\]", l).group(1)
    st = int(re.search(r"status=(\\d+)", l).group(1))
    durs.append(dur)
    if dur > best:
        best, best_id = dur, re.search(r"req_id=(\\S+)", l).group(1)
    if dur > 2000:
        over[svc] = over.get(svc, 0) + 1
    if st >= 500:
        n5 += 1
s = sorted(durs); N = len(s)
pct = lambda p: s[math.ceil(p / 100 * N) - 1]
ok = (rep.get("p50") == pct(50) and rep.get("p95") == pct(95)
      and rep.get("slowest_req_id") == best_id
      and rep.get("services_over_2000") == over
      and abs(rep.get("error_rate", -1) - round(n5 / N, 4)) < 1e-9)
print(1.0 if ok else 0.0)
''',
)

task(
    "log_grep_refactor",
    task_md="""
    # Task: build `logtool.py` against `server.log`

    `server.log` is a large log file in this directory. Write
    `logtool.py` exposing:

      - `parse(line) -> dict` with keys `ts` (int), `level`, `service`,
        `req_id`, `status` (int), `dur_ms` (int), `msg`
      - `iter_log(path)` yielding parsed dicts, skipping unparseable lines
      - `count_by(path, field)` -> dict field-value -> count
      - `search(path, pattern)` -> list of parsed dicts whose `msg`
        matches the regex `pattern`

    Then write `summary.json` with `{"levels": count_by(...,"level"),
    "services": count_by(...,"service"), "corruption_hits":
    len(search(..., "ledger corruption"))}` computed over `server.log`.

    Both `logtool.py` and `summary.json` are graded. Verify against the
    real file, do not guess the numbers.
    """,
    setup={"_make_fixture.py": BIG_FIXTURE_GEN},
    reference={"__SOLVE__": "log_grep_refactor"},
    grade='''\
import json, os, re, sys
d = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, d)
score = 0.0
log = os.path.join(d, "server.log")
try:
    import logtool
    line = open(log).readline().rstrip("\\n")
    p = logtool.parse(line)
    assert p["level"] in ("DEBUG", "INFO", "WARN", "ERROR")
    assert isinstance(p["ts"], int) and isinstance(p["dur_ms"], int)
    assert isinstance(p["status"], int) and p["service"]
    assert len(list(logtool.iter_log(log))) == len(open(log).read().splitlines())
    lv = logtool.count_by(log, "level")
    real = {}
    for l in open(log):
        k = l.split()[1]
        real[k] = real.get(k, 0) + 1
    assert lv == real
    assert len(logtool.search(log, "ledger corruption")) == 2
    score += 0.5
except Exception:
    pass
try:
    s = json.load(open(os.path.join(d, "summary.json")))
    real_lv, real_sv = {}, {}
    for l in open(log):
        real_lv[l.split()[1]] = real_lv.get(l.split()[1], 0) + 1
        sv = re.search(r"\\[(\\w+)\\]", l).group(1)
        real_sv[sv] = real_sv.get(sv, 0) + 1
    if (s.get("levels") == real_lv and s.get("services") == real_sv
            and s.get("corruption_hits") == 2):
        score += 0.5
except Exception:
    pass
print(score)
''',
)

task(
    "log_dedupe_report",
    task_md="""
    # Task: cross-reference `server.log` and `known_issues.md`

    This directory has a large `server.log` and a `known_issues.md`
    listing issue entries, each with a `pattern:` line (a substring to
    match against a log line's `msg`).

    Write `triage.json` containing exactly:

      - `"matched"`: object mapping issue id -> number of log lines whose
        msg contains that issue's pattern (include issues with 0)
      - `"unmatched_msgs"`: sorted list of DISTINCT msg strings appearing
        in the log that no known issue pattern matches
      - `"top_unmatched"`: the single unmatched msg with the most
        occurrences (ties: alphabetically first)

    Read both files for real; the log is thousands of lines.
    `triage.json` is the graded artifact.
    """,
    setup={
        "_make_fixture.py": BIG_FIXTURE_GEN,
        "known_issues.md": """
        # Known issues

        ## ISSUE-101
        pattern: cache miss
        owner: search

        ## ISSUE-102
        pattern: connection reset
        owner: gateway

        ## ISSUE-103
        pattern: ledger corruption
        owner: billing

        ## ISSUE-104
        pattern: slow query
        owner: billing

        ## ISSUE-105
        pattern: disk full
        owner: ops
        """,
    },
    reference={"__SOLVE__": "log_dedupe_report"},
    grade='''\
import json, os, re, sys
d = os.path.dirname(os.path.abspath(__file__))
try:
    rep = json.load(open(os.path.join(d, "triage.json")))
except Exception:
    print(0.0); sys.exit()
issues, cur = {}, None
for l in open(os.path.join(d, "known_issues.md")):
    m = re.match(r"##\\s+(\\S+)", l)
    if m:
        cur = m.group(1)
    elif l.startswith("pattern:") and cur:
        issues[cur] = l.split(":", 1)[1].strip()
msgs = [re.search(r'msg="([^"]*)"', l).group(1)
        for l in open(os.path.join(d, "server.log")) if 'msg="' in l]
matched = {k: sum(1 for m in msgs if p in m) for k, p in issues.items()}
counts = {}
for m in msgs:
    if not any(p in m for p in issues.values()):
        counts[m] = counts.get(m, 0) + 1
top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if counts else None
ok = (rep.get("matched") == matched
      and sorted(rep.get("unmatched_msgs") or []) == sorted(counts)
      and rep.get("top_unmatched") == top)
print(1.0 if ok else 0.0)
''',
)

# ---------------------------------------------------------------- 14
task(
    "graph_paths",
    task_md="""
    # Task: implement `graphlib_mini.py`

    Implement, without importing networkx:

      - `topo_sort(graph) -> list` — Kahn's algorithm over a dict
        node -> list of successors. Deterministic: at every step emit the
        alphabetically smallest node whose in-degree has reached zero
        (i.e. keep the ready set globally sorted, not a plain FIFO).
        Raise `ValueError("cycle")` on a cycle.
      - `shortest_path(graph, a, b) -> list | None` — BFS, node names as
        the tie-break, `None` when unreachable, `[a]` when `a == b`.
      - `components(graph) -> list[list]` — connected components treating
        edges as undirected, each component sorted, list sorted by first
        element.

    `test_graphlib_mini.py` is the spec. Do not edit it.
    Run `python -m pytest -q` until green.
    """,
    setup={
        "test_graphlib_mini.py": """
        import pytest
        from graphlib_mini import topo_sort, shortest_path, components

        G = {"a": ["b", "c"], "b": ["d"], "c": ["d"], "d": [], "e": []}


        def test_topo():
            assert topo_sort(G) == ["a", "b", "c", "d", "e"]


        def test_topo_cycle():
            with pytest.raises(ValueError):
                topo_sort({"a": ["b"], "b": ["a"]})


        def test_path():
            assert shortest_path(G, "a", "d") == ["a", "b", "d"]
            assert shortest_path(G, "a", "a") == ["a"]
            assert shortest_path(G, "d", "a") is None
            assert shortest_path(G, "a", "e") is None


        def test_components():
            assert components(G) == [["a", "b", "c", "d"], ["e"]]
        """,
    },
    reference={
        "graphlib_mini.py": """
        from collections import deque


        def _nodes(graph):
            s = set(graph)
            for vs in graph.values():
                s.update(vs)
            return s


        def topo_sort(graph):
            nodes = _nodes(graph)
            indeg = {n: 0 for n in nodes}
            for u in graph:
                for v in graph[u]:
                    indeg[v] += 1
            ready = sorted(n for n in nodes if indeg[n] == 0)
            out = []
            while ready:
                n = ready.pop(0)
                out.append(n)
                for v in sorted(graph.get(n, [])):
                    indeg[v] -= 1
                    if indeg[v] == 0:
                        ready.append(v)
                ready.sort()
            if len(out) != len(nodes):
                raise ValueError("cycle")
            return out


        def shortest_path(graph, a, b):
            if a == b:
                return [a]
            seen, q = {a}, deque([[a]])
            while q:
                path = q.popleft()
                for v in sorted(graph.get(path[-1], [])):
                    if v in seen:
                        continue
                    if v == b:
                        return path + [v]
                    seen.add(v)
                    q.append(path + [v])
            return None


        def components(graph):
            adj = {n: set() for n in _nodes(graph)}
            for u in graph:
                for v in graph[u]:
                    adj[u].add(v)
                    adj[v].add(u)
            seen, out = set(), []
            for n in sorted(adj):
                if n in seen:
                    continue
                comp, stack = [], [n]
                seen.add(n)
                while stack:
                    x = stack.pop()
                    comp.append(x)
                    for y in adj[x]:
                        if y not in seen:
                            seen.add(y)
                            stack.append(y)
                out.append(sorted(comp))
            return sorted(out, key=lambda c: c[0])
        """,
    },
)

# ---------------------------------------------------------------- 15
task(
    "api_client_refactor",
    task_md="""
    # Task: refactor `client.py` to inject its transport

    `client.py` hardcodes `urllib` calls, so it cannot be tested.
    Refactor `ApiClient` to take a `transport` callable in its
    constructor: `transport(method, url, body) -> (status, text)`.
    Default it to the existing urllib-based behaviour (move that into a
    module-level `urllib_transport` function) so existing callers keep
    working with no argument.

    Also add retry-on-5xx: up to `retries` extra attempts (default 2).
    Public behaviour of `get`/`post` otherwise unchanged; `get` returns
    parsed JSON, raises `ApiError` on non-2xx after retries.

    `test_client.py` is the spec. Do not edit it. Run
    `python -m pytest -q` until green.
    """,
    setup={
        "client.py": """
        import json
        import urllib.request


        class ApiError(Exception):
            pass


        class ApiClient:
            def __init__(self, base):
                self.base = base.rstrip("/")

            def _call(self, method, path, body=None):
                url = self.base + path
                data = json.dumps(body).encode() if body is not None else None
                req = urllib.request.Request(url, data=data, method=method)
                with urllib.request.urlopen(req) as r:
                    return r.status, r.read().decode()

            def get(self, path):
                status, text = self._call("GET", path)
                if not 200 <= status < 300:
                    raise ApiError(status)
                return json.loads(text)

            def post(self, path, body):
                status, text = self._call("POST", path, body)
                if not 200 <= status < 300:
                    raise ApiError(status)
                return json.loads(text)
        """,
        "test_client.py": """
        import inspect
        import json

        import pytest
        import client as C
        from client import ApiClient, ApiError


        def test_urllib_transport_exists():
            assert callable(C.urllib_transport)
            assert "transport" in inspect.signature(ApiClient.__init__).parameters


        def test_default_transport_is_urllib():
            assert ApiClient("http://x").transport is C.urllib_transport


        def test_get_uses_transport():
            calls = []

            def t(method, url, body):
                calls.append((method, url, body))
                return 200, json.dumps({"ok": True})

            assert ApiClient("http://x/", transport=t).get("/a") == {"ok": True}
            assert calls == [("GET", "http://x/a", None)]


        def test_post_body():
            seen = {}

            def t(method, url, body):
                seen.update(method=method, body=body)
                return 201, json.dumps({"id": 1})

            assert ApiClient("http://x", transport=t).post("/a", {"k": 1}) == {"id": 1}
            assert seen == {"method": "POST", "body": {"k": 1}}


        def test_retry_on_5xx():
            n = []

            def t(method, url, body):
                n.append(1)
                if len(n) < 3:
                    return 503, ""
                return 200, json.dumps({"ok": 1})

            assert ApiClient("http://x", transport=t).get("/a") == {"ok": 1}
            assert len(n) == 3


        def test_retry_exhausted():
            n = []

            def t(method, url, body):
                n.append(1)
                return 500, ""

            with pytest.raises(ApiError):
                ApiClient("http://x", transport=t).get("/a")
            assert len(n) == 3


        def test_4xx_not_retried():
            n = []

            def t(method, url, body):
                n.append(1)
                return 404, ""

            with pytest.raises(ApiError):
                ApiClient("http://x", transport=t).get("/a")
            assert len(n) == 1
        """,
    },
    reference={
        "client.py": """
        import json
        import urllib.request


        class ApiError(Exception):
            pass


        def urllib_transport(method, url, body=None):
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(url, data=data, method=method)
            with urllib.request.urlopen(req) as r:
                return r.status, r.read().decode()


        class ApiClient:
            def __init__(self, base, transport=urllib_transport, retries=2):
                self.base = base.rstrip("/")
                self.transport = transport
                self.retries = retries

            def _call(self, method, path, body=None):
                url = self.base + path
                status, text = self.transport(method, url, body)
                attempts = 0
                while status >= 500 and attempts < self.retries:
                    attempts += 1
                    status, text = self.transport(method, url, body)
                if not 200 <= status < 300:
                    raise ApiError(status)
                return json.loads(text)

            def get(self, path):
                return self._call("GET", path)

            def post(self, path, body):
                return self._call("POST", path, body)
        """,
    },
)


# --------------------------------------------------------------- writers

def write_all():
    for name, spec in TASKS.items():
        root = os.path.join(HERE, name)
        # The four log tasks have hand-written reference solutions (they
        # produce artifacts, not modules) that live on disk, not in this
        # file. Preserve them across a regenerate.
        keep_ref = spec["reference"].get("__SOLVE__") is not None
        ref_dir = os.path.join(root, "reference")
        saved = None
        if keep_ref and os.path.isdir(ref_dir):
            saved = os.path.join(HERE, f".__ref_{name}")
            shutil.rmtree(saved, ignore_errors=True)
            shutil.move(ref_dir, saved)
        shutil.rmtree(root, ignore_errors=True)
        os.makedirs(os.path.join(root, "setup"), exist_ok=True)
        os.makedirs(ref_dir, exist_ok=True)
        if saved:
            shutil.rmtree(ref_dir, ignore_errors=True)
            shutil.move(saved, ref_dir)
            spec = dict(spec, reference={})
        with open(os.path.join(root, "task.md"), "w") as f:
            f.write(spec["task_md"])
        with open(os.path.join(root, "grade.py"), "w") as f:
            f.write(spec["grade"])
        for rel, body in spec["setup"].items():
            p = os.path.join(root, "setup", rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write(body)
        for rel, body in spec["reference"].items():
            p = os.path.join(root, "reference", rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write(body)
        print("wrote", name)


if __name__ == "__main__":
    write_all()
