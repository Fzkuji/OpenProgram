import type { PDFDocumentProxy } from "pdfjs-dist";
import type { EventBus } from "pdfjs-dist/web/pdf_viewer.mjs";
import type { DocumentController } from "@/lib/files/document-controller";
import type { RichDocumentEditor } from "@/lib/files/document-types";

/** Persist standard PDF annotations through the document owner's revision lease. */
export function bindPdfEditor(pdf: PDFDocumentProxy, bus: EventBus, host: HTMLElement, controller: DocumentController) {
  const storage = pdf.annotationStorage as unknown as { onSetModified: (() => void) | null };
  const inputHost = host.closest("[data-pdf-reader]") ?? host;
  const generation = controller.getState().editorRevision;
  let destroyed = false, readonly = false, dirty = false;
  let savedHash = pdf.annotationStorage.serializable.hash;
  let saves: Promise<unknown> = Promise.resolve();
  const markDirty = () => {
    if (destroyed || readonly) return;
    dirty = pdf.annotationStorage.serializable.hash !== savedHash;
    controller.markRichEditorDirty(dirty, editor);
  };
  const editor: RichDocumentEditor = {
    save(_extension, options) {
      if (options?.commitPendingInput !== false && host.contains(document.activeElement)) {
        (document.activeElement as HTMLElement | null)?.blur();
      }
      const task = saves.then(async () => {
        if (destroyed) throw new Error("The PDF editor was closed.");
        const captured = pdf.annotationStorage.serializable.hash;
        if (captured === savedHash) { dirty = false; return; }
        const bytes = await pdf.saveDocument();
        if (destroyed) throw new Error("The PDF editor was closed.");
        await controller.stageRichExport(new Blob([new Uint8Array(bytes)], { type: "application/pdf" }), generation);
        savedHash = captured;
        dirty = pdf.annotationStorage.serializable.hash !== captured;
      });
      saves = task.catch(() => undefined);
      return task;
    },
    async flushPendingSaves() { await saves; },
    setReadonly(value) { readonly = value; inputHost.toggleAttribute("inert", value); },
    setInputEnabled(value) { inputHost.toggleAttribute("inert", !value || readonly); },
    destroy() { destroyed = true; storage.onSetModified = null; bus.off("editingstateschanged", markDirty); detach(); },
    getState() { return { dirty, readonly, destroyed, status: destroyed ? "destroyed" : "ready" }; },
  };
  const detach = controller.attachRichEditor(editor, generation);
  storage.onSetModified = () => { markDirty(); pdf.annotationStorage.resetModified(); };
  bus.on("editingstateschanged", markDirty);
  return editor;
}
