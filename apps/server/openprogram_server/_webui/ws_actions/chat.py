"""Chat WS actions: chat / retry_function /
set_conversation_channel.

The ``chat`` action is the sole turn entry point from the web UI. The
retry / channel-bind actions are ws-only.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid


def _db_agent_id(session_id: str) -> str:
    """Read agent_id from SessionStore, falling back to default."""
    from openprogram.agent.session_db import default_db
    from openprogram.webui.server import _default_agent_id
    return (default_db().get_session(session_id) or {}).get("agent_id") or _default_agent_id()


_INTERNAL_ATTACHMENT_NAMES = {".opdedup.json", ".opsourcepaths.json"}


def _safe_attach_name(name: str) -> str:
    """Filesystem-safe basename for a saved attachment."""
    import os
    base = os.path.basename((name or "file").strip()) or "file"
    out = "".join(c if (c.isalnum() or c in "._- ") else "_" for c in base).strip()
    out = out[:120] or "file"
    if out.casefold() in _INTERNAL_ATTACHMENT_NAMES:
        out = f"attachment-{out.lstrip('.')}"
    return out


def _write_private_json(path, data: dict) -> None:
    """Atomically replace a private attachment index."""
    import json
    import os
    import tempfile

    fd = -1
    temp_path = ""
    try:
        fd, temp_path = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
        )
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            json.dump(data, handle, ensure_ascii=False)
        os.replace(temp_path, path)
        temp_path = ""
        os.chmod(path, 0o600)
    except (OSError, TypeError):
        pass
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


# Hard per-file / per-turn caps. Browser uploads are also frontend-capped
# (image-attach.ts MAX_DOC_BYTES), so these mainly defend non-browser
# sources (future remote channels) and bound the git-workdir blob bloat
# (attachments are committed, so an oversized blob is permanent history).
MAX_ATTACH_MB = 32
MAX_ATTACH_BYTES = MAX_ATTACH_MB * 1024 * 1024
MAX_TURN_ATTACH_BYTES = 64 * 1024 * 1024
# Bytes of head text delivered once, on the turn a file is attached, as a
# first-look preview. The agent pages the rest with its bounded read/pdf
# tools — so prompt cost stays O(1) per file regardless of file size.
PREVIEW_CAP = 4096


def _decoded_kind(raw: bytes, name: str) -> str:
    """Classify saved bytes as 'pdf' | 'text' | 'binary' for preview/count."""
    import os
    ext = os.path.splitext(name)[1].lower()
    if ext == ".pdf" or raw[:5] == b"%PDF-":
        return "pdf"
    head = raw[:8192]
    if b"\x00" in head:
        return "binary"
    try:
        head.decode("utf-8")
        return "text"
    except UnicodeDecodeError:
        return "binary"


def _pdf_count_and_preview(raw: bytes):
    """``("<N> pages", head_preview)`` for a PDF, or ``(None, None)``.

    Page-1 text + a capped per-page first-line outline so the model can
    jump to the relevant page range instead of scanning. Any failure
    (corrupt / encrypted / pypdf missing / slow) degrades to a count-less,
    preview-less mention — the small-file fast path never regresses.
    """
    try:
        import io
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw))
        pages = len(reader.pages)
    except Exception:
        return (None, None)
    parts: list[str] = []
    try:
        first = (reader.pages[0].extract_text() or "").strip()
        if first:
            parts.append(first[: PREVIEW_CAP // 2])
    except Exception:
        pass
    outline: list[str] = []
    for i, pg in enumerate(reader.pages[:50]):
        try:
            lines = [ln for ln in (pg.extract_text() or "").splitlines() if ln.strip()]
            head_line = lines[0].strip()[:80] if lines else ""
        except Exception:
            head_line = ""
        outline.append(f"  p{i + 1}: {head_line}")
    if pages > 50:
        outline.append(f"  …({pages - 50} more pages)")
    if outline:
        parts.append("[page outline]\n" + "\n".join(outline))
    preview = ("\n\n".join(parts))[:PREVIEW_CAP] if parts else None
    return (f"{pages} pages", preview)


def _count_and_preview(raw: bytes, kind: str):
    """``(count_str, preview_text)`` for the head preview, per file kind.

    text -> ``('<N> lines', <=PREVIEW_CAP head)``; pdf -> page count +
    outline; binary -> ``(None, None)`` (no text preview — the agent uses
    ``bash`` on the path).
    """
    if kind == "text":
        total = raw.count(b"\n") + (1 if raw and not raw.endswith(b"\n") else 0)
        truncated = len(raw) > PREVIEW_CAP
        head = raw[:PREVIEW_CAP].decode("utf-8", errors="replace")
        if truncated:
            head = head + "\n…[truncated — read the path for the rest]"
        return (f"{total} lines", head)
    if kind == "pdf":
        return _pdf_count_and_preview(raw)
    return (None, None)


def _inject_mention(text: str, name: str, dest, count, oversize: bool,
                    size_bytes: int = 0, mime: str = "",
                    source_path: str = "") -> str:
    """Rewrite this file's path-less ``[attachment: name (meta)]`` mention
    to embed the saved absolute path + (page/line) count — or mark it
    oversize. The count goes INSIDE the captured parens group so the
    single-token invariant holds and the chip / title strip regexes keep
    matching.

    No mention to rewrite (images — the composer emits a preview strip,
    not a mention) → append a complete one built by
    ``attachments.format_marker``, so both branches leave the message
    carrying the exact same lexicon.
    """
    import re
    from openprogram import attachments as _att
    marker_name = _att.safe_marker_text(name)
    pat = re.compile(
        r"\[attachment:\s*" + re.escape(marker_name)
        + r"\s*\(([^)\]]*)\)\]"
    )
    if source_path:
        # Only the current payload's provenance can preserve an existing
        # marker. A same-name browser upload has no source_path and must still
        # receive its own saved path.
        for whole, found_name, found_path in _att.find_markers(text):
            encoded = _att.JSON_MENTION_RE.fullmatch(whole)
            if encoded is not None and encoded.group(6):
                continue
            if marker_name != found_name or source_path != found_path:
                continue
            if oversize or dest is None:
                return text
            marker = _att.format_marker(
                marker_name,
                source_path,
                size_bytes,
                mime=mime,
                count=count or "",
                preview_path=dest,
            )
            return text.replace(whole, marker, 1)
    if oversize:
        if pat.search(text):
            return pat.sub(
                lambda m: (f"[attachment: {marker_name} ({m.group(1)}, "
                           f"too large >{MAX_ATTACH_MB}MB, not stored)]"),
                text, count=1,
            )
        return (text + f"\n[attachment: {marker_name} "
                       f"(too large >{MAX_ATTACH_MB}MB, not stored)]").strip()
    if pat.search(text):
        marker = _att.format_marker(
            marker_name, dest, size_bytes, mime=mime, count=count or "",
        )
        return pat.sub(lambda _match: marker, text, count=1)
    marker = _att.format_marker(name, dest, size_bytes, mime=mime,
                                count=count or "")
    return (text + "\n" + marker).strip()


def _preview_block(abs_path: str, preview: str, count_str, kind: str) -> str:
    """A passive head-preview content part. The chip parser strips it from
    the bubble; the model reads it as a first look at constant cost."""
    shows = count_str or ""
    return (f'<attachment-preview path="{abs_path}" kind="{kind}" shows="{shows}">\n'
            f'{preview}\n</attachment-preview>')


def _attachment_name(d: dict, index: int) -> str:
    """Display/on-disk name for one incoming attachment.

    A pasted screenshot arrives with bytes and a media type but no
    filename. Naming it "file" would cost it its extension, and the
    extension is what tells the chip it's an image and the raw endpoint
    what content type to serve — so synthesise one from the media type.
    """
    import mimetypes
    name = (d.get("filename") or "").strip()
    if name:
        return name
    mime = (d.get("media_type") or "").partition(";")[0].strip()
    ext = mimetypes.guess_extension(mime) or ""
    if mime == "image/jpeg":
        ext = ".jpg"      # guess_extension says .jpe, which nothing shows
    stem = "image" if mime.startswith("image/") else "file"
    return f"{stem}-{index}{ext}"


def _persist_attachments(session_id: str, incoming: list, text: str) -> str:
    """Save base64 attachments to the session workdir so the agent's own
    file tools (``pdf`` / ``read`` / ``bash``) can actually reach them,
    and rewrite (or append) the ``[attachment: name (type, KB)]`` mention
    in ``text`` to embed the saved ABSOLUTE PATH.

    Images land on disk too, and keep going to the model as an
    ImageContent block on top of that. Their bytes used to live only in
    the outgoing request: the model saw the picture, the human saw an
    empty bubble, and a reload lost it entirely. One path in the message
    is what lets the chat render it back.

    This is the backend half of the uniform "every file is a path"
    model. A browser upload has no source path, so its bytes are saved
    under ``<session workdir>/attachments/`` and that generated path is
    written into the marker. An Electron upload already carries the
    original path; the stored marker keeps it for the agent and adds the
    immutable session-copy path used by local previews. The raw-file endpoint
    therefore stays within its existing readable roots. The agent reads the
    original marker path on demand; the file body is never inlined into the
    prompt.

    (Files that already live on disk — ``@``-mentions / typed paths —
    skip this entirely: the frontend emits the absolute path directly,
    no copy. See ``at-mention.ts`` / ``/api/file-resolve``.)

    Best-effort: a save failure leaves that file's mention untouched.
    """
    import base64
    import hashlib
    import json
    from openprogram.agent.internals._workdir import session_workdir_for

    wd = session_workdir_for(session_id)
    if wd is None:
        # First-turn race: the git workdir isn't resolvable yet (it's
        # finalised in the execution thread, after this synchronous
        # handler). Fall back to the deterministic ad-hoc session workdir
        # under the state dir — the SAME path apply_default_workdir will
        # set as the agent's cwd, so the saved file is still reachable.
        try:
            from pathlib import Path
            from openprogram.paths import get_state_dir
            wd = Path(get_state_dir()) / "sessions" / session_id / "workdir"
        except Exception:
            return text
    adir = wd / "attachments"
    # Within-session content-dedup index {sha256: stored relname}. Lets a
    # re-dropped identical file (or a turn retry) reuse the existing copy
    # instead of writing spec-1.pdf, spec-2.pdf … Best-effort: a missing /
    # corrupt index only risks a duplicate write, never a wrong mapping
    # (reuse is verified by re-hashing the candidate first).
    index_path = adir / ".opdedup.json"
    dedup: dict = {}
    try:
        if index_path.exists():
            loaded = json.loads(index_path.read_text())
            if isinstance(loaded, dict):
                dedup = loaded
    except Exception:
        dedup = {}

    new_text = text
    previews: list[str] = []
    turn_bytes = 0
    index_dirty = False

    for idx, d in enumerate(incoming, start=1):
        data = d.get("data")
        name = _attachment_name(d, idx)
        if not data:
            continue
        try:
            raw = base64.b64decode(data, validate=False)
        except Exception:
            continue
        if not raw:
            continue
        # Oversize (per-file + per-turn aggregate): tell the model it was
        # dropped rather than hand it a path to a file that isn't there.
        if len(raw) > MAX_ATTACH_BYTES or (turn_bytes + len(raw)) > MAX_TURN_ATTACH_BYTES:
            new_text = _inject_mention(
                new_text, name, None, None, oversize=True,
                source_path=d.get("source_path") or "",
            )
            continue

        sha = hashlib.sha256(raw).hexdigest()
        dest = None
        # Reuse an identical file already saved this session.
        prior = dedup.get(sha)
        if prior:
            cand = adir / prior
            try:
                if cand.is_file() and hashlib.sha256(cand.read_bytes()).hexdigest() == sha:
                    dest = cand
            except OSError:
                dest = None
        if dest is None:
            safe = _safe_attach_name(name)
            try:
                adir.mkdir(parents=True, exist_ok=True)
                dest = adir / safe
                stem, dot, ext = safe.rpartition(".")
                i = 1
                while dest.exists():
                    # Same name AND same bytes already on disk -> reuse it
                    # (index was missing/stale); else bump to name-N.
                    try:
                        if hashlib.sha256(dest.read_bytes()).hexdigest() == sha:
                            break
                    except OSError:
                        pass
                    dest = adir / ((f"{stem}-{i}.{ext}") if dot else f"{safe}-{i}")
                    i += 1
                if not dest.exists():
                    dest.write_bytes(raw)
                    turn_bytes += len(raw)
            except OSError:
                continue
            dedup[sha] = dest.name
            index_dirty = True

        kind = _decoded_kind(raw, name)
        count_str, preview = _count_and_preview(raw, kind)
        raw_source_path = d.get("source_path")
        source_path = raw_source_path if isinstance(raw_source_path, str) else ""
        new_text = _inject_mention(new_text, name, dest, count_str,
                                   oversize=False, size_bytes=len(raw),
                                   mime=d.get("media_type") or "",
                                   source_path=source_path)
        if preview:
            previews.append(_preview_block(str(dest), preview, count_str, kind))

    if index_dirty:
        _write_private_json(index_path, dedup)
    # Append the one-time head previews after the prose. They are bounded
    # (<=PREVIEW_CAP each, this turn only) and stripped from the bubble by
    # the chip parser, so the user sees a chip while the model gets a look.
    if previews:
        new_text = (new_text + "\n\n" + "\n".join(previews)).strip()
    return new_text


def _attachments_for_dispatch(incoming: list) -> list | None:
    """Keep image blocks and remove the web-only source_path metadata field.

    The path remains in the user-message marker and is intentionally visible
    to the model; only the duplicate attachment-object metadata is removed.
    """
    images = [
        {key: value for key, value in item.items() if key != "source_path"}
        for item in incoming if item.get("type") != "document"
    ]
    return images or None


def _title_from_text(text: str) -> str:
    """Conversation title from the first user message, with attachment
    markers + legacy inline blocks stripped so a truncated
    a long path-bearing attachment marker never leaks into the sidebar.

    Mirrors the web parser (``user-attachments.tsx``) on the backend so
    the stored title is already clean — the frontend strips markers for
    display too, but only when the closing bracket survives; truncating
    at 50 chars can sever it, so we clean first, then truncate.
    """
    import re
    from openprogram import attachments as _attachments
    t = re.sub(r"<attachment-preview[^>]*>.*?</attachment-preview>", "", text, flags=re.S)
    t = _attachments.strip_markers(t)
    t = re.sub(r"\[attachment:[^\]]*\]", "", t)
    t = re.sub(r"\[attached(?: file)?:[^\]]*\]", "", t)
    t = re.sub(r"<file [^>]*>.*?</file>", "", t, flags=re.S)
    t = t.strip()
    return t[:50] + ("..." if len(t) > 50 else "")


async def handle_chat(ws, cmd: dict):
    from openprogram.webui import server as _s
    text = cmd.get("text", "").strip()
    session_id = cmd.get("session_id")
    agent_id = cmd.get("agent_id") or None
    thinking_effort = cmd.get("thinking_effort") or None
    exec_thinking_effort = cmd.get("exec_thinking_effort") or None
    tools_flag = cmd.get("tools")
    tools_profile = cmd.get("tools_profile") or None
    web_search_flag = bool(cmd.get("web_search"))
    from openprogram.agent.session_config import _normalize_permission
    _raw_perm = cmd.get("permission_mode")
    if _raw_perm is None or (
        isinstance(_raw_perm, str) and _raw_perm.strip().lower() in ("", "inherit")
    ):
        permission_mode = None
    else:
        permission_mode = _normalize_permission(_raw_perm) or "ask"
    sandbox_flag = cmd.get("sandbox_enabled") if "sandbox_enabled" in cmd else None
    surface_ref = cmd.get("surface") if isinstance(cmd.get("surface"), dict) else None
    # Per-turn speed / priority tier from the composer's speed pill
    # ("priority" = Fast, "flex" = cheaper-slower). Rides the message
    # payload each turn (client remembers via localStorage like the
    # thinking pill) — no server-side persistence / DB column needed.
    service_tier = cmd.get("service_tier") or None
    response_format = None
    if cmd.get("response_format") is not None:
        from openprogram.providers.structured_output import (
            StructuredOutputError,
            normalize_response_format,
        )
        try:
            response_format = normalize_response_format(cmd["response_format"])
        except StructuredOutputError as exc:
            await ws.send_text(json.dumps({
                "type": "chat_response",
                "data": {
                    "type": "error",
                    "code": exc.code,
                    "content": "Structured output request is invalid",
                    "issues": exc.issues,
                },
            }))
            return
    # INTENT, not snapshot. We do NOT expand the toolset / DEFAULT_TOOLS into
    # a tool-name list here — that materialization is exactly what froze old
    # sessions to a stale tool set (they never saw newly-added tools). The
    # profile name and the web_search flag are persisted as INTENT and
    # expanded live each turn by the dispatcher. See
    # docs/design/runtime/tool-toggle-management.md §5.1.
    #
    # ``tools_profile`` is a session-only Access preset. It is stored as a
    # preset reference, never activated globally and never expanded here.
    #
    # web_search states, expressed as intent:
    #   * tools=False, web_search=False → tools off → tools_override=[]
    #   * tools=False, web_search=True  → only web_search → ["web_search"]
    #     (a one-element explicit list, not a full snapshot)
    #   * tools=True/None, web_search=* → web_search rides as an intent flag
    #     on top of the live-expanded set (handled in session_config + the
    #     dispatcher's dict-override branch).
    if web_search_flag and tools_flag is False:
        # "tools off but web search on" → the only tool is web_search.
        tools_flag = ["web_search"]
    elif tools_profile and tools_flag is not False:
        tools_flag = {"preset": tools_profile}
    # Otherwise leave tools_flag as True / None / False / explicit-list
    # untouched; web_search_flag and tools_profile are persisted as intent.
    raw_attachments = cmd.get("attachments") or None
    attachments = None
    if isinstance(raw_attachments, list) and raw_attachments:
        attachments = [a for a in raw_attachments if isinstance(a, dict) and a.get("data")]
        if not attachments:
            attachments = None
    if not text and not attachments:
        return
    if not text and attachments:
        text = "(see attachment)"

    # /skill <name> [rest of prompt] — expand the message in place by
    # loading the named SKILL.md and prepending its body, so the next
    # LLM turn has the skill's instructions available without us having
    # to touch tool dispatch or session config plumbing.
    if text.lower().startswith("/skill "):
        rest_after_cmd = text[len("/skill "):].strip()
        if rest_after_cmd:
            head, _, tail = rest_after_cmd.partition(" ")
            skill_name = head.strip()
            user_request = tail.strip()
            try:
                from openprogram.skills.tool import invoke as _skill_invoke
                from openprogram.skills.loader import (
                    AmbiguousSkillError, get_skill, resolve as _skill_resolve,
                )
                # Resolve the skill first so we have a stable object to
                # gate on. The actual invoke (which writes a trace
                # entry) only happens after the gate passes.
                resolved = get_skill(skill_name)
                if resolved is None:
                    try:
                        resolved = _skill_resolve(skill_name)
                    except AmbiguousSkillError as e:
                        raise e

                # Agent-profile gating — shared helper across all
                # extension types (tools / skills / mcp). Patterns
                # support fnmatch wildcards.
                gate_error: str | None = None
                if resolved is not None:
                    try:
                        from openprogram.agent.management import manager as _A
                        from openprogram.agent.management.gating import gate as _gate
                        ag = _A.get(agent_id) if hasattr(_A, "get") else None
                        prof = ag.to_dict().get("skills", {}) if ag else {}
                        gate_error = _gate(
                            name=resolved.name,
                            category=resolved.category or "",
                            disabled=prof.get("disabled") or [],
                            allowed=prof.get("allowed") or [],
                            categories=prof.get("categories") or [],
                        )
                    except Exception as e:
                        gate_error = (
                            f"Could not evaluate skill gating for "
                            f"{(resolved.name if resolved else 'unknown')!r}: "
                            f"{type(e).__name__}: {e}"
                        )

                try:
                    if gate_error:
                        raise PermissionError(gate_error)
                    skill_md = _skill_invoke(skill_name)
                    activation = (
                        f"Activating skill: **{skill_name}**\n\n"
                        f"{skill_md}\n\n"
                        f"---\n\n"
                    )
                    text = activation + (
                        user_request if user_request
                        else f"Please apply the {skill_name} skill."
                    )
                    # allowed-tools enforcement — if the skill declares
                    # an explicit allowlist, restrict the LLM's tool
                    # set for this turn to that intersection. Empty
                    # list = unrestricted; matches claude-code semantics.
                    try:
                        sk = (
                            get_skill(skill_name) or _skill_resolve(skill_name)
                        )
                        if sk and sk.allowed_tools:
                            allow = set(sk.allowed_tools)
                            if isinstance(tools_flag, list):
                                tools_flag = [t for t in tools_flag if t in allow]
                            elif tools_flag is True or tools_flag is None:
                                tools_flag = list(allow)
                            # tools_flag is False → user explicitly turned
                            # tools off; respect that and don't re-enable.
                    except Exception:
                        pass
                except PermissionError as e:
                    # Profile-level gate rejection — show the reason
                    # back in chat so the user knows to adjust the
                    # agent profile or pick a different skill.
                    text = f"[skill blocked] {e}"
                except AmbiguousSkillError as e:
                    text = (
                        f"Skill name {skill_name!r} is ambiguous. "
                        f"Candidates: {', '.join(e.candidates)}.\n\n"
                        f"Please retry with the full hierarchical name."
                    )
                except KeyError:
                    text = (
                        f"Skill {skill_name!r} not installed. "
                        f"Browse /skills to install it first."
                    )
            except Exception as _skill_err:
                # Defensive: if anything in skill loading blows up,
                # leave the raw /skill text intact so the user can see
                # what went wrong rather than getting a silent miss.
                text = f"[/skill load failed: {_skill_err}]\n\n{text}"

    new_channel = (cmd.get("channel") or "").strip().lower() or None
    new_account_id = (cmd.get("account_id") or "").strip() or None
    new_peer = (cmd.get("peer") or "").strip() or None
    conv = _s._get_or_create_session(
        session_id,
        agent_id=agent_id,
        channel=new_channel,
        account_id=new_account_id,
        peer=new_peer,
    )
    session_id = conv["id"]

    # Project binding MUST happen before the first DB write below (the
    # title backfill / run-config / _append_msg all update_session with
    # create_if_missing=True, which would materialise the session repo
    # at the home root). create_session is the only path that can place
    # the repo inside the project (<project>/.openprogram/sessions/<id>/),
    # so when the composer sent the picker's project_id with the first
    # message, create the session with it right here. Existing sessions
    # are untouched — mid-chat project switches go through
    # set_session_project and never move the repo.
    project_id = (cmd.get("project_id") or "").strip() or None
    if project_id:
        try:
            from openprogram.agent.session_db import default_db as _proj_db
            _pdb = _proj_db()
            if _pdb.get_session(session_id) is None:
                _pdb.create_session(
                    session_id,
                    agent_id or _s._default_agent_id(),
                    project_id=project_id,
                )
        except Exception:
            pass

    # Local builtin commands execute backend-side. Status/clear return a
    # local reply and stop. A command may instead return one ``invoke``
    # descriptor: dispatch that registered @agentic_function through the
    # same forced-call boundary as Programs. /goal uses this path, so the
    # command and form execute the same Goal Workflow rather than separate
    # chat-turn and function loops.
    if text.startswith("/"):
        _local_res = None
        try:
            from openprogram.commands.dispatch import invoke as _cmd_invoke
            _r = _cmd_invoke(text, session_id=session_id)
            if _r.ok and _r.kind == "local" and callable(_r.local_handler):
                _local_res = _r
        except Exception:
            _local_res = None
        if _local_res is not None:
            try:
                _out = await asyncio.to_thread(
                    _local_res.local_handler,
                    {"session_id": session_id},
                    _local_res.raw_args,
                ) or {}
            except Exception as _cmd_err:  # noqa: BLE001 — reply, don't drop the WS
                _out = {"text": (f"/{_local_res.command_name} failed: "
                                 f"{type(_cmd_err).__name__}: {_cmd_err}")}
            _reply_text = str(_out.get("text") or "")
            if _reply_text:
                await ws.send_text(json.dumps({
                    "type": "chat_response",
                    "data": {"type": "local_command",
                             "session_id": session_id,
                             "command": _local_res.command_name,
                             "content": _reply_text},
                }))
            _invoke = _out.get("invoke")
            if isinstance(_invoke, dict):
                _name = str(_invoke.get("name") or "")
                _kwargs = _invoke.get("kwargs")
                if not _name or not isinstance(_kwargs, dict):
                    await ws.send_text(json.dumps({
                        "type": "chat_response",
                        "data": {
                            "type": "error",
                            "session_id": session_id,
                            "code": "invalid_local_invocation",
                            "content": "Local command returned an invalid invocation.",
                        },
                    }))
                    return
                from openprogram.webui.routes.chat import (
                    run_agentic_function_call,
                )
                _validated_surface = None
                if surface_ref is not None:
                    _surface_window = surface_ref.get("window_id")
                    _surface_tab = surface_ref.get("tab_id")
                    from openprogram.webui.ws_actions import webtab
                    _surface_version_valid = (
                        type(surface_ref.get("version")) is int
                        and surface_ref.get("version") == 1
                    )
                    _surface_tab_valid = (
                        _surface_tab is None
                        or (
                            isinstance(_surface_tab, str)
                            and bool(_surface_tab)
                        )
                    )
                    _surface_window_owned = (
                        isinstance(_surface_window, str)
                        and bool(_surface_window)
                        and any(
                            owner is ws and window_id == _surface_window
                            for owner, window_id, _revision
                            in webtab.registered_desktop_windows()
                        )
                    )
                    if not (
                        _surface_version_valid
                        and _surface_tab_valid
                        and _surface_window_owned
                    ):
                        await ws.send_text(json.dumps({
                            "type": "chat_response",
                            "data": {
                                "type": "error",
                                "session_id": session_id,
                                "code": "page_context_stale",
                                "content": (
                                    "The submitted Page context is stale or "
                                    "belongs to another desktop window."
                                ),
                            },
                        }))
                        return
                    _validated_surface = {
                        "version": 1,
                        "window_id": _surface_window,
                    }
                    if _surface_tab:
                        _validated_surface["tab_id"] = _surface_tab
                _run_options = {}
                if _validated_surface:
                    _run_options["origin_window_id"] = _validated_surface[
                        "window_id"
                    ]
                    _run_options["surface_ref"] = _validated_surface
                _run = run_agentic_function_call(
                    _name, _kwargs, session_id, **_run_options,
                )
                if "error" in _run:
                    await ws.send_text(json.dumps({
                        "type": "chat_response",
                        "data": {
                            "type": "error",
                            "session_id": session_id,
                            "code": _run.get("code") or "function_call_failed",
                            "content": str(_run.get("error") or "Function call failed."),
                        },
                    }))
                    return
                if not _run.get("execution_id"):
                    await ws.send_text(json.dumps({
                        "type": "chat_response",
                        "data": {
                            "type": "error",
                            "session_id": session_id,
                            "code": "execution_admission_failed",
                            "content": "Function execution was not admitted.",
                        },
                    }))
                    return
                await ws.send_text(json.dumps({
                    "type": "chat_ack",
                    "data": {
                        "session_id": _run.get("session_id", session_id),
                        "msg_id": _run.get("msg_id", ""),
                        "execution_id": _run["execution_id"],
                        "function_run": True,
                    },
                }))
                return
            _send_text = _out.get("send_text")
            if not _send_text:
                return
            text = str(_send_text)

    # A conversation-only checkout deliberately leaves files untouched.
    # The next mutating turn must adopt that workspace explicitly; otherwise
    # the model would edit one branch while reading another branch's history.
    from openprogram.agent.workspace_alignment import (
        adopt_current_workspace,
        get_workspace_alignment,
    )
    alignment = get_workspace_alignment(session_id)
    if alignment.get("status") == "mismatch":
        if cmd.get("workspace_decision") == "keep_current_files":
            alignment = adopt_current_workspace(session_id)
        if alignment.get("status") == "mismatch":
            await ws.send_text(json.dumps({
                "type": "chat_response",
                "data": {
                    "type": "error",
                    "session_id": session_id,
                    "msg_id": str(uuid.uuid4())[:8],
                    "code": "workspace_alignment_required",
                    "content": (
                        "Conversation and workspace are not aligned. "
                        "Choose Keep current files or Restore branch code first."
                    ),
                    "workspace_alignment": alignment,
                    "display": "chat",
                    "retry_query": text,
                    "timestamp": time.time(),
                },
            }, default=str))
            return

    # Run-active guard — the last unguarded HEAD-moving entry point
    # (fn-form dispatch, retry/edit, checkout, merge, rewind all check
    # _is_run_active already). Without it, two clients racing on one
    # session dispatch two turns that _append_msg + advance the same
    # HEAD concurrently and interleave the conversation chain. The
    # frontend routes to either its queue or the explicit ``steer`` action
    # when it knows a run is active; this covers the race window it cannot
    # see. A concurrent ordinary ``chat`` remains a queue fallback, never an
    # implicit steer, so the user's selected running-message mode stays exact.
    msg_id = str(uuid.uuid4())[:8]
    if not _s._try_reserve_run(session_id, msg_id):
        await ws.send_text(json.dumps({
            "type": "chat_response",
            "data": {
                "type": "error",
                "session_id": session_id,
                "msg_id": str(uuid.uuid4())[:8],
                "code": "run_active",
                "content": _s.RUN_ACTIVE_ERROR,
                "display": "chat",
                "retry_query": text,
                "timestamp": time.time(),
            },
        }, default=str))
        return

    try:
        from openprogram.agent.session_config import (
            permission_from_config,
            project_defaults,
            save_session_run_config,
        )
        run_cfg = save_session_run_config(
            session_id,
            agent_id=_db_agent_id(session_id),
            tools=tools_flag,
            # web_search stored as INTENT (not expanded into a list) so
            # the session always follows the live tool set.
            web_search=web_search_flag,
            thinking_effort=thinking_effort,
            permission_mode=permission_mode,
            # 草稿会话（尚无 session_id）在首条消息落地额外工作目录的唯一通道
            # （additional-working-directories.md §3.3）。None = 不动既有配置。
            additional_working_dirs=cmd.get("additional_working_dirs"),
            sandbox_enabled=sandbox_flag,
        )
    except BaseException:
        _s._release_run_reservation(session_id, msg_id)
        raise
    effective_permission = permission_from_config(
        run_cfg, default=project_defaults(session_id).get("permission_mode"))
    conv["tools_enabled"] = run_cfg.tools_enabled
    conv["tools_override"] = run_cfg.tools_override
    conv["web_search"] = run_cfg.web_search
    conv["toolset"] = run_cfg.toolset
    conv["thinking_effort"] = run_cfg.thinking_effort
    conv["permission_mode"] = run_cfg.permission_mode
    conv["sandbox_enabled"] = (
        run_cfg.sandbox_enabled if run_cfg.sandbox_enabled is not None
        else sandbox_flag
    )
    # Persist EVERY attachment to the session workdir so the agent's file
    # tools can read them and the chat can render them back, and embed the
    # saved ABSOLUTE PATH into the message text (every file is referenced
    # by path, never inlined). Images additionally continue to the
    # dispatcher as ImageContent blocks; documents are NOT passed as
    # content blocks (providers have no document-block support here).
    if attachments:
        try:
            text = _persist_attachments(session_id, attachments, text)
        except BaseException:
            _s._release_run_reservation(session_id, msg_id)
            raise
        attachments = _attachments_for_dispatch(attachments)

    # Stage 1 (immediate, zero-latency sidebar placeholder): truncate the
    # user's first line into a title the instant the message is sent, so the
    # sidebar never shows an empty row while stage 2 (the background LLM
    # title in finalize→_maybe_auto_title) is still running. We mark
    # ``_auto_titled`` — the SAME flag _maybe_auto_title uses — so its own
    # stage-1 backfill is a no-op and its race guard (which compares the
    # live title against the truncation it expects) keeps the LLM title.
    # We do NOT set _user_titled: this is an automatic title, not a manual
    # rename, so the LLM stage and turn-1/6/16/40 re-titling stay live.
    try:
        from openprogram.agent.session_db import default_db as _chat_ddb
        _chat_sess = _chat_ddb().get_session(session_id) or {}
    except Exception:
        _chat_sess = {}
    _chat_extra = _chat_sess.get("extra_meta") or {}
    if not _chat_extra.get("_auto_titled") and not _chat_extra.get("_user_titled"):
        _truncated = _title_from_text(text)
        try:
            _chat_ddb().update_session(session_id, title=_truncated,
                                       _auto_titled=True)
        except Exception:
            pass

    try:
        parsed = _s._parse_chat_input(text)
    except BaseException:
        _s._release_run_reservation(session_id, msg_id)
        raise

    try:
        from openprogram.agent.authority import local_owner_authority
        _local_authority = local_owner_authority()
    except BaseException:
        _s._release_run_reservation(session_id, msg_id)
        raise

    # The renderer's surface reference is only a hint. Resolve it against
    # this authenticated websocket and capture a server-owned binding before
    # durable admission; client-supplied capability fields are never stored.
    from openprogram.agent.surface_context import (
        capture as _capture_surface,
        release_bindings as _release_surface_bindings,
    )
    try:
        surface_context = _capture_surface(surface_ref, ws)
    except BaseException:
        _s._release_run_reservation(session_id, msg_id)
        raise

    user_msg = {
        "role": "user",
        "id": msg_id,
        "content": text,
        "timestamp": time.time(),
        "source": "web",
        "interaction": (
            "spawn" if parsed.get("action") == "spawn"
            else "merge" if parsed.get("action") == "merge" else None
        ),
        **_local_authority,
    }
    if parsed["action"] == "spawn":
        # SYNC path only: tag the /task user msg so the DAG layout
        # treats it as a branch fork (main trunk stops here; the
        # spawned turn + sub-agent reply live on a new lane). Same
        # idea as git: /task probe → `git checkout -b probe`.
        # ASYNC path: don't tag — the spawned turn lives on its own
        # session (or independent branch), not as a fork of THIS
        # message. Marking it function="agent" made the user msg
        # surface in the Branches panel as a stray named branch
        # (with the raw command as its label) because lane.py
        # treated it as a fork tip with no follow-up.
        if parsed.get("wait", True):
            user_msg["function"] = "agent"
    if attachments:
        manifest = [
            {"type": a.get("type"), "media_type": a.get("media_type"),
             "size_b64": len(a.get("data") or "")}
            for a in attachments
        ]
        user_msg["extra"] = json.dumps({"attachments": manifest}, default=str)
    # Admission is the sole source of execution identity. The message id is
    # retained only as transport/DAG provenance and never becomes ownership.
    from openprogram.agent.dispatcher.types import TurnRequest
    from openprogram.agent.production_driver import CanonicalAgentAdapter
    _adapter = CanonicalAgentAdapter(
        event_sink=(
            lambda env: _s._broadcast_envelope(env)
            if hasattr(_s, "_broadcast_envelope")
            else _s._broadcast(json.dumps(env, default=str))
        ),
    )
    from openprogram.agent.session_config import tools_override_from_config
    _response_format_payload = (
        response_format.model_dump(mode="json")
        if hasattr(response_format, "model_dump") else response_format
    )
    _request_payload = {
        "session_id": session_id,
        "user_text": parsed.get("raw") or text,
        "agent_id": _db_agent_id(session_id),
        "source": "web",
        "permission_mode": run_cfg.permission_mode or "ask",
        "thinking_effort": run_cfg.thinking_effort,
        "service_tier": service_tier,
        "response_format": _response_format_payload,
        "tools_override": tools_override_from_config(run_cfg),
        "attachments": attachments,
        "user_msg_id": msg_id,
        "user_already_persisted": True,
        "surface_context": surface_context,
        "structured_output": (
            {
                "prompt": parsed.get("prompt") or "",
                "label": parsed.get("label") or "",
                "context": parsed.get("context") or "inherit",
                "wait": parsed.get("wait", True),
            }
            if parsed.get("action") == "spawn" else {
                "sub_sessions": parsed.get("sub_sessions") or [],
                "message": parsed.get("message") or "",
            }
            if parsed.get("action") == "merge" else None
        ),
        "additional_working_dirs": getattr(run_cfg, "additional_working_dirs", []),
    }
    try:
        _request = TurnRequest(**_request_payload)
        _admission = _adapter.admit(
            _request,
            trusted_actor=_local_authority,
            user_message_id=msg_id,
            # The dispatcher writes this deterministic placeholder before
            # provider/tool callbacks.  Persist the same anchor at admission
            # so a resumed Agent checkpoint can fence it against immutable
            # execution input and finalize it exactly once.
            assistant_message_id=f"{msg_id}_reply",
            config_snapshot_ref=f"session:{session_id}",
        )
    except BaseException:
        _release_surface_bindings(surface_context)
        _s._release_run_reservation(session_id, msg_id)
        raise
    execution_id = _admission.execution_id

    # User/DAG content is committed only after the bounded canonical payload
    # has been admitted. If admission rejects an oversized or malformed
    # request, no user node is left behind without an execution record.
    try:
        _s._append_msg(conv, user_msg)
    except BaseException:
        _adapter.fail_admission(_admission, reason_code="user_persist_failed")
        _release_surface_bindings(surface_context)
        _s._release_run_reservation(session_id, msg_id)
        raise

    # chat.before_send on the bus — plugin subscribers observe the
    # message about to enter the runtime. emit_safe swallows failures
    # so a bad subscriber can't poison the chat path.
    try:
        from openprogram.events import emit_safe
        emit_safe("chat.before_send", "user", {
            "session_id": session_id,
            "msg_id": msg_id,
            "text": text,
            "agent_id": _db_agent_id(session_id),
            "attachments": bool(attachments),
        }, {"session": session_id})
    except BaseException:
        _adapter.fail_admission(_admission, reason_code="chat_event_failed")
        _release_surface_bindings(surface_context)
        _s._release_run_reservation(session_id, msg_id)
        raise

    # Echo the STORED text back. The composer only knows the path-less
    # mention it wrote (the encoded absolute path is appended above, after the
    # bytes hit disk), so an optimistic bubble built from the client's
    # own draft has no path to open. Handing it the final text is what
    # makes an attachment clickable in the turn you sent it, instead of
    # only after a reload.
    try:
        await ws.send_text(json.dumps({
            "type": "chat_ack",
            "data": {
                "session_id": session_id,
                "msg_id": msg_id,
                "text": text,
                "execution_id": execution_id,
                "status_version": _admission.status_version,
                "permission_mode": effective_permission,
            },
        }))
    except Exception:
        # The turn is already persisted. Losing its originating socket must
        # not leave that user message without a corresponding execution.
        pass

    # Mark the session running + push the sidebar list right now, before
    # the exec thread starts — so every connected tab shows the new
    # conversation row already flowing (convRunningFlow) the instant the
    # turn is dispatched, not a round-trip later when the exec thread's
    # own running_task broadcast lands. setdefault so the thread's later
    # _running_tasks[...] = {...} overwrite stays the single source of
    # the task entry (no double running_task with a different started_at).
    import time as _t
    try:
        with _s._running_tasks_lock:
            _running_task = _s._running_tasks.setdefault(session_id, {
                "msg_id": msg_id, "func_name": "_chat",
                "started_at": _t.time(), "last_event_at": _t.time(),
                "display_params": "", "loaded_func_ref": None,
                "stream_events": [],
                "execution_id": execution_id,
                "status_version": _admission.status_version,
            })
            # The reservation is created before the admission has an execution
            # id. Fill that exact id into the owned task rather than relying on
            # setdefault, which would leave a provisional value unable to
            # match the admitted execution.
            if _running_task.get("msg_id") == msg_id:
                _running_task["execution_id"] = execution_id
                _running_task["status_version"] = _admission.status_version
        _s._emit_running_task_event(session_id)
    except BaseException:
        with _s._running_tasks_lock:
            if (_s._running_tasks.get(session_id) or {}).get("msg_id") == msg_id:
                _s._running_tasks.pop(session_id, None)
        _adapter.fail_admission(_admission, reason_code="chat_handoff_failed")
        _release_surface_bindings(surface_context)
        _s._release_run_reservation(session_id, msg_id)
        raise
    try:
        from openprogram.webui.ws_actions.session import broadcast_sessions_list
        broadcast_sessions_list()
    except Exception:
        pass

    def _clear_running_task():
        try:
            _s._emit_running_task_event(
                session_id,
                cleared_msg_id=msg_id,
                cleared_execution_id=execution_id,
            )
        except Exception:
            pass

    def _make_run_thread(**kwargs):
        try:
            return threading.Thread(**kwargs)
        except BaseException:
            _release_surface_bindings(surface_context)
            _adapter.fail_admission(
                _admission, reason_code="agent_runner_error",
            )
            _s._release_run_reservation(session_id, msg_id)
            _clear_running_task()
            raise

    def _run_canonical(**_thread_options):
        def _publish_activation(active):
            with _s._running_tasks_lock:
                task = _s._running_tasks.get(session_id)
                if task and task.get("execution_id") == active.admission.execution_id:
                    task["status_version"] = active.status_version
            _s._emit_running_task_event(session_id)

        async def _activate():
            return await _adapter.activate(_admission, on_activated=_publish_activation)

        try:
            asyncio.run(_activate())
        finally:
            _release_surface_bindings(surface_context)
            try:
                if _s._finish_owned_run(session_id, msg_id):
                    _s._emit_running_task_event(
                        session_id,
                        cleared_msg_id=msg_id,
                        cleared_execution_id=execution_id,
                    )
            except Exception:
                pass

    run_thread = _make_run_thread(
        target=_run_canonical,
        args=(),
        kwargs={"response_format": response_format},
        daemon=True,
    )

    if not _s._activate_run_reservation(session_id, msg_id, run_thread):
        _release_surface_bindings(surface_context)
        _adapter.fail_admission(_admission, reason_code="agent_runner_error")
        _s._release_run_reservation(session_id, msg_id)
        _clear_running_task()
        raise RuntimeError("chat execution reservation was lost before startup")

    try:
        run_thread.start()
    except BaseException:
        _release_surface_bindings(surface_context)
        _adapter.fail_admission(
            _admission, reason_code="agent_runner_error",
        )
        if _s._finish_owned_run(session_id, msg_id):
            _clear_running_task()
        _s._release_run_reservation(session_id, msg_id)
        raise


def _retry_call_node(session_id: str, node_id: str, func_name: str):
    """Return the exact persisted top-level code node named by Retry."""
    if not all(
        isinstance(value, str) and value
        for value in (session_id, node_id, func_name)
    ):
        return None
    from openprogram.agent.session_db import default_db
    try:
        nodes = default_db().get_nodes(session_id)
    except Exception:
        return None
    code_ids = {n.id for n in nodes if n.is_code()}
    return next((
        node for node in nodes
        if node.id == node_id
        and node.is_code()
        and node.name == func_name
        and isinstance(node.input, dict)
        and node.caller not in code_ids
    ), None)


def _call_predecessor(node) -> str:
    """The anchor a re-run passes so it lands as a SIBLING of ``node``
    (same fork point). Returned as ``pred:<id>`` — the forced-tool path
    decodes that into the re-run's ``predecessor`` (with an empty
    caller), matching the edge a fresh chained run uses, so the two runs
    are true alternatives sharing one predecessor.

    The fork point is ``node``'s own conversation predecessor (mirrors
    chat-retry's ``predecessor = src.predecessor``), falling back to the
    node's caller, then "ROOT" — so a first/root-level run re-runs as a
    ROOT sibling and an LLM-issued call re-runs off the same reply it
    originally hung from.

    ``predecessor`` is a top-level ``Call`` field (dag/overview.md §3);
    it is popped out of metadata on every write path, so reading
    ``metadata["predecessor"]`` here always yielded None and silently
    collapsed every top-level re-run onto "ROOT"."""
    pred = getattr(node, "predecessor", None)
    fork = pred or getattr(node, "caller", None) or "ROOT"
    return f"pred:{fork}"


async def handle_retry_function(ws, cmd: dict):
    """Re-run the exact function code node selected by the user.

    Wired to the runtime-block Retry button. Mirrors chat-message retry
    (``_fork_user_turn_and_run``): the re-run is anchored at the original
    call's OWN predecessor, so it forks off the same point rather than
    stacking as a second sequential node. The forced-tool-call path
    advances HEAD to the new node, so the retried run becomes the active
    branch — only it renders in the transcript, and the old run is
    reachable via the runtime-block's version switcher (< N/M >) and the
    Branches panel. Old messages are never stripped.
    """
    from openprogram.webui import server as _s
    from openprogram.webui.routes.chat import run_agentic_function_call

    session_id = cmd.get("session_id")
    func_name = cmd.get("function")
    if not session_id or not func_name:
        return

    def _fail(message: str) -> None:
        _s._broadcast_chat_response(session_id, str(uuid.uuid4())[:8], {
            "type": "error",
            "content": f"Retry failed: {message}",
            "function": func_name,
            "display": "runtime",
        })

    node = _retry_call_node(session_id, cmd.get("node_id"), func_name)
    if node is None:
        _fail(f"no matching {func_name!r} call node found in this session.")
        return

    kwargs = {k: v for k, v in node.input.items()
              if k not in ("runtime", "callback")}
    # Anchor the re-run at the ORIGINAL call's predecessor so it lands as
    # a sibling branch (same fork model as chat retry), not a stacked run.
    anchor = _call_predecessor(node)

    from openprogram.webui.ws_actions import webtab
    registered_window_id = next((
        window_id for owner, window_id, _revision
        in webtab.registered_desktop_windows()
        if owner is ws
    ), None)
    clicked_surface = (
        cmd.get("surface_ref")
        if isinstance(cmd.get("surface_ref"), dict) else None
    )
    metadata = node.metadata if isinstance(node.metadata, dict) else {}
    if "surface_origin" in metadata:
        stored_origin = metadata["surface_origin"]
        stored_version = (
            stored_origin.get("version")
            if isinstance(stored_origin, dict) else None
        )
        stored_window = (
            stored_origin.get("window_id")
            if isinstance(stored_origin, dict) else None
        )
        stored_tab = (
            stored_origin.get("tab_id")
            if isinstance(stored_origin, dict)
            and "tab_id" in stored_origin else None
        )
        stored_version_valid = (
            type(stored_version) is int and stored_version == 1
        )
        stored_window_valid = (
            isinstance(stored_window, str) and bool(stored_window)
        )
        stored_tab_valid = (
            stored_tab is None
            or (isinstance(stored_tab, str) and bool(stored_tab))
        )
        if not (
            stored_version_valid and stored_window_valid and stored_tab_valid
        ):
            _fail("the selected call has an invalid stored Page origin.")
            return
        if registered_window_id != stored_window:
            _fail("the original desktop window is no longer connected here.")
            return
        origin_window_id = stored_window
        surface_ref = (
            {
                "version": 1,
                "window_id": stored_window,
                "tab_id": stored_tab,
            }
            if stored_tab else None
        )
    else:
        # Nodes created before surface_origin was persisted may use the
        # exact Page reported by the desktop at click time.
        origin_window_id = registered_window_id
        surface_ref = clicked_surface
        surface_window_id = (
            surface_ref.get("window_id")
            if isinstance(surface_ref, dict)
            and isinstance(surface_ref.get("window_id"), str)
            else None
        )
        if surface_ref and (
            not origin_window_id or surface_window_id != origin_window_id
        ):
            _fail("surface belongs to another desktop window.")
            return
    options = {"anchor_msg_id": anchor}
    if origin_window_id:
        options["origin_window_id"] = origin_window_id
    if surface_ref:
        options["surface_ref"] = surface_ref
    result = run_agentic_function_call(
        func_name, kwargs, session_id, **options,
    )
    if "error" in result:
        _fail(result["error"])
        return
    await ws.send_text(json.dumps({
        "type": "chat_ack",
        # ``function_run`` tells the frontend this ack is a function
        # dispatch whose top-level card was PRE-CREATED on disk at dispatch
        # time (run_agentic_function_call), so it can hydrate the transcript
        # immediately instead of waiting for the first tree_update (~1.85s
        # after the spawned child's import finishes). See wsHandleChatAck.
        "data": {"session_id": result.get("session_id", session_id),
                 "msg_id": result.get("msg_id", ""),
                 "execution_id": (
                     result.get("execution_id")
                     or result.get("msg_id")
                     or ""
                 ),
                 "function_run": True},
    }))


async def handle_set_conversation_channel(ws, cmd: dict):
    """Bind (or unbind) a conversation to a chat channel + account.

    Enforces 1:1 ownership: stealing a (channel, account) slot evicts
    any prior owner back to local. Persists the binding to SessionDB
    when the conv already has a row.
    """
    from openprogram.webui import server as _s
    session_id = cmd.get("session_id")
    ch = (cmd.get("channel") or "").strip().lower() or None
    acct_id = (cmd.get("account_id") or "").strip() or None
    peer = (cmd.get("peer") or "").strip() or None
    peer_display = (cmd.get("peer_display") or "").strip() or None
    ok = False
    err = None
    if not session_id:
        err = "session_id required"
    else:
        with _s._sessions_lock:
            conv = _s._sessions.get(session_id)
        if conv is None:
            err = f"unknown conversation {session_id!r}"
        elif ch is None and (acct_id or peer):
            err = "channel must be set when account_id / peer is set"
        else:
            evicted_ids: list[str] = []
            if ch:
                from openprogram.agent.session_db import default_db
                db_pre = default_db()
                db_owners = set(db_pre.sessions_with_binding(ch, acct_id))
                with _s._sessions_lock:
                    mem_owners = {
                        oid for oid, o in _s._sessions.items()
                        if o.get("channel") == ch and o.get("account_id") == acct_id
                    }
                candidates = (db_owners | mem_owners) - {session_id}
                for oid in candidates:
                    with _s._sessions_lock:
                        other = _s._sessions.get(oid)
                        if other is not None:
                            other["channel"] = None
                            other["account_id"] = None
                            other["peer"] = None
                            other["peer_display"] = None
                    try:
                        if db_pre.get_session(oid) is not None:
                            db_pre.update_session(
                                oid,
                                channel=None,
                                account_id=None,
                                peer=None,
                                peer_display=None,
                            )
                    except Exception as ex:
                        _s._log(f"[set_conversation_channel] evict {oid} db: {ex}")
                    evicted_ids.append(oid)

            _ch_val = ch
            _acct_val = acct_id if ch else None
            _peer_val = peer if ch else None
            _pd_val = (peer_display if ch else None) if peer_display is not None else None
            try:
                from openprogram.agent.session_db import default_db
                db = default_db()
                _update_kw = {
                    "channel": _ch_val,
                    "account_id": _acct_val,
                    "peer": _peer_val,
                }
                if peer_display is not None:
                    _update_kw["peer_display"] = _pd_val
                if db.get_session(session_id) is not None:
                    db.update_session(session_id, **_update_kw)
                ok = True
            except Exception as e:
                err = f"persist failed: {type(e).__name__}: {e}"

            for oid in evicted_ids:
                try:
                    await ws.send_text(json.dumps({
                        "type": "session_channel_updated",
                        "data": {
                            "session_id": oid,
                            "ok": True,
                            "channel": None,
                            "account_id": None,
                            "peer": None,
                            "evicted_by": session_id,
                        },
                    }, default=str))
                except Exception:
                    pass
    await ws.send_text(json.dumps({
        "type": "session_channel_updated",
        "data": {
            "session_id": session_id,
            "ok": ok,
            "channel": ch,
            "account_id": acct_id,
            "peer": peer,
            "error": err,
        },
    }, default=str))


async def handle_compact(ws, cmd: dict):
    """Manual /compact entry point — user-initiated compaction.

    Frontend sends ``{action: "compact", session_id, keep_recent_tokens?}``.
    We delegate to ``dispatcher.trigger_compaction`` which walks the full
    ``engine.compact`` pipeline (LLM summary, DAG re-parent, event
    broadcast).
    """
    from openprogram.webui import server as _s
    from openprogram.agent.dispatcher import trigger_compaction

    session_id = cmd.get("session_id")
    if not session_id:
        await ws.send_text(json.dumps({
            "type": "chat_response",
            "data": {"type": "error",
                     "content": "compact: missing session_id"},
        }))
        return

    from openprogram.agent.session_db import default_db
    try:
        session_exists = default_db().get_session(session_id) is not None
    except Exception:
        session_exists = False
    if not session_exists:
        await ws.send_text(json.dumps({
            "type": "chat_response",
            "data": {"type": "error",
                     "session_id": session_id,
                     "content": f"compact: unknown session {session_id}"},
        }))
        return

    agent_id = _db_agent_id(session_id)
    keep_recent_tokens = cmd.get("keep_recent_tokens")
    if keep_recent_tokens is not None:
        try:
            keep_recent_tokens = int(keep_recent_tokens)
        except (TypeError, ValueError):
            keep_recent_tokens = None

    def _emit(envelope: dict) -> None:
        # Re-shape to the standard chat-response wire frame and
        # broadcast so every connected client sees compaction progress.
        if envelope.get("type") == "chat_response":
            _s._broadcast_chat_response(
                session_id, "compact", envelope.get("data") or {},
            )

    # Compaction is a blocking sync call (it runs an LLM under the hood
    # via its own event loop). Run it off the WS loop so the websocket
    # stays responsive.
    import asyncio
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(
            None,
            lambda: trigger_compaction(
                session_id,
                agent_id=agent_id,
                on_event=_emit,
                keep_recent_tokens=keep_recent_tokens,
            ),
        )
        _s.refresh_context_stats(session_id, "compact")
    except Exception as e:  # noqa: BLE001
        _s._broadcast_chat_response(session_id, "compact", {
            "type": "error",
            "content": f"compact failed: {type(e).__name__}: {e}",
        })


async def handle_sandbox(ws, cmd: dict):
    """Toggle the system sandbox on or off."""
    from openprogram.sandbox import is_enabled, set_mode, unavailable_reason
    current = is_enabled()
    reason = unavailable_reason()
    if not current and reason:
        await ws.send_text(json.dumps({
            "type": "chat_response",
            "data": {"type": "status",
                     "content": f"System sandbox not available ({reason})"},
        }))
        return
    # Persisted through the config, because the agent turn runs in a bare
    # thread that this asyncio task's context never reaches.
    set_mode(not current)
    state = "ON" if not current else "OFF"
    msg = f"Sandbox: {state}"
    if not current:
        msg += (" — bash writes confined to the working directory, "
                "credential paths unreadable, no network")
    await ws.send_text(json.dumps({
        "type": "chat_response",
        "data": {"type": "status", "content": msg},
    }))


async def handle_rewind_list(ws, cmd: dict):
    """List available rewind points for the session."""
    session_id = (cmd.get("session_id") or "").strip()
    if not session_id:
        await ws.send_text(json.dumps({
            "type": "rewind_points",
            "data": {"session_id": session_id or None, "points": [],
                     "error": "No session_id provided"},
        }))
        return
    try:
        from openprogram.agent._rewind import list_rewind_points
        import asyncio
        loop = asyncio.get_event_loop()
        points = await loop.run_in_executor(
            None, lambda: list_rewind_points(session_id),
        )
        await ws.send_text(json.dumps({
            "type": "rewind_points",
            "data": {"session_id": session_id, "points": points},
        }, default=str))
    except Exception as e:
        await ws.send_text(json.dumps({
            "type": "rewind_points",
            "data": {"session_id": session_id, "points": [],
                     "error": f"{type(e).__name__}: {e}"},
        }))


async def handle_rewind(ws, cmd: dict):
    """Rewind code + conversation to a chosen point."""
    session_id = (cmd.get("session_id") or "").strip()
    target_msg_id = (cmd.get("target_msg_id") or "").strip()
    if not session_id or not target_msg_id:
        await ws.send_text(json.dumps({
            "type": "rewind_result",
            "data": {"session_id": session_id or None,
                     "error": "session_id and target_msg_id are required"},
        }))
        return
    from openprogram.webui import server as _s
    if _s._is_run_active(session_id):
        await ws.send_text(json.dumps({
            "type": "rewind_result",
            "data": {"session_id": session_id, "error": _s.RUN_ACTIVE_ERROR,
                     "code": "run_active"},
        }))
        return
    try:
        from openprogram.agent._rewind import (
            list_rewind_points,
            plan_rewind,
            rewind_to,
        )
        import asyncio
        loop = asyncio.get_event_loop()
        if target_msg_id.startswith("__by_index__"):
            idx = int(target_msg_id.removeprefix("__by_index__"))
            points = await loop.run_in_executor(
                None, lambda: list_rewind_points(session_id),
            )
            if idx < 1 or idx > len(points):
                await ws.send_text(json.dumps({
                    "type": "rewind_result",
                    "data": {"session_id": session_id,
                             "error": f"Invalid index {idx}. Available: 1-{len(points)}"},
                }))
                return
            target_msg_id = points[idx - 1]["msg_id"]
        phase = (cmd.get("phase") or "plan").strip().lower()
        mode = (cmd.get("mode") or "code_and_conversation").strip()
        if phase == "plan":
            result = await loop.run_in_executor(
                None, lambda: plan_rewind(session_id, target_msg_id, mode=mode),
            )
        elif phase == "apply":
            idempotency_key = (cmd.get("idempotency_key") or "").strip()
            plan_hash = (cmd.get("plan_hash") or "").strip()
            if not idempotency_key or not plan_hash:
                await ws.send_text(json.dumps({
                    "type": "rewind_result",
                    "data": {
                        "session_id": session_id,
                        "status": "error",
                        "error": "apply requires idempotency_key and plan_hash",
                    },
                }))
                return
            result = await loop.run_in_executor(
                None, lambda: rewind_to(
                    session_id,
                    target_msg_id,
                    idempotency_key=idempotency_key,
                    expected_plan_hash=plan_hash,
                    mode=mode,
                ),
            )
        else:
            await ws.send_text(json.dumps({
                "type": "rewind_result",
                "data": {
                    "session_id": session_id,
                    "status": "error",
                    "error": f"unknown rewind phase {phase!r}",
                },
            }))
            return
        # The transaction moves HEAD only after every file action verifies.
        # Mirror that committed CAS, including a valid move to ``None``.
        if result.get("head_changed"):
            _s._set_active_head(session_id, result.get("new_head_id"))
            _s.refresh_context_stats(session_id)
        await ws.send_text(json.dumps({
            "type": "rewind_result",
            "data": {**result, "session_id": session_id},
        }, default=str))
    except Exception as e:
        await ws.send_text(json.dumps({
            "type": "rewind_result",
            "data": {"session_id": session_id,
                     "error": f"{type(e).__name__}: {e}"},
        }))


ACTIONS = {
    "chat": handle_chat,
    "retry_function": handle_retry_function,
    "set_conversation_channel": handle_set_conversation_channel,
    "compact": handle_compact,
    "sandbox": handle_sandbox,
    "rewind_list": handle_rewind_list,
    "rewind": handle_rewind,
}
