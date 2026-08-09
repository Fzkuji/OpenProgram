"use client";

/**
 * Attachment chips — for BOTH directions of the conversation.
 *
 * Every attached file is referenced in the message by PATH, not inlined:
 * its bytes are saved to the session workdir (uploads), used in place
 * (@-mentions / typed paths), downloaded to the channel account dir
 * (inbound Telegram / Discord / Slack), or produced by the agent and
 * registered with ``send_file``. Whichever it was, the message carries
 * the same one-line ``[attachment: paper.pdf (pdf, 1531 KB) @ /abs/path]``
 * mention. The agent reads the file on demand with its ``read`` / ``pdf``
 * tools — the file body never enters the prompt.
 *
 * Rendering that mention raw in the bubble shows the user ``[attachment:
 * …]`` markers (and, for older chats, inlined ``<file>`` bodies) mixed
 * into their own prose. This module surfaces each attachment as a compact
 * chip — a thumbnail for images — that opens the real file, and keeps the
 * typed text clean. It runs at DISPLAY time only: it parses ``msg.content``
 * into ``{ attachments, text }`` without mutating what was sent to the
 * model. Legacy ``<file>`` / ``[attached:]`` forms are still recognised so
 * historical messages render cleanly too.
 */
import { useState } from "react";

import { useTranslation } from "@/lib/i18n";
import { useSessionStore } from "@/lib/session-store";
import { absRawFileUrl } from "@/lib/state/files-shared";
import { AttachmentPreview } from "./attachment-preview";

export interface ParsedAttachment {
  filename: string;
  /** dim secondary line, e.g. ``pdf · 1531 KB`` or ``text file``. */
  meta: string;
  kind: "binary" | "file";
  /** Absolute path on the worker's disk, once one exists. Empty while a
   *  browser upload's optimistic bubble is still in flight (the backend
   *  appends ``@ <path>`` when the bytes land) — a chip with no path
   *  renders, it just isn't clickable yet. */
  path: string;
}

const IMAGE_EXTS = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "ico", "bmp"]);

function extOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
}

export function isImageAttachment(a: ParsedAttachment): boolean {
  return IMAGE_EXTS.has(extOf(a.filename));
}

// Legacy whole-file inline blocks — older messages dumped the file body
// into the prose as <file name="x">…</file> (uploaded text doc) or
// <file path="x">…</file> (expanded @-mention). The current design never
// inlines a file body (every file is referenced by path), but these stay
// so historical chats still render a clean chip instead of a wall of text.
const FILE_BLOCK = /<file (?:name|path)="([^"]*)">[\s\S]*?<\/file>/g;
// The backend's one-time head preview: <attachment-preview …>…</…>. It's
// for the MODEL (a first look at the file); the user sees a chip, so strip
// the whole block from the bubble. Stripped BEFORE the mention scan so any
// "[attachment:" inside previewed file content can't spawn a false chip.
const PREVIEW_BLOCK = /<attachment-preview[^>]*>[\s\S]*?<\/attachment-preview>/g;
// Current "[attachment: name (type, N KB[, <count>]) @ <abs path>]" mention
// plus the older "[attached: …]" spelling. The backend appends " @ <abs
// path>" once the file is saved (uploads); @-mentions carry the path from
// the frontend. The optional 4th group is the page/line count (e.g.
// "500 pages", "4210 lines") or an oversize note ("too large …"). The path
// was already being matched here and thrown away; capturing it is what
// turns a chip into something that opens the file.
const ATTACHED_MENTION =
  /\[attach(?:ed|ment):\s*([^()]+?)\s*\(([^,)]+),\s*([\d.]+)\s*KB(?:,\s*([^)]+))?\)(?:\s*@\s*([^\]]+))?\]/g;
// Fallback mention with no size/ext: "[attachment: name @ path]" (current)
// or "[attached file: name @ path]" (legacy).
const ATTACHED_FILE = /\[attach(?:ment|ed file):\s*([^(@\]]+?)\s*@\s*([^\]]+)\]/g;

