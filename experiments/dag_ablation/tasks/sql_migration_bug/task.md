# Task: fix `store.py`

`store.py` is a tiny SQLite-backed key/value store with history.
Its tests fail — there are three bugs (a SQL bug, a transaction bug,
and an off-by-one in `recent`). Fix them. Do not edit the tests.

Run `python -m pytest -q` until green.
