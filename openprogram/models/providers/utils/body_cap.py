"""A provider's cap on the bytes of one request.

A conversation carries its images inline as base64 in every request (a
computer-use session's screenshots, pasted pictures). Past a provider's
byte cap (Anthropic's 32 MB ``request_too_large``, a relay's 413) every
turn is refused with the same body, and the conversation can neither go on
nor compact.

A refusal that says the body is too large (:func:`refused_for_size`)
teaches the provider's cap: the size the refusal states, or else three
quarters of what was sent. The request is sent again with the oldest
images of its history left out, each replaced by a short note saying so,
until it is under three quarters of the cap, which leaves the
conversation room to grow a few turns before more go, so its prompt cache
is not broken every turn. The latest user message's images stay and no
text is touched. Later requests to the same provider are fitted the same
way before they are sent. When leaving every earlier image out is still
not enough, the refusal goes on as it came, for compaction to handle.

Caps are kept per provider and base URL for as long as the process runs.

After magpie's body cap (yetone/magpie, MIT).
"""
from __future__ import annotations

import json
import logging
import re
import threading

from openprogram.models.providers.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Model,
    TextContent,
    ToolResultMessage,
    UserMessage,
)

log = logging.getLogger(__name__)

IMAGE_LEFT_OUT = "[An earlier image was left out: the request was over the provider's size limit.]"

_SIZE_WORDS = re.compile(
    r"request[ _](body|entity|payload)( size)?( is| was)? "
    r"(too large|too big|over|larger than|exceeds?|exceeded)"
    r"|request body is [\d.]+ ?[kmg]i?b|payload too large|entity too large|request_too_large"
    r"|(body|request|payload) (size )?(exceeds?|exceeded|is over|is larger than) (the )?(max|limit|\d)"
    r"|maximum allowed number of bytes|limit for one request",
    re.I,
)
_STATUS_413 = re.compile(r"(?:error code|http|status)\s*[:=]?\s*413\b", re.I)
_SIZES = re.compile(r"(\d+(?:\.\d+)?)\s*(bytes|b|kb|kib|mb|mib|gb|gib)\b", re.I)
_UNITS = {"bytes": 1, "b": 1, "kb": 1 << 10, "kib": 1 << 10, "mb": 1 << 20,
          "mib": 1 << 20, "gb": 1 << 30, "gib": 1 << 30}

_caps: dict[tuple[str, str], int] = {}
_lock = threading.Lock()


def refused_for_size(message: AssistantMessage) -> bool:
    """The provider refused the request for its bytes, not its tokens or a rate."""
    if message.stop_reason != "error" or not message.error_message:
        return False
    from openprogram.models.providers.utils.overflow import OVERFLOW_PATTERNS

    text = message.error_message
    if any(pattern.search(text) for pattern in OVERFLOW_PATTERNS):
        return False  # the model's window, which compaction handles
    return bool(_STATUS_413.search(text) or _SIZE_WORDS.search(text))


def stated_cap(text: str, sent: int) -> int:
    """The smallest size ``text`` names below what was sent; 0 when it names none."""
    least = 0
    for number, unit in _SIZES.findall(text):
        size = int(float(number) * _UNITS[unit.lower()])
        if size < 1 << 10 or size >= sent:
            continue  # not a cap, or the body's own size
        least = size if not least else min(least, size)
    return least


def room(cap: int) -> int:
    """What a request is fitted to under ``cap``."""
    return cap // 4 * 3


def _key(model: Model) -> tuple[str, str]:
    return (str(model.provider), str(model.base_url or ""))


def known_cap(model: Model) -> int:
    with _lock:
        return _caps.get(_key(model), 0)


def learn(model: Model, refusal: str, sent: int) -> int:
    """Keep the cap ``refusal`` teaches (the smaller of it and the one known)."""
    cap = stated_cap(refusal, sent) or room(sent)
    with _lock:
        known = _caps.get(_key(model), 0)
        cap = min(cap, known) if known else cap
        _caps[_key(model)] = cap
    return cap


def forget() -> None:
    with _lock:
        _caps.clear()


def request_bytes(context: Context) -> int:
    """About what ``context`` weighs on the wire: its messages, prompt and tools as JSON."""
    document = context.model_dump(mode="json", exclude_none=True)
    return len(json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode())


def fit(context: Context, limit: int) -> tuple[Context, int] | None:
    """``context`` under ``limit`` bytes with its oldest images left out.

    Answers the fitted context and how many images went, or ``None`` when
    leaving out every image but the latest user message's is not enough.
    """
    size = request_bytes(context)
    if size <= limit:
        return context, 0
    latest_user = max(
        (index for index, message in enumerate(context.messages) if isinstance(message, UserMessage)),
        default=-1,
    )
    messages = list(context.messages)
    left_out = 0
    for index, message in enumerate(messages):
        if index == latest_user or size <= limit:
            continue
        if not isinstance(message, (UserMessage, ToolResultMessage)) or isinstance(message.content, str):
            continue
        content = list(message.content)
        changed = False
        for position, block in enumerate(content):
            if size <= limit:
                break
            if isinstance(block, ImageContent):
                size -= len(block.data) - len(IMAGE_LEFT_OUT)
                content[position] = TextContent(text=IMAGE_LEFT_OUT)
                left_out += 1
                changed = True
        if changed:
            messages[index] = message.model_copy(update={"content": content})
    if size > limit:
        return None
    return context.model_copy(update={"messages": messages}), left_out


def before_sending(model: Model, context: Context) -> Context:
    """``context`` fitted under the cap ``model``'s provider is known to have."""
    cap = known_cap(model)
    if not cap:
        return context
    fitted = fit(context, room(cap))
    return context if fitted is None else fitted[0]


def after_refusal(model: Model, context: Context, refusal: AssistantMessage) -> Context | None:
    """``context`` with older images left out when ``refusal`` was for its size, else ``None``."""
    if not refused_for_size(refusal):
        return None
    sent = request_bytes(context)
    cap = learn(model, refusal.error_message or "", sent)
    fitted = fit(context, room(cap))
    if fitted is None or not fitted[1]:
        return None
    log.warning(
        "%s refused a %d-byte request as too large; sending it again under %d bytes "
        "with the %d oldest images left out",
        model.provider, sent, room(cap), fitted[1],
    )
    return fitted[0]
