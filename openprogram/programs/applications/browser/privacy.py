"""Host-side redaction for browser observations, including upstream ARIA trees."""
from __future__ import annotations

import json

PASSWORD_VALUES_SCRIPT = """() => Array.from((() => {
    const values = new Set();
    const roots = [document];
    while (roots.length) {
        const root = roots.pop();
        for (const element of root.querySelectorAll('input[type=password]')) {
            if (element.value) values.add(element.value);
        }
        for (const element of root.querySelectorAll('*')) {
            if (element.shadowRoot) roots.push(element.shadowRoot);
        }
    }
    return values;
})())"""


def password_values(page) -> tuple[str, ...]:
    values = []
    for frame in getattr(page, "frames", None) or [page]:
        captured = frame.evaluate(PASSWORD_VALUES_SCRIPT)
        if not isinstance(captured, list) or any(not isinstance(value, str) for value in captured):
            raise RuntimeError("Cannot safely redact browser password fields")
        values.extend(captured)
    return tuple(values)


def _password_variants(secrets):
    return {variant for secret in secrets if secret for variant in (
        secret, json.dumps(secret, ensure_ascii=False)[1:-1],
        json.dumps(secret, ensure_ascii=True)[1:-1],
    )}


def redact_password_values(value, secrets):
    """Remove secrets before emitting observations; never log the captured values."""
    if isinstance(value, str):
        # Match every candidate against the original text. A shorter shared
        # prefix must not destroy a later full or longer-prefix match.
        ranges = []
        for secret in _password_variants(secrets):
            marker = secret[:32] if len(secret) > 32 else secret
            start = value.find(marker)
            while start >= 0:
                matched = len(secret)
                if not value.startswith(secret, start):
                    low, high = len(marker), min(len(secret), len(value) - start)
                    while low < high:
                        middle = (low + high + 1) // 2
                        if value.startswith(secret[:middle], start):
                            low = middle
                        else:
                            high = middle - 1
                    matched = low
                ranges.append((start, start + matched))
                start = value.find(marker, start + 1)
        merged = []
        for start, end in sorted(ranges):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        parts, cursor = [], 0
        for start, end in merged:
            parts.extend((value[cursor:start], "[redacted]"))
            cursor = end
        return "".join(parts) + value[cursor:]
    if isinstance(value, dict):
        return {key: redact_password_values(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_password_values(item, secrets) for item in value]
    return value
