"use client";

/**
 * User-message attachment chips.
 *
 * The composer encodes dropped files INTO the outgoing message text so
 * the model still receives them (see composer/index.tsx):
 *   * text-y docs  → ``<file name="x.md">…content…</file>`` blocks
 *   * binary docs  → ``[attached: paper.pdf (pdf, 1531 KB)]`` mentions
 *
 * Rendering that raw in the bubble shows the user a wall of
 * ``[attached: …]`` / inlined file text mixed into their own prose
 * (what the screenshot reported). Claude Code instead surfaces each
 * attachment as a compact, visually-distinct chip and keeps the user's
 * typed text clean. This module does the same at DISPLAY time only — it
 * parses ``msg.content`` into ``{ attachments, text }`` without mutating
 * what was sent to the model.
 */
import { useTranslation } from "@/lib/i18n";

export interface ParsedAttachment {
  filename: string;
  /** dim secondary line, e.g. ``pdf · 1531 KB`` or ``text file``. */
  meta: string;
  kind: "binary" | "file";
}

const FILE_BLOCK = /<file name="([^"]*)">[\s\S]*?<\/file>/g;
// Matches the composer's "[attached: name (ext, N KB)]" and the backend's
// rewritten form that appends " @ <abs path>" once the file is saved to
// disk. The path is consumed but not captured — it's for the model, not
// the chip.
const ATTACHED_MENTION =
  /\[attached:\s*([^()]+?)\s*\(([^,]+),\s*([\d.]+)\s*KB\)(?:\s*@\s*[^\]]+)?\]/g;
// Backend fallback when the frontend sent no mention: "[attached file: name @ path]".
const ATTACHED_FILE = /\[attached file:\s*([^@\]]+?)\s*@\s*[^\]]+\]/g;

/** Split raw user content into attachment chips + the cleaned prose. */
export function parseUserAttachments(
  content: string | null | undefined,
): { attachments: ParsedAttachment[]; text: string } {
  if (!content) return { attachments: [], text: content || "" };
  const attachments: ParsedAttachment[] = [];
  let text = content;

  // <file …> blocks come first in the composer's prefix. Strip the
  // (potentially huge) inlined content from the display — the user typed
  // a short prompt, not the file body.
  text = text.replace(FILE_BLOCK, (_m, name: string) => {
    attachments.push({ filename: name || "file", meta: "", kind: "file" });
    return "";
  });

  // [attached: NAME (EXT, N KB)] mentions for binary docs (optionally with
  // a backend-injected " @ <path>").
  text = text.replace(
    ATTACHED_MENTION,
    (_m, name: string, ext: string, kb: string) => {
      attachments.push({
        filename: (name || "").trim() || "file",
        meta: `${(ext || "").trim()} · ${kb} KB`,
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
