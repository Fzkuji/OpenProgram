"""Diagnostics shared by every entry point.

``checks`` holds the installation health checks behind ``openprogram
doctor`` and ``/api/doctor``. The scrubber that keeps credential-shaped
values out of a log, an error message or a support bundle is
``openprogram.models.auth.redaction``, with the credentials,
because the layers below operations use it too.
"""
