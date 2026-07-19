/**
 * Composer attachments — pending images, pending docs, drag-drop.
 *
 * Pulled out of composer/index.tsx so the main file isn't carrying
 * 150 lines of attachment state + window-level drag listeners +
 * processDroppedFiles + wrapper-level drop handlers. The composer
 * still owns ``onPaste`` (it needs to read input + setInput), so the
 * hook exposes ``addImagesForOwner`` / ``setImageError`` for the paste path.
 *
 * The hook also owns ``composerRootRef`` — the ref attached to the
 * wrapper element that doubles as the local drop zone. Without the
 * ref the ``onDragLeave`` check ("did we leave the wrapper, or just
 * cross into a child?") can't be done from inside the hook.
 */
"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useSessionStore } from "@/lib/session-store";
import {
  type PendingImage,
  readDroppedTextFile,
  readFileAsBase64,
  readImageFile,
  ACCEPTED_IMAGE_MIME,
} from "./image-attach";
import type { PendingDoc } from "./file-tiles";
import {
  attachmentOwnerIsClosed,
  loadAttachments,
  onAttachmentOwnerClosed,
  saveAttachments,
} from "./attach-idb";
import {
  addDocsForChat,
  addImagesForChat,
  mergeAttachments,
  removeDocForChat,
  removeImageForChat,
  setAttachmentsForChat,
  updateDocForChat,
  updateImageForChat,
  type AttachmentMergeChanges,
  type AttachmentsByChat,
  type StoredAttachments,
} from "./attachment-session-cache";

export interface UseComposerAttachmentsResult {
  pendingImages: PendingImage[];
  imageError: string | null;
  pendingDocs: PendingDoc[];
  dragActive: boolean;
  fileInputRef: React.RefObject<HTMLInputElement>;
  composerRootRef: React.MutableRefObject<HTMLDivElement | null>;
  addImagesForOwner: (ownerKey: string | null, imgs: PendingImage[]) => void;
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
  clearAfterSubmit: (ownerKey: string | null) => void;
}

function revokeAttachmentPreviews(data: StoredAttachments): void {
  for (const image of data.images) {
    if (!image.previewUrl) continue;
    try { URL.revokeObjectURL(image.previewUrl); } catch { /* ignore */ }
  }
}

