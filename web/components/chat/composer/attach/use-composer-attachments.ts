/**
 * Composer attachments — pending images, pending docs, drag-drop.
 *
 * Pulled out of composer/index.tsx so the main file isn't carrying
 * 150 lines of attachment state + window-level drag listeners +
 * processDroppedFiles + wrapper-level drop handlers. The composer
 * still owns ``onPaste`` (it needs to read input + setInput), so the
 * hook exposes ``addImages`` / ``setImageError`` for the paste path.
 *
 * The hook also owns ``composerRootRef`` — the ref attached to the
 * wrapper element that doubles as the local drop zone. Without the
 * ref the ``onDragLeave`` check ("did we leave the wrapper, or just
 * cross into a child?") can't be done from inside the hook.
 */
"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  type PendingImage,
  collectImagesFromFiles,
  readDroppedTextFile,
} from "./image-attach";
import type { PendingDoc } from "./file-tiles";

export interface UseComposerAttachmentsResult {
  pendingImages: PendingImage[];
  imageError: string | null;
  pendingDocs: PendingDoc[];
  dragActive: boolean;
  fileInputRef: React.RefObject<HTMLInputElement>;
  composerRootRef: React.MutableRefObject<HTMLDivElement | null>;
  addImages: (imgs: PendingImage[]) => void;
  removeImage: (id: string) => void;
  setImageError: (s: string | null) => void;
  addDocs: (docs: PendingDoc[]) => void;
  removeDoc: (id: string) => void;
  onPickImages: () => void;
  onFileInputChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onDragOver: (e: React.DragEvent<HTMLDivElement>) => void;
  onDragLeave: (e: React.DragEvent<HTMLDivElement>) => void;
  onDrop: (e: React.DragEvent<HTMLDivElement>) => void;
  /** Revoke image preview URLs + reset both attachment lists. Called
   *  by submit() once the WS payload is gone. */
  clearAfterSubmit: () => void;
}

