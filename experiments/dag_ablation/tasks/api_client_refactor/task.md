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
