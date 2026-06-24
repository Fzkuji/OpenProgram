"""Chat WS actions: chat / retry_node / retry_overwrite / switch_attempt /
set_conversation_channel.

The ``chat`` action is the sole turn entry point from the web UI. The
retry / switch / channel-bind actions are ws-only.
"""
from __future__ import annotations

import json
import threading
import time
import uuid


def _db_agent_id(session_id: str) -> str:
    """Read agent_id from SessionStore, falling back to default."""
    from openprogram.agent.session_db import default_db
    from openprogram.webui.server import _default_agent_id
    return (default_db().get_session(session_id) or {}).get("agent_id") or _default_agent_id()


def _safe_attach_name(name: str) -> str:
    """Filesystem-safe basename for a saved attachment."""
    import os
    base = os.path.basename((name or "file").strip()) or "file"
    out = "".join(c if (c.isalnum() or c in "._- ") else "_" for c in base).strip()
    return out[:120] or "file"


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


def _inject_mention(text: str, name: str, dest, count, oversize: bool) -> str:
    """Rewrite this file's path-less ``[attachment: name (meta)]`` mention
    to embed the saved absolute path + (page/line) count — or mark it
    oversize. The count goes INSIDE the captured parens group so the
    single-token invariant holds and the chip / title strip regexes keep
    matching.
    """
    import re
    pat = re.compile(r"\[attachment:\s*" + re.escape(name) + r"\s*\(([^)\]]*)\)\]")
    if oversize:
        if pat.search(text):
            return pat.sub(
                lambda m: (f"[attachment: {name} ({m.group(1)}, "
                           f"too large >{MAX_ATTACH_MB}MB, not stored)]"),
                text, count=1,
            )
        return (text + f"\n[attachment: {name} "
                       f"(too large >{MAX_ATTACH_MB}MB, not stored)]").strip()
    suffix = f", {count}" if count else ""
    if pat.search(text):
        return pat.sub(
            lambda m: f"[attachment: {name} ({m.group(1)}{suffix}) @ {dest}]",
            text, count=1,
        )
    extra = f" ({count})" if count else ""
    return (text + f"\n[attachment: {name}{extra} @ {dest}]").strip()


def _preview_block(abs_path: str, preview: str, count_str, kind: str) -> str:
    """A passive head-preview content part. The chip parser strips it from
    the bubble; the model reads it as a first look at constant cost."""
    shows = count_str or ""
    return (f'<attachment-preview path="{abs_path}" kind="{kind}" shows="{shows}">\n'
            f'{preview}\n</attachment-preview>')


def _persist_doc_attachments(session_id: str, documents: list, text: str) -> str:
    """Save base64 'document' attachments to the session workdir so the
    agent's own file tools (``pdf`` / ``read`` / ``bash``) can actually
    reach them, and rewrite the ``[attachment: name (type, KB)]`` mention
    in ``text`` to embed the saved ABSOLUTE PATH.

    This is the backend half of the uniform "every file is a path"
    model: a browser upload only carries bytes + a basename (the
    sandbox hides the source path), so we materialise the bytes ONCE
    under ``<session workdir>/attachments/`` — the agent's cwd — and
    tell the model exactly where. The agent reads it on demand; the
    file body is never inlined into the prompt. The web UI's chip
    parser hides the ``@ <path>`` suffix so the bubble reads cleanly.

    (Files that already live on disk — ``@``-mentions / typed paths —
    skip this entirely: the frontend emits the absolute path directly,
    no copy. See ``at-mention.ts`` / ``/api/file-resolve``.)

    Best-effort: a save failure leaves that file's mention untouched.
    """
    import base64
    import hashlib
    import json
    from openprogram.agent._workdir import session_workdir_for

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

    for d in documents:
        data = d.get("data")
        name = (d.get("filename") or "file").strip()
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
            new_text = _inject_mention(new_text, name, None, None, oversize=True)
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
        new_text = _inject_mention(new_text, name, dest, count_str, oversize=False)
        if preview:
            previews.append(_preview_block(str(dest), preview, count_str, kind))

    if index_dirty:
        try:
            index_path.write_text(json.dumps(dedup))
        except OSError:
            pass
    # Append the one-time head previews after the prose. They are bounded
    # (<=PREVIEW_CAP each, this turn only) and stripped from the bubble by
    # the chip parser, so the user sees a chip while the model gets a look.
    if previews:
        new_text = (new_text + "\n\n" + "\n".join(previews)).strip()
    return new_text


