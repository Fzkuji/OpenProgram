"""The isolated interpreter that runs ``computer_use_agent``'s ``code``.

- ``isolation.py`` — mandatory OS isolation for the interpreter
- ``runner.py`` — the persistent interpreter transport, without host or GUI capabilities
- ``broker.py`` — host-owned admission of GUI primitives and their durable receipts
- ``_child.py`` — the protocol copied into the scratch directory; never imported by the host
"""
