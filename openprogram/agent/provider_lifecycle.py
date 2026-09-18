"""Balanced diagnostic events for one provider request, independent of turn IDs."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import uuid4

from openprogram.providers.utils.errors import ExecInterrupt


@dataclass
class ProviderResponse:
    status: str = "incomplete"
    exhausted: bool = False


@contextmanager
def provider_response(emit):
    request_id = uuid4().hex
    response = ProviderResponse()
    emit("model.response_started", "agent", {"request_id": request_id})
    try:
        yield response
    except (asyncio.CancelledError, ExecInterrupt):
        response.status = "cancelled"
        raise
    except BaseException:
        if not response.exhausted:
            response.status = "failed"
        raise
    finally:
        emit("model.response_completed", "agent", {
            "request_id": request_id, "status": response.status,
            "is_error": response.status != "completed",
        })
