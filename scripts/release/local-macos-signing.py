#!/usr/bin/env python3
"""Run the checkout's local signer without importing an older installed package."""
from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).resolve().parents[2] / "openprogram/self_update/delivery/local_signing.py"),
              run_name="__main__")