function releaseAttachmentPreviews(data: StoredAttachments): StoredAttachments {
  revokeAttachmentPreviews(data);
  return {
    images: data.images.map((image) =>
      image.previewUrl ? { ...image, previewUrl: null } : image,
    ),
    docs: data.docs,
  };
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

  // Per-chat persistence (IndexedDB). Provisional local_* draft ids are
  // stable too, so multiple unsent chats keep independent attachments.
  const activeChatKey = useSessionStore((s) => s.activeChatKey);
  const mountedRef = useRef(true);
  const lifecycleEpochRef = useRef(0);
  const activeChatKeyRef = useRef<string | null>(activeChatKey);
  const attachmentsByChatRef = useRef<AttachmentsByChat>(new Map());
  const loadedAttachmentKeysRef = useRef(new Set<string>());
  const loadingAttachmentKeysRef = useRef(new Set<string>());
  const dirtyAttachmentKeysRef = useRef(new Set<string>());
  const attachmentMergeChangesRef = useRef(
    new Map<string, AttachmentMergeChanges>(),
  );
  const attachmentWriteChainsRef = useRef(new Map<string, Promise<void>>());
  activeChatKeyRef.current = activeChatKey;

  useEffect(() => {
    mountedRef.current = true;
    const epoch = ++lifecycleEpochRef.current;
    return () => {
      mountedRef.current = false;
      // React Strict Mode immediately runs setup again after its development
      // cleanup. Delay resource release by one microtask so that setup can
      // cancel this pass; an actual unmount has no matching setup.
      queueMicrotask(() => {
        if (mountedRef.current || lifecycleEpochRef.current !== epoch) return;
        for (const [chatKey, data] of attachmentsByChatRef.current) {
          setAttachmentsForChat(
            attachmentsByChatRef.current,
            chatKey,
            releaseAttachmentPreviews(data),
          );
        }
      });
    };
  }, []);

  const persistAttachments = useCallback((chatKey: string, data: StoredAttachments) => {
    if (attachmentOwnerIsClosed(chatKey)) return;
    const previous = attachmentWriteChainsRef.current.get(chatKey) ?? Promise.resolve();
    const next = previous
      .catch(() => undefined)
      .then(() => {
        if (attachmentOwnerIsClosed(chatKey)) return;
        return saveAttachments(chatKey, data);
      });
    attachmentWriteChainsRef.current.set(chatKey, next);
    void next.finally(() => {
      if (attachmentWriteChainsRef.current.get(chatKey) === next) {
        attachmentWriteChainsRef.current.delete(chatKey);
      }
    });
  }, []);

  const publishAttachments = useCallback((chatKey: string, data: StoredAttachments) => {
    if (attachmentOwnerIsClosed(chatKey)) {
      revokeAttachmentPreviews(data);
      attachmentsByChatRef.current.delete(chatKey);
      loadingAttachmentKeysRef.current.delete(chatKey);
      dirtyAttachmentKeysRef.current.delete(chatKey);
      attachmentMergeChangesRef.current.delete(chatKey);
      return;
    }
    let publishData = data;
    if (!mountedRef.current) {
      publishData = releaseAttachmentPreviews(data);
    }
    attachmentsByChatRef.current.set(chatKey, publishData);
    if (mountedRef.current && activeChatKeyRef.current === chatKey) {
      setPendingImages(publishData.images);
      setPendingDocs(publishData.docs);
    }
    if (loadedAttachmentKeysRef.current.has(chatKey)) {
      persistAttachments(chatKey, publishData);
    } else {
      dirtyAttachmentKeysRef.current.add(chatKey);
    }
  }, [persistAttachments]);

  const noteAttachmentRemoval = useCallback((
    chatKey: string,
    kind: "image" | "doc",
    id: string,
  ) => {
    if (loadedAttachmentKeysRef.current.has(chatKey)) return;
    const current = attachmentMergeChangesRef.current.get(chatKey) ?? {};
    if (current.cleared) return;
    const field = kind === "image" ? "removedImageIds" : "removedDocIds";
    const ids = current[field] ?? [];
    if (ids.includes(id)) return;
    attachmentMergeChangesRef.current.set(chatKey, {
      ...current,
      [field]: [...ids, id],
    });
  }, []);

  const noteAttachmentsCleared = useCallback((chatKey: string) => {
    if (loadedAttachmentKeysRef.current.has(chatKey)) return;
    attachmentMergeChangesRef.current.set(chatKey, { cleared: true });
  }, []);

  useEffect(() => onAttachmentOwnerClosed((chatKey) => {
    const closedAttachments = attachmentsByChatRef.current.get(chatKey);
    if (closedAttachments) revokeAttachmentPreviews(closedAttachments);
    attachmentsByChatRef.current.delete(chatKey);
    loadedAttachmentKeysRef.current.delete(chatKey);
    loadingAttachmentKeysRef.current.delete(chatKey);
    dirtyAttachmentKeysRef.current.delete(chatKey);
    attachmentMergeChangesRef.current.delete(chatKey);
    attachmentWriteChainsRef.current.delete(chatKey);
    if (activeChatKeyRef.current === chatKey) {
      setPendingImages([]);
      setPendingDocs([]);
      setImageError(null);
    }
  }), []);

  // Keep every chat's in-flight placeholders in an owner-keyed cache. A
  // FileReader callback may finish after another chat becomes visible; it
  // must update and persist its owner rather than the current React arrays.
  useEffect(() => {
    const sid = activeChatKey;
    if (!sid) {
      setImageError(null);
      setPendingImages([]);
      setPendingDocs([]);
      return;
    }
    setImageError(null);
    const cached = attachmentsByChatRef.current.get(sid);
    setPendingImages(cached?.images ?? []);
    setPendingDocs(cached?.docs ?? []);
    if (cached && loadedAttachmentKeysRef.current.has(sid)) return;
    if (loadingAttachmentKeysRef.current.has(sid)) return;
    loadingAttachmentKeysRef.current.add(sid);
    void loadAttachments(sid)
      .then((data) => {
        if (attachmentOwnerIsClosed(sid)) {
          revokeAttachmentPreviews(data);
          const cachedForClosedOwner = attachmentsByChatRef.current.get(sid);
          if (cachedForClosedOwner) {
            revokeAttachmentPreviews(cachedForClosedOwner);
          }
          attachmentsByChatRef.current.delete(sid);
          dirtyAttachmentKeysRef.current.delete(sid);
          attachmentMergeChangesRef.current.delete(sid);
          return;
        }
        let merged = mergeAttachments(
          data,
          attachmentsByChatRef.current.get(sid),
          attachmentMergeChangesRef.current.get(sid),
        );
        const retainedPreviewUrls = new Set(
          merged.images
            .map((image) => image.previewUrl)
            .filter((url): url is string => Boolean(url)),
        );
        for (const image of data.images) {
          if (image.previewUrl && !retainedPreviewUrls.has(image.previewUrl)) {
            try { URL.revokeObjectURL(image.previewUrl); } catch { /* ignore */ }
          }
        }
        if (!mountedRef.current) {
          merged = releaseAttachmentPreviews(merged);
        }
        attachmentMergeChangesRef.current.delete(sid);
        setAttachmentsForChat(attachmentsByChatRef.current, sid, merged);
        loadedAttachmentKeysRef.current.add(sid);
        if (dirtyAttachmentKeysRef.current.delete(sid)) {
          persistAttachments(sid, merged);
        }
        if (mountedRef.current && activeChatKeyRef.current === sid) {
          setPendingImages(merged.images);
          setPendingDocs(merged.docs);
        }
      })
      .finally(() => {
        loadingAttachmentKeysRef.current.delete(sid);
      });
  }, [activeChatKey, persistAttachments]);

  const addImagesForOwner = useCallback((ownerKey: string | null, imgs: PendingImage[]) => {
    if (imgs.length === 0) return;
    if (!ownerKey) {
      setPendingImages((prev) => [...prev, ...imgs]);
      setImageError(null);
      return;
    }
    publishAttachments(
      ownerKey,
      addImagesForChat(attachmentsByChatRef.current, ownerKey, imgs),
    );
    if (activeChatKeyRef.current === ownerKey) setImageError(null);
  }, [publishAttachments]);

  const removeImage = useCallback((id: string) => {
    const ownerKey = activeChatKeyRef.current;
    if (!ownerKey) {
      setPendingImages((prev) => {
        const removed = prev.find((image) => image.id === id);
        if (removed?.previewUrl) {
          try { URL.revokeObjectURL(removed.previewUrl); } catch { /* ignore */ }
        }
        return prev.filter((image) => image.id !== id);
      });
      return;
    }
    const current = attachmentsByChatRef.current.get(ownerKey);
    const removed = current?.images.find((image) => image.id === id);
    if (removed?.previewUrl) {
      try { URL.revokeObjectURL(removed.previewUrl); } catch { /* ignore */ }
    }
    noteAttachmentRemoval(ownerKey, "image", id);
    publishAttachments(
      ownerKey,
      removeImageForChat(attachmentsByChatRef.current, ownerKey, id),
    );
  }, [noteAttachmentRemoval, publishAttachments]);

  /** Replace a placeholder image entry (loading: true) with the
   *  finalised PendingImage once decode + thumbnail finish. Keeps
   *  the same id so the chip doesn't unmount / remount. */
  const updateImageForOwner = useCallback(
    (ownerKey: string | null, id: string, patch: Partial<PendingImage>) => {
      if (!ownerKey) {
        setPendingImages((prev) => prev.map((image) =>
          image.id === id ? { ...image, ...patch } : image,
        ));
        return;
      }
      const current = attachmentsByChatRef.current.get(ownerKey);
      if (!current?.images.some((image) => image.id === id)) {
        if (patch.previewUrl) {
          try { URL.revokeObjectURL(patch.previewUrl); } catch { /* ignore */ }
        }
        if (attachmentOwnerIsClosed(ownerKey) && current) {
          publishAttachments(ownerKey, current);
        }
        return;
      }
      publishAttachments(
        ownerKey,
        updateImageForChat(attachmentsByChatRef.current, ownerKey, id, patch),
      );
    },
    [publishAttachments],
  );

  const addDocsForOwner = useCallback((ownerKey: string | null, docs: PendingDoc[]) => {
    if (docs.length === 0) return;
    if (!ownerKey) {
      setPendingDocs((prev) => [...prev, ...docs]);
      return;
    }
    publishAttachments(
      ownerKey,
      addDocsForChat(attachmentsByChatRef.current, ownerKey, docs),
    );
  }, [publishAttachments]);

  const addDocs = useCallback(
    (docs: PendingDoc[]) => addDocsForOwner(activeChatKeyRef.current, docs),
    [addDocsForOwner],
  );

  const updateDocForOwner = useCallback(
    (ownerKey: string | null, id: string, patch: Partial<PendingDoc>) => {
      if (!ownerKey) {
        setPendingDocs((prev) => prev.map((doc) =>
          doc.id === id ? { ...doc, ...patch } : doc,
        ));
        return;
      }
      publishAttachments(
        ownerKey,
        updateDocForChat(attachmentsByChatRef.current, ownerKey, id, patch),
      );
    },
    [publishAttachments],
  );

  const removeDoc = useCallback((id: string) => {
    const ownerKey = activeChatKeyRef.current;
    if (!ownerKey) {
      setPendingDocs((prev) => prev.filter((doc) => doc.id !== id));
      return;
    }
    noteAttachmentRemoval(ownerKey, "doc", id);
    publishAttachments(
      ownerKey,
      removeDocForChat(attachmentsByChatRef.current, ownerKey, id),
    );
  }, [noteAttachmentRemoval, publishAttachments]);

  const onPickImages = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  // Ref hop so ``onFileInputChange`` (defined above
  // ``processDroppedFiles``) can call the processor without React
  // ordering complaints. Updated below once ``processDroppedFiles``
  // exists.
  const processDroppedFilesRef = useRef<
    ((files: File[]) => Promise<void>) | null
  >(null);

  const onFileInputChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      if (!files || files.length === 0) return;
      // Route through the same processor as drag-drop so images,
      // text files, and binaries all land in the right state slice
      // and get the loading-placeholder treatment. The plus-menu
      // "Attach file" entry no longer has to be image-specific.
      await processDroppedFilesRef.current?.(Array.from(files));
      // Reset so picking the same file twice re-fires onChange.
      e.target.value = "";
    },
    [],
  );

  // Shared file-drop processor — invoked from both the window-level
  // routeDrop (full-page drop zone) and the wrapper-level onDrop.
  // Images go to the image strip, text files to inline tiles, binary
  // to placeholder tiles. Nothing is rejected — everything dropped
  // shows up so the user always sees feedback.
  const processDroppedFiles = useCallback(async (files: File[]) => {
    if (files.length === 0) return;
    const ownerKey = activeChatKeyRef.current;
    const imageFiles: File[] = [];
    const otherFiles: File[] = [];
    for (const f of files) {
      if (ACCEPTED_IMAGE_MIME.has(f.type)) imageFiles.push(f);
      else otherFiles.push(f);
    }
    // STAGE 1 — immediately push placeholder chips for every file so
    // the user sees something the very next frame after dropping.
    // ``loading: true`` makes the chip render a skeleton in place of
    // the thumbnail / badge until stage 2 fills in the real data.
    // This is the pattern claude.ai uses: drop → tiles appear
    // instantly with a shimmer → content fills in.
    const imagePlaceholders: PendingImage[] = imageFiles.map((f) => ({
      id: `img-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      previewUrl: null,
      sizeBytes: f.size,
      attachment: {
        type: "image" as const,
        data: "",
        media_type: f.type || "image/png",
        ...(f.name ? { filename: f.name } : {}),
      },
      loading: true,
    }));
    const docPlaceholders: PendingDoc[] = otherFiles.map((f) => ({
      id: `doc-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      filename: f.name,
      ext: f.name.includes(".")
        ? f.name.split(".").pop()!.toLowerCase()
        : "",
      content: null,
      dataB64: null,
      mediaType: f.type || undefined,
      sizeBytes: f.size,
      loading: true,
    }));
    addImagesForOwner(ownerKey, imagePlaceholders);
    addDocsForOwner(ownerKey, docPlaceholders);

    // STAGE 2 — kick off the actual reads in parallel; each one
    // patches its placeholder in place when done so the chip never
    // unmounts. Errors leave the chip in ``loading: false`` with no
    // thumbnail; the user can × it manually.
    imagePlaceholders.forEach((placeholder, i) => {
      const f = imageFiles[i];
      readImageFile(f, f.name || undefined)
        .then((real) => {
          updateImageForOwner(ownerKey, placeholder.id, {
            previewUrl: real.previewUrl,
            attachment: real.attachment,
            loading: false,
          });
        })
        .catch((err) => {
          updateImageForOwner(ownerKey, placeholder.id, { loading: false });
          if (activeChatKeyRef.current === ownerKey) setImageError(String(err));
        });
    });
    docPlaceholders.forEach((placeholder, i) => {
      const f = otherFiles[i];
      // EVERY non-image file is delivered to the agent the same way:
      // its bytes are saved under the session workdir (backend) and the
      // message references it by PATH — never inlined. So always capture
      // ``dataB64`` for the upload. We additionally try a text decode,
      // but ONLY to power the local preview modal (``content``) — that
      // text is never sent to the model.
      Promise.all([readFileAsBase64(f), readDroppedTextFile(f)])
        .then(([b64, textRead]) => {
          updateDocForOwner(ownerKey, placeholder.id, {
            dataB64: b64,
            content: textRead ? textRead.content : null,
            loading: false,
          });
        })
        .catch(() => {
          updateDocForOwner(ownerKey, placeholder.id, { loading: false });
        });
    });
  }, [
    addDocsForOwner,
    addImagesForOwner,
    updateDocForOwner,
    updateImageForOwner,
  ]);

  // Keep the ref pointing at the latest processor so the file-input
  // change handler (declared earlier) routes through the same code
  // path as drag-drop.
  processDroppedFilesRef.current = processDroppedFiles;

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

  const clearAfterSubmit = useCallback((ownerKey: string | null) => {
    if (!ownerKey) {
      if (activeChatKeyRef.current !== null) return;
      revokeAttachmentPreviews({ images: pendingImages, docs: pendingDocs });
      setPendingImages([]);
      setPendingDocs([]);
      return;
    }
    const attachments = attachmentsByChatRef.current.get(ownerKey)
      ?? { images: [], docs: [] };
    revokeAttachmentPreviews(attachments);
    noteAttachmentsCleared(ownerKey);
    publishAttachments(ownerKey, { images: [], docs: [] });
  }, [noteAttachmentsCleared, pendingDocs, pendingImages, publishAttachments]);

  return {
    pendingImages,
    imageError,
    pendingDocs,
    dragActive,
    fileInputRef,
    composerRootRef,
    addImagesForOwner,
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
