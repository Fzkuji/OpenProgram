"""Diagnostics shared by every entry point.

``checks`` holds the installation health checks behind ``openprogram
doctor`` and ``/api/doctor``; ``redaction`` scrubs credential-shaped values
from text and JSON before they reach a log, an error message or a support
bundle.
"""