export function useComposerAttachments(): UseComposerAttachmentsResult {
  const [pendingImages, setPendingImages] = useState<PendingImage[]>([]);
  const [imageError, setImageError] = useState<string | null>(null);
  const [pendingDocs, setPendingDocs] = useState<PendingDoc[]>([]);
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const composerRootRef = useRef<HTMLDivElement | null>(null);

  // ``dragCounter`` tracks dragenter / dragleave net depth on the
  // window so the overlay only fades out when the cursor truly leaves
  // the window (lots of dragenter/leave events fire as the cursor
  // crosses child elements — a counter is the documented workaround).
  const dragCounter = useRef(0);

  const addImages = useCallback((imgs: PendingImage[]) => {
    if (imgs.length === 0) return;
    setPendingImages((prev) => [...prev, ...imgs]);
    setImageError(null);
  }, []);

  const removeImage = useCallback((id: string) => {
    setPendingImages((prev) => {
      const next: PendingImage[] = [];
      for (const p of prev) {
        if (p.id === id) {
          try { URL.revokeObjectURL(p.previewUrl); } catch { /* ignore */ }
        } else {
          next.push(p);
        }
      }
      return next;
    });
  }, []);

  const addDocs = useCallback((docs: PendingDoc[]) => {
    if (docs.length === 0) return;
    setPendingDocs((prev) => [...prev, ...docs]);
  }, []);

  const removeDoc = useCallback((id: string) => {
    setPendingDocs((prev) => prev.filter((d) => d.id !== id));
  }, []);

  const onPickImages = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const onFileInputChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      if (!files || files.length === 0) return;
      try {
        const imgs = await collectImagesFromFiles(files);
        addImages(imgs);
      } catch (err) {
        setImageError(String(err));
      }
      // Reset so picking the same file twice re-fires onChange.
      e.target.value = "";
    },
    [addImages],
  );

  // Shared file-drop processor — invoked from both the window-level
  // routeDrop (full-page drop zone) and the wrapper-level onDrop.
  // Images go to the image strip, text files to inline tiles, binary
  // to placeholder tiles. Nothing is rejected — everything dropped
  // shows up so the user always sees feedback.
  const processDroppedFiles = useCallback(async (files: File[]) => {
    if (files.length === 0) return;
    const imageFiles: File[] = [];
    const otherFiles: File[] = [];
    for (const f of files) {
      if (f.type.startsWith("image/")) imageFiles.push(f);
      else otherFiles.push(f);
    }
    // Kick off image + doc reads in parallel. ``collectImagesFromFiles``
    // is itself internally parallel now (see image-attach.ts); doing
    // images and docs together as well saves the doc-decode latency
    // from hiding behind the image decode for mixed drops.
    // Each branch updates state independently as soon as ITS results
    // are ready, so the UI shows half the chips first if one branch
    // finishes early — feels much snappier than waiting for both.
    const imagesPromise = imageFiles.length > 0
      ? collectImagesFromFiles(imageFiles).then((imgs) => addImages(imgs))
          .catch((err) => setImageError(String(err)))
      : Promise.resolve();
    const docsPromise = otherFiles.length > 0
      ? Promise.all(
          otherFiles.map(async (f) => {
            const ext = f.name.includes(".")
              ? f.name.split(".").pop()!.toLowerCase()
              : "";
            let content: string | null = null;
            const read = await readDroppedTextFile(f);
            if (read) content = read.content;
            return {
              id: `doc-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
              filename: f.name,
              ext,
              content,
              sizeBytes: f.size,
            } as PendingDoc;
          }),
        ).then((docs) => addDocs(docs))
      : Promise.resolve();
    await Promise.all([imagesPromise, docsPromise]);
  }, [addDocs, addImages, setImageError]);

  // Window-level guard — without this Chrome treats a file drop on
  // any unhandled element (page background, sidebar, status bar,
  // etc.) as a navigation request and opens the file in a new tab.
  // Even when the user lands on the composer the cancel happens too
  // late sometimes. Adding ``preventDefault`` on both dragover + drop
  // at the window level makes the drop zone effectively the whole
  // page; when the user releases anywhere we synthesise a drop into
  // the composer's handler.
  useEffect(() => {
    function hasFiles(e: DragEvent): boolean {
      return !!e.dataTransfer && e.dataTransfer.types.includes("Files");
    }
    function blockNav(e: DragEvent) {
      if (hasFiles(e)) e.preventDefault();
    }
    function onEnter(e: DragEvent) {
      if (!hasFiles(e)) return;
      e.preventDefault();
      dragCounter.current++;
      setDragActive(true);
    }
    function onLeave(e: DragEvent) {
      if (!hasFiles(e)) return;
      dragCounter.current = Math.max(0, dragCounter.current - 1);
      if (dragCounter.current === 0) setDragActive(false);
    }
    async function routeDrop(e: DragEvent) {
      if (!hasFiles(e)) return;
      e.preventDefault();
      dragCounter.current = 0;
      setDragActive(false);
      const dt = e.dataTransfer;
      if (!dt) return;
      const dropped: File[] = [];
      for (let i = 0; i < dt.files.length; i++) {
        dropped.push(dt.files[i]);
      }
      await processDroppedFiles(dropped);
    }
    window.addEventListener("dragenter", onEnter);
    window.addEventListener("dragleave", onLeave);
    window.addEventListener("dragover", blockNav);
    window.addEventListener("drop", routeDrop);
    return () => {
      window.removeEventListener("dragenter", onEnter);
      window.removeEventListener("dragleave", onLeave);
      window.removeEventListener("dragover", blockNav);
      window.removeEventListener("drop", routeDrop);
    };
  }, [processDroppedFiles]);

  const onDragOver = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    if (e.dataTransfer.types.includes("Files")) {
      e.preventDefault();
      setDragActive(true);
    }
  }, []);

  const onDragLeave = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    // Only fire when leaving the composer wrapper entirely, not just
    // crossing between children — relatedTarget points outside.
    if (!composerRootRef.current?.contains(e.relatedTarget as Node | null)) {
      setDragActive(false);
    }
  }, []);

  const onDrop = useCallback(
    async (e: React.DragEvent<HTMLDivElement>) => {
      if (!e.dataTransfer.types.includes("Files")) return;
      e.preventDefault();
      setDragActive(false);
      const dropped: File[] = [];
      for (let i = 0; i < e.dataTransfer.files.length; i++) {
        dropped.push(e.dataTransfer.files[i]);
      }
      await processDroppedFiles(dropped);
    },
    [processDroppedFiles],
  );

  const clearAfterSubmit = useCallback(() => {
    setPendingImages((prev) => {
      for (const p of prev) {
        try { URL.revokeObjectURL(p.previewUrl); } catch { /* ignore */ }
      }
      return [];
    });
    setPendingDocs([]);
  }, []);

  return {
    pendingImages,
    imageError,
    pendingDocs,
    dragActive,
    fileInputRef,
    composerRootRef,
    addImages,
    removeImage,
    setImageError,
    addDocs,
    removeDoc,
    onPickImages,
    onFileInputChange,
    onDragOver,
    onDragLeave,
    onDrop,
    clearAfterSubmit,
  };
}
