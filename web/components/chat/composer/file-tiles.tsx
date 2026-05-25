"use client";

/* eslint-disable react/no-unknown-property */
/**
 * Generic file-attachment tiles for the composer — non-image drops.
 *
 * Visual model copies claude.ai's composer: each attached file shows
 * up as a small square card in a row above the textarea with the
 * filename + an upper-case extension badge at the bottom-left. An ×
 * in the corner removes the tile.
 *
 * Images use the dedicated ``ImageAttachStrip`` (with thumbnail
 * previews); text + binary drops use this tile.
 */

import React from "react";

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
  return (
    <div
      title={`${doc.filename} · ${sizeLabel}`}
      style={{
        position: "relative",
        // Tighter card — claude.ai's chip is around 200×56. Wider so a
        // two-byte CJK filename like ``中期-0822-Fzc copy.docx`` fits on
        // one line at fontSize 12 (≈ 26 CJK chars at line-clamp 2).
        width: 200,
        height: 56,
        padding: "8px 10px",
        paddingRight: 26,           // room for the × without overlap
        borderRadius: 10,
        border: "1px solid var(--border)",
        background: "var(--bg-secondary)",
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        gap: 4,
        overflow: "hidden",
        animation: "tileIn 180ms cubic-bezier(0.16, 1, 0.3, 1)",
      }}
    >
      <div
        style={{
          fontSize: 12,
          lineHeight: 1.3,
          color: "var(--text-primary)",
          display: "-webkit-box",
          WebkitLineClamp: 1,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
          textOverflow: "ellipsis",
          wordBreak: "break-all",
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
        onClick={onRemove}
        aria-label={`Remove ${doc.filename}`}
        style={{
          position: "absolute",
          top: 4,
          right: 4,
          width: 16,
          height: 16,
          padding: 0,
          border: "none",
          borderRadius: 8,
          background: "rgba(0,0,0,0.45)",
          color: "white",
          cursor: "pointer",
          fontSize: 11,
          lineHeight: 1,
        }}
      >
        ×
      </button>
    </div>
  );
}
