"""Entry point so ``python -m openprogram`` works.

The channels worker spawner uses ``sys.executable -m openprogram ...``
to re-exec the current python/virtualenv without relying on whatever
``openprogram`` shim happens to be on PATH. That only works if this
module exists.
"""
from openprogram.cli import main

if __name__ == "__main__":
    main()