def _title_from_text(text: str) -> str:
    """Conversation title from the first user message, with attachment
    markers + legacy inline blocks stripped so a truncated
    ``[attachment: … @ /long/path]`` never leaks into the sidebar.

    Mirrors the web parser (``user-attachments.tsx``) on the backend so
    the stored title is already clean — the frontend strips markers for
    display too, but only when the closing bracket survives; truncating
    at 50 chars can sever it, so we clean first, then truncate.
    """
    import re
    t = re.sub(r"<attachment-preview[^>]*>.*?</attachment-preview>", "", text, flags=re.S)
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
    permission_mode = cmd.get("permission_mode") or None
    # Per-turn speed / priority tier from the composer's speed pill
    # ("priority" = Fast, "flex" = cheaper-slower). Rides the message
    # payload each turn (client remembers via localStorage like the
    # thinking pill) — no server-side persistence / DB column needed.
    service_tier = cmd.get("service_tier") or None
    # Tool profile override: when the user picked a non-"full" profile in
    # the composer, resolve it to the profile's tool name list so the
    # dispatcher only gives the model those tools.
    if tools_profile and tools_flag is True:
        try:
            from openprogram.functions import agent_tools as _at
            resolved = _at(toolset=tools_profile, only_available=True)
            if resolved is not None:
                tools_flag = [t.name for t in resolved]
        except Exception:
            pass
    # "Web Search" plus-menu toggle: layer ``web_search`` on top of
    # whatever the Tools toggle resolves to. Three useful states:
    #   * tools=False, web_search=False → tools_override=[]  (no tools)
    #   * tools=False, web_search=True  → tools_override=["web_search"]
    #   * tools=True,  web_search=*     → DEFAULT_TOOLS [+web_search]
    #   * tools=None (defer to agent profile), web_search=True
    #       → agent's tools list with "web_search" guaranteed
    if web_search_flag:
        try:
            from openprogram.functions import DEFAULT_TOOLS as _DEFAULT_TOOLS
        except Exception:
            _DEFAULT_TOOLS = []
        if isinstance(tools_flag, list):
            base = list(tools_flag)
        elif tools_flag is True:
            base = list(_DEFAULT_TOOLS)
        elif tools_flag is False:
            base = []
        else:
            # tools_flag is None — caller wants "use the agent profile's
            # configured tools". We don't have those resolved here, so
            # fall back to DEFAULT_TOOLS for the explicit override list
            # the dispatcher will see. Better to be slightly broader
            # than to drop the agent's other tools entirely.
            base = list(_DEFAULT_TOOLS)
        if "web_search" not in base:
            base.append("web_search")
        tools_flag = base
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
    from openprogram.agent.session_config import save_session_run_config
    run_cfg = save_session_run_config(
        session_id,
        agent_id=_db_agent_id(session_id),
        tools=tools_flag,
        thinking_effort=thinking_effort,
        permission_mode=permission_mode,
    )
    conv["tools_enabled"] = run_cfg.tools_enabled
    conv["tools_override"] = run_cfg.tools_override
    conv["thinking_effort"] = run_cfg.thinking_effort
    conv["permission_mode"] = run_cfg.permission_mode
    msg_id = str(uuid.uuid4())[:8]

    # Persist 'document' attachments to the session workdir so the agent's
    # file tools can read them, and embed the saved ABSOLUTE PATH into the
    # message text (every file is referenced by path, never inlined).
    # Images stay inline and continue to the dispatcher as ImageContent
    # blocks; documents are NOT passed as content blocks (providers have
    # no document-block support here).
    if attachments:
        _docs = [a for a in attachments if a.get("type") == "document"]
        if _docs:
            attachments = [a for a in attachments if a.get("type") != "document"] or None
            text = _persist_doc_attachments(session_id, _docs, text)

    # Stage 1 (immediate, zero-latency sidebar placeholder): truncate the
    # user's first line into a title the instant the message is sent, so the
    # sidebar never shows an empty row while stage 2 (the background LLM
    # title in finalize→_maybe_auto_title) is still running. We mark
    # ``_auto_titled`` — the SAME flag _maybe_auto_title uses — so its own
    # stage-1 backfill is a no-op and its race guard (which compares the
    # live title against the truncation it expects) keeps the LLM title.
    # We do NOT set _user_titled: this is an automatic title, not a manual
    # rename, so the LLM stage and turn-1/6/16/40 re-titling stay live.
    from openprogram.agent.session_db import default_db as _chat_ddb
    _chat_sess = _chat_ddb().get_session(session_id) or {}
    _chat_extra = _chat_sess.get("extra_meta") or {}
    if not _chat_extra.get("_auto_titled") and not _chat_extra.get("_user_titled"):
        _truncated = _title_from_text(text)
        try:
            _chat_ddb().update_session(session_id, title=_truncated,
                                       _auto_titled=True)
        except Exception:
            pass

    parsed = _s._parse_chat_input(text)

    user_msg = {
        "role": "user",
        "id": msg_id,
        "content": text,
        "timestamp": time.time(),
        "source": "web",
    }
    if parsed["action"] == "spawn":
        # SYNC path only: tag the /task user msg so the DAG layout
        # treats it as a branch fork (main trunk stops here; the
        # spawned turn + sub-agent reply live on a new lane). Same
        # idea as git: /task probe → `git checkout -b probe`.
        # ASYNC path: don't tag — the spawned turn lives on its own
        # session (or independent branch), not as a fork of THIS
        # message. Marking it function="task" made the user msg
        # surface in the Branches panel as a stray named branch
        # (with the raw command as its label) because lane.py
        # treated it as a fork tip with no follow-up.
        if parsed.get("wait", True):
            user_msg["function"] = "task"
    if attachments:
        manifest = [
            {"type": a.get("type"), "media_type": a.get("media_type"),
             "size_b64": len(a.get("data") or "")}
            for a in attachments
        ]
        user_msg["extra"] = json.dumps({"attachments": manifest}, default=str)
    _s._append_msg(conv, user_msg)

    # Fire chat.before_send hook so plugins can observe the message
    # about to enter the runtime. Failures are absorbed by
    # dispatch_hook so a bad plugin can't poison the chat path.
    try:
        from openprogram.plugins.hooks import dispatch_hook, HookEvent
        dispatch_hook(HookEvent.CHAT_BEFORE_SEND, {
            "session_id": session_id,
            "msg_id": msg_id,
            "text": text,
            "agent_id": _db_agent_id(session_id),
            "attachments": bool(attachments),
        })
    except Exception:
        pass

    await ws.send_text(json.dumps({
        "type": "chat_ack",
        "data": {"session_id": session_id, "msg_id": msg_id},
    }))

    # Mark the session running + push the sidebar list right now, before
    # the exec thread starts — so every connected tab shows the new
    # conversation row already flowing (convRunningFlow) the instant the
    # turn is dispatched, not a round-trip later when the exec thread's
    # own running_task broadcast lands. setdefault so the thread's later
    # _running_tasks[...] = {...} overwrite stays the single source of
    # the task entry (no double running_task with a different started_at).
    import time as _t
    with _s._running_tasks_lock:
        _s._running_tasks.setdefault(session_id, {
            "msg_id": msg_id, "func_name": "_chat",
            "started_at": _t.time(), "last_event_at": _t.time(),
            "display_params": "", "loaded_func_ref": None,
            "stream_events": [],
        })
    _s._emit_running_task_event(session_id)
    try:
        from openprogram.webui.ws_actions.session import broadcast_sessions_list
        broadcast_sessions_list()
    except Exception:
        pass

    if parsed["action"] == "query":
        threading.Thread(
            target=_s._execute_in_context,
            args=(session_id, msg_id, "query"),
            kwargs={"query": parsed["raw"],
                    "thinking_effort": run_cfg.thinking_effort,
                    "tools_flag": tools_flag,
                    "permission_mode": run_cfg.permission_mode,
                    "service_tier": service_tier,
                    "attachments": attachments},
            daemon=True,
        ).start()
    elif parsed["action"] == "spawn":
        threading.Thread(
            target=_s._execute_in_context,
            args=(session_id, msg_id, "spawn"),
            kwargs={"kwargs": {
                "prompt": parsed.get("prompt") or "",
                "label": parsed.get("label") or "",
                # New same-session multi-agent: "inherit" (default,
                # fork off this turn) or "clean" (new root in the
                # same session). Slash parser strips --clean /
                # --inherit and surfaces them here.
                "context": parsed.get("context") or "inherit",
                # wait=False (default for /task --async): submit to
                # TaskRunner, return immediately. wait=True (default)
                # blocks like the historical /task path.
                "wait": parsed.get("wait", True),
            }},
            daemon=True,
        ).start()
    elif parsed["action"] == "merge":
        threading.Thread(
            target=_s._execute_in_context,
            args=(session_id, msg_id, "merge"),
            kwargs={"kwargs": {
                "sub_sessions": parsed.get("sub_sessions") or [],
                "message": parsed.get("message") or "",
            }},
            daemon=True,
        ).start()


