"use client";

import type { DocumentController, } from "@/lib/files/document-controller";
import type { RichDocumentEditor } from "@/lib/files/document-types";

export interface OfficeEditorInstance extends RichDocumentEditor {
  save(targetExt?: string, options?: { commitPendingInput?: boolean }): Promise<File>;
  getState(): { dirty: boolean; readonly: boolean; destroyed: boolean; status?: string };
}
interface OfficeMount { activate(): Promise<OfficeEditorInstance>; destroy(): Promise<void>; }
interface OfficeApi { mountOfficeEditor(container: HTMLElement, options: Record<string, unknown>): OfficeMount; }
interface HostAvailability { available: boolean; installable?: boolean; downloadBytes?: number; moduleUrl?: string; hostUrl?: string; packageVersion?: string; hostBuildId?: string; assetManifestDigest?: string; reason?: string; }

export async function officeHostAvailability(sessionId: string, fetcher: typeof fetch = fetch, signal?: AbortSignal): Promise<HostAvailability> {
  const response = await fetcher(`/api/documents/office-host?session_id=${encodeURIComponent(sessionId)}`, { signal });
  if (!response.ok) throw new Error(`Office resources are unavailable (${response.status}).`);
  const value = await response.json() as HostAvailability;
  if ((!value.available || !value.hostUrl) && !value.installable) throw new Error(value.reason ?? "Office editing is unavailable.");
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
  signal?: AbortSignal;
  onError?: (error: Error) => void;
  onReadonlyChange?: (readonly: boolean) => void;
}

export async function createBoundOfficeEditor(options: OfficeEditorOptions): Promise<OfficeEditorInstance> {
  if (options.bytes.size > 64 * 1024 * 1024) throw new Error("Office preview supports files up to 64 MiB. Download the file to open it locally.");
  options.signal?.throwIfAborted();
  const api = await loadApi(options.moduleUrl);
  options.signal?.throwIfAborted();
  const generation = options.generation ?? options.controller.getState().editorRevision;
  let readyResolve!: () => void;
  let readyReject!: (error: Error) => void;
  const ready = new Promise<void>((resolve, reject) => { readyResolve = resolve; readyReject = reject; });
  void ready.catch(() => undefined);
  const mount = api.mountOfficeEditor(options.container, {
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
  let cleanup: Promise<void> | undefined;
  const destroy = () => cleanup ??= mount.destroy();
  let rejectStartup!: (error: unknown) => void;
  const stopped = new Promise<never>((_, reject) => { rejectStartup = reject; });
  const stop = (error: unknown) => {
    rejectStartup(error);
    void destroy().catch(() => undefined);
  };
  const abort = () => stop(options.signal!.reason);
  options.signal?.addEventListener("abort", abort, { once: true });
  const timeout = setTimeout(() => stop(new Error("The Office document did not finish loading.")), 45000);
  let editor: OfficeEditorInstance;
  try {
    const activated = mount.activate().then(async instance => {
      // Host activation can precede the native document's ready state.
      if (instance.getState().status !== "ready") await ready;
      return instance;
    });
    editor = await Promise.race([activated, stopped]);
    options.signal?.throwIfAborted();
  } catch (error) { await destroy(); throw error; }
  finally {
    clearTimeout(timeout);
    options.signal?.removeEventListener("abort", abort);
  }
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
  const availability = await officeHostAvailability(crypto.randomUUID().replace(/-/g, "").slice(0, 20), fetch, signal);
  signal.throwIfAborted();
  if (!availability.available) throw new Error("Install Office document support before converting this file.");
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
      assetManifestDigest: availability.assetManifestDigest, initialMode: "edit", exportOnly: true, signal });
    signal.throwIfAborted();
    const converted = await editor.save(targetFormat);
    await editor.flushPendingSaves();
    signal.throwIfAborted();
    return converted;
  } finally {
    try { await editor?.destroy(); } finally { container.remove(); }
  }
}
