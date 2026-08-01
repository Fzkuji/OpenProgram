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
