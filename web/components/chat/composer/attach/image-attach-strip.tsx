/**
 * Image attachment strip for the composer.
 *
 * Renders pending image thumbnails (added via paste / drag-drop / file
 * picker) and any user-facing error from the image-attach pipeline.
 * Owns nothing — pure presentational; the composer keeps the state and
 * passes handlers down. The hidden file input lives here too so the
 * same module owns "open picker" and "render selected files".
 */
"use client";

import React from "react";

import type { PendingImage } from "./image-attach";
import styles from "../composer.module.css";

interface ImageAttachStripProps {
  pendingImages: PendingImage[];
  imageError: string | null;
  fileInputRef: React.RefObject<HTMLInputElement>;
  onFileInputChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onRemove: (id: string) => void;
  onDismissError: () => void;
}

// Accept everything — the plus-menu entry is now a generic
// "Attach file"; drag-drop and the picker both route through
// processDroppedFiles which handles images, text, and binary
// drops uniformly.
const ACCEPTED_FILE_TYPES = "*/*";

export function ImageAttachStrip({
  pendingImages,
  imageError,
  fileInputRef,
  onFileInputChange,
  onRemove,
  onDismissError,
}: ImageAttachStripProps) {
  return (
    <>
      {/* Hidden file input — opened by the plus-menu "Attach image"
          entry. ``multiple`` so a single picker invocation can attach
          several screenshots. */}
      <input
        ref={fileInputRef}
        type="file"
        accept={ACCEPTED_FILE_TYPES}
        multiple
        onChange={onFileInputChange}
        style={{ display: "none" }}
      />
      {(pendingImages.length > 0 || imageError) && (
        <div className={styles.imageAttachStrip}>
          {pendingImages.map((p) => (
            <span
              key={p.id}
              title={p.attachment.filename || p.attachment.media_type}
              className={styles.imageAttachChip}
            >
              {p.previewUrl ? (
                <img
                  src={p.previewUrl}
                  alt={p.attachment.filename || "image"}
                  className={styles.imageAttachImg}
                />
              ) : (
                // Skeleton tile while the thumbnail is still being
                // decoded — sized identically to the real <img> so
                // the chip doesn't jump when the bitmap arrives.
                <span
                  className={styles.imageAttachImg}
                  aria-label="Loading…"
                  data-loading="true"
                />
              )}
              <button
                type="button"
                onClick={() => onRemove(p.id)}
                aria-label={`Remove image ${p.attachment.filename || p.id}`}
                className={styles.imageAttachRemove}
              >
                <svg width="10" height="10" viewBox="0 0 12 12" aria-hidden>
                  <path
                    d="M2.5 2.5 L9.5 9.5 M9.5 2.5 L2.5 9.5"
                    stroke="currentColor"
                    strokeWidth="1.6"
                    strokeLinecap="round"
                  />
                </svg>
              </button>
            </span>
          ))}
          {imageError && (
            <span className={styles.imageAttachError}>
              {imageError}
              <button
                type="button"
                onClick={onDismissError}
                aria-label="Dismiss image error"
                className={styles.imageAttachErrorDismiss}
              >
                ×
              </button>
            </span>
          )}
        </div>
      )}
    </>
  );
}
