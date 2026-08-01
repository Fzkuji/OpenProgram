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
