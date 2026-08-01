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
