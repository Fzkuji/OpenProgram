"""Persisting chat attachments to the session workdir and shaping them for dispatch."""
from __future__ import annotations

import logging
import asyncio
import json
from openprogram.host._platform.filesystem import replace_file

_log = logging.getLogger(__name__)


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
    import os
    import tempfile
    from openprogram.host._platform.filesystem import restrict_to_user

    fd = -1
    temp_path = ""
    try:
        fd, temp_path = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
        )
        restrict_to_user(temp_path)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            json.dump(data, handle, ensure_ascii=False)
        replace_file(temp_path, path)
        temp_path = ""
        restrict_to_user(path)
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
        # The preview just lacks the first-page text.
        _log.debug("PDF first-page text unavailable", exc_info=True)
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
                    source_path: str = "", saved_version: bool = False) -> str:
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
    from openprogram.state import attachments as _att
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
                dest if saved_version else source_path,
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

    A save failure rejects the send so the composer can retain the draft.
    """
    import base64
    import hashlib
    from openprogram.runtime.conversation.workdir import session_workdir_for

    wd = session_workdir_for(session_id)
    if wd is None:
        # First-turn race: the git workdir isn't resolvable yet (it's
        # finalised in the execution thread, after this synchronous
        # handler). Fall back to the deterministic ad-hoc session workdir
        # under the state dir — the SAME path apply_default_workdir will
        # set as the agent's cwd, so the saved file is still reachable.
        try:
            from pathlib import Path
            from openprogram.runtime.context.session_db import default_db
            store = default_db()
            sdir = store._session_dir(session_id)
            wd = Path(sdir) / "workdir"
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
            loaded = json.loads(index_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                dedup = loaded
    except Exception:
        dedup = {}

    new_text = text
    turn_bytes = 0
    index_dirty = False

    for idx, d in enumerate(incoming, start=1):
        data = d.get("original_data", d.get("data")) if d.get("type") == "image" else d.get("data")
        name = _attachment_name(d, idx)
        if not isinstance(data, str) or len(data) > 4 * ((MAX_ATTACH_BYTES + 2) // 3):
            raise ValueError(f"Cannot read attachment: {name}")
        try:
            raw = base64.b64decode(data, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Cannot read attachment: {name}") from exc
        if len(raw) > MAX_ATTACH_BYTES or (turn_bytes + len(raw)) > MAX_TURN_ATTACH_BYTES:
            raise ValueError(f"Attachment size limit exceeded: {name}")
        turn_bytes += len(raw)

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
            except OSError as exc:
                raise ValueError(f"Cannot save attachment: {name}") from exc
            dedup[sha] = dest.name
            index_dirty = True

        kind = _decoded_kind(raw, name)
        # Documents are read by tools on demand, never inserted into the prompt.
        count_str = _count_and_preview(raw, "text")[0] if kind == "text" else None
        raw_source_path = d.get("source_path")
        source_path = raw_source_path if isinstance(raw_source_path, str) else ""
        # Images always belong to the chat as a saved version. A later read
        # must not switch to a modified or removed desktop original.
        new_text = _inject_mention(new_text, name, dest, count_str,
                                   oversize=False, size_bytes=len(raw),
                                   mime=d.get("original_media_type") or d.get("media_type") or "",
                                   source_path=source_path,
                                   saved_version=d.get("type") == "image")

    if index_dirty:
        _write_private_json(index_path, dedup)
    return new_text


async def _persist_attachments_async(session_id: str, incoming: list,
                                     text: str) -> str:
    """Persist one attachment batch without blocking or cancelling its IO."""
    task = asyncio.create_task(asyncio.to_thread(
        _persist_attachments, session_id, incoming, text,
    ))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # The worker may already have created the session copy.  Wait for the
        # bounded operation before propagating cancellation so callers can
        # safely release the turn reservation and retry the draft.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                # A second cancellation must not release the caller's
                # reservation while the worker still owns filesystem IO.
                continue
        # Observe the worker result (and any worker exception) before the
        # original cancellation is propagated to the caller.
        try:
            task.result()
        except BaseException:
            # Only observing the worker; the original cancellation is re-raised
            # below.
            _log.debug(
                "attachment worker ended with an error after cancellation", exc_info=True)
        raise


def _attachments_for_dispatch(incoming: list) -> list | None:
    """Keep image blocks and remove the web-only source_path metadata field.

    The path remains in the user-message marker and is intentionally visible
    to the model; only the duplicate attachment-object metadata is removed.
    """
    images = [
        {key: value for key, value in item.items() if key not in {"source_path", "original_data", "original_media_type"}}
        for item in incoming if item.get("type") != "document"
    ]
    return images or None


def _save_steer_images(session_id: str, images: list) -> list[dict]:
    """Keep the model copies of a steer's images in the session's private
    folder; the durable command records their paths, never the bytes."""
    import base64
    import hashlib
    import mimetypes
    from pathlib import Path
    from openprogram.runtime.context.session_db import default_db
    from openprogram.host._platform.filesystem import restrict_to_user

    folder = Path(default_db()._session_dir(session_id)) / "steer-images"
    saved = []
    for index, image in enumerate(images, start=1):
        media_type = image.get("media_type") or "image/png"
        try:
            raw = base64.b64decode(image.get("data") or "", validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Cannot read attachment: {_attachment_name(image, index)}") from exc
        extension = mimetypes.guess_extension(media_type.partition(";")[0].strip()) or ".img"
        path = folder / f"{hashlib.sha256(raw).hexdigest()}{extension}"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if not path.is_file():
                path.write_bytes(raw)
                restrict_to_user(path)
        except OSError as exc:
            raise ValueError(f"Cannot save attachment: {_attachment_name(image, index)}") from exc
        saved.append({"path": str(path), "media_type": media_type})
    return saved


async def prepare_steer_payload(session_id: str, payload: dict) -> dict:
    """Shape a steer that carries attachments the way a sent message is:
    every file saved to the session workdir and named by path in the text,
    images also kept for the model at the next safe point."""
    incoming = payload["attachments"]
    text = await _persist_attachments_async(session_id, incoming, payload.get("message") or "")
    prepared = {"message": text}
    images = _attachments_for_dispatch(incoming)
    if images:
        prepared["images"] = await asyncio.to_thread(_save_steer_images, session_id, images)
    return prepared
