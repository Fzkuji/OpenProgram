"use client";

/**
 * User-message attachment chips.
 *
 * Every attached file is referenced in the message by PATH, not inlined:
 * its bytes are saved to the session workdir (uploads) or used in place
 * (@-mentions / typed paths), and the message carries a one-line
 * ``[attachment: paper.pdf (pdf, 1531 KB) @ /abs/path]`` mention. The
 * agent reads the file on demand with its ``read`` / ``pdf`` tools — the
 * file body never enters the prompt.
 *
 * Rendering that mention raw in the bubble shows the user ``[attachment:
 * …]`` markers (and, for older chats, inlined ``<file>`` bodies) mixed
 * into their own prose. This module surfaces each attachment as a compact
 * chip and keeps the typed text clean. It runs at DISPLAY time only — it
 * parses ``msg.content`` into ``{ attachments, text }`` without mutating
 * what was sent to the model. Legacy ``<file>`` / ``[attached:]`` forms
 * are still recognised so historical messages render cleanly too.
 */
import { useTranslation } from "@/lib/i18n";

export interface ParsedAttachment {
  filename: string;
  /** dim secondary line, e.g. ``pdf · 1531 KB`` or ``text file``. */
  meta: string;
  kind: "binary" | "file";
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
// is consumed but not captured — it's for the model, not the chip.
const ATTACHED_MENTION =
  /\[attach(?:ed|ment):\s*([^()]+?)\s*\(([^,)]+),\s*([\d.]+)\s*KB(?:,\s*([^)]+))?\)(?:\s*@\s*[^\]]+)?\]/g;
// Fallback mention with no size/ext: "[attachment: name @ path]" (current)
// or "[attached file: name @ path]" (legacy).
const ATTACHED_FILE = /\[attach(?:ment|ed file):\s*([^(@\]]+?)\s*@\s*[^\]]+\]/g;

/** Split raw user content into attachment chips + the cleaned prose. */
export function parseUserAttachments(
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
    attachments.push({ filename: name || "file", meta: "", kind: "file" });
    return "";
  });

  // [attachment: NAME (EXT, N KB[, COUNT])] mentions (optionally with a
  // backend-injected " @ <path>"). COUNT is the page/line scope badge or an
  // oversize note.
  text = text.replace(
    ATTACHED_MENTION,
    (_m, name: string, ext: string, kb: string, count?: string) => {
      const scope = (count || "").trim();
      attachments.push({
        filename: (name || "").trim() || "file",
        meta: `${(ext || "").trim()} · ${kb} KB` + (scope ? ` · ${scope}` : ""),
        kind: "binary",
      });
      return "";
    },
  );

  // [attached file: NAME @ PATH] — backend fallback with no size/ext.
  text = text.replace(ATTACHED_FILE, (_m, name: string) => {
    attachments.push({
      filename: (name || "").trim() || "file",
      meta: "",
      kind: "binary",
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

export function UserAttachments({ items }: { items: ParsedAttachment[] }) {
  const { text } = useTranslation();
  if (items.length === 0) return null;
  return (
    <div className="user-attachments">
      {items.map((a, i) => (
        <span
          key={`${a.filename}-${i}`}
          className="user-attach-chip"
          title={a.filename}
        >
          <FileGlyph />
          <span className="user-attach-name">{a.filename}</span>
          <span className="user-attach-meta">
            {a.meta || text("file", "文件")}
          </span>
        </span>
      ))}
    </div>
  );
}