async def handle_retry_node(ws, cmd: dict):
    from openprogram.webui import server as _s
    node_path = cmd.get("node_path")
    session_id = cmd.get("session_id")
    params_override = cmd.get("params")
    _s._log(f"[retry] received retry_node: session_id={session_id}, node_path={node_path}, params_override={params_override}")
    if not node_path or not session_id:
        _s._log("[retry] missing node_path or session_id, aborting")
        await ws.send_text(json.dumps({
            "type": "chat_response",
            "data": {"type": "error",
                     "content": "Retry failed: missing node_path or session_id",
                     "session_id": session_id or "", "msg_id": "err"},
        }))
        return
    msg_id = str(uuid.uuid4())[:8]
    await ws.send_text(json.dumps({
        "type": "chat_ack",
        "data": {"session_id": session_id, "msg_id": msg_id},
    }))
    _s._log(f"[retry] starting retry thread msg_id={msg_id}")
    threading.Thread(
        target=_s._retry_node,
        args=(session_id, msg_id, node_path, params_override),
        daemon=True,
    ).start()


async def handle_retry_overwrite(ws, cmd: dict):
    """Overwrite retry: drop old user+assistant for the function, re-run."""
    from openprogram.webui import server as _s
    session_id = cmd.get("session_id")
    func_name = cmd.get("function")
    text = cmd.get("text", "").strip()
    thinking_effort = cmd.get("thinking_effort") or None
    exec_thinking_effort = cmd.get("exec_thinking_effort") or None
    if not session_id or not text:
        return

    conv = _s._get_or_create_session(session_id)
    conv.pop("_last_exec_session", None)
    old_rt = conv.pop("_last_exec_runtime", None)
    if old_rt and hasattr(old_rt, "close"):
        old_rt.close()

    messages = conv.get("messages", [])
    new_messages = []
    skip_next_assistant = False
    for m in messages:
        if skip_next_assistant and m.get("role") == "assistant":
            skip_next_assistant = False
            continue
        if (m.get("role") == "user" and m.get("display") == "runtime"):
            parsed_check = _s._parse_chat_input(m.get("content", ""))
            if parsed_check.get("function") == func_name:
                skip_next_assistant = True
                continue
        new_messages.append(m)
    conv["messages"] = new_messages
    _s._set_active_head(session_id, new_messages[-1]["id"] if new_messages else None)

    msg_id = str(uuid.uuid4())[:8]
    original_content = cmd.get("original_content", text)

    _s._append_msg(conv, {
        "role": "user",
        "id": msg_id,
        "content": text,
        "original_content": original_content,
        "display": "runtime",
        "timestamp": time.time(),
    })

    await ws.send_text(json.dumps({
        "type": "chat_ack",
        "data": {"session_id": session_id, "msg_id": msg_id},
    }))

    parsed = _s._parse_chat_input(text)
    # The legacy /run retry path was removed when @agentic_function
    # dispatch unified onto dispatcher.dispatch_forced_tool_call.
    # Frontend retry should now call POST /api/function/{name} directly.
    if parsed["action"] == "query":
        threading.Thread(
            target=_s._execute_in_context,
            args=(session_id, msg_id, "query"),
            kwargs={"query": parsed["raw"],
                    "thinking_effort": thinking_effort},
            daemon=True,
        ).start()
    else:
        _s._broadcast_chat_response(session_id, msg_id, {
            "type": "error",
            "content": (
                "retry_overwrite no longer dispatches @agentic_function "
                "runs — call POST /api/function/{name} instead."
            ),
            "function": func_name,
            "display": "runtime",
        })


