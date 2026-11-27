"""This profile's worker as the machine sees it: its state files, its lock,
its port and the interpreter it runs as.

Every layer reads these facts — the runtime checks who holds the worker
lock, the gateway address names the worker's port, the native checks start
the worker's interpreter — so they live here, below the runtime, and
``openprogram.operations.worker`` starts and stops the worker on top of them.
"""
