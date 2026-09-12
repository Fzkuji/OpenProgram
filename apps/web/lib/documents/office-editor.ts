"use client";

import type { DocumentController, } from "@/lib/state/document-controller";
import type { RichDocumentEditor } from "@/lib/state/document-types";

export interface OfficeEditorInstance extends RichDocumentEditor {
  save(targetExt?: string, options?: { commitPendingInput?: boolean }): Promise<File>;
  getState(): { dirty: boolean; readonly: boolean; destroyed: boolean; status?: string };
}
interface OfficeApi { createOfficeEditor(container: HTMLElement, options: Record<string, unknown>): Promise<OfficeEditorInstance>; }
interface HostAvailability { available: boolean; moduleUrl?: string; hostUrl?: string; packageVersion?: string; hostBuildId?: string; assetManifestDigest?: string; reason?: string; }

export async function officeHostAvailability(sessionId: string, fetcher: typeof fetch = fetch): Promise<HostAvailability> {
  const response = await fetcher(`/api/documents/office-host?session_id=${encodeURIComponent(sessionId)}`);
  if (!response.ok) throw new Error(`Office resources are unavailable (${response.status}).`);
  const value = await response.json() as HostAvailability;
  if (!value.available || !value.hostUrl) throw new Error(value.reason ?? "Office editing is unavailable.");
  return value;
}

async function loadApi(url: string): Promise<OfficeApi> {
  if (!/^\/api\/documents\/office-module\/[a-f0-9]{64}\.js$/.test(url))
    throw new Error("The Office module identity is invalid.");
  return import(/* webpackIgnore: true */ url) as Promise<OfficeApi>;
}

export interface OfficeEditorOptions {
  container: HTMLElement;
  controller: DocumentController;
  bytes: Blob;
  fileName: string;
  hostUrl: string;
  moduleUrl: string;
  initialMode?: "preview" | "edit";
  packageVersion?: string;
  hostBuildId?: string;
  assetManifestDigest?: string;
  readonly?: boolean;
  exportOnly?: boolean;
  generation?: number;
  onError?: (error: Error) => void;
  onReadonlyChange?: (readonly: boolean) => void;
}

export async function createBoundOfficeEditor(options: OfficeEditorOptions): Promise<OfficeEditorInstance> {
  if (options.bytes.size > 64 * 1024 * 1024) throw new Error("Office preview supports files up to 64 MiB. Download the file to open it locally.");
  const api = await loadApi(options.moduleUrl);
  const generation = options.generation ?? options.controller.getState().editorRevision;
  let readyResolve!: () => void;
  let readyReject!: (error: Error) => void;
  const ready = new Promise<void>((resolve, reject) => { readyResolve = resolve; readyReject = reject; });
  void ready.catch(() => undefined);
  const editor = await api.createOfficeEditor(options.container, {
    hostUrl: options.hostUrl,
    expectedHostIdentity: {
      packageVersion: options.packageVersion ?? "",
      hostBuildId: options.hostBuildId ?? "",
      assetManifestDigest: options.assetManifestDigest ?? "",
    },
    file: new File([options.bytes], options.fileName, { type: options.bytes.type || "application/octet-stream" }),
    fileName: options.fileName,
    mode: options.readonly || options.initialMode !== "edit" ? "readonly" : "edit",
    readonly: Boolean(options.readonly),
    // Preview uses the existing engine in readonly mode so native undo survives.
    canReturnToPreview: false,
    saveBehavior: options.readonly ? "download" : "callback",
    onSave: options.readonly ? undefined : async (file: File) => {
      if (!options.exportOnly) await options.controller.stageRichExport(file, generation);
      return true;
    },
    onDirtyChange: (dirty: boolean, instance: OfficeEditorInstance) => {
      if (!options.exportOnly) options.controller.markRichEditorDirty(dirty, instance);
    },
    onReady: () => readyResolve(),
    onStateChange: (state: { readonly: boolean }) => options.onReadonlyChange?.(state.readonly),
    onError: (error: Error) => { readyReject(error); options.onError?.(error); },
  });
  const timeout = setTimeout(() => readyReject(new Error("The Office document did not finish loading.")), 45000);
  try {
    if (editor.getState().status !== "ready") await ready;
  } catch (error) { await editor.destroy(); throw error; }
  finally { clearTimeout(timeout); }
  const bound: OfficeEditorInstance = Object.assign(editor, {
    setInputEnabled(enabled: boolean) {
      options.container.toggleAttribute("inert", !enabled);
      options.container.setAttribute("aria-disabled", String(!enabled));
      if (!enabled) (document.activeElement as HTMLElement | null)?.blur?.();
    },
  });
  if (!options.readonly && !options.exportOnly) {
    try {
      const detach = options.controller.attachRichEditor(bound, generation);
      const destroy = bound.destroy.bind(bound);
      bound.destroy = async () => { await destroy(); detach(); };
    } catch (error) { await bound.destroy(); throw error; }
  }
  return bound;
}

/** Conversion uses a disposable engine with no publication rights to the source. */
export async function convertOfficeDocument(controller: DocumentController, bytes: Blob, fileName: string,
  targetFormat: string, signal: AbortSignal): Promise<File> {
  signal.throwIfAborted();
  const availability = await officeHostAvailability(crypto.randomUUID().replace(/-/g, "").slice(0, 20));
  signal.throwIfAborted();
  const container = document.createElement("div");
  Object.assign(container.style, { position: "fixed", width: "1024px", height: "768px",
    top: "0", left: "0", visibility: "hidden", pointerEvents: "none" });
  container.inert = true;
  document.body.appendChild(container);
  let editor: OfficeEditorInstance | undefined;
  try {
    editor = await createBoundOfficeEditor({ container, controller, bytes, fileName,
      hostUrl: availability.hostUrl!, moduleUrl: availability.moduleUrl ?? "",
      packageVersion: availability.packageVersion, hostBuildId: availability.hostBuildId,
      assetManifestDigest: availability.assetManifestDigest, initialMode: "edit", exportOnly: true });
    signal.throwIfAborted();
    const converted = await editor.save(targetFormat);
    await editor.flushPendingSaves();
    signal.throwIfAborted();
    return converted;
  } finally {
    try { await editor?.destroy(); } finally { container.remove(); }
  }
}
