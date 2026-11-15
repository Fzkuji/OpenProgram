import { create } from "zustand";

import { desktopBridge } from "@/lib/desktop/desktop-bridge";

/** A small picture of a web resource's page, taken from its tab after the
 *  Agent's last operation on it (or when the row is first shown). A tab
 *  only has a picture while its page is loaded, so the last one is kept in
 *  this browser's storage and shown again after a restart. The desktop
 *  bridge is the only source; the plain web build keeps the page icon. */
export type WebResourceThumbnail = { url: string; at: number; operationId: string | null };

const WIDTH = 112;
const HEIGHT = 72;

type ThumbnailState = {
  shots: Record<string, WebResourceThumbnail>;
};

const STORAGE_KEY = "openprogram.web-resource-thumbnails";
const KEEP = 60;

function load(): Record<string, WebResourceThumbnail> {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch { return {}; }
}

function save(shots: Record<string, WebResourceThumbnail>): void {
  // The newest pictures only, so storage stays small.
  const kept = Object.entries(shots).sort(([, a], [, b]) => b.at - a.at).slice(0, KEEP);
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(Object.fromEntries(kept))); }
  catch { /* Pictures stay in memory if storage is unavailable or full. */ }
}

export const useWebResourceThumbnails = create<ThumbnailState>(() => ({
  shots: typeof window === "undefined" ? {} : load(),
}));
useWebResourceThumbnails.subscribe((state, previous) => { if (state.shots !== previous.shots) save(state.shots); });

const inFlight = new Set<string>();

/** Scale a page capture down to the thumbnail size, cropped from the top
 *  so the page's header stays in view. */
async function shrink(dataUrl: string): Promise<string | null> {
  const image = new Image();
  image.src = dataUrl;
  await image.decode();
  if (!image.naturalWidth || !image.naturalHeight) return null;
  const scale = window.devicePixelRatio > 1 ? 2 : 1;
  const canvas = document.createElement("canvas");
  canvas.width = WIDTH * scale;
  canvas.height = HEIGHT * scale;
  const context = canvas.getContext("2d");
  if (!context) return null;
  const sourceHeight = Math.min(image.naturalHeight, image.naturalWidth * HEIGHT / WIDTH);
  context.imageSmoothingQuality = "high";
  context.drawImage(image, 0, 0, image.naturalWidth, sourceHeight, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", 0.82);
}

/** Take a new thumbnail of `tabId` unless one for `operationId` exists;
 *  `force` takes one anyway, for a page that has just loaded. */
export function refreshWebResourceThumbnail(tabId: string, operationId: string | null, force = false): void {
  const current = useWebResourceThumbnails.getState().shots[tabId];
  if (!force && current && current.operationId === operationId) return;
  const capture = desktopBridge()?.webTab?.capture;
  if (!capture || inFlight.has(tabId)) return;
  inFlight.add(tabId);
  // "presentation" is the page as laid out in its own tab, also when the
  // tab is not on screen. A page that is not loaded gives no picture, and
  // the earlier one (if any) stays.
  void capture(tabId, "presentation").then(async shot => {
    const url = shot ? await shrink(shot) : null;
    if (!url) return;
    useWebResourceThumbnails.setState(state => ({
      shots: { ...state.shots, [tabId]: { url, at: Date.now(), operationId } },
    }));
  }).catch(() => undefined).finally(() => { inFlight.delete(tabId); });
}

/** Forget thumbnails of tabs that are gone. An empty tab list is the
 *  store still loading, never a reason to forget everything. */
export function pruneWebResourceThumbnails(alive: ReadonlySet<string>): void {
  if (!alive.size) return;
  const { shots } = useWebResourceThumbnails.getState();
  if (Object.keys(shots).every(id => alive.has(id))) return;
  useWebResourceThumbnails.setState({ shots: Object.fromEntries(Object.entries(shots).filter(([id]) => alive.has(id))) });
}

/** Pages the Resources panel lists, with the operation each picture is for. */
const watched = new Map<string, string | null>();
let stopWatching: (() => void) | null = null;
const loadTimers = new Map<string, ReturnType<typeof setTimeout>>();

/** Picture a listed page the moment it finishes loading, so opening it (or
 *  the Agent loading it) fills its row in without waiting for a retry. */
export function watchWebResourceThumbnails(tabs: ReadonlyMap<string, string | null>): () => void {
  watched.clear();
  tabs.forEach((operationId, tabId) => watched.set(tabId, operationId));
  const webTab = desktopBridge()?.webTab;
  if (!stopWatching && webTab?.onState) {
    stopWatching = webTab.onState(state => {
      if (state.loading !== false || !watched.has(state.id)) return;
      // One frame after load ends the page has painted; repeated state
      // events of one load collapse into one picture.
      clearTimeout(loadTimers.get(state.id));
      loadTimers.set(state.id, setTimeout(() => {
        loadTimers.delete(state.id);
        if (watched.has(state.id)) refreshWebResourceThumbnail(state.id, watched.get(state.id) ?? null, true);
      }, 120));
    });
  }
  return () => {
    watched.clear();
    stopWatching?.();
    stopWatching = null;
    loadTimers.forEach(timer => clearTimeout(timer));
    loadTimers.clear();
  };
}