async def handle_switch_attempt(ws, cmd: dict):
    """Swap the visible result among stored attempts for a function call."""
    from openprogram.webui import server as _s
    session_id = cmd.get("session_id")
    func_name = cmd.get("function")
    attempt_idx = cmd.get("attempt_index", 0)
    conv = _s._sessions.get(session_id)
    if not conv:
        return
    messages = conv.get("messages", [])
    msg_idx = None
    target_msg = None
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if (m.get("role") == "assistant"
                and m.get("type") == "result"
                and m.get("function") == func_name
                and "attempts" in m):
            target_msg = m
            msg_idx = i
            break

    if target_msg and 0 <= attempt_idx < len(target_msg["attempts"]):
        old_idx = target_msg.get("current_attempt", 0)
        attempts = target_msg["attempts"]

        subsequent_now = messages[msg_idx + 1:]
        if old_idx < len(attempts):
            attempts[old_idx]["subsequent_messages"] = subsequent_now

        target_msg["current_attempt"] = attempt_idx
        target_msg["content"] = attempts[attempt_idx]["content"]

        restored = attempts[attempt_idx].get("subsequent_messages", [])
        new_msgs_for_attempt = messages[:msg_idx + 1] + restored
        conv["messages"] = new_msgs_for_attempt
        _s._set_active_head(
            session_id,
            new_msgs_for_attempt[-1]["id"] if new_msgs_for_attempt else None,
        )

        _s._save_session(session_id)
        await ws.send_text(json.dumps({
            "type": "attempt_switched",
            "data": {
                "function": func_name,
                "attempt_index": attempt_idx,
                "content": attempts[attempt_idx]["content"],
                "tree": attempts[attempt_idx].get("tree"),
                "total": len(attempts),
                "subsequent_messages": restored,
            },
        }, default=str))


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
        await ws.send(json.dumps({
            "type": "chat_response",
            "data": {"type": "error",
                     "content": "compact: missing session_id"},
        }))
        return

    conv = _s._get_or_create_session(session_id)
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
    except Exception as e:  # noqa: BLE001
        _s._broadcast_chat_response(session_id, "compact", {
            "type": "error",
            "content": f"compact failed: {type(e).__name__}: {e}",
        })


async def handle_sandbox(ws, cmd: dict):
    """Toggle system sandbox on/off for the current session."""
    from openprogram.sandbox import sandbox_enabled, is_available
    available = is_available()
    if not available:
        await ws.send(json.dumps({
            "type": "chat_response",
            "data": {"type": "status",
                     "content": "System sandbox not available "
                                "(macOS needs sandbox-exec; Linux needs bubblewrap)"},
        }))
        return
    current = sandbox_enabled.get(False)
    sandbox_enabled.set(not current)
    state = "ON" if not current else "OFF"
    msg = f"Sandbox: {state}"
    if not current:
        msg += " — bash commands restricted to cwd writes only"
    await ws.send(json.dumps({
        "type": "chat_response",
        "data": {"type": "status", "content": msg},
    }))


ACTIONS = {
    "chat": handle_chat,
    "retry_node": handle_retry_node,
    "retry_overwrite": handle_retry_overwrite,
    "switch_attempt": handle_switch_attempt,
    "set_conversation_channel": handle_set_conversation_channel,
    "compact": handle_compact,
    "sandbox": handle_sandbox,
}
