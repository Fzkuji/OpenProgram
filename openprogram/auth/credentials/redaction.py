def is_redacted_value(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    lowered = value.strip().casefold()
    return (
        lowered in {"redacted", "<redacted>", "[redacted]", "***redacted***"}
        or value == "•" * 8
        or "…" in value
    )
