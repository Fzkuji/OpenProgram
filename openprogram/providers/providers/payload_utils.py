"""
Payload utilities for provider on_payload support.

Mirrors the on_payload behavior in TypeScript providers:
- Calls the on_payload callback if provided
- If callback returns non-None value, replaces the original payload
- Supports both sync and async callbacks
"""
from __future__ import annotations

import inspect
from typing import Any, Callable

from ..types import Model


async def apply_on_payload(
    payload: Any,
    model: Model,
    on_payload_callback: Callable[[Any, Model], Any | None] | None,
) -> Any:
    """
    Apply on_payload callback and return potentially modified payload.
    
    Mirrors TypeScript behavior:
    ```typescript
    const nextParams = await options?.onPayload?.(params, model);
    if (nextParams !== undefined) {
        params = nextParams;
    }
    ```
    
    Args:
        payload: The original payload dict
        model: The model being used
        on_payload_callback: Optional callback function
        
    Returns:
        The modified payload if callback returns non-None, otherwise original payload
    """
    if not on_payload_callback:
        return payload
    
    # Call the callback
    result = on_payload_callback(payload, model)
    
    # Handle async callbacks
    if inspect.iscoroutine(result) or inspect.isawaitable(result):
        result = await result
    
    # Replace payload if callback returned non-None
    if result is not None:
        return result
    
    return payload
