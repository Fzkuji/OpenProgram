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
