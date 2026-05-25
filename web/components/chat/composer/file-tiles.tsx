"use client";

/* eslint-disable react/no-unknown-property */
/**
 * Generic file-attachment tiles for the composer — non-image drops.
 *
 * Visual model copies claude.ai's composer: each attached file shows
 * up as a rounded card with the filename + an upper-case extension
 * badge. A prominent × in the top-left corner removes the tile;
 * clicking anywhere ELSE on the tile opens a preview modal showing
 * the file content (or a "binary" placeholder for non-text drops).
 *
 * Images use the dedicated ``ImageAttachStrip`` (with thumbnail
 * previews); text + binary drops use this tile.
 */

import React, { useEffect, useState } from "react";
import { createPortal } from "react-dom";

export interface PendingDoc {
  id: string;
  filename: string;
  /** Lower-cased extension without the dot — used for the badge.
   *  Empty when the file has no recognizable extension. */
  ext: string;
  /** Decoded text content for text-y files. ``null`` for binaries we
   *  can't inline — the tile is shown but submit only mentions the
   *  filename in the outgoing message. */
  content: string | null;
  /** Raw size in bytes — surfaced in the tile's hover title. */
  sizeBytes: number;
}

interface FileTilesProps {
  docs: PendingDoc[];
  onRemove: (id: string) => void;
}

// Keyframes injected once via a style tag — gives us the pop-in
// animation without a CSS-module file. Cheap; React dedupes the
// element if the component mounts twice.
const TILE_KEYFRAMES = `
@keyframes tileIn {
  from { opacity: 0; transform: translateY(-4px) scale(0.96); }
  to   { opacity: 1; transform: translateY(0)    scale(1); }
}
@keyframes overlayIn {
  from { opacity: 0; }
  to   { opacity: 1; }
}
.composer-file-tile {
  transition: border-color 120ms ease, background 120ms ease,
              box-shadow 120ms ease;
}
.composer-file-tile:hover {
  border-color: var(--text-muted);
  background: var(--bg-tertiary);
  box-shadow: 0 1px 4px rgba(0,0,0,0.25);
}
.composer-file-tile-close {
  transition: background 120ms ease, transform 120ms ease;
}
.composer-file-tile-close:hover {
  background: rgba(255,255,255,0.92);
  transform: scale(1.06);
}
`;

export function FileTiles({ docs, onRemove }: FileTilesProps) {
  if (docs.length === 0) return null;
  // Wrapper padding sits the row inside the rounded composer border —
  // ~14px clear from the top edge + 12px sides matches claude.ai's
  // breathing room. The bottom gap (10px) plus the textarea's own
  // padding keeps the gap from the typing line equal to the gap from
  // the composer top.
  return (
    <>
      <style>{TILE_KEYFRAMES}</style>
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 8,
          padding: "14px 12px 10px",
        }}
      >
        {docs.map((d) => (
          <FileTile key={d.id} doc={d} onRemove={() => onRemove(d.id)} />
        ))}
      </div>
    </>
  );
}

function FileTile({ doc, onRemove }: { doc: PendingDoc; onRemove: () => void }) {
  const sizeLabel = doc.sizeBytes >= 1024 * 1024
    ? `${(doc.sizeBytes / 1024 / 1024).toFixed(1)} MB`
    : `${(doc.sizeBytes / 1024).toFixed(0)} KB`;
  const [previewOpen, setPreviewOpen] = useState(false);
  return (
    <>
      <div
        className="composer-file-tile"
        role="button"
        tabIndex={0}
        onClick={() => setPreviewOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setPreviewOpen(true);
          }
        }}
        title={`${doc.filename} · ${sizeLabel} · click to preview`}
        style={{
          position: "relative",
          // Roomier pill-ish card to match claude.ai. Wider so CJK
          // filenames fit on two lines; taller so the badge has
          // breathing room.
          width: 220,
          height: 68,
          padding: "10px 14px",
          paddingRight: 16,
          // Pill-ish corners — rounder than a normal card but still
          // rectangular enough to fit two lines of text.
          borderRadius: 16,
          border: "1px solid var(--border)",
          background: "var(--bg-secondary)",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          gap: 4,
          overflow: "hidden",
          cursor: "pointer",
          animation: "tileIn 180ms cubic-bezier(0.16, 1, 0.3, 1)",
          outline: "none",
        }}
      >
        <div
          style={{
            fontSize: 12,
            lineHeight: 1.3,
            color: "var(--text-primary)",
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
            wordBreak: "break-all",
            paddingRight: 18,  // keep clear of the × hot zone
          }}
        >
          {doc.filename}
        </div>
        {doc.ext && (
          <div
            style={{
              alignSelf: "flex-start",
              fontSize: 9,
              fontWeight: 600,
              letterSpacing: 0.5,
              textTransform: "uppercase",
              padding: "1px 5px",
              borderRadius: 3,
              background: "var(--bg-tertiary)",
              color: "var(--text-muted)",
              lineHeight: 1.4,
            }}
          >
            {doc.ext}
          </div>
        )}
        <button
          type="button"
          className="composer-file-tile-close"
          // stopPropagation so the click only removes — doesn't also
          // open the preview modal.
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
          aria-label={`Remove ${doc.filename}`}
          style={{
            position: "absolute",
            // Negative offset puts the × half over the tile edge —
            // matches claude.ai. Easier to hit, doesn't overlap
            // filename.
            top: -6,
            left: -6,
            width: 22,
            height: 22,
            padding: 0,
            border: "1px solid var(--border)",
            borderRadius: 11,
            background: "var(--bg-primary, #1a1a1c)",
            color: "var(--text-primary, #e5e5e5)",
            cursor: "pointer",
            fontSize: 14,
            lineHeight: 1,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            // Drop shadow so the × visibly floats over the tile edge.
            boxShadow: "0 1px 3px rgba(0,0,0,0.4)",
          }}
        >
          ×
        </button>
      </div>
      {previewOpen && (
        <FilePreviewModal doc={doc} onClose={() => setPreviewOpen(false)} />
      )}
    </>
  );
}

function FilePreviewModal({
  doc, onClose,
}: { doc: PendingDoc; onClose: () => void }) {
  // Esc closes. Defensive — only attach the listener while open.
  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") onClose(); }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  if (typeof document === "undefined") return null;
  return createPortal(
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 10_001,
        background: "rgba(0,0,0,0.55)",
        backdropFilter: "blur(2px)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        animation: "overlayIn 140ms ease-out",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(720px, 90vw)",
          maxHeight: "80vh",
          background: "var(--bg-secondary)",
          border: "1px solid var(--border)",
          borderRadius: 14,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            padding: "12px 16px",
            borderBottom: "1px solid var(--border)",
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <span style={{
            fontFamily: "ui-monospace, monospace",
            fontSize: 13,
            color: "var(--text-primary)",
            flex: 1,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}>
            {doc.filename}
          </span>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close preview"
            style={{
              width: 26, height: 26,
              border: "none",
              borderRadius: 13,
              background: "transparent",
              color: "var(--text-muted)",
              cursor: "pointer",
              fontSize: 18,
              lineHeight: 1,
            }}
          >×</button>
        </div>
        <div
          style={{
            flex: 1,
            overflow: "auto",
            padding: 16,
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
            fontSize: 12,
            lineHeight: 1.5,
            color: "var(--text-primary)",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {doc.content === null ? (
            <span style={{ color: "var(--text-muted)" }}>
              Binary file — no inline preview available. The LLM will
              still know the file is attached (via filename + size in
              the outgoing message).
            </span>
          ) : doc.content || (
            <span style={{ color: "var(--text-muted)" }}>(empty)</span>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