/** Split raw message content into attachment chips + the cleaned prose.
 *  Same lexicon in both directions, so the assistant's own ``send_file``
 *  attachments render through this too. */
export function parseAttachments(
  content: string | null | undefined,
): { attachments: ParsedAttachment[]; text: string } {
  if (!content) return { attachments: [], text: content || "" };
  const attachments: ParsedAttachment[] = [];
  let text = content;

  // Strip the model-only head preview first so its file content can't be
  // mistaken for prose or spawn a false chip. It produces no chip itself —
  // the chip comes from the matching [attachment:] mention.
  text = text.replace(PREVIEW_BLOCK, "");

  // <file …> blocks come first in the composer's prefix. Strip the
  // (potentially huge) inlined content from the display — the user typed
  // a short prompt, not the file body.
  text = text.replace(FILE_BLOCK, (_m, name: string) => {
    attachments.push({
      filename: name || "file", meta: "", kind: "file", path: "",
    });
    return "";
  });

  // [attachment: NAME (EXT, N KB[, COUNT])] mentions (optionally with a
  // backend-injected " @ <path>"). COUNT is the page/line scope badge or an
  // oversize note.
  text = text.replace(
    ATTACHED_MENTION,
    (_m, name: string, ext: string, kb: string, count?: string, path?: string) => {
      const scope = (count || "").trim();
      attachments.push({
        filename: (name || "").trim() || "file",
        meta: `${(ext || "").trim()} · ${kb} KB` + (scope ? ` · ${scope}` : ""),
        kind: "binary",
        path: (path || "").trim(),
      });
      return "";
    },
  );

  // [attached file: NAME @ PATH] — backend fallback with no size/ext.
  text = text.replace(ATTACHED_FILE, (_m, name: string, path?: string) => {
    attachments.push({
      filename: (name || "").trim() || "file",
      meta: "",
      kind: "binary",
      path: (path || "").trim(),
    });
    return "";
  });

  // The prefix was joined with "\n" and separated from the prose by
  // "\n\n"; after removing the markers, collapse the leftover blank lines.
  text = text.replace(/\n{3,}/g, "\n\n").trim();
  return { attachments, text };
}

function FileGlyph() {
  return (
    <svg
      className="user-attach-glyph"
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
    </svg>
  );
}

/** Attachment chips. A chip with a path opens the real file in a
 *  preview overlay; images render as a thumbnail off the raw endpoint
 *  instead of a file glyph. A path-less chip (upload still in flight,
 *  legacy inlined `<file>` block, oversize file that was never stored)
 *  stays a plain label — there is nothing to open. */
export function AttachmentChips({ items }: { items: ParsedAttachment[] }) {
  const { text } = useTranslation();
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const [open, setOpen] = useState<ParsedAttachment | null>(null);
  if (items.length === 0) return null;
  return (
    <div className="user-attachments">
      {items.map((a, i) => {
        const key = `${a.filename}-${i}`;
        const label = a.meta || text("file", "文件");
        if (!a.path) {
          return (
            <span key={key} className="user-attach-chip" title={a.filename}>
              <FileGlyph />
              <span className="user-attach-name">{a.filename}</span>
              <span className="user-attach-meta">{label}</span>
            </span>
          );
        }
        const isImage = isImageAttachment(a);
        return (
          <button
            key={key}
            type="button"
            className={
              "user-attach-chip user-attach-open"
              + (isImage ? " user-attach-image" : "")
            }
            title={`${a.filename} — ${a.path}`}
            onClick={() => setOpen(a)}
          >
            {isImage ? (
              /* eslint-disable-next-line @next/next/no-img-element */
              <img
                className="user-attach-thumb"
                src={absRawFileUrl(a.path, sessionId ?? undefined)}
                alt={a.filename}
              />
            ) : (
              <FileGlyph />
            )}
            <span className="user-attach-name">{a.filename}</span>
            <span className="user-attach-meta">{label}</span>
          </button>
        );
      })}
      {open ? (
        <AttachmentPreview
          path={open.path}
          filename={open.filename}
          onClose={() => setOpen(null)}
        />
      ) : null}
    </div>
  );
}
