"use client";

import React, { useState } from "react";

import { useTranslation } from "@/lib/i18n";
import {
  formatAttachmentLabel,
  formatAttachmentSize,
  type PendingImage,
} from "./image-attach";
import { FilePreviewModal, type PendingDoc } from "./file-tiles";
import {
  orderedComposerAttachments,
} from "./attachment-session-cache";
import styles from "./attachment-strip.module.css";

interface AttachmentStripProps {
  pendingImages: PendingImage[];
  pendingDocs: PendingDoc[];
  imageError: string | null;
  fileInputRef: React.RefObject<HTMLInputElement>;
  onFileInputChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onRemoveImage: (id: string) => void;
  onRemoveDoc: (id: string) => void;
  onDismissError: () => void;
}

function extensionOfImage(image: PendingImage): string {
  const filename = image.attachment.filename || "";
  const dot = filename.lastIndexOf(".");
  if (dot > 0) return filename.slice(dot + 1).toLowerCase();
  return (image.attachment.media_type.split("/")[1] || "png").replace("jpeg", "jpg");
}

function iconClass(label: string): string {
  const key = label.toLowerCase();
  if (key === "pdf") return `${styles.formatIcon} ${styles.formatIconPdf}`;
  if (key === "xls" || key === "xlsx" || key === "csv") {
    return `${styles.formatIcon} ${styles.formatIconSheet}`;
  }
  if (key === "folder") return `${styles.formatIcon} ${styles.formatIconFolder}`;
  return styles.formatIcon;
}

function itemMeta(
  label: string,
  size: string,
  loading: boolean | undefined,
  error: string | undefined,
  reading: string,
  failed: string,
): string {
  const base = size ? `${label} · ${size}` : label;
  if (loading) return `${base} · ${reading}`;
  if (error) return `${base} · ${failed}`;
  return base;
}

export function AttachmentStrip({
  pendingImages,
  pendingDocs,
  imageError,
  fileInputRef,
  onFileInputChange,
  onRemoveImage,
  onRemoveDoc,
  onDismissError,
}: AttachmentStripProps) {
  const { text } = useTranslation();
  const ordered = orderedComposerAttachments({
    images: pendingImages,
    docs: pendingDocs,
  });
  const [preview, setPreview] = useState<
    | { kind: "image"; item: PendingImage }
    | { kind: "doc"; item: PendingDoc }
    | null
  >(null);
  const failedName = ordered.find((entry) => entry.item.error)?.item;
  const failedLabel = failedName
    && ("filename" in failedName
      ? failedName.filename
      : failedName.attachment.filename || "file");

  if (ordered.length === 0 && !imageError) {
    return (
      <input
        ref={fileInputRef}
        type="file"
        accept="*/*"
        multiple
        onChange={onFileInputChange}
        className={styles.fileInput}
      />
    );
  }

  return (
    <>
      <input
        ref={fileInputRef}
        type="file"
        accept="*/*"
        multiple
        onChange={onFileInputChange}
        className={styles.fileInput}
      />
      <div className={styles.attachmentStrip} data-attachment-strip>
        {ordered.map((entry) => {
          if (entry.kind === "image") {
            const image = entry.item;
            const label = formatAttachmentLabel(extensionOfImage(image));
            const size = formatAttachmentSize(image.sizeBytes);
            const state = image.loading ? "loading" : image.error ? "error" : "ready";
            const className = [
              styles.attachmentItem,
              image.loading ? styles.attachmentItemPending : "",
              image.error ? styles.attachmentItemError : "",
            ].filter(Boolean).join(" ");
            const name = image.attachment.filename || text("image", "图片");
            return (
              <div
                key={image.id}
                className={className}
                data-attachment-id={image.id}
                data-attachment-state={state}
              >
                <button
                  type="button"
                  className={styles.preview}
                  data-attachment-preview
                  aria-label={`${name} · ${size} · ${text("click to preview", "点击预览")}`}
                  onClick={() => setPreview({ kind: "image", item: image })}
                >
                  {image.previewUrl ? (
                    <span className={styles.thumb} aria-hidden="true">
                      <img
                        src={image.previewUrl}
                        alt=""
                        className={styles.thumbImg}
                      />
                    </span>
                  ) : (
                    <span className={iconClass(label)} aria-hidden="true">
                      {label}
                    </span>
                  )}
                  <span className={styles.copy}>
                    <span className={styles.name} title={name}>{name}</span>
                    <span className={styles.meta}>
                      {itemMeta(
                        label,
                        size,
                        image.loading,
                        image.error,
                        text("Reading…", "读取中…"),
                        text("Read failed", "读取失败"),
                      )}
                    </span>
                  </span>
                </button>
                <button
                  type="button"
                  className={styles.remove}
                  data-attachment-remove
                  aria-label={`${text("Remove", "移除")} ${name}`}
                  onClick={() => onRemoveImage(image.id)}
                >
                  ×
                </button>
              </div>
            );
          }
          const doc = entry.item;
          const folder = doc.ext === "folder";
          const knownFormat = ({ "application/pdf": "pdf", "text/csv": "csv", "application/json": "json", "text/plain": "txt" } as Record<string, string>)[doc.mediaType || ""];
          const rawLabel = formatAttachmentLabel(doc.ext || knownFormat || "");
          const label = rawLabel === "Folder" ? text("Folder", "文件夹") : rawLabel === "File" ? text("File", "文件") : rawLabel;
          const size = formatAttachmentSize(doc.sizeBytes, { folder });
          const state = doc.loading ? "loading" : doc.error ? "error" : "ready";
          const className = [
            styles.attachmentItem,
            doc.loading ? styles.attachmentItemPending : "",
            doc.error ? styles.attachmentItemError : "",
          ].filter(Boolean).join(" ");
          return (
            <div
              key={doc.id}
              className={className}
              data-attachment-id={doc.id}
              data-attachment-state={state}
            >
              <button
                type="button"
                className={styles.preview}
                data-attachment-preview
                aria-label={`${doc.filename} · ${size || label} · ${text("click to preview", "点击预览")}`}
                onClick={() => setPreview({ kind: "doc", item: doc })}
              >
                <span className={iconClass(label)} aria-hidden="true">{label}</span>
                <span className={styles.copy}>
                  <span className={styles.name} title={doc.filename}>{doc.filename}</span>
                  <span className={styles.meta}>
                    {itemMeta(
                      label,
                      size,
                      doc.loading,
                      doc.error,
                      text("Reading…", "读取中…"),
                      text("Read failed", "读取失败"),
                    )}
                  </span>
                </span>
              </button>
              <button
                type="button"
                className={styles.remove}
                data-attachment-remove
                aria-label={`${text("Remove", "移除")} ${doc.filename}`}
                onClick={() => onRemoveDoc(doc.id)}
              >
                ×
              </button>
            </div>
          );
        })}
        {(imageError || failedLabel) && (
          <div className={styles.errorNote}>
            {imageError || text(
              `Cannot read ${failedLabel}. Add it again or remove it.`,
              `无法读取 ${failedLabel}。请重新添加或移除。`,
            )}
            {imageError ? (
              <button
                type="button"
                onClick={onDismissError}
                aria-label={text("Dismiss image error", "关闭图片错误")}
                className={styles.remove}
              >
                ×
              </button>
            ) : null}
          </div>
        )}
      </div>
      {preview?.kind === "image" && (
        <FilePreviewModal
          image={preview.item}
          onClose={() => setPreview(null)}
        />
      )}
      {preview?.kind === "doc" && (
        <FilePreviewModal
          doc={preview.item}
          onClose={() => setPreview(null)}
        />
      )}
    </>
  );
}
