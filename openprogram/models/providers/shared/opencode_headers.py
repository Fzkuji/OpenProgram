"""OpenCode routing identity for OpenProgram's own coding-agent requests."""
from urllib.parse import urlsplit
from uuid import uuid4


def opencode_headers(provider: str, base_url: str | None, session_id: str | None = None) -> dict[str, str]:
    if provider not in {"opencode", "opencode-go"} and urlsplit(base_url or "").hostname != "opencode.ai":
        return {}
    from openprogram.state.execution_facts import current_session_id
    # Auxiliary requests inherit the owning chat. Standalone requests have no
    # conversation history and receive their own identity, never a global one.
    session_id = session_id or current_session_id() or str(uuid4())
    return {"x-opencode-session": session_id, "User-Agent": "OpenProgram"}
