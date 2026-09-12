"use client";
import type { DocumentController } from "@/lib/state/document-controller";
import { assertEncodedRaster, validateRasterDecoded, type RasterFormat } from "./raster-format";

type TuiEditor = {
  rotate(degree: number): Promise<unknown>; crop(options: { left: number; top: number; width: number; height: number }): Promise<unknown>;
  addText(text: string, options?: Record<string, unknown>): Promise<unknown>; addShape(type: string, options?: Record<string, unknown>): Promise<unknown>;
  addIcon(type: string, options?: Record<string, unknown>): Promise<unknown>; addObject?(_object: unknown): Promise<unknown>;
  undo(): Promise<unknown>; redo(): Promise<unknown>; toDataURL(options?: { format?: string; quality?: number }): string; destroy(): void;
  on(event: string, handler: () => void): void; off?(event: string, handler: () => void): void;
};
export interface RasterEditorInstance {
  save(targetExt?: string, options?: { commitPendingInput?: boolean }): Promise<File>;
  flushPendingSaves(): Promise<void>; setReadonly(readonly: boolean): void; setInputEnabled(enabled: boolean): void;
  destroy(): Promise<void>; getState(): { dirty: boolean; readonly: boolean; destroyed: boolean; status?: string };
  rotate(): Promise<void>; crop(options: { left: number; top: number; width: number; height: number }): Promise<void>; cropCenter(): Promise<void>;
  addText(text: string): Promise<unknown>; addShape(type: string): Promise<unknown>; undo(): Promise<void>; redo(): Promise<void>;
}
interface TuiModule { default?: new (element: HTMLElement, options: Record<string, unknown>) => TuiEditor; ImageEditor?: new (element: HTMLElement, options: Record<string, unknown>) => TuiEditor; }
let modulePromise: Promise<TuiModule> | null = null;
async function loadTui(): Promise<TuiModule> {
  modulePromise ??= import("tui-image-editor") as unknown as Promise<TuiModule>;
  return modulePromise;
}
function nextFrame(): Promise<void> { return new Promise((resolve) => requestAnimationFrame(() => resolve())); }

export async function createBoundRasterEditor(options: {
  container: HTMLElement; controller: DocumentController; bytes: Blob; fileName: string; readonly?: boolean;
  onError?: (error: Error) => void; generation?: number;
}): Promise<RasterEditorInstance> {
  const checked = await validateRasterDecoded(options.bytes);
  if (!checked.width || !checked.height) throw new Error("UNSUPPORTED_IMAGE: browser image decoding is unavailable.");
  const module = await loadTui();
  const Editor = module.default ?? module.ImageEditor;
  if (!Editor) throw new Error("Raster editor resources are unavailable.");
  const generation = options.generation ?? options.controller.getState().editorRevision;
  const editor = new Editor(options.container, { includeUI: false, usageStatistics: false,
    cssMaxWidth: 1600, cssMaxHeight: 1200, selectionStyle: { cornerSize: 12 },
    theme: { common: { bi: { image: "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==" } } },
  });
  let dirty = false; let readonly = Boolean(options.readonly); let destroyed = false; let queued: Promise<unknown> = Promise.resolve(); let raf = 0;
  const markDirty = () => { dirty = true; options.controller.markRichEditorDirty(true, instance); if (raf) cancelAnimationFrame(raf); raf = requestAnimationFrame(() => { raf = 0; }); };
  const onUndo = () => markDirty();
  editor.on("undoStackChanged", onUndo); editor.on("redoStackChanged", onUndo);
  const run = async <T>(operation: () => Promise<T>): Promise<T> => { if (readonly || destroyed) throw new Error("Raster editor is read-only."); queued = queued.then(operation); return queued as Promise<T>; };
  const exportFile = async (): Promise<File> => {
    await queued; if (raf) await nextFrame(); await nextFrame();
    const url = editor.toDataURL({ format: checked.format === "jpeg" ? "jpeg" : checked.format });
    const blob = await (await fetch(url)).blob();
    assertEncodedRaster(blob, checked.format);
    return new File([blob], options.fileName, { type: checked.mime });
  };
  const instance: RasterEditorInstance = {
    async save() { const file = await exportFile(); if (!options.readonly) await options.controller.stageRichExport(file, generation); dirty = false; return file; },
    async flushPendingSaves() { await queued; }, setReadonly(value) { readonly = value; },
    setInputEnabled(enabled) { options.container.toggleAttribute("inert", !enabled); options.container.setAttribute("aria-disabled", String(!enabled)); },
    async destroy() { if (destroyed) return; destroyed = true; if (raf) cancelAnimationFrame(raf); editor.off?.("undoStackChanged", onUndo); editor.off?.("redoStackChanged", onUndo); editor.destroy(); detach?.(); detach = undefined; },
    getState() { return { dirty, readonly, destroyed, status: destroyed ? "destroyed" : "ready" }; },
    rotate: () => run(async () => { await editor.rotate(90); }), crop: (value) => run(async () => { await editor.crop(value); }), cropCenter: () => run(async () => { await editor.crop({ left: checked.width * .1, top: checked.height * .1, width: checked.width * .8, height: checked.height * .8 }); }),
    addText: (value) => run(() => editor.addText(value)), addShape: (value) => run(() => editor.addShape(value)),
    undo: () => run(async () => { await editor.undo(); }), redo: () => run(async () => { await editor.redo(); }),
  };
  let detach: (() => void) | undefined;
  try { if (!options.readonly) detach = options.controller.attachRichEditor(instance, generation); return instance; }
  catch (error) { editor.destroy(); options.onError?.(error instanceof Error ? error : new Error(String(error))); throw error; }
}
