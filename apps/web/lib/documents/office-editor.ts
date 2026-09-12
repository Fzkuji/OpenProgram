"use client";

import type { DocumentController, } from "@/lib/state/document-controller";
import type { RichDocumentEditor } from "@/lib/state/document-types";

export const OFFICE_PATCH_SHA256 = "0abfb281c7f523d5d0b9dc2f0dc60f6af4751920754d0a54cb14755b9478b088";
export interface OfficeEditorInstance extends RichDocumentEditor {
  save(targetExt?: string): Promise<File>;
  getState(): { dirty: boolean; readonly: boolean; destroyed: boolean; status?: string };
}
interface OfficeApi { createOfficeEditor(container: HTMLElement, options: Record<string, unknown>): Promise<OfficeEditorInstance>; }
interface HostAvailability { available: boolean; hostUrl?: string; packageVersion?: string; hostBuildId?: string; assetManifestDigest?: string; reason?: string; }

export async function officeHostAvailability(sessionId: string, fetcher: typeof fetch = fetch): Promise<HostAvailability> {
  const response = await fetcher(`/api/documents/office-host?session_id=${encodeURIComponent(sessionId)}`);
  if (!response.ok) throw new Error(`Office resources are unavailable (${response.status}).`);
  const value = await response.json() as HostAvailability;
  if (!value.available || !value.hostUrl) throw new Error(value.reason ?? "Office editing is unavailable.");
  return value;
}

async function loadApi(): Promise<OfficeApi> {
  const url = `/document-assets/office/${OFFICE_PATCH_SHA256}/public-api.js`;
  // The release stage owns this exact pinned path. No caller supplied module
  // URL is accepted and the module is never loaded from a CDN.
  return import(/* webpackIgnore: true */ url) as Promise<OfficeApi>;
}

export interface OfficeEditorOptions {
  container: HTMLElement;
  controller: DocumentController;
  bytes: Blob;
  fileName: string;
  hostUrl: string;
  packageVersion?: string;
  hostBuildId?: string;
  assetManifestDigest?: string;
  readonly?: boolean;
  generation?: number;
  onError?: (error: Error) => void;
}

export async function createBoundOfficeEditor(options: OfficeEditorOptions): Promise<OfficeEditorInstance> {
  const api = await loadApi();
  const generation = options.generation ?? options.controller.getState().generation;
  const editor = await api.createOfficeEditor(options.container, {
    hostUrl: options.hostUrl,
    expectedHostIdentity: {
      packageVersion: options.packageVersion ?? "",
      hostBuildId: options.hostBuildId ?? "",
      assetManifestDigest: options.assetManifestDigest ?? "",
    },
    file: new File([options.bytes], options.fileName, { type: options.bytes.type || "application/octet-stream" }),
    fileName: options.fileName,
    mode: options.readonly ? "readonly" : "edit",
    readonly: Boolean(options.readonly),
    canReturnToPreview: true,
    saveBehavior: options.readonly ? "download" : "callback",
    onSave: options.readonly ? undefined : async (file: File) => {
      await options.controller.stageRichExport(file, generation);
      return true;
    },
    onDirtyChange: (dirty: boolean, instance: OfficeEditorInstance) => options.controller.markRichEditorDirty(dirty, instance),
    onError: (error: Error) => options.onError?.(error),
    // Runtime autosave is deliberately disabled: the controller owns durable
    // draft acknowledgement and publication ordering.
    autosave: false,
  });
  const bound: OfficeEditorInstance = Object.assign(editor, {
    setInputEnabled(enabled: boolean) {
      options.container.toggleAttribute("inert", !enabled);
      options.container.setAttribute("aria-disabled", String(!enabled));
      if (!enabled) (document.activeElement as HTMLElement | null)?.blur?.();
    },
  });
  if (!options.readonly) options.controller.attachRichEditor(bound, generation);
  return bound;
}
