"use client";

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

export function FileTiles({ docs, onRemove }: FileTilesProps) {
  if (docs.length === 0) return null;
  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: 8,
        padding: "8px 8px 0",
      }}
    >
      {docs.map((d) => (
        <FileTile key={d.id} doc={d} onRemove={() => onRemove(d.id)} />
      ))}
    </div>
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
        width: 140,
        height: 90,
        padding: "10px 12px",
        borderRadius: 10,
        border: "1px solid var(--border)",
        background: "var(--bg-secondary)",
        display: "flex",
        flexDirection: "column",
        justifyContent: "space-between",
        overflow: "hidden",
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
        }}
      >
        {doc.filename}
      </div>
      {doc.ext && (
        <div
          style={{
            alignSelf: "flex-start",
            fontSize: 10,
            fontWeight: 600,
            letterSpacing: 0.5,
            textTransform: "uppercase",
            padding: "2px 6px",
            borderRadius: 4,
            background: "var(--bg-tertiary)",
            color: "var(--text-muted)",
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
          width: 18,
          height: 18,
          padding: 0,
          border: "none",
          borderRadius: 9,
          background: "rgba(0,0,0,0.55)",
          color: "white",
          cursor: "pointer",
          fontSize: 12,
          lineHeight: 1,
        }}
      >
        ×
      </button>
    </div>
  );
}
